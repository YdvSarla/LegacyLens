"""Shared request dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Header
from sqlalchemy.orm import Session

from app.core.errors import AuthError, ValidationFailed
from app.core.security import COOKIE_NAME, decode_access_token
from app.db import models as m
from app.db.session import get_db
from app.services.projects import get_owned_project

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
    session_cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> m.User:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    token = token or session_cookie
    if not token:
        raise AuthError("Please sign in.")
    claims = decode_access_token(token)
    try:
        user_id = uuid.UUID(claims.user_id)
    except ValueError as exc:
        raise AuthError("Your session is invalid. Please sign in again.") from exc
    user = db.get(m.User, user_id)
    if user is None:
        raise AuthError("Your account no longer exists.")
    if claims.session_version != user.session_version:
        raise AuthError("You were signed out. Please sign in again.")
    return user


CurrentUser = Annotated[m.User, Depends(get_current_user)]


def get_project(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> m.Project:
    return get_owned_project(db, project_id, user)


OwnedProject = Annotated[m.Project, Depends(get_project)]


def require_ready(project: OwnedProject) -> m.Project:
    if project.summary is None:
        raise ValidationFailed("This project has not been analyzed yet.")
    return project


ReadyProject = Annotated[m.Project, Depends(require_ready)]
