"""CRUD for chat sessions and messages."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm_models import ChatMessageORM, ChatSessionORM
from app.schemas import ArticleRef, ChatMessageRead


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_session(db: Session, title: Optional[str] = None) -> ChatSessionORM:
    sid = str(uuid.uuid4())
    row = ChatSessionORM(id=sid, title=title, created_at=_utcnow(), updated_at=_utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(db: Session, session_id: str) -> Optional[ChatSessionORM]:
    return db.get(ChatSessionORM, session_id)


def touch_session(db: Session, session_id: str) -> None:
    row = get_session(db, session_id)
    if row:
        row.updated_at = _utcnow()
        db.commit()


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
) -> ChatMessageORM:
    payload: Optional[dict[str, Any]] = None
    if sources:
        payload = {"sources": [s.model_dump() for s in sources]}
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
        sources=sources,
        created_at=m.created_at,
    )
