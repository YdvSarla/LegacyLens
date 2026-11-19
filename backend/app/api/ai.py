"""AI provider status and switching. Keys are never accepted or returned here."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession
from app.core.config import ALLOWED_PROVIDERS, get_settings
from app.core.errors import ServiceUnavailableError
from app.core.security import rate_limiter
from app.services.llm import AllProvidersUnavailable, ChatMessage, get_router

router = APIRouter(prefix="/ai", tags=["ai providers"])

ProviderName = Literal["gemini", "groq", "ollama"]

_PRIVACY_NOTES = {
    "gemini": "Code is sent to Google. On the free tier, Google may use it to improve its products.",
    "groq": "Code is sent to Groq's cloud service.",
    "ollama": "Code stays on this machine.",
}


class PreferencesRequest(BaseModel):
    # Null restores the server default order.
    provider_order: list[ProviderName] | None = Field(default=None, max_length=3)


@router.get("/providers")
def providers(user: CurrentUser) -> dict[str, Any]:
    llm = get_router()
    statuses = llm.status()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for status in statuses:
        grouped[status.provider].append(
            {
                "model": status.model,
                "state": status.state,
                "reason": status.reason,
                "detail": status.detail,
                "available_at": status.available_at,
                "requests_today": status.requests_today,
                "requests_last_minute": status.requests_last_minute,
                "daily_budget": status.daily_budget,
                "minute_budget": status.minute_budget,
                "tokens_today": status.tokens_today,
            }
        )
    order = llm.effective_order(user.provider_order)
    return {
        "order": order,
        "default_order": get_settings().llm_provider_order,
        "custom_order": user.provider_order is not None,
        "providers": [
            {
                "name": name,
                "configured": llm.providers[name].is_configured(),
                "is_local": llm.providers[name].is_local,
                "privacy": _PRIVACY_NOTES[name],
                "models": grouped.get(name, []),
            }
            for name in order
        ],
    }


@router.put("/preferences")
def preferences(body: PreferencesRequest, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    if body.provider_order is None:
        user.provider_order = None
    else:
        # Keep the order, remove repeats, and only ever store allowed names.
        seen: list[str] = []
        for name in body.provider_order:
            if name in ALLOWED_PROVIDERS and name not in seen:
                seen.append(name)
        user.provider_order = seen
    db.commit()
    return {"provider_order": user.provider_order, "order": get_router().effective_order(user.provider_order)}


@router.post("/providers/{name}/test")
def test_provider(name: ProviderName, user: CurrentUser) -> dict[str, Any]:
    """Makes one very small real call, which counts against the free quota."""
    rate_limiter.check(f"ai:{user.id}", get_settings().rate_limit_ai_per_minute, 60, "AI")
    try:
        result = get_router().generate(
            [ChatMessage("user", "Reply with the single word: ready")],
            only_provider=name,
            max_tokens=1024,  # Newer models may spend part of this on thinking.
        )
    except AllProvidersUnavailable as exc:
        attempts = "; ".join(
            f"{a['model']}: {a['reason']}" + (f" ({a['detail']})" if a.get("detail") else "") for a in exc.attempts
        )
        raise ServiceUnavailableError(
            f"{name} is not available. {attempts}" if attempts else f"{name} is not available."
        ) from exc
    return {"provider": result.provider, "model": result.model, "reply": result.text[:200]}


@router.post("/providers/{name}/clear-cooldown", status_code=204)
def clear_cooldown(name: ProviderName, user: CurrentUser) -> None:
    """For use after fixing a key. Daily budgets still apply, since they come from usage."""
    get_router().clear_cooldowns(name)
