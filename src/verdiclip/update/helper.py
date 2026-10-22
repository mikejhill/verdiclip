"""Install a VerdiClip update once the app has exited, then start it again.

VerdiClip copies this file next to its settings and runs it with the base
Python installation, outside the environment being updated: Windows won't
replace files a running program holds open. It uses only the standard library
and never imports verdiclip, whose files change under it.

Usage: ``python -I helper.py PLAN.json``. The plan names the app's process,
the commands to run, the command that starts the app again, and where to
write the log and the result the restarted app reports.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TextIO

EXIT_WAIT_S: Final = 60.0
STEP_TIMEOUT_S: Final = 900.0
POLL_S: Final = 0.2
STILL_RUNNING: Final = "VerdiClip didn't exit"
VERSION_CODE: Final = "import importlib.metadata as m; print(m.version('verdiclip'))"

_CREATE_NO_WINDOW: Final = 0x08000000
_CREATE_NEW_PROCESS_GROUP: Final = 0x00000200
_SYNCHRONIZE: Final = 0x00100000
_WAIT_TIMEOUT: Final = 0x102
NO_WINDOW: Final = _CREATE_NO_WINDOW if sys.platform == "win32" else 0


def wait_for_exit(pid: int, timeout: float) -> bool:
    """Return True once process ``pid`` has exited, False after ``timeout``."""
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415 - Windows only

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)  # noqa: FBT003
        if not handle:
            return True  # No such process any more
        try:
            waited = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        finally:
            kernel32.CloseHandle(handle)
        return int(waited) != _WAIT_TIMEOUT
    return _poll_for_exit(pid, timeout)  # pragma: no cover - POSIX development hosts


def _poll_for_exit(pid: int, timeout: float) -> bool:  # pragma: no cover - POSIX
    """Poll ``pid`` with signal 0 until it is gone."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # Alive, owned by someone else
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_S)


def run_step(argv: list[str], log: TextIO) -> str | None:
    """Run one command, logging its output; return why it failed, if it did."""
    log.write(f"$ {subprocess.list2cmdline(argv)}\n")
    log.flush()
    try:
        done = subprocess.run(  # noqa: S603 - commands VerdiClip planned
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=STEP_TIMEOUT_S,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.write(f"{err}\n")
        return f"{Path(argv[0]).name} could not run: {err}"
    if done.returncode != 0:
        return f"{Path(argv[0]).name} failed with exit code {done.returncode}"
    return None


def installed_version(python: str, log: TextIO) -> str:
    """Return the version of verdiclip that ``python`` now imports, or ""."""
    try:
        done = subprocess.run(  # noqa: S603 - the app's own interpreter
            [python, "-I", "-c", VERSION_CODE],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_S,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.write(f"Could not read the installed version: {err}\n")
        return ""
    if done.returncode != 0:
        log.write(done.stderr)
        return ""
    return done.stdout.strip()


def relaunch(command: str, log: TextIO) -> None:
    """Start the tray app again, detached from this helper."""
    if not command:
        return
    log.write(f"Starting {command}\n")
    argv: str | list[str] = command if sys.platform == "win32" else shlex.split(command)
    try:
        subprocess.Popen(  # noqa: S603 - the app's own launch command
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW
            | (_CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
            start_new_session=sys.platform != "win32",
            close_fds=True,
        )
    except OSError as err:
        log.write(f"Could not start VerdiClip: {err}\n")


def update(plan: dict[str, object], log: TextIO) -> dict[str, object]:
    """Wait for the app, run the plan's steps, and return the result to report."""
    previous, target = str(plan.get("previous", "")), str(plan.get("target", ""))
    result: dict[str, object] = {"previous": previous, "target": target}
    pid = plan.get("pid")
    if isinstance(pid, int) and not wait_for_exit(pid, EXIT_WAIT_S):
        log.write(f"Process {pid} is still running\n")
        return result | {"ok": False, "message": STILL_RUNNING}
    steps = plan.get("steps")
    for step in steps if isinstance(steps, list) else []:
        error = run_step([str(arg) for arg in step], log)
        if error is not None:
            return result | {"ok": False, "message": error}
    verify = plan.get("verify")
    installed = (
        installed_version(verify, log) if isinstance(verify, str) and verify else ""
    )
    return result | {"ok": True, "installed": installed, "message": ""}


def main(argv: list[str]) -> int:
    """Run the plan in ``argv[1]``; always start the app again afterwards."""
    plan_path = Path(argv[1])
    plan: object = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        return 2
    log_path = Path(str(plan.get("log", plan_path.with_suffix(".log"))))
    result_path = Path(str(plan.get("result", plan_path.with_name("result.json"))))
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"VerdiClip update started {datetime.now(UTC).isoformat()}\n")
        log.flush()
        result = update(plan, log)
        log.write(f"Result: {json.dumps(result)}\n")
        result["log"] = str(log_path)
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        # A VerdiClip that didn't exit is still running; don't start another
        if result.get("ok"):
            relaunch(str(plan.get("relaunch", "")), log)
        elif result.get("message") != STILL_RUNNING:
            fallback = plan.get("fallback") or plan.get("relaunch", "")
            relaunch(str(fallback), log)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
