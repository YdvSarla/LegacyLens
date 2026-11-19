"""Tree-sitter helpers. Error-tolerant parsing matters for legacy code, which may
use old syntax (Python 2, PHP 5) that a strict parser rejects outright."""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import lru_cache

import tree_sitter_javascript
import tree_sitter_php
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser


@lru_cache
def _language(name: str) -> Language:
    loaders = {
        "python": tree_sitter_python.language,
        "javascript": tree_sitter_javascript.language,
        "typescript": tree_sitter_typescript.language_typescript,
        "tsx": tree_sitter_typescript.language_tsx,
        "php": tree_sitter_php.language_php,
    }
    return Language(loaders[name]())


def parse(language: str, source: bytes) -> Node:
    # Parser objects are not thread safe, so each parse gets its own.
    return Parser(_language(language)).parse(source).root_node


def text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def line(node: Node) -> int:
    return node.start_point[0] + 1


def end_line(node: Node) -> int:
    # A node ending at column 0 stops at the end of the previous line.
    row, column = node.end_point
    return row + 1 if column > 0 or row == node.start_point[0] else row


def walk(node: Node, *, stop_at: frozenset[str] = frozenset()) -> Iterator[Node]:
    """Depth-first traversal that does not descend into `stop_at` node types."""
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        yield current
        if current.type not in stop_at:
            stack.extend(reversed(current.children))


def count_errors(root: Node) -> int:
    if not root.has_error:
        return 0
    return sum(1 for node in walk(root) if node.type == "ERROR" or node.is_missing)


_STRING_NODES = frozenset({"string", "encapsed_string", "template_string"})
_QUOTED = re.compile(r"""^[a-zA-Z]*("{3}|'{3}|"|'|`)(.*)\1$""", re.DOTALL)


def string_value(node: Node | None) -> str | None:
    """The text of a string literal, or None for anything else.

    The text alone is not enough: `'a' . $b . 'c'` also starts and ends with a quote.
    """
    if node is None or node.type not in _STRING_NODES:
        return None
    match = _QUOTED.match(text(node))
    return match.group(2) if match else None


def one_line(value: str, limit: int = 300) -> str:
    collapsed = " ".join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "..."


def complexity_of(
    node: Node,
    *,
    decisions: frozenset[str],
    boolean_types: frozenset[str],
    boolean_ops: frozenset[str],
    nested: frozenset[str],
) -> int:
    """McCabe style count: one plus each branch, not including nested functions."""
    total = 1
    for current in walk(node, stop_at=nested):
        kind = current.type
        if kind in decisions:
            total += 1
        elif kind in boolean_types:
            operator = current.child_by_field_name("operator")
            operator_text = text(operator) if operator is not None else ""
            if not boolean_ops or operator_text in boolean_ops:
                total += 1
    return total
