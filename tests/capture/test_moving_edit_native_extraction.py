"""Fake-native adversarial tests; never load a Windows API or run the fixture."""

from __future__ import annotations

# ruff: noqa: ANN401 -- fake ctypes pointers and COM vtables are deliberately dynamic.
import ctypes as c
import json
import threading
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from verdiclip.capture import edit_scroll_native as module
from verdiclip.capture.edit_native import ProbeBudget
from verdiclip.capture.edit_scroll_contract import (
    FIXTURE_CONTRACT,
    FONT_ARGS,
    FONT_ATTESTATION_SCHEMA,
    FONT_METRIC_BYTE_FIELDS,
    FONT_METRIC_LONG_FIELDS,
    FONT_METRIC_WORD_FIELDS,
    TEXT,
    validate_font_attestation,
)
from verdiclip.capture.edit_scroll_native import (
    GuiThreadInfo,
    ScrollObservation,
)
from verdiclip.capture.observe_native import FileTime, LastInput, Point, Rect
from verdiclip.capture.observe_protocol import ObserveTarget

EPOCH = "1" * 32


def _attestation() -> dict[str, Any]:
    metrics = dict.fromkeys(
        FONT_METRIC_LONG_FIELDS + FONT_METRIC_WORD_FIELDS + FONT_METRIC_BYTE_FIELDS,
        0,
    )
    metrics.update(height=16, average_width=8, maximum_width=10, pitch_family=54)
    return {
        "schema": FONT_ATTESTATION_SCHEMA,
        "fixture_contract": FIXTURE_CONTRACT,
        "epoch": EPOCH,
        "pid": 99,
        "creation": 12345,
        "role": "target",
        "root": 100,
        "child": 101,
        "opaque_hfont": 888,
        "source_authored_createfont": list(FONT_ARGS),
        "owner_measured_face": "Courier New",
        "owner_measured_metrics": metrics,
    }


class Harness:
    """A pinned owned native target with exact SDK-compatible output buffers."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0915 - explicit frozen native fake setup
        self.now = 0.0
        monkeypatch.setattr(module.time, "monotonic", lambda: self.now)
        self.events: list[dict[str, Any]] = []
        self.first = 0
        self.position: int | None = None
        self.style = module.STYLE
        self.input_tick = 15
        self.margins = 0
        self.advance = 8
        self.opaque_hfont = 888
        self.text = TEXT
        self.selection = 0
        self.index_shift = 0
        self.point_shift = 0
        self.active = 100
        self.focus = 101
        self.caret = 101
        self.capture = 0
        self.menu_owner = 0
        self.move_size = 0
        self.flags = 0
        self.caret_rect = (0, 0, 1, 16)
        self.api_success = 1
        self.return_override: int | None = None
        self.send_duration = 0.0
        self.seek_duration = 0.0
        self.seek_status = 0
        self.sent: list[tuple[int, int, int, int, int, int]] = []
        self.writes: list[tuple[object, ...]] = []
        self.released: list[tuple[str, int]] = []
        self.native: Any = ScrollObservation.__new__(ScrollObservation)
        native = self.native
        native.unsafe_write_reason = None
        native._write_admission = None
        native._font_attestation_json = json.dumps(
            validate_font_attestation(
                _attestation(),
                epoch=EPOCH,
                pid=99,
                creation=12345,
                role="target",
                root=100,
                child=101,
            )
        )
        native.budget = ProbeBudget(30, self.events.append)
        native.edit_emit = self.events.append
        native._thread = threading.get_ident()
        native._closed = False
        native._owners = []
        native.target = ObserveTarget(99, 12345, 100, (80, 80))
        native.child = 101
        native.panel_id, native.root_id = (1, 2), (1,)
        native.panel, native.root, native.by_handle = "panel", "root", "panel-copy"
        native.panel_baseline = {
            "hwnd": 101,
            "pid": 99,
            "offscreen": False,
            "framework": "Win32",
            "rect": [60, 60, 570, 473],
        }
        native.root_baseline = {
            "hwnd": 100,
            "pid": 99,
            "offscreen": False,
            "framework": "Win32",
            "rect": [40, 40, 573, 701],
        }
        native._process_guard = Mock()
        native._association = Mock()
        native._same = Mock(return_value=True)
        native.runtime_id = lambda element: (1, 2) if element == "panel" else (1,)
        native.structure = lambda element: dict(
            native.panel_baseline if element == "panel" else native.root_baseline
        )
        native._visible = Mock(return_value=True)
        native._window_long = lambda index: self.style if index == -16 else 0
        native._client_rect = Mock(return_value=[40, 40, 573, 701])
        native._occlusion = Mock()
        native._monitors = Mock(return_value=[[1, 0, 0, 1024, 768, True]])
        native.user = SimpleNamespace(
            GetForegroundWindow=lambda: 100,
            GetDpiForWindow=lambda _hwnd: 96,
            GetWindowThreadProcessId=self.pid,
            GetParent=lambda _hwnd: 100,
            GetGUIThreadInfo=self.gui,
            SetFocus=Mock(side_effect=AssertionError("Forbidden helper SetFocus")),
            HideCaret=Mock(side_effect=AssertionError("Forbidden helper HideCaret")),
            GetClassNameW=self.class_name,
            GetClientRect=self.client_rect,
            GetWindowRect=self.window_rect,
            ClientToScreen=self.to_screen,
            WindowFromPoint=lambda _point: 101,
            GetScrollBarInfo=self.bar,
            GetScrollInfo=self.scroll,
            GetLastInputInfo=self.last_input,
            SendMessageTimeoutW=self.send,
            GetDC=Mock(side_effect=AssertionError("Forbidden helper GetDC")),
            ReleaseDC=Mock(side_effect=AssertionError("Forbidden helper ReleaseDC")),
        )
        native.gdi = SimpleNamespace(
            **{
                name: Mock(side_effect=AssertionError(f"Forbidden helper GDI: {name}"))
                for name in (
                    "GetObjectW",
                    "SelectObject",
                    "GetTextFaceW",
                    "GetTextMetricsW",
                )
            }
        )
        native.pattern = SimpleNamespace(read=self.pattern_read, call=self.pattern_call)

    def pid(self, _hwnd: int, output: Any) -> int:
        """Write exact PID, return owning thread ID."""
        c.cast(output, c.POINTER(c.c_uint32)).contents.value = 99
        return 3

    def gui(self, _thread: int, output: Any) -> int:
        """Keep child focus and its owner-hidden caret, with no capture."""
        info = c.cast(output, c.POINTER(GuiThreadInfo)).contents
        for field in (
            "active",
            "focus",
            "caret",
            "capture",
            "menu_owner",
            "move_size",
            "flags",
        ):
            setattr(info, field, getattr(self, field))
        (
            info.caret_rect.left,
            info.caret_rect.top,
            info.caret_rect.right,
            info.caret_rect.bottom,
        ) = self.caret_rect
        return 1

    def class_name(self, _hwnd: int, output: Any, _length: int) -> int:
        """Return un-subclassed standard class name."""
        output.value = "Edit"
        return 4

    def client_rect(self, _hwnd: int, output: Any) -> int:
        """Expose fixed client bounds."""
        rect = c.cast(output, c.POINTER(Rect)).contents
        rect.left, rect.top, rect.right, rect.bottom = module.CLIENT
        return 1

    def window_rect(self, _hwnd: int, output: Any) -> int:
        """Vertical rail is the only nonclient strip."""
        rect = c.cast(output, c.POINTER(Rect)).contents
        rect.left, rect.top, rect.right, rect.bottom = 60, 60, 570, 473
        return 1

    def to_screen(self, _hwnd: int, output: Any) -> int:
        """Map exact local client coordinates."""
        point = c.cast(output, c.POINTER(Point)).contents
        point.x += 60
        point.y += 60
        return 1

    def bar(self, hwnd: int, object_id: int, output: Any) -> int:
        """Return vertical bar geometry and changing thumb only."""
        assert (hwnd, object_id) == (101, -5)
        bar = c.cast(output, c.POINTER(module.ScrollBarInfo)).contents
        bar.rect.left, bar.rect.top, bar.rect.right, bar.rect.bottom = 553, 60, 570, 473
        bar.line, bar.top, bar.bottom = 17, 17 + self.first, 285 + self.first
        return 1

    def scroll(self, hwnd: int, axis: int, output: Any) -> int:
        """Read SIF_ALL, never an inferred range or percentage endpoint."""
        assert (hwnd, axis) == (101, 1)
        info = c.cast(output, c.POINTER(module.ScrollInfo)).contents
        assert (info.size, info.mask) == (28, 0x17)
        info.minimum, info.maximum, info.page = 0, 37, 25
        info.position = self.first if self.position is None else self.position
        info.track = 0
        return 1

    def last_input(self, output: Any) -> int:
        """Observe one frozen input epoch."""
        c.cast(output, c.POINTER(LastInput)).contents.tick = self.input_tick
        return 1

    def send(  # noqa: PLR0913, PLR0917 - exact fixed SendMessageTimeoutW ABI
        self,
        hwnd: int,
        message: int,
        wp: int,
        lp: int,
        flags: int,
        timeout: int,
        output: Any,
    ) -> int:
        """Dispatch only the frozen read-only scalar/text messages in tests."""
        self.sent.append((hwnd, message, wp, lp, flags, timeout))
        values = {
            0x0E: len(self.text),
            0x31: self.opaque_hfont,
            0xB0: self.selection,
            0xBA: 38,
            0xCE: self.first,
            0xD4: self.margins,
        }
        if message == 0x0D:
            data = self.text.encode("utf-16-le") + b"\0\0"
            c.memmove(lp, data, min(len(data), wp * 2))
            value = len(self.text)
        elif message == 0xBB:
            value = wp * 45 + self.index_shift
        elif message == 0xD6:
            line, column = divmod(wp, 45)
            x, y = column * self.advance, (line - self.first) * 16 + self.point_shift
            value = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        else:
            value = values[message]
        if self.return_override is not None:
            value = self.return_override
        c.cast(output, c.POINTER(c.c_size_t)).contents.value = value
        self.now += self.send_duration
        return self.api_success

    def pattern_read(self, slot: int, kind: Any) -> Any:
        """Match fixed vertical provider view and measured current position."""
        return kind(
            {5: 0.0, 6: 100 * self.first / 13, 7: 100, 8: 100 * 25 / 38, 9: 0, 10: 1}[
                slot
            ]
        )

    def pattern_call(self, slot: int, types: tuple[object, ...], *args: Any) -> int:
        """Exercise real raw-call admission and its synchronous postchecks."""
        assert slot == 4
        assert types == (c.c_double, c.c_double)

        def dispatch() -> int:
            self.writes.append(args)
            self.now += self.seek_duration
            self.first = round(float(args[1]) * 13 / 100)
            return self.seek_status

        return int(self.native.raw(dispatch))


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    """Supply independent all-fake handles and clock for every test."""
    return Harness(monkeypatch)


def test_sdk_abi_is_explicit() -> None:
    """Linux wchar/long sizes cannot silently change the Windows ABI."""
    assert c.sizeof(GuiThreadInfo) == 72
    assert c.sizeof(module.ScrollInfo) == 28
    assert c.sizeof(module.ScrollBarInfo) == 60
    assert c.sizeof(c.c_size_t) == c.sizeof(c.c_void_p)


def test_snapshot_measures_vertical_motion_without_rebaseline(harness: Harness) -> None:
    """Real adapter verifies shared points at each fixed native pose."""
    native = harness.native
    frames = []
    for first in (0, 3, 0, 8, 13, 13, 0, 3):
        harness.first = first
        frames.append(native.snapshot())
    assert [frame["text_layout"]["line13_y"] for frame in frames] == [
        208,
        160,
        208,
        80,
        0,
        0,
        208,
        160,
    ]
    assert (
        frames[0]["font"]["attestation"]["owner_measured_metrics"]["average_width"] == 8
    )
    assert (
        frames[0]["font"]["attestation"]["owner_measured_metrics"]["maximum_width"]
        == 10
    )
    assert frames[1] == frames[-1]
    assert frames[4] == frames[5]
    assert native.unsafe_write_reason is None
    harness.native.user.GetDC.assert_not_called()
    harness.native.user.ReleaseDC.assert_not_called()
    for function in vars(harness.native.gdi).values():
        function.assert_not_called()
    assert all(message[0] == 101 and message[4] == 0x23 for message in harness.sent)


def test_snapshot_is_detached_from_frozen_guard(harness: Harness) -> None:
    """Mutating a returned JSON snapshot cannot mutate the private baseline."""
    initial = harness.native.snapshot()
    initial["native"]["client_screen"][0] = 999
    assert harness.native.snapshot()["native"]["client_screen"] == [60, 60, 553, 473]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("style", 0x503008C4),
        ("style", 0x501008C4),  # Prior erroneous horizontal-only request.
        ("input_tick", 16),
        ("margins", 1),
        ("opaque_hfont", 889),
        ("advance", 0),
        ("selection", 1),
        ("focus", 100),
        ("caret", 0),
        ("text", "X" + TEXT[1:]),
        ("index_shift", 1),
        ("point_shift", 1),
        ("advance", 12),
    ],
)
def test_guard_drift_prohibits_all_writes(
    harness: Harness, field: str, value: object
) -> None:
    """Every frozen input/layout/font/text violation is terminal."""
    harness.native.snapshot()
    setattr(harness, field, value)
    with pytest.raises((ValueError, RuntimeError)):
        harness.native.snapshot()
    assert harness.native.unsafe_write_reason is not None
    with pytest.raises(RuntimeError, match="prohibited"):
        harness.native.seek(0, restoring=True, admit_write=lambda: None)
    assert harness.writes == []


@pytest.mark.parametrize("after_baseline", [False, True])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active", 0),
        ("active", 101),
        ("active", 999),
        ("focus", 0),
        ("focus", 100),
        ("focus", 999),
        ("caret", 0),
        ("caret", 100),
        ("caret", 999),
        ("capture", 100),
        ("capture", 101),
        ("capture", 999),
        ("menu_owner", 100),
        ("menu_owner", 101),
        ("menu_owner", 999),
        ("move_size", 100),
        ("move_size", 101),
        ("move_size", 999),
        ("flags", 1),
        ("flags", 2),
        ("flags", 4),
        ("flags", 8),
        ("flags", 16),
        ("flags", 0xFFFFFFFF),
    ],
)
def test_exact_focused_hidden_caret_guard_rejects_before_write(
    harness: Harness, field: str, value: int, *, after_baseline: bool
) -> None:
    """No initial alternative or later GUI drift can admit a seek or restore."""
    native = harness.native
    if after_baseline:
        native.snapshot()
    setattr(harness, field, value)
    gui = Mock(wraps=harness.gui)
    native.user.GetGUIThreadInfo = gui
    later_class = Mock(wraps=harness.class_name)
    native.user.GetClassNameW = later_class
    native._monitors.reset_mock()
    admission = Mock()
    predicate = Mock(wraps=native._reject)
    native._reject = predicate
    with pytest.raises(ValueError, match="Focused EDIT/hidden-caret/input") as caught:
        native.seek(100, admit_write=admission)
    error = caught.value
    assert native.unsafe_write_reason == str(error)
    assert native.budget.failure is error
    gui.assert_called_once()
    later_class.assert_not_called()
    native._monitors.assert_not_called()
    admission.assert_not_called()
    assert harness.writes == []
    assert hasattr(native, "_fixed_baseline") is after_baseline
    module.retain_native_diagnostic(error)
    diagnostic = cast("Any", error).native_diagnostic
    assert diagnostic["first_failed_predicate"] == f"gui.{field}"
    assert diagnostic["current"]["gui"]["value"][field] == (value or None)
    gui_order = [
        "active",
        "focus",
        "caret",
        "capture",
        "menu_owner",
        "move_size",
        "flags",
    ]
    assert [
        call.args[0]
        for call in predicate.call_args_list
        if call.args[0].startswith("gui.")
    ] == [f"gui.{name}" for name in gui_order[: gui_order.index(field) + 1]]
    assert diagnostic["current"]["class_name"] == {"status": "unattempted"}
    calls_before_restore = len(harness.events)
    for restoring in (False, True):
        with pytest.raises(RuntimeError, match="prohibited"):
            native.seek(0, restoring=restoring, admit_write=admission)
    assert len(harness.events) == calls_before_restore
    assert harness.writes == []
    admission.assert_not_called()
    native.user.SetFocus.assert_not_called()
    native.user.HideCaret.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [("active", 101), ("focus", 100), ("caret", 0), ("flags", 1)],
)
def test_postseek_gui_drift_latches_without_repair_or_restore(
    harness: Harness, field: str, value: int
) -> None:
    """A successful write never authorizes a changed focus or visible caret."""
    native = harness.native
    baseline = native.snapshot()
    original = harness.pattern_call

    def changed_after_seek(*args: Any) -> int:
        result = original(*args)
        setattr(harness, field, value)
        return result

    native.pattern.call = changed_after_seek
    native.seek(100 * 3 / 13, admit_write=lambda: None)
    assert harness.writes == [(-1.0, 100 * 3 / 13)]
    with pytest.raises(ValueError, match="Focused EDIT/hidden-caret/input") as caught:
        native.snapshot()
    calls_after_failure = len(harness.events)
    with pytest.raises(RuntimeError, match="prohibited"):
        native.seek(0, restoring=True, admit_write=lambda: None)
    assert len(harness.events) == calls_after_failure
    assert harness.writes == [(-1.0, 100 * 3 / 13)]
    assert native._last_native_snapshot == baseline
    assert native.budget.failure is caught.value
    native.user.SetFocus.assert_not_called()
    native.user.HideCaret.assert_not_called()


def test_caret_rectangle_is_passive_diagnostic_not_frozen_geometry(
    harness: Harness,
) -> None:
    """Already-read rcCaret may move; no caret pixel or coordinate is normalized."""
    native = harness.native
    gui = Mock(wraps=harness.gui)
    native.user.GetGUIThreadInfo = gui
    baseline = native.snapshot()
    harness.caret_rect = (-32, -208, 1, 16)
    assert native.snapshot() == baseline
    assert gui.call_count == 2
    assert native._diagnostic_current["gui"]["value"]["caret_rect"] == (
        -32,
        -208,
        1,
        16,
    )
    harness.flags = 1
    with pytest.raises(ValueError, match="Focused EDIT/hidden-caret/input") as caught:
        native.snapshot()
    assert gui.call_count == 3
    harness.caret_rect = (0, 0, 0, 0)
    module.retain_native_diagnostic(caught.value)
    diagnostic = cast("Any", caught.value).native_diagnostic
    assert diagnostic["current"]["gui"]["value"] == {
        "active": 100,
        "focus": 101,
        "caret": 101,
        "capture": None,
        "menu_owner": None,
        "move_size": None,
        "flags": 1,
        "caret_rect": [-32, -208, 1, 16],
    }
    assert gui.call_count == 3
    native.user.SetFocus.assert_not_called()
    native.user.HideCaret.assert_not_called()


def test_failed_gui_query_has_no_fabricated_caret_diagnostic(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed GUI read does not turn its untrusted output into an observation."""
    native = harness.native
    monkeypatch.setattr(c, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        c, "WinError", lambda code: OSError(code, "GUI query failed"), raising=False
    )
    native.user.GetGUIThreadInfo = Mock(return_value=0)
    with pytest.raises(OSError, match="GUI query failed") as caught:
        native.snapshot()
    module.retain_native_diagnostic(caught.value)
    diagnostic = cast("Any", caught.value).native_diagnostic
    assert diagnostic["current"]["gui"] == {"status": "attempted-no-output"}
    assert diagnostic["current"]["class_name"] == {"status": "unattempted"}
    native.user.GetGUIThreadInfo.assert_called_once()
    assert native.unsafe_write_reason is not None
    assert harness.writes == []


def test_compare_elements_guard_is_not_runtimeid_only(harness: Harness) -> None:
    """Same RuntimeId values cannot replace retained COM CompareElements."""
    harness.native._same = Mock(return_value=False)
    with pytest.raises(ValueError, match="identity"):
        harness.native.snapshot()
    assert harness.native.unsafe_write_reason is not None


def test_fully_read_state_mismatch_does_not_latch_uncertainty(harness: Harness) -> None:
    """Known line/scroll disagreement is distinct from identity or timeout loss."""
    harness.position = 3
    with pytest.raises(ValueError, match="line/position/percentage"):
        harness.native.snapshot()
    assert harness.native.unsafe_write_reason is None
    assert harness.native.budget.failure is None


@pytest.mark.parametrize(
    "value", [0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0x00008000, 0x80000000, 0x0000FFFF]
)
def test_character_failure_and_signed16_overflow_rejected(
    harness: Harness, value: int
) -> None:
    """-1 sentinel and negative/overflow packed points are never fitted."""
    harness.return_override = value
    with pytest.raises(ValueError, match=r"position|coordinate"):
        harness.native._point(585)


@pytest.mark.parametrize("value", [0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 586])
def test_lineindex_negative_and_unexpected_index_rejected(
    harness: Harness, value: int
) -> None:
    """Pointer-width signed -1 and low-DWORD -1 cannot become character indexes."""
    harness.return_override = value
    with pytest.raises(ValueError, match="line index"):
        harness.native._line_index(13)


def test_message_uses_pointer_width_output_not_success(harness: Harness) -> None:
    """API success BOOL/LRESULT and message DWORD_PTR remain separate."""
    harness.return_override = 0x1234567800010002
    assert harness.native._message(0xD6, 585) == 0x1234567800010002
    assert harness.native._point(585) == (2, 1)


def test_message_timeout_is_tighter_and_rounded_down_after_logging(
    harness: Harness,
) -> None:
    """Logging consumes the active subphase instead of extending the syscall."""
    harness.native.budget.deadline = 1.2349

    def emit(event: dict[str, object]) -> None:
        if event["event"] == "call_start":
            harness.now += 0.2

    harness.native.edit_emit = emit
    harness.native._message(0xCE)
    assert harness.sent[-1][-1] == 1034


def test_zero_whole_milliseconds_rejects_without_sending(harness: Harness) -> None:
    """A positive submillisecond budget is not rounded up."""
    harness.native.budget.deadline = 0.0009
    with pytest.raises(RuntimeError, match="millisecond"):
        harness.native._message(0xCE)
    assert harness.sent == []
    assert harness.native.unsafe_write_reason is not None


def test_unknown_read_timeout_latches(harness: Harness) -> None:
    """Uncertain native reads prohibit later restoration too."""
    harness.api_success = 0
    with pytest.raises(RuntimeError, match="timeout"):
        harness.native._message(0xCE)
    assert harness.native.unsafe_write_reason is not None


@pytest.mark.parametrize("duration", [3.0, 3.001])
def test_successful_late_message_is_not_admitted(
    harness: Harness, duration: float
) -> None:
    """Successful API return does not override elapsed per-call bounds."""
    harness.send_duration = duration
    with pytest.raises(RuntimeError, match=">=3s"):
        harness.native._message(0xCE)
    assert harness.native.unsafe_write_reason is not None


def test_no_generic_write_message_dispatch(harness: Harness) -> None:
    """Even the private fixed dispatcher excludes EM_LINESCROLL/WM_SETTEXT."""
    for message in (0xB6, 0x0C, 0x30, 0xD3, 0x114, 0x115):
        with pytest.raises(ValueError, match="read-only"):
            harness.native._message(message)
    assert harness.sent == []


def test_seek_only_retained_slot_and_no_horizontal_change(harness: Harness) -> None:
    """Admission runs immediately before the single retained COM dispatch."""
    admitted = []

    def admit() -> None:
        assert harness.writes == []
        assert harness.events[-1]["event"] == "call_start"
        admitted.append(True)

    harness.native.seek(100 * 8 / 13, admit_write=admit)
    assert admitted == [True]
    assert harness.writes == [(-1.0, 100 * 8 / 13)]
    assert harness.native.unsafe_write_reason is None
    assert harness.native.snapshot()["text_layout"]["first_visible_line"] == 8


@pytest.mark.parametrize("value", [True, -1, 1, 50, float("nan"), float("inf")])
def test_seek_rejects_arbitrary_percentages(harness: Harness, value: float) -> None:
    """No search, fallback or provider-specific tuning is accepted."""
    with pytest.raises(ValueError, match="frozen seek"):
        harness.native.seek(value, admit_write=lambda: None)
    assert harness.writes == []


@pytest.mark.parametrize(
    "failure",
    [
        "guard",
        "seek_start",
        "call_start",
        "admit",
        "call_end",
        "seek_end",
        "hresult",
        "slow",
    ],
)
def test_seek_failure_permanently_suppresses_restore(
    harness: Harness, failure: str
) -> None:
    """Guard/log/callback/native uncertainties never permit another write."""
    native = harness.native
    if failure == "guard":
        harness.input_tick = 16
        native._fixed_baseline = {"wrong": True}
    if failure == "hresult":
        harness.seek_status = -2147467259
    if failure == "slow":
        harness.seek_duration = 3.0

    def emit(event: dict[str, Any]) -> None:
        harness.events.append(event)
        if event["event"] == failure and (
            failure != "call_start" or native._write_admission is not None
        ):
            raise OSError("flushed log failure")

    def admit() -> None:
        if failure == "admit":
            raise ValueError("reserve exhausted")

    native.edit_emit = emit
    with pytest.raises((ValueError, RuntimeError, OSError)):
        native.seek(100, admit_write=admit)
    prior_count = len(harness.writes)
    assert native.unsafe_write_reason is not None
    with pytest.raises(RuntimeError, match="prohibited"):
        native.seek(100 * 3 / 13, restoring=True, admit_write=lambda: None)
    assert len(harness.writes) == prior_count


def test_guard_logging_expiring_before_dispatch_never_writes(harness: Harness) -> None:
    """Late logging fails before admission can extend guard to call deadline."""
    native = harness.native
    native.budget.deadline = 2
    admitted = Mock()

    def emit(event: dict[str, Any]) -> None:
        if event["event"] == "seek_start":
            harness.now = 2

    native.edit_emit = emit
    with pytest.raises(RuntimeError, match="deadline"):
        native.seek(100, admit_write=admitted)
    admitted.assert_not_called()
    assert harness.writes == []


def test_late_native_output_still_owned_before_postcheck(harness: Harness) -> None:
    """Resource acquisition return survives post-log failure until ownership."""
    native = harness.native

    def emit(event: dict[str, Any]) -> None:
        if event["event"] == "call_end":
            raise OSError("postlog")

    native.edit_emit = emit
    value = native.raw(lambda: 345)
    released = Mock()
    native.own(lambda: released(value))
    with pytest.raises(OSError, match="postlog"):
        native.budget.check()
    native.close()
    released.assert_called_once_with(345)


def test_cleanup_attempts_all_and_preserves_primary(harness: Harness) -> None:
    """Cleanup failure suppresses writes without abandoning other owners."""
    native = harness.native
    calls = []
    native.own(lambda: calls.append("first"))

    def bad() -> None:
        calls.append("bad")
        raise OSError("release failed")

    native.own(bad)
    native.own(lambda: calls.append("last"))
    primary = ValueError("original")
    native.close(primary)
    assert calls == ["last", "bad", "first"]
    assert native.unsafe_write_reason == "release failed"
    assert "cleanup failed" in primary.__notes__[0]


def test_read_timeout_also_reserves_time_consumed_by_call_logging(
    harness: Harness,
) -> None:
    """Per-call cap is measured from call_start, even with abundant phase time."""

    def emit(event: dict[str, Any]) -> None:
        if event["event"] == "call_start":
            harness.now += 1.25

    harness.native.edit_emit = emit
    harness.native._message(0xCE)
    assert harness.sent[-1][-1] == 1750


def test_read_logging_failure_latches_uncertainty(harness: Harness) -> None:
    """Post-read diagnostic callback failure prohibits later writes."""

    def emit(event: dict[str, Any]) -> None:
        if event["event"] == "edit_read":
            raise OSError("diagnostic failed")

    harness.native.edit_emit = emit
    with pytest.raises(OSError, match="diagnostic failed"):
        harness.native._message(0xCE)
    assert harness.native.unsafe_write_reason is not None


def test_restore_can_correct_fully_read_state_mismatch(harness: Harness) -> None:
    """Deterministic disagreement does not masquerade as native uncertainty."""
    harness.native.snapshot()
    harness.position = 3
    with pytest.raises(module.NativeStateMismatchError, match="disagreement"):
        harness.native.snapshot()
    harness.native.seek(100 * 3 / 13, restoring=True, admit_write=lambda: None)
    assert harness.native.snapshot()["text_layout"]["first_visible_line"] == 3
    assert harness.native.unsafe_write_reason is None


def test_subphase_deadline_after_success_is_synchronous(harness: Harness) -> None:
    """A completed call past the tighter cap fails before another native call."""
    harness.native.budget.deadline = 1.0
    harness.send_duration = 1.0
    with pytest.raises(RuntimeError, match="deadline"):
        harness.native._message(0xCE)
    assert len(harness.sent) == 1
    with pytest.raises(RuntimeError, match="deadline"):
        harness.native._message(0xCE)
    assert len(harness.sent) == 1


def test_clock_regression_suppresses_write(harness: Harness) -> None:
    """Clock rollback is terminal even when deadline has not expired."""
    harness.native.budget.last = 1.0
    with pytest.raises(RuntimeError, match="regressed"):
        harness.native.seek(0, admit_write=lambda: None)
    assert harness.writes == []
    assert harness.native.unsafe_write_reason is not None


def test_actual_apis_bind_pointer_width_message_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise declared ABI setup without loading any native library."""
    native: Any = ScrollObservation.__new__(ScrollObservation)
    names = (
        "GetClassNameW",
        "GetWindowLongPtrW",
        "GetParent",
        "GetScrollInfo",
        "GetScrollBarInfo",
        "SendMessageTimeoutW",
        "GetGUIThreadInfo",
    )
    native.user = SimpleNamespace(**{name: Mock() for name in names})
    load = Mock(side_effect=AssertionError("Helper must not load a GDI library"))
    monkeypatch.setattr(c, "WinDLL", load, raising=False)
    native._edit_apis()
    load.assert_not_called()
    function = native.user.SendMessageTimeoutW
    assert function.restype is c.c_ssize_t
    assert function.argtypes == [
        c.c_void_p,
        c.c_uint32,
        c.c_size_t,
        c.c_ssize_t,
        c.c_uint32,
        c.c_uint32,
        c.POINTER(c.c_size_t),
    ]


def test_snapshot_serialization_deadline_also_latches(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful native reads cannot hide a late detached-state conversion."""
    loads = module.json.loads

    def slow_loads(value: str) -> Any:
        result = loads(value)
        harness.now = 30.0
        return result

    monkeypatch.setattr(module.json, "loads", slow_loads)
    with pytest.raises(RuntimeError, match="deadline"):
        harness.native.snapshot()
    assert harness.native.unsafe_write_reason is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("epoch", "2" * 32),
        ("pid", 100),
        ("creation", 12346),
        ("role", "oracle"),
        ("root", 102),
        ("child", 103),
        ("schema", "old"),
        ("fixture_contract", "unknown"),
        ("fixture_contract", "builtin-edit-owned-scroll-v1"),
        ("fixture_contract", "builtin-edit-owned-scroll-hpercent-positive-zero-v2"),
        ("owner_measured_face", "Arial"),
        ("opaque_hfont", 0),
    ],
)
def test_constructor_rejects_invalid_owner_before_native_startup(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Invalid and stale attestation cannot acquire resources or issue writes."""
    startup = Mock()
    monkeypatch.setattr(module.EditObservation, "__init__", startup)
    attestation = _attestation()
    attestation[field] = value
    with pytest.raises(ValueError, match=r"[Ff]ont"):
        ScrollObservation(
            harness.native.target,
            101,
            harness.native.budget,
            font_attestation=attestation,
            epoch=EPOCH,
        )
    startup.assert_not_called()
    assert harness.sent == []
    assert harness.writes == []


def test_constructor_freezes_owner_evidence_before_base_startup(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a detached immutable canonical copy survives into native startup."""
    startup = Mock()
    monkeypatch.setattr(module.EditObservation, "__init__", startup)
    attestation = _attestation()
    native = ScrollObservation(
        harness.native.target,
        101,
        harness.native.budget,
        font_attestation=attestation,
        epoch=EPOCH,
    )
    startup.assert_called_once_with(harness.native.target, 101, harness.native.budget)
    attestation["opaque_hfont"] = 999
    attestation["owner_measured_metrics"]["average_width"] = 10
    retained = json.loads(native._font_attestation_json)
    assert retained["opaque_hfont"] == 888
    assert retained["owner_measured_metrics"]["average_width"] == 8


def test_changed_opaque_font_token_forbids_restore_and_all_gdi(
    harness: Harness,
) -> None:
    """Continuity is an opaque equality check, not foreign GDI ownership."""
    harness.native.snapshot()
    harness.opaque_hfont = 889
    with pytest.raises(ValueError, match="opaque WM_GETFONT"):
        harness.native.seek(100, admit_write=lambda: None)
    with pytest.raises(RuntimeError, match="prohibited"):
        harness.native.seek(100 * 3 / 13, restoring=True, admit_write=lambda: None)
    assert harness.writes == []
    assert harness.native.unsafe_write_reason is not None
    for function in vars(harness.native.gdi).values():
        function.assert_not_called()


def test_constructor_creation_guard_rejects_self_consistent_stale_attestation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retained process query independently rejects an old reported creation."""

    def times(_handle: int, created: Any, *_remaining: Any) -> int:
        output = c.cast(created, c.POINTER(FileTime)).contents
        output.low, output.high = 12346, 0  # Receipt/target both report 12345.
        return 1

    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=444),
        CloseHandle=Mock(return_value=1),
        WaitForSingleObject=Mock(return_value=258),
        GetProcessTimes=times,
    )
    harness.native.user.GetAncestor = Mock(return_value=100)

    def apis(native: Any) -> None:
        native.kernel = kernel
        native.user = harness.native.user

    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(ScrollObservation, "_apis", apis)
    monkeypatch.setattr(ScrollObservation, "_edit_apis", Mock())
    monkeypatch.setattr(ScrollObservation, "_dpi", Mock())
    com = Mock(side_effect=AssertionError("Must reject before COM startup"))
    monkeypatch.setattr(ScrollObservation, "_com_init", com)
    native = ScrollObservation.__new__(ScrollObservation)
    with pytest.raises(ValueError, match="identity changed"):
        native.__init__(
            harness.native.target,
            101,
            harness.native.budget,
            font_attestation=_attestation(),
            epoch=EPOCH,
        )
    kernel.CloseHandle.assert_called_once_with(444)
    com.assert_not_called()
    assert native._closed is True
    assert harness.sent == []
    with pytest.raises(ValueError, match="identity changed"):
        native.seek(100, admit_write=lambda: None)
    assert harness.writes == []


def _override_pattern(
    harness: Harness, values: dict[int, float | int], *, fail_slot: int | None = None
) -> list[int]:
    reads: list[int] = []

    def read(slot: int, kind: Any) -> Any:
        reads.append(slot)
        if slot == fail_slot:
            raise OSError("Pattern getter failed")
        return (
            kind(values[slot]) if slot in values else harness.pattern_read(slot, kind)
        )

    harness.native.pattern.read = read
    return reads


def _capability_error(harness: Harness) -> Any:
    with pytest.raises(
        ValueError, match="Vertical-only ScrollPattern capabilities changed"
    ) as caught:
        harness.native.snapshot()
    return caught.value


@pytest.mark.parametrize(
    ("values", "expected", "checked"),
    [
        (
            {5: float("nan"), 6: float("inf"), 7: 0.0},
            "pattern.finite.horizontal_percent",
            ["finite.horizontal_percent"],
        ),
        (
            {6: float("inf"), 7: 0.0},
            "pattern.finite.vertical_percent",
            ["finite.horizontal_percent", "finite.vertical_percent"],
        ),
        (
            {7: float("-inf"), 8: 0.0},
            "pattern.finite.horizontal_view",
            [
                "finite.horizontal_percent",
                "finite.vertical_percent",
                "finite.horizontal_view",
            ],
        ),
        (
            {8: float("nan"), 9: 1},
            "pattern.finite.vertical_view",
            [
                "finite.horizontal_percent",
                "finite.vertical_percent",
                "finite.horizontal_view",
                "finite.vertical_view",
            ],
        ),
        (
            {5: -1.0, 7: 0.0, 8: 0.0, 9: 1},
            "pattern.horizontal_percent",
            ["horizontal_percent"],
        ),
        (
            {7: 0.0, 8: 0.0, 9: 1},
            "pattern.horizontal_view",
            ["horizontal_percent", "horizontal_view"],
        ),
        (
            {8: 0.0, 9: 1},
            "pattern.vertical_view",
            ["horizontal_percent", "horizontal_view", "vertical_view"],
        ),
        (
            {9: 1, 10: 0},
            "pattern.scrollable_axes",
            [
                "horizontal_percent",
                "horizontal_view",
                "vertical_view",
                "scrollable_axes",
            ],
        ),
    ],
)
def test_capability_predicate_keeps_exact_short_circuit_order(
    harness: Harness, values: dict[int, float | int], expected: str, checked: list[str]
) -> None:
    """All six getters precede the original ordered finite and axis predicates."""
    reads = _override_pattern(harness, values)
    original = harness.native._reject
    checks: list[str] = []

    def reject(name: str, failed: Any) -> Any:
        if name.startswith("pattern.") and name != "pattern.required":
            checks.append(name.removeprefix("pattern."))
        return original(name, failed)

    harness.native._reject = reject
    error = _capability_error(harness)
    expected_checks = (
        checked
        if checked[0].startswith("finite.")
        else [f"finite.{name}" for name in module._PATTERN_NAMES[:4]] + checked
    )
    assert checks == expected_checks
    assert reads == [5, 6, 7, 8, 9, 10]
    module.retain_native_diagnostic(error)
    diagnostic = error.native_diagnostic
    assert diagnostic["first_failed_predicate"] == expected
    assert diagnostic["current"]["input_tick"]["status"] == "unattempted"
    assert diagnostic["current"]["monitors"]["status"] == "unattempted"
    json.dumps(diagnostic, allow_nan=False)


def test_nonfinite_double_evidence_is_explicit_text_not_json_nan(
    harness: Harness,
) -> None:
    """Every already-read NaN/infinity retains its actual float representation."""
    _override_pattern(
        harness, {5: float("nan"), 6: float("inf"), 7: float("-inf"), 8: -0.0}
    )
    error = _capability_error(harness)
    module.retain_native_diagnostic(error)
    current = error.native_diagnostic["current"]
    for name, text in zip(
        module._PATTERN_NAMES[:3], ("nan", "inf", "-inf"), strict=True
    ):
        value = current[f"pattern.{name}"]["value"]
        assert value == {"type": "float", "repr": text, "hex": text, "nonfinite": text}
    assert current["pattern.vertical_view"]["value"] == {
        "type": "float",
        "repr": "-0.0",
        "hex": "-0x0.0p+0",
    }
    assert "NaN" not in json.dumps(error.native_diagnostic, allow_nan=False)


def test_partial_pattern_read_distinguishes_unattempted_from_no_output(
    harness: Harness,
) -> None:
    """A failing getter is never logged as zero, and later getters stay untouched."""
    reads = _override_pattern(harness, {}, fail_slot=7)
    with pytest.raises(OSError, match="Pattern getter failed") as caught:
        harness.native.snapshot()
    module.retain_native_diagnostic(caught.value)
    current = cast("Any", caught.value).native_diagnostic["current"]
    assert reads == [5, 6, 7]
    assert current["pattern.horizontal_percent"]["status"] == "observed"
    assert current["pattern.vertical_percent"]["status"] == "observed"
    assert current["pattern.horizontal_view"] == {"status": "attempted-no-output"}
    assert all(
        current[f"pattern.{name}"] == {"status": "unattempted"}
        for name in module._PATTERN_NAMES[3:]
    )
    assert cast("Any", caught.value).native_diagnostic["first_failed_predicate"] == {
        "status": "unavailable"
    }


def test_character_failure_retains_current_points_and_last_baseline(
    harness: Harness,
) -> None:
    """Downstream geometry remains diagnostic even after capability admission."""
    baseline = harness.native.snapshot()
    harness.point_shift = 1
    with pytest.raises(
        ValueError, match="Native character pitch/origin/horizontal fit disagrees"
    ) as caught:
        harness.native.snapshot()
    module.retain_native_diagnostic(caught.value)
    diagnostic = cast("Any", caught.value).native_diagnostic
    assert diagnostic["first_failed_predicate"] == "characters.origin_pitch"
    current = diagnostic["current"]
    assert current["characters"]["value"]["points"]["0"]["first"] == [0, 1]
    assert current["characters"]["value"]["points"]["0"]["last"] == [336, 1]
    assert current["character_points"]["value"][0]["packed"] == 1 << 16
    assert current["character_points"]["value"][0]["decoded"] == [0, 1]
    assert (
        diagnostic["last_successful_snapshot"]["text_layout"] == baseline["text_layout"]
    )
    assert current["expected"]["value"]["pitch"] == 16


def test_first_failure_snapshot_survives_safe_restoration_and_cleanup(
    harness: Harness,
) -> None:
    """A known state mismatch stays restore-eligible without replacing its proof."""
    harness.native.snapshot()
    harness.position = 3
    with pytest.raises(module.NativeStateMismatchError, match="disagreement") as caught:
        harness.native.snapshot()
    error: Any = caught.value
    frozen = error._native_diagnostic_frozen
    assert harness.native.unsafe_write_reason is None
    assert harness.native.budget.failure is None
    harness.native.seek(100 * 3 / 13, restoring=True, admit_write=lambda: None)
    harness.native.snapshot()
    harness.native.close(error)
    harness.native.retain_diagnostic(error)
    diagnostic = error.native_diagnostic
    assert error._native_diagnostic_frozen is frozen
    assert diagnostic["first_failed_predicate"] == "state.position_agrees"
    assert diagnostic["current"]["scroll_info"]["value"]["position"] == 3
    assert diagnostic["current"]["first_visible_line"]["value"] == 0
    assert (
        diagnostic["last_successful_snapshot"]["text_layout"]["first_visible_line"] == 0
    )
    module.retain_native_diagnostic(error)
    assert error.native_diagnostic is diagnostic
    assert type(error) is module.NativeStateMismatchError
    assert str(error) == "Native line/position/percentage disagreement"
    assert harness.native.budget.failure is None


def test_successful_snapshot_adds_no_serialization_or_hashing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passive collection leaves the original one hash and JSON work unchanged."""
    dumps, loads, digest = module.json.dumps, module.json.loads, module.hashlib.sha256
    dump_spy, load_spy, hash_spy = (
        Mock(wraps=dumps),
        Mock(wraps=loads),
        Mock(wraps=digest),
    )
    monkeypatch.setattr(module.json, "dumps", dump_spy)
    monkeypatch.setattr(module.json, "loads", load_spy)
    monkeypatch.setattr(module.hashlib, "sha256", hash_spy)
    harness.native.snapshot()
    assert dump_spy.call_count == 1
    assert load_spy.call_count == 2
    assert hash_spy.call_count == 1


def test_native_failure_freeze_does_not_format_or_serialize(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed formatting is deferred to the caller's admitted failure budget."""
    _override_pattern(harness, {5: -1.0})
    no_json = Mock(side_effect=AssertionError("premature serialization"))
    no_format = Mock(side_effect=AssertionError("premature formatting"))
    monkeypatch.setattr(module.json, "dumps", no_json)
    monkeypatch.setattr(module, "_diagnostic_value", no_format)
    error = _capability_error(harness)
    no_json.assert_not_called()
    no_format.assert_not_called()
    assert (
        error._native_diagnostic_frozen["first_failed_predicate"]
        == "pattern.horizontal_percent"
    )
    assert not hasattr(error, "native_diagnostic")


def test_diagnostic_observation_does_not_change_text_short_circuit(
    harness: Harness,
) -> None:
    """A failed line count still skips margins and all later text reads."""
    original = harness.native.user.SendMessageTimeoutW

    def send(  # noqa: PLR0913, PLR0917 - fixed SendMessageTimeoutW ABI
        hwnd: int, message: int, wp: int, lp: int, flags: int, timeout: int, output: Any
    ) -> int:
        result = original(hwnd, message, wp, lp, flags, timeout, output)
        if message == 0xBA:
            c.cast(output, c.POINTER(c.c_size_t)).contents.value = 37
        return int(result)

    harness.native.user.SendMessageTimeoutW = send
    with pytest.raises(
        ValueError, match="Native logical line count/margins changed"
    ) as caught:
        harness.native.snapshot()
    module.retain_native_diagnostic(caught.value)
    diagnostic = cast("Any", caught.value).native_diagnostic
    assert diagnostic["first_failed_predicate"] == "text.line_count"
    assert diagnostic["current"]["line_count"]["value"] == 37
    assert diagnostic["current"]["margins"] == {"status": "unattempted"}
    assert 0xD4 not in [call[1] for call in harness.sent]
    assert 0xB0 not in [call[1] for call in harness.sent]


def test_diagnostic_postcall_failure_still_returns_acquired_owner(
    harness: Harness,
) -> None:
    """Recording cannot strand a native resource before its owner is retained."""
    native = harness.native
    native._begin_diagnostic()
    native._observed = Mock(side_effect=OSError("diagnostic storage failed"))
    value = native.raw(lambda: 456)
    release = Mock()
    native.own(lambda: release(value))
    with pytest.raises(OSError, match="diagnostic storage failed"):
        native.budget.check()
    native.close()
    release.assert_called_once_with(456)


def test_positive_zero_observation_is_separate_from_no_change_command(
    harness: Harness,
) -> None:
    """Raw +0.0 is retained while every horizontal command remains -1.0."""
    native = harness.native
    top = native.snapshot()
    assert type(top["scroll_pattern"][0]) is float
    assert top["scroll_pattern"][0].hex() == "0x0.0p+0"
    assert top["pattern_fixed"][0].hex() == "0x0.0p+0"
    lines = (3, 0, 8, 13, 13, 0, 3)
    for index, line in enumerate(lines):
        native.seek(
            100 * line / 13, restoring=index == len(lines) - 1, admit_write=lambda: None
        )
        state = native.snapshot()
        assert state["text_layout"]["first_visible_line"] == line
        assert state["scroll"]["position"] == line
        assert state["scroll_pattern"][0].hex() == "0x0.0p+0"
        assert state["pattern_fixed"][0].hex() == "0x0.0p+0"
        assert native._fixed_baseline["pattern_fixed"][0].hex() == "0x0.0p+0"
        assert native._last_native_snapshot["scroll_pattern"][0].hex() == "0x0.0p+0"
    assert harness.writes == [(-1.0, 100 * line / 13) for line in lines]
    assert all(
        type(horizontal) is float and horizontal.hex() == "-0x1.0000000000000p+0"
        for horizontal, _vertical in harness.writes
    )
    assert native.unsafe_write_reason is None


@pytest.mark.parametrize("after_baseline", [False, True])
@pytest.mark.parametrize(
    "value",
    [
        -0.0,
        -1.0,
        1.0,
        -0.01,
        0.01,
        100.0,
        5e-324,
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_non_positive_zero_horizontal_observation_never_admits_write(
    harness: Harness, value: float, *, after_baseline: bool
) -> None:
    """No -0/-1 alias, drift, nonfinite value, or near-zero is normalized."""
    native = harness.native
    if after_baseline:
        native.snapshot()
    reads = _override_pattern(harness, {5: value})
    admission = Mock()
    with pytest.raises(
        ValueError, match="Vertical-only ScrollPattern capabilities changed"
    ) as caught:
        native.seek(100, admit_write=admission)
    error: Any = caught.value
    admission.assert_not_called()
    assert harness.writes == []
    assert reads == [5, 6, 7, 8, 9, 10]
    observed = error._native_diagnostic_frozen["current"]["pattern.horizontal_percent"][
        "value"
    ]
    assert type(value) is float
    assert type(observed) is float
    assert observed.hex() == float.hex(value)
    module.retain_native_diagnostic(error)
    assert error.native_diagnostic["current"]["pattern.horizontal_percent"]["value"][
        "hex"
    ] == float.hex(value)
    assert native.unsafe_write_reason is not None
    with pytest.raises(RuntimeError, match="prohibited"):
        native.seek(100 * 3 / 13, restoring=True, admit_write=admission)
    admission.assert_not_called()
    assert harness.writes == []
    assert reads == [5, 6, 7, 8, 9, 10]
    if after_baseline:
        assert native._fixed_baseline["pattern_fixed"][0].hex() == "0x0.0p+0"
        assert native._last_native_snapshot["scroll_pattern"][0].hex() == "0x0.0p+0"
    else:
        assert not hasattr(native, "_fixed_baseline")


@pytest.mark.parametrize("length", [1, 32, 33])
def test_extracted_runtime_id_producer_keeps_signed32_bound(
    harness: Harness, length: int
) -> None:
    """Inherited actual SAFEARRAY reader retains the original 32-part maximum."""
    native = harness.native
    released: list[int] = []
    reads: list[int] = []

    def write(output: Any, value: int, kind: Any) -> int:
        c.cast(output, c.POINTER(kind)).contents.value = value
        return 0

    def element(_pointer: Any, index: Any, output: Any) -> int:
        part = c.cast(index, c.POINTER(c.c_int32)).contents.value
        reads.append(part)
        return write(output, -(1 << 31) + part, c.c_int32)

    native.auto = SimpleNamespace(
        SafeArrayGetDim=lambda _pointer: 1,
        SafeArrayGetElemsize=lambda _pointer: 4,
        SafeArrayGetVartype=lambda _pointer, out: write(out, 3, c.c_uint16),
        SafeArrayGetLBound=lambda _pointer, _axis, out: write(out, 0, c.c_int32),
        SafeArrayGetUBound=lambda _pointer, _axis, out: write(
            out, length - 1, c.c_int32
        ),
        SafeArrayGetElement=element,
        SafeArrayDestroy=lambda pointer: released.append(pointer.value) or 0,
    )
    interface: Any = SimpleNamespace(
        call=lambda _slot, _types, out: write(out, 44, c.c_void_p)
    )
    if length > 32:
        with pytest.raises(ValueError, match="runtime ID array"):
            module.NativeObservation.runtime_id(native, interface)
        assert reads == []
    else:
        assert module.NativeObservation.runtime_id(native, interface) == tuple(
            -(1 << 31) + part for part in range(length)
        )
        assert reads == list(range(length))
    assert released == [44]


def test_actual_snapshot_retains_native_rail_without_invented_thumb_equation(
    harness: Harness,
) -> None:
    """Only existing rail predicates constrain state, while values remain exact."""
    original = harness.bar

    def arbitrary_sdk_state(hwnd: int, object_id: int, output: Any) -> int:
        result = original(hwnd, object_id, output)
        bar = c.cast(output, c.POINTER(module.ScrollBarInfo)).contents
        bar.state[1] = (1 << 32) - 1
        bar.line, bar.top, bar.bottom = -(1 << 31), 0, (1 << 31) - 1
        return result

    harness.native.user.GetScrollBarInfo = arbitrary_sdk_state
    snapshot = harness.native.snapshot()
    assert snapshot["native"]["vertical_bar"] == [553, 60, 570, 473]
    assert snapshot["scroll"]["bar_state"] == [0, (1 << 32) - 1, 0, 0, 0, 0]
    assert snapshot["scroll"]["thumb"] == [-(1 << 31), 0, (1 << 31) - 1]
    assert harness.native.snapshot() == snapshot


@pytest.mark.parametrize("field", ["root", "panel"])
def test_actual_snapshot_checks_full_retained_native_structure(
    harness: Harness, field: str
) -> None:
    """Retained structure is independently guarded before any frame or move."""
    native = harness.native
    native.snapshot()
    original = native.structure

    def changed(element: str) -> dict[str, Any]:
        result = original(element)
        if element == field:
            result["framework"] = "changed"
        return result

    native.structure = changed
    with pytest.raises(ValueError, match="identity/layout"):
        native.seek(100.0, admit_write=lambda: None)
    assert harness.writes == []
    assert native.unsafe_write_reason is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [("left", 552), ("top", 61), ("right", 569), ("bottom", 472), ("state", 0x10000)],
)
def test_actual_scroll_rail_rejects_original_native_predicates(
    harness: Harness, field: str, value: int
) -> None:
    """Source native rail visibility and every frozen edge fail before moving."""
    original = harness.bar

    def changed(hwnd: int, object_id: int, output: Any) -> int:
        result = original(hwnd, object_id, output)
        bar = c.cast(output, c.POINTER(module.ScrollBarInfo)).contents
        if field == "state":
            bar.state[0] = value
        else:
            setattr(bar.rect, field, value)
        return result

    harness.native.user.GetScrollBarInfo = changed
    with pytest.raises(ValueError, match="rail geometry/visibility"):
        harness.native.seek(100.0, admit_write=lambda: None)
    assert harness.writes == []
    assert harness.native.unsafe_write_reason is not None


@pytest.mark.parametrize("value", [True, -1, 38])
def test_actual_native_line_index_rejects_before_message_dispatch(
    harness: Harness, value: int
) -> None:
    """The extracted read protocol cannot address outside the fixed text."""
    with pytest.raises(ValueError, match="fixed line index"):
        harness.native._line_index(value)
    assert harness.sent == []


@pytest.mark.parametrize("value", [True, -1, len(TEXT)])
def test_actual_native_character_index_rejects_before_message_dispatch(
    harness: Harness, value: int
) -> None:
    """Rejected character offsets never enter the native timeout wrapper."""
    with pytest.raises(ValueError, match="bounded character index"):
        harness.native._point(value)
    assert harness.sent == []


def test_missing_retained_pattern_fails_before_write(harness: Harness) -> None:
    """Moving capture has no discovery fallback after its retained pattern is lost."""
    harness.native.pattern = None
    with pytest.raises(ValueError, match="ScrollPattern is required"):
        harness.native.seek(100.0, admit_write=lambda: None)
    assert harness.writes == []


def test_source_text_length_guard_checks_real_utf16_buffer(harness: Harness) -> None:
    """A fixed-length buffer cannot substitute different native text bytes."""
    harness.text = "X" + TEXT[1:]
    with pytest.raises(ValueError, match="text bytes changed"):
        harness.native.snapshot()
    assert harness.native.unsafe_write_reason is not None


def test_missing_native_diagnostic_does_not_invent_failure_evidence() -> None:
    """The failure helper cannot manufacture snapshots for an unrelated error."""
    error = ValueError("unrelated")
    module.retain_native_diagnostic(error)
    assert not hasattr(error, "native_diagnostic")
