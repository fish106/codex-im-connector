from __future__ import annotations

import asyncio
import logging
import random
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from codex_app_server import AsyncCodex, AsyncThread, AsyncTurnHandle
from codex_app_server.client import AppServerConfig
from codex_app_server.errors import JsonRpcError, is_retryable_error
from codex_app_server.generated.v2_all import (
    AgentMessageThreadItem,
    AgentMessageDeltaNotification,
    ContextCompactedNotification,
    ImageGenerationThreadItem,
    ImageViewThreadItem,
    ItemCompletedNotification,
    ReasoningEffort,
    ServerRequestResolvedNotification,
    ThreadReadResponse,
    ThreadStatus,
    TurnCompletedNotification,
)
from codex_app_server.models import Notification

from core.config import Settings
from model.connector_models import (
    AvailableModelInfo,
    CurrentThreadInfo,
    GeneratedImageOutput,
    StreamCallbacks,
    ThreadListPage,
    ThreadRecord,
)
from service.approval_service import ApprovalService
from service.session_service import SessionService

logger = logging.getLogger(__name__)
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
LOCAL_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

T = TypeVar("T")


class BusyError(RuntimeError):
    pass


async def retry_on_overload_async(
    op: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    initial_delay_s: float = 0.25,
    max_delay_s: float = 2.0,
    jitter_ratio: float = 0.2,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    delay = initial_delay_s
    attempt = 0
    while True:
        attempt += 1
        try:
            return await op()
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable_error(exc):
                raise
            jitter = delay * jitter_ratio
            sleep_for = min(max_delay_s, delay) + random.uniform(-jitter, jitter)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            delay = min(max_delay_s, delay * 2)


@dataclass(slots=True)
class _ActiveTurnState:
    user_open_id: str
    thread_id: str
    turn_id: str
    handle: AsyncTurnHandle
    callbacks: StreamCallbacks
    full_text: str = ""


@dataclass(slots=True)
class _ActiveCompactionState:
    user_open_id: str
    thread_id: str
    on_completed: Callable[[], Awaitable[None]]
    on_failed: Callable[[str], Awaitable[None]]


class CodexRuntime:
    def __init__(
        self,
        settings: Settings,
        session_service: SessionService,
        approval_service: ApprovalService,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._settings = settings
        self._session_service = session_service
        self._approval_service = approval_service
        self._loop = loop
        self._sync_state_lock = threading.Lock()
        self._codex: AsyncCodex | None = None
        self._threads: dict[str, AsyncThread] = {}
        self._active_state: _ActiveTurnState | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._compact_state: _ActiveCompactionState | None = None
        self._compact_task: asyncio.Task[None] | None = None
        self._state_lock = asyncio.Lock()
        self._turn_done_events: dict[str, asyncio.Event] = {}
        self._models_cache: list[AvailableModelInfo] = []

    async def start(self) -> None:
        self._codex = AsyncCodex(
            config=AppServerConfig(
                codex_bin=self._settings.CODEX_BIN,
                cwd=str(self._settings.codex_cwd_path),
            )
        )
        await self._codex.__aenter__()
        # AsyncCodex does not expose approval_handler publicly. We inject it
        # into the underlying sync client to preserve the existing approval bridge.
        self._codex._client._sync._approval_handler = self._approval_handler  # noqa: SLF001

    async def close(self) -> None:
        compact_task = self._compact_task
        if compact_task is not None and not compact_task.done():
            compact_task.cancel()
            try:
                await compact_task
            except asyncio.CancelledError:
                pass
        if self._codex is not None:
            await self._codex.close()
        self._codex = None
        self._threads.clear()

    def is_busy(self) -> bool:
        turn_busy = self._active_task is not None and not self._active_task.done()
        compact_busy = self._compact_task is not None and not self._compact_task.done()
        return turn_busy or compact_busy

    async def create_thread(self, user_open_id: str, name: str | None = None) -> tuple[str, str | None]:
        if self.is_busy():
            raise BusyError("runtime is busy")
        codex = self._require_codex()
        state = await self._session_service.get_session_state(user_open_id)
        effective_cwd = await self._session_service.get_effective_cwd(user_open_id, str(self._settings.codex_cwd_path))
        model_name, reasoning_effort = await self._resolve_effective_model_config(
            state.default_model,
            state.default_reasoning_effort,
        )
        if state.default_model is None or state.default_reasoning_effort is None:
            await self._session_service.set_default_model_config(user_open_id, model_name, reasoning_effort)
        thread = await retry_on_overload_async(
            lambda: codex.thread_start(
                cwd=effective_cwd,
                approval_policy=self._settings.CODEX_APPROVAL_POLICY,
                model=model_name,
                config={"model_reasoning_effort": reasoning_effort},
            ),
            max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
            initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
            max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
        )
        if name:
            await retry_on_overload_async(
                lambda: thread.set_name(name),
                max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
                initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
                max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
            )
        self._threads[thread.id] = thread
        await self._session_service.set_current_thread(user_open_id, thread.id)
        await self._session_service.upsert_thread_binding(user_open_id, thread.id, name=name)
        return thread.id, name

    async def list_threads(
        self,
        user_open_id: str,
        search_term: str | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ThreadListPage:
        del user_open_id
        codex = self._require_codex()
        response = await retry_on_overload_async(
            lambda: codex.thread_list(
                archived=False,
                search_term=search_term or None,
                cursor=cursor,
                limit=limit,
            ),
            max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
            initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
            max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
        )
        return ThreadListPage(
            items=[
                ThreadRecord(
                    thread_id=item.id,
                    name=item.name,
                    cwd=item.cwd.root,
                    preview=item.preview or "",
                    updated_at=self._format_thread_updated_at(item.updated_at),
                )
                for item in response.data
            ],
            next_cursor=response.next_cursor,
            current_cursor=cursor,
            search_term=search_term,
        )

    async def list_models(self) -> list[AvailableModelInfo]:
        codex = self._require_codex()
        response = await retry_on_overload_async(
            lambda: codex.models(include_hidden=False),
            max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
            initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
            max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
        )
        models = [
            AvailableModelInfo(
                model_id=item.id,
                display_name=item.display_name,
                is_default=item.is_default,
                default_reasoning_effort=getattr(item.default_reasoning_effort, "value", str(item.default_reasoning_effort)),
                supported_reasoning_efforts=[
                    getattr(option.reasoning_effort, "value", str(option.reasoning_effort))
                    for option in item.supported_reasoning_efforts
                ],
            )
            for item in response.data
        ]
        self._models_cache = models
        return models

    async def set_default_model_config(self, user_open_id: str, model_name: str, reasoning_effort: str) -> None:
        await self._session_service.set_default_model_config(user_open_id, model_name, reasoning_effort)

    async def get_default_model_config(self, user_open_id: str) -> tuple[str | None, str | None]:
        state = await self._session_service.get_session_state(user_open_id)
        return state.default_model, state.default_reasoning_effort

    async def set_cwd(self, user_open_id: str, cwd: str, *, sync_current_thread: bool = False) -> tuple[str, bool]:
        if self.is_busy():
            raise BusyError("runtime is busy")
        resolved_cwd = self._validate_cwd_path(cwd)
        normalized_cwd = str(resolved_cwd)
        await self._session_service.set_default_cwd(user_open_id, normalized_cwd)

        if not sync_current_thread:
            return normalized_cwd, False

        state = await self._session_service.get_session_state(user_open_id)
        if not state.current_thread_id:
            return normalized_cwd, False

        default_model, default_reasoning_effort = await self.get_default_model_config(user_open_id)
        model_name, reasoning_effort = await self._resolve_effective_model_config(
            default_model,
            default_reasoning_effort,
        )
        try:
            await self._resume_thread_handle(
                state.current_thread_id,
                model_name=model_name,
                reasoning_effort=reasoning_effort,
                cwd=normalized_cwd,
            )
        except JsonRpcError as exc:
            if "thread not found" not in str(exc).lower():
                raise
            return normalized_cwd, False
        return normalized_cwd, True

    async def resume_thread(self, user_open_id: str, thread_id: str) -> tuple[str, str | None]:
        if self.is_busy():
            raise BusyError("runtime is busy")
        state = await self._session_service.get_session_state(user_open_id)
        model_name, reasoning_effort = await self._resolve_effective_model_config(
            state.default_model,
            state.default_reasoning_effort,
        )
        if state.default_model is None or state.default_reasoning_effort is None:
            await self._session_service.set_default_model_config(user_open_id, model_name, reasoning_effort)
        thread = await self._resume_thread_handle(
            thread_id,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
        )
        response = await retry_on_overload_async(
            lambda: thread.read(include_turns=False),
            max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
            initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
            max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
        )
        name = response.thread.name
        await self._session_service.set_current_thread(user_open_id, thread.id)
        await self._session_service.upsert_thread_binding(user_open_id, thread.id, name=name)
        return thread.id, name

    async def ensure_thread_available(self, user_open_id: str, thread_id: str) -> tuple[str, str | None, bool]:
        try:
            resumed_thread_id, name = await self.resume_thread(user_open_id, thread_id)
            return resumed_thread_id, name, False
        except JsonRpcError as exc:
            if "thread not found" not in str(exc).lower():
                raise
            logger.exception(
                "stored thread is no longer available, creating a new thread user_open_id=%s thread_id=%s",
                user_open_id,
                thread_id,
            )
            new_thread_id, name = await self.create_thread(user_open_id, None)
            return new_thread_id, name, True

    async def read_thread(self, thread_id: str) -> ThreadReadResponse:
        thread = await self._get_thread_handle(thread_id)
        return await retry_on_overload_async(
            lambda: thread.read(include_turns=False),
            max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
            initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
            max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
        )

    async def read_current_thread_info(self, thread_id: str) -> CurrentThreadInfo:
        response = await self.read_thread(thread_id)
        thread = response.thread
        return CurrentThreadInfo(
            thread_id=thread.id,
            name=thread.name,
            source=self._session_source_to_str(thread.source),
            model_provider=thread.model_provider,
            cwd=thread.cwd.root,
            preview=thread.preview or "",
            created_at=self._format_timestamp(thread.created_at),
            updated_at=self._format_timestamp(thread.updated_at),
        )

    async def rename_thread(self, user_open_id: str, thread_id: str, name: str) -> tuple[str, str | None]:
        if self.is_busy():
            raise BusyError("runtime is busy")
        thread = await self._get_thread_handle(thread_id)
        await retry_on_overload_async(
            lambda: thread.set_name(name),
            max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
            initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
            max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
        )
        await self._session_service.rename_thread(user_open_id, thread_id, name)
        return thread_id, name

    async def compact_thread(
        self,
        user_open_id: str,
        thread_id: str,
        on_completed: Callable[[], Awaitable[None]],
        on_failed: Callable[[str], Awaitable[None]],
    ) -> None:
        async with self._state_lock:
            if self.is_busy():
                raise BusyError("runtime is busy")
        thread = await self._get_thread_handle(thread_id)
        await retry_on_overload_async(
            lambda: thread.compact(),
            max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
            initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
            max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
        )
        compact_state = _ActiveCompactionState(
            user_open_id=user_open_id,
            thread_id=thread_id,
            on_completed=on_completed,
            on_failed=on_failed,
        )
        async with self._state_lock:
            self._compact_state = compact_state
            self._compact_task = asyncio.create_task(self._watch_compaction(compact_state))

    async def interrupt_current_turn(self, user_open_id: str) -> bool:
        async with self._state_lock:
            state = self._active_state
        if state is None or state.user_open_id != user_open_id:
            return False
        await state.handle.interrupt()
        return True

    async def steer_current_turn(self, user_open_id: str, text: str) -> bool:
        async with self._state_lock:
            state = self._active_state
        if state is None or state.user_open_id != user_open_id:
            return False
        try:
            from codex_app_server import TextInput

            await state.handle.steer(TextInput(text))
            return True
        except Exception:
            logger.exception(
                "steer execution failed user_open_id=%s thread_id=%s turn_id=%s",
                user_open_id,
                state.thread_id,
                state.turn_id,
            )
            return False

    async def approve_pending(self, user_open_id: str) -> bool:
        context = await self._approval_service.approve(user_open_id)
        return context is not None

    async def cancel_pending(self, user_open_id: str) -> tuple[str, str] | None:
        context = await self._approval_service.cancel(user_open_id)
        if context is None:
            return None
        await context.resolved_event.wait()
        await self.log_thread_status(context.thread_id, context.request_id)
        await self.wait_for_turn_completion(context.turn_id)
        return context.thread_id, context.turn_id

    async def resolve_pending_decision(self, user_open_id: str, decision: str) -> bool:
        context = await self._approval_service.resolve(user_open_id, decision)
        return context is not None

    async def submit_inputs(
        self,
        user_open_id: str,
        thread_id: str,
        inputs: list[Any],
        callbacks: StreamCallbacks,
    ) -> str:
        async with self._state_lock:
            if self.is_busy():
                raise BusyError("runtime is busy")
            turn_done = asyncio.Event()
            started: asyncio.Future[str] = self._loop.create_future()
            self._active_task = asyncio.create_task(
                self._run_turn(
                    user_open_id=user_open_id,
                    thread_id=thread_id,
                    inputs=inputs,
                    callbacks=callbacks,
                    started=started,
                    turn_done=turn_done,
                )
            )
        turn_id = await started
        self._turn_done_events[turn_id] = turn_done
        return turn_id

    async def wait_for_turn_completion(self, turn_id: str) -> None:
        event = self._turn_done_events.get(turn_id)
        if event is not None:
            await event.wait()

    async def _run_turn(
        self,
        user_open_id: str,
        thread_id: str,
        inputs: list[Any],
        callbacks: StreamCallbacks,
        started: asyncio.Future[str],
        turn_done: asyncio.Event,
    ) -> None:
        last_emit = 0.0
        turn_id = ""
        image_outputs: list[GeneratedImageOutput] = []
        image_output_keys: set[str] = set()
        try:
            thread = await self._get_thread_handle(thread_id)
            state = await self._session_service.get_session_state(user_open_id)
            thread_read = await retry_on_overload_async(
                lambda: thread.read(include_turns=False),
                max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
                initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
                max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
            )
            thread_cwd = thread_read.thread.cwd.root
            model_name = state.default_model or self._settings.CODEX_MODEL
            reasoning_effort = state.default_reasoning_effort or self._settings.CODEX_DEFAULT_REASONING_EFFORT
            turn_handle = await retry_on_overload_async(
                lambda: thread.turn(
                    inputs,
                    model=model_name,
                    effort=ReasoningEffort(reasoning_effort),
                ),
                max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
                initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
                max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
            )
            turn_id = turn_handle.id
            async with self._state_lock:
                self._active_state = _ActiveTurnState(
                    user_open_id=user_open_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    handle=turn_handle,
                    callbacks=callbacks,
                    full_text="",
                )
            await self._session_service.set_current_turn(user_open_id, turn_id)
            started.set_result(turn_id)

            async for notification in turn_handle.stream():
                logger.debug(notification)
                payload = notification.payload
                if (
                    notification.method == "item/agentMessage/delta"
                    and isinstance(payload, AgentMessageDeltaNotification)
                    and payload.turn_id == turn_id
                ):
                    current_text = ""
                    with self._sync_state_lock:
                        if self._active_state is not None and self._active_state.turn_id == turn_id:
                            self._active_state.full_text += payload.delta
                            current_text = self._active_state.full_text
                    now = self._loop.time()
                    if now - last_emit >= self._settings.STREAM_PATCH_INTERVAL_S:
                        await callbacks.on_stream_update(current_text, False)
                        last_emit = now
                    continue
                if (
                    notification.method == "item/completed"
                    and isinstance(payload, ItemCompletedNotification)
                    and payload.turn_id == turn_id
                ):
                    for image_output in self._extract_generated_image_outputs(payload.item, thread_cwd):
                        output_key = f"{image_output.source_type}:{image_output.path}"
                        if output_key in image_output_keys:
                            continue
                        image_output_keys.add(output_key)
                        image_outputs.append(image_output)
                    continue
                if notification.method == "serverRequest/resolved" and isinstance(
                    payload, ServerRequestResolvedNotification
                ):
                    resolved_user = await self._session_service.find_user_open_id_by_thread_id(payload.thread_id)
                    log_fn = logger.info if resolved_user is not None else logger.warning
                    log_fn(
                        "resolved approval notification thread_id=%s request_id=%s mapped_user_open_id=%s",
                        payload.thread_id,
                        str(payload.request_id),
                        resolved_user,
                    )
                    if resolved_user is not None:
                        await self._approval_service.mark_resolved_by_user(resolved_user)
                    continue
                if (
                    notification.method == "turn/completed"
                    and isinstance(payload, TurnCompletedNotification)
                    and payload.turn.id == turn_id
                ):
                    logger.info(
                        "running approval fallback cleanup on turn completion user_open_id=%s thread_id=%s turn_id=%s",
                        user_open_id,
                        thread_id,
                        turn_id,
                    )
                    await self._approval_service.mark_resolved_by_user(user_open_id)
                    status_value = getattr(payload.turn.status, "value", str(payload.turn.status))
                    with self._sync_state_lock:
                        buffered_text = ""
                        if self._active_state is not None and self._active_state.turn_id == turn_id:
                            buffered_text = self._active_state.full_text
                    final_text = buffered_text.strip() or self._fallback_turn_text(status_value)
                    await callbacks.on_stream_update(final_text, True)
                    if image_outputs and callbacks.on_image_outputs is not None:
                        await callbacks.on_image_outputs(image_outputs)
                    break
        except Exception as exc:
            logger.exception(
                "turn execution failed user_open_id=%s thread_id=%s turn_id=%s",
                user_open_id,
                thread_id,
                turn_id or "<not-started>",
            )
            if not started.done():
                started.set_exception(exc)
            await callbacks.on_error(str(exc))
        finally:
            if turn_id:
                self._turn_done_events.setdefault(turn_id, turn_done).set()
            await self._session_service.set_current_turn(user_open_id, None)
            await self._session_service.set_waiting_for_approval(user_open_id, False)
            async with self._state_lock:
                self._active_state = None

    async def _get_thread_handle(self, thread_id: str) -> AsyncThread:
        cached = self._threads.get(thread_id)
        if cached is not None:
            return cached
        return await self._resume_thread_handle(thread_id)

    async def _resume_thread_handle(
        self,
        thread_id: str,
        *,
        model_name: str | None = None,
        reasoning_effort: str | None = None,
        cwd: str | None = None,
    ) -> AsyncThread:
        codex = self._require_codex()
        thread = await retry_on_overload_async(
            lambda: codex.thread_resume(
                thread_id,
                cwd=cwd,
                model=model_name,
                config={"model_reasoning_effort": reasoning_effort} if reasoning_effort else None,
            ),
            max_attempts=self._settings.CODEX_RETRY_MAX_ATTEMPTS,
            initial_delay_s=self._settings.CODEX_RETRY_INITIAL_DELAY_S,
            max_delay_s=self._settings.CODEX_RETRY_MAX_DELAY_S,
        )
        self._threads[thread.id] = thread
        return thread

    async def _resolve_effective_model_config(
        self,
        default_model: str | None,
        default_reasoning_effort: str | None,
    ) -> tuple[str, str]:
        model_name = default_model or self._settings.CODEX_MODEL
        models = self._models_cache or await self.list_models()

        selected_model = None
        if model_name is not None:
            selected_model = next((item for item in models if item.model_id == model_name), None)
        if selected_model is None:
            selected_model = next((item for item in models if item.is_default), None)
        if selected_model is None and models:
            selected_model = models[0]

        if selected_model is not None:
            model_name = selected_model.model_id
            reasoning_effort = default_reasoning_effort or selected_model.default_reasoning_effort
        else:
            model_name = model_name or "unknown"
            reasoning_effort = default_reasoning_effort or self._settings.CODEX_DEFAULT_REASONING_EFFORT

        return model_name, reasoning_effort

    def _require_codex(self) -> AsyncCodex:
        if self._codex is None:
            raise RuntimeError("AsyncCodex is not initialized")
        return self._codex

    def _validate_cwd_path(self, cwd: str) -> Path:
        raw = cwd.strip()
        if not raw:
            raise ValueError("cwd path is empty")
        resolved = Path(raw).expanduser().resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(f"cwd does not exist or is not a directory: {resolved}")
        for root in self._settings.allowed_cwd_roots:
            if resolved == root or root in resolved.parents:
                return resolved
        raise ValueError(f"cwd is not under allowed roots: {resolved}")

    @staticmethod
    def _format_thread_updated_at(updated_at: int) -> str:
        return datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_timestamp(timestamp_s: int) -> str:
        return datetime.fromtimestamp(timestamp_s).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _session_source_to_str(source: Any) -> str:
        root = getattr(source, "root", source)
        value = getattr(root, "value", None)
        if isinstance(value, str):
            return value
        custom = getattr(root, "custom", None)
        if isinstance(custom, str):
            return custom
        sub_agent = getattr(root, "sub_agent", None)
        if sub_agent is not None:
            sub_root = getattr(sub_agent, "root", sub_agent)
            sub_value = getattr(sub_root, "value", None)
            if isinstance(sub_value, str):
                return sub_value
        return str(root)

    async def _watch_compaction(self, state: _ActiveCompactionState) -> None:
        try:
            await asyncio.wait_for(
                self._wait_for_compaction_notification(state.thread_id),
                timeout=self._settings.CODEX_COMPACT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "thread compaction timed out user_open_id=%s thread_id=%s timeout_s=%s",
                state.user_open_id,
                state.thread_id,
                self._settings.CODEX_COMPACT_TIMEOUT_S,
            )
            await self._safe_call_compaction_failure(
                state,
                f"等待 compact 完成超时（{self._settings.CODEX_COMPACT_TIMEOUT_S} 秒）",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "thread compaction failed user_open_id=%s thread_id=%s",
                state.user_open_id,
                state.thread_id,
            )
            await self._safe_call_compaction_failure(state, str(exc))
        else:
            logger.info(
                "thread compaction completed user_open_id=%s thread_id=%s",
                state.user_open_id,
                state.thread_id,
            )
            await self._safe_call_compaction_completed(state)
        finally:
            async with self._state_lock:
                if self._compact_state is state:
                    self._compact_state = None
                if self._compact_task is asyncio.current_task():
                    self._compact_task = None

    async def _wait_for_compaction_notification(self, thread_id: str) -> None:
        codex = self._require_codex()
        while True:
            notification = await codex._client.next_notification()  # noqa: SLF001
            payload = notification.payload
            if (
                notification.method == "thread/compacted"
                and isinstance(payload, ContextCompactedNotification)
                and payload.thread_id == thread_id
            ):
                return

    async def _safe_call_compaction_completed(self, state: _ActiveCompactionState) -> None:
        try:
            await state.on_completed()
        except Exception:
            logger.exception(
                "compact completion callback failed user_open_id=%s thread_id=%s",
                state.user_open_id,
                state.thread_id,
            )

    async def _safe_call_compaction_failure(self, state: _ActiveCompactionState, message: str) -> None:
        try:
            await state.on_failed(message)
        except Exception:
            logger.exception(
                "compact failure callback failed user_open_id=%s thread_id=%s",
                state.user_open_id,
                state.thread_id,
            )

    async def log_thread_status(self, thread_id: str, request_id: str) -> None:
        try:
            response = await self.read_thread(thread_id)
            status = response.thread.status
            status_type = self._thread_status_type(status)
            active_flags = self._thread_status_flags(status)
            logger.info(
                "approval reject resolved request_id=%s thread_id=%s status=%s active_flags=%s",
                request_id,
                thread_id,
                status_type,
                active_flags,
            )
        except Exception:
            logger.warning("failed to inspect thread status after reject", exc_info=True)

    @staticmethod
    def _fallback_turn_text(status: str) -> str:
        if status == "interrupted":
            return "任务已中断。"
        if status == "failed":
            return "任务执行失败。"
        return "任务已完成。"

    @staticmethod
    def _thread_status_type(status: ThreadStatus) -> str:
        root = getattr(status, "root", status)
        return getattr(root, "type", "unknown")

    @staticmethod
    def _thread_status_flags(status: ThreadStatus) -> list[str]:
        root = getattr(status, "root", status)
        flags = getattr(root, "active_flags", None) or []
        return [getattr(flag, "value", str(flag)) for flag in flags]

    def _extract_generated_image_outputs(self, item: Any, cwd: str | None) -> list[GeneratedImageOutput]:
        thread_item = getattr(item, "root", item)
        if isinstance(thread_item, ImageGenerationThreadItem):
            image_path = self._absolute_path_to_str(thread_item.saved_path)
            if image_path is None:
                return []
            output = self._build_generated_image_output(
                item_id=thread_item.id,
                path=image_path,
                source_type="image_generation",
                cwd=None,
            )
            return [output] if output is not None else []
        if isinstance(thread_item, ImageViewThreadItem):
            image_path = self._absolute_path_to_str(thread_item.path)
            if image_path is None:
                return []
            output = self._build_generated_image_output(
                item_id=thread_item.id,
                path=image_path,
                source_type="image_view",
                cwd=None,
            )
            return [output] if output is not None else []
        if isinstance(thread_item, AgentMessageThreadItem):
            return self._extract_agent_message_image_outputs(thread_item, cwd)
        return []

    def _build_generated_image_output(
        self,
        *,
        item_id: str,
        path: str,
        source_type: str,
        cwd: str | None,
    ) -> GeneratedImageOutput | None:
        path_obj = Path(path).expanduser()
        if not path_obj.is_absolute():
            if not cwd:
                return None
            path_obj = (Path(cwd).expanduser().resolve() / path_obj).resolve()
        else:
            path_obj = path_obj.resolve()
        resolved_path = path_obj
        if not resolved_path.is_file():
            logger.warning(
                "generated image output is missing item_id=%s source_type=%s path=%s",
                item_id,
                source_type,
                resolved_path,
            )
            return None
        return GeneratedImageOutput(
            item_id=item_id,
            path=str(resolved_path),
            source_type=source_type,
        )

    def _extract_agent_message_image_outputs(
        self,
        thread_item: AgentMessageThreadItem,
        cwd: str | None,
    ) -> list[GeneratedImageOutput]:
        if not thread_item.text:
            return []
        outputs: list[GeneratedImageOutput] = []
        for index, match in enumerate(MARKDOWN_LINK_RE.finditer(thread_item.text)):
            target = match.group(1).strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if "://" in target:
                continue
            suffix = Path(target).suffix.lower()
            if suffix not in LOCAL_IMAGE_SUFFIXES:
                continue
            output = self._build_generated_image_output(
                item_id=f"{thread_item.id}:{index}",
                path=target,
                source_type="agent_message_link",
                cwd=cwd,
            )
            if output is not None:
                outputs.append(output)
        return outputs

    @staticmethod
    def _absolute_path_to_str(path_obj: Any) -> str | None:
        if path_obj is None:
            return None
        return getattr(path_obj, "root", path_obj)

    def _approval_command_to_str(self, params: dict[str, Any]) -> str:
        command_raw = params.get("command")
        if isinstance(command_raw, list):
            command = " ".join(str(item) for item in command_raw)
        else:
            command = str(command_raw or "")
        if command:
            return command

        meta = params.get("_meta")
        if not isinstance(meta, dict):
            return ""

        tool_description = str(meta.get("tool_description") or "").strip()
        tool_params_display = meta.get("tool_params_display") or []
        param_parts: list[str] = []
        if isinstance(tool_params_display, list):
            for item in tool_params_display:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("display_name") or item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if name and value:
                    param_parts.append(f"{name}={value}")
                elif value:
                    param_parts.append(value)

        if tool_description and param_parts:
            return f"{tool_description} ({', '.join(param_parts)})"
        if tool_description:
            return tool_description
        if param_parts:
            return ", ".join(param_parts)
        return ""

    def _approval_handler(self, request_method: str, params: dict | None) -> dict[str, Any]:
        logger.info("[Approval] request_method=%s, params=%s", request_method, params)
        with self._sync_state_lock:
            state = self._active_state
        if state is None:
            return {"decision": "cancel"}
        request_id = str((params or {}).get("requestId") or uuid.uuid4())
        params = params or {}
        reason = str(params.get("reason") or params.get("message") or "")
        command = self._approval_command_to_str(params)
        available_decisions = [item for item in (params.get("availableDecisions") or []) if isinstance(item, str)]
        if not available_decisions:
            available_decisions = ["accept", "cancel"]
        future = asyncio.run_coroutine_threadsafe(
            self._approval_service.create_pending(
                user_open_id=state.user_open_id,
                thread_id=state.thread_id,
                turn_id=state.turn_id,
                request_id=request_id,
                request_method=request_method,
                reason=reason,
                command=command,
                available_decisions=available_decisions,
            ),
            self._loop,
        )
        context = future.result()
        logger.info(
            "dispatching approval prompt update user_open_id=%s thread_id=%s turn_id=%s request_id=%s",
            state.user_open_id,
            state.thread_id,
            state.turn_id,
            request_id,
        )
        prompt_future = asyncio.run_coroutine_threadsafe(
            state.callbacks.on_approval_request(context.to_prompt(), state.full_text),
            self._loop,
        )
        try:
            prompt_future.result(timeout=10)
            with self._sync_state_lock:
                if self._active_state is not None and self._active_state.turn_id == state.turn_id:
                    self._active_state.full_text = ""
            logger.info(
                "approval prompt update finished user_open_id=%s thread_id=%s turn_id=%s request_id=%s",
                state.user_open_id,
                state.thread_id,
                state.turn_id,
                request_id,
            )
        except Exception:
            logger.exception(
                "approval prompt update failed user_open_id=%s thread_id=%s turn_id=%s request_id=%s",
                state.user_open_id,
                state.thread_id,
                state.turn_id,
                request_id,
            )
        context.decision_event.wait()
        return self._approval_result(request_method, params, context.decision or "cancel")

    def _approval_result(self, request_method: str, params: dict[str, Any], decision: str) -> dict[str, Any]:
        if request_method == "mcpServer/elicitation/request":
            return self._elicitation_result(params, decision)
        return {"decision": decision}

    @staticmethod
    def _elicitation_result(params: dict[str, Any], decision: str) -> dict[str, Any]:
        if decision == "accept":
            requested_schema = params.get("requestedSchema")
            properties = requested_schema.get("properties") if isinstance(requested_schema, dict) else None
            if properties and isinstance(properties, dict):
                logger.warning(
                    "elicitation accept requested for non-empty schema; returning empty content because IM flow cannot collect form fields"
                )
            return {"action": "accept", "content": {}}
        if decision == "cancel":
            return {"action": "cancel"}
        return {"action": "decline"}
