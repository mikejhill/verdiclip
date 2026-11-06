"""One causally armed Qt-owner crash in an exact multiprocessing-spawn child."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import multiprocessing
import os
import sys
import time
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

import PySide6
from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication
from tests.capture.hosted_fixture_input import require_hosted
from tests.capture.native_cursor import CursorInfo

from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.native_pointer import PointerPhase
from verdiclip.capture.service import CaptureService

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from verdiclip.capture.overlay import WindowTarget


class Wire(Protocol):
    """Only bounded synchronous pipe operations are used."""

    def send_bytes(self, value: bytes, /) -> None:
        """Write one fixed message."""

    def recv_bytes(self, maxlength: int, /) -> bytes:
        """Read at most the explicit bound."""

    def poll(self, timeout: float = 0.0, /) -> bool:
        """Observe arrival without waiting for a favorable value."""

    def close(self) -> None:
        """Close the owned endpoint."""


def runtime() -> dict[str, str]:
    """Private runtime comparison only; never emit these paths in public receipts."""
    return {
        "executable": sys.executable,
        "base": str(vars(sys)["_base_executable"]),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "version": sys.version,
        "pyside": PySide6.__version__,
        "pyside_file": PySide6.__file__,
        "qt": qVersion(),
    }


def send(pipe: Wire, value: dict[str, object]) -> None:
    """Small synchronous bytes, no background feeder or pickle on received data."""
    encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 4096:
        raise RuntimeError("crash-message-size")
    pipe.send_bytes(encoded)


def unique(fields: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate keys rather than accepting a last-value override."""
    result: dict[str, object] = {}
    for key, value in fields:
        if key in result:
            raise ValueError("crash-message-duplicate")
        result[key] = value
    return result


def receive(pipe: Wire, deadline: float) -> dict[str, object]:
    """Arrival only, within one absolute deadline and fixed byte bound."""
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not pipe.poll(remaining):
        raise RuntimeError("crash-message-timeout")
    value = json.loads(pipe.recv_bytes(4096), object_pairs_hook=unique)
    if time.monotonic() > deadline or not isinstance(value, dict):
        raise RuntimeError("crash-message-unavailable")
    return cast("dict[str, object]", value)


class NoTargets:
    """Region capture requires no enumeration of unrelated window metadata."""

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[WindowTarget]:
        """Return no candidates."""
        del exclude
        return ()

    def foreground(self) -> WindowTarget | None:
        """No target metadata is needed by this region-only fixture."""
        return None


def eligible(
    service: CaptureService,
    is_window: Callable[[int], bool],
    cross_matches: Callable[[], bool],
) -> bool:
    """Fresh live owner-thread evidence, not a parent's earlier admission receipt."""
    pointer = service._native_pointer
    return bool(
        pointer is not None
        and pointer.route.phase is PointerPhase.OWNED
        and service.is_selecting
        and service.overlays
        and all(is_window(int(item.winId())) for item in service.overlays)
        and pointer.safe()
        and cross_matches()
    )


def child(pipe: Wire, nonce: str) -> None:  # noqa: PLR0915 - one finite owner lifetime
    """Construct Qt only after parent accepts exact PID/runtime identity."""
    require_hosted(os.environ)
    send(
        pipe,
        {"phase": "ready", "nonce": nonce, "pid": os.getpid(), "runtime": runtime()},
    )
    command = receive(pipe, time.monotonic() + 5)
    if command != {"phase": "start", "nonce": nonce}:
        raise RuntimeError("crash-start-refused")
    app = QApplication([])
    source = MssScreenSource()
    service = CaptureService(source, NoTargets(), show_magnifier=False)
    service.enable_native_pointer()
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes, user.IsWindow.restype = [w.HWND], w.BOOL
    user.LoadImageW.argtypes = [
        w.HINSTANCE,
        ctypes.c_void_p,
        w.UINT,
        ctypes.c_int,
        ctypes.c_int,
        w.UINT,
    ]
    user.LoadImageW.restype = w.HANDLE
    user.GetCursorInfo.argtypes, user.GetCursorInfo.restype = (
        [ctypes.POINTER(CursorInfo)],
        w.BOOL,
    )
    expected_cross = int(user.LoadImageW(None, 32515, 2, 0, 0, 0x8040) or 0)

    def cross_matches() -> bool:
        info = CursorInfo()
        info.size = ctypes.sizeof(info)
        return bool(
            expected_cross
            and user.GetCursorInfo(ctypes.byref(info))
            and info.flags == 1
            and int(info.cursor or 0) == expected_cross
        )

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], w.HANDLE
    kernel.TerminateProcess.argtypes, kernel.TerminateProcess.restype = (
        [w.HANDLE, w.UINT],
        w.BOOL,
    )
    deadline = time.monotonic() + 5
    admitted = False
    failed = False

    def tick() -> None:
        nonlocal admitted, failed
        try:
            if time.monotonic() > deadline:
                raise RuntimeError("crash-owner-timeout")  # noqa: TRY301 - fixed callback failure
            if not admitted and eligible(service, user.IsWindow, cross_matches):
                admitted = True
                send(
                    pipe,
                    {
                        "phase": "admitted",
                        "nonce": nonce,
                        "handles": [int(item.winId()) for item in service.overlays],
                    },
                )
            if pipe.poll():
                command = receive(pipe, deadline)
                if command != {"phase": "crash", "nonce": nonce} or not eligible(
                    service, user.IsWindow, cross_matches
                ):
                    raise RuntimeError("crash-not-eligible")  # noqa: TRY301 - fixed callback failure
                send(pipe, {"phase": "crash-entry", "nonce": nonce})
                kernel.TerminateProcess(kernel.GetCurrentProcess(), 97)
                raise RuntimeError("crash-termination-returned")  # noqa: TRY301 - fixed callback failure
        except (OSError, ValueError, RuntimeError, TypeError):
            failed = True
            timer.stop()
            app.quit()

    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(tick)
    try:
        service.capture_region()
        timer.start()
        app.exec()
    finally:
        timer.stop()
        try:
            service.cancel()
        finally:
            try:
                source.close()
            finally:
                pipe.close()
    if failed:
        raise RuntimeError("owned-crash-incomplete")


class CrashChild:
    """Retain the exact Windows process handle through startup, crash and cleanup."""

    def __init__(self) -> None:
        self.nonce = uuid4().hex
        context = multiprocessing.get_context("spawn")
        self.pipe, self.child_pipe = context.Pipe(duplex=True)
        self.process = context.Process(target=child, args=(self.child_pipe, self.nonce))
        self.started = False
        self.handles: tuple[int, ...] = ()

    def start(self) -> None:
        """Attest before authorizing the child to construct any owned surface."""
        self.process.start()
        self.started = True
        self.child_pipe.close()
        value = receive(self.pipe, time.monotonic() + 5)
        expected = {
            "phase": "ready",
            "nonce": self.nonce,
            "pid": self.process.pid,
            "runtime": runtime(),
        }
        if value != expected:
            raise RuntimeError("crash-runtime-attestation-failed")
        send(self.pipe, {"phase": "start", "nonce": self.nonce})

    def crash(self) -> None:
        """No repeated commands; exact causal marker and exit97 are mandatory."""
        deadline = time.monotonic() + 5
        admission = receive(self.pipe, deadline)
        handles = admission.get("handles")
        if (
            set(admission) != {"phase", "nonce", "handles"}
            or admission["phase"] != "admitted"
            or admission["nonce"] != self.nonce
            or not isinstance(handles, list)
            or not 1 <= len(handles) <= 16
            or any(type(item) is not int or item <= 0 for item in handles)
        ):
            raise RuntimeError("crash-admission-unavailable")
        self.handles = tuple(cast("list[int]", handles))
        send(self.pipe, {"phase": "crash", "nonce": self.nonce})
        if receive(self.pipe, deadline) != {
            "phase": "crash-entry",
            "nonce": self.nonce,
        }:
            raise RuntimeError("crash-entry-unavailable")
        self.process.join(max(0, deadline - time.monotonic()))
        if self.process.is_alive() or self.process.exitcode != 97:
            raise RuntimeError("crash-exit-unconfirmed")

    def close(self) -> None:
        """Failure retirement never qualifies as the causally armed crash."""
        try:
            if self.started or self.process.pid is not None:
                try:
                    if self.process.is_alive():
                        self.process.terminate()
                finally:
                    self.process.join(2)
                if self.process.is_alive():
                    raise RuntimeError("crash-child-cleanup-unconfirmed")
                self.process.close()
        finally:
            try:
                self.pipe.close()
            finally:
                self.child_pipe.close()
