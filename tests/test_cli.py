"""Test command-line parsing without launching application workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from verdiclip.cli import Command, CommandLine, HeadlessMode
from verdiclip.geometry import Rect


class TestCommandLine:
    """Typed CLI requests and argparse error handling."""

    @pytest.mark.parametrize(
        "args",
        [[], ["--log-level", "DEBUG"], ["--log-level", "ERROR"]],
        ids=["default", "debug", "error"],
    )
    def test_tray(self, args: list[str]) -> None:
        """No subcommand launches the tray with the chosen logging level."""
        parser = CommandLine()

        request = parser.parse(args)

        assert request.command is Command.TRAY
        assert request.log_level == (args[-1] if args else "INFO")

    @pytest.mark.parametrize(
        "args",
        [
            ["--log-level", "DEBUG", "capture", "screen"],
            ["capture", "screen", "--log-level", "DEBUG"],
            ["open", "image.png", "--log-level", "DEBUG"],
        ],
        ids=["before", "after-capture", "after-open"],
    )
    def test_global_log_level(self, args: list[str]) -> None:
        """Logging options work before and after subcommands."""
        parser = CommandLine()

        request = parser.parse(args)

        assert request.log_level == "DEBUG"

    def test_region_capture(self) -> None:
        """Region capture parses negative origins, output, delay, and clipboard."""
        parser = CommandLine()

        request = parser.parse(
            [
                "capture",
                "region",
                "--region=-20,-30,100,200",
                "--output",
                "shot.png",
                "--delay",
                "0.5",
                "--clipboard",
            ]
        )

        assert request.command is Command.CAPTURE
        assert request.mode is HeadlessMode.REGION
        assert request.region == Rect(-20, -30, 100, 200)
        assert request.output == Path("shot.png")
        assert request.delay == 0.5
        assert request.to_clipboard

    def test_open(self) -> None:
        """Open preserves file order as typed paths."""
        parser = CommandLine()

        request = parser.parse(["open", "one.png", "two.jpg"])

        assert request.command is Command.OPEN
        assert request.files == (Path("one.png"), Path("two.jpg"))

    @pytest.mark.parametrize(
        "args",
        [
            ["unknown"],
            ["capture", "unknown"],
            ["capture", "region"],
            ["capture", "region", "--region=1,2,3"],
            ["capture", "region", "--region=a,2,3,4"],
            ["capture", "region", "--region=1,2,0,4"],
            ["capture", "screen", "--delay=-1"],
            ["open"],
            ["--log-level", "TRACE"],
        ],
        ids=[
            "command",
            "mode",
            "missing-region",
            "short-region",
            "noninteger",
            "zero-width",
            "negative-delay",
            "missing-files",
            "log-level",
        ],
    )
    def test_invalid(self, args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
        """Invalid input exits with code two and an argparse error."""
        parser = CommandLine()

        with pytest.raises(SystemExit, match="2") as caught:
            parser.parse(args)

        assert caught.value.code == 2
        assert "error:" in capsys.readouterr().err

    def test_window_defaults(self) -> None:
        """Window capture retains optional capture defaults."""
        parser = CommandLine()

        request = parser.parse(["capture", "window"])

        assert request.mode is HeadlessMode.WINDOW
        assert request.region is None
        assert request.output is None
        assert request.delay == 0
        assert not request.to_clipboard
