"""Entry point for VerdiClip application."""

from __future__ import annotations

import logging
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
        logger.info("Starting VerdiClip v%s (tray mode)", "0.1.0")
        from verdiclip.app import VerdiClipApp

        app = VerdiClipApp(sys.argv)
        sys.exit(app.run())


if __name__ == "__main__":
    main()
