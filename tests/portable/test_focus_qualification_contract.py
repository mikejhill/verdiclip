"""Focused workflow selectors must still name actual source tests; no Qt imports."""

from __future__ import annotations

import ast
import re
from pathlib import Path


def test_focus_qualification_selectors_exist() -> None:
    """A feature rename cannot silently leave the focused proof uncollectable."""
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github/workflows/focus-fixture-lifecycle.yml"
    selectors = re.findall(
        r"tests/[A-Za-z0-9_/]+\.py(?:::[A-Za-z0-9_]+)*",
        workflow.read_text(encoding="utf-8"),
    )
    assert selectors, "Focused workflow must retain explicit regression selectors"
    for selector in selectors:
        module, *names = selector.split("::")
        scope = ast.parse((root / module).read_text(encoding="utf-8")).body
        for name in names:
            matches = [
                node
                for node in scope
                if isinstance(
                    node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
                and node.name == name
            ]
            assert len(matches) == 1, f"Uncollectable focused proof: {selector}"
            scope = matches[0].body
