"""Entry point for VerdiClip application."""

from __future__ import annotations

import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _setup_logging() -> None:
    """Configure rotating file log and console output."""
    log_dir = Path.home() / "AppData" / "Roaming" / "VerdiClip" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_dir / "verdiclip.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger("verdiclip")
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def _install_signal_handlers() -> None:
    """Allow Ctrl+C to cleanly shut down the Qt event loop.

    Qt's C++ event loop swallows SIGINT by default, so Python's signal
    handler never runs.  Two steps fix this:

    1. Register a SIGINT handler that calls ``QApplication.quit()``.
    2. Start a 0-second ``QTimer`` that fires periodically — its callback
       is a no-op, but it transfers control back to Python long enough
       for the interpreter to invoke the signal handler.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    def _on_sigint(_signum: int, _frame) -> None:
        logger = logging.getLogger("verdiclip")
        logger.info("SIGINT received — shutting down.")
        app = QApplication.instance()
        if app:
            app.quit()

    signal.signal(signal.SIGINT, _on_sigint)

    # Periodic no-op timer lets the Python interpreter check for pending
    # signals between Qt event-loop iterations.  200 ms is imperceptible.
    _timer = QTimer()
    _timer.timeout.connect(lambda: None)
    _timer.start(200)
    # Prevent garbage collection — attach to the QApplication instance.
    app = QApplication.instance()
    if app:
        app.setProperty("_sigint_timer", _timer)


def main() -> None:
    """Launch VerdiClip in GUI (tray) mode or CLI mode."""
    _setup_logging()
    logger = logging.getLogger("verdiclip")

    from verdiclip.cli import build_parser

    parser = build_parser()
    args = parser.parse_args()

    if args.command is not None:
        # CLI mode: capture or open
        logger.info("VerdiClip CLI: %s", args.command)
        from verdiclip.cli import run_cli

        sys.exit(run_cli(args))
    else:
        # GUI tray mode (no subcommand)
        from verdiclip import __version__

        logger.info("Starting VerdiClip v%s (tray mode)", __version__)
        from verdiclip.app import VerdiClipApp

        app = VerdiClipApp(sys.argv)
        # _install_signal_handlers must be called AFTER QApplication is
        # created (inside app.run), so we hook into the run sequence.
        app.register_post_init_hook(_install_signal_handlers)
        sys.exit(app.run())


if __name__ == "__main__":
    main()
