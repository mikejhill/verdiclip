"""One hosted cold native region observation; never changes the capture budget."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Final, override

from PySide6 import __version__ as qt_version
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication, QPaintEvent
from PySide6.QtWidgets import QApplication
from tests.capture.desktop import DesktopSession

from verdiclip.capture import overlay as overlay_module
from verdiclip.capture import service as service_module
from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.overlay import SelectionOverlay, WindowTarget
from verdiclip.capture.service import CaptureService

ORIGINAL_BUDGET_MS: Final = 150


class FirstPaints:
    """Latch every owned window's first completed paint, never a later retry."""

    def __init__(self) -> None:
        self.values: dict[int, float] = {}

    def record(self, handle: int, elapsed_ms: float) -> None:
        """Record a valid first observation without replacing existing evidence."""
        if handle <= 0 or elapsed_ms < 0 or not math.isfinite(elapsed_ms):
            raise ValueError("Invalid owned paint evidence")
        self.values.setdefault(handle, elapsed_ms)

    def complete(self, handles: Sequence[int], expected: int) -> bool:
        """Require one first completed paint for every actual overlay window."""
        return (
            expected > 0
            and len(handles) == expected
            and len(set(handles)) == expected
            and all(handle in self.values for handle in handles)
        )


class NoWindows:
    """Region-only observation never enumerates another application's windows."""

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[WindowTarget]:
        """No window-picking fixture is needed or pre-painted."""
        del exclude
        return ()

    def foreground(self) -> WindowTarget | None:
        """Do not consult or activate an external foreground window."""
        return None


def wait_for(predicate: Callable[[], bool], seconds: float) -> bool:
    """Pump only the owned GUI loop until a fixed deadline, not until good timing."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        ready = predicate()
        if time.perf_counter() >= deadline:
            return False
        if ready:
            return True
        time.sleep(0.001)
    return False


def attest() -> None:
    """Bind the actual imported capture bytes to the selected clean checkout."""
    root = Path.cwd().resolve()
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Disposable Windows CI required")
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Hosted runner identity required")
    if qt_version != os.environ["QT_VERSION"]:
        raise RuntimeError("Unexpected Qt runtime")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git unavailable")
    for module, relative in (
        (service_module, "src/verdiclip/capture/service.py"),
        (overlay_module, "src/verdiclip/capture/overlay.py"),
    ):
        path = root / relative
        if Path(module.__file__).resolve() != path:
            raise RuntimeError("Capture import origin mismatch")
        committed = subprocess.check_output(  # noqa: S603 - fixed owned source query
            [git, "show", "HEAD:" + relative], cwd=root, timeout=5
        )
        if path.read_bytes() != committed:
            raise RuntimeError("Capture source bytes mismatch")
        digest = hashlib.sha256(committed).hexdigest()
        sys.stdout.write(f"NATIVE_SOURCE_ATTESTED {relative} {digest}\n")


def observe() -> dict[str, object]:  # noqa: PLR0915 - explicit independent resource retirement
    """Construct no widget before the single production region request."""
    attest()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    if QGuiApplication.platformName() != "windows":
        raise RuntimeError("Native Qt platform required")
    desktop = DesktopSession.current()
    if desktop is None or desktop.state != 0 or desktop.flags != 1:
        raise RuntimeError("Known active unlocked desktop required")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.IsWindow.argtypes = (ctypes.wintypes.HWND,)
    api.IsWindow.restype = ctypes.wintypes.BOOL
    with ExitStack() as construction:
        source = MssScreenSource()
        construction.callback(source.close)
        capture = CaptureService(source, NoWindows())
        construction.callback(capture.cancel)
        first = FirstPaints()
        errors: list[bool] = []
        capture.failed.connect(lambda _message: errors.append(True))
        expected = len(QGuiApplication.screens())
        started = 0.0
        handles: list[int] = []
        handles_known = False
        original = service_module.SelectionOverlay

        class ObservedOverlay(SelectionOverlay):
            @override
            def paintEvent(self, event: QPaintEvent) -> None:
                try:
                    super().paintEvent(event)
                    completed = time.perf_counter()
                    handle = int(self.winId())
                    if not self.isVisible() or not api.IsWindow(handle):
                        errors.append(True)
                        return
                    first.record(handle, (completed - started) * 1000)
                except Exception:  # noqa: BLE001 - Qt callback cannot report a false pass
                    errors.append(True)

        def painted() -> bool:
            handles[:] = [int(item.winId()) for item in capture.overlays]
            return first.complete(handles, expected) or bool(errors)

        def retired() -> bool:
            return (
                handles_known
                and not capture.is_selecting
                and not any(api.IsWindow(h) for h in handles)
            )

        receipt: dict[str, object] = {
            "source": os.environ["CAPTURE_SOURCE"],
            "qt": qt_version,
            "python": sys.version.split()[0],
            "measurement": "request_to_all_first_completed_native_overlay_paints",
            "real_screen_freeze": True,
            "prior_widget_paint": False,
            "input_injected": False,
            "original_budget_ms": ORIGINAL_BUDGET_MS,
            "original_gate_replaced": False,
            "cleanup_confirmed": False,
        }
        try:
            construction.pop_all()  # The active finalizer now owns both resources.
            vars(service_module)["SelectionOverlay"] = ObservedOverlay
            started = time.perf_counter()
            capture.capture_region()
            ready = wait_for(painted, 5.0)
            if not ready or errors or not first.complete(handles, expected):
                raise RuntimeError("First native paint unavailable")
            elapsed = max(first.values[h] for h in handles)
            receipt.update(
                status="observed",
                overlay_count=len(handles),
                first_paint_ms=round(elapsed, 3),
                within_original_150ms=elapsed < ORIGINAL_BUDGET_MS,
            )
        finally:
            try:
                try:
                    handles[:] = list(
                        set(handles) | {int(o.winId()) for o in capture.overlays}
                    )
                    handles_known = True
                finally:
                    capture.cancel()
            finally:
                try:
                    receipt["cleanup_confirmed"] = wait_for(retired, 2.0)
                finally:
                    try:
                        source.close()
                    finally:
                        vars(service_module)["SelectionOverlay"] = original
                        sys.stdout.write(
                            "COLD_NATIVE_REGION="
                            + json.dumps(receipt, sort_keys=True)
                            + "\n"
                        )
        if receipt["cleanup_confirmed"] is not True:
            raise RuntimeError("Owned overlay cleanup unconfirmed")
        return receipt


if __name__ == "__main__":
    observe()
