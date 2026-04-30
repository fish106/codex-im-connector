from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict


class MessageTarget(BaseModel):
    user_open_id: str


class InboundMessage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    platform_message_id: str
    user_open_id: str
    chat_id: str
    chat_type: str
    message_type: str = "text"
    text: str
    image_key: str | None = None
    raw_event: Any

    @property
    def is_private(self) -> bool:
        return self.chat_type == "p2p"

    @property
    def is_text_message(self) -> bool:
        return self.message_type == "text"

    @property
    def is_image_message(self) -> bool:
        return self.message_type == "image"

    @property
    def target(self) -> MessageTarget:
        return MessageTarget(user_open_id=self.user_open_id)


class ThreadRecord(BaseModel):
    thread_id: str
    name: str | None
    cwd: str
    preview: str
    updated_at: str


class ThreadListPage(BaseModel):
    items: list[ThreadRecord]
    next_cursor: str | None = None
    current_cursor: str | None = None
    search_term: str | None = None


class CurrentThreadInfo(BaseModel):
    thread_id: str
    name: str | None
    source: str
    model_provider: str
    cwd: str
    preview: str
    created_at: str
    updated_at: str


class AvailableModelInfo(BaseModel):
    model_id: str
    display_name: str
    is_default: bool
    default_reasoning_effort: str
    supported_reasoning_efforts: list[str]


class ApprovalPrompt(BaseModel):
    request_id: str
    thread_id: str
    turn_id: str
    request_method: str
    reason: str
    command: str
    available_decisions: list[str]


class GeneratedImageOutput(BaseModel):
    item_id: str
    path: str
    source_type: str


class ApprovalActionEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_open_id: str
    open_message_id: str | None = None
    request_id: str
    decision: str
    raw_event: Any


class ThreadListActionEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_open_id: str
    open_message_id: str | None = None
    direction: str
    raw_event: Any


class StreamCallbacks(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    on_stream_update: Callable[[str, bool], Awaitable[None]]
    on_approval_request: Callable[[ApprovalPrompt, str], Awaitable[None]]
    on_error: Callable[[str], Awaitable[None]]
    on_image_outputs: Callable[[list[GeneratedImageOutput]], Awaitable[None]] | None = None
