"""Portable owned-WPF provider contracts; fake native ABI, never real UIA/input."""

from __future__ import annotations

import ctypes as c
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from verdiclip.capture import observe_native as native
from verdiclip.capture import observe_provider as provider
from verdiclip.capture.observe_protocol import ObservationError, ObserveTarget


class NativeFixture:
    """Fixed fake COM graph and actual ctypes out buffers for ABI ownership tests."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 0.0
        self.cancelled: object = False
        self.calls: list[str] = []
        self.released: list[int] = []
        self.arrays: list[int] = []
        self.buffers: list[object] = []
        self.libraries = {
            name: Mock()
            for name in ("kernel32", "user32", "dwmapi", "ole32", "oleaut32")
        }
        self.target = ObserveTarget(7, 1234, 99, (11, 21))
        self.shot = SimpleNamespace(width=4, height=3, rgb=bytes(range(36)))
        self.screen = Mock()
        self.screen.grab.return_value = self.shot
        self.screen.close.side_effect = lambda: self.calls.append("mss.close")
        self.mss_factory = Mock(side_effect=self.make_mss)
        monkeypatch.setattr(
            native,
            "sys",
            SimpleNamespace(platform="win32", exception=__import__("sys").exception),
        )
        monkeypatch.setattr(
            c, "WinDLL", lambda name, **_kw: self.libraries[name], raising=False
        )
        monkeypatch.setattr(
            c, "WINFUNCTYPE", lambda *_args: lambda function: function, raising=False
        )
        monkeypatch.setattr(c, "get_last_error", lambda: 5, raising=False)
        monkeypatch.setattr(
            c, "WinError", lambda code: OSError(code, "native denied"), raising=False
        )
        monkeypatch.setattr(native._Com, "_function", self.com_function)
        monkeypatch.setattr(provider.mss, "mss", self.mss_factory)
        monkeypatch.setattr(provider.time, "sleep", self.sleep)
        self.install()

    def clock(self) -> float:
        """Return the controllable monotonic clock."""
        return self.now

    def cancel(self) -> bool:
        """Supply strict or deliberately malformed cancellation evidence."""
        return self.cancelled  # ty: ignore[invalid-return-type] - invalid callback tests

    def sleep(self, seconds: float) -> None:
        """Advance time without blocking the portable test."""
        self.now += seconds + 0.000001

    def make_mss(self, **kwargs: object) -> Mock:
        """Record cursor-free construction after DPI establishment."""
        assert kwargs == {"with_cursor": False}
        self.calls.append("mss.create")
        return self.screen

    def api(self, library: str, name: str, callback: Callable[..., object]) -> None:
        """Install a counted native call with its precise output behavior."""

        def call(*args: object) -> object:
            self.calls.append(name)
            return callback(*args)

        getattr(self.libraries[library], name).side_effect = call

    @staticmethod
    def out(pointer: object, value: object) -> int:
        """Populate the actual ctypes byref output object."""
        cast(Any, pointer)._obj.value = value
        return 0

    @staticmethod
    def rect(pointer: object, edges: tuple[int, int, int, int]) -> int:
        """Populate explicit32bit native rectangle fields."""
        for key, value in zip(("left", "top", "right", "bottom"), edges, strict=True):
            setattr(cast(Any, pointer)._obj, key, value)
        return 1

    def install(self) -> None:
        """Define all fixed Win32 and automation success calls."""
        self.api("kernel32", "OpenProcess", lambda *_a: 77)
        self.api("kernel32", "CloseHandle", lambda *_a: 1)
        self.api("kernel32", "WaitForSingleObject", lambda *_a: 258)

        def times(_handle: int, created: object, *_rest: object) -> int:
            cast(Any, created)._obj.low, cast(Any, created)._obj.high = 1234, 0
            return 1

        self.api("kernel32", "GetProcessTimes", times)
        self.api("user32", "SetProcessDpiAwarenessContext", lambda *_a: 1)
        self.api("user32", "GetThreadDpiAwarenessContext", lambda: -4)
        self.api("user32", "AreDpiAwarenessContextsEqual", lambda *_a: 1)
        self.api("user32", "GetDpiForWindow", lambda *_a: 96)
        self.api(
            "user32", "GetWindowThreadProcessId", lambda _h, pid: self.out(pid, 7) or 1
        )
        self.api("user32", "GetForegroundWindow", lambda: 99)
        self.api("user32", "GetAncestor", lambda *_a: 99)
        self.api("user32", "WindowFromPoint", lambda *_a: 99)
        self.api(
            "user32",
            "GetClientRect",
            lambda _h, rect: self.rect(rect, (0, 0, 500, 500)),
        )
        self.api("user32", "ClientToScreen", lambda *_a: 1)

        def tick(value: object) -> int:
            cast(Any, value)._obj.tick = 123
            return 1

        self.api("user32", "GetLastInputInfo", tick)
        self.api("user32", "GetTopWindow", lambda *_a: 99)
        self.api("user32", "GetWindow", lambda *_a: 99)
        self.api(
            "user32",
            "GetWindowRect",
            lambda _h, rect: self.rect(rect, (700, 700, 800, 800)),
        )
        self.api("user32", "IsWindowVisible", lambda *_a: 1)
        self.api("user32", "IsIconic", lambda *_a: 0)
        self.api("user32", "EnumChildWindows", lambda *_a: 0)
        self.api(
            "user32",
            "EnumDisplayMonitors",
            lambda _a, _b, callback, _d: callback(1, None, None, 0),
        )

        def monitor(_handle: int, pointer: object) -> int:
            info = cast(Any, pointer)._obj
            info.monitor = native.Rect(0, 0, 500, 500)
            info.flags = 1
            return 1

        self.api("user32", "GetMonitorInfoW", monitor)
        self.api("dwmapi", "DwmGetWindowAttribute", lambda *_a: 0)
        self.api("ole32", "CoInitializeEx", lambda *_a: 0)
        self.api("ole32", "CoUninitialize", lambda: None)
        self.api("ole32", "CoCreateInstance", lambda *_a: self.out(_a[-1], 101))
        self.api("oleaut32", "SysStringLen", lambda *_a: 3)
        self.api("oleaut32", "SysFreeString", lambda *_a: None)
        self.api("oleaut32", "SafeArrayGetDim", lambda *_a: 1)
        self.api("oleaut32", "SafeArrayGetElemsize", lambda *_a: 4)
        self.api("oleaut32", "SafeArrayGetVartype", lambda _p, out: self.out(out, 3))
        self.api("oleaut32", "SafeArrayGetLBound", lambda _p, _d, out: self.out(out, 0))
        self.api("oleaut32", "SafeArrayGetUBound", lambda _p, _d, out: self.out(out, 1))
        self.api(
            "oleaut32",
            "SafeArrayGetElement",
            lambda array, index, out: self.out(out, array.value + index._obj.value),
        )
        self.api(
            "oleaut32",
            "SafeArrayDestroy",
            lambda pointer: self.arrays.append(pointer.value) or 0,
        )

    def com_function(  # noqa: C901 - explicit small fixed ABI graph
        self, slot: int, _result: type, _arguments: tuple[type, ...]
    ) -> Callable[..., object]:
        """Replace only the vtable invocation; production out-pointer handling runs."""

        def invoke(pointer: c.c_void_p, *args: object) -> object:  # noqa: PLR0911 - fixed slots
            identity = pointer.value
            assert identity is not None
            self.calls.append(f"com:{identity}:{slot}")
            if slot == 2:
                self.released.append(identity)
                return 0
            if identity == 101:
                if slot == 3:
                    return self.out(
                        args[-1],
                        int(cast(Any, args[0]).value == cast(Any, args[1]).value),
                    )
                return self.out(args[-1], {6: 103, 7: 104, 16: 102}[slot])
            if identity == 102:
                return self.out(args[-1], 103)
            if identity in (103, 104):
                if slot == 4:
                    return self.out(args[-1], identity + 1000)
                if slot == 14:
                    return self.out(args[-1], 105 if identity == 104 else None)
                if slot == 40:
                    buffer = c.create_unicode_buffer("WPF")
                    self.buffers.append(buffer)
                    return self.out(args[-1], c.addressof(buffer))
                if slot == 43:
                    self.rect(
                        args[-1],
                        (0, 0, 500, 500) if identity == 103 else (10, 20, 14, 23),
                    )
                    return 0
                return self.out(
                    args[-1], {20: 7, 36: 99 if identity == 103 else 0, 38: 0}[slot]
                )
            return self.out(args[-1], {5: -1, 6: 0, 7: 100, 8: 25, 9: 0, 10: 1}[slot])

        return invoke

    def run(self) -> provider.ObservedFrame:
        """Invoke the real provider with a fixed original deadline."""
        return provider.observe_once(
            self.target,
            "a" * 32,
            1,
            deadline=2.0,
            clock=self.clock,
            cancelled=self.cancel,
        )


@pytest.fixture
def fixture(monkeypatch: pytest.MonkeyPatch) -> NativeFixture:
    """Every test has isolated native calls, owned outputs and callback state."""
    return NativeFixture(monkeypatch)


def test_full_observation_uses_outer_pixels_and_closes_before_result(
    fixture: NativeFixture,
) -> None:
    """Real schema and stable-sampling code operate through the fake native ABI."""
    result = fixture.run()
    assert result.rgb == fixture.shot.rgb
    identity = result.observation.frame_identity()
    assert (identity.x, identity.y, identity.width, identity.height) == (10, 20, 4, 3)
    assert fixture.screen.grab.call_count == 3
    fixture.screen.grab.assert_called_with(
        {"left": 10, "top": 20, "width": 4, "height": 3}
    )
    fixture.mss_factory.assert_called_once_with(with_cursor=False)
    assert fixture.calls.index("AreDpiAwarenessContextsEqual") < fixture.calls.index(
        "GetProcessTimes"
    )
    assert fixture.calls.index("AreDpiAwarenessContextsEqual") < fixture.calls.index(
        "CoInitializeEx"
    )
    assert fixture.calls.index("CoInitializeEx") < fixture.calls.index("mss.create")
    assert fixture.calls.index("mss.close") < fixture.calls.index("CoUninitialize")
    assert fixture.calls[-1] == "CloseHandle"
    assert {101, 102, 103, 104, 105} <= set(fixture.released)
    assert fixture.arrays


@pytest.mark.parametrize(
    "failure",
    [
        "dpi",
        "process",
        "creation",
        "foreground",
        "window-dpi",
        "input",
        "array",
        "bstr",
    ],
)
def test_native_guard_failures_never_capture(
    fixture: NativeFixture, failure: str
) -> None:
    """Denied or unsupported evidence fails rather than weakening fixture guards."""
    if failure == "dpi":
        fixture.api("user32", "AreDpiAwarenessContextsEqual", lambda *_a: 0)
    elif failure == "process":
        fixture.api("kernel32", "WaitForSingleObject", lambda *_a: 0)
    elif failure == "creation":
        fixture.target = ObserveTarget(7, 9999, 99, (11, 21))
    elif failure == "foreground":
        fixture.api("user32", "GetForegroundWindow", lambda: 1)
    elif failure == "window-dpi":
        fixture.api("user32", "GetDpiForWindow", lambda *_a: 120)
    elif failure == "input":
        fixture.api("user32", "GetLastInputInfo", lambda *_a: 0)
    elif failure == "array":
        fixture.api(
            "oleaut32", "SafeArrayGetLBound", lambda _p, _d, out: fixture.out(out, 1)
        )
    else:
        fixture.api("oleaut32", "SysStringLen", lambda *_a: 257)
    with pytest.raises((ObservationError, OSError)):
        fixture.run()
    fixture.screen.grab.assert_not_called()
    if failure == "dpi":
        assert "OpenProcess" not in fixture.calls
        assert "CoInitializeEx" not in fixture.calls
    else:
        assert fixture.calls[-1] == "CloseHandle"


@pytest.mark.parametrize("failure", ["pixels", "dimensions", "bytes", "guard"])
def test_changed_samples_do_not_retry(fixture: NativeFixture, failure: str) -> None:
    """No successful result or extra capture follows a changed first/second sample."""
    if failure == "pixels":
        fixture.screen.grab.side_effect = [
            fixture.shot,
            SimpleNamespace(width=4, height=3, rgb=bytes(36)),
        ]
    elif failure == "dimensions":
        fixture.shot.width = 3
    elif failure == "bytes":
        fixture.shot.rgb = bytes(35)
    else:

        def changed(_region: object) -> SimpleNamespace:
            fixture.api("user32", "GetForegroundWindow", lambda: 1)
            return fixture.shot

        fixture.screen.grab.side_effect = changed
    with pytest.raises(ObservationError):
        fixture.run()
    assert fixture.screen.grab.call_count <= 2
    assert fixture.calls[-1] == "CloseHandle"


@pytest.mark.parametrize("stage", ["mss", "grab", "cleanup"])
def test_deadline_crossing_cannot_publish(fixture: NativeFixture, stage: str) -> None:
    """Resources acquired before an expired callback are still retired."""
    if stage == "mss":

        def create(**kwargs: object) -> Mock:
            fixture.now = 2.0
            return fixture.make_mss(**kwargs)

        fixture.mss_factory.side_effect = create
    elif stage == "grab":

        def grab(_region: object) -> SimpleNamespace:
            fixture.now = 2.0
            return fixture.shot

        fixture.screen.grab.side_effect = grab
    else:
        fixture.screen.close.side_effect = lambda: setattr(fixture, "now", 2.0)
    with pytest.raises(ObservationError, match="deadline"):
        fixture.run()
    fixture.screen.close.assert_called_once()
    assert fixture.calls[-1] == "CloseHandle"


def test_cancel_after_partial_sample_blocks_more_native_evidence(
    fixture: NativeFixture,
) -> None:
    """After grab changes the latch, the remaining calls are cleanup only."""

    def grab(_region: object) -> SimpleNamespace:
        fixture.cancelled = True
        fixture.calls.append("cancel-latched")
        return fixture.shot

    fixture.screen.grab.side_effect = grab
    with pytest.raises(ObservationError, match="cancelled"):
        fixture.run()
    tail = fixture.calls[fixture.calls.index("cancel-latched") + 1 :]
    assert all(
        call in {"mss.close", "CoUninitialize", "CloseHandle"} or call.endswith(":2")
        for call in tail
    )
    assert fixture.screen.grab.call_count == 1


@pytest.mark.parametrize("stage", ["factory", "interface", "bstr", "array"])
def test_failed_hresult_retains_and_releases_output(
    fixture: NativeFixture,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    """Even a failed call's nonnull owned output enters terminal cleanup."""
    if stage == "factory":

        def create(*args: object) -> int:
            fixture.out(args[-1], 101)
            return -1

        fixture.api("ole32", "CoCreateInstance", create)
    else:
        original = fixture.com_function

        def function(
            slot: int, result: type, args: tuple[type, ...]
        ) -> Callable[..., object]:
            actual = original(slot, result, args)

            def invoke(pointer: c.c_void_p, *values: object) -> object:
                response = actual(pointer, *values)
                desired = {"interface": 6, "bstr": 40, "array": 4}[stage]
                return -1 if slot == desired else response

            return invoke

        monkeypatch.setattr(native._Com, "_function", staticmethod(function))
    with pytest.raises(OSError, match="HRESULT"):
        fixture.run()
    assert fixture.calls[-1] == "CloseHandle"
    if stage == "factory":
        assert fixture.released == [101]
    elif stage == "interface":
        assert 103 in fixture.released
    elif stage == "bstr":
        assert "SysFreeString" in fixture.calls
    else:
        assert fixture.arrays
    assert "CoUninitialize" in fixture.calls


def test_cleanup_attempts_all_and_preserves_primary(fixture: NativeFixture) -> None:
    """MSS and handle cleanup failures cannot replace a failed capture."""
    fixture.screen.grab.side_effect = RuntimeError("capture primary")
    fixture.screen.close.side_effect = OSError("screen cleanup")
    fixture.api("kernel32", "CloseHandle", lambda *_a: 0)
    with pytest.raises(RuntimeError, match="capture primary") as caught:
        fixture.run()
    assert len(caught.value.__notes__) == 2
    assert "CoUninitialize" in fixture.calls
    assert fixture.calls[-1] == "CloseHandle"


@pytest.mark.parametrize(
    "stage", ["z-cycle", "z-missing", "external", "unknown", "child"]
)
def test_conservative_occlusion_rejects_unknown_or_intersection(
    fixture: NativeFixture, stage: str
) -> None:
    """No window-name exemptions, guessed emptiness or fixture crop can admit pixels."""
    fixture.api("user32", "GetTopWindow", lambda *_a: 101)
    if stage == "z-cycle":
        fixture.api("user32", "GetWindow", lambda *_a: 101)
    elif stage == "z-missing":
        fixture.api("user32", "GetWindow", lambda *_a: None)
    elif stage == "unknown":
        fixture.api("user32", "GetWindowRect", lambda *_a: 0)
    else:
        fixture.api(
            "user32",
            "GetWindowRect",
            lambda _h, rect: fixture.rect(rect, (11, 21, 20, 30)),
        )
        if stage == "child":
            fixture.api("user32", "GetTopWindow", lambda *_a: 99)
            fixture.api(
                "user32",
                "EnumChildWindows",
                lambda _root, callback, _data: callback(101, 0),
            )
    with pytest.raises((ObservationError, OSError)):
        fixture.run()
    fixture.screen.grab.assert_not_called()


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), 0.0, 2.001])
def test_original_deadline_validation(deadline: float) -> None:
    """No native resource is needed to reject an extended or malformed budget."""
    with pytest.raises(ObservationError, match="deadline"):
        native.ObserveBudget(deadline, lambda: 0.0, lambda: False)


@pytest.mark.parametrize("value", [0, None, object(), True])
def test_budget_strict_cancel_latches(fixture: NativeFixture, value: object) -> None:
    """The cancellation callback must return exactly bool and remains terminal."""
    budget = native.ObserveBudget(2.0, fixture.clock, fixture.cancel)
    fixture.cancelled = value
    with pytest.raises(ObservationError) as caught:
        budget.check()
    fixture.cancelled = False
    with pytest.raises(ObservationError) as repeated:
        budget.check()
    assert repeated.value is caught.value


@pytest.mark.parametrize(
    "failure", ["dim", "kind", "upper", "destroy", "monitor", "child", "rect"]
)
def test_additional_native_bounds_and_unknown_evidence(
    fixture: NativeFixture, failure: str
) -> None:
    """Native arrays/enumerations have hard bounds, and failures are not skipped."""
    if failure == "dim":
        fixture.api("oleaut32", "SafeArrayGetDim", lambda *_a: 2)
    elif failure == "kind":
        fixture.api(
            "oleaut32", "SafeArrayGetVartype", lambda _p, out: fixture.out(out, 8)
        )
    elif failure == "upper":
        fixture.api(
            "oleaut32", "SafeArrayGetUBound", lambda _p, _d, out: fixture.out(out, 32)
        )
    elif failure == "destroy":
        fixture.api("oleaut32", "SafeArrayDestroy", lambda *_a: -1)
    elif failure == "monitor":

        def monitors(
            _dc: object, _clip: object, callback: Callable[..., int], _data: int
        ) -> int:
            for handle in range(1, 10):
                if not callback(handle, None, None, 0):
                    return 0
            return 1

        fixture.api("user32", "EnumDisplayMonitors", monitors)
    elif failure == "child":
        fixture.api(
            "user32",
            "EnumChildWindows",
            lambda _r, callback, _d: callback(101, 0) and callback(101, 0),
        )
    else:
        fixture.api("user32", "GetTopWindow", lambda *_a: 101)
        fixture.api(
            "user32", "GetWindowRect", lambda _h, rect: fixture.rect(rect, (0, 0, 0, 0))
        )
    with pytest.raises((ObservationError, OSError)):
        fixture.run()
    fixture.screen.grab.assert_not_called()
    assert fixture.calls[-1] == "CloseHandle"


def test_array_primary_survives_destroy_error(fixture: NativeFixture) -> None:
    """Immediate array cleanup cannot hide the original invalid-array evidence."""
    fixture.api("oleaut32", "SafeArrayGetDim", lambda *_a: 2)
    fixture.api("oleaut32", "SafeArrayDestroy", lambda *_a: -1)
    with pytest.raises(ObservationError, match="runtime ID array") as caught:
        fixture.run()
    assert any("cleanup" in note for note in caught.value.__notes__)
    assert "CoUninitialize" in fixture.calls


def test_successful_observation_cleanup_error_disqualifies(
    fixture: NativeFixture,
) -> None:
    """A complete pixel set is insufficient when an owned handle fails to close."""
    fixture.api("kernel32", "CloseHandle", lambda *_a: 0)
    with pytest.raises(OSError, match="native denied"):
        fixture.run()
    assert fixture.screen.grab.call_count == 3
    assert "CoUninitialize" in fixture.calls


def test_native_callback_exception_latches_before_next_read(
    fixture: NativeFixture,
) -> None:
    """An unexpected API callback error has no recovery path to additional reads."""
    budget = native.ObserveBudget(2.0, fixture.clock, fixture.cancel)
    owner = native.NativeObservation(fixture.target, budget)
    first = RuntimeError("callback primary")
    failed = Mock(side_effect=first)
    with pytest.raises(RuntimeError, match="callback primary"):
        owner.raw(failed)
    later = Mock()
    with pytest.raises(RuntimeError, match="callback primary"):
        owner.raw(later)
    later.assert_not_called()
    owner.close()
    calls = list(fixture.calls)
    owner.close()
    assert fixture.calls == calls


@pytest.mark.parametrize(
    "failure", ["cycle", "ownership", "depth", "root-change", "panel-change", "point"]
)
def test_retained_association_and_identity_cannot_be_replaced(
    fixture: NativeFixture,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Retained references still require bounded ancestry to the exact retained root."""
    owner = native.NativeObservation(
        fixture.target, native.ObserveBudget(2.0, fixture.clock, fixture.cancel)
    )
    if failure == "cycle":
        monkeypatch.setattr(owner, "_parent", lambda element: element)
    elif failure == "ownership":
        original = owner.structure
        monkeypatch.setattr(
            owner, "structure", lambda element: {**original(element), "pid": 8}
        )
    elif failure == "depth":
        monkeypatch.setattr(owner, "_same", lambda *_a: False)
        sequence = iter(range(1000))
        monkeypatch.setattr(owner, "runtime_id", lambda _e: (next(sequence),))
    elif failure in {"root-change", "panel-change"}:
        original = owner.structure
        changed = owner.root if failure == "root-change" else owner.panel

        def structure(element: native._Com) -> dict[str, Any]:
            result = original(element)
            return {**result, "rect": [1, 1, 2, 2]} if element is changed else result

        monkeypatch.setattr(owner, "structure", structure)
    else:
        original = fixture.libraries["user32"].GetAncestor.side_effect
        fixture.api("user32", "WindowFromPoint", lambda *_a: 100)
        fixture.api(
            "user32",
            "GetAncestor",
            lambda hwnd, flags: 100 if hwnd == 100 else original(hwnd, flags),
        )
    with pytest.raises(ObservationError):
        owner.snapshot()
    owner.close()
    fixture.screen.grab.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        "null-factory",
        "null-root",
        "null-array",
        "unsupported-pattern",
        "root-invalid",
        "discovery-cycle",
        "no-scroll",
    ],
)
def test_discovery_failure_does_not_reacquire(
    fixture: NativeFixture,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Fixed discovery fails closed on missing resources or unsupported structure."""
    if failure == "null-factory":
        fixture.api("ole32", "CoCreateInstance", lambda *_a: 0)
    else:
        original = fixture.com_function

        def function(
            slot: int, result: type, args: tuple[type, ...]
        ) -> Callable[..., object]:
            actual = original(slot, result, args)

            def invoke(pointer: c.c_void_p, *values: object) -> object:
                if (failure == "null-root" and pointer.value == 101 and slot == 6) or (
                    failure == "null-array" and slot == 4
                ):
                    return 0
                response = actual(pointer, *values)
                if failure == "unsupported-pattern" and slot == 14:
                    return c.c_int32(0x80040204).value
                if failure == "root-invalid" and pointer.value == 103 and slot == 36:
                    return fixture.out(values[-1], 100)
                if failure == "discovery-cycle" and pointer.value == 102 and slot == 3:
                    return fixture.out(values[-1], 104)
                if failure in {"no-scroll", "discovery-cycle"} and slot == 10:
                    return fixture.out(values[-1], 0)
                return response

            return invoke

        monkeypatch.setattr(native._Com, "_function", staticmethod(function))
    with pytest.raises(ObservationError):
        fixture.run()
    assert fixture.calls.count("com:101:7") <= 1
    fixture.screen.grab.assert_not_called()
    assert fixture.calls[-1] == "CloseHandle"


def test_nonintersecting_child_does_not_invent_product_policy(
    fixture: NativeFixture,
) -> None:
    """A visible native child outside the capture bounds does not reject a fixture."""
    fixture.api("user32", "EnumChildWindows", lambda _r, callback, _d: callback(101, 0))
    assert fixture.run().rgb == fixture.shot.rgb


def test_hidden_above_root_window_is_not_occlusion(fixture: NativeFixture) -> None:
    """Every visited HWND counts but definitely hidden windows need no rectangle."""
    fixture.api("user32", "GetTopWindow", lambda *_a: 101)
    fixture.api("user32", "IsWindowVisible", lambda hwnd: int(hwnd == 99))
    assert fixture.run().rgb == fixture.shot.rgb
    assert "GetWindowRect" not in fixture.calls


@pytest.mark.parametrize("correlation", ["epoch", "request", "target"])
def test_bad_correlation_rejected_before_native(
    fixture: NativeFixture,
    correlation: str,
) -> None:
    """Malformed invocation data does not even open the fixture process."""
    with pytest.raises(ObservationError, match="correlation"):
        provider.observe_once(
            fixture.target if correlation != "target" else {},  # ty: ignore[invalid-argument-type]
            "0" * 32 if correlation == "epoch" else "a" * 32,
            128 if correlation == "request" else 1,
            deadline=2.0,
            clock=fixture.clock,
            cancelled=fixture.cancel,
        )
    assert fixture.calls == []


def test_too_short_quiet_period_is_not_rounded_up(
    fixture: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An early wake does not manufacture the required stable duration."""
    monkeypatch.setattr(provider.time, "sleep", lambda _s: None)
    with pytest.raises(ObservationError, match="quiet interval"):
        fixture.run()
    assert fixture.screen.grab.call_count == 3


def test_thread_affinity_checked_before_read_or_close(
    fixture: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrong-thread access cannot release COM or perform a native guard read."""
    owner = native.NativeObservation(
        fixture.target, native.ObserveBudget(2.0, fixture.clock, fixture.cancel)
    )
    actual = native.threading.get_ident()
    count = len(fixture.calls)
    with monkeypatch.context() as patch:
        patch.setattr(native.threading, "get_ident", lambda: actual + 1)
        with pytest.raises(ObservationError, match="thread affinity"):
            owner.snapshot()
        with pytest.raises(ObservationError, match="thread affinity"):
            owner.close()
    assert len(fixture.calls) == count
    owner.close()


def test_expiry_after_process_acquisition_still_closes(fixture: NativeFixture) -> None:
    """A successful returned owner is retained before post-acquisition admission."""

    def open_process(*_args: object) -> int:
        fixture.now = 2.0
        return 77

    fixture.api("kernel32", "OpenProcess", open_process)
    with pytest.raises(ObservationError, match="deadline"):
        fixture.run()
    assert fixture.calls[-1] == "CloseHandle"
    assert "GetProcessTimes" not in fixture.calls


def test_real_ctypes_vtable_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the actual vtable lookup and32bit out-value ABI without Windows."""
    monkeypatch.setattr(c, "WINFUNCTYPE", c.CFUNCTYPE, raising=False)
    calls: list[str] = []
    release_type = c.CFUNCTYPE(c.c_uint32, c.c_void_p)
    read_type = c.CFUNCTYPE(c.c_int32, c.c_void_p, c.POINTER(c.c_int32))

    def release(_pointer: object) -> int:
        calls.append("release")
        return 0

    def read(_pointer: object, output: Any) -> int:  # noqa: ANN401 - real ctypes pointer
        output[0] = -123
        return 0

    release_callback, read_callback = release_type(release), read_type(read)
    table = (c.c_void_p * 21)()
    table[2] = c.cast(release_callback, c.c_void_p).value
    table[20] = c.cast(read_callback, c.c_void_p).value
    interface = (c.c_void_p * 1)(c.cast(table, c.c_void_p).value)
    owner = Mock()
    owner.own.side_effect = native._Owner
    owner.raw.side_effect = lambda function, *args: function(*args)
    reference = native._Com(owner, c.cast(interface, c.c_void_p))
    assert reference.read(20, c.c_int32).value == -123
    reference.owner.close()
    assert calls == ["release"]
    assert c.sizeof(native.Rect) == 16
    assert c.sizeof(native.Point) == 8
    assert c.sizeof(native.Guid) == 16


def test_closed_owner_cannot_resume_native_reads(fixture: NativeFixture) -> None:
    """No stale callback can use closed process/interface ownership."""
    owner = native.NativeObservation(
        fixture.target, native.ObserveBudget(2.0, fixture.clock, fixture.cancel)
    )
    owner.close()
    count = len(fixture.calls)
    with pytest.raises(ObservationError, match="closed"):
        owner.snapshot()
    with pytest.raises(ObservationError, match="closed"):
        owner.own(Mock())
    assert len(fixture.calls) == count


@pytest.mark.parametrize("size", [0, 1, 2, 8, 2**32 - 1])
def test_array_element_size_checked_before_destination_write(
    fixture: NativeFixture,
    size: int,
) -> None:
    """Even VT_I4 cannot authorize a descriptor-sized copy into a four-byte slot."""
    fixture.api("oleaut32", "SafeArrayGetElemsize", lambda *_a: size)
    with pytest.raises(ObservationError, match="runtime ID array"):
        fixture.run()
    assert "SafeArrayGetElement" not in fixture.calls
    assert fixture.arrays == [1103]
    assert fixture.calls.count("SafeArrayDestroy") == 1
    assert "CoUninitialize" in fixture.calls
    assert fixture.calls[-1] == "CloseHandle"
