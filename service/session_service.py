from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import MessageCheckpoint, ThreadBinding, UserSession
from model.connector_models import ThreadRecord


class SessionService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], default_cwd: str | None = None) -> None:
        self._session_factory = session_factory
        self._default_cwd = default_cwd

    async def ensure_user(self, user_open_id: str) -> UserSession:
        async with self._session_factory() as session:
            model = await session.get(UserSession, user_open_id)
            if model is None:
                model = self._build_new_user_session(user_open_id)
                session.add(model)
                await session.commit()
                await session.refresh(model)
            return model

    async def get_session_state(self, user_open_id: str) -> UserSession:
        return await self.ensure_user(user_open_id)

    async def set_current_thread(self, user_open_id: str, thread_id: str | None) -> None:
        async with self._session_factory() as session:
            model = await session.get(UserSession, user_open_id)
            if model is None:
                model = self._build_new_user_session(user_open_id)
                session.add(model)
            model.current_thread_id = thread_id
            await session.commit()

    async def set_current_turn(self, user_open_id: str, turn_id: str | None) -> None:
        async with self._session_factory() as session:
            model = await session.get(UserSession, user_open_id)
            if model is None:
                model = self._build_new_user_session(user_open_id)
                session.add(model)
            model.current_turn_id = turn_id
            await session.commit()

    async def set_waiting_for_approval(self, user_open_id: str, waiting: bool) -> None:
        async with self._session_factory() as session:
            model = await session.get(UserSession, user_open_id)
            if model is None:
                model = self._build_new_user_session(user_open_id)
                session.add(model)
            model.waiting_for_approval = waiting
            await session.commit()

    async def set_default_model_config(
        self,
        user_open_id: str,
        model_name: str,
        reasoning_effort: str,
    ) -> None:
        async with self._session_factory() as session:
            model = await session.get(UserSession, user_open_id)
            if model is None:
                model = self._build_new_user_session(user_open_id)
                session.add(model)
            model.default_model = model_name
            model.default_reasoning_effort = reasoning_effort
            await session.commit()

    async def set_default_cwd(self, user_open_id: str, cwd: str) -> None:
        async with self._session_factory() as session:
            model = await session.get(UserSession, user_open_id)
            if model is None:
                model = self._build_new_user_session(user_open_id)
                session.add(model)
            model.default_cwd = cwd
            await session.commit()

    async def get_effective_cwd(self, user_open_id: str, fallback_cwd: str) -> str:
        model = await self.get_session_state(user_open_id)
        return model.default_cwd or fallback_cwd

    async def set_current_stream_message(
        self,
        user_open_id: str,
        stream_message_id: str | None,
        source_message_id: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            model = await session.get(UserSession, user_open_id)
            if model is None:
                model = self._build_new_user_session(user_open_id)
                session.add(model)
            model.current_stream_message_id = stream_message_id

            checkpoint = await session.scalar(
                select(MessageCheckpoint).where(MessageCheckpoint.user_open_id == user_open_id)
            )
            if checkpoint is None:
                checkpoint = MessageCheckpoint(user_open_id=user_open_id)
                session.add(checkpoint)
            checkpoint.stream_message_id = stream_message_id
            checkpoint.source_message_id = source_message_id
            await session.commit()

    def _build_new_user_session(self, user_open_id: str) -> UserSession:
        return UserSession(
            user_open_id=user_open_id,
            default_cwd=self._default_cwd,
        )

    async def upsert_thread_binding(self, user_open_id: str, thread_id: str, name: str | None = None) -> None:
        async with self._session_factory() as session:
            binding = await session.scalar(
                select(ThreadBinding).where(
                    ThreadBinding.user_open_id == user_open_id,
                    ThreadBinding.thread_id == thread_id,
                )
            )
            if binding is None:
                binding = ThreadBinding(user_open_id=user_open_id, thread_id=thread_id, name=name)
                session.add(binding)
            else:
                binding.name = name or binding.name
                binding.updated_at = datetime.now(UTC)
            await session.commit()

    async def list_threads(self, user_open_id: str) -> list[ThreadRecord]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ThreadBinding)
                .where(ThreadBinding.user_open_id == user_open_id, ThreadBinding.archived.is_(False))
                .order_by(desc(ThreadBinding.updated_at))
            )
            return [
                ThreadRecord(
                    thread_id=row.thread_id,
                    name=row.name,
                    cwd="",
                    preview="",
                    updated_at=row.updated_at.isoformat() if row.updated_at else "",
                )
                for row in rows.all()
            ]

    async def find_user_open_id_by_thread_id(self, thread_id: str) -> str | None:
        async with self._session_factory() as session:
            binding = await session.scalar(
                select(ThreadBinding)
                .where(ThreadBinding.thread_id == thread_id)
                .order_by(desc(ThreadBinding.updated_at))
            )
            if binding is None:
                return None
            return binding.user_open_id

    async def rename_thread(self, user_open_id: str, thread_id: str, name: str | None) -> None:
        async with self._session_factory() as session:
            binding = await session.scalar(
                select(ThreadBinding).where(
                    ThreadBinding.user_open_id == user_open_id,
                    ThreadBinding.thread_id == thread_id,
                )
            )
            if binding is not None:
                binding.name = name
                binding.updated_at = datetime.now(UTC)
                await session.commit()
