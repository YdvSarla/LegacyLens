"""Splits files into retrieval chunks that know their exact line range."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.analysis.parsed import ParsedSymbol

MAX_CHUNK_LINES = 80
WINDOW_LINES = 60
WINDOW_OVERLAP = 10


@dataclass
class Chunk:
    start_line: int
    end_line: int
    content: str
    symbol_name: str | None = None


def _slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


def _windows(lines: list[str], start: int, end: int, name: str | None) -> list[Chunk]:
    chunks: list[Chunk] = []
    position = start
    while position <= end:
        stop = min(position + WINDOW_LINES - 1, end)
        content = _slice(lines, position, stop)
        if content.strip():
            chunks.append(Chunk(position, stop, content, name))
        if stop >= end:
            break
        position = stop - WINDOW_OVERLAP + 1
    return chunks


def chunk_file(content: str, symbols: list[ParsedSymbol]) -> list[Chunk]:
    """One chunk per function or method, windows for the rest of the file.

    Classes contribute their methods. Lines outside any function, such as imports,
    properties and top-level statements, are collected into windows of their own.
    """
    lines = content.splitlines()
    if not lines:
        return []
    chunks: list[Chunk] = []
    covered = [False] * (len(lines) + 2)
    leaf_symbols = [s for s in symbols if s.kind in ("function", "method")]
    has_nested_parent = {s.qualified_name for s in leaf_symbols}
    for symbol in sorted(leaf_symbols, key=lambda s: (s.start_line, -s.end_line)):
        start = max(symbol.start_line, 1)
        end = min(symbol.end_line, len(lines))
        if start > end or all(covered[i] for i in range(start, end + 1)):
            continue
        # A function nested in another function is already part of the outer chunk.
        if any(
            other != symbol.qualified_name and symbol.qualified_name.startswith(f"{other}.")
            for other in has_nested_parent
        ):
            continue
        for number in range(start, end + 1):
            covered[number] = True
        if end - start + 1 <= MAX_CHUNK_LINES:
            chunks.append(Chunk(start, end, _slice(lines, start, end), symbol.qualified_name))
        else:
            chunks.extend(_windows(lines, start, end, symbol.qualified_name))
    # Short class-like symbols with no methods, such as interfaces and enums.
    for symbol in symbols:
        if symbol.kind in ("function", "method"):
            continue
        start, end = max(symbol.start_line, 1), min(symbol.end_line, len(lines))
        if start <= end and not any(covered[i] for i in range(start, end + 1)):
            if end - start + 1 <= MAX_CHUNK_LINES:
                for number in range(start, end + 1):
                    covered[number] = True
                chunks.append(Chunk(start, end, _slice(lines, start, end), symbol.qualified_name))
    # Everything left over.
    number = 1
    while number <= len(lines):
        if covered[number]:
            number += 1
            continue
        stop = number
        while stop + 1 <= len(lines) and not covered[stop + 1]:
            stop += 1
        chunks.extend(_windows(lines, number, stop, None))
        number = stop + 1
    return sorted(chunks, key=lambda c: c.start_line)
