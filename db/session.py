from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from db.models import Base


def create_engine_and_session_factory(sqlite_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(sqlite_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        result = await conn.execute(text("PRAGMA table_info(user_session)"))
        columns = {row[1] for row in result.fetchall()}
        missing_column_ddl = {
            "default_model": "ALTER TABLE user_session ADD COLUMN default_model VARCHAR(128)",
            "default_reasoning_effort": "ALTER TABLE user_session ADD COLUMN default_reasoning_effort VARCHAR(32)",
            "default_cwd": "ALTER TABLE user_session ADD COLUMN default_cwd VARCHAR(1024)",
        }
        for column_name, ddl in missing_column_ddl.items():
            if column_name not in columns:
                await conn.execute(text(ddl))
