"""Ordinary-layout diagnostic inputs must not reuse the colored-row oracle."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QWidget
from scripts import scrolling_diagnostics as diagnostics

from verdiclip.geometry import Point


@pytest.mark.parametrize("layout", ["edge-prose", "edge-panel", "edge-sidebar"])
def test_document_has_real_prose_and_declared_layout(layout: str) -> None:
    """Vary normal document layout without authoring row-level pixel IDs."""
    source = diagnostics.document(layout)
    assert "The user's flow is the product" not in source  # Escaped HTML prose.
    assert "Get a precise piece of the screen" in source
    assert "background:rgb(" not in source
    assert ('<section tabindex="0"' in source) == (layout == "edge-panel")
    assert ("<aside>" in source) == (layout == "edge-sidebar")


def test_diagnostic_declares_limited_oracle_even_when_capture_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed attempt cannot inherit the special colored-row completeness claim."""
    monkeypatch.setattr(
        diagnostics.app, "_case", Mock(side_effect=RuntimeError("Cannot join"))
    )
    report: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="Cannot join"):
        diagnostics._case("edge-prose", tmp_path, report)
    assert report["oracle"] == (
        "capture/restoration smoke only; inspect exact PNG content"
    )


def test_explorer_uses_only_disposable_owned_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explorer diagnostics do not open user folders or alter app settings."""
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    popen = Mock()
    monkeypatch.setattr(diagnostics.subprocess, "Popen", popen)
    monkeypatch.setattr(diagnostics.native, "_record_version", Mock())
    report: dict[str, object] = {}
    diagnostics._explorer("explorer-click", tmp_path, report)
    folder = tmp_path / "VerdiClip scrolling check"
    assert len(list(folder.glob("*.txt"))) == 160
    assert popen.call_args.args[0] == [
        str(tmp_path / "Windows" / "explorer.exe"),
        "/separate,",
        str(folder),
    ]


def test_diagnostic_gesture_uses_held_native_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both click and Enter allow Qt turns between native press and release."""
    click = Mock()
    monkeypatch.setattr(diagnostics.Desktop, "click", click)
    desktop = object.__new__(diagnostics.HumanDesktop)
    widget = Mock(spec=QWidget)
    desktop.click(widget, Point(30, 40), enter=True)
    click.assert_called_once_with(widget, Point(30, 40), enter=True, hold_ms=120)


def test_explorer_discovery_failure_preserves_setup_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing folder target is a setup failure, not a capture verdict."""
    runner = diagnostics.ExplorerCase()
    runner.process = Mock(pid=123)
    runner.process.poll.return_value = 0
    monkeypatch.setattr(
        diagnostics.native, "_window", Mock(side_effect=RuntimeError("No folder"))
    )
    evidence = Mock()
    monkeypatch.setattr(diagnostics.native, "_desktop_evidence", evidence)
    case = Mock()
    monkeypatch.setattr(diagnostics, "_case", case)
    report: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="No folder"):
        runner.run("explorer-click", tmp_path, report)
    assert report == {"explorer_launcher_pid": 123, "explorer_launcher_exit": 0}
    evidence.assert_called_once_with(tmp_path, report)
    case.assert_not_called()


@pytest.mark.parametrize("state", ["owned", "shared", "exited", "missing"])
def test_explorer_capture_requires_live_owned_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str
) -> None:
    """Shared-shell setup is rejected before moving, focusing or capturing it."""
    runner = diagnostics.ExplorerCase()
    process = Mock(pid=123)
    process.poll.return_value = 0 if state == "exited" else None
    monkeypatch.setattr(diagnostics, "_explorer", Mock(return_value=process))
    assert runner.launch("explorer-click", tmp_path, {}) is process
    if state == "missing":
        runner.process = None
    monkeypatch.setattr(
        diagnostics.native, "_window", Mock(return_value=Mock(handle=9))
    )
    user = Mock()

    def process_id(_window: int, out: c.c_void_p) -> int:
        c.cast(out, c.POINTER(w.DWORD)).contents.value = (
            999 if state == "shared" else 123
        )
        return 7

    user.GetWindowThreadProcessId.side_effect = process_id
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    case = Mock()
    monkeypatch.setattr(diagnostics, "_case", case)
    report: dict[str, object] = {}
    if state == "owned":
        runner.run("explorer-click", tmp_path, report)
        case.assert_called_once_with("explorer-click", tmp_path, report)
    else:
        with pytest.raises(RuntimeError, match="shared-shell capture setup"):
            runner.run("explorer-click", tmp_path, report)
        case.assert_not_called()
