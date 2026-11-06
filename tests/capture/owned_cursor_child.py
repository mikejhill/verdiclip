"""Hosted-only disposable component child; no supervisor cursor repair."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import cast

from tests.capture.hosted_fixture_input import require_hosted
from tests.capture.owned_cursor_runtime import measure_runtime

PHASES = frozenset(
    {
        "crash-entry",
        "baseline-admitted",
        "surface-created",
        "admitted",
        "refused",
        "revoked",
        "retired",
    }
)
REASONS = frozenset(
    {
        "completed",
        "cancelled",
        "no-natural-ownership",
        "foreign-state-refused",
        "foreign-state-changed",
        "counter-refused",
        "cleanup-unconfirmed",
        "native-failure",
    }
)
FIELDS = frozenset(
    {
        "admitted",
        "balance_attempted",
        "balance_confirmed",
        "hwnd_destroyed",
        "thread_retired",
        "foreign_preserved",
        "cleanup_confirmed",
        "reason",
    }
)


def validate_result(value: object) -> dict[str, bool | str]:
    """Discard unknown keys/types rather than leaking native values or errors."""
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("invalid-component-result")
    if type(value["reason"]) is not str or value["reason"] not in REASONS:
        raise ValueError("invalid-component-reason")
    if any(type(value[key]) is not bool for key in FIELDS - {"reason"}):
        raise ValueError("invalid-component-field")
    return cast("dict[str, bool | str]", value)


def publish(path: Path, value: dict[str, object]) -> None:
    """Atomic fixed-schema publication within the exact supervisor directory."""
    temporary = path.with_suffix(".partial")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, separators=(",", ":"))
    temporary.rename(path)


def run(directory: Path, nonce: str) -> int:  # noqa: C901 - finite owned protocol and independent cleanup
    """Wait idle before starting the adapter, retaining one command observer."""
    require_hosted(os.environ)
    if os.environ.get("VERDICLIP_HOSTED_OWNED_CURSOR") != "1":
        raise ValueError("hosted-cursor-gate-required")
    if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise ValueError("invalid-scope")
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("invalid-scope-directory")
    # No stand-in is permitted in a native lane. Import only after hosted admission.
    from tests.capture.owned_cursor_native import (  # noqa: PLC0415 - hosted gate before native import
        run_owned_surface,
    )

    publish(
        directory / "ready.json",
        {
            "nonce": nonce,
            "ready": True,
            "pid": os.getpid(),
            "runtime": measure_runtime(),
        },
    )
    deadline = time.monotonic() + 5
    while not (directory / "start").exists():
        if time.monotonic() >= deadline:
            return 2
        time.sleep(0.005)
    cancel = threading.Event()
    crash_requested = threading.Event()
    done = threading.Event()
    lock = threading.Lock()
    emitted: set[str] = set()

    def commands() -> None:
        while not done.wait(0.005):
            if (directory / "crash").exists():
                crash_requested.set()
                return
            if (directory / "cancel").exists():
                cancel.set()
                return

    def emit(phase: str) -> None:
        if phase not in PHASES:
            raise ValueError("invalid-component-phase")
        with lock:
            if phase in emitted:
                raise ValueError("duplicate-component-phase")
            emitted.add(phase)
            publish(directory / (phase + ".json"), {"nonce": nonce, "phase": phase})

    watcher = threading.Thread(target=commands, name="owned-cursor-commands")
    watcher.start()
    try:
        result = validate_result(
            run_owned_surface(cancel, emit, crash_requested=crash_requested)
        )
        publish(directory / "result.json", {"nonce": nonce, "result": result})
        return 0 if result["cleanup_confirmed"] else 3
    finally:
        done.set()
        watcher.join(timeout=1)
        if watcher.is_alive():
            raise RuntimeError("command-observer-not-retired")


def main() -> int:
    """Ordinary failures remain fixed exit codes; raw output is never published."""
    try:
        if len(sys.argv) != 3:
            return 2
        return run(Path(sys.argv[1]), sys.argv[2])
    except Exception:  # noqa: BLE001 - sanitize private native exceptions at process boundary
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
