"""Real reference sequencing with deterministic capture/clock boundaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import mss
import mss.tools
import pytest
from scripts import installed_observe_smoke as smoke


def setup_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[float], Any, Any, dict[str, Any], smoke.Journal]:
    """A fixed authored panel plus off-panel markers, not provider-derived geometry."""
    clock = [0.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        smoke.time, "sleep", lambda dt: clock.__setitem__(0, round(clock[0] + dt, 6))
    )
    marker_values: list[dict[str, Any]] = [
        {"id": str(i), "rect": [550, 10 + i * 8, 554, 14 + i * 8], "rgb": [i + 1, 2, 3]}
        for i in range(4)
    ]
    state = {
        "mode": "observe-only",
        "alive": True,
        "dpi_x": 96,
        "dpi_y": 96,
        "pid": 123,
        "process_created_100ns": 1234,
        "root_hwnd": 42,
        "rect": [10, 10, 520, 440],
        "content_rect": [12, 12, 502, 438],
        "authored_layout_rect": [0, 0, 600, 500],
        "markers": marker_values,
        "state": {
            "target": {"horizontal": 0, "vertical": 119},
            "sibling": {"vertical": 93},
        },
    }
    expected = smoke.fixture_key(state)
    sequence = [0]

    def latest(_path: Path) -> dict[str, Any]:
        """Each completed native frame receives a strictly newer authored sample."""
        sequence[0] += 1
        return {**state, "sequence": sequence[0]}

    monkeypatch.setattr(smoke, "latest", latest)
    rgb = bytearray(600 * 500 * 3)
    for marker in marker_values:
        left, top, right, bottom = marker["rect"]
        for y in range(top, bottom):
            for x in range(left, right):
                rgb[(y * 600 + x) * 3 : (y * 600 + x) * 3 + 3] = bytes(marker["rgb"])
    source = SimpleNamespace(
        grab=Mock(return_value=SimpleNamespace(width=600, height=500, rgb=bytes(rgb))),
        close=Mock(),
    )
    monkeypatch.setattr(mss, "MSS", Mock(return_value=source))
    monkeypatch.setattr(mss.tools, "to_png", Mock())
    guard = SimpleNamespace(initial={"rect": [0, 0, 600, 500]}, check=Mock())
    journal = smoke.Journal(tmp_path / "calls.jsonl", 15)
    return clock, source, guard, expected, journal


def test_reference_requires_quiet_complete_samples_and_preserves_all_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only independent frozen R selects510x430x3 bytes, never a provider crop."""
    clock, source, guard, expected, journal = setup_reference(monkeypatch, tmp_path)
    try:
        root, panel = smoke.reference(
            journal, guard, tmp_path, expected, tmp_path, "before"
        )
        assert len(root) == 600 * 500 * 3
        assert len(panel) == 510 * 430 * 3
        assert panel == bytes(510 * 430 * 3)
        assert source.grab.call_count >= 3
        assert clock[0] >= 0.2
        cast(Any, mss.MSS).assert_called_once_with(with_cursor=False)
        source.close.assert_called_once()
    finally:
        journal.stream.close()


def test_slow_successful_creation_is_closed_without_grabbing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resource return is retained before rejecting a completed late constructor."""
    clock, source, guard, expected, journal = setup_reference(monkeypatch, tmp_path)

    def create(**_kwargs: object) -> Any:  # noqa: ANN401 - controlled resource fake
        """Return an allocated MSS object at the exact original phase deadline."""
        clock[0] = 2.0
        return source

    monkeypatch.setattr(mss, "MSS", create)
    try:
        with pytest.raises(RuntimeError, match="setup exceeded"):
            smoke.reference(journal, guard, tmp_path, expected, tmp_path, "before")
        source.grab.assert_not_called()
        source.close.assert_called_once()
    finally:
        journal.stream.close()


def test_late_cleanup_cannot_admit_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Even successful full RGB collection remains subject to phase cleanup time."""
    clock, source, guard, expected, journal = setup_reference(monkeypatch, tmp_path)
    source.close.side_effect = lambda: clock.__setitem__(0, 2.0)
    try:
        with pytest.raises(RuntimeError, match="cleanup exceeded"):
            smoke.reference(journal, guard, tmp_path, expected, tmp_path, "before")
        source.close.assert_called_once()
    finally:
        journal.stream.close()


def test_native_failure_survives_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No cleanup exception may replace the first failed native reference read."""
    _, source, guard, expected, journal = setup_reference(monkeypatch, tmp_path)
    failure = RuntimeError("guard changed")
    guard.check.side_effect = failure
    source.close.side_effect = RuntimeError("close failed")
    try:
        with pytest.raises(RuntimeError, match="guard changed") as caught:
            smoke.reference(journal, guard, tmp_path, expected, tmp_path, "before")
        assert caught.value is failure
        assert "close failed" in caught.value.__notes__[0]
        source.close.assert_called_once()
    finally:
        journal.stream.close()


def test_failed_cleanup_logging_does_not_skip_resource_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A full/broken evidence stream still cannot strand the retained MSS owner."""
    _, source, guard, expected, journal = setup_reference(monkeypatch, tmp_path)
    original = journal.emit

    def emit(event: str, name: str, error: str | None = None) -> None:
        """Break only the cleanup-start record, after a valid reference exists."""
        if name == "reference.MSS.close":
            raise OSError("log failed")
        original(event, name, error)

    monkeypatch.setattr(journal, "emit", emit)
    try:
        with pytest.raises(OSError, match="log failed"):
            smoke.reference(journal, guard, tmp_path, expected, tmp_path, "before")
        source.close.assert_called_once()
    finally:
        journal.stream.close()
