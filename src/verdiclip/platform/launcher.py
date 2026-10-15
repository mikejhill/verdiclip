"""Pick a program that starts VerdiClip without opening a console window.

Shortcuts, the Startup entry, and Explorer's Open with command all launch the
tray app. Windows opens a console for any console-subsystem program started
outside a terminal, and it stays open for as long as the program runs. A
``pythonw.exe`` is not always a GUI program: uv can write one into a virtual
environment as a copy of its console launcher. So each candidate is checked by
its PE header rather than trusted by name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

GUI_SCRIPT: Final = "verdiclip-gui.exe"
MODULE_ARGUMENTS: Final = "-m verdiclip"

_PE_POINTER_OFFSET: Final = 0x3C
_PE_SIGNATURE: Final = b"PE\0\0"
_SUBSYSTEM_OFFSET: Final = 0x5C  # From the PE signature to OptionalHeader.Subsystem
_SUBSYSTEM_WINDOWS_GUI: Final = 2


@dataclass(frozen=True, slots=True)
class LaunchCommand:
    """A program and the arguments that start the tray app."""

    program: Path
    arguments: str

    @property
    def command_line(self) -> str:
        """Return the quoted command line, as a registry command expects."""
        quoted = f'"{self.program}"'
        return f"{quoted} {self.arguments}" if self.arguments else quoted


def is_windowless(program: Path) -> bool:
    """Return whether ``program`` is a GUI-subsystem executable.

    Missing, unreadable, or malformed files count as not windowless.
    """
    try:
        with program.open("rb") as file:
            file.seek(_PE_POINTER_OFFSET)
            signature_at = int.from_bytes(file.read(4), "little")
            file.seek(signature_at)
            if file.read(4) != _PE_SIGNATURE:
                return False
            file.seek(signature_at + _SUBSYSTEM_OFFSET)
            subsystem = int.from_bytes(file.read(2), "little")
    except OSError:
        return False
    return subsystem == _SUBSYSTEM_WINDOWS_GUI


def tray_launch(executable: Path) -> LaunchCommand:
    """Return the console-less way to start VerdiClip from ``executable``'s folder.

    Prefers the environment's ``pythonw.exe``, then the ``verdiclip-gui``
    entry point, and falls back to ``executable`` itself when neither is a
    GUI program.
    """
    candidates = (
        LaunchCommand(executable.with_name("pythonw.exe"), MODULE_ARGUMENTS),
        LaunchCommand(executable.with_name(GUI_SCRIPT), ""),
    )
    for candidate in candidates:
        if is_windowless(candidate.program):
            return candidate
    logger.warning("No console-less launcher next to %s", executable)
    return LaunchCommand(executable, MODULE_ARGUMENTS)
