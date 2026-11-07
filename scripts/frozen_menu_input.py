"""Observed native input and exact owned-pixel oracles for menu qualification."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray, QCoreApplication
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


class MouseInput(ctypes.Structure):
    """Documented Win32 MOUSEINPUT layout, including pointer-sized extra info."""

    _fields_ = (
        ("dx", w.LONG),
        ("dy", w.LONG),
        ("data", w.DWORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", ctypes.c_size_t),
    )


class KeyInput(ctypes.Structure):
    """Documented Win32 KEYBDINPUT layout."""

    _fields_ = (
        ("vk", w.WORD),
        ("scan", w.WORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", ctypes.c_size_t),
    )


class InputData(ctypes.Union):
    """Mouse is the largest supported INPUT payload, preserving native size."""

    _fields_ = (("mouse", MouseInput), ("key", KeyInput))


class NativeInput(ctypes.Structure):
    """Documented Win32 INPUT layout."""

    _fields_ = (("kind", w.DWORD), ("data", InputData))


def wait_for(predicate: Callable[[], bool], label: str, seconds: float = 5.0) -> None:
    """One fixed deadline with a late-success check; never retry an action."""
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        QApplication.processEvents()
        if predicate() and time.perf_counter() - started < seconds:
            return
        time.sleep(0.005)
    raise TimeoutError(f"{label} did not complete within {seconds}s")


def pixel_digest(image: QImage) -> str:
    """Hash detached, normalized pixels, not an encoded image or metadata."""
    if image.isNull():
        raise ValueError("Empty image is not pixel evidence")
    normalized = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return hashlib.sha256(bytes(normalized.constBits())).hexdigest()


def is_marker(image: QImage) -> bool:
    """Require both solid colors across every interior pixel, not one sample."""
    if image.width() < 2 or image.height() < 1:  # noqa: PLR2004 - two colors
        return False
    left, right = (0xD72B91, 0x19BD83)
    for y in range(image.height()):
        for x in range(image.width()):
            expected = left if x < image.width() // 2 else right
            if image.pixel(x, y) & 0xFFFFFF != expected:
                return False
    return True


def has_contrast(image: QImage) -> bool:
    """A native text-menu patch must contain actual contrasting menu content."""
    if image.isNull():
        return False
    first = image.pixel(0, 0)
    return any(
        image.pixel(x, y) != first
        for y in range(image.height())
        for x in range(image.width())
    )


class HotkeyObservation(QAbstractNativeEventFilter):
    """Observe after production filter installation, without consuming messages."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, str | int]] = []

    @override
    def nativeEventFilter(
        self, event_type: QByteArray | bytes | bytearray | memoryview, message: int
    ) -> tuple[bool, int]:
        """Record actual Qt categories for WM_HOTKEY in this owned GUI thread."""
        category = (
            event_type.data()
            if isinstance(event_type, QByteArray)
            else bytes(event_type)
        )
        if category in {b"windows_generic_MSG", b"windows_dispatcher_MSG"} and int(
            message
        ):
            native = w.MSG.from_address(int(message))
            if native.message == 0x0312:  # noqa: PLR2004 - WM_HOTKEY
                self.events.append(
                    {
                        "category": bytes(category).decode("ascii"),
                        "id": int(native.wParam),
                        "hwnd": int(native.hWnd or 0),
                    }
                )
        return False, 0


def valid_case_nonce(value: str) -> bool:
    """Require the supervisor's exact opaque 128-bit hexadecimal case identity."""
    return len(value) == 32 and all(  # noqa: PLR2004 - UUID hex width
        character in "0123456789abcdef" for character in value
    )


class OwnedInput:
    """SendInput only while the foreground belongs to the disposable fixture."""

    def __init__(self, *, initialize_ledger: bool = True) -> None:
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.SendInput.argtypes = (
            w.UINT,
            ctypes.POINTER(NativeInput),
            ctypes.c_int,
        )
        self.api.SendInput.restype = w.UINT
        self.api.GetForegroundWindow.argtypes = ()
        self.api.GetForegroundWindow.restype = w.HWND
        self.api.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
        self.api.GetWindowThreadProcessId.restype = w.DWORD
        self.api.GetCursorPos.argtypes = (ctypes.POINTER(w.POINT),)
        self.api.GetCursorPos.restype = w.BOOL
        self.api.IsWindow.argtypes = (w.HWND,)
        self.api.IsWindow.restype = w.BOOL
        self.allowed_pids = {os.getpid()}
        self.down: set[int] = set()
        self.mouse_down = False
        path = os.environ.get("QUALIFICATION_INPUT_LEDGER")
        self.ledger = Path(path) if path is not None else None
        self.nonce = os.environ.get("QUALIFICATION_RUN_NONCE", "")
        if initialize_ledger:
            self._save_ledger()

    def process(self, hwnd: int) -> int:
        """Resolve an explicitly known owned HWND, never enumerate applications."""
        pid = w.DWORD()
        if not self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            raise RuntimeError("Owned window process unavailable")
        return int(pid.value)

    def foreground_owned(self) -> bool:
        """Verify target ownership immediately before each input submission."""
        hwnd = self.api.GetForegroundWindow()
        return bool(hwnd) and self.process(int(hwnd)) in self.allowed_pids

    def _save_ledger(self) -> None:
        """Persist owned releases for the independent timeout supervisor."""
        if self.ledger is not None:
            if not valid_case_nonce(self.nonce):
                raise RuntimeError(
                    "Valid supervisor case identity required before input"
                )
            pending = self.ledger.with_suffix(".pending")
            pending.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "nonce": self.nonce,
                        "keys": sorted(self.down),
                        "mouse_down": self.mouse_down,
                    }
                ),
                encoding="utf-8",
            )
            pending.replace(self.ledger)

    def _send(
        self,
        event: NativeInput,
        *,
        cleanup: bool = False,
        mark_key: int | None = None,
        mark_button: bool = False,
    ) -> None:
        if self.ledger is not None and not valid_case_nonce(self.nonce):
            raise RuntimeError("Valid supervisor case identity required before input")
        if not cleanup and not self.foreground_owned():
            raise RuntimeError("Foreground is not an owned qualification window")
        # A rejected ownership check never acquires a key/button release lease.
        # Persist before the native call; partial SendInput failure is uncertain.
        if mark_key is not None:
            self.down.add(mark_key)
        if mark_button:
            self.mouse_down = True
        self._save_ledger()
        if self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(NativeInput)) != 1:
            raise RuntimeError(f"SendInput failed: {ctypes.get_last_error()}")

    def key(self, vk: int, *, up: bool = False, cleanup: bool = False) -> None:
        """Acquire release ownership only immediately before native submission."""
        self._send(
            NativeInput(1, InputData(key=KeyInput(vk, 0, 2 if up else 0, 0, 0))),
            cleanup=cleanup,
            mark_key=None if up else vk,
        )
        if up:
            self.down.discard(vk)
            self._save_ledger()

    def chord(self, vk: int) -> None:
        """Generate one real Ctrl+Shift+F-key chord, not a posted WM_HOTKEY."""
        try:
            for key in (0x11, 0x10, vk):
                self.key(key)
        finally:
            self.release()

    def escape(self) -> None:
        """Deliver Escape through the same native input queue as selection."""
        try:
            self.key(0x1B)
        finally:
            self.release()

    def cursor(self) -> tuple[int, int]:
        """Read the system pointer, independent of Qt selection state."""
        position = w.POINT()
        if not self.api.GetCursorPos(ctypes.byref(position)):
            raise RuntimeError("GetCursorPos failed")
        return int(position.x), int(position.y)

    def move(self, x: int, y: int) -> tuple[int, int]:
        """Submit ordinary absolute native movement without changing pointer speed."""
        left, top = self.api.GetSystemMetrics(76), self.api.GetSystemMetrics(77)
        width, height = self.api.GetSystemMetrics(78), self.api.GetSystemMetrics(79)
        dx = round((x - left) * 65535 / (width - 1))
        dy = round((y - top) * 65535 / (height - 1))
        self._send(NativeInput(0, InputData(mouse=MouseInput(dx, dy, 0, 0xC001, 0, 0))))
        wait_for(
            lambda: abs(self.cursor()[0] - x) <= 1 and abs(self.cursor()[1] - y) <= 1,
            "native cursor endpoint",
            1.0,
        )
        return self.cursor()

    def button(self, *, up: bool = False, cleanup: bool = False) -> None:
        """Track native left-button boundaries only after foreground validation."""
        self._send(
            NativeInput(0, InputData(mouse=MouseInput(0, 0, 0, 4 if up else 2, 0, 0))),
            cleanup=cleanup,
            mark_button=not up,
        )
        if up:
            self.mouse_down = False
            self._save_ledger()

    def release(self) -> None:
        """Release only keys/buttons submitted here, even after target focus loss."""
        errors: list[Exception] = []
        for vk in sorted(self.down, reverse=True):
            try:
                self.key(vk, up=True, cleanup=True)
            except Exception as error:  # noqa: BLE001 - retain every cleanup failure
                errors.append(error)
        if self.mouse_down:
            try:
                self.button(up=True, cleanup=True)
            except Exception as error:  # noqa: BLE001 - independent retirement
                errors.append(error)
        if errors:
            raise ExceptionGroup("Owned input release failed", errors)

    @staticmethod
    def install_observer(observer: HotkeyObservation) -> None:
        """Qt calls the most recently installed filter first."""
        app = QCoreApplication.instance()
        if app is None:
            raise RuntimeError("Owned QApplication required")
        app.installNativeEventFilter(observer)


def release_timeout_ledger(path: Path, expected_nonce: str, launcher_pid: int) -> None:
    """After the supervisor reaps a hung child, release only its recorded input."""
    if (
        sys.platform != "win32"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise RuntimeError("Disposable hosted Windows required for timeout cleanup")
    if not valid_case_nonce(expected_nonce) or not path.exists():
        raise RuntimeError("Owned input ledger or supervisor identity unavailable")
    ledger = json.loads(path.read_text(encoding="utf-8"))
    producer_pid = ledger.get("pid") if isinstance(ledger, dict) else None
    sys.stdout.write(
        "NATIVE_INPUT_IDENTITY="
        + json.dumps(
            {
                "launcher_pid": launcher_pid,
                "producer_pid": producer_pid if type(producer_pid) is int else None,
                "case_token_matches": isinstance(ledger, dict)
                and ledger.get("nonce") == expected_nonce,
            }
        )
        + "\n"
    )
    allowed = {0x11, 0x10, 0x0D, 0x1B, *range(0x81, 0x85)}
    if (
        not isinstance(ledger, dict)
        or set(ledger) != {"pid", "nonce", "keys", "mouse_down"}
        or ledger.get("nonce") != expected_nonce
        or type(producer_pid) is not int
        or producer_pid <= 0
        or launcher_pid <= 0
        or not isinstance(ledger.get("keys"), list)
        or any(type(key) is not int or key not in allowed for key in ledger["keys"])
        or len(set(ledger["keys"])) != len(ledger["keys"])
        or type(ledger.get("mouse_down")) is not bool
    ):
        raise RuntimeError("Invalid owned timeout input ledger")
    owner = OwnedInput(initialize_ledger=False)
    owner.ledger = path
    owner.nonce = expected_nonce
    owner.down = set(ledger["keys"])
    owner.mouse_down = ledger["mouse_down"]
    owner.release()
    sys.stdout.write("NATIVE_INPUT_CLEANUP=owned pending releases completed\n")


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--release-ledger":  # noqa: PLR2004 - CLI arity
        raise SystemExit("Expected --release-ledger PATH CASE_NONCE LAUNCHER_PID")
    release_timeout_ledger(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
