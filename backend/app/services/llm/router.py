"""Chooses a provider, tracks free-quota state and falls back automatically.

Fallback only ever moves between the configured free tiers and local models.
When all of them are exhausted the call fails with AllProvidersUnavailable, and
the caller shows a clear message or continues without AI. Nothing is ever retried
against a paid service, because no client for one exists.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import ALLOWED_PROVIDERS, Settings, get_settings
from app.db.models import LlmUsage, ProviderCooldown
from app.db.session import session_scope
from app.services.llm.base import ChatMessage, LlmProvider, LlmResult, ProviderError
from app.services.llm.providers import (
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    next_pacific_midnight,
)

logger = logging.getLogger("legacylens.llm")


_QUOTA_REASONS = {"quota", "daily_budget", "minute_budget"}


class AllProvidersUnavailable(Exception):
    """Every allowed model is out of quota, not configured, or failing."""

    def __init__(self, attempts: list[dict[str, str]], *, next_retry_at: datetime | None) -> None:
        self.attempts = attempts
        self.next_retry_at = next_retry_at
        super().__init__(self.user_message)

    @property
    def user_message(self) -> str:
        reasons = {attempt["reason"] for attempt in self.attempts}
        if not reasons & _QUOTA_REASONS:
            return (
                "No AI provider is reachable. Set GEMINI_API_KEY or GROQ_API_KEY on the server, "
                "or start a local Ollama server."
            )
        message = "The free AI quota is used up on every available provider."
        if self.next_retry_at:
            ready_at = self.next_retry_at.astimezone(UTC).strftime("%H:%M UTC on %d %b %Y")
            message += f" The next one should be ready at {ready_at}."
        return message


@dataclass
class ModelStatus:
    provider: str
    model: str
    configured: bool
    is_local: bool
    state: str  # ready | cooldown | budget_exhausted | not_configured
    reason: str | None
    detail: str | None
    available_at: datetime | None
    requests_today: int
    requests_last_minute: int
    daily_budget: int | None
    minute_budget: int | None
    tokens_today: int


class LlmRouter:
    def __init__(self, settings: Settings, providers: dict[str, LlmProvider]) -> None:
        self.settings = settings
        self.providers = providers
        # Serializes the budget check and the usage insert across worker threads.
        self._lock = threading.Lock()

    # Public API -------------------------------------------------------------
    def effective_order(self, preferred_order: list[str] | None = None, *, local_only: bool = False) -> list[str]:
        """Preferred providers first, then any remaining default ones, deduplicated."""
        order: list[str] = []
        for name in [*(preferred_order or []), *self.settings.llm_provider_order]:
            if name in ALLOWED_PROVIDERS and name in self.providers and name not in order:
                order.append(name)
        if local_only:
            order = [name for name in order if self.providers[name].is_local]
        return order

    def generate(
        self,
        messages: list[ChatMessage],
        *,
        preferred_order: list[str] | None = None,
        only_provider: str | None = None,
        local_only: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmResult:
        max_tokens = max_tokens or self.settings.llm_max_output_tokens
        temperature = self.settings.llm_temperature if temperature is None else temperature
        order = self.effective_order(preferred_order, local_only=local_only)
        if only_provider is not None:
            order = [name for name in order if name == only_provider]

        attempts: list[dict[str, str]] = []
        retry_times: list[datetime] = []
        for name in order:
            provider = self.providers[name]
            if not provider.is_configured():
                attempts.append({"provider": name, "model": "*", "reason": "not_configured", "detail": ""})
                continue
            for model in provider.models():
                blocked = self._blocked_until(name, model)
                if blocked is not None:
                    until, reason = blocked
                    retry_times.append(until)
                    attempts.append({"provider": name, "model": model, "reason": reason, "detail": ""})
                    continue
                started = time.monotonic()
                try:
                    response = provider.generate(model, messages, max_tokens=max_tokens, temperature=temperature)
                except ProviderError as exc:
                    latency = int((time.monotonic() - started) * 1000)
                    until = self._record_failure(name, model, exc, latency)
                    if until is not None:
                        retry_times.append(until)
                    attempts.append({"provider": name, "model": model, "reason": exc.kind, "detail": exc.message})
                    logger.warning("LLM fallback from %s/%s: %s", name, model, exc.kind)
                    continue
                self._record_usage(
                    name,
                    model,
                    "ok",
                    response.input_tokens,
                    response.output_tokens,
                    int((time.monotonic() - started) * 1000),
                )
                return LlmResult(
                    text=response.text,
                    provider=name,
                    model=model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    fallbacks=attempts,
                )
        raise AllProvidersUnavailable(attempts, next_retry_at=min(retry_times) if retry_times else None)

    def status(self, *, now: datetime | None = None) -> list[ModelStatus]:
        now = now or datetime.now(UTC)
        statuses: list[ModelStatus] = []
        with session_scope() as db:
            for name, provider in self.providers.items():
                daily, minute = self._budgets(name)
                for model in provider.models():
                    today, last_minute = self._usage_counts(db, name, model, now)
                    tokens = self._tokens_today(db, name, model, now)
                    state, reason, detail, available_at = "ready", None, None, None
                    if not provider.is_configured():
                        state = "not_configured"
                    else:
                        cooldown = db.get(ProviderCooldown, (name, model))
                        if cooldown and cooldown.until > now:
                            state, reason, detail = "cooldown", cooldown.reason, cooldown.detail
                            available_at = cooldown.until
                        elif daily is not None and today >= daily:
                            state, reason = "budget_exhausted", "daily_budget"
                            available_at = next_pacific_midnight(now)
                        elif minute is not None and last_minute >= minute:
                            state, reason = "budget_exhausted", "minute_budget"
                            available_at = now + timedelta(seconds=60)
                    statuses.append(
                        ModelStatus(
                            provider=name,
                            model=model,
                            configured=provider.is_configured(),
                            is_local=provider.is_local,
                            state=state,
                            reason=reason,
                            detail=detail,
                            available_at=available_at,
                            requests_today=today,
                            requests_last_minute=last_minute,
                            daily_budget=daily,
                            minute_budget=minute,
                            tokens_today=tokens,
                        )
                    )
        return statuses

    def clear_cooldowns(self, provider: str) -> None:
        with session_scope() as db:
            db.execute(delete(ProviderCooldown).where(ProviderCooldown.provider == provider))

    # Internal ---------------------------------------------------------------
    def _budgets(self, provider: str) -> tuple[int | None, int | None]:
        if provider == "gemini":
            return self.settings.gemini_daily_request_budget, self.settings.gemini_minute_request_budget
        if provider == "groq":
            return self.settings.groq_daily_request_budget, self.settings.groq_minute_request_budget
        return None, None  # Local models have no quota.

    @staticmethod
    def _usage_counts(db: Session, provider: str, model: str, now: datetime) -> tuple[int, int]:
        # Rejected calls do not consume quota for auth failures or an unreachable
        # server, but counting every real attempt keeps the local budget conservative.
        day_start = next_pacific_midnight(now) - timedelta(days=1)
        base = select(func.count(LlmUsage.id)).where(
            LlmUsage.provider == provider,
            LlmUsage.model == model,
            LlmUsage.status.in_(("ok", "quota", "bad_request")),
        )
        today = db.scalar(base.where(LlmUsage.created_at >= day_start)) or 0
        last_minute = db.scalar(base.where(LlmUsage.created_at >= now - timedelta(seconds=60))) or 0
        return int(today), int(last_minute)

    @staticmethod
    def _tokens_today(db: Session, provider: str, model: str, now: datetime) -> int:
        day_start = next_pacific_midnight(now) - timedelta(days=1)
        total = db.scalar(
            select(
                func.coalesce(
                    func.sum(func.coalesce(LlmUsage.input_tokens, 0) + func.coalesce(LlmUsage.output_tokens, 0)),
                    0,
                )
            ).where(
                LlmUsage.provider == provider,
                LlmUsage.model == model,
                LlmUsage.created_at >= day_start,
            )
        )
        return int(total or 0)

    def _blocked_until(self, provider: str, model: str) -> tuple[datetime, str] | None:
        now = datetime.now(UTC)
        daily, minute = self._budgets(provider)
        with self._lock, session_scope() as db:
            cooldown = db.get(ProviderCooldown, (provider, model))
            if cooldown and cooldown.until > now:
                return cooldown.until, cooldown.reason
            if daily is None and minute is None:
                return None
            today, last_minute = self._usage_counts(db, provider, model, now)
            if daily is not None and today >= daily:
                return next_pacific_midnight(now), "daily_budget"
            if minute is not None and last_minute >= minute:
                return now + timedelta(seconds=60), "minute_budget"
        return None

    def _record_usage(
        self,
        provider: str,
        model: str,
        status: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
    ) -> None:
        with session_scope() as db:
            db.add(
                LlmUsage(
                    provider=provider,
                    model=model,
                    status=status,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                )
            )

    def _record_failure(self, provider: str, model: str, exc: ProviderError, latency_ms: int) -> datetime | None:
        self._record_usage(provider, model, exc.kind, None, None, latency_ms)
        until = exc.retry_at
        if until is None and exc.cooldown_seconds > 0:
            until = datetime.now(UTC) + timedelta(seconds=exc.cooldown_seconds)
        if until is None:
            return None
        with session_scope() as db:
            statement = insert(ProviderCooldown).values(
                provider=provider, model=model, until=until, reason=exc.kind, detail=exc.message
            )
            db.execute(
                statement.on_conflict_do_update(
                    index_elements=[ProviderCooldown.provider, ProviderCooldown.model],
                    set_={"until": until, "reason": exc.kind, "detail": exc.message},
                )
            )
        return until


def build_router(settings: Settings, client: httpx.Client | None = None) -> LlmRouter:
    http = client or httpx.Client(timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0))
    providers: dict[str, LlmProvider] = {
        "gemini": GeminiProvider(settings, http),
        "groq": GroqProvider(settings, http),
        "ollama": OllamaProvider(settings, http),
    }
    return LlmRouter(settings, providers)


@lru_cache
def get_router() -> LlmRouter:
    return build_router(get_settings())
