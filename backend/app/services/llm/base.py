"""Shared types for AI providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class LlmResult:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Models that were tried first and why they were skipped.
    fallbacks: list[dict[str, str]] = field(default_factory=list)


class ProviderError(Exception):
    """A provider call failed. `kind` decides what the router does next."""

    kind = "error"
    default_cooldown_seconds = 30

    def __init__(
        self,
        message: str,
        *,
        retry_at: datetime | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_at = retry_at
        self.cooldown_seconds = cooldown_seconds if cooldown_seconds is not None else self.default_cooldown_seconds


class QuotaExceeded(ProviderError):
    """HTTP 429 or an equivalent free-quota signal."""

    kind = "quota"
    default_cooldown_seconds = 60


class AuthFailed(ProviderError):
    """Missing permission or a bad key."""

    kind = "auth"
    default_cooldown_seconds = 600


class ProviderUnavailable(ProviderError):
    """Network failure, timeout, 5xx or a server that is not running."""

    kind = "unavailable"
    default_cooldown_seconds = 20


class ModelNotFound(ProviderError):
    """The configured model does not exist on this provider or was retired."""

    kind = "model_not_found"
    default_cooldown_seconds = 3600


class BadRequest(ProviderError):
    """This request was rejected, for example too large. Another model may accept it."""

    kind = "bad_request"
    default_cooldown_seconds = 0


class LlmProvider(Protocol):
    name: str
    is_local: bool

    def is_configured(self) -> bool: ...

    def models(self) -> list[str]: ...

    def generate(
        self, model: str, messages: list[ChatMessage], *, max_tokens: int, temperature: float
    ) -> ProviderResponse: ...
