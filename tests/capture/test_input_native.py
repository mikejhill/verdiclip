"""Execute the production Win32 adapter against fake DLLs and synthetic packets."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys
from collections.abc import Iterator

import pytest
from tests.capture.input_dll_fixture import InputDllFixture

from verdiclip.capture.input_native import Win32InputPlatform
from verdiclip.capture.input_raw import WM_RAW_INPUT, RawPointer
from verdiclip.capture.input_routing import MoveMode
from verdiclip.exceptions import PlatformError
from verdiclip.geometry import Point

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows ABI signatures"
)


@pytest.fixture
def native(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Win32InputPlatform, InputDllFixture]]:
    """DLL loading is replaced before constructing the adapter; no hook is installed."""
    dll = InputDllFixture()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: dll)
    api = Win32InputPlatform()
    yield api, dll
    dll.fail = ""
    api.close()
    ctypes.set_last_error(0)


def test_native_adapter_registers_only_own_raw_receiver_and_retires_every_resource(
    native: tuple[Win32InputPlatform, InputDllFixture],
) -> None:
    """The actual ownership code runs without any native API or input device."""
    api, dll = native
    api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
    assert dll.registrations == [(1, 2, 0x100, 1234)]
    assert dll.hook_calls == 2
    assert api.desktop_safe()
    assert api.cursor() == Point(100, 200)
    assert not api.buttons_held()
    dll.held = {0x11, 0x0D, 1}
    assert api.control_held()
    assert api.buttons_held()
    assert api.keys_held() == frozenset({0x11, 0x0D})
    assert api.foreground() == 1
    assert api.close()
    assert dll.registrations == []
    assert dll.window == 0
    assert dll.calls.count("UnhookWindowsHookEx") == 2


def test_existing_registration_is_never_overwritten(
    native: tuple[Win32InputPlatform, InputDllFixture],
) -> None:
    """An existing component keeps its registration and no receiver is created."""
    api, dll = native
    original = [(1, 2, 0x100, 9999)]
    dll.registrations = original.copy()
    with pytest.raises(PlatformError, match="already owns"):
        api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
    assert api.close()
    assert dll.registrations == original
    assert dll.window == 0
    assert dll.hook_calls == 0


@pytest.mark.parametrize(
    "failure", ["CreateWindowExW", "RegisterRawInputDevices", "SetTimer", "second-hook"]
)
def test_partial_native_setup_always_has_owned_cleanup(
    native: tuple[Win32InputPlatform, InputDllFixture], failure: str
) -> None:
    """Every acquisition boundary can fail without leaking registration or hooks."""
    api, dll = native
    dll.fail = failure
    ctypes.set_last_error(5)
    with pytest.raises(OSError, match=r"\[WinError 5\]"):
        api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
    dll.fail = ""
    assert api.close()
    assert not dll.registrations
    assert dll.window == 0


def test_packet_processing_uses_raw_flags_and_always_calls_default_cleanup(
    native: tuple[Win32InputPlatform, InputDllFixture],
) -> None:
    """Foreground/background raw packets are decoded with an explicit ownership path."""
    api, dll = native
    events: list[RawPointer] = []
    api.install(lambda *_args: 0, lambda *_args: 0, events.append)
    dll.packet.mouse.x, dll.packet.mouse.y = 7, -2
    message = w.MSG()
    message.hWnd, message.message, message.lParam = 1234, WM_RAW_INPUT, 4321
    dll.messages.append(message)
    assert api.pump()
    assert events[0].point == Point(7, -2)
    assert events[0].mode is MoveMode.RELATIVE_DELTA
    assert dll.calls.count("DefWindowProcW") == 1
    assert not api.pump()
    assert api.close()


def test_changed_registration_preserves_its_new_owner(
    native: tuple[Win32InputPlatform, InputDllFixture],
) -> None:
    """Cleanup does not unregister another component that replaced this source."""
    api, dll = native
    api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
    dll.registrations = [(1, 2, 0x100, 5678)]
    assert api.close()
    assert dll.registrations == [(1, 2, 0x100, 5678)]
    assert dll.window == 0


@pytest.mark.parametrize("invalid", [False, True])
def test_unhook_failure_retains_retry_barrier_unless_handle_already_removed(
    native: tuple[Win32InputPlatform, InputDllFixture], *, invalid: bool
) -> None:
    """Only a confirmed absent hook can be treated as successfully retired."""
    api, dll = native
    api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
    dll.fail = "UnhookWindowsHookEx"
    ctypes.set_last_error(1404 if invalid else 5)
    assert api.close() is invalid
    dll.fail = ""
    assert api.close()


@pytest.mark.parametrize("failure", ["GetPhysicalCursorPos", "GetMessageW"])
def test_failed_native_reads_never_fabricate_cursor_or_messages(
    native: tuple[Win32InputPlatform, InputDllFixture], failure: str
) -> None:
    """Read failures propagate to the worker's cancellation/cleanup boundary."""
    api, dll = native
    ctypes.set_last_error(5)
    if failure == "GetMessageW":
        dll.handlers[failure] = lambda *_args: -1
        with pytest.raises(OSError, match=r"\[WinError 5\]"):
            api.pump()
    else:
        dll.fail = failure
        with pytest.raises(OSError, match=r"\[WinError 5\]"):
            api.cursor()


def test_non_raw_messages_use_default_dispatch_and_unowned_hooks_forward(
    native: tuple[Win32InputPlatform, InputDllFixture],
) -> None:
    """Normal message dispatch and negative hook codes are never swallowed."""
    api, dll = native
    message = w.MSG()
    message.message = 0x113
    dll.messages.append(message)
    assert api.pump()
    assert "TranslateMessage" in dll.calls
    assert "DispatchMessageW" in dll.calls
    assert api.forward(-1, 0x100, 0) == 1
    assert "CallNextHookEx" in dll.calls


@pytest.mark.parametrize("failure", ["OpenInputDesktop", "GetUserObjectInformationW"])
def test_unavailable_desktop_never_becomes_capture_capability(
    native: tuple[Win32InputPlatform, InputDllFixture], failure: str
) -> None:
    """A failed read refuses ownership without changing the input desktop."""
    api, dll = native
    dll.fail = failure
    assert not api.desktop_safe()


@pytest.mark.parametrize(
    "kind",
    [
        "registration-query",
        "registration-read",
        "packet-size",
        "packet-kind",
        "packet-read",
        "busy",
    ],
)
def test_raw_transport_refuses_inconsistent_or_busy_sources(
    native: tuple[Win32InputPlatform, InputDllFixture], kind: str
) -> None:
    """Metadata races and malformed packets never become selection coordinates."""
    api, dll = native
    if kind.startswith("registration"):
        if kind == "registration-read":
            dll.registrations = [(1, 6, 0, 0)]
        original = dll.handlers["GetRegisteredRawInputDevices"]

        def unavailable(*args: object) -> object:
            if args[0] is not None or kind == "registration-query":
                return 0xFFFFFFFF
            return original(*args)

        dll.handlers["GetRegisteredRawInputDevices"] = unavailable
        with pytest.raises(PlatformError, match="registration"):
            api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
        return
    if kind == "busy":
        dll.peek_flood = True
        with pytest.raises(PlatformError, match="busy"):
            api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
        assert dll.calls.count("DefWindowProcW") == 256
        return
    api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
    if kind == "packet-size":
        dll.fail = "GetRawInputData"
    elif kind == "packet-kind":
        dll.packet.header.kind = 1
    else:
        original = dll.handlers["GetRawInputData"]

        def changed(*args: object) -> object:
            return 0xFFFFFFFF if args[2] is not None else original(*args)

        dll.handlers["GetRawInputData"] = changed
    message = w.MSG()
    message.hWnd, message.message, message.lParam = 1234, WM_RAW_INPUT, 4321
    dll.messages.append(message)
    with pytest.raises(PlatformError, match="report"):
        api.pump()
    assert dll.calls.count("DefWindowProcW") == 1


@pytest.mark.parametrize("failure", ["RegisterRawInputDevices", "DestroyWindow"])
def test_raw_cleanup_failure_requires_safe_retry(
    native: tuple[Win32InputPlatform, InputDllFixture], failure: str
) -> None:
    """Failed owned-resource retirement cannot be acknowledged as success."""
    api, dll = native
    api.install(lambda *_args: 0, lambda *_args: 0, lambda _event: None)
    dll.fail = failure
    assert not api.close()
    dll.fail = ""
    assert api.close()
