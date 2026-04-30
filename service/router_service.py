from __future__ import annotations

import logging
from dataclasses import dataclass, field

from codex_app_server import LocalImageInput, TextInput
from codex_app_server.errors import JsonRpcError

from client.codex_runtime import BusyError, CodexRuntime
from connector.base import IMConnectorBase
from model.connector_models import (
    ApprovalActionEvent,
    ApprovalPrompt,
    GeneratedImageOutput,
    InboundMessage,
    MessageTarget,
    StreamCallbacks,
    ThreadListActionEvent,
)
from service.approval_service import ApprovalService
from service.pending_image_service import PendingImageService
from service.render_service import RenderService
from service.session_service import SessionService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ThreadListCardState:
    user_open_id: str
    message_id: str
    search_term: str | None
    current_cursor: str | None
    prev_cursors: list[str | None] = field(default_factory=list)
    next_cursor: str | None = None


class RouterService:
    def __init__(
        self,
        connector: IMConnectorBase,
        session_service: SessionService,
        approval_service: ApprovalService,
        pending_image_service: PendingImageService,
        render_service: RenderService,
        runtime: CodexRuntime,
    ) -> None:
        self._connector = connector
        self._session_service = session_service
        self._approval_service = approval_service
        self._pending_image_service = pending_image_service
        self._render = render_service
        self._runtime = runtime
        self._stream_targets: dict[str, dict[str, str]] = {}
        self._thread_list_cards: dict[str, _ThreadListCardState] = {}

    async def handle_approval_action(self, action_event: ApprovalActionEvent) -> None:
        pending = await self._approval_service.get_pending(action_event.user_open_id)
        if pending is None or pending.request_id != action_event.request_id:
            logger.warning(
                "received stale approval action user_open_id=%s request_id=%s decision=%s",
                action_event.user_open_id,
                action_event.request_id,
                action_event.decision,
            )
            return

        target_state = self._stream_targets.get(action_event.user_open_id)
        if target_state is not None:
            new_message_id = await self._connector.create_stream_message(
                InboundMessage(
                    platform_message_id="",
                    user_open_id=action_event.user_open_id,
                    chat_id="",
                    chat_type="p2p",
                    text="",
                    raw_event={},
                ).target,
                self._render.approved_stream_placeholder(),
            )
            target_state["message_id"] = new_message_id
            target_state["last_markdown"] = self._render.approved_stream_placeholder()
            await self._session_service.set_current_stream_message(
                action_event.user_open_id,
                stream_message_id=new_message_id,
                source_message_id=action_event.open_message_id,
            )

        resolved = await self._runtime.resolve_pending_decision(action_event.user_open_id, action_event.decision)
        if not resolved:
            logger.warning(
                "failed to resolve approval action user_open_id=%s request_id=%s decision=%s",
                action_event.user_open_id,
                action_event.request_id,
                action_event.decision,
            )

    async def handle_message(self, inbound: InboundMessage) -> None:
        try:
            if not inbound.is_private:
                return
            logger.info(
                "received inbound message user_open_id=%s platform_message_id=%s chat_id=%s text=%r",
                inbound.user_open_id,
                inbound.platform_message_id,
                inbound.chat_id,
                inbound.text,
            )
            await self._connector.ack_message(inbound)
            text = inbound.text.strip()
            if inbound.is_text_message and not text:
                await self._connector.send_rich_text(inbound.target, self._render.unsupported_message())
                return

            pending = await self._approval_service.get_pending(inbound.user_open_id)
            if pending is not None:
                if inbound.is_image_message:
                    await self._connector.send_rich_text(
                        inbound.target,
                        self._render.image_not_supported_during_approval_message(),
                    )
                    return
                if inbound.is_text_message and text.startswith("/cwd"):
                    await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                    return
                await self._handle_pending_approval(inbound, text)
                return

            if inbound.is_text_message and text.startswith("/"):
                if await self._handle_command(inbound, text):
                    return
                await self._connector.send_rich_text(inbound.target, self._render.unknown_command_message())
                return

            if inbound.is_image_message:
                await self._handle_image_message(inbound)
                return

            await self._handle_text_message(inbound, text)
        except Exception:
            logger.exception(
                "failed to handle inbound message user_open_id=%s platform_message_id=%s",
                inbound.user_open_id,
                inbound.platform_message_id,
            )
            await self._connector.send_rich_text(inbound.target, self._render.error_message("服务端处理异常"))

    async def handle_thread_list_action(self, action_event: ThreadListActionEvent) -> None:
        message_id = action_event.open_message_id
        if not message_id:
            return
        state = self._thread_list_cards.get(message_id)
        if state is None or state.user_open_id != action_event.user_open_id:
            logger.warning(
                "received stale thread list action user_open_id=%s message_id=%s direction=%s",
                action_event.user_open_id,
                message_id,
                action_event.direction,
            )
            await self._connector.send_rich_text(
                MessageTarget(user_open_id=action_event.user_open_id),
                self._render.thread_list_state_expired_message(),
            )
            return

        if action_event.direction == "next":
            if state.next_cursor is None:
                return
            cursor = state.next_cursor
            prev_cursors = [*state.prev_cursors, state.current_cursor]
        elif action_event.direction == "prev":
            if not state.prev_cursors:
                return
            cursor = state.prev_cursors[-1]
            prev_cursors = state.prev_cursors[:-1]
        else:
            logger.warning(
                "received invalid thread list action user_open_id=%s message_id=%s direction=%s",
                action_event.user_open_id,
                message_id,
                action_event.direction,
            )
            return

        page = await self._runtime.list_threads(
            action_event.user_open_id,
            search_term=state.search_term,
            cursor=cursor,
            limit=5,
        )
        current_thread_id = (await self._session_service.get_session_state(action_event.user_open_id)).current_thread_id
        await self._connector.update_thread_list_card(
            message_id=message_id,
            page=page,
            current_thread_id=current_thread_id,
            can_go_prev=bool(prev_cursors),
            can_go_next=page.next_cursor is not None,
        )
        state.current_cursor = page.current_cursor
        state.prev_cursors = prev_cursors
        state.next_cursor = page.next_cursor

    async def _handle_command(self, inbound: InboundMessage, text: str) -> bool:
        command, _, arg = text.partition(" ")
        arg = arg.strip()
        if command == "/help":
            await self._connector.send_rich_text(inbound.target, self._render.help_message())
            return True
        if command == "/new":
            if self._runtime.is_busy():
                await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                return True
            thread_id, name = await self._runtime.create_thread(inbound.user_open_id, name=arg or None)
            await self._connector.send_rich_text(inbound.target, self._render.new_thread_message(thread_id, name))
            return True
        if command == "/list":
            state = await self._session_service.get_session_state(inbound.user_open_id)
            page = await self._runtime.list_threads(
                inbound.user_open_id,
                search_term=arg or None,
                limit=5,
            )
            message_id = await self._connector.send_thread_list_card(
                inbound.target,
                page=page,
                current_thread_id=state.current_thread_id,
                can_go_prev=False,
                can_go_next=page.next_cursor is not None,
            )
            self._thread_list_cards[message_id] = _ThreadListCardState(
                user_open_id=inbound.user_open_id,
                message_id=message_id,
                search_term=page.search_term,
                current_cursor=page.current_cursor,
                prev_cursors=[],
                next_cursor=page.next_cursor,
            )
            return True
        if command == "/models":
            models = await self._runtime.list_models()
            current_model_id, current_reasoning_effort = await self._runtime.get_default_model_config(inbound.user_open_id)
            await self._connector.send_rich_text(
                inbound.target,
                self._render.models_message(
                    models,
                    current_model_id=current_model_id,
                    current_reasoning_effort=current_reasoning_effort,
                ),
            )
            return True
        if command == "/model":
            if not arg:
                await self._connector.send_rich_text(inbound.target, self._render.model_usage_message())
                return True
            parts = arg.split()
            if len(parts) < 2:
                await self._connector.send_rich_text(inbound.target, self._render.model_usage_message())
                return True
            model_name = parts[0]
            reasoning_effort = parts[1]
            allowed_efforts = {"none", "minimal", "low", "medium", "high", "xhigh"}
            if reasoning_effort not in allowed_efforts:
                await self._connector.send_rich_text(
                    inbound.target,
                    self._render.invalid_reasoning_effort_message(reasoning_effort),
                )
                return True
            await self._runtime.set_default_model_config(inbound.user_open_id, model_name, reasoning_effort)
            await self._connector.send_rich_text(
                inbound.target,
                self._render.model_updated_message(model_name, reasoning_effort),
            )
            return True
        if command == "/resume":
            if self._runtime.is_busy():
                await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                return True
            thread_id = await self._resolve_resume_target(inbound.user_open_id, arg)
            if thread_id is None:
                await self._connector.send_rich_text(inbound.target, self._render.invalid_resume_message())
                return True
            try:
                resumed_thread_id, name = await self._runtime.resume_thread(inbound.user_open_id, thread_id)
            except JsonRpcError as exc:
                if "thread not found" not in str(exc).lower():
                    raise
                await self._connector.send_rich_text(inbound.target, self._render.invalid_resume_message())
                return True
            await self._connector.send_rich_text(
                inbound.target,
                self._render.resumed_thread_message(resumed_thread_id, name),
            )
            return True
        if command == "/rename":
            if not arg:
                await self._connector.send_rich_text(inbound.target, self._render.rename_usage_message())
                return True
            state = await self._session_service.get_session_state(inbound.user_open_id)
            if not state.current_thread_id:
                await self._connector.send_rich_text(inbound.target, self._render.no_thread_message())
                return True
            if self._runtime.is_busy():
                await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                return True
            try:
                thread_id, name = await self._runtime.rename_thread(
                    inbound.user_open_id,
                    state.current_thread_id,
                    arg,
                )
            except BusyError:
                await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                return True
            except JsonRpcError as exc:
                if "thread not found" not in str(exc).lower():
                    raise
                await self._connector.send_rich_text(inbound.target, self._render.current_thread_unavailable_message())
                return True
            await self._connector.send_rich_text(inbound.target, self._render.renamed_thread_message(thread_id, name))
            return True
        if command == "/status":
            state = await self._session_service.get_session_state(inbound.user_open_id)
            if not state.current_thread_id:
                await self._connector.send_rich_text(inbound.target, self._render.no_available_thread_message())
                return True
            try:
                thread_info = await self._runtime.read_current_thread_info(state.current_thread_id)
            except JsonRpcError as exc:
                if "thread not found" not in str(exc).lower():
                    raise
                await self._connector.send_rich_text(inbound.target, self._render.current_thread_unavailable_message())
                return True
            current_model_id, current_reasoning_effort = await self._runtime.get_default_model_config(inbound.user_open_id)
            await self._connector.send_rich_text(
                inbound.target,
                self._render.status_message(
                    thread_info=thread_info,
                    current_turn_id=state.current_turn_id,
                    waiting_for_approval=state.waiting_for_approval,
                    is_busy=self._runtime.is_busy(),
                    default_cwd=state.default_cwd,
                    current_model=current_model_id,
                    current_reasoning_effort=current_reasoning_effort,
                ),
            )
            return True
        if command == "/compact":
            state = await self._session_service.get_session_state(inbound.user_open_id)
            if not state.current_thread_id:
                await self._connector.send_rich_text(inbound.target, self._render.no_thread_message())
                return True
            if self._runtime.is_busy():
                await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                return True
            thread_id = state.current_thread_id

            async def on_compact_completed() -> None:
                await self._connector.send_rich_text(
                    inbound.target,
                    self._render.compact_completed_message(thread_id),
                )

            async def on_compact_failed(message: str) -> None:
                await self._connector.send_rich_text(
                    inbound.target,
                    self._render.compact_failed_message(thread_id, message),
                )

            try:
                await self._runtime.compact_thread(
                    inbound.user_open_id,
                    thread_id,
                    on_compact_completed,
                    on_compact_failed,
                )
            except BusyError:
                await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                return True
            except JsonRpcError as exc:
                if "thread not found" not in str(exc).lower():
                    raise
                await self._connector.send_rich_text(inbound.target, self._render.current_thread_unavailable_message())
                return True
            await self._connector.send_rich_text(inbound.target, self._render.compact_started_message())
            return True
        if command == "/cwd":
            if not arg:
                await self._connector.send_rich_text(inbound.target, self._render.cwd_usage_message())
                return True
            sync_current_thread = False
            cwd_arg = arg
            if arg == "--current":
                await self._connector.send_rich_text(inbound.target, self._render.cwd_usage_message())
                return True
            if arg.startswith("--current "):
                sync_current_thread = True
                cwd_arg = arg[len("--current ") :].strip()
            elif arg.startswith("--"):
                await self._connector.send_rich_text(inbound.target, self._render.cwd_usage_message())
                return True
            if not cwd_arg:
                await self._connector.send_rich_text(inbound.target, self._render.cwd_usage_message())
                return True
            if self._runtime.is_busy():
                await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                return True
            state = await self._session_service.get_session_state(inbound.user_open_id)
            try:
                cwd, thread_synced = await self._runtime.set_cwd(
                    inbound.user_open_id,
                    cwd_arg,
                    sync_current_thread=sync_current_thread,
                )
            except BusyError:
                await self._connector.send_rich_text(inbound.target, self._render.busy_message())
                return True
            except ValueError as exc:
                await self._connector.send_rich_text(inbound.target, self._render.cwd_invalid_message(str(exc)))
                return True
            if not sync_current_thread:
                await self._connector.send_rich_text(inbound.target, self._render.cwd_default_only_message(cwd))
                return True
            if not state.current_thread_id:
                await self._connector.send_rich_text(inbound.target, self._render.cwd_default_only_message(cwd))
                return True
            if thread_synced:
                await self._connector.send_rich_text(inbound.target, self._render.cwd_updated_message(cwd))
                return True
            await self._connector.send_rich_text(
                inbound.target,
                self._render.cwd_updated_thread_unavailable_message(cwd),
            )
            return True
        if command == "/stop":
            stopped = await self._runtime.interrupt_current_turn(inbound.user_open_id)
            await self._connector.send_rich_text(
                inbound.target,
                self._render.stop_message() if stopped else self._render.no_thread_message(),
            )
            return True
        if command == "/steer":
            if not arg:
                await self._connector.send_rich_text(inbound.target, self._render.steer_usage_message())
                return True
            steered = await self._runtime.steer_current_turn(inbound.user_open_id, arg)
            await self._connector.send_rich_text(
                inbound.target,
                self._render.steer_success_message() if steered else self._render.steer_unavailable_message(),
            )
            return True
        return False

    async def _handle_pending_approval(self, inbound: InboundMessage, text: str) -> None:
        result = await self._runtime.cancel_pending(inbound.user_open_id)
        if result is None:
            await self._connector.send_rich_text(inbound.target, self._render.no_pending_approval_message())
            return
        await self._handle_text_message(inbound, text)

    async def _handle_text_message(self, inbound: InboundMessage, text: str) -> None:
        if self._runtime.is_busy():
            await self._connector.send_rich_text(inbound.target, self._render.busy_message())
            return
        pending_images = await self._pending_image_service.get_images(inbound.user_open_id)
        state = await self._session_service.get_session_state(inbound.user_open_id)
        thread_id = state.current_thread_id
        recreated = False
        if not thread_id:
            thread_id, _ = await self._runtime.create_thread(inbound.user_open_id, None)
        else:
            thread_id, _, recreated = await self._runtime.ensure_thread_available(inbound.user_open_id, thread_id)
        initial_markdown = self._render.recreated_thread_message(thread_id) if recreated else self._render.stream_placeholder()
        stream_message_id = await self._connector.create_stream_message(
            inbound.target,
            initial_markdown,
        )
        target_state = {
            "message_id": stream_message_id,
            "last_markdown": initial_markdown,
        }
        self._stream_targets[inbound.user_open_id] = target_state
        await self._session_service.set_current_stream_message(
            inbound.user_open_id,
            stream_message_id=stream_message_id,
            source_message_id=inbound.platform_message_id,
        )

        async def on_stream_update(markdown: str, final: bool = False) -> None:
            target_state["last_markdown"] = markdown
            await self._connector.update_stream_message(
                target_state["message_id"],
                markdown,
                final=final,
            )
            if final:
                self._stream_targets.pop(inbound.user_open_id, None)

        async def on_approval_request(prompt: ApprovalPrompt, current_full_text: str) -> None:
            latest_markdown = current_full_text or target_state["last_markdown"]
            approval_markdown = self._render.approval_inline_message(latest_markdown, prompt)
            target_state["last_markdown"] = approval_markdown
            await self._connector.update_stream_message(
                target_state["message_id"],
                approval_markdown,
                final=True,
                approval_prompt=prompt,
            )
            logger.info(
                "stream message updated into approval state user_open_id=%s request_id=%s message_id=%s",
                inbound.user_open_id,
                prompt.request_id,
                target_state["message_id"],
            )

        async def on_error(message: str) -> None:
            await self._connector.update_stream_message(
                target_state["message_id"],
                self._render.error_message(message),
                final=True,
            )
            self._stream_targets.pop(inbound.user_open_id, None)

        async def on_image_outputs(images: list[GeneratedImageOutput]) -> None:
            failures: list[str] = []
            for image in images:
                try:
                    await self._connector.send_local_image(inbound.target, image.path)
                except Exception as exc:
                    logger.exception(
                        "failed to send generated image user_open_id=%s thread_id=%s item_id=%s path=%s",
                        inbound.user_open_id,
                        thread_id,
                        image.item_id,
                        image.path,
                    )
                    failures.append(str(exc))
            if failures:
                await self._connector.send_rich_text(
                    inbound.target,
                    self._render.image_output_send_failed_message(failures[0]),
                )

        callbacks = StreamCallbacks(
            on_stream_update=on_stream_update,
            on_approval_request=on_approval_request,
            on_error=on_error,
            on_image_outputs=on_image_outputs,
        )
        inputs: list[object]
        if pending_images:
            try:
                image_paths = []
                for image in pending_images:
                    image_inbound = InboundMessage(
                        platform_message_id=image.message_id,
                        user_open_id=inbound.user_open_id,
                        chat_id=inbound.chat_id,
                        chat_type=inbound.chat_type,
                        message_type="image",
                        text="",
                        image_key=image.image_key,
                        raw_event={},
                    )
                    image_paths.append(await self._connector.download_message_image(image_inbound))
            except Exception as exc:
                logger.exception("failed to download pending image(s) user_open_id=%s", inbound.user_open_id)
                await self._connector.send_rich_text(
                    inbound.target,
                    self._render.image_download_failed_message(str(exc)),
                )
                return
            inputs = [*(LocalImageInput(path=path) for path in image_paths), TextInput(text)]
        else:
            inputs = [TextInput(text)]
        try:
            turn_id = await self._runtime.submit_inputs(inbound.user_open_id, thread_id, inputs, callbacks)
            if pending_images:
                await self._pending_image_service.clear(inbound.user_open_id)
            await self._session_service.set_current_turn(inbound.user_open_id, turn_id)
        except BusyError:
            await self._connector.send_rich_text(inbound.target, self._render.busy_message())

    async def _handle_image_message(self, inbound: InboundMessage) -> None:
        if self._runtime.is_busy():
            await self._connector.send_rich_text(inbound.target, self._render.busy_message())
            return
        if not inbound.image_key:
            await self._connector.send_rich_text(inbound.target, self._render.image_download_failed_message("image_key is missing"))
            return
        try:
            count = await self._pending_image_service.add_image(
                inbound.user_open_id,
                inbound.platform_message_id,
                inbound.image_key,
            )
        except ValueError:
            await self._connector.send_rich_text(
                inbound.target,
                self._render.pending_images_limit_message(3),
            )
            return
        await self._connector.send_rich_text(
            inbound.target,
            self._render.pending_image_received_message(count),
        )

    async def _resolve_resume_target(self, user_open_id: str, arg: str) -> str | None:
        del user_open_id
        return arg or None
