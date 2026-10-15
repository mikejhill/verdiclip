"""Pick a way to start VerdiClip that opens no console window.

Shortcuts, the Startup entry, and Explorer's Open with command all launch the
tray app. Windows opens a console for any console-subsystem program started
outside a terminal, and it stays open for as long as the program runs. A
virtual environment's ``pythonw.exe`` is not always a GUI program: uv writes
its own console launcher under that name. So candidates are checked by their
PE header rather than trusted by name, and only Python's own executables are
ever used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

MODULE_ARGUMENTS: Final = "-m verdiclip"
CREATE_NO_WINDOW: Final = 0x08000000

# Run by the base interpreter's pythonw.exe: start the environment's own
# interpreter (passed as the first argument) with no console, then exit.
RELAY_CODE: Final = (
    "import subprocess,sys;"
    f"subprocess.Popen(sys.argv[1:],creationflags={CREATE_NO_WINDOW})"
)

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
        return f'"{self.program}" {self.arguments}'


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


def tray_launch(executable: Path, base_prefix: Path) -> LaunchCommand:
    """Return the console-less way to start VerdiClip.

    ``executable`` is the running interpreter and ``base_prefix`` the Python
    installation behind its environment. A real ``pythonw.exe`` next to
    ``executable`` runs the module directly. Otherwise the installation's
    ``pythonw.exe`` relays to ``executable`` with no console window, which
    keeps the environment's packages. With neither, ``executable`` runs the
    module itself and Windows shows its console.
    """
    pythonw = executable.with_name("pythonw.exe")
    if is_windowless(pythonw):
        return LaunchCommand(pythonw, MODULE_ARGUMENTS)
    base_pythonw = base_prefix / "pythonw.exe"
    if is_windowless(base_pythonw):
        return LaunchCommand(
            base_pythonw, f'-c "{RELAY_CODE}" "{executable}" {MODULE_ARGUMENTS}'
        )
    logger.warning("No console-less Python for %s", executable)
    return LaunchCommand(executable, MODULE_ARGUMENTS)
