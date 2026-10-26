"""Fail-closed CI qualification guards, always over test-double backends."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import mss
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from scripts.test_tabs_ci import write_summary
from tests.tabs_ci_support import install_simulated_backends


def test_simulation_guards_refuse_all_backends_and_restore_only_test_doubles() -> None:
    """A broken guard hits a safe sentinel, never a real native API."""
    calls: list[bool] = []

    def sentinel(*_args: object, **_kwargs: object) -> NoReturn:
        calls.append(True)
        raise RuntimeError("Safe sentinel reached")

    with pytest.MonkeyPatch.context() as outer:
        outer.setattr(ctypes, "WinDLL", sentinel, raising=False)
        outer.setattr(mss, "MSS", sentinel)
        outer.setattr(QDesktopServices, "openUrl", sentinel)
        with pytest.MonkeyPatch.context() as guard:
            install_simulated_backends(guard)
            with pytest.raises(AssertionError, match="Tabs CI forbids"):
                ctypes.WinDLL("user32")
            with pytest.raises(AssertionError, match="Tabs CI forbids"):
                mss.MSS()
            with pytest.raises(AssertionError, match="Tabs CI forbids"):
                QDesktopServices.openUrl(QUrl("about:blank"))
        assert calls == []
        with pytest.raises(RuntimeError, match="Safe sentinel"):
            ctypes.WinDLL("user32")
        assert calls == [True]


def test_wrong_runtime_fails_before_running_pytest_or_writing_evidence(
    tmp_path: Path,
) -> None:
    """An incorrectly selected Qt runtime cannot produce a green qualification."""
    script = Path(__file__).resolve().parents[1] / "scripts/test_tabs_ci.py"
    output = tmp_path / "must-not-exist"
    result = subprocess.run(  # noqa: S603 - own script, literal arguments, no shell
        [
            sys.executable,
            str(script),
            "--expected-qt",
            "0.0",
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 2
    assert "Expected Qt 0.0, actual Qt" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(("branch_rate", "expected"), [(0.90, 0), (0.899, 1)])
def test_branch_coverage_gate_cannot_hide_behind_high_line_coverage(
    tmp_path: Path,
    branch_rate: float,
    expected: int,
) -> None:
    """A green test process with high line coverage still fails below branch floor."""
    (tmp_path / "coverage.xml").write_text(
        f'<coverage line-rate="0.99" branch-rate="{branch_rate}"/>', encoding="utf-8"
    )
    manifest: dict[str, object] = {
        "qt": "fixture",
        "python": "fixture",
        "source_head": "fixture",
        "source_dirty": False,
        "seed": 1,
    }
    assert write_summary(tmp_path, manifest, 0) == expected
    assert manifest["status"] == ("passed" if expected == 0 else "failed")
    assert manifest["exit_code"] == expected
    assert "branches >=90%" in (tmp_path / "summary.md").read_text(encoding="utf-8")
