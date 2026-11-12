"""Run one ``verdiclip ctl`` request and print its JSON result."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from verdiclip.automation.client import ControlClient
from verdiclip.automation.protocol import AutomationError, ErrorCode, ExitCode
from verdiclip.automation.skills import SkillScope, install_skill
from verdiclip.document.annotations import JsonObject, JsonValue

LOCAL_METHODS = frozenset({"install-skill"})


def run_ctl(
    method: str,
    params: JsonObject,
    stdout: TextIO,
    stderr: TextIO,
    *,
    client: ControlClient | None = None,
) -> int:
    """Send ``method`` to the running app; print the result or the error.

    Returns:
        The process exit code (see ``ExitCode``).
    """
    try:
        if method in LOCAL_METHODS:
            result = _install(params)
        else:
            result = (client or ControlClient()).call(method, params)
    except AutomationError as err:
        failure = {"ok": False, "error": err.to_json()}
        stdout.write(json.dumps(failure, indent=2) + "\n")
        stderr.write(_encodable(f"verdiclip ctl {method}: {err.message}\n", stderr))
        return ExitCode.for_error(err.code)
    # ASCII escapes keep the JSON valid whatever encoding a redirected stream uses
    stdout.write(json.dumps(result, indent=2) + "\n")
    return ExitCode.OK


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
