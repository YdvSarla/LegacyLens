from __future__ import annotations

import logging
import shutil
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.errors import AuthError, ConflictError, ForbiddenError
from app.core.security import (
    COOKIE_NAME,
    create_access_token,
    hash_password,
    login_throttle,
    verify_password,
)
from app.db import models as m
from app.services import retrieval
from app.services.projects import BUSY_STATES, repo_path

logger = logging.getLogger("legacylens.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class AccountDelete(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _user_payload(user: m.User) -> dict[str, Any]:
    return {"id": str(user.id), "email": user.email, "name": user.name, "provider_order": user.provider_order}


def _set_session_cookie(response: Response, user: m.User) -> str:
    settings = get_settings()
    token = create_access_token(str(user.id), user.session_version)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return token


def _session_response(response: Response, user: m.User) -> dict[str, Any]:
    token = _set_session_cookie(response, user)
    return {"user": _user_payload(user), "access_token": token, "token_type": "bearer"}


def _throttled_password_check(user: m.User, password: str, request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    key = f"{client}:{user.email}"
    login_throttle.check(key)
    if not verify_password(password, user.password_hash):
        login_throttle.record_failure(key)
        raise AuthError("That password is not correct.")
    login_throttle.reset(key)


@router.get("/config")
def public_config() -> dict[str, bool]:
    """What the sign-in page needs to know before anyone has signed in."""
    return {"registration_open": get_settings().allow_registration}


@router.post("/register", status_code=201)
def register(body: RegisterRequest, response: Response, db: DbSession) -> dict[str, Any]:
    if not get_settings().allow_registration:
        raise ForbiddenError("Registration is disabled on this server.")
    email = body.email.lower()
    if db.scalars(select(m.User).where(m.User.email == email)).first():
        raise ConflictError("An account with this email already exists.")
    user = m.User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("An account with this email already exists.") from exc
    return _session_response(response, user)


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    email = body.email.lower()
    client = request.client.host if request.client else "unknown"
    key = f"{client}:{email}"
    login_throttle.check(key)
    user = db.scalars(select(m.User).where(m.User.email == email)).first()
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        login_throttle.record_failure(key)
        raise AuthError("Incorrect email or password.")
    login_throttle.reset(key)
    return _session_response(response, user)


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.post("/logout-all", status_code=204)
def logout_everywhere(user: CurrentUser, response: Response, db: DbSession) -> None:
    """Ends every session of this account, including tokens already handed out."""
    user.session_version += 1
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me")
def me(user: CurrentUser) -> dict[str, Any]:
    return {**_user_payload(user), "registration_open": get_settings().allow_registration}


@router.patch("/me")
def update_profile(body: ProfileUpdate, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    user.name = body.name.strip()
    db.commit()
    return _user_payload(user)


@router.post("/change-password")
def change_password(
    body: PasswordChange, request: Request, response: Response, user: CurrentUser, db: DbSession
) -> dict[str, str]:
    """Other devices are signed out. This one receives a new session."""
    _throttled_password_check(user, body.current_password, request)
    user.password_hash = hash_password(body.new_password)
    user.session_version += 1
    db.commit()
    _set_session_cookie(response, user)
    return {"status": "changed"}


@router.delete("/me", status_code=204)
def delete_account(body: AccountDelete, request: Request, response: Response, user: CurrentUser, db: DbSession) -> None:
    _throttled_password_check(user, body.password, request)
    projects = db.scalars(select(m.Project).where(m.Project.owner_id == user.id)).all()
    if any(project.status in BUSY_STATES for project in projects):
        raise ConflictError("A project is still being processed. Please wait for it to finish.")
    project_ids = [project.id for project in projects]
    db.delete(user)
    db.commit()
    settings = get_settings()
    for project_id in project_ids:
        shutil.rmtree(repo_path(settings, project_id), ignore_errors=True)
        retrieval.delete_index(settings, project_id)
    response.delete_cookie(COOKIE_NAME, path="/")
