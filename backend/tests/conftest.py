"""Test setup. Uses a real PostgreSQL database, migrated with the real Alembic scripts."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest

_BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def _read_database_url() -> str:
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    url = os.environ.get("DATABASE_URL")
    env_file = _BACKEND.parent / ".env"
    if not url and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    if not url:
        pytest.exit("Set TEST_DATABASE_URL or DATABASE_URL. Start the database with: make db", 2)
    return url.rsplit("/", 1)[0] + "/legacylens_test"


_DATA_DIR = tempfile.mkdtemp(prefix="legacylens-tests-")
os.environ.update(
    {
        "DATABASE_URL": _read_database_url(),
        "DATA_DIR": _DATA_DIR,
        "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256",
        "ENVIRONMENT": "test",
        "GEMINI_API_KEY": "test-gemini-key",
        "GROQ_API_KEY": "test-groq-key",
        "GEMINI_MODELS": "gemini-3.5-flash-lite",
        "GROQ_MODELS": "llama-3.3-70b-versatile",
        "OLLAMA_MODELS": "test-model",
        "OLLAMA_BASE_URL": "http://ollama.test:11434",
        "LLM_PROVIDER_ORDER": "gemini,groq,ollama",
        "GEMINI_DAILY_REQUEST_BUDGET": "1000",
        "GEMINI_MINUTE_REQUEST_BUDGET": "1000",
        "GROQ_DAILY_REQUEST_BUDGET": "1000",
        "GROQ_MINUTE_REQUEST_BUDGET": "1000",
        "SANDBOX_TIMEOUT_SECONDS": "60",
    }
)

import psycopg  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.core.security import login_throttle, rate_limiter  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import jobs, retrieval  # noqa: E402
from app.services.llm.router import get_router  # noqa: E402


class FakeEmbedder:
    """Deterministic bag-of-words vectors, so tests need no model download."""

    dimension = 128

    def embed(self, texts):
        vectors = np.zeros((len(texts), self.dimension), dtype="float32")
        for row, value in enumerate(texts):
            for word in value.lower().replace("_", " ").split():
                bucket = int(hashlib.sha1(word.encode(), usedforsecurity=False).hexdigest(), 16)
                vectors[row, bucket % self.dimension] += 1.0
            norm = np.linalg.norm(vectors[row])
            if norm:
                vectors[row] /= norm
        return vectors


def _ensure_database(url: str) -> None:
    sync_url = url.replace("postgresql+psycopg://", "postgresql://")
    admin_url, _, name = sync_url.rpartition("/")
    with psycopg.connect(f"{admin_url}/postgres", autocommit=True) as connection:
        exists = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            connection.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    _ensure_database(os.environ["DATABASE_URL"])
    config = Config(str(_BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND / "alembic"))
    command.upgrade(config, "head")
    yield
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with get_engine().begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    jobs.set_inline(True)
    retrieval.set_embedder(FakeEmbedder())
    login_throttle.reset()
    rate_limiter.reset()
    get_router.cache_clear()
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def register(client: TestClient) -> Callable[..., dict[str, str]]:
    def _register(email: str = "dev@example.com") -> dict[str, str]:
        response = client.post(
            "/api/auth/register",
            json={"email": email, "name": "Dev", "password": "correct horse battery"},
        )
        assert response.status_code == 201, response.text
        client.cookies.clear()  # Tests choose identities explicitly through headers.
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _register


@pytest.fixture
def auth(register: Callable[..., dict[str, str]]) -> dict[str, str]:
    return register()


def zip_directory(
    path: Path, extra: dict[str, str] | None = None, changes: dict[str, str | None] | None = None
) -> bytes:
    """A ZIP of a fixture. `changes` replaces or (with None) removes files."""
    buffer = io.BytesIO()
    changes = changes or {}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(path.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                relative = file.relative_to(path).as_posix()
                if relative not in changes:
                    bundle.write(file, relative)
        for relative, content in {**(extra or {}), **changes}.items():
            if content is not None:
                bundle.writestr(relative, content)
    return buffer.getvalue()


@pytest.fixture
def upload(client: TestClient, auth: dict[str, str]) -> Callable[..., dict]:
    """Uploads a fixture project and returns it fully analyzed (jobs run inline)."""

    def _upload(name: str, *, headers: dict[str, str] | None = None, extra: dict[str, str] | None = None) -> dict:
        data = zip_directory(FIXTURES / name, extra)
        response = client.post(
            "/api/projects/upload",
            headers=headers or auth,
            files={"file": (f"{name}.zip", data, "application/zip")},
        )
        assert response.status_code == 202, response.text
        project_id = response.json()["id"]
        detail = client.get(f"/api/projects/{project_id}", headers=headers or auth).json()
        assert detail["status"] == "ready", detail
        return detail

    return _upload
