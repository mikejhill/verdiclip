"""Run one ``verdiclip ctl`` request and print its JSON result."""

from __future__ import annotations

import json
from typing import TextIO

from verdiclip.automation.client import ControlClient
from verdiclip.automation.protocol import AutomationError, ExitCode
from verdiclip.document.annotations import JsonObject


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
