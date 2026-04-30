from __future__ import annotations

from types import SimpleNamespace

import pytest
from codex_app_server.errors import JsonRpcError

from model.connector_models import (
    AvailableModelInfo,
    CurrentThreadInfo,
    GeneratedImageOutput,
    InboundMessage,
    ThreadListActionEvent,
    ThreadListPage,
    ThreadRecord,
)
from service.pending_image_service import PendingImageService
from service.render_service import RenderService
from service.router_service import RouterService


class FakeConnector:
    def __init__(self) -> None:
        self.acks = []
        self.sent = []
        self.sent_images = []
        self.stream_created = []
        self.stream_updates = []
        self.thread_list_created = []
        self.thread_list_updated = []

    async def ack_message(self, inbound):
        self.acks.append(inbound.platform_message_id)

    async def send_rich_text(self, target, markdown_text):
        self.sent.append(markdown_text)
        return "msg-rich"

    async def create_stream_message(self, target, markdown_text):
        self.stream_created.append(markdown_text)
        return "msg-stream"

    async def download_message_image(self, inbound):
        return f"/tmp/{inbound.platform_message_id}.png"

    async def send_local_image(self, target, local_path):
        self.sent_images.append(local_path)
        return "msg-image"

    async def send_thread_list_card(self, target, page, current_thread_id, can_go_prev, can_go_next):
        self.thread_list_created.append((page, current_thread_id, can_go_prev, can_go_next))
        return "om-list-1"

    async def update_thread_list_card(self, message_id, page, current_thread_id, can_go_prev, can_go_next):
        self.thread_list_updated.append((message_id, page, current_thread_id, can_go_prev, can_go_next))

    async def update_stream_message(self, message_id, markdown_text, final=False, approval_prompt=None):
        self.stream_updates.append((message_id, markdown_text, final, approval_prompt))


class FakeSessionService:
    def __init__(self) -> None:
        self.state = SimpleNamespace(
            current_thread_id=None,
            current_turn_id=None,
            waiting_for_approval=False,
            default_cwd="/tmp/default-cwd",
        )

    async def get_session_state(self, user_open_id):
        return self.state

    async def set_current_turn(self, user_open_id, turn_id):
        self.state.current_turn_id = turn_id

    async def set_current_stream_message(self, user_open_id, stream_message_id, source_message_id=None):
        return None


class FakeApprovalContext:
    def __init__(self) -> None:
        self.resolved_event = SimpleNamespace(wait=self._noop)
        self.thread_id = "thread-1"
        self.turn_id = "turn-1"
        self.request_id = "req-1"

    async def _noop(self):
        return None


class FakeApprovalService:
    def __init__(self) -> None:
        self.pending = None

    async def get_pending(self, user_open_id):
        return self.pending

    async def resolve(self, user_open_id, decision):
        return self.pending


class FakeRuntime:
    def __init__(self) -> None:
        self.busy = False
        self.created = []
        self.listed = []
        self.resumed = []
        self.submitted = []
        self.submitted_callbacks = []
        self.stop_called = False
        self.cancel_called = False
        self.steer_called = None
        self.model_set = None
        self.renamed = []
        self.compacted = []
        self.cwd_set = []

    def is_busy(self):
        return self.busy

    async def create_thread(self, user_open_id, name=None):
        self.created.append((user_open_id, name))
        return "thread-new", name

    async def list_threads(self, user_open_id, search_term=None, cursor=None, limit=5):
        self.listed.append((user_open_id, search_term, cursor, limit))
        if cursor == "cursor-2":
            return ThreadListPage(
                items=[
                    ThreadRecord(
                        thread_id="thread-3",
                        name="Gamma",
                        cwd="/tmp/project-c",
                        preview="preview-c",
                        updated_at="2026-04-27 10:00:02",
                    ),
                ],
                current_cursor="cursor-2",
                next_cursor=None,
                search_term=search_term,
            )
        return ThreadListPage(
            items=[
                ThreadRecord(
                    thread_id="thread-1",
                    name="Alpha",
                    cwd="/tmp/project-a",
                    preview="preview-a",
                    updated_at="2026-04-27 10:00:00",
                ),
                ThreadRecord(
                    thread_id="thread-2",
                    name="Beta",
                    cwd="/tmp/project-b",
                    preview="preview-b",
                    updated_at="2026-04-27 10:00:01",
                ),
            ],
            current_cursor=cursor,
            next_cursor="cursor-2",
            search_term=search_term,
        )

    async def list_models(self):
        return [
            AvailableModelInfo(
                model_id="gpt-5.5",
                display_name="GPT-5.5",
                is_default=True,
                default_reasoning_effort="medium",
                supported_reasoning_efforts=["low", "medium", "high"],
            )
        ]

    async def resume_thread(self, user_open_id, thread_id):
        if thread_id == "missing":
            raise JsonRpcError(-32600, "thread not found: missing")
        self.resumed.append(thread_id)
        return thread_id, "Resumed"

    async def ensure_thread_available(self, user_open_id, thread_id):
        if thread_id == "missing":
            raise JsonRpcError(-32600, "thread not found: missing")
        return thread_id, "Resumed", False

    async def rename_thread(self, user_open_id, thread_id, name):
        if thread_id == "missing":
            raise JsonRpcError(-32600, "thread not found: missing")
        self.renamed.append((user_open_id, thread_id, name))
        return thread_id, name

    async def read_current_thread_info(self, thread_id):
        if thread_id == "missing":
            raise JsonRpcError(-32600, "thread not found: missing")
        return CurrentThreadInfo(
            thread_id=thread_id,
            name="Alpha",
            source="appServer",
            model_provider="openai",
            cwd="/tmp/project-a",
            preview="preview-a",
            created_at="2026-04-27 10:00:00",
            updated_at="2026-04-27 10:10:00",
        )

    async def compact_thread(self, user_open_id, thread_id, on_completed, on_failed):
        if thread_id == "missing":
            raise JsonRpcError(-32600, "thread not found: missing")
        self.compacted.append((user_open_id, thread_id, on_completed, on_failed))

    async def set_cwd(self, user_open_id, cwd, *, sync_current_thread=False):
        self.cwd_set.append((user_open_id, cwd, sync_current_thread))
        if cwd == "/invalid":
            raise ValueError("cwd is not under allowed roots: /invalid")
        if cwd == "/thread-missing":
            return "/thread-missing", False
        return cwd, True

    async def submit_inputs(self, user_open_id, thread_id, inputs, callbacks):
        self.submitted.append((user_open_id, thread_id, inputs))
        self.submitted_callbacks.append(callbacks)
        return "turn-123"

    async def interrupt_current_turn(self, user_open_id):
        self.stop_called = True
        return True

    async def steer_current_turn(self, user_open_id, text):
        self.steer_called = (user_open_id, text)
        return True

    async def set_default_model_config(self, user_open_id, model_name, reasoning_effort):
        self.model_set = (user_open_id, model_name, reasoning_effort)

    async def get_default_model_config(self, user_open_id):
        return "gpt-5.5", "high"

    async def cancel_pending(self, user_open_id):
        self.cancel_called = True
        return ("thread-1", "turn-1")


@pytest.fixture
def inbound() -> InboundMessage:
    return InboundMessage(
        platform_message_id="m1",
        user_open_id="ou_1",
        chat_id="oc_1",
        chat_type="p2p",
        message_type="text",
        text="/help",
        image_key=None,
        raw_event={},
    )


@pytest.fixture
def router():
    return RouterService(
        connector=FakeConnector(),
        session_service=FakeSessionService(),
        approval_service=FakeApprovalService(),
        pending_image_service=PendingImageService(max_images_per_user=3),
        render_service=RenderService(),
        runtime=FakeRuntime(),
    )


@pytest.mark.asyncio
async def test_help_command_is_local(router, inbound):
    await router.handle_message(inbound)
    assert router._connector.acks == ["m1"]
    assert any("/help" in message for message in router._connector.sent)


@pytest.mark.asyncio
async def test_plain_text_creates_thread_and_submits(router, inbound):
    inbound.text = "hello codex"
    await router.handle_message(inbound)
    assert router._runtime.created == [("ou_1", None)]
    submitted_inputs = router._runtime.submitted[0][2]
    assert len(submitted_inputs) == 1
    assert getattr(submitted_inputs[0], "text", None) == "hello codex"
    assert router._connector.stream_created == [router._render.stream_placeholder()]


@pytest.mark.asyncio
async def test_pending_approval_non_approve_cancels_then_resubmits(router, inbound):
    inbound.text = "请改成只读检查"
    router._approval_service.pending = FakeApprovalContext()
    await router.handle_message(inbound)
    assert router._runtime.cancel_called is True
    submitted_inputs = router._runtime.submitted[0][2]
    assert getattr(submitted_inputs[0], "text", None) == "请改成只读检查"


@pytest.mark.asyncio
async def test_steer_command_calls_runtime(router, inbound):
    inbound.text = "/steer 继续只读检查"
    await router.handle_message(inbound)
    assert router._runtime.steer_called == ("ou_1", "继续只读检查")
    assert router._connector.sent[-1] == router._render.steer_success_message()


@pytest.mark.asyncio
async def test_models_command_lists_available_models(router, inbound):
    inbound.text = "/models"
    await router.handle_message(inbound)
    assert "gpt-5.5" in router._connector.sent[-1]
    assert "（当前）" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_model_command_sets_default_model_and_effort(router, inbound):
    inbound.text = "/model gpt-5.5 high"
    await router.handle_message(inbound)
    assert router._runtime.model_set == ("ou_1", "gpt-5.5", "high")


@pytest.mark.asyncio
async def test_status_command_returns_no_available_thread_when_empty(router, inbound):
    inbound.text = "/status"
    await router.handle_message(inbound)
    assert router._connector.sent[-1] == router._render.no_available_thread_message()


@pytest.mark.asyncio
async def test_status_command_returns_thread_summary_and_session_state(router, inbound):
    router._session_service.state.current_thread_id = "thread-1"
    router._session_service.state.current_turn_id = "turn-1"
    router._session_service.state.waiting_for_approval = True
    inbound.text = "/status"
    await router.handle_message(inbound)
    message = router._connector.sent[-1]
    assert "thread-1" in message
    assert "appServer" in message
    assert "openai" in message
    assert "gpt-5.5" in message
    assert "high" in message
    assert "turn-1" in message


@pytest.mark.asyncio
async def test_status_command_reports_unavailable_current_thread(router, inbound):
    router._session_service.state.current_thread_id = "missing"
    inbound.text = "/status"
    await router.handle_message(inbound)
    assert router._connector.sent[-1] == router._render.current_thread_unavailable_message()


@pytest.mark.asyncio
async def test_rename_command_requires_name(router, inbound):
    inbound.text = "/rename"
    await router.handle_message(inbound)
    assert router._connector.sent[-1] == router._render.rename_usage_message()


@pytest.mark.asyncio
async def test_rename_command_renames_current_thread(router, inbound):
    router._session_service.state.current_thread_id = "thread-1"
    inbound.text = "/rename New Name"
    await router.handle_message(inbound)
    assert router._runtime.renamed == [("ou_1", "thread-1", "New Name")]
    assert "New Name" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_compact_command_starts_compaction(router, inbound):
    router._session_service.state.current_thread_id = "thread-1"
    inbound.text = "/compact"
    await router.handle_message(inbound)
    assert len(router._runtime.compacted) == 1
    assert router._connector.sent[-1] == router._render.compact_started_message()


@pytest.mark.asyncio
async def test_cwd_command_requires_path(router, inbound):
    inbound.text = "/cwd"
    await router.handle_message(inbound)
    assert router._connector.sent[-1] == router._render.cwd_usage_message()


@pytest.mark.asyncio
async def test_cwd_command_updates_default_only_when_no_current_thread(router, inbound):
    inbound.text = "/cwd /tmp/project-a"
    await router.handle_message(inbound)
    assert router._runtime.cwd_set == [("ou_1", "/tmp/project-a", False)]
    assert "后续新建线程的默认工作目录" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_cwd_command_updates_default_only_when_current_thread_exists(router, inbound):
    router._session_service.state.current_thread_id = "thread-1"
    inbound.text = "/cwd /tmp/project-a"
    await router.handle_message(inbound)
    assert router._runtime.cwd_set == [("ou_1", "/tmp/project-a", False)]
    assert "后续新建线程的默认工作目录" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_cwd_current_command_updates_current_thread_and_default(router, inbound):
    router._session_service.state.current_thread_id = "thread-1"
    inbound.text = "/cwd --current /tmp/project-a"
    await router.handle_message(inbound)
    assert router._runtime.cwd_set == [("ou_1", "/tmp/project-a", True)]
    assert "切换当前线程" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_cwd_current_command_handles_unavailable_current_thread(router, inbound):
    router._session_service.state.current_thread_id = "thread-1"
    inbound.text = "/cwd --current /thread-missing"
    await router.handle_message(inbound)
    assert "当前线程不可用" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_cwd_command_reports_invalid_path(router, inbound):
    inbound.text = "/cwd /invalid"
    await router.handle_message(inbound)
    assert "工作目录切换失败" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_cwd_current_command_requires_path_after_flag(router, inbound):
    inbound.text = "/cwd --current"
    await router.handle_message(inbound)
    assert router._connector.sent[-1] == router._render.cwd_usage_message()


@pytest.mark.asyncio
async def test_cwd_command_rejects_unknown_flag(router, inbound):
    inbound.text = "/cwd --foo /tmp/project-a"
    await router.handle_message(inbound)
    assert router._connector.sent[-1] == router._render.cwd_usage_message()


@pytest.mark.asyncio
async def test_cwd_default_command_is_blocked_while_runtime_busy(router, inbound):
    router._runtime.busy = True
    inbound.text = "/cwd /tmp/project-a"
    await router.handle_message(inbound)
    assert router._runtime.cwd_set == []
    assert router._connector.sent[-1] == router._render.busy_message()


@pytest.mark.asyncio
async def test_cwd_current_command_is_blocked_while_runtime_busy(router, inbound):
    router._runtime.busy = True
    inbound.text = "/cwd --current /tmp/project-a"
    await router.handle_message(inbound)
    assert router._runtime.cwd_set == []
    assert router._connector.sent[-1] == router._render.busy_message()


@pytest.mark.asyncio
async def test_cwd_command_is_blocked_during_pending_approval(router, inbound):
    router._approval_service.pending = FakeApprovalContext()
    inbound.text = "/cwd /tmp/project-a"
    await router.handle_message(inbound)
    assert router._runtime.cancel_called is False
    assert router._runtime.cwd_set == []
    assert router._connector.sent[-1] == router._render.busy_message()


@pytest.mark.asyncio
async def test_image_message_is_buffered_and_prompts_for_text(router, inbound):
    inbound.message_type = "image"
    inbound.text = ""
    inbound.image_key = "img-key-1"
    await router.handle_message(inbound)
    assert router._runtime.submitted == []
    assert "已收到第" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_buffered_images_are_sent_with_followup_text(router, inbound):
    inbound.message_type = "image"
    inbound.text = ""
    inbound.image_key = "img-key-1"
    await router.handle_message(inbound)

    inbound.message_type = "text"
    inbound.platform_message_id = "m2"
    inbound.text = "请描述这张图"
    inbound.image_key = None
    await router.handle_message(inbound)

    submitted_inputs = router._runtime.submitted[-1][2]
    assert len(submitted_inputs) == 2
    assert getattr(submitted_inputs[0], "path", None) == "/tmp/m1.png"
    assert getattr(submitted_inputs[1], "text", None) == "请描述这张图"


@pytest.mark.asyncio
async def test_fourth_buffered_image_is_rejected(router, inbound):
    inbound.message_type = "image"
    inbound.text = ""
    inbound.image_key = "img-key"
    for idx in range(1, 5):
        inbound.platform_message_id = f"m{idx}"
        await router.handle_message(inbound)
    assert "最多暂存" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_image_message_is_rejected_during_pending_approval(router, inbound):
    router._approval_service.pending = FakeApprovalContext()
    inbound.message_type = "image"
    inbound.text = ""
    inbound.image_key = "img-key-1"
    await router.handle_message(inbound)
    assert router._runtime.cancel_called is False
    assert "暂不支持发送图片" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_list_command_sends_thread_list_card(router, inbound):
    inbound.text = "/list hello"
    await router.handle_message(inbound)
    assert router._runtime.listed[-1] == ("ou_1", "hello", None, 5)
    page, current_thread_id, can_go_prev, can_go_next = router._connector.thread_list_created[-1]
    assert page.search_term == "hello"
    assert current_thread_id is None
    assert can_go_prev is False
    assert can_go_next is True


@pytest.mark.asyncio
async def test_thread_list_next_action_updates_same_card(router, inbound):
    inbound.text = "/list"
    await router.handle_message(inbound)

    await router.handle_thread_list_action(
        ThreadListActionEvent(
            user_open_id="ou_1",
            open_message_id="om-list-1",
            direction="next",
            raw_event={},
        )
    )

    assert router._runtime.listed[-1] == ("ou_1", None, "cursor-2", 5)
    message_id, page, _, can_go_prev, can_go_next = router._connector.thread_list_updated[-1]
    assert message_id == "om-list-1"
    assert page.current_cursor == "cursor-2"
    assert can_go_prev is True
    assert can_go_next is False


@pytest.mark.asyncio
async def test_thread_list_prev_action_returns_to_previous_page(router, inbound):
    inbound.text = "/list"
    await router.handle_message(inbound)
    await router.handle_thread_list_action(
        ThreadListActionEvent(
            user_open_id="ou_1",
            open_message_id="om-list-1",
            direction="next",
            raw_event={},
        )
    )

    await router.handle_thread_list_action(
        ThreadListActionEvent(
            user_open_id="ou_1",
            open_message_id="om-list-1",
            direction="prev",
            raw_event={},
        )
    )

    assert router._runtime.listed[-1] == ("ou_1", None, None, 5)
    message_id, page, _, can_go_prev, can_go_next = router._connector.thread_list_updated[-1]
    assert message_id == "om-list-1"
    assert page.current_cursor is None
    assert can_go_prev is False
    assert can_go_next is True


@pytest.mark.asyncio
async def test_thread_list_stale_action_sends_expired_message(router):
    await router.handle_thread_list_action(
        ThreadListActionEvent(
            user_open_id="ou_1",
            open_message_id="om-missing",
            direction="next",
            raw_event={},
        )
    )

    assert router._connector.sent[-1] == router._render.thread_list_state_expired_message()


@pytest.mark.asyncio
async def test_resume_command_accepts_thread_id(router, inbound):
    inbound.text = "/resume thread-2"
    await router.handle_message(inbound)
    assert router._runtime.resumed == ["thread-2"]
    assert "thread-2" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_resume_command_rejects_missing_thread_id(router, inbound):
    inbound.text = "/resume missing"
    await router.handle_message(inbound)
    assert router._runtime.resumed == []
    assert router._connector.sent[-1] == router._render.invalid_resume_message()


@pytest.mark.asyncio
async def test_status_message_includes_default_cwd(router, inbound):
    router._session_service.state.current_thread_id = "thread-1"
    inbound.text = "/status"
    await router.handle_message(inbound)
    assert "/tmp/default-cwd" in router._connector.sent[-1]


@pytest.mark.asyncio
async def test_text_turn_sends_generated_images_after_final_stream_update(router):
    router._session_service.state.current_thread_id = "thread-1"
    inbound = InboundMessage(
        platform_message_id="m2",
        user_open_id="ou_1",
        chat_id="oc_1",
        chat_type="p2p",
        message_type="text",
        text="draw something",
        raw_event={},
    )

    await router.handle_message(inbound)

    callbacks = router._runtime.submitted_callbacks[0]
    await callbacks.on_stream_update("final text", True)
    await callbacks.on_image_outputs(
        [
            GeneratedImageOutput(item_id="img-1", path="/tmp/output-1.png", source_type="image_generation"),
            GeneratedImageOutput(item_id="img-2", path="/tmp/output-2.png", source_type="image_view"),
        ]
    )

    assert router._connector.stream_updates[-1] == ("msg-stream", "final text", True, None)
    assert router._connector.sent_images == ["/tmp/output-1.png", "/tmp/output-2.png"]


@pytest.mark.asyncio
async def test_text_turn_continues_sending_later_images_after_one_failure(router):
    router._session_service.state.current_thread_id = "thread-1"
    inbound = InboundMessage(
        platform_message_id="m3",
        user_open_id="ou_1",
        chat_id="oc_1",
        chat_type="p2p",
        message_type="text",
        text="draw more",
        raw_event={},
    )

    async def flaky_send_local_image(target, local_path):
        if local_path == "/tmp/output-1.png":
            raise RuntimeError("upload failed")
        router._connector.sent_images.append(local_path)
        return "msg-image"

    router._connector.send_local_image = flaky_send_local_image

    await router.handle_message(inbound)

    callbacks = router._runtime.submitted_callbacks[0]
    await callbacks.on_image_outputs(
        [
            GeneratedImageOutput(item_id="img-1", path="/tmp/output-1.png", source_type="image_generation"),
            GeneratedImageOutput(item_id="img-2", path="/tmp/output-2.png", source_type="image_view"),
        ]
    )

    assert router._connector.sent_images == ["/tmp/output-2.png"]
    assert "图片输出发送失败" in router._connector.sent[-1]
