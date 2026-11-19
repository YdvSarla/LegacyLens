"""Password hashing, session tokens and a small login throttle."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import AuthError, RateLimitedError

_hasher = PasswordHasher()
_ALGORITHM = "HS256"
COOKIE_NAME = "legacylens_session"

# Verified against when the email is unknown, so response time does not reveal
# whether an account exists.
_DUMMY_HASH = _hasher.hash("legacylens-timing-equalizer")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str | None) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: str, session_version: int = 0) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "ver": session_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=_ALGORITHM)


@dataclass(frozen=True)
class SessionClaims:
    user_id: str
    session_version: int


def decode_access_token(token: str) -> SessionClaims:
    try:
        payload = jwt.decode(
            token,
            get_settings().secret_key.get_secret_value(),
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("Your session is invalid or has expired. Please sign in again.") from exc
    return SessionClaims(str(payload["sub"]), int(payload.get("ver", 0)))


class LoginThrottle:
    """Allows a limited number of failed logins per key inside a sliding window."""

    def __init__(self, max_failures: int = 8, window_seconds: int = 300) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            attempts = self._prune(key)
            if len(attempts) >= self.max_failures:
                retry_after = int(self.window_seconds - (time.monotonic() - attempts[0])) + 1
                raise RateLimitedError(
                    "Too many failed sign-in attempts. Please try again later.",
                    details={"retry_after_seconds": retry_after},
                )

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key).append(time.monotonic())

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)

    def _prune(self, key: str) -> deque[float]:
        attempts = self._failures[key]
        cutoff = time.monotonic() - self.window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts


login_throttle = LoginThrottle()


class RateLimiter:
    """Limits costly actions per user, so one account cannot use up the shared free AI quota."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int, action: str) -> None:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and events[0] < now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                retry_after = int(window_seconds - (now - events[0])) + 1
                raise RateLimitedError(
                    f"Too many {action} requests. Please wait {retry_after} seconds and try again.",
                    details={"retry_after_seconds": retry_after},
                )
            events.append(now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = RateLimiter()
