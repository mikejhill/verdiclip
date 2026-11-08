"""One disposable process per scrolling transaction, one terminal result.

stdin stays open while the parent owns the transaction. EOF kills the helper
without further target calls, including when UIA is blocked. ``cancel`` requests
cooperative cancellation/restoration; a short parent deadline bounds that path.
There is no per-frame protocol, server, shared screenshot journal, or retry.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtGui import QImageReader

from verdiclip.capture.scrolling import MAX_PIXELS, MAX_SECONDS, ScrollResult
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point

logger = logging.getLogger(__name__)

_MAX_RESULT_BYTES = 8192
_MAX_DIAGNOSTIC_BYTES = 16_384
_CANCEL_GRACE_MS = 1500


class ScrollHelper(QObject):
    """Own process, scratch directory and deadline until a single final signal."""

    finished = Signal(object)  # ScrollResult

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.finished.connect(self._exited)
        self._process.errorOccurred.connect(self._error)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        self._directory: TemporaryDirectory[str] | None = None
        self._cancelled = False
        self._terminal = False
        self._timeout_message = ""
        self._diagnostics = ""

    @property
    def diagnostics(self) -> str:
        """Return bounded child diagnostics, with no captured content or titles."""
        return self._diagnostics

    def start(self, point: Point, *, window: int, input_tick: int) -> None:
        """Start with the selected root and completed-input tick, without rebasing.

        The caller closes selectors first and keeps this owner until ``finished``.
        """
        if self._directory is not None or self._terminal:
            raise RuntimeError("A scrolling helper can only be started once")
        self._directory = TemporaryDirectory(prefix="verdiclip-scroll-")
        self._process.setProgram(sys.executable)
        self._process.setArguments(
            [
                "-m",
                "verdiclip.capture.scroll_helper",
                str(round(point.x)),
                str(round(point.y)),
                str(window),
                str(input_tick),
                self._directory.name,
            ]
        )
        self._timer.start(round((MAX_SECONDS + 2.0) * 1000))
        self._process.start()

    def cancel(self) -> None:
        """Request cooperative cancellation, then kill if UIA does not return."""
        if self._terminal or self._directory is None or self._cancelled:
            return
        self._cancelled = True
        self._process.write(b"cancel\n")
        self._timer.start(_CANCEL_GRACE_MS)

    def shutdown(self) -> None:
        """Kill and retire the owned child before the application closes."""
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
        self._complete(ScrollResult(None, "Scrolling capture stopped", "unknown", 0))

    def _timeout(self) -> None:
        self._cancelled = True
        self._timeout_message = (
            "Scrolling capture stopped. The panel's position could not be restored."
        )
        self._process.kill()
        # Keep the directory alive until QProcess confirms process exit.

    def _error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(
                ScrollResult(None, "Could not start scrolling capture", "unchanged", 0)
            )

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
            or not isinstance(value.get("restoration"), str)
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
        return ScrollResult(image, message, value["restoration"], value["frames"])

    @staticmethod
    def _validate_exit(code: int, status: QProcess.ExitStatus) -> None:
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            raise ValueError("Scrolling helper stopped unexpectedly")

    def _exited(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._terminal:
            return
        raw = bytes(self._process.readAllStandardError().data())
        self._diagnostics = (
            raw[-_MAX_DIAGNOSTIC_BYTES:].decode("utf-8", errors="replace").strip()
        )
        if self._diagnostics:
            logger.info("Scrolling helper diagnostics:\n%s", self._diagnostics)
        try:
            self._validate_exit(code, status)
            result = self._read_result()
        except (ValueError, OSError) as error:
            result = ScrollResult(
                None, self._timeout_message or str(error), "unknown: helper stopped", 0
            )
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
                    message="Temporary scrolling files could not be removed. "
                    + result.message,
                )
        self.finished.emit(result)


def _watch_parent(cancelled: threading.Event) -> None:
    """Exit without restoration when the sole process owner disappears."""
    for line in sys.stdin:
        if line.strip() == "cancel":
            cancelled.set()
    os._exit(1)


def main() -> None:
    """Run only in a fresh helper; no app controller or single-instance setup."""
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    # Importing this adapter never starts UIA in the parent GUI process.
    import ctypes  # noqa: PLC0415 - Windows dependency belongs to the helper

    from verdiclip.capture.scrolling import ScrollTransaction  # noqa: PLC0415
    from verdiclip.platform.scrolling import UiaScrollTarget  # noqa: PLC0415

    x, y, window, input_tick, directory = sys.argv[1:]
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
    user.SetProcessDpiAwarenessContext.restype = ctypes.c_int
    if not user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        raise OSError("Could not establish physical-pixel coordinates")
    cancelled = threading.Event()
    threading.Thread(target=_watch_parent, args=(cancelled,), daemon=True).start()
    result: ScrollResult | None = None
    try:
        with UiaScrollTarget(
            Point(int(x), int(y)), window=int(window), input_tick=int(input_tick)
        ) as target:
            result = ScrollTransaction(target, cancelled.is_set).run()
    except (CaptureError, OSError) as error:
        restoration = (
            "unchanged: target discovery failed"
            if result is None
            else "unknown: provider cleanup failed"
        )
        result = ScrollResult(None, str(error), restoration, 0)
    if result.image is not None and not result.image.save(
        str(Path(directory) / "capture.png")
    ):
        raise OSError("Could not save the scrolling image")
    sys.stdout.write(
        json.dumps(
            {
                "success": result.image is not None,
                "message": result.message,
                "restoration": result.restoration,
                "frames": result.frames,
            }
        )
        + "\n"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
