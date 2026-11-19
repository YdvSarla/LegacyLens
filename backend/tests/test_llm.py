"""AI provider switching, free-quota fallback and the never-paid guarantees."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import ALLOWED_PROVIDERS, Settings, get_settings
from app.db import models as m
from app.db.session import session_scope
from app.services.llm import AllProvidersUnavailable, ChatMessage
from app.services.llm.providers import GEMINI_BASE_URL, GROQ_BASE_URL, next_pacific_midnight
from app.services.llm.router import build_router

GEMINI_URL = f"{GEMINI_BASE_URL}/models/gemini-3.5-flash-lite:generateContent"
GROQ_URL = f"{GROQ_BASE_URL}/chat/completions"
OLLAMA_URL = "http://ollama.test:11434/api/chat"
HELLO = [ChatMessage("system", "be brief"), ChatMessage("user", "hi")]


def gemini_ok(text="from gemini"):
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
        },
    )


def groq_ok(text="from groq"):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
    )


def ollama_ok(text="from ollama"):
    return httpx.Response(200, json={"message": {"content": text}, "prompt_eval_count": 4, "eval_count": 2})


def gemini_quota(per_day=False, retry="42s"):
    quota = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if per_day else "GenerateRequestsPerMinute"
    return httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "message": "Quota exceeded",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota}]},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry},
                ],
            }
        },
    )


@pytest.fixture
def router():
    return build_router(get_settings())


def _cooldown(provider, model):
    with session_scope() as db:
        return db.get(m.ProviderCooldown, (provider, model))


class TestRequests:
    @respx.mock
    def test_gemini_request_shape(self, router):
        route = respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        result = router.generate(HELLO)
        assert (result.provider, result.text, result.input_tokens) == ("gemini", "from gemini", 5)
        sent = route.calls[0].request
        assert sent.headers["x-goog-api-key"] == "test-gemini-key"
        assert "test-gemini-key" not in str(sent.url), "keys must not appear in URLs, where they get logged"
        body = httpx.Response(200, content=sent.content).json()
        assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
        assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]

    @respx.mock
    def test_gemini_ignores_thought_parts(self, router):
        respx.post(GEMINI_URL).mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "hmm", "thought": True}, {"text": "answer"}]}}]},
            )
        )
        assert router.generate(HELLO).text == "answer"

    @respx.mock
    def test_groq_request_never_asks_for_a_paid_tier(self, router):
        route = respx.post(GROQ_URL).mock(return_value=groq_ok())
        router.generate(HELLO, only_provider="groq")
        sent = route.calls[0].request
        assert sent.headers["authorization"] == "Bearer test-groq-key"
        body = httpx.Response(200, content=sent.content).json()
        assert "service_tier" not in body
        assert body["model"] == "llama-3.3-70b-versatile"

    @respx.mock
    def test_ollama_needs_no_key(self, router):
        route = respx.post(OLLAMA_URL).mock(return_value=ollama_ok())
        result = router.generate(HELLO, only_provider="ollama")
        assert result.provider == "ollama"
        assert "authorization" not in route.calls[0].request.headers


class TestFallback:
    @respx.mock
    def test_quota_falls_back_to_next_provider(self, router):
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        result = router.generate(HELLO)
        assert result.provider == "groq"
        assert result.fallbacks[0]["provider"] == "gemini"
        assert result.fallbacks[0]["reason"] == "quota"

    @respx.mock
    def test_falls_all_the_way_to_local_model(self, router):
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(
            return_value=httpx.Response(429, headers={"retry-after": "60"}, json={"error": {"message": "x"}})
        )
        respx.post(OLLAMA_URL).mock(return_value=ollama_ok())
        assert router.generate(HELLO).provider == "ollama"

    @respx.mock
    def test_cooldown_skips_exhausted_provider_on_next_call(self, router):
        gemini = respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        router.generate(HELLO)
        router.generate(HELLO)
        assert gemini.call_count == 1, "A provider that reported no quota must not be retried at once"

    @respx.mock
    def test_retry_delay_from_gemini_sets_cooldown(self, router):
        respx.post(GEMINI_URL).mock(return_value=gemini_quota(retry="42s"))
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        router.generate(HELLO)
        remaining = (_cooldown("gemini", "gemini-3.5-flash-lite").until - datetime.now(UTC)).total_seconds()
        assert 35 < remaining <= 42

    @respx.mock
    def test_daily_quota_waits_until_pacific_midnight(self, router):
        respx.post(GEMINI_URL).mock(return_value=gemini_quota(per_day=True))
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        router.generate(HELLO)
        assert _cooldown("gemini", "gemini-3.5-flash-lite").until > datetime.now(UTC) + timedelta(minutes=1)

    @respx.mock
    def test_groq_retry_after_header_sets_cooldown(self, router):
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(
            return_value=httpx.Response(429, headers={"retry-after": "120"}, json={"error": {"message": "x"}})
        )
        respx.post(OLLAMA_URL).mock(return_value=ollama_ok())
        router.generate(HELLO)
        remaining = (_cooldown("groq", "llama-3.3-70b-versatile").until - datetime.now(UTC)).total_seconds()
        assert 110 < remaining <= 120

    @respx.mock
    def test_cooldown_survives_a_new_router_after_restart(self, router):
        gemini = respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        router.generate(HELLO)
        build_router(get_settings()).generate(HELLO)
        assert gemini.call_count == 1

    @respx.mock
    @pytest.mark.parametrize(
        "response,reason",
        [
            (httpx.Response(401, json={"error": {"message": "bad key"}}), "auth"),
            (httpx.Response(403, json={"error": {"message": "denied"}}), "auth"),
            (httpx.Response(404, json={"error": {"message": "no model"}}), "model_not_found"),
            (httpx.Response(500, text="oops"), "unavailable"),
            (httpx.Response(503, text="overloaded"), "unavailable"),
            (httpx.Response(400, json={"error": {"message": "too long"}}), "bad_request"),
        ],
    )
    def test_every_failure_kind_falls_back(self, router, response, reason):
        respx.post(GEMINI_URL).mock(return_value=response)
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        result = router.generate(HELLO)
        assert result.provider == "groq"
        assert result.fallbacks[0]["reason"] == reason

    @respx.mock
    def test_timeout_and_connection_errors_fall_back(self, router):
        respx.post(GEMINI_URL).mock(side_effect=httpx.ReadTimeout("slow"))
        respx.post(GROQ_URL).mock(side_effect=httpx.ConnectError("down"))
        respx.post(OLLAMA_URL).mock(return_value=ollama_ok())
        assert router.generate(HELLO).provider == "ollama"

    @respx.mock
    def test_blocked_gemini_response_falls_back(self, router):
        respx.post(GEMINI_URL).mock(
            return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
        )
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        assert router.generate(HELLO).provider == "groq"

    @respx.mock
    def test_all_exhausted_raises_with_retry_time(self, router):
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=httpx.Response(429, json={"error": {"message": "x"}}))
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("not running"))
        with pytest.raises(AllProvidersUnavailable) as caught:
            router.generate(HELLO)
        assert caught.value.next_retry_at is not None
        assert {a["provider"] for a in caught.value.attempts} == {"gemini", "groq", "ollama"}
        assert "free AI quota" in caught.value.user_message
        assert "UTC" in caught.value.user_message

    @respx.mock
    def test_nothing_configured_or_reachable_says_how_to_fix_it(self):
        settings = get_settings().model_copy(update={"gemini_api_key": None, "groq_api_key": None})
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("not running"))
        with pytest.raises(AllProvidersUnavailable) as caught:
            build_router(settings).generate(HELLO)
        assert "GEMINI_API_KEY" in caught.value.user_message
        assert "Ollama" in caught.value.user_message

    @respx.mock
    def test_second_model_of_same_provider_is_tried(self):
        settings = get_settings().model_copy(update={"gemini_models": ["gemini-3.5-flash-lite", "gemini-3.8-flash"]})
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        second = respx.post(f"{GEMINI_BASE_URL}/models/gemini-3.8-flash:generateContent").mock(return_value=gemini_ok())
        result = build_router(settings).generate(HELLO)
        assert (result.provider, result.model) == ("gemini", "gemini-3.8-flash")
        assert second.called


class TestLocalBudgets:
    """These stop calls before the provider's own limit, even with billing linked to a key."""

    @respx.mock
    def test_daily_budget_stops_calling_the_provider(self):
        settings = get_settings().model_copy(update={"gemini_daily_request_budget": 2})
        gemini = respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        router = build_router(settings)
        assert [router.generate(HELLO).provider for _ in range(4)] == ["gemini", "gemini", "groq", "groq"]
        assert gemini.call_count == 2

    @respx.mock
    def test_minute_budget_stops_calling_the_provider(self):
        settings = get_settings().model_copy(update={"groq_minute_request_budget": 1})
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        respx.post(OLLAMA_URL).mock(return_value=ollama_ok())
        router = build_router(settings)
        order = ["groq", "ollama"]
        assert router.generate(HELLO, preferred_order=order).provider == "groq"
        assert router.generate(HELLO, preferred_order=order).provider == "ollama"

    @respx.mock
    def test_budget_is_shared_between_router_instances(self):
        settings = get_settings().model_copy(update={"gemini_daily_request_budget": 1})
        gemini = respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        build_router(settings).generate(HELLO)
        assert build_router(settings).generate(HELLO).provider == "groq"
        assert gemini.call_count == 1

    @respx.mock
    def test_usage_is_recorded(self, router):
        respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        router.generate(HELLO)
        with session_scope() as db:
            row = db.scalars(select(m.LlmUsage)).one()
        assert (row.provider, row.status, row.input_tokens, row.output_tokens) == ("gemini", "ok", 5, 2)

    @respx.mock
    def test_status_reports_budget_exhaustion(self):
        settings = get_settings().model_copy(update={"gemini_daily_request_budget": 1})
        respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        router = build_router(settings)
        router.generate(HELLO)
        status = next(s for s in router.status() if s.provider == "gemini")
        assert (status.state, status.reason, status.requests_today) == ("budget_exhausted", "daily_budget", 1)


class TestSwitching:
    @respx.mock
    def test_user_order_wins(self, router):
        respx.post(GROQ_URL).mock(return_value=groq_ok())
        assert router.generate(HELLO, preferred_order=["groq", "gemini"]).provider == "groq"

    @respx.mock
    def test_only_provider_never_falls_back(self, router):
        respx.post(GROQ_URL).mock(return_value=httpx.Response(500, text="x"))
        gemini = respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        with pytest.raises(AllProvidersUnavailable):
            router.generate(HELLO, only_provider="groq")
        assert not gemini.called

    @respx.mock
    def test_local_only_never_contacts_cloud_providers(self, router):
        gemini = respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        groq = respx.post(GROQ_URL).mock(return_value=groq_ok())
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(AllProvidersUnavailable):
            router.generate(HELLO, local_only=True)
        assert not gemini.called and not groq.called

    def test_missing_key_is_skipped_without_a_request(self):
        settings = get_settings().model_copy(update={"gemini_api_key": None, "groq_api_key": None})
        with respx.mock(assert_all_called=False) as mock:
            gemini = mock.post(GEMINI_URL).mock(return_value=gemini_ok())
            mock.post(OLLAMA_URL).mock(return_value=ollama_ok())
            result = build_router(settings).generate(HELLO)
        assert result.provider == "ollama"
        assert not gemini.called
        assert result.fallbacks[0]["reason"] == "not_configured"


class TestNeverPaid:
    def test_only_free_providers_exist(self):
        assert ALLOWED_PROVIDERS == ("gemini", "groq", "ollama")
        assert set(build_router(get_settings()).providers) == set(ALLOWED_PROVIDERS)

    @pytest.mark.parametrize("name", ["openai", "anthropic", "claude", "mistral", "gemini-paid"])
    def test_paid_providers_cannot_be_configured(self, name):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, llm_provider_order=[name])

    @pytest.mark.parametrize(
        "model",
        ["gemini-4-argon", "gemini-3.1-pro-preview", "gemini-2.5-pro", "gpt-4", "claude-opus"],
    )
    def test_paid_gemini_models_are_rejected(self, model):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, gemini_models=[model])

    @pytest.mark.parametrize(
        "model",
        ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3-flash-preview", "gemini-2.5-flash-lite", "gemma-4"],
    )
    def test_free_gemini_models_are_accepted(self, model):
        assert Settings(_env_file=None, gemini_models=[model]).gemini_models == [model]

    @pytest.mark.parametrize("model", ["glm-4.7:cloud", "qwen3:480b-cloud"])
    def test_ollama_cloud_models_are_rejected(self, model):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, ollama_models=[model])

    @pytest.mark.parametrize("url", ["https://ollama.com", "https://api.ollama.com/v1"])
    def test_ollama_cloud_host_is_rejected(self, url):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, ollama_base_url=url)

    def test_cloud_endpoints_are_not_settings(self):
        fields = set(Settings.model_fields)
        assert not {name for name in fields if "gemini" in name and "url" in name}
        assert not {name for name in fields if "groq" in name and "url" in name}

    def test_no_paid_sdk_or_endpoint_in_the_code(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        forbidden = ("api.openai.com", "api.anthropic.com", "import openai", "import anthropic")
        for path in (root / "app").rglob("*.py"):
            content = path.read_text()
            for token in forbidden:
                assert token not in content, f"{token} found in {path}"
        requirements = (root / "requirements.txt").read_text().lower()
        assert "openai" not in requirements and "anthropic" not in requirements


class TestKeySafety:
    def test_keys_are_hidden_in_settings_repr(self):
        text = repr(Settings(_env_file=None, gemini_api_key="abc-secret", groq_api_key="def-secret"))
        assert "abc-secret" not in text and "def-secret" not in text

    @respx.mock
    def test_provider_error_detail_does_not_contain_key(self, router):
        respx.post(GEMINI_URL).mock(side_effect=httpx.ConnectError("boom"))
        respx.post(GROQ_URL).mock(side_effect=httpx.ConnectError("boom"))
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(AllProvidersUnavailable) as caught:
            router.generate(HELLO)
        assert "test-gemini-key" not in str(caught.value.attempts)
        assert "test-groq-key" not in str(caught.value.attempts)


def test_next_pacific_midnight():
    moment = datetime(2026, 7, 1, 18, 0, tzinfo=UTC)  # 11:00 in Los Angeles.
    assert next_pacific_midnight(moment) == datetime(2026, 7, 2, 7, 0, tzinfo=UTC)
