"""Application settings, loaded from environment variables only.

API keys live here and nowhere else: they are never written to the database,
never returned by an API response and never logged.
"""

from __future__ import annotations

import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The only AI providers this application can talk to. There is deliberately no
# setting that adds another one, so a paid API cannot be enabled by configuration.
ALLOWED_PROVIDERS: tuple[str, ...] = ("gemini", "groq", "ollama")

# Flash, Flash-Lite and Gemma are the families with a free tier. Pro and the
# newest flagship models are paid only on the Gemini API, so they are rejected.
GEMINI_FREE_MODEL_RE = re.compile(r"^(gemini-[\d.]+-flash(-lite)?(-preview)?|gemma-[\w.-]+)$")

DEFAULT_SECRET = "change-me-to-a-long-random-string"

CommaList = Annotated[list[str], NoDecode]


def _split(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    # The project root .env, when running from backend/, then a local one.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    environment: str = "development"
    secret_key: SecretStr = SecretStr(DEFAULT_SECRET)
    access_token_minutes: int = 60 * 12
    cookie_secure: bool = False
    allow_registration: bool = True

    database_url: str = "postgresql+psycopg://legacylens:legacylens@localhost:5433/legacylens"
    data_dir: Path = Path("./data")

    # Ingestion limits.
    max_upload_mb: int = 200
    max_extracted_mb: int = 1024
    max_archive_files: int = 50_000
    max_compression_ratio: int = 200
    max_repo_mb: int = 2048
    clone_timeout_seconds: int = 600
    max_file_kb: int = 512
    max_analyzed_files: int = 20_000
    max_projects_per_user: int = 50

    # Requests per user. They protect the shared free AI quota and the server.
    rate_limit_ai_per_minute: int = 20
    rate_limit_imports_per_hour: int = 30
    rate_limit_test_runs_per_minute: int = 10

    # AI providers. Keys stay in the environment.
    llm_provider_order: CommaList = Field(default_factory=lambda: list(ALLOWED_PROVIDERS))
    llm_timeout_seconds: float = 120.0
    llm_max_output_tokens: int = 2048
    llm_temperature: float = 0.2

    gemini_api_key: SecretStr | None = None
    gemini_models: CommaList = Field(default_factory=lambda: ["gemini-3.5-flash-lite", "gemini-3.8-flash"])
    gemini_daily_request_budget: int = 200
    gemini_minute_request_budget: int = 10

    groq_api_key: SecretStr | None = None
    groq_models: CommaList = Field(
        default_factory=lambda: [
            "llama-3.3-70b-versatile",
            "openai/gpt-oss-20b",
            "llama-3.1-8b-instant",
        ]
    )
    groq_daily_request_budget: int = 800
    groq_minute_request_budget: int = 25

    ollama_base_url: str = "http://localhost:11434"
    ollama_models: CommaList = Field(default_factory=lambda: ["qwen2.5-coder:1.5b"])
    ollama_num_ctx: int = 8192
    # Local models on a CPU are slow. This stays under the web proxy's 300 second limit.
    ollama_timeout_seconds: float = 280.0

    # Embeddings and retrieval.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_batch_size: int = 16
    # At about 50 chunks per second on a CPU, this is around 7 minutes for the largest projects.
    # Chunks beyond the cap, and tests in large projects, are still found by keyword search.
    max_embedded_chunks: int = 20_000
    embed_tests_below_chunks: int = 3_000
    retrieval_top_k: int = 8
    # Free tiers limit tokens per minute, and some allow only about 6,000 to 8,000. This is
    # around 4,500 tokens of code, which leaves room for the question and the answer.
    max_context_chars: int = 16_000

    # Sandbox.
    sandbox_enabled: bool = True
    sandbox_timeout_seconds: int = 180
    sandbox_install_timeout_seconds: int = 300
    sandbox_memory_mb: int = 1024
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 256
    sandbox_output_limit_kb: int = 512
    sandbox_image_python: str = "legacylens-sandbox-python:1"
    sandbox_image_node: str = "legacylens-sandbox-node:2"
    sandbox_image_php: str = "legacylens-sandbox-php:1"

    cors_origins: CommaList = Field(default_factory=lambda: ["http://localhost:3000"])
    worker_threads: int = 2

    @field_validator(
        "llm_provider_order",
        "gemini_models",
        "groq_models",
        "ollama_models",
        "cors_origins",
        mode="before",
    )
    @classmethod
    def _parse_lists(cls, value: object) -> object:
        return _split(value)

    @field_validator("gemini_api_key", "groq_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("llm_provider_order")
    @classmethod
    def _only_free_providers(cls, value: list[str]) -> list[str]:
        unknown = [name for name in value if name not in ALLOWED_PROVIDERS]
        if unknown:
            raise ValueError(
                f"Unsupported AI provider(s) {unknown}. Allowed: {list(ALLOWED_PROVIDERS)}. "
                "LegacyLens only supports free tiers and local models."
            )
        return value

    @field_validator("gemini_models")
    @classmethod
    def _only_free_gemini_models(cls, value: list[str]) -> list[str]:
        paid = [m for m in value if not GEMINI_FREE_MODEL_RE.match(m)]
        if paid:
            raise ValueError(f"Gemini model(s) {paid} are outside the free-tier families (flash, flash-lite, gemma).")
        return value

    @field_validator("ollama_models")
    @classmethod
    def _only_local_ollama_models(cls, value: list[str]) -> list[str]:
        # Ollama's ":cloud" tags run on Ollama's paid cloud rather than locally.
        cloud = [m for m in value if m.endswith("cloud")]
        if cloud:
            raise ValueError(f"Ollama cloud model(s) {cloud} are not allowed. Use local models.")
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def _only_self_hosted_ollama(cls, value: str) -> str:
        host = (urlparse(value).hostname or "").lower()
        if host == "ollama.com" or host.endswith(".ollama.com"):
            raise ValueError("OLLAMA_BASE_URL must point at a self-hosted server, not ollama.com.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _safe_secret(self) -> Settings:
        secret = self.secret_key.get_secret_value()
        if self.environment == "production" and (secret == DEFAULT_SECRET or len(secret) < 32):
            raise ValueError("SECRET_KEY must be a random string of at least 32 characters.")
        if secret == DEFAULT_SECRET:
            # The default is public, so development gets a random key. Sessions end on restart.
            self.secret_key = SecretStr(secrets.token_urlsafe(48))
        return self

    # Paths ------------------------------------------------------------------
    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def indexes_dir(self) -> Path:
        return self.data_dir / "indexes"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
