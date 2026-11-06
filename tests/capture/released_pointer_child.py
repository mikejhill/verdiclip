"""Isolated 0.12.1 source child on the parent's current Qt runtime.

Only standard-library imports run while multiprocessing resolves this target.
The old capture implementation is imported from the verified separate checkout,
never copied, patched, or replaced with the candidate service. Private paths and
native handles travel only over the owned pipe, never in diagnostic output.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import hashlib
import importlib
import json
import os
import sys
import time
from asyncio import CancelledError
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Never, Protocol, cast

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

_LIMIT = 4096
_TIMEOUT = 5.0
_HASHES = {
    "service.py": "3006a3f1abf5e7cb349674fc01441672975232d199131256cc2e88fcfd58bae8",
    "overlay.py": "d6854656379edd56d334f96b54634a2261405e21d58c3a16d4dc15725481bda8",
}


class Wire(Protocol):
    """A bounded synchronous byte pipe, without pickle, feeders, or read threads."""

    def send_bytes(self, value: bytes, /) -> None:
        """Send one small private frame."""

    def recv_bytes(self, maxlength: int, /) -> bytes:
        """Read at most the explicit byte bound."""

    def poll(self, timeout: float = 0.0, /) -> bool:
        """Wait only for frame arrival."""

    def close(self) -> None:
        """Close this process's endpoint."""


def send(pipe: Wire, value: dict[str, object]) -> None:
    """Send a small JSON frame synchronously."""
    encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > _LIMIT:
        raise RuntimeError("baseline-message-size")
    pipe.send_bytes(encoded)


def _unique(fields: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in fields:
        if key in result:
            raise ValueError("baseline-message-duplicate")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> Never:
    raise ValueError("baseline-message-constant")


def receive(pipe: Wire, deadline: float) -> dict[str, object]:
    """Bound arrival and decoding by one absolute deadline and 4096 bytes."""
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not pipe.poll(remaining):
        raise RuntimeError("baseline-message-timeout")
    value = json.loads(
        pipe.recv_bytes(_LIMIT),
        object_pairs_hook=_unique,
        parse_constant=_invalid_constant,
    )
    if time.monotonic() > deadline or not isinstance(value, dict):
        raise RuntimeError("baseline-message-unavailable")
    return cast("dict[str, object]", value)


def runtime() -> dict[str, str]:
    """Return private identity fields without constructing any Qt application."""
    import PySide6  # noqa: PLC0415 - keep the spawn target's import graph isolated
    from PySide6.QtCore import qVersion  # noqa: PLC0415

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


def require_hosted(environment: Mapping[str, str]) -> None:
    """Fail closed outside the explicitly opted-in disposable hosted source job."""
    required = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Windows",
        "RUNNER_ARCH": "X64",
        "GITHUB_REPOSITORY": "mikejhill/verdiclip",
        "VERDICLIP_HOSTED_SCROLLBAR": "1",
        "VERDICLIP_RELEASED_BASELINE": "1",
        "QT_QPA_PLATFORM": "windows",
    }
    head = environment.get("VERDICLIP_QUALIFICATION_HEAD", "")
    if (
        any(environment.get(key) != value for key, value in required.items())
        or len(head) != 40
        or any(c not in "0123456789abcdef" for c in head)
        or environment.get("VERDICLIP_HOSTED_SOURCE") != head
        or sys.platform != "win32"
    ):
        raise RuntimeError("baseline-hosted-source-required")


def _is_product(name: str) -> bool:
    return name == "verdiclip" or name.startswith("verdiclip.")


def verify_source(root: str) -> Path:
    """Reject preloaded product modules and verify the two actual release files."""
    if any(_is_product(name) for name in sys.modules):
        raise RuntimeError("baseline-product-preloaded")
    requested = Path(root)
    if not requested.is_absolute():
        raise RuntimeError("baseline-path-invalid")
    checkout = requested.resolve(strict=True)
    source = (checkout / "src").resolve(strict=True)
    if source != checkout / "src" or not source.is_dir():
        raise RuntimeError("baseline-source-path-invalid")
    for name, expected in _HASHES.items():
        requested_file = source / "verdiclip" / "capture" / name
        actual = requested_file.resolve(strict=True)
        if (
            actual != requested_file
            or hashlib.sha256(actual.read_bytes()).hexdigest() != expected
        ):
            raise RuntimeError("baseline-source-mismatch")
    return source


def verify_origins(source: Path) -> None:
    """Every loaded product module, package path and spec must be baseline-local."""
    found = False
    for name, module in tuple(sys.modules.items()):
        if not _is_product(name):
            continue
        found = True
        origin = getattr(getattr(module, "__spec__", None), "origin", None)
        paths = [getattr(module, "__file__", None), origin]
        paths.extend(getattr(module, "__path__", ()))
        if any(
            not isinstance(path, str)
            or not Path(path).resolve(strict=True).is_relative_to(source)
            for path in paths
        ):
            raise RuntimeError("baseline-module-origin-mismatch")
    if not found:
        raise RuntimeError("baseline-module-origin-unavailable")


class NoTargets:
    """The actual old region service needs no foreign-window metadata."""

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[Never]:
        """Return no candidates without enumerating any external windows."""
        del exclude
        return ()

    def foreground(self) -> None:
        """Region capture does not need a target title or bounds."""
        return


class Overlay(Protocol):
    """Only public old-overlay observations used by the comparison."""

    def winId(self) -> int:  # noqa: N802 - Qt's public native identifier
        """Return this already-visible overlay's native handle."""

    def isVisible(self) -> bool:  # noqa: N802 - Qt's public visibility query
        """Return Qt's visibility state."""


class Source(Protocol):
    """Only explicit source retirement is driven by the harness."""

    def close(self) -> None:
        """Retire the source's owned capture resources."""


class Service(Protocol):
    """Unmodified public 0.12.1 service surface used by the harness."""

    @property
    def is_selecting(self) -> bool:
        """Whether the old service still owns a selection."""

    @property
    def overlays(self) -> tuple[Overlay, ...]:
        """All overlays belonging to the current selection."""

    def capture_region(self) -> None:
        """Run the actual released region-capture path."""

    def cancel(self) -> None:
        """Run the actual released cancellation path."""


class ServiceFactory(Protocol):
    """The old constructor, deliberately without any candidate-only keywords."""

    def __call__(
        self, screens: Source, windows: NoTargets, *, show_magnifier: bool
    ) -> Service:
        """Construct the real old service."""


@dataclass(frozen=True, repr=False)
class Baseline:
    """Verified factories, not a copied or reimplemented released service."""

    screen_source: Callable[[], Source]
    capture_service: ServiceFactory


def load_baseline(root: str) -> Baseline:
    """Prepend verified source before delayed imports, then audit every origin."""
    source = verify_source(root)
    sys.path.insert(0, str(source))
    importlib.invalidate_caches()
    service = importlib.import_module("verdiclip.capture.service")
    grabber = importlib.import_module("verdiclip.capture.grabber")
    verify_origins(source)
    return Baseline(
        cast("Callable[[], Source]", grabber.MssScreenSource),
        cast("ServiceFactory", service.CaptureService),
    )


class CursorInfo(ctypes.Structure):
    """Read-only Win32 CURSORINFO; no cursor writers are exposed here."""

    _fields_ = (
        ("size", w.DWORD),
        ("flags", w.DWORD),
        ("cursor", w.HANDLE),
        ("point", w.POINT),
    )


@dataclass(frozen=True, repr=False)
class Pointer:
    """One private pointer sample, never serialized or logged."""

    showing: bool
    cursor: int
    point: tuple[int, int]
    window: int
    foreground: int


class Native:
    """Read native ownership, plus the single exact self-termination operation."""

    def __init__(self) -> None:
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.IsWindow.argtypes, self.user.IsWindow.restype = [w.HWND], w.BOOL
        self.user.IsWindowVisible.argtypes = [w.HWND]
        self.user.IsWindowVisible.restype = w.BOOL
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetWindowThreadProcessId.restype = w.DWORD
        self.user.GetForegroundWindow.argtypes = []
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.WindowFromPoint.argtypes = [w.POINT]
        self.user.WindowFromPoint.restype = w.HWND
        self.user.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
        self.user.GetCursorInfo.restype = w.BOOL
        self.user.LoadImageW.argtypes = [
            w.HINSTANCE,
            ctypes.c_void_p,
            w.UINT,
            ctypes.c_int,
            ctypes.c_int,
            w.UINT,
        ]
        self.user.LoadImageW.restype = w.HANDLE
        # Match Qt's Windows backend: IMAGE_CURSOR, LR_SHARED | LR_DEFAULTSIZE.
        # This shared handle is not ours to destroy.
        self.cross = int(self.user.LoadImageW(None, 32515, 2, 0, 0, 0x8040) or 0)
        if not self.cross:
            raise RuntimeError("baseline-cross-unavailable")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetCurrentProcess.argtypes = []
        self.kernel.GetCurrentProcess.restype = w.HANDLE
        self.kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        self.kernel.TerminateProcess.restype = w.BOOL

    def is_window(self, handle: int) -> bool:
        """Read liveness without creating or activating a window."""
        return bool(self.user.IsWindow(handle))

    def visible(self, handle: int) -> bool:
        """Read native visibility."""
        return bool(self.user.IsWindowVisible(handle))

    def process_id(self, handle: int) -> int:
        """Read ownership without opening a foreign process."""
        pid = w.DWORD()
        if not self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid)):
            return 0
        return int(pid.value)

    def pointer(self) -> Pointer:
        """Take one actual visible-cursor and exact pointer-window observation."""
        info = CursorInfo()
        info.size = ctypes.sizeof(info)
        if not self.user.GetCursorInfo(ctypes.byref(info)):
            raise RuntimeError("baseline-pointer-unavailable")
        return Pointer(
            info.flags == 1,
            int(info.cursor or 0),
            (info.point.x, info.point.y),
            int(self.user.WindowFromPoint(info.point) or 0),
            int(self.user.GetForegroundWindow() or 0),
        )

    def terminate(self) -> Never:
        """Terminate only this exact process with the distinctive exit value."""
        self.kernel.TerminateProcess(self.kernel.GetCurrentProcess(), 97)
        raise RuntimeError("baseline-termination-returned")


@dataclass(frozen=True, repr=False)
class Admission:
    """Private HWND proof plus the sole public foreground classification."""

    handles: tuple[int, ...]
    foreground_owned: bool


def eligible(
    service: Service, native: Native, expected_point: tuple[int, int]
) -> Admission | None:
    """Prove real visible CrossCursor over an exact live process-owned overlay."""
    overlays = service.overlays
    if not service.is_selecting or not 1 <= len(overlays) <= 16:
        return None
    if not all(item.isVisible() for item in overlays):
        return None
    handles = tuple(int(item.winId()) for item in overlays)
    if len(set(handles)) != len(handles) or not all(
        handle > 0
        and native.is_window(handle)
        and native.visible(handle)
        and native.process_id(handle) == os.getpid()
        for handle in handles
    ):
        return None
    pointer = native.pointer()
    if (
        not pointer.showing
        or not native.cross
        or pointer.cursor != native.cross
        or pointer.point != expected_point
        or pointer.window not in handles
    ):
        return None
    return Admission(handles, pointer.foreground in handles)


class FailureError(RuntimeError):
    """Fixed diagnostic vocabulary, with no provider text or native identifiers."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__("baseline-child-failed")
        self.stage, self.reason = stage, reason


def _reason(error: Exception) -> str:
    known = {
        "baseline-owner-timeout": "timeout",
        "baseline-message-timeout": "timeout",
        "baseline-owner-proof-changed": "invalid-proof",
        "baseline-command-refused": "invalid-proof",
        "baseline-command-early": "invalid-proof",
        "baseline-start-refused": "invalid-proof",
        "baseline-source-mismatch": "invalid-proof",
        "baseline-product-preloaded": "invalid-proof",
        "baseline-module-origin-mismatch": "invalid-proof",
        "baseline-pointer-unavailable": "unavailable",
        "baseline-cross-unavailable": "unavailable",
        "baseline-termination-returned": "unavailable",
    }
    return (
        known.get(str(error), "unknown") if type(error) is RuntimeError else "unknown"
    )


def _report_failure(pipe: Wire, nonce: str, stage: str, reason: str) -> None:
    try:
        send(
            pipe, {"phase": "failed", "nonce": nonce, "stage": stage, "reason": reason}
        )
    except Exception:  # noqa: BLE001 - best effort; preserve any control exception
        return


class Session:
    """A GUI-thread-only finite protocol; no helper can move or set the pointer."""

    def __init__(  # noqa: PLR0913 - fixed process-local dependencies
        self,
        pipe: Wire,
        nonce: str,
        mode: str,
        expected_point: tuple[int, int],
        *,
        service: Service,
        native: Native,
        quit_app: Callable[[], None],
    ) -> None:
        self.pipe, self.nonce, self.mode = pipe, nonce, mode
        self.point, self.service, self.native = expected_point, service, native
        self.quit_app = quit_app
        self.phase = "admission"
        self.deadline = time.monotonic() + _TIMEOUT
        self.handles: tuple[int, ...] = ()
        self.completed = False
        self.failed = False
        self.failure: FailureError | None = None
        self.control: KeyboardInterrupt | SystemExit | CancelledError | None = None

    def tick(self) -> None:
        """Observe every 10 ms; exceptions become a fixed silent child failure."""
        if self.failed or self.completed:
            return
        try:
            self._advance()
        except (KeyboardInterrupt, SystemExit, CancelledError) as control:
            self.control = control
            self.failed = True
            self.quit_app()
        except Exception as error:  # noqa: BLE001 - expose only fixed diagnostic labels
            self.failure = FailureError(self.phase, _reason(error))
            self.failed = True
            self.quit_app()

    def _advance(self) -> None:
        if time.monotonic() > self.deadline:
            raise RuntimeError("baseline-owner-timeout")
        if self.phase == "admission":
            self._admit()
        elif self.phase == "retirement":
            self._retire()
        if self.pipe.poll():
            command = receive(self.pipe, self.deadline)
            if command != {"phase": self._command(), "nonce": self.nonce}:
                raise RuntimeError("baseline-command-refused")
            if self.phase == "action":
                self._act()
            elif self.phase == "finish":
                self.completed = True
                self.quit_app()
            else:
                raise RuntimeError("baseline-command-early")

    def _command(self) -> str:
        return "finish" if self.phase == "finish" else "action"

    def _admit(self) -> None:
        proof = eligible(self.service, self.native, self.point)
        if proof is None:
            return
        self.handles = proof.handles
        send(
            self.pipe,
            {
                "phase": "admitted",
                "nonce": self.nonce,
                "handles": list(proof.handles),
                "foreground_owned": proof.foreground_owned,
            },
        )
        self.phase = "action"
        self.deadline = time.monotonic() + _TIMEOUT

    def _act(self) -> None:
        proof = eligible(self.service, self.native, self.point)
        if proof is None or proof.handles != self.handles:
            raise RuntimeError("baseline-owner-proof-changed")
        if self.mode == "crash":
            send(self.pipe, {"phase": "crash-entry", "nonce": self.nonce})
            self.native.terminate()
        else:
            self.service.cancel()
            self.phase = "retirement"
            self.deadline = time.monotonic() + _TIMEOUT

    def _retire(self) -> None:
        # Do not poll for favorable cursor/foreground restoration. Only wait
        # until the old service has finished and every admitted HWND is gone.
        if self.service.is_selecting or any(
            self.native.is_window(handle) for handle in self.handles
        ):
            return
        send(self.pipe, {"phase": "retired", "nonce": self.nonce})
        self.phase = "finish"
        self.deadline = time.monotonic() + _TIMEOUT


def _cleanup(actions: Sequence[Callable[[], None]]) -> None:
    """Try every cleanup, preserving a pending failure or control exception."""
    primary = sys.exception()
    errors: list[BaseException] = []
    for action in actions:
        try:
            action()
        except (Exception, KeyboardInterrupt, SystemExit, CancelledError) as error:  # noqa: BLE001 - cleanup siblings must still run
            errors.append(error)
    if isinstance(primary, (KeyboardInterrupt, SystemExit, CancelledError)):
        return
    for error in errors:
        if isinstance(error, (KeyboardInterrupt, SystemExit, CancelledError)):
            raise error
    if primary is None and errors:
        raise errors[0]


def _run_gui(
    pipe: Wire,
    nonce: str,
    mode: str,
    expected_point: tuple[int, int],
    baseline: Baseline,
) -> None:
    from PySide6.QtCore import QTimer  # noqa: PLC0415 - only after parent start
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    app: QApplication = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    cleanup: list[Callable[[], None]] = []
    try:
        source = baseline.screen_source()
        cleanup.insert(0, source.close)
        service = baseline.capture_service(source, NoTargets(), show_magnifier=False)
        cleanup.insert(0, service.cancel)
        native = Native()
        session = Session(
            pipe,
            nonce,
            mode,
            expected_point,
            service=service,
            native=native,
            quit_app=app.quit,
        )
        timer = QTimer()
        cleanup.insert(0, timer.stop)
        timer.setInterval(10)
        timer.timeout.connect(session.tick)
        service.capture_region()
        timer.start()
        app.exec()
        if session.control is not None:
            raise session.control
        if session.failure is not None:
            raise session.failure
        if not session.completed:
            raise FailureError(session.phase, "unavailable")
    finally:
        _cleanup(cleanup)


def _validate(nonce: str, mode: str, point: tuple[int, int]) -> None:
    if (
        len(nonce) != 32
        or any(c not in "0123456789abcdef" for c in nonce)
        or mode not in {"cancel", "crash"}
        or len(point) != 2
        or any(
            type(value) is not int or not -(2**31) <= value < 2**31 for value in point
        )
    ):
        raise RuntimeError("baseline-arguments-invalid")


def child(
    pipe: Wire,
    nonce: str,
    baseline_root: str,
    mode: str,
    expected_point: tuple[int, int],
) -> None:
    """The standalone spawn target; no Qt application exists until exact start."""
    stage = "source"
    try:
        try:
            require_hosted(os.environ)
            _validate(nonce, mode, expected_point)
            baseline = load_baseline(baseline_root)
            send(
                pipe,
                {
                    "phase": "ready",
                    "nonce": nonce,
                    "pid": os.getpid(),
                    "runtime": runtime(),
                    "baseline_verified": True,
                },
            )
            stage = "start"
            if receive(pipe, time.monotonic() + _TIMEOUT) != {
                "phase": "start",
                "nonce": nonce,
            }:
                raise RuntimeError("baseline-start-refused")  # noqa: TRY301 - preserve stage in fixed failure receipt
            stage = "admission"
            _run_gui(pipe, nonce, mode, expected_point, baseline)
        except Exception as error:  # noqa: BLE001 - fixed private protocol, no provider traceback
            if isinstance(error, FailureError):
                stage, reason = error.stage, error.reason
            else:
                reason = _reason(error)
            _report_failure(pipe, nonce, stage, reason)
            raise SystemExit(1) from None
    finally:
        try:
            _cleanup((pipe.close,))
        except Exception:  # noqa: BLE001 - endpoint cleanup must not leak provider diagnostics
            _report_failure(pipe, nonce, "finish", "unknown")
            raise SystemExit(1) from None
