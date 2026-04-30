from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from model.connector_models import (
    ApprovalActionEvent,
    ApprovalPrompt,
    InboundMessage,
    MessageTarget,
    ThreadListActionEvent,
    ThreadListPage,
)


class IMConnectorBase(ABC):
    @abstractmethod
    def start(
        self,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        on_approval_action: Callable[[ApprovalActionEvent], Awaitable[None]],
        on_thread_list_action: Callable[[ThreadListActionEvent], Awaitable[None]],
    ) -> None:
        """Start the platform connection and forward inbound messages via callback."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the platform connection and release any background resources."""

    @abstractmethod
    async def ack_message(self, inbound: InboundMessage) -> None:
        """Do a lightweight immediate acknowledgement on the source platform."""

    @abstractmethod
    async def send_rich_text(self, target: MessageTarget, markdown_text: str) -> str:
        """Send a complete rich-text message and return the platform message id."""

    @abstractmethod
    async def create_stream_message(self, target: MessageTarget, markdown_text: str) -> str:
        """Create the placeholder message that later stream updates will edit."""

    @abstractmethod
    async def download_message_image(self, inbound: InboundMessage) -> str:
        """Download an inbound image message to a local temporary file and return its path."""

    @abstractmethod
    async def send_local_image(self, target: MessageTarget, local_path: str) -> str:
        """Upload a local image file and send it to the target chat, returning the platform message id."""

    @abstractmethod
    async def send_thread_list_card(
        self,
        target: MessageTarget,
        page: ThreadListPage,
        current_thread_id: str | None,
        can_go_prev: bool,
        can_go_next: bool,
    ) -> str:
        """Send a thread list card and return the platform message id."""

    @abstractmethod
    async def update_thread_list_card(
        self,
        message_id: str,
        page: ThreadListPage,
        current_thread_id: str | None,
        can_go_prev: bool,
        can_go_next: bool,
    ) -> None:
        """Update an existing thread list card in place."""

    @abstractmethod
    async def update_stream_message(
        self,
        message_id: str,
        markdown_text: str,
        final: bool = False,
        approval_prompt: ApprovalPrompt | None = None,
    ) -> None:
        """Update the existing placeholder message with the latest streamed content."""
