"""CRUD for chat sessions and messages."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth_tokens import hash_passcode
from app.orm_models import ChatMessageORM, ChatSessionORM, UserORM
from app.schemas import ArticleRef, ChatMessageRead


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_user(
    db: Session,
    *,
    username: str,
    passcode: str,
    is_admin: bool = False,
) -> UserORM:
    row = UserORM(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_passcode(passcode),
        is_admin=is_admin,
        created_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_user_by_username(db: Session, username: str) -> Optional[UserORM]:
    stmt = select(UserORM).where(UserORM.username == username)
    return db.scalars(stmt).first()


def get_user_by_id(db: Session, user_id: str) -> Optional[UserORM]:
    return db.get(UserORM, user_id)


def seed_default_admin(db: Session, *, passcode: str) -> None:
    """Create ``admin`` once if missing (bootstrap)."""
    if get_user_by_username(db, "admin") is not None:
        return
    create_user(db, username="admin", passcode=passcode, is_admin=True)


def create_session(db: Session, user_id: str, title: Optional[str] = None) -> ChatSessionORM:
    sid = str(uuid.uuid4())
    row = ChatSessionORM(
        id=sid,
        user_id=user_id,
        title=title,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(db: Session, session_id: str) -> Optional[ChatSessionORM]:
    return db.get(ChatSessionORM, session_id)


def get_session_for_access(db: Session, session_id: str, user: UserORM) -> Optional[ChatSessionORM]:
    sess = db.get(ChatSessionORM, session_id)
    if sess is None:
        return None
    if user.is_admin:
        return sess
    if sess.user_id == user.id:
        return sess
    return None


def list_chat_sessions(db: Session, user: UserORM, limit: int = 100) -> list[ChatSessionORM]:
    stmt = (
        select(ChatSessionORM)
        .options(joinedload(ChatSessionORM.owner))
        .order_by(ChatSessionORM.updated_at.desc())
        .limit(limit)
    )
    if not user.is_admin:
        stmt = stmt.where(ChatSessionORM.user_id == user.id)
    return list(db.scalars(stmt).unique().all())


def touch_session(db: Session, session_id: str) -> None:
    row = get_session(db, session_id)
    if row:
        row.updated_at = _utcnow()
        db.commit()


def _derive_session_title(content: str, max_len: int = 72) -> str:
    """First line of the user's message, truncated for sidebar display."""
    line = (content or "").strip().split("\n", 1)[0].strip()
    if len(line) > max_len:
        return line[: max_len - 1].rstrip() + "…"
    return line or "Chat"


def append_message(
    db: Session,
    *,
    session_id: str,
    role: str,
    content: str,
    ticker_filter: Optional[str] = None,
    answer_source: Optional[str] = None,
    sources: Optional[list[ArticleRef]] = None,
    fallback_mode: Optional[bool] = None,
    summarization_attribution: Optional[str] = None,
) -> ChatMessageORM:
    payload: Optional[dict[str, Any]] = None
    if sources or summarization_attribution:
        payload = {}
        if sources:
            payload["sources"] = [s.model_dump() for s in sources]
        if summarization_attribution:
            payload["attribution"] = summarization_attribution
    msg = ChatMessageORM(
        session_id=session_id,
        role=role,
        content=content,
        ticker_filter=ticker_filter,
        answer_source=answer_source,
        sources_json=payload,
        fallback_mode=fallback_mode,
        created_at=_utcnow(),
    )
    db.add(msg)
    sess = get_session(db, session_id)
    if sess:
        sess.updated_at = _utcnow()
        if role == "user" and not (sess.title or "").strip():
            sess.title = _derive_session_title(content)
    db.commit()
    db.refresh(msg)
    return msg


def list_messages(db: Session, session_id: str, limit: int = 200) -> list[ChatMessageORM]:
    stmt = (
        select(ChatMessageORM)
        .where(ChatMessageORM.session_id == session_id)
        .order_by(ChatMessageORM.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def orm_message_to_read(m: ChatMessageORM) -> ChatMessageRead:
    """Map ORM row to API schema (sources from JSON blob)."""
    sources: list[ArticleRef] = []
    raw = m.sources_json or {}
    blob = raw.get("sources")
    attr = raw.get("attribution")
    summarization_attribution = attr if isinstance(attr, str) else None
    if isinstance(blob, list):
        for item in blob:
            if isinstance(item, dict):
                try:
                    sources.append(ArticleRef(**item))
                except Exception:
                    continue
    return ChatMessageRead(
        id=m.id,
        role=m.role,
        content=m.content,
        ticker_filter=m.ticker_filter,
        answer_source=m.answer_source,
        fallback_mode=m.fallback_mode,
        summarization_attribution=summarization_attribution,
        sources=sources,
        created_at=m.created_at,
    )
