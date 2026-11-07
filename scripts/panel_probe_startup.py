"""Bounded startup diagnostics for the fixed owned-fixture probe only.

These phases never satisfy or extend the supervisor's three-second watchdog.
No native operations run before the original probe entrypoint.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from contextlib import suppress
from pathlib import Path

_MAX_PHASES = 5
_MAX_BYTES = 8192
_MAX_MESSAGE_CHARS = 512
_MAX_ARGUMENTS = 32
_MAX_ARGUMENT_CHARS = 4096


def diagnostic_path(arguments: list[str]) -> Path:
    """Require one explicit probe output under an already existing case directory."""
    if len(arguments) > _MAX_ARGUMENTS or any(
        len(argument) > _MAX_ARGUMENT_CHARS or "\0" in argument
        for argument in arguments
    ):
        raise ValueError("Invalid bounded probe arguments")
    if arguments.count("--output") != 1 or any(
        argument.startswith("--output=") for argument in arguments
    ):
        raise ValueError("Expected exactly one explicit --output argument")
    index = arguments.index("--output") + 1
    if index >= len(arguments):
        raise ValueError("Missing probe output")
    output = Path(arguments[index])
    if not output.is_absolute() or output.name != "probe" or output.exists():
        raise ValueError("Expected a new absolute probe output directory")
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir() or output.parent != parent:
        raise ValueError("Expected a resolved existing case directory")
    return parent / "probe-startup.json"


class StartupDiagnostic:
    """Atomically publish at most five small metadata-only phase records."""

    def __init__(self, path: Path) -> None:
        """Refuse reuse of a prior diagnostic or its interrupted pending file."""
        self.path = path
        self.pending = path.with_suffix(".pending")
        if path.exists() or self.pending.exists():
            raise ValueError("Startup diagnostic already exists")
        self.phases: list[dict[str, object]] = []

    def record(self, phase: str, error: BaseException | None = None) -> None:
        """Keep previous complete evidence if publishing the next record fails."""
        event: dict[str, object] = {"phase": phase, "monotonic": time.monotonic()}
        if error is not None:
            event["exception_type"] = type(error).__name__[:_MAX_MESSAGE_CHARS]
            event["message"] = str(error)[:_MAX_MESSAGE_CHARS]
        phases = [*self.phases, event]
        payload = json.dumps({"pid": os.getpid(), "phases": phases}).encode("utf-8")
        if len(phases) > _MAX_PHASES or len(payload) > _MAX_BYTES:
            raise ValueError("Startup diagnostic exceeds bound")
        with self.pending.open("xb") as stream:
            stream.write(payload)
        self.pending.replace(self.path)
        self.phases = phases


def main() -> int:
    """Invoke only the original probe, preserving all option arguments unchanged."""
    diagnostic = StartupDiagnostic(diagnostic_path(sys.argv[1:]))
    diagnostic.record("entered")
    try:
        diagnostic.record("import_start")
        probe = importlib.import_module("scripts.panel_uia_probe")
        diagnostic.record("import_complete")
        result = probe.main()
        diagnostic.record("returned")
    except BaseException as error:
        # Logging must never replace the original startup exception/SystemExit.
        with suppress(OSError, ValueError):
            diagnostic.record("exception", error)
        raise
    else:
        return result


if __name__ == "__main__":
    raise SystemExit(main())
