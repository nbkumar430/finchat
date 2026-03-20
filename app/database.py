"""SQLite engine, session factory, and schema initialization."""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from typing import TYPE_CHECKING, Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.orm_models import Base

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
SessionLocal: Optional[sessionmaker[Session]] = None


def _sqlite_connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        # FastAPI / threadpool: allow cross-thread use of connection pool
        return {"check_same_thread": False}
    return {}


def configure_engine(settings: Settings) -> None:
    global _engine, SessionLocal
    if not settings.chat_sessions_enabled:
        _engine = None
        SessionLocal = None
        logger.info("Chat sessions persistence disabled (CHAT_SESSIONS_ENABLED=false)")
        return

    path = settings.chat_sqlite_path
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)

    url = f"sqlite:///{path}"
    _engine = create_engine(
        url,
        connect_args=_sqlite_connect_args(url),
        pool_pre_ping=True,
    )

    # Enforce foreign keys in SQLite
    @event.listens_for(_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=_engine,
        expire_on_commit=False,
    )
    logger.info("Chat database engine configured at %s", path)


def _migrate_sqlite_schema(engine: Engine) -> None:
    """Lightweight migrations for existing SQLite files (CREATE_ALL skips alters)."""
    if not str(engine.url).startswith("sqlite"):
        return
    try:
        insp = inspect(engine)
        tables = insp.get_table_names()
        if "chat_sessions" in tables:
            cols = {c["name"] for c in insp.get_columns("chat_sessions")}
            if "user_id" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE chat_sessions ADD COLUMN user_id VARCHAR(36)"))
                logger.info("Migration: added chat_sessions.user_id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("SQLite schema migration: %s", exc)


def init_db() -> None:
    if _engine is None:
        return
    Base.metadata.create_all(bind=_engine)
    _migrate_sqlite_schema(_engine)
    logger.info("Chat database tables ready")


def get_db() -> Generator[Optional[Session], None, None]:
    """Yield DB session, or None when chat persistence is disabled."""
    if SessionLocal is None:
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def reset_for_tests() -> None:
    """Tear down engine (pytest only)."""
    global _engine, SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    SessionLocal = None
