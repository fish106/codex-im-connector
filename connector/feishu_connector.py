from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from uuid import uuid4
from typing import Awaitable, Callable

import lark_oapi as lark
import lark_oapi.ws.client as lark_ws_client
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    CallBackToast,
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from connector.base import IMConnectorBase
from core.config import Settings
from model.connector_models import (
    ApprovalActionEvent,
    ApprovalPrompt,
    InboundMessage,
    MessageTarget,
    ThreadListActionEvent,
    ThreadListPage,
)

logger = logging.getLogger(__name__)
APPROVAL_ACTION_TYPE = "approval_decision"
THREAD_LIST_ACTION_TYPE = "thread_list_page"


class FeishuConnector(IMConnectorBase):
    def __init__(self, settings: Settings, app_loop: asyncio.AbstractEventLoop) -> None:
        self._settings = settings
        self._app_loop = app_loop
        self._on_message: Callable[[InboundMessage], Awaitable[None]] | None = None
        self._on_approval_action: Callable[[ApprovalActionEvent], Awaitable[None]] | None = None
        self._on_thread_list_action: Callable[[ThreadListActionEvent], Awaitable[None]] | None = None
        self._ws_client: lark.ws.Client | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_started = threading.Event()
        self._ws_error: BaseException | None = None
        self._stopping = False
        self._client = (
            lark.Client.builder()
            .app_id(settings.FEISHU_APP_ID)
            .app_secret(settings.FEISHU_APP_SECRET)
            .log_level(getattr(lark.LogLevel, settings.LOG_LEVEL.upper(), lark.LogLevel.INFO))
            .build()
        )

    def start(
        self,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        on_approval_action: Callable[[ApprovalActionEvent], Awaitable[None]],
        on_thread_list_action: Callable[[ThreadListActionEvent], Awaitable[None]],
    ) -> None:
        self._on_message = on_message
        self._on_approval_action = on_approval_action
        self._on_thread_list_action = on_thread_list_action
        self._stopping = False
        self._ws_started.clear()
        self._ws_error = None
        self._ws_thread = threading.Thread(target=self._run_ws_client, name="feishu-ws-client", daemon=True)
        self._ws_thread.start()
        self._ws_started.wait()
        if self._ws_error is not None:
            raise RuntimeError("failed to start Feishu websocket client") from self._ws_error

    def stop(self) -> None:
        self._stopping = True
        ws_client = self._ws_client
        ws_loop = self._ws_loop
        ws_thread = self._ws_thread
        if ws_client is not None:
            ws_client._auto_reconnect = False  # noqa: SLF001

        if ws_client is not None and ws_loop is not None and ws_loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(ws_client._disconnect(), ws_loop)  # noqa: SLF001
                future.result(timeout=5)
            except Exception:
                logger.debug("feishu ws disconnect did not finish cleanly", exc_info=True)
            try:
                ws_loop.call_soon_threadsafe(ws_loop.stop)
            except RuntimeError:
                pass
        if ws_thread is not None and ws_thread.is_alive():
            ws_thread.join(timeout=5)
        self._ws_client = None
        self._ws_loop = None
        self._ws_thread = None
        self._ws_started.clear()

    async def ack_message(self, inbound: InboundMessage) -> None:
        request = (
            lark.im.v1.CreateMessageReactionRequest.builder()
            .message_id(inbound.platform_message_id)
            .request_body(
                lark.im.v1.CreateMessageReactionRequestBody.builder()
                .reaction_type(
                    lark.im.v1.Emoji.builder().emoji_type(self._settings.FEISHU_REACTION_EMOJI).build()
                )
                .build()
            )
            .build()
        )
        try:
            await asyncio.to_thread(self._execute, self._client.im.v1.message_reaction.create, request, "ack_message")
        except Exception:
            logger.warning("failed to ack inbound message", exc_info=True)

    async def send_rich_text(self, target: MessageTarget, markdown_text: str) -> str:
        request = self._build_create_message_request(target, markdown_text)
        response = await asyncio.to_thread(self._execute, self._client.im.v1.message.create, request, "send_rich_text")
        return response.data.message_id

    async def create_stream_message(self, target: MessageTarget, markdown_text: str) -> str:
        request = self._build_create_message_request(target, markdown_text)
        response = await asyncio.to_thread(
            self._execute,
            self._client.im.v1.message.create,
            request,
            "create_stream_message",
        )
        return response.data.message_id

    async def download_message_image(self, inbound: InboundMessage) -> str:
        if not inbound.image_key:
            raise ValueError("image_key is missing")
        target_dir = self._settings.app_root_path / "tmp" / "feishu-images"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{inbound.platform_message_id}-{uuid4().hex}.bin"

        request = (
            lark.im.v1.GetMessageResourceRequest.builder()
            .message_id(inbound.platform_message_id)
            .file_key(inbound.image_key)
            .type("image")
            .build()
        )
        response = await asyncio.to_thread(
            self._execute,
            self._client.im.v1.message_resource.get,
            request,
            "download_message_image",
        )
        file_bytes = response.file.read()
        target_path.write_bytes(file_bytes)
        return str(target_path)

    async def send_local_image(self, target: MessageTarget, local_path: str) -> str:
        image_path = Path(local_path).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"image file does not exist: {image_path}")
        image_key = await asyncio.to_thread(self._upload_local_image, image_path)
        request = self._build_create_image_message_request(target, image_key)
        response = await asyncio.to_thread(self._execute, self._client.im.v1.message.create, request, "send_local_image")
        return response.data.message_id

    async def send_thread_list_card(
        self,
        target: MessageTarget,
        page: ThreadListPage,
        current_thread_id: str | None,
        can_go_prev: bool,
        can_go_next: bool,
    ) -> str:
        request = self._build_thread_list_create_message_request(
            target,
            page=page,
            current_thread_id=current_thread_id,
            can_go_prev=can_go_prev,
            can_go_next=can_go_next,
        )
        response = await asyncio.to_thread(
            self._execute,
            self._client.im.v1.message.create,
            request,
            "send_thread_list_card",
        )
        return response.data.message_id

    async def update_thread_list_card(
        self,
        message_id: str,
        page: ThreadListPage,
        current_thread_id: str | None,
        can_go_prev: bool,
        can_go_next: bool,
    ) -> None:
        request = (
            lark.im.v1.PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                lark.im.v1.PatchMessageRequestBody.builder()
                .content(
                    self._build_thread_list_card_content(
                        page=page,
                        current_thread_id=current_thread_id,
                        can_go_prev=can_go_prev,
                        can_go_next=can_go_next,
                    )
                )
                .build()
            )
            .build()
        )
        await asyncio.to_thread(self._execute, self._client.im.v1.message.patch, request, "update_thread_list_card")

    async def update_stream_message(
        self,
        message_id: str,
        markdown_text: str,
        final: bool = False,
        approval_prompt: ApprovalPrompt | None = None,
    ) -> None:
        request = (
            lark.im.v1.PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                lark.im.v1.PatchMessageRequestBody.builder()
                .content(self._build_card_content(markdown_text, final=final, approval_prompt=approval_prompt))
                .build()
            )
            .build()
        )
        await asyncio.to_thread(self._execute, self._client.im.v1.message.patch, request, "update_stream_message")

    def _handle_message_receive(self, event: lark.im.v1.P2ImMessageReceiveV1) -> None:
        if self._on_message is None:
            return
        inbound = self._parse_inbound(event)
        if inbound is None:
            return
        asyncio.run_coroutine_threadsafe(self._on_message(inbound), self._app_loop)

    @staticmethod
    def _ignore_event(_event: object) -> None:
        return None

    def _handle_card_action(self, event: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        if event.event is None or event.event.action is None:
            return self._build_callback_toast("未处理按钮事件", toast_type="warning")
        value = event.event.action.value or {}
        user_open_id = event.event.operator.open_id if event.event.operator else None
        logger.info(
            "received card action callback user_open_id=%s open_message_id=%s tag=%s name=%s value=%s",
            user_open_id,
            event.event.context.open_message_id if event.event.context else None,
            event.event.action.tag,
            event.event.action.name,
            value,
        )
        if not user_open_id:
            return self._build_callback_toast("按钮参数不完整", toast_type="warning")

        action_type = value.get("type")
        if action_type == APPROVAL_ACTION_TYPE:
            if self._on_approval_action is None:
                return self._build_callback_toast("未处理按钮事件", toast_type="warning")
            request_id = value.get("request_id")
            decision = value.get("action")
            if not isinstance(request_id, str) or not isinstance(decision, str):
                return self._build_callback_toast("按钮参数不完整", toast_type="warning")
            action_event = ApprovalActionEvent(
                user_open_id=user_open_id,
                open_message_id=event.event.context.open_message_id if event.event.context else None,
                request_id=request_id,
                decision=decision,
                raw_event=event,
            )
            asyncio.run_coroutine_threadsafe(self._on_approval_action(action_event), self._app_loop)
            return self._build_callback_toast(f"已选择 {decision}")

        if action_type == THREAD_LIST_ACTION_TYPE:
            if self._on_thread_list_action is None:
                return self._build_callback_toast("未处理按钮事件", toast_type="warning")
            direction = value.get("direction")
            if not isinstance(direction, str):
                return self._build_callback_toast("按钮参数不完整", toast_type="warning")
            action_event = ThreadListActionEvent(
                user_open_id=user_open_id,
                open_message_id=event.event.context.open_message_id if event.event.context else None,
                direction=direction,
                raw_event=event,
            )
            asyncio.run_coroutine_threadsafe(self._on_thread_list_action(action_event), self._app_loop)
            return self._build_callback_toast("正在更新列表")

        return self._build_callback_toast("未识别的按钮动作", toast_type="warning")

    def _run_ws_client(self) -> None:
        ws_loop = asyncio.new_event_loop()
        self._ws_loop = ws_loop
        lark_ws_client.loop = ws_loop
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_receive)
            .register_p2_card_action_trigger(self._handle_card_action)
            .register_p2_im_message_reaction_created_v1(self._ignore_event)
            .register_p2_im_message_reaction_deleted_v1(self._ignore_event)
            .register_p2_im_message_message_read_v1(self._ignore_event)
            .register_p2_im_message_recalled_v1(self._ignore_event)
            .build()
        )
        self._ws_client = lark.ws.Client(
            self._settings.FEISHU_APP_ID,
            self._settings.FEISHU_APP_SECRET,
            log_level=getattr(lark.LogLevel, self._settings.LOG_LEVEL.upper(), lark.LogLevel.INFO),
            event_handler=event_handler,
        )
        self._ws_started.set()
        try:
            self._ws_client.start()
        except RuntimeError as exc:
            if not self._stopping or "Event loop stopped before Future completed" not in str(exc):
                self._ws_error = exc
                logger.error("feishu websocket client exited unexpectedly", exc_info=True)
        except BaseException as exc:
            self._ws_error = exc
            logger.error("feishu websocket client exited unexpectedly", exc_info=True)
        finally:
            pending = asyncio.all_tasks(ws_loop)
            for task in pending:
                task.cancel()
            if pending:
                try:
                    ws_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            ws_loop.close()

    def _parse_inbound(self, event: lark.im.v1.P2ImMessageReceiveV1) -> InboundMessage | None:
        message = event.event.message if event.event else None
        sender = event.event.sender if event.event else None
        if message is None or sender is None or sender.sender_id is None:
            return None
        if message.chat_type != "p2p" or message.message_type not in {"text", "image"}:
            return None
        user_open_id = sender.sender_id.open_id
        if not user_open_id:
            return None
        text = ""
        image_key = None
        if message.content:
            try:
                content = json.loads(message.content)
                text = content.get("text", "")
                image_key = content.get("image_key")
            except json.JSONDecodeError:
                text = message.content
        return InboundMessage(
            platform_message_id=message.message_id or "",
            user_open_id=user_open_id,
            chat_id=message.chat_id or "",
            chat_type=message.chat_type or "",
            message_type=message.message_type or "text",
            text=text.strip(),
            image_key=image_key,
            raw_event=event,
        )

    def _build_create_message_request(self, target: MessageTarget, markdown_text: str) -> lark.im.v1.CreateMessageRequest:
        body = (
            lark.im.v1.CreateMessageRequestBody.builder()
            .receive_id(target.user_open_id)
            .msg_type("interactive")
            .content(self._build_card_content(markdown_text))
            .build()
        )
        return lark.im.v1.CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()

    def _build_thread_list_create_message_request(
        self,
        target: MessageTarget,
        *,
        page: ThreadListPage,
        current_thread_id: str | None,
        can_go_prev: bool,
        can_go_next: bool,
    ) -> lark.im.v1.CreateMessageRequest:
        body = (
            lark.im.v1.CreateMessageRequestBody.builder()
            .receive_id(target.user_open_id)
            .msg_type("interactive")
            .content(
                self._build_thread_list_card_content(
                    page=page,
                    current_thread_id=current_thread_id,
                    can_go_prev=can_go_prev,
                    can_go_next=can_go_next,
                )
            )
            .build()
        )
        return lark.im.v1.CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()

    @staticmethod
    def _build_create_image_message_request(target: MessageTarget, image_key: str) -> lark.im.v1.CreateMessageRequest:
        body = (
            lark.im.v1.CreateMessageRequestBody.builder()
            .receive_id(target.user_open_id)
            .msg_type("image")
            .content(json.dumps({"image_key": image_key}, ensure_ascii=False))
            .build()
        )
        return lark.im.v1.CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()

    def _upload_local_image(self, image_path: Path) -> str:
        with image_path.open("rb") as image_file:
            request = (
                lark.im.v1.CreateImageRequest.builder()
                .request_body(
                    lark.im.v1.CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(image_file)
                    .build()
                )
                .build()
            )
            response = self._execute(self._client.im.v1.image.create, request, "upload_local_image")
        return response.data.image_key

    @staticmethod
    def _build_card_content(
        markdown_text: str,
        final: bool = False,
        approval_prompt: ApprovalPrompt | None = None,
    ) -> str:
        content = markdown_text or " "
        elements: list[dict] = [
            {
                "tag": "markdown",
                "content": content,
            }
        ]
        if approval_prompt is not None and approval_prompt.available_decisions:
            for idx, decision in enumerate(approval_prompt.available_decisions):
                elements.append(
                    {
                        "tag": "button",
                        "text": {
                            "tag": "plain_text",
                            "content": decision,
                        },
                        "type": "primary" if idx == 0 else "default",
                        "behaviors": [
                            {
                                "type": "callback",
                                "value": {
                                    "type": APPROVAL_ACTION_TYPE,
                                    "action": decision,
                                    "request_id": approval_prompt.request_id,
                                },
                            }
                        ],
                    }
                )
        card = {
            "schema": "2.0",
            "config": {
                # "streaming_mode": True,
                "wide_screen_mode": True,
            },
            "body": {
                "elements": elements,
            },
        }
        return json.dumps(card, ensure_ascii=False)

    @staticmethod
    def _build_thread_list_card_content(
        *,
        page: ThreadListPage,
        current_thread_id: str | None,
        can_go_prev: bool,
        can_go_next: bool,
    ) -> str:
        title_lines = ["### 线程列表"]
        if page.search_term:
            title_lines.append(f"搜索词：`{page.search_term}`")
        if not page.items:
            title_lines.append("当前页没有线程。")

        rows = []
        for item in page.items:
            name = item.name or "未命名线程"
            if item.thread_id == current_thread_id:
                name = f"{name} (当前)"
            rows.append(
                {
                    "thread_id": item.thread_id,
                    "name": name,
                    "cwd": item.cwd,
                    "updated_at": item.updated_at,
                }
            )

        elements: list[dict] = [
            {
                "tag": "markdown",
                "content": "\n".join(title_lines),
            },
            {
                "tag": "table",
                "page_size": 5,
                "row_height": "auto",
                "header_style": {
                    "text_align": "left",
                    "text_size": "normal",
                    "background_style": "grey",
                    "text_color": "default",
                    "bold": True,
                    "lines": 1,
                },
                "columns": [
                    {"name": "thread_id", "display_name": "ID", "data_type": "text", "width": "auto"},
                    {"name": "name", "display_name": "名称", "data_type": "text", "width": "160px"},
                    {"name": "cwd", "display_name": "当前工作目录", "data_type": "text", "width": "220px"},
                    {"name": "updated_at", "display_name": "最后更新时间", "data_type": "text", "width": "160px"},
                ],
                "rows": rows,
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "上一页"},
                "type": "primary_filled",
                "disabled": not can_go_prev,
                "disabled_tips": {"tag": "plain_text", "content": "已经是第一页"},
                "behaviors": [
                    {
                        "type": "callback",
                        "value": {
                            "type": THREAD_LIST_ACTION_TYPE,
                            "direction": "prev",
                        },
                    }
                ],
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "下一页"},
                "type": "primary_filled",
                "disabled": not can_go_next,
                "disabled_tips": {"tag": "plain_text", "content": "已经是最后一页"},
                "behaviors": [
                    {
                        "type": "callback",
                        "value": {
                            "type": THREAD_LIST_ACTION_TYPE,
                            "direction": "next",
                        },
                    }
                ],
            },
        ]

        card = {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
            },
            "body": {
                "elements": elements,
            },
        }
        return json.dumps(card, ensure_ascii=False)

    @staticmethod
    def _build_callback_toast(content: str, toast_type: str = "info") -> P2CardActionTriggerResponse:
        response = P2CardActionTriggerResponse()
        response.toast = CallBackToast({"type": toast_type, "content": content})
        return response

    @staticmethod
    def _execute(fn, request, op_name: str):
        response = fn(request)
        if not response.success():
            log_id = response.get_log_id()
            raise RuntimeError(f"{op_name} failed: code={response.code}, msg={response.msg}, log_id={log_id}")
        return response
