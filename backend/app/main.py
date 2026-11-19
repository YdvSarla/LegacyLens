"""LegacyLens API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.api import ai, assistant, auth, compare, documents, insights, projects, regression
from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError, install_error_handlers
from app.core.security import COOKIE_NAME
from app.db.session import session_scope
from app.services import jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("legacylens")


class _BodyTooLarge(Exception):
    pass


class MaxBodySizeMiddleware:
    """Rejects oversized requests before the multipart parser buffers them to disk."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"error":{"code":"too_large","message":"The request is too large.","details":{}}}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityMiddleware:
    """Adds response headers, and rejects cross-origin writes that rely on the session cookie.

    SameSite=Lax treats other ports on the same host as the same site, so a page on
    localhost:8080 could otherwise send requests with a session for localhost:3000.
    """

    UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed_origins = {origin.rstrip("/").lower() for origin in allowed_origins}

    def _is_forbidden(self, scope: Scope) -> bool:
        if scope["method"] not in self.UNSAFE_METHODS:
            return False
        headers = dict(scope.get("headers") or [])
        origin = headers.get(b"origin", b"").decode("latin-1").rstrip("/").lower()
        if not origin or COOKIE_NAME.encode() not in headers.get(b"cookie", b""):
            return False  # Bearer tokens and non-browser clients cannot be forged this way.
        if origin in self.allowed_origins:
            return False
        origin_host = origin.split("://", 1)[-1]
        served_as = {
            headers.get(b"x-forwarded-host", b"").decode("latin-1").lower(),
            headers.get(b"host", b"").decode("latin-1").lower(),
        }
        return origin_host not in served_as

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._is_forbidden(scope):
            body = b'{"error":{"code":"forbidden","message":"This request came from another site.","details":{}}}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        is_docs = scope["path"] in ("/api/docs", "/api/openapi.json")

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-content-type-options", b"nosniff"))
                if not is_docs:
                    # Responses hold private code and analysis, which must not stay in caches.
                    headers.append((b"cache-control", b"no-store"))
                    headers.append((b"x-frame-options", b"DENY"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, with_headers)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    for directory in (settings.repos_dir, settings.indexes_dir, settings.uploads_dir):
        directory.mkdir(parents=True, exist_ok=True)
    jobs.recover_interrupted()
    logger.info("LegacyLens %s started. AI provider order: %s", __version__, settings.llm_provider_order)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="LegacyLens API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=(settings.max_upload_mb + 2) * 1024 * 1024)
    app.add_middleware(SecurityMiddleware, allowed_origins=settings.cors_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    api = APIRouter(prefix="/api")
    for module in (auth, projects, insights, assistant, compare, regression, documents, ai):
        api.include_router(module.router)

    @api.get("/health", tags=["health"])
    def health() -> dict[str, Any]:
        try:
            with session_scope() as db:
                db.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailableError("The database is not reachable.") from exc
        return {"status": "ok", "version": __version__}

    app.include_router(api)
    return app


app = create_app()
