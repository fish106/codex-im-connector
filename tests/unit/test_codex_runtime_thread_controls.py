from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from codex_app_server.generated.v2_all import (
    AgentMessageThreadItem,
    ContextCompactedNotification,
    ImageGenerationThreadItem,
    ImageViewThreadItem,
    ItemCompletedNotification,
    ServerRequestResolvedNotification,
    TurnCompletedNotification,
)

from client.codex_runtime import CodexRuntime
from core.config import get_settings


class DummySessionService:
    def __init__(self) -> None:
        self.renamed = []
        self.default_cwds = []
        self.state = SimpleNamespace(
            default_model=None,
            default_reasoning_effort=None,
            current_thread_id=None,
            default_cwd=None,
        )

    async def rename_thread(self, user_open_id: str, thread_id: str, name: str | None) -> None:
        self.renamed.append((user_open_id, thread_id, name))

    async def set_default_cwd(self, user_open_id: str, cwd: str) -> None:
        self.default_cwds.append((user_open_id, cwd))
        self.state.default_cwd = cwd

    async def get_session_state(self, user_open_id: str):
        return self.state

    async def get_effective_cwd(self, user_open_id: str, fallback_cwd: str) -> str:
        return self.state.default_cwd or fallback_cwd

    async def find_user_open_id_by_thread_id(self, thread_id: str) -> str | None:
        if thread_id == "thread-1":
            return "ou_1"
        return None

    async def set_current_turn(self, user_open_id: str, turn_id: str | None) -> None:
        self.state.current_turn_id = turn_id

    async def set_current_thread(self, user_open_id: str, thread_id: str | None) -> None:
        self.state.current_thread_id = thread_id

    async def set_waiting_for_approval(self, user_open_id: str, waiting: bool) -> None:
        return None

    async def upsert_thread_binding(self, user_open_id: str, thread_id: str, name: str | None = None) -> None:
        return None


class DummyApprovalService:
    def __init__(self) -> None:
        self.resolved_users = []

    async def mark_resolved_by_user(self, user_open_id: str) -> None:
        self.resolved_users.append(user_open_id)


class FakeThread:
    def __init__(self) -> None:
        self.names = []
        self.compacted = 0

    async def set_name(self, name: str):
        self.names.append(name)
        return None

    async def compact(self):
        self.compacted += 1
        return None


def build_runtime(session_service: DummySessionService | None = None) -> CodexRuntime:
    return CodexRuntime(
        settings=get_settings(),
        session_service=session_service or DummySessionService(),
        approval_service=DummyApprovalService(),
        loop=asyncio.get_running_loop(),
    )


@pytest.mark.asyncio
async def test_rename_thread_updates_remote_and_local_binding(monkeypatch):
    session_service = DummySessionService()
    runtime = build_runtime(session_service)
    fake_thread = FakeThread()

    async def fake_get_thread_handle(thread_id: str):
        assert thread_id == "thread-1"
        return fake_thread

    monkeypatch.setattr(runtime, "_get_thread_handle", fake_get_thread_handle)

    thread_id, name = await runtime.rename_thread("ou_1", "thread-1", "Renamed")

    assert thread_id == "thread-1"
    assert name == "Renamed"
    assert fake_thread.names == ["Renamed"]
    assert session_service.renamed == [("ou_1", "thread-1", "Renamed")]


@pytest.mark.asyncio
async def test_read_current_thread_info_maps_thread_read_response(monkeypatch):
    runtime = build_runtime()

    async def fake_read_thread(thread_id: str):
        assert thread_id == "thread-1"
        return SimpleNamespace(
            thread=SimpleNamespace(
                id="thread-1",
                name="Alpha",
                source=SimpleNamespace(root=SimpleNamespace(value="appServer")),
                model_provider="openai",
                cwd=SimpleNamespace(root="/tmp/project-a"),
                preview="preview-a",
                created_at=1712345600,
                updated_at=1712345678,
            )
        )

    monkeypatch.setattr(runtime, "read_thread", fake_read_thread)

    info = await runtime.read_current_thread_info("thread-1")

    assert info.thread_id == "thread-1"
    assert info.name == "Alpha"
    assert info.source == "appServer"
    assert info.model_provider == "openai"
    assert info.cwd == "/tmp/project-a"
    assert info.preview == "preview-a"
    assert info.created_at
    assert info.updated_at


@pytest.mark.asyncio
async def test_compact_thread_sets_busy_and_notifies_on_completion(monkeypatch):
    runtime = build_runtime()
    fake_thread = FakeThread()
    completed = asyncio.Event()

    async def fake_get_thread_handle(thread_id: str):
        assert thread_id == "thread-1"
        return fake_thread

    notifications = [
        SimpleNamespace(
            method="thread/compacted",
            payload=ContextCompactedNotification(threadId="thread-1", turnId="turn-1"),
        )
    ]

    async def fake_next_notification():
        await asyncio.sleep(0)
        return notifications.pop(0)

    runtime._codex = SimpleNamespace(_client=SimpleNamespace(next_notification=fake_next_notification))
    monkeypatch.setattr(runtime, "_get_thread_handle", fake_get_thread_handle)

    async def on_completed() -> None:
        completed.set()

    async def on_failed(message: str) -> None:
        raise AssertionError(message)

    await runtime.compact_thread("ou_1", "thread-1", on_completed, on_failed)
    assert runtime.is_busy() is True

    await asyncio.wait_for(completed.wait(), timeout=1)
    assert fake_thread.compacted == 1
    assert runtime.is_busy() is False


@pytest.mark.asyncio
async def test_compact_thread_timeout_clears_busy_and_notifies_failure(monkeypatch):
    runtime = build_runtime()
    runtime._settings.CODEX_COMPACT_TIMEOUT_S = 0.01
    fake_thread = FakeThread()
    failed = asyncio.Event()
    failure_messages: list[str] = []

    async def fake_get_thread_handle(thread_id: str):
        return fake_thread

    async def fake_next_notification():
        await asyncio.sleep(1)
        return SimpleNamespace(method="noop", payload=None)

    runtime._codex = SimpleNamespace(_client=SimpleNamespace(next_notification=fake_next_notification))
    monkeypatch.setattr(runtime, "_get_thread_handle", fake_get_thread_handle)

    async def on_completed() -> None:
        raise AssertionError("should not complete")

    async def on_failed(message: str) -> None:
        failure_messages.append(message)
        failed.set()

    await runtime.compact_thread("ou_1", "thread-1", on_completed, on_failed)
    assert runtime.is_busy() is True

    await asyncio.wait_for(failed.wait(), timeout=1)
    assert fake_thread.compacted == 1
    assert runtime.is_busy() is False
    assert "超时" in failure_messages[0]


@pytest.mark.asyncio
async def test_run_turn_resolves_approval_by_thread_mapping(monkeypatch):
    session_service = DummySessionService()
    approval_service = DummyApprovalService()
    runtime = CodexRuntime(
        settings=get_settings(),
        session_service=session_service,
        approval_service=approval_service,
        loop=asyncio.get_running_loop(),
    )
    session_service.state.default_model = "gpt-5.5"
    session_service.state.default_reasoning_effort = "medium"

    notifications = [
        SimpleNamespace(method="serverRequest/resolved", payload=SimpleNamespace(thread_id="thread-1", request_id=0)),
        SimpleNamespace(
            method="turn/completed",
            payload=TurnCompletedNotification(
                threadId="thread-1",
                turn={"id": "turn-1", "status": "completed", "startedAt": 1, "items": []},
            ),
        ),
    ]
    notifications[0] = SimpleNamespace(
        method="serverRequest/resolved",
        payload=ServerRequestResolvedNotification(requestId=0, threadId="thread-1"),
    )

    class FakeTurnHandle:
        id = "turn-1"

        async def stream(self):
            for notification in notifications:
                yield notification

    class FakeThread:
        async def read(self, include_turns: bool = False):
            return SimpleNamespace(thread=SimpleNamespace(cwd=SimpleNamespace(root="/tmp/project-a")))

        async def turn(self, inputs, **kwargs):
            return FakeTurnHandle()

    async def fake_get_thread_handle(thread_id: str):
        return FakeThread()

    callbacks = SimpleNamespace(
        on_stream_update=_async_noop,
        on_approval_request=_async_noop_approval,
        on_error=_async_noop_error,
    )
    monkeypatch.setattr(runtime, "_get_thread_handle", fake_get_thread_handle)

    started: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    turn_done = asyncio.Event()
    await runtime._run_turn("ou_1", "thread-1", [SimpleNamespace(text="hi")], callbacks, started, turn_done)

    assert approval_service.resolved_users == ["ou_1", "ou_1"]


@pytest.mark.asyncio
async def test_run_turn_skips_resolved_cleanup_when_thread_binding_missing(monkeypatch):
    session_service = DummySessionService()
    approval_service = DummyApprovalService()
    runtime = CodexRuntime(
        settings=get_settings(),
        session_service=session_service,
        approval_service=approval_service,
        loop=asyncio.get_running_loop(),
    )
    session_service.state.default_model = "gpt-5.5"
    session_service.state.default_reasoning_effort = "medium"

    async def missing_thread_mapping(thread_id: str) -> str | None:
        return None

    session_service.find_user_open_id_by_thread_id = missing_thread_mapping

    notifications = [
        SimpleNamespace(
            method="serverRequest/resolved",
            payload=ServerRequestResolvedNotification(requestId=0, threadId="thread-1"),
        ),
        SimpleNamespace(
            method="turn/completed",
            payload=TurnCompletedNotification(
                threadId="thread-1",
                turn={"id": "turn-1", "status": "completed", "startedAt": 1, "items": []},
            ),
        ),
    ]

    class FakeTurnHandle:
        id = "turn-1"

        async def stream(self):
            for notification in notifications:
                yield notification

    class FakeThread:
        async def read(self, include_turns: bool = False):
            return SimpleNamespace(thread=SimpleNamespace(cwd=SimpleNamespace(root="/tmp/project-a")))

        async def turn(self, inputs, **kwargs):
            return FakeTurnHandle()

    async def fake_get_thread_handle(thread_id: str):
        return FakeThread()

    callbacks = SimpleNamespace(
        on_stream_update=_async_noop,
        on_approval_request=_async_noop_approval,
        on_error=_async_noop_error,
    )
    monkeypatch.setattr(runtime, "_get_thread_handle", fake_get_thread_handle)

    started: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    turn_done = asyncio.Event()
    await runtime._run_turn("ou_1", "thread-1", [SimpleNamespace(text="hi")], callbacks, started, turn_done)

    assert approval_service.resolved_users == ["ou_1"]


async def _async_noop(*args, **kwargs):
    return None


async def _async_noop_approval(*args, **kwargs):
    return None


async def _async_noop_error(*args, **kwargs):
    return None


async def _async_noop_images(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_approval_handler_maps_mcp_elicitation_fields_and_default_buttons():
    runtime = build_runtime()
    captured = {}

    class DummyContext:
        def __init__(self) -> None:
            import threading

            self.decision_event = threading.Event()
            self.decision = "accept"
            self.decision_event.set()

        def to_prompt(self):
            return None

    async def fake_create_pending(**kwargs):
        captured.update(kwargs)
        return DummyContext()

    runtime._approval_service.create_pending = fake_create_pending
    runtime._active_state = SimpleNamespace(
        user_open_id="ou_1",
        thread_id="thread-1",
        turn_id="turn-1",
        callbacks=SimpleNamespace(on_approval_request=_async_noop_approval),
        full_text="",
    )

    result = await asyncio.to_thread(
        runtime._approval_handler,
        "mcpServer/elicitation/request",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "message": 'Allow the playwright MCP server to run tool "browser_navigate"?',
            "_meta": {
                "tool_description": "Navigate to a URL",
                "tool_params_display": [
                    {
                        "name": "url",
                        "display_name": "url",
                        "value": "https://www.qq.com",
                    }
                ],
            },
            "requestedSchema": {"type": "object", "properties": {}},
        },
    )

    assert result == {"action": "accept", "content": {}}
    assert captured["reason"] == 'Allow the playwright MCP server to run tool "browser_navigate"?'
    assert captured["command"] == "Navigate to a URL (url=https://www.qq.com)"
    assert captured["available_decisions"] == ["accept", "cancel"]


@pytest.mark.asyncio
async def test_approval_handler_returns_cancel_for_elicitation_cancel():
    runtime = build_runtime()

    class DummyContext:
        def __init__(self) -> None:
            import threading

            self.decision_event = threading.Event()
            self.decision = "cancel"
            self.decision_event.set()

        def to_prompt(self):
            return None

    async def fake_create_pending(**kwargs):
        return DummyContext()

    runtime._approval_service.create_pending = fake_create_pending
    runtime._active_state = SimpleNamespace(
        user_open_id="ou_1",
        thread_id="thread-1",
        turn_id="turn-1",
        callbacks=SimpleNamespace(on_approval_request=_async_noop_approval),
        full_text="",
    )

    result = await asyncio.to_thread(
        runtime._approval_handler,
        "mcpServer/elicitation/request",
        {
            "message": "confirm?",
            "requestedSchema": {"type": "object", "properties": {}},
        },
    )

    assert result == {"action": "cancel"}


@pytest.mark.asyncio
async def test_set_cwd_updates_default_only_without_current_thread(tmp_path):
    session_service = DummySessionService()
    runtime = build_runtime(session_service)
    runtime._settings.APP_ROOT_PATH = str(tmp_path)
    (tmp_path / "workspace").mkdir()

    cwd, thread_synced = await runtime.set_cwd("ou_1", str(tmp_path / "workspace"))

    assert cwd == str((tmp_path / "workspace").resolve())
    assert thread_synced is False
    assert session_service.default_cwds == [("ou_1", str((tmp_path / "workspace").resolve()))]


@pytest.mark.asyncio
async def test_set_cwd_default_mode_does_not_sync_current_thread(tmp_path, monkeypatch):
    session_service = DummySessionService()
    session_service.state.current_thread_id = "thread-1"
    runtime = build_runtime(session_service)
    runtime._settings.APP_ROOT_PATH = str(tmp_path)
    (tmp_path / "workspace").mkdir()
    resume_calls = []

    async def fake_resume_thread_handle(thread_id: str, *, model_name=None, reasoning_effort=None, cwd=None):
        resume_calls.append((thread_id, model_name, reasoning_effort, cwd))
        return SimpleNamespace(id=thread_id)

    monkeypatch.setattr(runtime, "_resume_thread_handle", fake_resume_thread_handle)

    cwd, thread_synced = await runtime.set_cwd("ou_1", str(tmp_path / "workspace"))

    assert cwd == str((tmp_path / "workspace").resolve())
    assert thread_synced is False
    assert resume_calls == []


@pytest.mark.asyncio
async def test_run_turn_collects_generated_images_and_dispatches_callback(tmp_path, monkeypatch):
    session_service = DummySessionService()
    approval_service = DummyApprovalService()
    runtime = CodexRuntime(
        settings=get_settings(),
        session_service=session_service,
        approval_service=approval_service,
        loop=asyncio.get_running_loop(),
    )
    session_service.state.default_model = "gpt-5.5"
    session_service.state.default_reasoning_effort = "medium"
    generated_file = tmp_path / "generated.png"
    generated_file.write_bytes(b"image")
    viewed_file = tmp_path / "viewed.png"
    viewed_file.write_bytes(b"image")
    received_images = []

    notifications = [
        SimpleNamespace(
            method="item/completed",
            payload=ItemCompletedNotification(
                threadId="thread-1",
                turnId="turn-1",
                item=ImageGenerationThreadItem(
                    id="img-1",
                    result="ok",
                    revisedPrompt=None,
                    savedPath=str(generated_file),
                    status="completed",
                    type="imageGeneration",
                ),
            ),
        ),
        SimpleNamespace(
            method="item/completed",
            payload=ItemCompletedNotification(
                threadId="thread-1",
                turnId="turn-1",
                item=ImageViewThreadItem(
                    id="img-2",
                    path=str(viewed_file),
                    type="imageView",
                ),
            ),
        ),
        SimpleNamespace(
            method="turn/completed",
            payload=TurnCompletedNotification(
                threadId="thread-1",
                turn={"id": "turn-1", "status": "completed", "startedAt": 1, "items": []},
            ),
        ),
    ]

    class FakeTurnHandle:
        id = "turn-1"

        async def stream(self):
            for notification in notifications:
                yield notification

    class FakeThread:
        async def read(self, include_turns: bool = False):
            return SimpleNamespace(thread=SimpleNamespace(cwd=SimpleNamespace(root="/tmp/project-a")))

        async def turn(self, inputs, **kwargs):
            return FakeTurnHandle()

    async def fake_get_thread_handle(thread_id: str):
        return FakeThread()

    async def on_image_outputs(images):
        received_images.extend(images)

    callbacks = SimpleNamespace(
        on_stream_update=_async_noop,
        on_approval_request=_async_noop_approval,
        on_error=_async_noop_error,
        on_image_outputs=on_image_outputs,
    )
    monkeypatch.setattr(runtime, "_get_thread_handle", fake_get_thread_handle)

    started: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    turn_done = asyncio.Event()
    await runtime._run_turn("ou_1", "thread-1", [SimpleNamespace(text="hi")], callbacks, started, turn_done)

    assert [(item.item_id, item.path, item.source_type) for item in received_images] == [
        ("img-1", str(generated_file.resolve()), "image_generation"),
        ("img-2", str(viewed_file.resolve()), "image_view"),
    ]


@pytest.mark.asyncio
async def test_run_turn_extracts_markdown_image_links_from_agent_message(tmp_path, monkeypatch):
    session_service = DummySessionService()
    approval_service = DummyApprovalService()
    runtime = CodexRuntime(
        settings=get_settings(),
        session_service=session_service,
        approval_service=approval_service,
        loop=asyncio.get_running_loop(),
    )
    session_service.state.default_model = "gpt-5.5"
    session_service.state.default_reasoning_effort = "medium"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    linked_file = workspace / "qq-homepage.png"
    linked_file.write_bytes(b"image")
    session_service.get_effective_cwd = _build_effective_cwd_override(str(workspace))
    received_images = []

    notifications = [
        SimpleNamespace(
            method="item/completed",
            payload=ItemCompletedNotification(
                threadId="thread-1",
                turnId="turn-1",
                item=AgentMessageThreadItem(
                    id="msg-1",
                    memoryCitation=None,
                    phase="final_answer",
                    text="已经打开 [`qq-homepage.png`](./qq-homepage.png)。",
                    type="agentMessage",
                ),
            ),
        ),
        SimpleNamespace(
            method="turn/completed",
            payload=TurnCompletedNotification(
                threadId="thread-1",
                turn={"id": "turn-1", "status": "completed", "startedAt": 1, "items": []},
            ),
        ),
    ]

    class FakeTurnHandle:
        id = "turn-1"

        async def stream(self):
            for notification in notifications:
                yield notification

    class FakeThread:
        async def read(self, include_turns: bool = False):
            return SimpleNamespace(thread=SimpleNamespace(cwd=SimpleNamespace(root=str(workspace))))

        async def turn(self, inputs, **kwargs):
            return FakeTurnHandle()

    async def fake_get_thread_handle(thread_id: str):
        return FakeThread()

    async def on_image_outputs(images):
        received_images.extend(images)

    callbacks = SimpleNamespace(
        on_stream_update=_async_noop,
        on_approval_request=_async_noop_approval,
        on_error=_async_noop_error,
        on_image_outputs=on_image_outputs,
    )
    monkeypatch.setattr(runtime, "_get_thread_handle", fake_get_thread_handle)

    started: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    turn_done = asyncio.Event()
    await runtime._run_turn("ou_1", "thread-1", [SimpleNamespace(text="hi")], callbacks, started, turn_done)

    assert [(item.path, item.source_type) for item in received_images] == [
        (str(linked_file.resolve()), "agent_message_link")
    ]


def _build_effective_cwd_override(cwd: str):
    async def _get_effective_cwd(user_open_id: str, fallback_cwd: str) -> str:
        return cwd

    return _get_effective_cwd


@pytest.mark.asyncio
async def test_set_cwd_syncs_current_thread_when_available(tmp_path, monkeypatch):
    session_service = DummySessionService()
    session_service.state.current_thread_id = "thread-1"
    runtime = build_runtime(session_service)
    runtime._settings.APP_ROOT_PATH = str(tmp_path)
    runtime._settings.CODEX_ALLOWED_CWD_ROOTS = f"[\"{tmp_path / 'allowed'}\"]"
    runtime._models_cache = [
        SimpleNamespace(
            model_id="gpt-5.5",
            is_default=True,
            default_reasoning_effort="medium",
        )
    ]
    (tmp_path / "workspace").mkdir()
    (tmp_path / "allowed").mkdir()
    target = tmp_path / "allowed" / "child"
    target.mkdir()
    calls = []

    async def fake_resume_thread_handle(thread_id: str, *, model_name=None, reasoning_effort=None, cwd=None):
        calls.append((thread_id, model_name, reasoning_effort, cwd))
        return SimpleNamespace(id=thread_id)

    monkeypatch.setattr(runtime, "_resume_thread_handle", fake_resume_thread_handle)

    cwd, thread_synced = await runtime.set_cwd("ou_1", str(target), sync_current_thread=True)

    assert cwd == str(target.resolve())
    assert thread_synced is True
    assert session_service.default_cwds == [("ou_1", str(target.resolve()))]
    assert calls[-1][0] == "thread-1"
    assert calls[-1][-1] == str(target.resolve())


@pytest.mark.asyncio
async def test_set_cwd_rejects_path_outside_allowed_roots(tmp_path):
    session_service = DummySessionService()
    runtime = build_runtime(session_service)
    runtime._settings.APP_ROOT_PATH = str(tmp_path)
    (tmp_path / "workspace").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="not under allowed roots"):
        await runtime.set_cwd("ou_1", str(outside))


@pytest.mark.asyncio
async def test_resume_thread_does_not_use_default_cwd(tmp_path, monkeypatch):
    session_service = DummySessionService()
    session_service.state.default_model = "gpt-5.5"
    session_service.state.default_reasoning_effort = "medium"
    session_service.state.default_cwd = str(tmp_path / "workspace")
    runtime = build_runtime(session_service)
    runtime._models_cache = [
        SimpleNamespace(
            model_id="gpt-5.5",
            is_default=True,
            default_reasoning_effort="medium",
        )
    ]
    calls = []

    async def fake_read(include_turns: bool = False):
        return SimpleNamespace(thread=SimpleNamespace(name="Alpha"))

    async def fake_resume_thread_handle_with_read(thread_id: str, *, model_name=None, reasoning_effort=None, cwd=None):
        calls.append((thread_id, model_name, reasoning_effort, cwd))
        return SimpleNamespace(id=thread_id, read=fake_read)

    monkeypatch.setattr(runtime, "_resume_thread_handle", fake_resume_thread_handle_with_read)

    await runtime.resume_thread("ou_1", "thread-1")

    assert calls[-1][-1] is None
