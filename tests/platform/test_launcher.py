"""Verify console-less launcher selection against synthetic PE headers."""

from __future__ import annotations

from pathlib import Path

import pytest

from verdiclip.platform.launcher import LaunchCommand, is_windowless, tray_launch

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

    def test_prefers_gui_pythonw(self, tmp_path: Path) -> None:
        """A real pythonw.exe runs the module."""
        python = write_pe(tmp_path / "python.exe", CONSOLE)
        pythonw = write_pe(tmp_path / "pythonw.exe", GUI)
        write_pe(tmp_path / "verdiclip-gui.exe", GUI)

        assert tray_launch(python) == LaunchCommand(pythonw, "-m verdiclip")

    def test_skips_console_pythonw_for_gui_script(self, tmp_path: Path) -> None:
        """A uv console copy named pythonw.exe gives way to the entry point."""
        python = write_pe(tmp_path / "python.exe", CONSOLE)
        write_pe(tmp_path / "pythonw.exe", CONSOLE)
        script = write_pe(tmp_path / "verdiclip-gui.exe", GUI)

        assert tray_launch(python) == LaunchCommand(script, "")

    def test_falls_back_to_running_interpreter(self, tmp_path: Path) -> None:
        """With no GUI candidate the app still starts, console and all."""
        python = write_pe(tmp_path / "python.exe", CONSOLE)
        write_pe(tmp_path / "pythonw.exe", CONSOLE)

        assert tray_launch(python) == LaunchCommand(python, "-m verdiclip")


class TestCommandLine:
    """Rendering a launch as a registry command."""

    def test_quotes_program_and_appends_arguments(self) -> None:
        """Paths with spaces stay one argument."""
        launch = LaunchCommand(Path("C:/Program Files/py/pythonw.exe"), "-m verdiclip")

        assert launch.command_line == f'"{launch.program}" -m verdiclip'

    def test_omits_empty_arguments(self) -> None:
        """An entry point needs no trailing space."""
        launch = LaunchCommand(Path("C:/env/verdiclip-gui.exe"), "")

        assert launch.command_line == f'"{launch.program}"'
