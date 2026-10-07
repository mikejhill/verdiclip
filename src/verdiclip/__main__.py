"""Application entry point: tray app, headless capture, or open-in-editor."""

from __future__ import annotations

import logging
import signal
import sys
import time
from collections.abc import Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import FrameType
from typing import Final

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from verdiclip import APP_NAME, VERSION
from verdiclip.capture.grabber import MssScreenSource
from verdiclip.cli import CliRequest, Command, CommandLine, HeadlessMode
from verdiclip.exceptions import AppError, CaptureError
from verdiclip.output.delivery import ImageDelivery
from verdiclip.platform.hotkeys import HotkeyService
from verdiclip.platform.startup import StartupRegistration
from verdiclip.platform.windows import WindowLocator
from verdiclip.settings import SettingsStore
from verdiclip.shell.controller import AppController
from verdiclip.shell.instance import SingleInstance

logger = logging.getLogger(__name__)

SIGNAL_POLL_MS: Final = 200
LOG_BYTES: Final = 2 * 1024 * 1024


class Application:
    """Wire settings, logging, and services, then run the requested command."""

    def __init__(self) -> None:
        self._signal_timer: QTimer | None = None

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """Run and exit with the resulting status code."""
        sys.exit(cls().run(argv))

    def run(self, argv: Sequence[str] | None = None) -> int:
        """Run once and return a process exit code."""
        request = CommandLine().parse(argv)
        store = SettingsStore(SettingsStore.default_path())
        self._configure_logging(request.log_level, store.path.parent / "logs")
        try:
            if request.command is Command.CAPTURE:
                return self._run_capture(request, store)
            return self._run_gui(request, store)
        except AppError:
            logger.exception("%s failed", APP_NAME)
            return 1
        except KeyboardInterrupt:
            return 130

    # Commands

    def _run_gui(self, request: CliRequest, store: SettingsStore) -> int:
        """Run the tray app, or forward to the one already running."""
        app = QApplication.instance() or QApplication(sys.argv[:1])
        if not isinstance(app, QApplication):
            msg = "A non-widget Qt application is already running"
            raise AppError(msg)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(VERSION)
        app.setQuitOnLastWindowClosed(False)
        files = [str(p.resolve()) for p in request.files]
        instance = SingleInstance()
        if instance.forward(files):
            logger.info("Forwarded to the running instance")
            return 0
        instance.listen()
        controller = AppController(
            store,
            MssScreenSource(),
            WindowLocator(),
            HotkeyService(),
            StartupRegistration(),
        )
        instance.message_received.connect(controller.handle_forwarded)
        app.aboutToQuit.connect(controller.shutdown)
        app.aboutToQuit.connect(instance.close)
        self._install_sigint(app)
        controller.start()
        for path in request.files:
            controller.open_image(path)
        return app.exec()

    def _run_capture(self, request: CliRequest, store: SettingsStore) -> int:
        """Capture without UI, then save or copy."""
        app = QApplication.instance() or QApplication(sys.argv[:1])
        del app
        if request.delay:
            time.sleep(request.delay)
        frozen = MssScreenSource().freeze()
        if request.mode is HeadlessMode.SCREEN:
            image = frozen.image
        elif request.mode is HeadlessMode.REGION and request.region is not None:
            image = frozen.crop(request.region)
        else:
            window = WindowLocator().foreground()
            if window is None:
                msg = "There is no active window to capture"
                raise CaptureError(msg)
            image = frozen.crop(window.bounds)
        delivery = ImageDelivery(store.load().output)
        if request.to_clipboard:
            delivery.copy(image)
            sys.stdout.write("Copied to clipboard\n")
            return 0
        path = request.output or delivery.auto_path()
        sys.stdout.write(f"{delivery.save(image, path)}\n")
        return 0

    # Infrastructure

    def _install_sigint(self, app: QApplication) -> None:
        """Let Ctrl+C in the terminal quit promptly (UX-TRY-05)."""

        def on_sigint(_signum: int, _frame: FrameType | None) -> None:
            logger.info("Interrupted; quitting")
            app.quit()

        signal.signal(signal.SIGINT, on_sigint)
        # Qt's event loop blocks Python signal handling; wake it periodically
        timer = QTimer()
        timer.timeout.connect(lambda: None)
        timer.start(SIGNAL_POLL_MS)
        self._signal_timer = timer

    @staticmethod
    def _configure_logging(level: str, log_dir: Path) -> None:
        """Log to stderr and to a rotating file under the settings folder."""
        handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    log_dir / "verdiclip.log",
                    maxBytes=LOG_BYTES,
                    backupCount=2,
                    encoding="utf-8",
                )
            )
        except OSError as err:
            sys.stderr.write(f"File logging disabled: {err}\n")
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            handlers=handlers,
            force=True,
        )


if __name__ == "__main__":
    Application.main()
