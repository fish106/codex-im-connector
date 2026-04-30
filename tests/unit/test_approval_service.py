from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base, PendingApprovalRecord
from service.approval_service import ApprovalService


class DummySessionService:
    def __init__(self) -> None:
        self.waiting_states = []

    async def set_waiting_for_approval(self, user_open_id: str, waiting: bool) -> None:
        self.waiting_states.append((user_open_id, waiting))


@pytest.mark.asyncio
async def test_mark_resolved_by_user_clears_pending_and_marks_resolved(tmp_path):
    db_path = tmp_path / "approval.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_service = DummySessionService()
    approval_service = ApprovalService(session_factory, session_service)

    context = await approval_service.create_pending(
        user_open_id="ou_1",
        thread_id="thread-1",
        turn_id="turn-1",
        request_id="req-1",
        request_method="item/commandExecution/requestApproval",
        reason="need approval",
        command="touch test.txt",
        available_decisions=["accept", "cancel"],
    )

    assert await approval_service.get_pending("ou_1") is context

    await approval_service.mark_resolved_by_user("ou_1")

    assert context.resolved_event.is_set() is True
    assert await approval_service.get_pending("ou_1") is None
    assert session_service.waiting_states[-1] == ("ou_1", False)

    async with session_factory() as session:
        record = await session.get(PendingApprovalRecord, 1)
        assert record is not None
        assert record.status == "resolved"
        assert record.resolved_at is not None

    await engine.dispose()
