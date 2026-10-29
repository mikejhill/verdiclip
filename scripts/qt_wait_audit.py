"""Audit discarded pytest-qt context managers without importing Qt."""

from __future__ import annotations

import ast

CONTEXT_WAITS = frozenset({"waitExposed", "wait_exposed", "waitActive", "wait_active"})


def discarded_waits(source: str) -> list[int]:
    """Return lines that construct and discard a known Qt wait context."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr in CONTEXT_WAITS
    )
