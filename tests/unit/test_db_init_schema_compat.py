from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from db.session import init_db


@pytest.mark.asyncio
async def test_init_db_adds_missing_user_session_columns(tmp_path):
    db_path = tmp_path / "compat.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE user_session (
                    user_open_id VARCHAR(128) PRIMARY KEY,
                    current_thread_id VARCHAR(128),
                    current_turn_id VARCHAR(128),
                    current_stream_message_id VARCHAR(128),
                    waiting_for_approval BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )

    await init_db(engine)

    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(user_session)"))
        columns = {row[1] for row in result.fetchall()}

    await engine.dispose()

    assert "default_model" in columns
    assert "default_reasoning_effort" in columns
    assert "default_cwd" in columns
