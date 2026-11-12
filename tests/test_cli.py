"""Test command-line parsing without launching application workflows."""

from __future__ import annotations

import io
import json
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

    def test_ctl_apply_reads_ops_from_a_file_or_stdin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Apply takes an ops array, or an object holding one, plus its options."""
        ops = [{"op": "delete", "ids": ["a"]}]
        path = tmp_path / "ops.json"
        path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ops)))

        from_file = CommandLine().parse(
            ["ctl", "apply", "--ops", str(path), "--expect-state", "3"]
        )
        from_stdin = CommandLine().parse(
            ["ctl", "apply", "--ops", "-", "--label", "Box it", "--select"]
        )

        assert from_file.params == {"ops": ops, "select": False, "expect_state": 3}
        assert from_stdin.params == {"ops": ops, "select": True, "label": "Box it"}

    def test_ctl_history_travel_and_select(self) -> None:
        """History, undo, redo and select become control requests."""
        parser = CommandLine()

        history = parser.parse(["ctl", "history", "--doc", "d"])
        undo = parser.parse(["ctl", "undo", "--steps", "2", "--only-cli"])
        redo = parser.parse(["ctl", "redo", "--to", "7", "--expect-state", "4"])
        select = parser.parse(["ctl", "select", "a", "b"])
        clear = parser.parse(["ctl", "select"])

        assert (history.method, history.params) == ("history", {"doc": "d"})
        assert undo.params == {"only_cli": True, "steps": 2}
        assert redo.params == {"only_cli": False, "to": 7, "expect_state": 4}
        assert (select.params, clear.params) == ({"ids": ["a", "b"]}, {"ids": []})

    def test_ctl_install_skill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Install-skill needs agents; scope defaults to user; --dir is absolute."""
        monkeypatch.chdir(tmp_path)
        parser = CommandLine()

        user = parser.parse(["ctl", "install-skill", "--agent", "claude,codex"])
        custom = parser.parse(
            [
                "ctl",
                "install-skill",
                "--agent",
                "codex",
                "--scope",
                "project",
                "--dir",
                "s",
            ]
        )

        assert user.params == {"agents": ["claude", "codex"], "scope": "user"}
        assert custom.params == {
            "agents": ["codex"],
            "scope": "project",
            "dir": str((tmp_path / "s").resolve()),
        }

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            (None, "can't read JSON"),
            ("{not json", "can't read JSON"),
            ('{"op": "delete"}', "JSON array"),
        ],
        ids=["missing", "malformed", "not-array"],
    )
    def test_ctl_apply_rejects_bad_ops(
        self,
        tmp_path: Path,
        content: str | None,
        message: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unreadable or non-array ops are usage errors."""
        path = tmp_path / "ops.json"
        if content is not None:
            path.write_text(content, encoding="utf-8")

        with pytest.raises(SystemExit, match="2"):
            CommandLine().parse(["ctl", "apply", "--ops", str(path)])

        assert message in capsys.readouterr().err

    @pytest.mark.parametrize(
        "args",
        [
            ["ctl"],
            ["ctl", "paint"],
            ["ctl", "render", "--max-side", "-1"],
            ["ctl", "render", "--grid", "-5"],
            ["ctl", "render", "--region", "1,2,3"],
            ["ctl", "status", "--doc", "x"],
            ["ctl", "apply"],
            ["ctl", "undo", "--steps", "1", "--to", "2"],
            ["ctl", "install-skill"],
            ["ctl", "install-skill", "--agent", "gpt"],
            ["ctl", "install-skill", "--agent", "claude,codex", "--dir", "x"],
        ],
        ids=[
            "no-method",
            "unknown",
            "max-side",
            "grid",
            "region",
            "status-doc",
            "apply-no-ops",
            "steps-and-to",
            "skill-no-agent",
            "skill-unknown-agent",
            "skill-dir-two-agents",
        ],
    )
    def test_ctl_invalid(
        self, args: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Bad ctl usage exits with code two."""
        with pytest.raises(SystemExit, match="2"):
            CommandLine().parse(args)

        assert "error:" in capsys.readouterr().err
