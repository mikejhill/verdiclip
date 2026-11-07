"""Bounded external owner of the inert GUI proof, not product orchestration.

Only this owned job is retired. A timeout, nonzero exit or surviving descendant
always fails qualification, even if forced cleanup subsequently succeeds.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from scripts.owned_worker_smoke import CASES
from scripts.panel_owned_job import OwnedJob

TIMEOUT_SECONDS = 45.0
MAX_RECEIPT_BYTES = 131_072


def read_proof(path: Path) -> bool:
    """Bound and validate completion evidence only after the proof process exits."""
    with path.open("rb") as stream:
        data = stream.read(MAX_RECEIPT_BYTES + 1)
    if len(data) > MAX_RECEIPT_BYTES:
        raise ValueError("Proof receipt exceeds bound")
    value = json.loads(data)
    if not isinstance(value, dict) or value.get("passed") is not True:
        return False
    cases = value.get("cases")
    return (
        isinstance(cases, list)
        and len(cases) == len(CASES)
        and all(
            isinstance(item, dict)
            and item.get("experiment") == expected
            and item.get("passed") is True
            and item.get("thread_finished") is True
            for item, expected in zip(cases, CASES, strict=True)
        )
    )


def supervise(output: Path, repository: Path) -> int:  # noqa: C901 - explicit failure receipts
    """Launch explicit venv Python in an atomic owned job and retain all outcomes."""
    output.mkdir(parents=True, exist_ok=False)
    python = repository / ".venv" / "Scripts" / "python.exe"
    result: dict[str, object] = {
        "passed": False,
        "timeout_seconds": TIMEOUT_SECONDS,
        "failure": None,
        "exit_code": None,
        "pre_cleanup_pids": None,
        "cleanup": None,
        "forced_teardown_is_qualification": False,
    }
    job: OwnedJob | None = None
    try:
        if not python.is_file():
            raise FileNotFoundError(  # noqa: TRY301 - persist setup failure too
                "Explicit project venv Python is missing"
            )
        job = OwnedJob()
        deadline = time.monotonic() + TIMEOUT_SECONDS
        _, process = job.start(
            (
                str(python),
                "-m",
                "scripts.owned_worker_smoke",
                "--output",
                str(output / "proof"),
            ),
            repository,
        )
        remaining = max(0.0, deadline - time.monotonic())
        code = job.wait(process, remaining)
        result["exit_code"] = code
        if code is None:
            result["failure"] = "proof-timeout"
        elif code != 0:
            result["failure"] = "proof-exit-failed"
        elif time.monotonic() >= deadline:
            result["failure"] = "proof-deadline-exceeded"
        else:
            pids = job.pids()
            result["pre_cleanup_pids"] = pids
            if pids:
                result["failure"] = "proof-surviving-descendants"
            elif not read_proof(output / "proof" / "receipt.json"):
                result["failure"] = "proof-receipt-failed"
            elif time.monotonic() >= deadline:
                result["failure"] = "proof-deadline-exceeded"
    except Exception as error:  # noqa: BLE001 - retain native supervisor failure
        result["failure"] = f"supervisor-error:{error!r}"
    finally:
        if job is not None:
            try:
                result["cleanup"] = job.close()
            except Exception as error:  # noqa: BLE001 - never lose timeout evidence
                result["cleanup"] = {"confirmed": False, "error": repr(error)}
        cleanup = result["cleanup"]
        result["passed"] = (
            result["failure"] is None
            and isinstance(cleanup, dict)
            and cleanup.get("confirmed") is True
        )
        (output / "supervisor.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    return int(not result["passed"])


def main() -> int:
    """Use fixed repository-relative runtime, never a shell or arbitrary target."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The owned native supervisor requires Windows")
    return supervise(args.output.resolve(), Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
