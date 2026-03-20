"""SQLAlchemy ORM models for chat sessions (industry-standard chat persistence)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserORM(Base):
    """End user account (passcode-based)."""

    __tablename__ = "chat_users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
    )

    sessions: Mapped[list["ChatSessionORM"]] = relationship(
        "ChatSessionORM",
        back_populates="owner",
    )


class ChatSessionORM(Base):
    """A logical chat thread (e.g. browser tab / user conversation)."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("chat_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )

    owner: Mapped[Optional["UserORM"]] = relationship(
        "UserORM",
        back_populates="sessions",
    )
    messages: Mapped[list["ChatMessageORM"]] = relationship(
        "ChatMessageORM",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageORM.created_at",
    )


class ChatMessageORM(Base):
    """Single turn in a chat (user or assistant)."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    ticker_filter: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    answer_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    sources_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    fallback_mode: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
    )

    session: Mapped[ChatSessionORM] = relationship(back_populates="messages")
