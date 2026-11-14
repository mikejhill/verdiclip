"""Run one ``verdiclip ctl`` request and print its JSON result."""

from __future__ import annotations

import ctypes
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final, TextIO

from verdiclip.automation.client import DEFAULT_TIMEOUT_S, ControlClient
from verdiclip.automation.protocol import AutomationError, ErrorCode, ExitCode
from verdiclip.automation.skills import SkillScope, install_skill
from verdiclip.document.annotations import JsonObject, JsonValue

LOCAL_METHODS = frozenset({"install-skill"})
# Options the command line acts on itself; the app never sees them
DELAY: Final = "delay"
TIMEOUT: Final = "timeout"


def run_ctl(  # noqa: PLR0913 - streams and test seams
    method: str,
    params: JsonObject,
    stdout: TextIO,
    stderr: TextIO,
    *,
    client: ControlClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Send ``method`` to the running app; print the result or the error.

    ``params`` may hold ``delay`` (seconds to wait before sending) and
    ``timeout`` (seconds to wait for the answer); both stay on this side.

    Returns:
        The process exit code (see ``ExitCode``).
    """
    request = dict(params)
    delay = _seconds(request.pop(DELAY, None), 0.0)
    timeout = _seconds(request.pop(TIMEOUT, None), DEFAULT_TIMEOUT_S)
    try:
        if method in LOCAL_METHODS:
            result = _install(request)
        else:
            if delay:
                sleep(delay)
            if method == "capture":
                _allow_foreground()
            result = (client or ControlClient(timeout=timeout)).call(method, request)
    except AutomationError as err:
        failure = {"ok": False, "error": err.to_json()}
        stdout.write(json.dumps(failure, indent=2) + "\n")
        stderr.write(_encodable(f"verdiclip ctl {method}: {err.message}\n", stderr))
        return ExitCode.for_error(err.code)
    # ASCII escapes keep the JSON valid whatever encoding a redirected stream uses
    stdout.write(json.dumps(result, indent=2) + "\n")
    return ExitCode.OK


def _allow_foreground() -> None:  # pragma: no cover - Windows desktop
    """Let the app bring its capture overlay or chooser to the front.

    Windows only lets the process the user is working in hand out focus; a
    command run from the user's terminal passes that on, so a chooser opened
    for it takes the keyboard (Esc) at once.
    """
    if sys.platform == "win32":
        asfw_any = -1
        ctypes.WinDLL("user32").AllowSetForegroundWindow(asfw_any)


def _seconds(value: JsonValue | None, default: float) -> float:
    """Read a number of seconds the parser already validated."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _encodable(text: str, stream: TextIO) -> str:
    """Escape characters ``stream``'s encoding can't write (e.g. cp1252 pipes)."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    return text.encode(encoding, "backslashreplace").decode(encoding)


def _install(params: JsonObject) -> JsonObject:
    """Install the skill on this PC; the running app isn't involved.

    Raises:
        AutomationError: If a skill file can't be written.
    """
    agents = params.get("agents")
    names = [str(a) for a in agents] if isinstance(agents, list) else []
    folder = params.get("dir")
    try:
        written = install_skill(
            names,
            scope=SkillScope(str(params.get("scope", SkillScope.USER))),
            root=Path(folder) if isinstance(folder, str) else None,
        )
    except OSError as err:
        msg = f"Could not write the skill: {err}"
        raise AutomationError(ErrorCode.FAILED, msg) from err
    installed: list[JsonValue] = [
        {"agent": w.agent, "path": str(w.path), "replaced": w.replaced} for w in written
    ]
    return {"installed": installed}
