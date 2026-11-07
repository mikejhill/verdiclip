"""Predeclared ten-process diagnostic batch; never retry an unexpected result."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import qt_target_diagnostic as proof

COVERAGE_MINIMUM = 90
ATTEMPTS = 10
SLOT_SECONDS = 51.0
CLEANUP_SECONDS = 3.0
BATCH_SECONDS = 540.0


def read(path: Path, limit: int) -> Any:  # noqa: ANN401 - admission validates records
    """Read one bounded complete evidence file without stat/read races."""
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("batch evidence byte limit")
    return json.loads(raw)


def admit(  # noqa: C901, PLR0912, PLR0915 - fixed evidence contract
    output: Path, code: int, nodes: list[str], used_epochs: set[str]
) -> dict[str, Any]:
    """Require complete non-reproduction, including genuine coverage-only exit1."""
    receipt = read(output / "receipt.json", proof.MAX_JSON)
    epoch = receipt["epoch"]
    if (
        not isinstance(epoch, str)
        or re.fullmatch(r"[0-9a-f]{32}", epoch) is None
        or epoch in used_epochs
        or receipt["base"] != proof.BASE
        or receipt["seed"] != proof.SEED
        or receipt["status"] != "non-reproduction"
        or receipt["failure"] is not None
        or receipt["target_reached"] is not True
        or receipt["target_outcome"] != "passed"
        or receipt["cleanup_confirmed"] is not True
        or receipt["native_stack_available"] is not False
        or receipt["debugger_intervention"] is not False
        or receipt["collection_scope"] != "full-collection-prefix-execution"
        or type(code) is not int
        or code not in (0, 1)
        or type(receipt["exit_code"]) is not int
        or receipt["exit_code"] != code
    ):
        raise ValueError("unexpected inner verdict/session")
    cleanup = receipt["cleanup"]["pytest"]
    if (
        cleanup["confirmed"] is not True
        or cleanup["errors"]
        or cleanup["remaining_pids"]
        or cleanup["deadline_exceeded"] is not False
        or cleanup["direct_exits"] != [code]
        or receipt["inspection_close_errors"]
    ):
        raise ValueError("inner cleanup evidence mismatch")
    runtime = read(output / "runtime.json", 4096)
    if (
        runtime["python"] != [3, 13, 17]
        or runtime["pyside"] != "6.11.3"
        or runtime["qt"] != "6.11.3"
        or runtime["qpa_override"] is not None
    ):
        raise ValueError("runtime mismatch")
    source_hash = proof.digest(proof.ROOT / "scripts/qt_hang_diagnostic/inputs.json")
    collection = read(
        output / "full-collection.json", proof.FullCollection.MAX_ORDER_BYTES
    )
    selected = collection["selected"]
    if (
        collection["epoch"] != epoch
        or collection["source_sha256"] != source_hash
        or type(collection["discovered"]) is not int
        or collection["discovered"] != proof.FullCollection.DISCOVERED
        or len(selected) != proof.FullCollection.SELECTED
        or len(set(selected)) != proof.FullCollection.SELECTED
        or selected[: proof.NODE_COUNT] != nodes
        or len(collection["excluded"]) != len(proof.FullCollection.EXCLUDED)
        or set(collection["excluded"]) != proof.FullCollection.EXCLUDED
        or collection["original_nextitem"] != selected[proof.NODE_COUNT]
        or collection["historical_windows_suffix_verified"] is not False
        or read(output / "collected.json", proof.MAX_JSON) != nodes
    ):
        raise ValueError("collection mismatch")
    with (output / "phases.jsonl").open("rb") as stream:
        raw = stream.read(proof.MAX_JOURNAL_BYTES + 1)
    if len(raw) > proof.MAX_JOURNAL_BYTES or not raw.endswith(b"\n"):
        raise ValueError("journal byte boundary")
    lines = raw.splitlines()
    if len(lines) != proof.MAX_EVENTS:
        raise ValueError("journal event count")
    last = -1.0
    times = []
    for index, line in enumerate(lines):
        if len(line) + 1 > proof.MAX_EVENT_BYTES:
            raise ValueError("journal record bound")
        event = json.loads(line)
        phase = ("start", "setup", "call", "teardown")[index % 4]
        stamp = proof.finite_time(event["monotonic"])
        if (
            set(event)
            != {
                "epoch",
                "source_sha256",
                "sequence",
                "nodeid",
                "phase",
                "outcome",
                "monotonic",
            }
            or event["epoch"] != epoch
            or event["source_sha256"] != source_hash
            or type(event["sequence"]) is not int
            or event["sequence"] != index + 1
            or event["nodeid"] != nodes[index // 4]
            or event["phase"] != phase
            or event["outcome"] != (None if phase == "start" else "passed")
            or stamp < last
        ):
            raise ValueError("journal order/identity/outcome")
        last = stamp
        times.append(stamp)
    target, identity = proof.target_snapshot(output / "target.json")
    window = proof.TargetWindow(times[0], epoch, nodes)
    window.admit(target, identity, times[-1])
    window.completed(read(output / "target-done.json", proof.MAX_JSON), times[-1])
    if identity != receipt["target_receipt_sha256"] or target["started"] != times[-4]:
        raise ValueError("target/journal mismatch")
    result = read(output / "pytest-exit.json", 4096)
    if (
        result != receipt["pytest_exit"]
        or result["epoch"] != epoch
        or type(result["exit_code"]) is not int
        or result["exit_code"] != code
        or result["target_phases_passed"] is not True
    ):
        raise ValueError("process/pytest exit mismatch")
    with (output / "pytest.log").open("rb") as stream:
        log = stream.read(proof.MAX_LOG + 1)
    if len(log) > proof.MAX_LOG:
        raise ValueError("pytest log bound")
    coverage = result["coverage_failure"]
    if code == 1:
        match = re.fullmatch(
            r"FAIL Required test coverage of 90\.0% not reached\. "
            r"Total coverage: ([0-9]+\.[0-9]+)%",
            coverage or "",
        )
        if (
            match is None
            or not 0 <= float(match[1]) < COVERAGE_MINIMUM
            or coverage.encode() not in log
        ):
            raise ValueError("exit1 is not verified coverage-only failure")
        if b"INTERNALERROR>" in log or b"FAILED " in log or b"ERROR " in log:
            raise ValueError("additional pytest failure")
    elif coverage is not None:
        raise ValueError("coverage receipt conflicts with zero exit")
    used_epochs.add(epoch)
    return {
        "epoch": epoch,
        "process_exit": code,
        "coverage_failure": coverage,
        "target_outcome": "passed",
        "receipt_sha256": proof.digest(output / "receipt.json"),
        "journal_sha256": proof.digest(output / "phases.jsonl"),
    }


def check(deadline: float) -> None:
    """No evidence operation may consume and then reset its original deadline."""
    if time.monotonic() >= deadline:
        raise TimeoutError("batch/slot deadline exceeded")


def run(source: Path, output: Path) -> int:  # noqa: C901, PLR0915 - one bounded ownership loop
    """Run at most ten fresh owned supervisors, stopping at the first exception."""
    output.mkdir(parents=True, exist_ok=False)
    nodes = proof.validate_inputs(source)
    proof.validate_inputs(proof.ROOT)
    manifest = {
        "batch_epoch": uuid.uuid4().hex,
        "source_base": proof.BASE,
        "source_manifest_sha256": proof.digest(
            proof.ROOT / "scripts/qt_hang_diagnostic/inputs.json"
        ),
        "supervisor_sha256": proof.digest(Path(proof.__file__)),
        "attempt_limit": ATTEMPTS,
        "inner_seconds": [30, 15, 48],
        "outer_slot_seconds": SLOT_SECONDS,
        "outer_cleanup_seconds": CLEANUP_SECONDS,
        "batch_seconds": BATCH_SECONDS,
        "limitation": (
            "Shared warmed runner/filesystem/cache; not independent samples. "
            "Non-reproduction never resolves original hang."
        ),
    }
    proof.write_once(output / "manifest.json", manifest)
    batch_deadline = time.monotonic() + BATCH_SECONDS
    used_epochs: set[str] = set()
    coverage_failed = False
    for index in range(ATTEMPTS):
        slot = output / f"attempt-{index + 1:02d}"
        slot.mkdir(exist_ok=False)
        verdict: dict[str, Any] = {
            "index": index + 1,
            "continued": False,
            "failure": None,
            "natural_exit_empty": False,
            "forced_cleanup": False,
        }
        job = None
        deadline = min(time.monotonic() + SLOT_SECONDS, batch_deadline)
        try:
            check(deadline)
            proof.validate_inputs(source)
            check(deadline)
            job = proof.new_job()
            check(deadline)
            _, process = job.start(
                [
                    sys.executable,
                    str(proof.ROOT / "scripts/qt_target_diagnostic.py"),
                    "--source",
                    str(source),
                    "--output",
                    str(slot),
                ],
                source,
            )
            while True:
                check(deadline)
                code = job.wait(process, 0)
                members = job.pids()
                check(deadline)
                if code is not None and not members:
                    verdict["natural_exit_empty"] = True
                    verdict["evidence"] = admit(slot, code, nodes, used_epochs)
                    check(deadline)
                    break
                time.sleep(0.01)
            owner, job = job, None
            errors = []
            for handle in [*owner.processes, owner.handle]:
                try:
                    if not owner.api.CloseHandle(handle):
                        errors.append("CloseHandle failed")
                except Exception as error:  # noqa: BLE001 - attempt every owned close
                    errors.append(repr(error))
            verdict["close_errors"] = errors
            check(deadline)
            if errors:
                raise RuntimeError("natural outer handle close failed")  # noqa: TRY301
            verdict["continued"] = True
            coverage_failed |= code == 1
        except Exception as error:  # noqa: BLE001 - preserve first outcome and stop
            verdict["failure"] = repr(error)
        finally:
            if job is not None:
                verdict["forced_cleanup"] = True
                verdict["cleanup"] = proof.retire(
                    [("supervisor", job)],
                    min(
                        time.monotonic() + CLEANUP_SECONDS,
                        deadline + CLEANUP_SECONDS,
                        batch_deadline,
                    ),
                )
                verdict["continued"] = False
            proof.write_once(slot / "batch-verdict.json", verdict)
        try:
            check(deadline)
        except Exception as error:  # noqa: BLE001 - late verdict persistence cannot admit continuation
            proof.write_once(
                slot / "batch-terminal-error.json", {"failure": repr(error)}
            )
            return 1
        if not verdict["continued"]:
            return 1
    return 1 if coverage_failed else 0


def main() -> int:
    """Accept only source/output locations, never arbitrary executable commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return run(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
