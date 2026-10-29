"""Pure diagnostic/source fixtures. Does not invoke actionlint or Windows."""

# Standalone stdlib tests deliberately run without installing pytest or the app.
# ruff: noqa: PT009, PT027

import contextlib
import io
import unittest
from copy import deepcopy
from typing import ClassVar
from unittest.mock import patch

from actionlint_cache_compat import main, validate


class CacheCompatibilityTests(unittest.TestCase):
    """Verify narrow compatibility cannot mask ordinary diagnostics/tool failure."""

    source = (
        "jobs:\n  driver-staging:\n    cache-mode: none\n"
        "  runtime-model:\n    cache-mode: none\n"
    )
    diagnostic: ClassVar[dict[str, object]] = {
        "filepath": ".github/workflows/ci.yml",
        "line": 3,
        "column": 5,
        "kind": "syntax-check",
        "message": (
            'unexpected key "cache-mode" for "job" section. expected one of "if"'
        ),
    }

    def test_only_documented_source_correlated_error_accepted(self) -> None:
        """Require exact source location, diagnostic kind and key."""
        self.assertEqual(validate(self.source, [self.diagnostic]), [])
        self.assertEqual(validate(self.source, []), [])  # newer supporting linter
        for key, value in (
            ("filepath", "other.yml"),
            ("line", 4),
            ("column", 6),
            ("kind", "expression"),
            ("message", "another syntax failure"),
        ):
            with self.subTest(key=key):
                fault = deepcopy(self.diagnostic)
                fault[key] = value
                self.assertEqual(validate(self.source, [fault]), [fault])

    def test_other_errors_preserved_alongside_compatibility(self) -> None:
        """Keep real failures alongside an accepted extension diagnostic."""
        fault = {**self.diagnostic, "message": "duplicate key"}
        self.assertEqual(validate(self.source, [self.diagnostic, fault]), [fault])

    def test_invalid_missing_duplicate_or_misplaced_setting_refused(self) -> None:
        """Refuse unsafe values and ambiguous/missing key placement."""
        faults = (
            self.source.replace("none", "write"),
            self.source.replace("cache-mode: none", "cache-mode: [none]"),
            self.source.replace("    cache-mode: none\n", "", 1),
            self.source.replace("  runtime-model:", "  arbitrary:"),
            self.source.replace("    cache-mode: none", "  cache-mode: none", 1),
            self.source.replace(
                "    cache-mode: none", "    cache-mode: none\n    cache-mode: none", 1
            ),
        )
        for source in faults:
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate(source, [self.diagnostic])

    def test_failed_tool_without_diagnostics_never_passes(self) -> None:
        """An empty or invalid failed-tool result cannot become green."""
        for status in ("1", "2", "invalid"):
            with (
                self.subTest(status=status),
                patch("sys.argv", ["tool", "lint.json", status]),
                patch("pathlib.Path.read_text", side_effect=[self.source, ""]),
                self.assertRaises(ValueError),
            ):
                main()
        with (
            patch("sys.argv", ["tool", "lint.json", "0"]),
            patch("pathlib.Path.read_text", side_effect=[self.source, ""]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(), 0)


if __name__ == "__main__":
    unittest.main()
