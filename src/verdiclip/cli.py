"""Command-line parsing into typed requests."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from verdiclip import VERSION
from verdiclip.geometry import Rect

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class Command(StrEnum):
    """What the process was asked to do."""

    TRAY = "tray"
    CAPTURE = "capture"
    OPEN = "open"


class HeadlessMode(StrEnum):
    """Capture modes available without the overlay."""

    SCREEN = "screen"
    WINDOW = "window"
    REGION = "region"


@dataclass(frozen=True, slots=True)
class CliRequest:
    """A parsed command line."""

    command: Command
    log_level: str = "INFO"
    mode: HeadlessMode = HeadlessMode.SCREEN
    output: Path | None = None
    region: Rect | None = None
    delay: float = 0.0
    to_clipboard: bool = False
    files: tuple[Path, ...] = ()


class CommandLine:
    """Parse ``verdiclip`` arguments."""

    def __init__(self) -> None:
        self._parser = self._build_parser()

    def parse(self, argv: Sequence[str] | None = None) -> CliRequest:
        """Return the typed request for ``argv``; exits with code 2 on bad input."""
        args = self._parser.parse_args(argv)
        level = str(args.log_level)
        if args.command is None:
            return CliRequest(command=Command.TRAY, log_level=level)
        if args.command == Command.OPEN:
            return CliRequest(
                command=Command.OPEN,
                log_level=level,
                files=tuple(Path(f) for f in args.files),
            )
        mode = HeadlessMode(args.mode)
        region = self._parse_region(args.region) if args.region else None
        if mode is HeadlessMode.REGION and region is None:
            self._parser.error("capture region needs --region X,Y,WIDTH,HEIGHT")
        if args.delay < 0:
            self._parser.error(f"--delay must be >= 0, got {args.delay}")
        return CliRequest(
            command=Command.CAPTURE,
            log_level=level,
            mode=mode,
            output=Path(args.output) if args.output else None,
            region=region,
            delay=float(args.delay),
            to_clipboard=bool(args.clipboard),
        )

    def _parse_region(self, text: str) -> Rect:
        """Parse ``X,Y,W,H`` in physical desktop pixels."""
        try:
            x, y, w, h = (int(part) for part in text.split(","))
        except ValueError:
            self._parser.error(
                f"--region must be X,Y,WIDTH,HEIGHT integers, got {text!r}"
            )
        if w <= 0 or h <= 0:
            self._parser.error(
                f"--region width and height must be positive, got {text!r}"
            )
        return Rect(x, y, w, h)

    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        """Define the arguments."""
        parser = argparse.ArgumentParser(
            prog="verdiclip",
            description=(
                "Screenshot capture and annotation. "
                "Without a command, runs in the system tray."
            ),
        )
        parser.add_argument(
            "--version", action="version", version=f"%(prog)s {VERSION}"
        )
        parser.add_argument(
            "--log-level",
            default="INFO",
            choices=LOG_LEVELS,
            help="Logging verbosity (default: %(default)s).",
        )
        # Accept global options after the subcommand too
        shared = argparse.ArgumentParser(add_help=False)
        shared.add_argument(
            "--log-level",
            default=argparse.SUPPRESS,
            choices=LOG_LEVELS,
            help="Logging verbosity.",
        )
        commands = parser.add_subparsers(dest="command")
        capture = commands.add_parser(
            "capture",
            parents=[shared],
            help="Capture without the overlay and exit.",
        )
        capture.add_argument(
            "mode", choices=[m.value for m in HeadlessMode], help="What to capture."
        )
        capture.add_argument(
            "-o",
            "--output",
            help="File to write (default: the configured folder and pattern).",
        )
        capture.add_argument(
            "--region", help="X,Y,WIDTH,HEIGHT in physical pixels (region mode)."
        )
        capture.add_argument(
            "--delay", type=float, default=0.0, help="Seconds to wait before capturing."
        )
        capture.add_argument(
            "--clipboard",
            action="store_true",
            help="Copy to the clipboard instead of saving.",
        )
        open_cmd = commands.add_parser(
            "open", parents=[shared], help="Open images in the editor."
        )
        open_cmd.add_argument("files", nargs="+", help="Image files to open.")
        return parser
