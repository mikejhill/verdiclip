"""Independent origin calibration never searches for a matching placement."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest
from scripts.panel_matrix_origin import MARKERS, ViewportOrigin, verify_calibration


@pytest.mark.parametrize("dpr", [1, 1.25])
def test_predicted_markers_and_shift_rejection(dpr: float) -> None:
    """All marker pixels are checked at the measured origin only."""
    root = [0, 0, 100, 100]
    origin = {"screen_client_rect": [4, 8, 100, 100]}
    state = {
        "dpr": dpr,
        "viewport": {"scroll": [0, 0], "visual_offset": [0, 0], "visual_scale": 1},
        "calibration": [[x, y, x + 8, y + 8] for x, y, *_ in MARKERS],
        "state": {"target": {"rect": [44.4, 55.6, 64.4, 75.6]}},
    }
    rgb = bytearray(100 * 100 * 3)
    for x, y, left, right in MARKERS:
        for row in range(int(8 * dpr)):
            for col in range(int(8 * dpr)):
                offset = ((8 + int(y * dpr) + row) * 100 + 4 + int(x * dpr) + col) * 3
                rgb[offset : offset + 3] = bytes(left if col < int(4 * dpr) else right)
    result = verify_calibration(bytes(rgb), root, origin, state)
    assert result["numeric_phase_qualified"] is False
    with pytest.raises(RuntimeError, match="pixels differ"):
        verify_calibration(
            bytes(rgb), root, {"screen_client_rect": [5, 8, 100, 100]}, state
        )
    rgb[(12 * 100 + 8) * 3] ^= 1
    if dpr == 1:
        with pytest.raises(RuntimeError, match="pixels differ"):
            verify_calibration(bytes(rgb), root, origin, state)


def test_native_call_has_two_guards_and_common_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-call guard failure prevents return consumption."""
    log = []
    reader = object.__new__(ViewportOrigin)
    reader.emit = lambda *values: log.append(values)
    monkeypatch.setattr(
        reader,
        "guard",
        SimpleNamespace(check=lambda: log.append("guard")),
        raising=False,
    )
    monkeypatch.setattr(
        reader,
        "api",
        SimpleNamespace(Check=lambda: log.append("native") or 7),
        raising=False,
    )
    assert reader.call("Check") == 7
    assert log == [
        ("call_start", "reference.origin.Check"),
        "guard",
        "native",
        "guard",
        ("call_end", "reference.origin.Check"),
    ]


@pytest.mark.parametrize("repeated", [False, True])
def test_traversal_counts_all_handles(
    monkeypatch: pytest.MonkeyPatch, *, repeated: bool
) -> None:
    """Noncandidate HWNDs cannot evade the fixed traversal bound."""
    reader = object.__new__(ViewportOrigin)

    def call(name: str, *args: object) -> int:
        if name == "GetWindow":
            hwnd, kind = args
            assert isinstance(hwnd, int)
            if kind == 5:
                return 1 if hwnd == 999 else 0
            return 1 if repeated else int(hwnd) + 1
        if name == "GetClassNameW":
            assert isinstance(args[1], ctypes.Array)
            args[1].value = "Other"
            return 5
        raise AssertionError(name)

    monkeypatch.setattr(reader, "call", call)
    with pytest.raises(RuntimeError, match="bound or repeated"):
        reader.observe({"root_hwnd": 999})


def test_post_native_guard_failure_is_not_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership loss after a native return prevents call-end admission."""
    reader = object.__new__(ViewportOrigin)
    checks = []
    events = []

    def check() -> None:
        checks.append(1)
        if len(checks) == 2:
            raise RuntimeError("ownership lost")

    monkeypatch.setattr(reader, "guard", SimpleNamespace(check=check), raising=False)
    monkeypatch.setattr(reader, "api", SimpleNamespace(Read=lambda: 7), raising=False)
    monkeypatch.setattr(
        reader, "emit", lambda *args: events.append(args), raising=False
    )
    with pytest.raises(RuntimeError, match="ownership lost"):
        reader.call("Read")
    assert events == [("call_start", "reference.origin.Read")]


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_render_widgets_reject(
    monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    """Neither missing nor multiple candidates can be selected by a guess."""
    reader = object.__new__(ViewportOrigin)

    def call(name: str, *args: object) -> int:
        if name == "GetWindow":
            hwnd, kind = args
            assert isinstance(hwnd, int)
            if kind == 5:
                return 1 if hwnd == 999 and count else 0
            return hwnd + 1 if hwnd < count else 0
        if name == "GetClassNameW":
            assert isinstance(args[1], ctypes.Array)
            args[1].value = "Chrome_RenderWidgetHostHWND"
            return 26
        if name == "IsWindowVisible":
            return 1
        raise AssertionError(name)

    monkeypatch.setattr(reader, "call", call)
    monkeypatch.setattr(reader, "inspect", lambda hwnd, _manifest: {"hwnd": hwnd})
    with pytest.raises(RuntimeError, match="exactly one"):
        reader.observe({"root_hwnd": 999})
