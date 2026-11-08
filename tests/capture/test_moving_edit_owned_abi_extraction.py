"""Exercise extracted retained EDIT ownership and ABI with native call fakes."""

from __future__ import annotations

import ctypes as c
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from tests.capture.test_moving_edit_native_extraction import Harness

from verdiclip.capture import edit_native as module
from verdiclip.capture.edit_native import EditObservation, ProbeBudget, RootReference
from verdiclip.capture.observe_native import Rect

# ruff: noqa: ANN401 -- exact ctypes outputs and retained resource fakes.


def read_only(harness: Harness) -> Any:
    """Supply dual-scrollbar native geometry to the original first-frame producer."""
    native = harness.native
    harness.style = 0x503008C4

    def window(_hwnd: int, output: Any) -> int:
        rect = c.cast(output, c.POINTER(Rect)).contents
        rect.left, rect.top, rect.right, rect.bottom = 60, 60, 570, 490
        return 1

    def bar(_hwnd: int, object_id: int, output: Any) -> int:
        rect = c.cast(output, c.POINTER(module.ScrollBarInfo)).contents.rect
        coordinates = (60, 473, 553, 490) if object_id == -6 else (553, 60, 570, 473)
        rect.left, rect.top, rect.right, rect.bottom = coordinates
        return 1

    def scroll(_hwnd: int, _axis: int, output: Any) -> int:
        info = c.cast(output, c.POINTER(module.ScrollInfo)).contents
        info.minimum, info.maximum, info.page = 0, 37, 25
        info.position, info.track = harness.first, 0
        return 1

    native.user.GetWindowRect = window
    native.user.GetScrollBarInfo = bar
    native.user.GetScrollInfo = scroll
    return native


def test_original_edit_snapshot_uses_actual_native_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All client edges and both native rails survive the unmodified base layer."""
    harness = Harness(monkeypatch)
    native = read_only(harness)
    first = EditObservation.snapshot(native)
    assert first["native"]["horizontal_bar"] == (60, 473, 553, 490)
    assert first["native"]["vertical_bar"] == (553, 60, 570, 473)
    assert first["scroll"][0][:5] == [0, 37, 25, 0, 0]
    native.baseline = first
    assert EditObservation.snapshot(native) == first
    harness.input_tick += 1
    with pytest.raises(ValueError, match="state changed"):
        EditObservation.snapshot(native)


@pytest.mark.parametrize(
    "fault",
    ["identity", "foreground", "dpi", "pid", "bar", "pose", "hit", "pattern"],
)
def test_original_edit_snapshot_does_not_weaken_guards(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Actual base predicates reject before unchanged-state admission."""
    harness = Harness(monkeypatch)
    native = read_only(harness)
    if fault == "identity":
        native.runtime_id = lambda _element: (99,)
    elif fault == "foreground":
        native.user.GetForegroundWindow = lambda: 101
    elif fault == "dpi":
        native.user.GetDpiForWindow = lambda _hwnd: 144
    elif fault == "pid":
        native.target = SimpleNamespace(hwnd=100, pid=98, point=(80, 80))
    elif fault == "bar":
        original = native.user.GetScrollBarInfo

        def invisible(hwnd: int, object_id: int, output: Any) -> int:
            result = original(hwnd, object_id, output)
            c.cast(output, c.POINTER(module.ScrollBarInfo)).contents.state[0] = 0x8000
            return int(result)

        native.user.GetScrollBarInfo = invisible
    elif fault == "pose":
        harness.first = 1
    elif fault == "hit":
        native.user.WindowFromPoint = lambda _point: 100
    else:
        native.pattern = SimpleNamespace(
            read=lambda _slot, kind: (
                kind(float("nan")) if kind is c.c_double else kind(0)
            )
        )
    with pytest.raises(ValueError, match=r"changed|top-left|scrollbar|capability"):
        EditObservation.snapshot(native)


def test_original_edit_no_pattern_remains_capability_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source's optional base capability is not confused with moving support."""
    native = read_only(Harness(monkeypatch))
    native.pattern = None
    assert EditObservation.snapshot(native)["scroll_pattern"] is None


def prepare_constructor(
    monkeypatch: pytest.MonkeyPatch, *, missing_process: bool = False
) -> tuple[Harness, Mock]:
    """Keep source initialization/ownership while replacing only OS interfaces."""
    harness = Harness(monkeypatch)
    template = read_only(harness)
    close = Mock(return_value=1)

    def apis(native: Any) -> None:
        for name in (
            "user",
            "_process_guard",
            "_association",
            "_same",
            "runtime_id",
            "structure",
            "_visible",
            "_window_long",
            "_client_rect",
            "_occlusion",
            "_monitors",
        ):
            setattr(native, name, getattr(template, name))
        native.kernel = SimpleNamespace(
            OpenProcess=lambda *_args: 0 if missing_process else 123,
            CloseHandle=close,
        )
        native.uia = "uia"
        native._interface = lambda _source, slot, _types, *args: (
            "walker"
            if slot == 16
            else "panel"
            if slot == 7
            else "root"
            if args == (100,)
            else "panel-copy"
        )
        native._pattern = lambda _panel: template.pattern

    monkeypatch.setattr(module.c, "WinError", OSError, raising=False)
    monkeypatch.setattr(module.c, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(EditObservation, "_apis", apis)
    monkeypatch.setattr(EditObservation, "_edit_apis", lambda _native: None)
    monkeypatch.setattr(EditObservation, "_dpi", lambda _native: None)
    monkeypatch.setattr(EditObservation, "_com_init", lambda _native: None)
    return harness, close


@pytest.mark.parametrize("kind", [EditObservation, RootReference])
def test_actual_owned_constructor_closes_process_once(
    monkeypatch: pytest.MonkeyPatch, kind: type[EditObservation]
) -> None:
    """Retained native constructor owns a successful process acquisition."""
    harness, close = prepare_constructor(monkeypatch)
    native = kind(harness.native.target, 101, harness.native.budget)
    assert native.baseline
    native.close()
    native.close()
    close.assert_called_once_with(123)


@pytest.mark.parametrize("fault", ["platform", "process", "association", "root"])
def test_actual_owned_constructor_cleans_failed_admission(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Each constructor failure preserves acquired ownership without a leak."""
    harness, close = prepare_constructor(
        monkeypatch, missing_process=fault == "process"
    )
    if fault == "platform":
        monkeypatch.setattr(module.sys, "platform", "linux")
    elif fault == "association":
        harness.native._same.return_value = False
    elif fault == "root":
        harness.native.root_baseline["offscreen"] = True
    with pytest.raises((ValueError, OSError)):
        EditObservation(harness.native.target, 101, harness.native.budget)
    if fault in ("platform", "process"):
        close.assert_not_called()
    else:
        close.assert_called_once_with(123)


def test_root_reference_constructor_failure_retains_process_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The independent retained reference cannot leak when its guard rejects."""
    harness, close = prepare_constructor(monkeypatch)
    harness.native.user.GetForegroundWindow = lambda: 101
    with pytest.raises(ValueError, match="foreground"):
        RootReference(harness.native.target, 101, harness.native.budget)
    close.assert_called_once_with(123)


@pytest.mark.parametrize("fault", ["dpi", "state"])
def test_root_reference_guards_remain_exact(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Source reference reads do not permit input or display rebaselining."""
    harness = Harness(monkeypatch)
    native = harness.native
    native.baseline = RootReference.snapshot(native)
    if fault == "dpi":
        native.user.GetDpiForWindow = lambda _hwnd: 144
    else:
        harness.input_tick += 1
    with pytest.raises(ValueError, match=r"DPI changed|state changed"):
        RootReference.snapshot(native)


@pytest.mark.parametrize("handles", [[101, 102], [101, 101], list(range(1000, 1257))])
def test_child_enumeration_enforces_intersection_cycle_and_bound(
    monkeypatch: pytest.MonkeyPatch, handles: list[int]
) -> None:
    """The actual callback reports errors only after enumeration unwinds."""
    native = Harness(monkeypatch).native
    native._intersects = lambda *_args: handles == [101, 102]
    native.user.IsWindowVisible = lambda _hwnd: 1
    native.window_callback = lambda callback: callback

    def enumerate_children(_root: int, callback: Any, _data: int) -> int:
        for hwnd in handles:
            if callback(hwnd, 0) == 0:
                return 0
        return 1

    native.user.EnumChildWindows = enumerate_children
    with pytest.raises(ValueError, match=r"overlay|bound/cycle"):
        EditObservation._child_windows(native, [60, 60, 553, 473])


def test_selected_child_and_nonintersecting_children_are_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal bounded enumeration uses source callback completion checks."""
    native = Harness(monkeypatch).native
    native.window_callback = lambda callback: callback
    native._intersects = lambda *_args: False
    native.user.EnumChildWindows = lambda _root, callback, _data: callback(101, 0)
    EditObservation._child_windows(native, [60, 60, 553, 473])


@pytest.mark.parametrize(("value", "error"), [(0, 0), (0, 5), (0x502008C4, 0)])
def test_window_style_zero_requires_clean_native_last_error(
    monkeypatch: pytest.MonkeyPatch, value: int, error: int
) -> None:
    """Zero ex-style is valid; a failed native zero cannot masquerade as one."""
    native = Harness(monkeypatch).native
    native.user.GetWindowLongPtrW = lambda *_args: value
    setter = Mock()
    monkeypatch.setattr(module.c, "set_last_error", setter, raising=False)
    monkeypatch.setattr(module.c, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(module.c, "WinError", OSError, raising=False)
    if error:
        with pytest.raises(OSError, match="5"):
            EditObservation._window_long(native, -20)
    else:
        assert EditObservation._window_long(native, -20) == value
    setter.assert_called_once_with(0)


@pytest.mark.parametrize("deadline", [True, 0.0, 30.001, float("nan"), float("inf")])
def test_probe_budget_rejects_invalid_original_deadline(
    monkeypatch: pytest.MonkeyPatch, deadline: float
) -> None:
    """A derived budget never resets or widens the fixed original allowance."""
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)
    with pytest.raises(ValueError, match="deadline"):
        ProbeBudget(deadline, lambda _event: None)
