"""Pick a way to start VerdiClip that opens no console window.

Shortcuts, the Startup entry, and Explorer's Open with command all launch the
tray app. Windows opens a console for any console-subsystem program started
outside a terminal, and it stays open for as long as the program runs. A
virtual environment's ``pythonw.exe`` is not always a GUI program: uv writes
its own console launcher under that name. So candidates are checked by their
PE header rather than trusted by name, and only Python's own executables are
ever used. Some machines also block ``pythonw.exe`` by policy, so a candidate
must start a trial run before it is used. Without a usable ``pythonw.exe``, the
console ``python.exe`` relays instead: its console shows only for a moment.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

MODULE_ARGUMENTS: Final = "-m verdiclip"
CREATE_NO_WINDOW: Final = 0x08000000
PROBE_TIMEOUT_S: Final = 15.0

# Run by the base interpreter's pythonw.exe (or python.exe): start the environment's own
# interpreter (passed as the first argument) with no console, then exit.
RELAY_CODE: Final = (
    "import subprocess,sys;"
    f"subprocess.Popen(sys.argv[1:],creationflags={CREATE_NO_WINDOW})"
)

_RELAY_PREFIX: Final = f'-c "{RELAY_CODE}" "'

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

    @property
    def environment(self) -> Path:
        """Return the interpreter whose environment runs the app.

        For the relay, ``program`` is the shared base interpreter and the
        environment's own interpreter is the first quoted argument.
        """
        if self.is_relay:
            return Path(self.arguments[len(_RELAY_PREFIX) :].partition('"')[0])
        return self.program

    @property
    def is_relay(self) -> bool:
        """Return whether ``program`` only starts the app with no console."""
        return self.arguments.startswith(_RELAY_PREFIX)

    @classmethod
    def parse(cls, command_line: str) -> LaunchCommand:
        """Split a command line into its program and the rest."""
        if command_line.startswith('"'):
            program, _, rest = command_line[1:].partition('"')
        else:
            program, _, rest = command_line.partition(" ")
        return cls(Path(program), rest.lstrip())


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


@cache
def can_run(program: Path) -> bool:
    """Return whether ``program`` starts and runs Python here.

    Policy such as AppLocker can block ``pythonw.exe`` while allowing
    ``python.exe``; Windows then refuses to start it, or stops it at once.
    """
    try:
        result = subprocess.run(  # noqa: S603 - a Python executable we chose
            [str(program), "-c", "pass"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        logger.warning("%s can't run here: %s", program, err)
        return False
    if result.returncode != 0:
        logger.warning("%s exited with %s", program, result.returncode)
    return result.returncode == 0


def _relay(program: Path, executable: Path) -> LaunchCommand:
    """Return ``program`` starting ``executable`` with no console, then exiting."""
    return LaunchCommand(
        program, f'-c "{RELAY_CODE}" "{executable}" {MODULE_ARGUMENTS}'
    )


def tray_launch(
    executable: Path,
    base_prefix: Path,
    runs: Callable[[Path], bool] = can_run,
) -> LaunchCommand:
    """Return the console-less way to start VerdiClip.

    ``executable`` is the running interpreter and ``base_prefix`` the Python
    installation behind its environment. A real ``pythonw.exe`` next to
    ``executable`` runs the module directly. Otherwise the installation's
    ``pythonw.exe`` relays to ``executable`` with no console window, which
    keeps the environment's packages. A ``pythonw.exe`` only counts when
    ``runs`` says it can start. With neither, the installation's ``python.exe``
    (or ``executable``) relays instead, and its console flashes while it does.
    """
    pythonw = executable.with_name("pythonw.exe")
    if is_windowless(pythonw) and runs(pythonw):
        return LaunchCommand(pythonw, MODULE_ARGUMENTS)
    base_pythonw = base_prefix / "pythonw.exe"
    if is_windowless(base_pythonw) and runs(base_pythonw):
        return _relay(base_pythonw, executable)
    logger.warning("No usable pythonw.exe for %s; relaying with a console", executable)
    base_python = base_prefix / "python.exe"
    return _relay(base_python if base_python.is_file() else executable, executable)


def is_other_install(current: LaunchCommand, ours: LaunchCommand) -> bool:
    """Return whether ``current`` starts another environment, and starts it well.

    Environments are told apart by their interpreter's folder, so moving
    between ``python.exe``, ``pythonw.exe``, and the relay keeps them the same.
    Another environment only counts while it still exists and ``current``
    starts it without a lasting console, directly or through a relay: a
    command from before console-less launches (``python.exe -m verdiclip``, or
    uv's console ``pythonw.exe``) is stale, and replacing it is the only way it
    gets repaired.
    """
    environment = current.environment
    return (
        environment.parent != ours.environment.parent
        and environment.is_file()
        and (
            is_windowless(current.program)
            or (current.is_relay and current.program.is_file())
        )
    )
