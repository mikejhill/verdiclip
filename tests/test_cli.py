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

    def test_ctl_queries(self) -> None:
        """Ctl status, list and get become control requests."""
        parser = CommandLine()

        status = parser.parse(["ctl", "status"])
        listed = parser.parse(["ctl", "list", "--log-level", "DEBUG"])
        got = parser.parse(["ctl", "get", "--doc", "abc"])
        active = parser.parse(["ctl", "get"])

        assert status.command is Command.CTL
        assert (status.method, status.params) == ("status", {})
        assert (listed.method, listed.log_level) == ("list", "DEBUG")
        assert (got.method, got.params) == ("get", {"doc": "abc"})
        assert active.params == {}

    def test_ctl_render_defaults(self) -> None:
        """Render downscales to 1568 px, without grid or base, by default."""
        request = CommandLine().parse(["ctl", "render"])

        assert request.method == "render"
        assert request.params == {"max_side": 1568, "grid": 0, "base": False}

    def test_ctl_render_options(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Render options are passed through; the output path is made absolute."""
        monkeypatch.chdir(tmp_path)

        request = CommandLine().parse(
            [
                "ctl",
                "render",
                "--doc",
                "d1",
                "-o",
                "view.png",
                "--region",
                "10,20,30,40",
                "--max-side",
                "0",
                "--grid",
                "50",
                "--base",
            ]
        )

        assert request.params == {
            "doc": "d1",
            "output": str((tmp_path / "view.png").resolve()),
            "region": [10, 20, 30, 40],
            "max_side": 0,
            "grid": 50,
            "base": True,
        }

    @pytest.mark.parametrize(
        "args",
        [
            ["ctl"],
            ["ctl", "paint"],
            ["ctl", "render", "--max-side", "-1"],
            ["ctl", "render", "--grid", "-5"],
            ["ctl", "render", "--region", "1,2,3"],
            ["ctl", "status", "--doc", "x"],
        ],
        ids=["no-method", "unknown", "max-side", "grid", "region", "status-doc"],
    )
    def test_ctl_invalid(
        self, args: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Bad ctl usage exits with code two."""
        with pytest.raises(SystemExit, match="2"):
            CommandLine().parse(args)

        assert "error:" in capsys.readouterr().err
