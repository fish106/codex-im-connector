from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import PendingApprovalRecord
from model.connector_models import ApprovalPrompt
from service.session_service import SessionService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingApprovalContext:
    user_open_id: str
    thread_id: str
    turn_id: str
    request_id: str
    request_method: str
    reason: str
    command: str
    available_decisions: list[str]
    summary: str
    decision_event: threading.Event = field(default_factory=threading.Event)
    resolved_event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: str | None = None

    def to_prompt(self) -> ApprovalPrompt:
        return ApprovalPrompt(
            request_id=self.request_id,
            thread_id=self.thread_id,
            turn_id=self.turn_id,
            request_method=self.request_method,
            reason=self.reason,
            command=self.command,
            available_decisions=self.available_decisions,
        )


class ApprovalService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        session_service: SessionService,
    ) -> None:
        self._session_factory = session_factory
        self._session_service = session_service
        self._lock = asyncio.Lock()
        self._pending_by_user: dict[str, PendingApprovalContext] = {}

    async def create_pending(
        self,
        user_open_id: str,
        thread_id: str,
        turn_id: str,
        request_id: str,
        request_method: str,
        reason: str,
        command: str,
        available_decisions: list[str],
    ) -> PendingApprovalContext:
        summary = self.build_summary(request_method, reason, command)
        async with self._lock:
            context = PendingApprovalContext(
                user_open_id=user_open_id,
                thread_id=thread_id,
                turn_id=turn_id,
                request_id=request_id,
                request_method=request_method,
                reason=reason,
                command=command,
                available_decisions=available_decisions,
                summary=summary,
            )
            self._pending_by_user[user_open_id] = context
        logger.info(
            "approval requested user_open_id=%s thread_id=%s turn_id=%s request_id=%s request_method=%s reason=%s command=%s available_decisions=%s",
            user_open_id,
            thread_id,
            turn_id,
            request_id,
            request_method,
            reason,
            command,
            available_decisions,
        )
        await self._session_service.set_waiting_for_approval(user_open_id, True)
        async with self._session_factory() as session:
            record = PendingApprovalRecord(
                user_open_id=user_open_id,
                thread_id=thread_id,
                turn_id=turn_id,
                request_id=request_id,
                request_method=request_method,
                summary=summary,
                status="pending",
            )
            session.add(record)
            await session.commit()
        return context

    async def get_pending(self, user_open_id: str) -> PendingApprovalContext | None:
        async with self._lock:
            return self._pending_by_user.get(user_open_id)

    async def approve(self, user_open_id: str) -> PendingApprovalContext | None:
        return await self._resolve_decision(user_open_id, "accept")

    async def cancel(self, user_open_id: str) -> PendingApprovalContext | None:
        return await self._resolve_decision(user_open_id, "cancel")

    async def resolve(self, user_open_id: str, decision: str) -> PendingApprovalContext | None:
        return await self._resolve_decision(user_open_id, decision)

    async def mark_resolved_by_user(self, user_open_id: str) -> None:
        async with self._lock:
            context = self._pending_by_user.get(user_open_id)
            if context is None:
                return
            context.resolved_event.set()
            self._pending_by_user.pop(user_open_id, None)
        logger.info(
            "approval pending cleared user_open_id=%s thread_id=%s turn_id=%s request_id=%s",
            user_open_id,
            context.thread_id,
            context.turn_id,
            context.request_id,
        )
        await self._session_service.set_waiting_for_approval(user_open_id, False)
        async with self._session_factory() as session:
            record = await session.scalar(
                select(PendingApprovalRecord).where(PendingApprovalRecord.request_id == context.request_id)
            )
            if record is not None:
                record.status = "resolved"
                record.resolved_at = datetime.now(UTC)
                await session.commit()

    async def _resolve_decision(self, user_open_id: str, decision: str) -> PendingApprovalContext | None:
        async with self._lock:
            context = self._pending_by_user.get(user_open_id)
            if context is None:
                return None
            context.decision = decision
            context.decision_event.set()
        async with self._session_factory() as session:
            record = await session.scalar(
                select(PendingApprovalRecord).where(PendingApprovalRecord.request_id == context.request_id)
            )
            if record is not None:
                record.status = "approved" if decision == "accept" else "cancelled"
                await session.commit()
        return context

    @staticmethod
    def build_summary(request_method: str, reason: str, command: str) -> str:
        return (
            f"- 类型：`{request_method}`\n"
            f"- 原因：{reason or '无'}\n"
            f"- 命令：`{command or '无'}`"
        )
