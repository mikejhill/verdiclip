"""Qualify real 200% scaling on one disposable hosted Windows display.

This setup probe changes only the existing Windows Settings Scale selection.
It does not qualify scrolling: the eventual target HWND must separately report
192 DPI on a monitor reporting 200%. Run before starting the application tests.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

_SCALE = 200
_DPI = 192
_PER_MONITOR_AWARE = 2
_TIMEOUT = 60
_OUTPUT = Path("artifacts/simple-scroll/display-scale")
_OVERRIDES = (
    "QT_SCALE_FACTOR",
    "QT_SCREEN_SCALE_FACTORS",
    "QT_FONT_DPI",
    "QT_DEVICE_PIXEL_RATIO",
)


def require_hosted() -> None:
    """Refuse user computers and scale overrides before any native operation."""
    if not (
        sys.platform == "win32"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    ):
        raise RuntimeError("Display setup requires a disposable hosted Windows runner")
    if any(os.environ.get(name) for name in _OVERRIDES):
        raise RuntimeError("Remove synthetic Qt scale overrides before qualification")


@dataclass(frozen=True)
class Reading:
    """Independent monitor scale and an owned per-monitor-aware window's DPI."""

    monitor_count: int
    monitor_scale: int
    scale_hresult: int
    window_dpi: int
    window_awareness: int
    physical_bounds: tuple[int, int, int, int]

    @property
    def qualified(self) -> bool:
        """A successful-looking output from a failed HRESULT is not evidence."""
        return (
            self.monitor_count == 1
            and self.scale_hresult == 0
            and self.monitor_scale == _SCALE
            and self.window_dpi == _DPI
            and self.window_awareness == _PER_MONITOR_AWARE
        )


class _MonitorInfo(c.Structure):
    _fields_ = (
        ("size", w.DWORD),
        ("monitor", w.RECT),
        ("work", w.RECT),
        ("flags", w.DWORD),
    )


class Display:
    """Read documented APIs, creating only a small invisible owned HWND."""

    def __init__(self) -> None:
        require_hosted()
        self.user = c.WinDLL("user32", use_last_error=True)
        self.shcore = c.WinDLL("shcore", use_last_error=True)
        self._bind()

    def _bind(self) -> None:
        signatures = {
            "SetThreadDpiAwarenessContext": ((c.c_void_p,), c.c_void_p),
            "MonitorFromPoint": ((w.POINT, w.DWORD), w.HANDLE),
            "GetMonitorInfoW": ((w.HANDLE, c.POINTER(_MonitorInfo)), w.BOOL),
            "GetSystemMetrics": ((c.c_int,), c.c_int),
            "CreateWindowExW": (
                (
                    w.DWORD,
                    w.LPCWSTR,
                    w.LPCWSTR,
                    w.DWORD,
                    c.c_int,
                    c.c_int,
                    c.c_int,
                    c.c_int,
                    w.HWND,
                    w.HMENU,
                    w.HINSTANCE,
                    c.c_void_p,
                ),
                w.HWND,
            ),
            "DestroyWindow": ((w.HWND,), w.BOOL),
            "GetDpiForWindow": ((w.HWND,), w.UINT),
            "GetWindowDpiAwarenessContext": ((w.HWND,), c.c_void_p),
            "GetAwarenessFromDpiAwarenessContext": ((c.c_void_p,), c.c_int),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = arguments, result
        getter = self.shcore.GetScaleFactorForMonitor
        getter.argtypes, getter.restype = (w.HANDLE, c.POINTER(c.c_int)), c.c_long

    def read(self) -> Reading:
        """Use PMv2 only for measurement; it does not set the display scale."""
        previous = self.user.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        if not previous:
            raise RuntimeError("Could not establish the owned PMv2 measurement context")
        try:
            return self._read()
        finally:
            if not self.user.SetThreadDpiAwarenessContext(previous):
                raise RuntimeError("Could not restore the measurement thread context")

    def _read(self) -> Reading:
        monitor = self.user.MonitorFromPoint(w.POINT(0, 0), 1)
        info = _MonitorInfo()
        info.size = c.sizeof(info)
        if not monitor or not self.user.GetMonitorInfoW(monitor, c.byref(info)):
            raise RuntimeError("Could not read the primary display")
        bounds = info.monitor
        hwnd = self.user.CreateWindowExW(
            0,
            "STATIC",
            "Owned hosted DPI probe",
            0x80000000,
            bounds.left + 1,
            bounds.top + 1,
            16,
            16,
            None,
            None,
            None,
            None,
        )
        if not hwnd:
            raise RuntimeError("Could not create the owned DPI probe window")
        try:
            scale = c.c_int()
            status = self.shcore.GetScaleFactorForMonitor(monitor, c.byref(scale))
            context = self.user.GetWindowDpiAwarenessContext(hwnd)
            return Reading(
                monitor_count=int(self.user.GetSystemMetrics(80)),  # SM_CMONITORS
                monitor_scale=scale.value,
                scale_hresult=int(status),
                window_dpi=int(self.user.GetDpiForWindow(hwnd)),
                window_awareness=int(
                    self.user.GetAwarenessFromDpiAwarenessContext(context)
                ),
                physical_bounds=(bounds.left, bounds.top, bounds.right, bounds.bottom),
            )
        finally:
            if not self.user.DestroyWindow(hwnd):
                raise RuntimeError("Could not close the owned DPI probe window")


def select_scale(output: Path) -> None:
    """One Settings-only UIA attempt; a timeout is uncertain, never retried."""
    require_hosted()
    receipt = output / "settings-outcome.json"
    receipt.write_text('{"status": "not_started"}', encoding="utf-8")
    executable = (
        Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    script = Path(__file__).with_suffix(".ps1").resolve()
    completed = subprocess.run(  # noqa: S603 - fixed system tool and checked-in helper
        [
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-Mta",
            "-File",
            str(script),
            "-OutputDirectory",
            str(output.resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    if completed.returncode:
        (output / "settings-process-error.txt").write_text(
            completed.stderr[:2048], encoding="utf-8"
        )
        raise RuntimeError(
            "Settings setup or cleanup failed; see settings-outcome.json"
        )
    try:
        outcome = json.loads(receipt.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise RuntimeError("Settings returned no valid cleanup receipt") from error
    if not (
        isinstance(outcome, dict)
        and outcome.get("status") == "selection_completed"
        and outcome.get("closed_owned_window") is True
    ):
        raise RuntimeError("Settings owned-window disappearance was not confirmed")


def _require_reading(reading: Reading, *, initial: bool) -> None:
    if reading.monitor_count != 1 or reading.scale_hresult != 0:
        raise RuntimeError("Setup requires one monitor with successful scale readback")
    if not initial and not reading.qualified:
        raise RuntimeError("Actual monitor scale=200 and owned window DPI=192 required")


def failure_image(output: Path) -> None:
    """Attempt one bounded hosted screenshot, even if UIA timed out."""
    with suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603 - this checked-in helper, no shell
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--failure-screenshot",
                str(output.resolve() / "desktop-failure.png"),
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )


def _screenshot(path: Path) -> None:
    require_hosted()
    import mss  # noqa: PLC0415 - only the bounded hosted screenshot child
    import mss.tools  # noqa: PLC0415

    with mss.mss() as source:
        monitor = source.monitors[1]
        if monitor["width"] * monitor["height"] > 16_777_216:  # noqa: PLR2004
            raise RuntimeError("Diagnostic screenshot exceeds the pixel budget")
        shot = source.grab(monitor)
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))


def qualify(output: Path = _OUTPUT) -> int:
    """Write a truthful setup result; unsupported setup is always nonzero."""
    require_hosted()
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "status": "failed",
        "scope": "display setup only; target application not yet qualified",
        "image_version": os.environ.get("IMAGEVERSION", "unknown"),
        "source_head": os.environ.get("QUALIFICATION_HEAD", "unknown"),
    }
    display = None
    try:
        display = Display()
        before = display.read()
        report["before"] = asdict(before)
        _require_reading(before, initial=True)
        if before.monitor_scale != _SCALE:
            select_scale(output)
        deadline = time.monotonic() + 2.0
        after = display.read()
        while not after.qualified and time.monotonic() < deadline:
            time.sleep(0.1)
            after = display.read()
        report["after"] = asdict(after)
        _require_reading(after, initial=False)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = f"{type(error).__name__}: {error}"[:1024]
        report["selection_outcome_uncertain"] = isinstance(
            error, subprocess.TimeoutExpired
        )
        if display is not None:
            try:
                report["failure_readback"] = asdict(display.read())
            except (OSError, RuntimeError) as read_error:
                report["readback_error"] = type(read_error).__name__
        failure_image(output)
        report["screenshot_saved"] = any(
            (output / name).is_file()
            for name in ("settings-failure.png", "desktop-failure.png")
        )
        return 1
    else:
        report["status"] = "setup_verified"
        return 0
    finally:
        (output / "display-scale.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--failure-screenshot":  # noqa: PLR2004
        _screenshot(Path(sys.argv[2]))
        raise SystemExit(0)
    raise SystemExit(qualify())
