"""Local embeddings (Sentence Transformers) and a per-project FAISS index."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, Protocol

import numpy as np
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings, get_settings
from app.db import models as m
from app.services.analysis.scanner import NON_PRODUCTION_ROLES

logger = logging.getLogger("legacylens.retrieval")

_RRF_K = 60
_MAX_EMBEDDED_CHARS = 1000
_CACHE_FILE = "embedding_cache.npz"
_MAX_SEARCH_TERMS = 6
# Questions are usually about the product, so tests and samples rank lower unless asked for.
_NON_PRODUCTION_WEIGHT = 0.45
_ASKS_ABOUT_TESTS = re.compile(
    r"\b(test|tests|testing|spec|specs|example|examples|sample|samples|demo)\b", re.IGNORECASE
)
_STOPWORDS = {
    "the",
    "who",
    "gets",
    "someone",
    "anyone",
    "they",
    "them",
    "then",
    "will",
    "after",
    "before",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "what",
    "where",
    "when",
    "which",
    "does",
    "how",
    "why",
    "are",
    "was",
    "were",
    "can",
    "could",
    "should",
    "would",
    "has",
    "have",
    "had",
    "not",
    "you",
    "your",
    "our",
    "its",
    "into",
    "use",
    "used",
    "uses",
    "using",
    "code",
    "file",
    "files",
    "function",
    "functions",
    "class",
    "classes",
    "method",
    "methods",
    "project",
    "explain",
    "show",
    "find",
    "tell",
    "about",
    "work",
    "works",
    "get",
    "set",
    "all",
    "any",
    "there",
}
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$\\.\-/]{2,}")


class IndexingCancelled(Exception):
    """The project was deleted or analyzed again while its index was being built."""


class ChunkRow(NamedTuple):
    chunk_id: int
    path: str
    symbol: str | None
    content: str
    role: str


class Embedder(Protocol):
    dimension: int

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    """Runs locally on the CPU. The model is loaded on first use and then cached on disk."""

    def __init__(self, model_name: str, batch_size: int) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                logger.info("Loading embedding model %s", self.model_name)
                self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    @property
    def dimension(self) -> int:
        model = self._load()
        # The method was renamed in newer versions of the library.
        method = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
        return int(method())

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._load().encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype="float32")


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        settings = get_settings()
        _embedder = SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_batch_size)
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Test hook: replaces the embedder."""
    global _embedder
    _embedder = embedder


def _index_dir(settings: Settings, project_id: uuid.UUID) -> Path:
    return settings.indexes_dir / str(project_id)


def _embedding_text(path: str, symbol: str | None, content: str) -> str:
    # Measured on real repositories: adding names split into plain words made retrieval worse,
    # while root words in the keyword search improved it. See docs/ARCHITECTURE.md.
    header = f"File: {path}" + (f" | Symbol: {symbol}" if symbol else "")
    # The model only reads about 1,000 characters, so more text would just slow it down.
    return f"{header}\n{content[:_MAX_EMBEDDED_CHARS]}"


def load_chunk_rows(db: Session, project_id: uuid.UUID) -> list[ChunkRow]:
    """Everything needed to decide what to embed, and to embed it."""
    statement = (
        select(m.CodeChunk.id, m.SourceFile.path, m.CodeChunk.symbol_name, m.CodeChunk.content, m.SourceFile.role)
        .join(m.SourceFile, m.CodeChunk.file_id == m.SourceFile.id)
        .where(m.CodeChunk.project_id == project_id)
        .order_by(m.CodeChunk.id)
    )
    return [ChunkRow(*row) for row in db.execute(statement).all()]


def choose_chunks_to_embed(rows: Sequence[ChunkRow], settings: Settings) -> list[ChunkRow]:
    """Embedding is slow on a CPU, so large projects embed the code that matters most.

    Tests are skipped once a project is large, and functions come before loose windows of
    code. Everything left out can still be found by keyword search.
    """
    selected = list(rows)
    if len(selected) > settings.embed_tests_below_chunks:
        selected = [row for row in selected if row.role not in NON_PRODUCTION_ROLES]
    selected.sort(key=lambda row: (row.symbol is None, row.chunk_id))
    return selected[: settings.max_embedded_chunks]


def _load_vector_cache(directory: Path, model_name: str) -> dict[str, np.ndarray]:
    path = directory / _CACHE_FILE
    if not path.exists():
        return {}
    try:
        with np.load(path, allow_pickle=False) as data:
            if str(data["model"]) != model_name:
                return {}
            return dict(zip(data["hashes"].tolist(), data["vectors"], strict=True))
    except (OSError, ValueError, KeyError):
        logger.warning("Ignoring an unreadable embedding cache in %s", directory)
        return {}


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()


def build_index(
    db: Session,
    project_id: uuid.UUID,
    settings: Settings,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Embeds the project's chunks and writes a new FAISS index atomically.

    Vectors of unchanged code are reused, so updating a large project takes seconds or
    minutes instead of embedding everything again.
    """
    import faiss

    rows = load_chunk_rows(db, project_id)
    directory = _index_dir(settings, project_id)
    if not rows:
        shutil.rmtree(directory, ignore_errors=True)
        _load_index.cache_clear()
        return {"chunks": 0, "embedded": 0, "reused": 0}
    selected = choose_chunks_to_embed(rows, settings)
    embedder = get_embedder()
    model_name = getattr(embedder, "model_name", type(embedder).__name__)
    cache = _load_vector_cache(directory, model_name)
    texts = [_embedding_text(row.path, row.symbol, row.content) for row in selected]
    hashes = [_text_hash(text) for text in texts]
    missing = [position for position, digest in enumerate(hashes) if digest not in cache]
    batch = max(settings.embedding_batch_size * 4, 64)
    if progress is not None:
        progress(0, len(missing))
    for start in range(0, len(missing), batch):
        part = missing[start : start + batch]
        vectors = embedder.embed([texts[position] for position in part])
        for position, vector in zip(part, vectors, strict=True):
            cache[hashes[position]] = np.asarray(vector, dtype="float32")
        if progress is not None:
            progress(min(start + batch, len(missing)), len(missing))
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(embedder.dimension))
    matrix = np.stack([cache[digest] for digest in hashes]).astype("float32")
    index.add_with_ids(matrix, np.asarray([row.chunk_id for row in selected], dtype="int64"))
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "index.faiss.tmp"
    faiss.write_index(index, str(temporary))
    temporary.replace(directory / "index.faiss")
    unique = list(dict.fromkeys(hashes))
    cache_path = directory / "cache.tmp.npz"
    np.savez(
        cache_path,
        model=np.asarray(model_name),
        hashes=np.asarray(unique),
        vectors=np.stack([cache[digest] for digest in unique]).astype("float32"),
    )
    cache_path.replace(directory / _CACHE_FILE)
    _load_index.cache_clear()
    return {"chunks": len(rows), "embedded": len(selected), "reused": len(selected) - len(missing)}


def delete_index(settings: Settings, project_id: uuid.UUID) -> None:
    shutil.rmtree(_index_dir(settings, project_id), ignore_errors=True)
    _load_index.cache_clear()


@lru_cache(maxsize=8)
def _load_index(path: str, modified: float):
    import faiss

    return faiss.read_index(path)


def _vector_search(settings: Settings, project_id: uuid.UUID, query: str, limit: int) -> list[int]:
    path = _index_dir(settings, project_id) / "index.faiss"
    if not path.exists():
        return []
    try:
        index = _load_index(str(path), path.stat().st_mtime)
        vector = get_embedder().embed([query])
        _, ids = index.search(vector, limit)
    except Exception:  # noqa: BLE001
        logger.exception("Vector search failed, falling back to keyword search.")
        return []
    return [int(i) for i in ids[0] if i != -1]


def _stem(word: str) -> str:
    """A rough word root, so "replies" finds Reply and "notified" finds Notification."""
    lower = word.lower()
    if lower.endswith("ies") and len(lower) > 5:
        return lower[:-3]
    if lower.endswith(("ied", "ing")) and len(lower) > 6:
        return lower[:-3]
    if lower.endswith("ed") and len(lower) > 5:
        stem = lower[:-2]
        return stem[:-1] if len(stem) > 3 and stem[-1] == stem[-2] else stem  # banned -> ban
    if lower.endswith("s") and not lower.endswith("ss") and len(lower) > 4:
        return lower[:-1]
    return lower


def extract_terms(query: str) -> list[str]:
    """Identifiers as written, because they are the most specific, then roots of plain words."""
    identifiers: dict[str, None] = {}
    words: dict[str, None] = {}
    for match in _IDENTIFIER_RE.findall(query):
        cleaned = match.strip(".-/\\")
        if len(cleaned) < 3 or cleaned.lower() in _STOPWORDS:
            continue
        looks_like_code = (
            cleaned != cleaned.lower() and cleaned != cleaned.capitalize() or re.search(r"[_\\/.$\d]", cleaned)
        )
        if looks_like_code:
            identifiers[cleaned] = None
        else:
            words[_stem(cleaned)] = None
    ordered = sorted(identifiers, key=len, reverse=True) + sorted(words, key=len, reverse=True)
    return ordered[:_MAX_SEARCH_TERMS]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _keyword_search(db: Session, project_id: uuid.UUID, query: str, limit: int) -> list[int]:
    """Exact matches on symbol names and paths, which embeddings are bad at."""
    ranked: dict[int, float] = {}
    for position, term in enumerate(extract_terms(query)):
        pattern = f"%{_escape_like(term)}%"
        weight = 1.0 / (position + 1)
        symbol_matches = db.execute(
            select(m.CodeChunk.id, m.CodeChunk.symbol_name, m.SourceFile.path)
            .join(m.SourceFile, m.CodeChunk.file_id == m.SourceFile.id)
            .where(
                m.CodeChunk.project_id == project_id,
                or_(
                    m.CodeChunk.symbol_name.ilike(pattern, escape="\\"),
                    m.SourceFile.path.ilike(pattern, escape="\\"),
                ),
            )
            .limit(limit * 3)
        ).all()
        for chunk_id, symbol, _path in symbol_matches:
            boost = 3.0 if symbol and term.lower() in symbol.lower() else 2.0
            ranked[chunk_id] = ranked.get(chunk_id, 0.0) + boost * weight
        content_matches = db.scalars(
            select(m.CodeChunk.id)
            .where(
                m.CodeChunk.project_id == project_id,
                m.CodeChunk.content.ilike(pattern, escape="\\"),
            )
            .limit(limit * 2)
        ).all()
        for chunk_id in content_matches:
            ranked[chunk_id] = ranked.get(chunk_id, 0.0) + weight
    return [chunk_id for chunk_id, _ in sorted(ranked.items(), key=lambda kv: -kv[1])][:limit]


@dataclass
class RetrievedChunk:
    chunk_id: int
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    content: str
    score: float


def search(db: Session, project_id: uuid.UUID, query: str, *, top_k: int | None = None) -> list[RetrievedChunk]:
    """Hybrid search: vector and keyword rankings merged by reciprocal rank fusion."""
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k
    pool = top_k * 4
    scores: dict[int, float] = {}
    for ranking in (
        _vector_search(settings, project_id, query, pool),
        _keyword_search(db, project_id, query, pool),
    ):
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
    if not scores:
        return []
    candidates = sorted(scores, key=lambda chunk_id: -scores[chunk_id])[:pool]
    # The project filter also guards against a stale index returning foreign ids.
    rows = db.scalars(
        select(m.CodeChunk)
        .options(joinedload(m.CodeChunk.file))
        .where(m.CodeChunk.project_id == project_id, m.CodeChunk.id.in_(candidates))
    ).all()
    wants_tests = bool(_ASKS_ABOUT_TESTS.search(query))

    def final_score(row: m.CodeChunk) -> float:
        weight = 1.0 if wants_tests or row.file.role not in NON_PRODUCTION_ROLES else _NON_PRODUCTION_WEIGHT
        return scores[row.id] * weight

    best = sorted(rows, key=final_score, reverse=True)[:top_k]
    return [
        RetrievedChunk(
            chunk_id=row.id,
            path=row.file.path,
            start_line=row.start_line,
            end_line=row.end_line,
            symbol=row.symbol_name,
            content=row.content,
            score=round(final_score(row), 5),
        )
        for row in best
    ]


def has_chunks(db: Session, project_id: uuid.UUID) -> bool:
    return bool(db.scalar(select(func.count(m.CodeChunk.id)).where(m.CodeChunk.project_id == project_id)))
