"""Dispatches a file to the right language analyzer."""

from __future__ import annotations

import logging

from app.services.analysis.languages import javascript, php, python
from app.services.analysis.parsed import ParsedFile

logger = logging.getLogger("legacylens.analysis")

_ANALYZERS = {
    "python": python.analyze,
    "javascript": javascript.analyze,
    "typescript": javascript.analyze,
    "php": php.analyze,
}


def parse_source(language: str, source: bytes, path: str) -> ParsedFile | None:
    """Parses one file. Returns None when the language has no parser or parsing crashes,
    so a single unusual file cannot fail the analysis of a whole project."""
    analyzer = _ANALYZERS.get(language)
    if analyzer is None:
        return None
    try:
        return analyzer(source, path)
    except RecursionError:
        logger.warning("File %s is nested too deeply to analyze.", path)
    except Exception:  # noqa: BLE001
        logger.exception("Analyzer failed on %s", path)
    return None
