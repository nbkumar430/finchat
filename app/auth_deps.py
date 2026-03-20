"""FastAPI dependencies for passcode + cookie auth."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import chat_repository
from app.auth_tokens import verify_auth_token
from app.config import get_settings
from app.database import get_db
from app.orm_models import UserORM


def get_db_required(db: Annotated[Optional[Session], Depends(get_db)]) -> Session:
    if db is None:
        raise HTTPException(status_code=503, detail="Chat session persistence is disabled")
    return db


def _user_from_cookie(request: Request, db: Session) -> Optional[UserORM]:  # noqa: UP007
    settings = get_settings()
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return None
    payload = verify_auth_token(token, settings.auth_secret)
    if not payload:
        return None
    return chat_repository.get_user_by_id(db, str(payload["uid"]))


def get_current_user_for_persistence(
    request: Request,
    db: Annotated[Optional[Session], Depends(get_db)],
) -> Optional[UserORM]:  # noqa: UP007
    """When chat DB is off: ``None``.

    * ``require_auth`` true: must present valid cookie or 401.
    * ``require_auth`` false: return cookie user if any, else ``None`` (guest chat — no persistence).
    """
    if db is None:
        return None
    settings = get_settings()
    if settings.require_auth:
        token = request.cookies.get(settings.auth_cookie_name)
        if not token:
            raise HTTPException(status_code=401, detail="Login required")
        payload = verify_auth_token(token, settings.auth_secret)
        if not payload:
            raise HTTPException(status_code=401, detail="Session expired or invalid")
        user = chat_repository.get_user_by_id(db, str(payload["uid"]))
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    return _user_from_cookie(request, db)


def get_current_user_optional(
    request: Request,
    db: Annotated[Optional[Session], Depends(get_db)],
) -> Optional[UserORM]:  # noqa: UP007
    """Resolve user from cookie without 401 (for /api/auth/me)."""
    if db is None:
        return None
    return _user_from_cookie(request, db)


def require_admin_user(
    user: Annotated[Optional[UserORM], Depends(get_current_user_for_persistence)],
) -> UserORM:
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
