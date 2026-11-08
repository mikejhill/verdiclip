"""Default native provider through actual channel/evidence, replacing OS ABI only."""

from __future__ import annotations

# ruff: noqa: ANN401 -- SDK pointers and COM function signatures are dynamic seams.
import ctypes as c
import hashlib
import json
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any, override

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from tests.capture.moving_wire_fixtures import make_begin
from tests.capture.test_moving_edit_native_extraction import Harness
from tests.capture.test_moving_edit_provider import CaptureSource
from tests.platform.test_moving_edit_channel_lifecycle import Bridge
from tests.platform.test_sequence_channel import MemoryServer

from verdiclip.capture import (
    edit_native,
    edit_scroll_provider,
    observe_native,
    sequence_credit,
)
from verdiclip.capture.edit_scroll_provider import MovingProvider, NativeBoundary
from verdiclip.capture.moving_edit_frame import PeerPixelSlots
from verdiclip.capture.observe_native import FileTime, MonitorInfo, Point, Rect
from verdiclip.capture.observe_protocol import ObserveTarget
from verdiclip.platform import moving_edit_channel as channel
from verdiclip.platform import moving_edit_peer as peer
from verdiclip.platform import moving_edit_protocol as wire
from verdiclip.platform.moving_edit_runtime import MovingEditRuntimeManifest


class NativeFunction:
    """A callable DLL/vtable ABI entry retaining production function names."""

    def __init__(self, abi: NativeABI, name: str, action: Callable[..., Any]) -> None:
        self.abi, self.__name__, self.action = abi, name, action
        self.argtypes: list[object] = []
        self.restype: object = None

    def __call__(self, *args: Any) -> Any:
        """Record one native dispatch with its unmodified function name."""
        self.abi.calls.append(self.__name__)
        return self.action(*args)


class NativeLibrary:
    """Resolve native imports without replacing any higher-level adapter method."""

    def __init__(self, abi: NativeABI, name: str) -> None:
        self.abi, self.name = abi, name
        self.functions: dict[str, NativeFunction] = {}

    def __getattr__(self, name: str) -> NativeFunction:
        """Resolve a fixed imported native function."""
        if name not in self.functions:
            self.functions[name] = NativeFunction(
                self.abi, name, self.abi.function(name)
            )
        return self.functions[name]


class SharedClockHarness(Harness):
    """Reuse real SDK scalar fixtures while sharing the lifecycle's exact clock."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, now: list[int]) -> None:
        self.clock = now
        original = now[0]
        super().__init__(monkeypatch)
        now[0] = original

    @property
    def now(self) -> float:
        """Read the actual shared monotonic timestamp."""
        return self.clock[0] / 1e9

    @now.setter
    def now(self, value: float) -> None:
        self.clock[0] = round(value * 1e9)


class NativeABI:
    """Actual DLL/COM call outputs with retained BSTR/SAFEARRAY ownership."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, now: list[int]) -> None:
        self.harness = SharedClockHarness(monkeypatch, now)
        self.calls: list[str] = []
        self.com_calls: list[tuple[int, int, tuple[str, ...]]] = []
        self.releases: list[int] = []
        self.arrays: dict[int, tuple[int, ...]] = {}
        self.strings: dict[int, Any] = {}
        self.next_array = 5000
        self.closed_processes: list[int] = []
        self.apartment_closes = 0
        self.source = CaptureSource(self.harness)
        self.libraries: dict[str, NativeLibrary] = {}
        monkeypatch.setattr(c, "WinDLL", self.library, raising=False)
        monkeypatch.setattr(c, "WINFUNCTYPE", self.callback_type, raising=False)
        monkeypatch.setattr(c, "get_last_error", lambda: 0, raising=False)
        monkeypatch.setattr(c, "set_last_error", lambda _value: None, raising=False)
        monkeypatch.setattr(
            c, "WinError", lambda code=0: OSError(code, "fake ABI"), raising=False
        )
        monkeypatch.setattr(edit_native.sys, "platform", "win32")
        monkeypatch.setattr(observe_native._Com, "_function", self.com_function)
        monkeypatch.setattr(edit_scroll_provider.mss, "MSS", self.open_capture)
        monkeypatch.setattr(edit_scroll_provider.time, "monotonic_ns", lambda: now[0])
        monkeypatch.setattr(edit_scroll_provider.time, "sleep", self.sleep)

    def open_capture(self, **kwargs: Any) -> CaptureSource:
        """Require the actual fixed MSS constructor options."""
        assert kwargs == {"with_cursor": False}
        return self.source

    def sleep(self, seconds: float) -> None:
        """Consume quiet sampling waits inside the unchanged two-second bound."""
        self.harness.now += seconds + 0.000001

    def library(self, name: str, **_kwargs: Any) -> NativeLibrary:
        """Resolve only the five DLLs imported by the real owned provider."""
        assert name in {"kernel32", "user32", "dwmapi", "ole32", "oleaut32"}
        if name not in self.libraries:
            self.libraries[name] = NativeLibrary(self, name)
        return self.libraries[name]

    @staticmethod
    def callback_type(_result: object, *_arguments: object) -> Callable[..., Any]:
        """Preserve callback execution while replacing ctypes' Windows trampoline."""
        return lambda callback: callback

    @staticmethod
    def pointer(output: Any, value: int) -> int:
        """Write a pointer output exactly where the caller supplied it."""
        c.cast(output, c.POINTER(c.c_void_p)).contents.value = value
        return 0

    def function(self, name: str) -> Callable[..., Any]:
        """Keep unknown invoked APIs fatal instead of returning permissive values."""
        h = self.harness
        functions: dict[str, Callable[..., Any]] = {
            "OpenProcess": lambda *_args: 123,
            "CloseHandle": self.close_process,
            "WaitForSingleObject": lambda *_args: 258,
            "GetProcessTimes": self.process_times,
            "SetProcessDpiAwarenessContext": lambda *_args: 1,
            "GetThreadDpiAwarenessContext": lambda: -4,
            "AreDpiAwarenessContextsEqual": lambda *_args: 1,
            "GetDpiForWindow": lambda _hwnd: 96,
            "GetWindowThreadProcessId": h.pid,
            "GetForegroundWindow": lambda: 100,
            "GetAncestor": lambda *_args: 100,
            "GetParent": lambda _hwnd: 100,
            "WindowFromPoint": lambda _point: 101,
            "GetClientRect": self.client_rect,
            "ClientToScreen": self.to_screen,
            "GetWindowRect": h.window_rect,
            "GetClassNameW": h.class_name,
            "GetWindowLongPtrW": lambda _hwnd, index: h.style if index == -16 else 0,
            "GetGUIThreadInfo": h.gui,
            "GetScrollBarInfo": h.bar,
            "GetScrollInfo": h.scroll,
            "GetLastInputInfo": h.last_input,
            "SendMessageTimeoutW": h.send,
            "GetTopWindow": lambda _parent: 100,
            "GetWindow": lambda *_args: 0,
            "IsWindowVisible": lambda _hwnd: 1,
            "IsIconic": lambda _hwnd: 0,
            "EnumChildWindows": lambda _root, callback, data: callback(101, data),
            "EnumDisplayMonitors": lambda _dc, _rect, callback, data: callback(
                1, 0, None, data
            ),
            "GetMonitorInfoW": self.monitor,
            "DwmGetWindowAttribute": self.cloaked,
            "CoInitializeEx": lambda *_args: 0,
            "CoUninitialize": self.close_apartment,
            "CoCreateInstance": lambda *_args: self.pointer(_args[-1], 1003),
            "SysStringLen": lambda pointer: len(self.strings[pointer.value].value),
            "SysFreeString": lambda pointer: self.strings.pop(pointer.value),
            "SafeArrayGetDim": lambda _pointer: 1,
            "SafeArrayGetElemsize": lambda _pointer: 4,
            "SafeArrayGetVartype": lambda _pointer, output: self.scalar(
                output, c.c_uint16, 3
            ),
            "SafeArrayGetLBound": lambda _pointer, _dim, output: self.scalar(
                output, c.c_int32, 0
            ),
            "SafeArrayGetUBound": lambda pointer, _dim, output: self.scalar(
                output, c.c_int32, len(self.arrays[pointer.value]) - 1
            ),
            "SafeArrayGetElement": self.array_element,
            "SafeArrayDestroy": self.destroy_array,
        }
        if name in functions:
            return functions[name]

        def missing(*_args: Any) -> Any:
            raise AssertionError(f"Unmodelled native API: {name}")

        return missing

    @staticmethod
    def scalar(output: Any, kind: Any, value: Any) -> int:
        """Write one explicitly sized SDK scalar output."""
        c.cast(output, c.POINTER(kind)).contents.value = value
        return 0

    def process_times(self, _handle: int, created: Any, *_rest: Any) -> int:
        """Keep the pinned creation identity through every actual process guard."""
        c.cast(created, c.POINTER(FileTime)).contents.low = 12345
        return 1

    def close_process(self, handle: int) -> int:
        """Track exact native ownership release, without clearing previous evidence."""
        self.closed_processes.append(handle)
        return 1

    def close_apartment(self) -> None:
        """Track matching COM apartment retirement."""
        self.apartment_closes += 1

    def client_rect(self, hwnd: int, output: Any) -> int:
        """Supply actual distinct root and EDIT local coordinate systems."""
        if hwnd == 101:
            return self.harness.client_rect(hwnd, output)
        rect = c.cast(output, c.POINTER(Rect)).contents
        rect.left, rect.top, rect.right, rect.bottom = 0, 0, 533, 661
        return 1

    def to_screen(self, hwnd: int, output: Any) -> int:
        """Preserve all four actual native client-origin mapping calls."""
        if hwnd == 101:
            return self.harness.to_screen(hwnd, output)
        point = c.cast(output, c.POINTER(Point)).contents
        point.x += 40
        point.y += 40
        return 1

    @staticmethod
    def monitor(_handle: int, output: Any) -> int:
        """Fill the complete SDK monitor metadata consumed by the real producer."""
        info = c.cast(output, c.POINTER(MonitorInfo)).contents
        info.monitor.left, info.monitor.top = 0, 0
        info.monitor.right, info.monitor.bottom = 1024, 768
        info.flags = 1
        return 1

    def cloaked(self, _hwnd: int, _attribute: int, output: Any, _size: int) -> int:
        """Fill the actual DWORD cloaking result."""
        return self.scalar(output, c.c_uint32, 0)

    def array_element(self, pointer: Any, index: Any, output: Any) -> int:
        """Copy bounded runtime-ID members through real SAFEARRAY output buffers."""
        position = c.cast(index, c.POINTER(c.c_int32)).contents.value
        return self.scalar(output, c.c_int32, self.arrays[pointer.value][position])

    def destroy_array(self, pointer: Any) -> int:
        """Require each temporary SAFEARRAY to be destroyed exactly once."""
        del self.arrays[pointer.value]
        return 0

    def com_function(
        self, slot: int, result: Any, arguments: tuple[Any, ...]
    ) -> NativeFunction:
        """Replace only the resolved vtable function, preserving _Com calls/owners."""
        del result

        def invoke(pointer: Any, *args: Any) -> int:
            interface = pointer.value
            self.com_calls.append(
                (interface, slot, tuple(str(kind) for kind in arguments))
            )
            return self.com_dispatch(interface, slot, args)

        return NativeFunction(self, "COM", invoke)

    def com_dispatch(self, interface: int, slot: int, args: tuple[Any, ...]) -> int:  # noqa: C901, PLR0911, PLR0912 - explicit fixed SDK vtable dispatch
        """Model only the actual fixed UIA slots, with retained output lifetimes."""
        if slot == 2:
            self.releases.append(interface)
            return 0
        if interface == 1003:
            if slot == 3:
                return self.scalar(
                    args[-1], c.c_int32, int(args[0].value == args[1].value)
                )
            if slot in (6, 7, 16):
                value = (
                    1004
                    if slot == 16
                    else 1001
                    if slot == 6 and args[0] == 100
                    else 1002
                )
                return self.pointer(args[-1], value)
        elif interface == 1004 and slot == 3:
            return self.pointer(args[-1], 1001)
        elif interface == 1005:
            if slot == 4:
                self.harness.writes.append(args)
                self.harness.first = round(float(args[1]) * 13 / 100)
                self.harness.now += self.harness.seek_duration
                return self.harness.seek_status
            if 5 <= slot <= 10:
                kind = c.c_double if slot < 9 else c.c_int32
                return self.scalar(
                    args[-1], kind, self.harness.pattern_read(slot, kind).value
                )
        elif interface in (1001, 1002):
            if slot == 4:
                self.next_array += 1
                self.arrays[self.next_array] = (1,) if interface == 1001 else (1, 2)
                return self.pointer(args[-1], self.next_array)
            if slot == 14:
                return self.pointer(args[-1], 1005)
            if slot in (20, 36, 38):
                kind = c.c_void_p if slot == 36 else c.c_int32
                value = (
                    99
                    if slot == 20
                    else 0
                    if slot == 38
                    else 100
                    if interface == 1001
                    else 101
                )
                return self.scalar(args[-1], kind, value)
            if slot == 40:
                buffer = c.create_unicode_buffer("Win32")
                address = c.addressof(buffer)
                self.strings[address] = buffer
                return self.pointer(args[-1], address)
            if slot == 43:
                rect = c.cast(args[-1], c.POINTER(Rect)).contents
                coordinates = (
                    (40, 40, 573, 701) if interface == 1001 else (60, 60, 570, 473)
                )
                rect.left, rect.top, rect.right, rect.bottom = coordinates
                return 0
        raise AssertionError(f"Unmodelled COM interface/slot: {interface}/{slot}")


class DefaultBridge(Bridge):
    """Use real NativeBoundary construction and every default native provider method."""

    abi: NativeABI
    native_boundary: NativeBoundary

    @override
    def _create_provider(self) -> None:
        # Construction must follow independent context and peer hello ACKs.
        pass

    @override
    def begin(self) -> None:
        value = make_begin()
        target = ObserveTarget(99, 12345, 100, (80, 80))
        self.facade.begin(1, target, value["profile_evidence"])
        request = self.outgoing()
        self.run_peer(lambda: self._begin_native(request, target, value))

    def _begin_native(
        self, request: wire.Message, target: ObserveTarget, value: dict[str, Any]
    ) -> None:
        self.protocol.begin(request)
        assert self.protocol.clock is not None
        slots = PeerPixelSlots()
        journal = self.evidence.journal
        self.native_boundary = NativeBoundary(
            target,
            value["profile_evidence"],
            self.protocol.clock.deadline_ns,
            self.control.checkpoint,
            slots,
            journal,
            evidence_emit=journal.emit,
            evidence_controller=journal,
            phase_deadline_ns=journal.deadline_ns,
        )
        self.provider = MovingProvider(self.native_boundary, slots)
        reply = self.protocol.bind_provider(self.provider, self.control.checkpoint)
        self.on_owner(lambda: self.deliver(reply))

    @override
    def move(self, step: int) -> wire.Message:
        """Retain native constructor/seek/snapshot affinity on one peer thread."""
        self.facade.move_next(2 * step)
        request = self.outgoing()

        def moved() -> wire.Message:
            reply = self.protocol.move(request, self.control.checkpoint)
            self.on_owner(lambda: self.deliver(reply))
            self.protocol.move_sent(self.control.checkpoint)
            return reply

        reply = self.run_peer(moved)
        assert isinstance(reply, wire.Message)
        return reply

    @override
    def complete(self) -> None:
        """Execute the actual provider lifecycle on the retained native owner thread."""
        self.begin()
        for step in range(1, 8):
            self.move(step)
            self.observe(step)
        self.facade.finish()
        request = self.outgoing()

        def finished() -> None:
            reply = self.protocol.finish(request, self.control.checkpoint)
            self.on_owner(lambda: self.deliver(reply))
            self._finish_native()

        self.run_peer(finished)

    @override
    def finish_cleanup(self) -> None:
        """Run resource releases on the original constructor's native thread."""
        self.run_peer(self._finish_native)

    def _finish_native(self) -> None:
        self.native_boundary.set_deadline(self.evidence.journal.deadline_ns)
        self.native_boundary.close()
        self.evidence.journal.phase_end()
        self.evidence.journal.finish()


@pytest.fixture
def default_bridge(
    qapp: QCoreApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[DefaultBridge]:
    """Keep actual worker/peer/evidence state while replacing native platform I/O."""
    now = [1_000_000_000]
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(channel.time, "monotonic", lambda: now[0] / 1e9)
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(channel._Worker, "moveToThread", lambda *_: None)
    monkeypatch.setattr(channel, "QLocalServer", MemoryServer)
    runtime = MovingEditRuntimeManifest(
        str(tmp_path / "python.exe"),
        (str(tmp_path),),
        tuple(str(tmp_path / f"module{i}.py") for i in range(23)),
        ("0.13.3", "10.2.0"),
        (3, 13, 1),
        64,
    )
    lifetime = channel._Lifetime(
        1, runtime, threading.Event(), wire.EvidenceBinding("c" * 32, 30, 300)
    )
    lifetime.expiry_timer.stop()
    value = DefaultBridge(lifetime, now, monkeypatch)
    value.abi = NativeABI(monkeypatch, now)
    assert value.worker.thread() is qapp.thread()
    monkeypatch.setattr(peer, "_MovingEditPipe", value.frame_factory)
    yield value
    value.worker._close_evidence()
    value.evidence.journal.close()
    if hasattr(value, "native_boundary"):
        with suppress(Exception):
            value.run_peer(value.native_boundary.close)
    if hasattr(value, "provider"):
        value.peer_pool.submit(value.provider.cancel).result(timeout=10)
    value.peer_pool.shutdown(wait=True)
    value.worker._discard_pixels()
    value.worker.helper = None
    lifetime._missing_receipt = False
    if lifetime.receipt is None:
        lifetime._retired(1, b'{"passed":false}')
    if not lifetime.stopped:
        lifetime._stopped()
    lifetime._release_if_disposed()
    if shiboken6.isValid(value.facade):
        shiboken6.delete(value.facade)
    if shiboken6.isValid(value.worker):
        value.worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert lifetime not in channel._RETAINED


def test_default_provider_complete_through_real_evidence_and_collector(
    default_bridge: DefaultBridge,
    tmp_path: Path,
) -> None:
    """Every actual default call must fit its independently authenticated context."""
    value = default_bridge
    value.complete()
    assert value.worker.failure is None
    assert value.provider.accepted == value.protocol.frames == 7
    assert len(value.abi.harness.writes) == 7
    assert value.abi.source.grabs == 21
    assert value.abi.source.closed == 1
    assert value.abi.closed_processes == [123]
    assert value.abi.apartment_closes == 1
    assert not value.abi.arrays
    assert not value.abi.strings
    assert value.evidence.reader.complete
    assert value.evidence.reader.correlated_replay_verified
    assert value.worker.sequence_candidate is not None
    assert value.worker.sequence_candidate.assembly.rgb == b"".join(
        bytes([row % 256, row // 256, 71]) * 493 for row in range(621)
    )
    assert Counter(value.abi.calls)["SendMessageTimeoutW"] > 100
    first_call = next(
        packet for kind, packet in value.evidence.events if kind == "call_start"
    )
    begin_context = value.evidence.parent_packets[0]
    assert first_call["name"] == "SetProcessDpiAwarenessContext"
    assert first_call["effective_deadline_ns"] == begin_context["deadline_ns"]
    assert (
        begin_context["deadline_ns"] == value.requests[0].payload["command_deadline_ns"]
    )
    assert value.evidence.reader.count < wire.MAX_EVIDENCE_RECORDS
    assert value.evidence.reader.byte_count < wire.MAX_EVIDENCE_BYTES
    parent_raw = value.evidence.parent_raw.getvalue()
    assert len(value.evidence.parent_packets) == wire.MAX_PARENT_EVIDENCE_RECORDS
    assert value.evidence.reader.parent_count == wire.MAX_PARENT_EVIDENCE_RECORDS
    assert value.evidence.reader.parent_byte_count == len(parent_raw)
    assert (
        value.evidence.reader.parent_hexdigest == hashlib.sha256(parent_raw).hexdigest()
    )
    assert len(parent_raw) <= (
        wire.MAX_PARENT_EVIDENCE_RECORDS * wire.MAX_PARENT_EVIDENCE_RECORD_BYTES
    )
    assert json.loads(value.seal())["passed"]
    artifact = tmp_path / "default-provider-composed-summary.json"
    artifact.write_text(
        json.dumps(
            {
                "qualification": "portable fake ABI; not native Windows qualification",
                "expected_identity": value.evidence.reader.expected.value,
                "reader": value.evidence.reader.status,
                "parent_raw": {
                    "records": len(value.evidence.parent_packets),
                    "bytes": len(parent_raw),
                    "sha256": hashlib.sha256(parent_raw).hexdigest(),
                },
                "native_calls": dict(Counter(value.abi.calls)),
                "com_signatures": sorted(set(value.abi.com_calls)),
                "accepted_frames": value.provider.accepted,
                "target_writes": len(value.abi.harness.writes),
                "mss_grabs": value.abi.source.grabs,
                "process_closes": value.abi.closed_processes,
                "apartment_closes": value.abi.apartment_closes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (tmp_path / "moving-evidence.raw").write_bytes(value.evidence.raw.getvalue())
    (tmp_path / "moving-evidence-parent.raw").write_bytes(
        value.evidence.parent_raw.getvalue()
    )
    print(f"DEFAULT_PROVIDER_COMPOSED={artifact}")  # noqa: T201 - preserve artifact origin in test stdout


def test_default_provider_inventory_without_transport_qualification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Enumerate real default calls; this collector grants no channel qualification."""
    now = [1_000_000_000]
    abi = NativeABI(monkeypatch, now)
    records: list[tuple[str, dict[str, Any], int]] = []
    current = ["begin"]

    def emit(value: dict[str, Any], *, timestamp_ns: int) -> None:
        records.append((current[0], dict(value), timestamp_ns))

    def check() -> None:
        assert now[0] < 30_000_000_000

    value = make_begin()
    slots = PeerPixelSlots()
    boundary = NativeBoundary(
        ObserveTarget(99, 12345, 100, (80, 80)),
        value["profile_evidence"],
        30_000_000_000,
        check,
        slots,
        lambda _event: None,
        evidence_emit=emit,
        phase_deadline_ns=now[0] + wire.OBSERVE_NS,
    )
    provider = MovingProvider(boundary, slots)
    try:
        provider.begin(check)
        for step in range(1, 8):
            current[0] = f"move{step}"
            boundary.set_deadline(min(now[0] + wire.MOVE_NS, 30_000_000_000))
            provider.move(step, check)
            current[0] = f"observe{step}"
            boundary.set_deadline(min(now[0] + wire.OBSERVE_NS, 30_000_000_000))
            provider.capture(step, check)
            provider.accept(step, check)
        current[0] = "finish-and-cleanup"
        provider.finish(check)
        boundary.set_deadline(min(now[0] + 5_000_000_000, 30_000_000_000))
    finally:
        boundary.close()
    assert len(abi.harness.writes) == 7
    assert abi.source.grabs == 21
    assert abi.source.closed == 1
    assert abi.closed_processes == [123]
    assert abi.apartment_closes == 1
    assert not abi.arrays
    assert not abi.strings
    inventory = {
        "qualification": False,
        "scope": "OS-only default provider inventory; no control/evidence admission",
        "record_count": len(records),
        "call_names": dict(
            Counter(
                event["name"]
                for _, event, _ in records
                if event["event"] == "call_start"
            )
        ),
        "phase_record_counts": dict(Counter(phase for phase, _, _ in records)),
        "native_calls": dict(Counter(abi.calls)),
        "com_signatures": sorted(set(abi.com_calls)),
        "events": records,
    }
    path = tmp_path / "default-provider-inventory.json"
    path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(f"DEFAULT_PROVIDER_INVENTORY={path}")  # noqa: T201 - preserve exact test artifact path in stdout


def test_actual_default_constructor_late_ack_dispatches_no_native_api(
    default_bridge: DefaultBridge,
) -> None:
    """The first real constructor call cannot run at the original begin deadline."""
    value = default_bridge
    value.evidence.delay = "call-ack"
    with pytest.raises(wire.ProtocolError, match=r".+"):
        value.begin()
    assert not value.abi.calls
    assert not value.abi.harness.writes
    assert value.abi.source.grabs == 0
    assert not value.abi.closed_processes
    assert value.evidence.journal.failed
    value.worker._disconnected()
    assert value.worker.failure is not None
    assert value.worker.evidence_client is None


def test_actual_default_failure_releases_resources_without_later_target_write(
    default_bridge: DefaultBridge,
) -> None:
    """Failed evidence blocks target work but cannot skip native resource releases."""
    value = default_bridge
    value.begin()
    value.move(1)
    value.evidence.delay = "call-ack"
    with pytest.raises(wire.ProtocolError, match=r".+"):
        value.observe(1)
    assert len(value.abi.harness.writes) == 1
    assert value.abi.source.grabs == 0
    assert value.provider.accepted == 0
    assert value.evidence.journal.failed
    with pytest.raises((RuntimeError, ValueError), match=r".+"):
        value.run_peer(value.native_boundary.close)
    assert value.abi.source.closed == 1
    assert value.abi.closed_processes == [123]
    assert value.abi.apartment_closes == 1
    assert not value.abi.arrays
    assert not value.abi.strings
    assert len(value.abi.harness.writes) == 1
    value.worker._disconnected()
    assert value.worker.failure is not None
    assert value.worker.evidence_client is None
