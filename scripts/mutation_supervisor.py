"""Stop the owned mutation process group before permitting cache snapshots."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

RECEIPT = Path("mutation-quiescence.json")


def identity() -> dict[str, str]:
    """Bind retirement proof to the exact hosted execution attempt."""
    result = {
        name: os.environ.get(name, "")
        for name in (
            "GITHUB_SHA",
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
        )
    }
    if not all(result.values()):
        raise ValueError("Missing hosted execution identity")
    return result


def group_exists(group: int) -> bool:
    """Treat permission errors as uncertainty, never proof of retirement."""
    try:
        getattr(os, "killpg")(group, 0)  # noqa: B009 - POSIX-only runtime API
    except ProcessLookupError:
        return False
    return True


def retire(child: subprocess.Popen[bytes], grace: float = 10.0) -> bool:
    """Retire only our freshly created process group, with bounded escalation."""
    for signum in (signal.SIGINT, signal.SIGTERM, 9):  # POSIX SIGKILL
        child.poll()
        if not group_exists(child.pid):
            return True
        with suppress(ProcessLookupError):
            getattr(os, "killpg")(child.pid, signum)  # noqa: B009 - POSIX API
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            child.poll()
            if not group_exists(child.pid):
                return True
            time.sleep(0.05)
    child.poll()
    return not group_exists(child.pid)


def write_proof(proof: dict[str, Any]) -> None:
    """Publish one complete receipt, retaining fail-closed prior state on error."""
    temporary = RECEIPT.with_suffix(".pending")
    temporary.write_text(json.dumps(proof), encoding="utf-8")
    temporary.replace(RECEIPT)


def execution_budget(seconds: float) -> float:
    """Reserve cleanup before the unchanged hosted job deadline."""
    if os.name != "posix" or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Expected POSIX and a positive execution budget")
    job_deadline = float(os.environ["MUTATION_JOB_DEADLINE"])
    if not math.isfinite(job_deadline):
        raise ValueError("Invalid job cleanup deadline")
    seconds = min(seconds, job_deadline - time.time())
    if seconds <= 0:
        raise ValueError("No mutation time remains before cleanup reserve")
    return seconds


def supervise(command: list[str], seconds: float) -> int:
    """Preserve failure while independently recording verified writer shutdown."""
    seconds = execution_budget(seconds)
    proof: dict[str, Any] = {"identity": identity(), "quiescent": False}
    write_proof(proof)
    interrupted: list[int] = []
    result = 1

    def stop(signum: int, _frame: object) -> None:
        nonlocal result
        if not interrupted:
            interrupted.append(signum)
        result = 128 + interrupted[0]

    previous = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        child: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603 - trusted workflow argv; no shell
            command, start_new_session=True
        )
        try:
            proof["process_group"] = child.pid
            write_proof(proof)
            deadline = time.monotonic() + seconds
            while (
                child.poll() is None and not interrupted and time.monotonic() < deadline
            ):
                time.sleep(0.1)
            result = child.returncode if child.returncode is not None else 124
            if interrupted:
                result = 128 + interrupted[0]
        finally:
            proof["quiescent"] = retire(child)
            if interrupted:
                result = 128 + interrupted[0]
            proof["exit_code"] = result
            write_proof(proof)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if not isinstance(result, int):
        raise TypeError("Invalid child exit status")
    return result if proof["quiescent"] else 1


def verify() -> None:
    """Never seal or report a cache whose writers are still unaccounted for."""
    proof = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if proof.get("identity") != identity() or proof.get("quiescent") is not True:
        raise ValueError("Mutation writer retirement is unverified")


def main() -> None:
    """Run or verify the narrowly owned mutation process lifecycle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--seconds", type=float, default=14400)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "verify":
        verify()
    else:
        command = args.command
        if command[:1] == ["--"]:
            command = command[1:]
        if not command:
            parser.error("Missing command")
        raise SystemExit(supervise(command, args.seconds))


if __name__ == "__main__":
    main()
