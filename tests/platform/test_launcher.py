"""Verify console-less launcher selection against synthetic PE headers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from verdiclip.platform.launcher import (
    CREATE_NO_WINDOW,
    RELAY_CODE,
    LaunchCommand,
    is_windowless,
    tray_launch,
)

GUI = 2
CONSOLE = 3


def write_pe(path: Path, subsystem: int, *, signature: bytes = b"PE\0\0") -> Path:
    """Write the smallest file whose PE header carries ``subsystem``."""
    header = bytearray(0x200)
    header[0:2] = b"MZ"
    pe_at = 0x80
    header[0x3C:0x40] = pe_at.to_bytes(4, "little")
    header[pe_at : pe_at + 4] = signature
    header[pe_at + 0x5C : pe_at + 0x5E] = subsystem.to_bytes(2, "little")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header))
    return path


class TestIsWindowless:
    """PE subsystem detection."""

    def test_gui_subsystem_is_windowless(self, tmp_path: Path) -> None:
        """A Windows GUI program opens no console."""
        assert is_windowless(write_pe(tmp_path / "pythonw.exe", GUI))

    def test_console_subsystem_is_not(self, tmp_path: Path) -> None:
        """A console program named pythonw.exe still opens a console."""
        assert not is_windowless(write_pe(tmp_path / "pythonw.exe", CONSOLE))

    def test_missing_file_is_not(self, tmp_path: Path) -> None:
        """A program that does not exist cannot be launched windowless."""
        assert not is_windowless(tmp_path / "pythonw.exe")

    @pytest.mark.parametrize("content", [b"", b"MZ", b"not an executable" * 10])
    def test_truncated_or_foreign_file_is_not(
        self, tmp_path: Path, content: bytes
    ) -> None:
        """Files without a PE header are rejected without raising."""
        program = tmp_path / "pythonw.exe"
        program.write_bytes(content)

        assert not is_windowless(program)

    def test_wrong_signature_is_not(self, tmp_path: Path) -> None:
        """The subsystem only counts behind a real PE signature."""
        program = write_pe(tmp_path / "pythonw.exe", GUI, signature=b"NE\0\0")

        assert not is_windowless(program)


class TestTrayLaunch:
    """Choosing the program the shortcuts and Open with command run."""

    @pytest.fixture
    def env(self, tmp_path: Path) -> Path:
        """Return a venv Scripts folder with a console python.exe."""
        scripts = tmp_path / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        write_pe(scripts / "python.exe", CONSOLE)
        return scripts

    @pytest.fixture
    def base(self, tmp_path: Path) -> Path:
        """Return a base installation with a GUI pythonw.exe."""
        base = tmp_path / "Python 3.13"
        base.mkdir()
        write_pe(base / "pythonw.exe", GUI)
        return base

    def test_prefers_gui_pythonw_beside_interpreter(
        self, env: Path, base: Path
    ) -> None:
        """Python's own venv launcher runs the module directly."""
        pythonw = write_pe(env / "pythonw.exe", GUI)

        assert tray_launch(env / "python.exe", base) == LaunchCommand(
            pythonw, "-m verdiclip"
        )

    def test_console_pythonw_relays_through_base(self, env: Path, base: Path) -> None:
        """A uv console launcher named pythonw.exe is bypassed, not trusted."""
        write_pe(env / "pythonw.exe", CONSOLE)
        python = env / "python.exe"

        launch = tray_launch(python, base)

        assert launch == LaunchCommand(
            base / "pythonw.exe", f'-c "{RELAY_CODE}" "{python}" -m verdiclip'
        )

    def test_missing_pythonw_relays_through_base(self, env: Path, base: Path) -> None:
        """An environment without pythonw.exe also uses the base installation."""
        assert tray_launch(env / "python.exe", base).program == base / "pythonw.exe"

    def test_falls_back_to_running_interpreter(self, env: Path, tmp_path: Path) -> None:
        """With no GUI Python anywhere the app still starts, console and all."""
        write_pe(env / "pythonw.exe", CONSOLE)
        python = env / "python.exe"

        assert tray_launch(python, tmp_path / "missing") == LaunchCommand(
            python, "-m verdiclip"
        )


class TestRelayCode:
    """The one-liner the base pythonw.exe runs."""

    def test_starts_its_arguments_without_a_console(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Argv after -c becomes the child command, flagged CREATE_NO_WINDOW."""
        calls: list[tuple[list[str], int]] = []

        def popen(args: list[str], creationflags: int) -> None:
            calls.append((args, creationflags))

        monkeypatch.setattr(subprocess, "Popen", popen)
        monkeypatch.setattr("sys.argv", ["-c", "C:/venv/python.exe", "-m", "x"])

        exec(RELAY_CODE, {})  # noqa: S102 - the exact code the shortcut runs

        assert calls == [(["C:/venv/python.exe", "-m", "x"], CREATE_NO_WINDOW)]

    def test_has_no_double_quotes(self) -> None:
        """The code sits inside a double-quoted command-line argument."""
        assert '"' not in RELAY_CODE


class TestCommandLine:
    """Rendering a launch as a registry command."""

    def test_quotes_program_and_appends_arguments(self) -> None:
        """Paths with spaces stay one argument."""
        launch = LaunchCommand(Path("C:/Program Files/py/pythonw.exe"), "-m verdiclip")

        assert launch.command_line == f'"{launch.program}" -m verdiclip'

    @pytest.mark.parametrize("quoted", [True, False])
    def test_parse_round_trips(self, *, quoted: bool) -> None:
        """A registry command splits back into program and arguments."""
        program = Path(
            "C:/Program Files/py/pythonw.exe" if quoted else "C:/py/pythonw.exe"
        )
        launch = LaunchCommand(program, '-m verdiclip open "%1"')
        text = launch.command_line if quoted else f"{program} {launch.arguments}"

        assert LaunchCommand.parse(text) == launch


class TestEnvironment:
    """Which environment a launch runs, through the relay or directly."""

    def test_direct_launch_is_its_program(self) -> None:
        """A venv pythonw.exe runs its own environment."""
        program = Path("C:/main/.venv/Scripts/pythonw.exe")

        assert LaunchCommand(program, "-m verdiclip").environment == program

    def test_relay_is_the_interpreter_it_starts(self, tmp_path: Path) -> None:
        """The relay's base pythonw.exe is shared; the quoted interpreter isn't."""
        write_pe(tmp_path / "pythonw.exe", GUI)
        interpreter = tmp_path / "venv" / "python.exe"

        launch = tray_launch(interpreter, tmp_path)

        assert launch.program == tmp_path / "pythonw.exe"
        assert launch.environment == interpreter
