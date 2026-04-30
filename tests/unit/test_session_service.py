from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base
from service.session_service import SessionService


@pytest.mark.asyncio
async def test_find_user_open_id_by_thread_id_returns_latest_binding(tmp_path):
    db_path = tmp_path / "session.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = SessionService(session_factory)
    await service.upsert_thread_binding("ou_1", "thread-1", "Alpha")

    user_open_id = await service.find_user_open_id_by_thread_id("thread-1")

    assert user_open_id == "ou_1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_user_initializes_default_cwd_for_new_user(tmp_path):
    db_path = tmp_path / "session.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = SessionService(session_factory, default_cwd="/tmp/default-workspace")

    state = await service.get_session_state("ou_1")

    assert state.default_cwd == "/tmp/default-workspace"
    await engine.dispose()

