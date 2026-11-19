"""HTTP clients for the three supported providers.

Gemini and Groq endpoints are constants rather than settings, so configuration
cannot point these clients at a different (paid) service. No paid-only request
option, such as Groq flex processing or Gemini priority tier, is ever sent.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx

from app.core.config import Settings
from app.services.llm.base import (
    AuthFailed,
    BadRequest,
    ChatMessage,
    ModelNotFound,
    ProviderError,
    ProviderResponse,
    ProviderUnavailable,
    QuotaExceeded,
)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_PACIFIC = ZoneInfo("America/Los_Angeles")


def next_pacific_midnight(now: datetime | None = None) -> datetime:
    """Gemini's requests-per-day quota resets at midnight Pacific time."""
    current = (now or datetime.now(UTC)).astimezone(_PACIFIC)
    tomorrow = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.astimezone(UTC)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _post(
    client: httpx.Client, url: str, *, headers: dict[str, str], payload: dict, timeout: float | None = None
) -> httpx.Response:
    try:
        if timeout is None:
            return client.post(url, headers=headers, json=payload)
        return client.post(url, headers=headers, json=payload, timeout=httpx.Timeout(timeout, connect=10.0))
    except httpx.TimeoutException as exc:
        raise ProviderUnavailable(f"Request timed out: {exc}") from exc
    except httpx.TransportError as exc:
        raise ProviderUnavailable(f"Could not reach the provider: {exc}") from exc


class GeminiProvider:
    name = "gemini"
    is_local = False

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self._settings = settings
        self._client = client

    def is_configured(self) -> bool:
        return self._settings.gemini_api_key is not None

    def models(self) -> list[str]:
        return list(self._settings.gemini_models)

    def generate(
        self, model: str, messages: list[ChatMessage], *, max_tokens: int, temperature: float
    ) -> ProviderResponse:
        key = self._settings.gemini_api_key
        if key is None:
            raise AuthFailed("GEMINI_API_KEY is not set.")
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ]
        payload: dict = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        response = _post(
            self._client,
            f"{GEMINI_BASE_URL}/models/{model}:generateContent",
            headers={"x-goog-api-key": key.get_secret_value()},
            payload=payload,
        )
        if response.status_code != 200:
            raise self._error(response)
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> ProviderResponse:
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable("Gemini returned invalid JSON.") from exc
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise BadRequest(f"Gemini returned no answer ({reason}).")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        if not text.strip():
            finish = candidates[0].get("finishReason", "unknown")
            raise BadRequest(f"Gemini returned an empty answer (finishReason={finish}).")
        usage = data.get("usageMetadata") or {}
        return ProviderResponse(
            text=text,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )

    @staticmethod
    def _error(response: httpx.Response) -> ProviderError:
        status = response.status_code
        message = _short(response.text)
        details: list[dict] = []
        try:
            error = response.json().get("error") or {}
            message = _short(str(error.get("message") or message))
            details = error.get("details") or []
        except (json.JSONDecodeError, AttributeError):
            pass

        if status == 429:
            retry_seconds = _parse_retry_after(response.headers.get("retry-after"))
            daily = False
            for detail in details:
                kind = str(detail.get("@type", ""))
                if kind.endswith("RetryInfo"):
                    match = re.fullmatch(r"([\d.]+)s", str(detail.get("retryDelay", "")))
                    if match:
                        retry_seconds = float(match.group(1))
                elif kind.endswith("QuotaFailure"):
                    for violation in detail.get("violations") or []:
                        if "perday" in str(violation.get("quotaId", "")).lower():
                            daily = True
            if daily:
                return QuotaExceeded(f"Gemini daily free quota reached: {message}", retry_at=next_pacific_midnight())
            return QuotaExceeded(f"Gemini quota reached: {message}", cooldown_seconds=retry_seconds)
        if status in (401, 403):
            return AuthFailed(f"Gemini rejected the API key: {message}")
        if status == 404:
            return ModelNotFound(f"Gemini model not found: {message}")
        if status == 400 and "api key" in message.lower():
            return AuthFailed(f"Gemini rejected the API key: {message}")
        if status in (400, 413):
            return BadRequest(f"Gemini rejected the request: {message}")
        return ProviderUnavailable(f"Gemini error {status}: {message}")


class GroqProvider:
    name = "groq"
    is_local = False

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self._settings = settings
        self._client = client

    def is_configured(self) -> bool:
        return self._settings.groq_api_key is not None

    def models(self) -> list[str]:
        return list(self._settings.groq_models)

    def generate(
        self, model: str, messages: list[ChatMessage], *, max_tokens: int, temperature: float
    ) -> ProviderResponse:
        key = self._settings.groq_api_key
        if key is None:
            raise AuthFailed("GROQ_API_KEY is not set.")
        response = _post(
            self._client,
            f"{GROQ_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {key.get_secret_value()}"},
            payload={
                "model": model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "max_completion_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
        )
        if response.status_code != 200:
            raise self._error(response)
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable("Groq returned an unexpected response.") from exc
        if not text.strip():
            raise BadRequest("Groq returned an empty answer.")
        usage = data.get("usage") or {}
        return ProviderResponse(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    @staticmethod
    def _error(response: httpx.Response) -> ProviderError:
        status = response.status_code
        message = _short(response.text)
        try:
            message = _short(str(response.json()["error"]["message"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        if status == 429:
            return QuotaExceeded(
                f"Groq quota reached: {message}",
                cooldown_seconds=_parse_retry_after(response.headers.get("retry-after")),
            )
        if status in (401, 403):
            return AuthFailed(f"Groq rejected the API key: {message}")
        if status == 404:
            return ModelNotFound(f"Groq model not found: {message}")
        if status in (400, 413, 422):
            return BadRequest(f"Groq rejected the request: {message}")
        return ProviderUnavailable(f"Groq error {status}: {message}")


class OllamaProvider:
    name = "ollama"
    is_local = True

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self._settings = settings
        self._client = client

    def is_configured(self) -> bool:
        return bool(self._settings.ollama_base_url and self._settings.ollama_models)

    def models(self) -> list[str]:
        return list(self._settings.ollama_models)

    def generate(
        self, model: str, messages: list[ChatMessage], *, max_tokens: int, temperature: float
    ) -> ProviderResponse:
        response = _post(
            self._client,
            f"{self._settings.ollama_base_url}/api/chat",
            headers={},
            timeout=self._settings.ollama_timeout_seconds,
            payload={
                "model": model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "num_ctx": self._settings.ollama_num_ctx,
                },
            },
        )
        if response.status_code != 200:
            message = _short(response.text)
            if response.status_code == 404:
                raise ModelNotFound(
                    f"Ollama does not have model '{model}'. Run: ollama pull {model}",
                    cooldown_seconds=30,
                )
            if response.status_code == 400:
                raise BadRequest(f"Ollama rejected the request: {message}")
            raise ProviderUnavailable(f"Ollama error {response.status_code}: {message}")
        try:
            data = response.json()
            text = data["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProviderUnavailable("Ollama returned an unexpected response.") from exc
        if not text.strip():
            raise BadRequest("Ollama returned an empty answer.")
        return ProviderResponse(
            text=text,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )
