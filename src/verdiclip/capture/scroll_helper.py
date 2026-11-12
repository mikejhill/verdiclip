"""One disposable process per scrolling capture, so a hung provider can be killed.

The parent passes the chosen point and a scratch directory. The child prints
one JSON result and writes ``capture.png`` on success. ``cancel`` on stdin asks
it to stop and restore; stdin closing (parent gone) exits at once.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtGui import QImageReader

from verdiclip.capture.scrolling import MAX_PIXELS, MAX_SECONDS, ScrollResult
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point

logger = logging.getLogger(__name__)

_MAX_RESULT_BYTES = 8192
_MAX_DIAGNOSTIC_BYTES = 16_384
_CANCEL_GRACE_MS = 2000
_DEADLINE_MARGIN = 10.0
# The child entry point; tests substitute a scripted stand-in.
CHILD_MODULE = "verdiclip.capture.scroll_helper"


class ScrollHelper(QObject):
    """Own the child process, its scratch directory and its deadline."""

    finished = Signal(object)  # ScrollResult

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.finished.connect(self._exited)
        self._process.errorOccurred.connect(self._error)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._timeout)
        self._directory: TemporaryDirectory[str] | None = None
        self._cancelled = self._terminal = self._timed_out = False

    def start(self, point: Point) -> None:
        """Start capturing the panel at ``point`` (physical pixels)."""
        if self._directory is not None or self._terminal:
            raise RuntimeError("A scrolling helper can only be started once")
        self._directory = TemporaryDirectory(prefix="verdiclip-scroll-")
        self._process.setProgram(sys.executable)
        self._process.setArguments(
            [
                "-m",
                CHILD_MODULE,
                str(round(point.x)),
                str(round(point.y)),
                self._directory.name,
            ]
        )
        self._timer.start(math.ceil((MAX_SECONDS + _DEADLINE_MARGIN) * 1000))
        self._process.start()

    def cancel(self) -> None:
        """Ask the child to stop and restore; kill it if it does not answer."""
        if self._terminal or self._directory is None or self._cancelled:
            return
        self._cancelled = True
        self._process.write(b"cancel\n")
        remaining = self._timer.remainingTime()
        self._timer.start(
            _CANCEL_GRACE_MS if remaining < 0 else min(remaining, _CANCEL_GRACE_MS)
        )

    def shutdown(self) -> None:
        """Kill the child before the application closes."""
        if self._terminal:
            return
        self._cancelled = True
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            if (
                not self._process.waitForFinished(1000)
                and self._process.state() != QProcess.ProcessState.NotRunning
            ):
                raise CaptureError("The scrolling helper has not stopped")
        self._complete(ScrollResult(None, "Scrolling capture stopped", 0))

    def _timeout(self) -> None:
        if self._terminal:
            return
        self._timed_out = True
        self._process.kill()  # _exited completes once the process is gone

    def _error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(ScrollResult(None, "Could not start scrolling capture", 0))

    def _read_result(self) -> ScrollResult:
        if self._directory is None:
            raise ValueError("Missing capture directory")
        raw = bytes(self._process.readAllStandardOutput().data())
        if len(raw) > _MAX_RESULT_BYTES:
            raise ValueError("Oversized scrolling result")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("message"), str)
            or not isinstance(value.get("warning"), str)
            or type(value.get("frames")) is not int
            or type(value.get("success")) is not bool
        ):
            raise ValueError("Invalid scrolling result")
        image = None
        if value["success"] and not self._cancelled:
            reader = QImageReader(str(Path(self._directory.name) / "capture.png"))
            size = reader.size()
            if (
                size.width() <= 0
                or size.height() <= 0
                or size.width() * size.height() > MAX_PIXELS
            ):
                raise ValueError("Invalid scrolling image size")
            image = reader.read()
            if image.isNull():
                raise ValueError("Invalid scrolling image")
        message = "Scrolling capture cancelled" if self._cancelled else value["message"]
        return ScrollResult(image, message, value["frames"], value["warning"])

    def _exited(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._terminal:
            return
        diagnostics = bytes(self._process.readAllStandardError().data())
        text = diagnostics[-_MAX_DIAGNOSTIC_BYTES:].decode("utf-8", errors="replace")
        if text.strip():
            logger.info("Scrolling helper diagnostics:\n%s", text.strip())
        if self._timed_out:
            result = ScrollResult(None, "Scrolling capture took too long", 0)
        elif code != 0 or status != QProcess.ExitStatus.NormalExit:
            message = (
                "Scrolling capture cancelled"
                if self._cancelled
                else "Scrolling capture stopped unexpectedly"
            )
            result = ScrollResult(None, message, 0)
        else:
            try:
                result = self._read_result()
            except (ValueError, OSError) as error:
                result = ScrollResult(None, str(error), 0)
        self._complete(result)

    def _complete(self, result: ScrollResult) -> None:
        if self._terminal:
            return
        self._terminal = True
        self._timer.stop()
        if self._directory is not None:
            directory, self._directory = self._directory, None
            try:
                directory.cleanup()
            except OSError:
                logger.exception("Could not remove scrolling capture temporary files")
                result = replace(
                    result,
                    warning=(
                        result.warning + " Temporary files could not be removed."
                    ).strip(),
                )
        self.finished.emit(result)


def _watch_parent(
    cancelled: threading.Event, commands: Iterable[str], on_eof: Callable[[], None]
) -> None:
    """Flag ``cancel``; call ``on_eof`` when the parent closes stdin."""
    for line in commands:
        if line.strip() == "cancel":
            cancelled.set()
    on_eof()


def _exit_now() -> None:
    os._exit(1)


def main(
    argv: Sequence[str] | None = None,
    commands: Iterable[str] | None = None,
    on_eof: Callable[[], None] = _exit_now,
) -> None:
    """Capture in a fresh process: no app controller, no single-instance setup."""
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    import ctypes  # noqa: PLC0415 - Windows-only, helper process only

    from verdiclip.capture.scrolling import ScrollCapture  # noqa: PLC0415
    from verdiclip.platform.scrolling import UiaScrollTarget  # noqa: PLC0415

    x, y, directory = sys.argv[1:] if argv is None else argv
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
    user.SetProcessDpiAwarenessContext.restype = ctypes.c_int
    # Per-monitor v2: UIA bounds and screen pixels share physical coordinates.
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    cancelled = threading.Event()
    threading.Thread(
        target=_watch_parent,
        args=(cancelled, sys.stdin if commands is None else commands, on_eof),
        daemon=True,
    ).start()
    started = time.monotonic()
    try:
        with UiaScrollTarget(Point(int(x), int(y))) as target:
            result = ScrollCapture(target, cancelled.is_set).run()
    except (CaptureError, OSError) as error:
        result = ScrollResult(None, str(error), 0)
    logger.info(
        "Scrolling capture: frames=%s seconds=%.1f success=%s",
        result.frames,
        time.monotonic() - started,
        result.image is not None,
    )
    if result.image is not None and not result.image.save(
        str(Path(directory) / "capture.png")
    ):
        result = ScrollResult(None, "Could not save the scrolling image", result.frames)
    sys.stdout.write(
        json.dumps(
            {
                "success": result.image is not None,
                "message": result.message,
                "warning": result.warning,
                "frames": result.frames,
            }
        )
        + "\n"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
