from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserSession(Base):
    __tablename__ = "user_session"

    user_open_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    current_thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_turn_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_stream_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    waiting_for_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_reasoning_effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ThreadBinding(Base):
    __tablename__ = "thread_binding"
    __table_args__ = (
        UniqueConstraint("user_open_id", "thread_id", name="uq_thread_binding_user_thread"),
        Index("ix_thread_binding_user_updated", "user_open_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_open_id: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PendingApprovalRecord(Base):
    __tablename__ = "pending_approval"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_pending_approval_request_id"),
        Index("ix_pending_approval_user_created", "user_open_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_open_id: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_method: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageCheckpoint(Base):
    __tablename__ = "message_checkpoint"
    __table_args__ = (UniqueConstraint("user_open_id", name="uq_message_checkpoint_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_open_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stream_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
