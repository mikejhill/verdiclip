"""Fixed target-aware Python-only diagnosis; no debugger path or fallback.

The external owner never waits inside Qt. Thirty seconds prepares the exact
prefix, fifteen seconds covers the target, and cleanup is capped at launch+48s.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if TYPE_CHECKING:
    from scripts.owned_worker_supervisor import ResidualObserver
    from scripts.panel_owned_job import OwnedJob

BASE = "ecb4a4cf675b701259ced8b18c1f5f4c1a32cdfa"
SEED = 2197208277
PREPARATION = 30.0
TOTAL = 15.0
OUTER = PREPARATION + TOTAL + 3.0
NODE_COUNT = 157
MAX_PID = 0xFFFFFFFF
MAX_JSON = 131072
MAX_LOG = 2 * 1024 * 1024
MAX_EVENTS = NODE_COUNT * 4
MAX_EVENT_BYTES = 4096
MAX_JOURNAL_BYTES = MAX_EVENTS * MAX_EVENT_BYTES


def new_job() -> OwnedJob:
    """Load native ownership helpers only in the external supervisor."""
    from scripts.panel_owned_job import OwnedJob  # noqa: PLC0415

    return OwnedJob()


def new_observer(job: OwnedJob) -> ResidualObserver:
    """Keep native inspection dependencies out of the pytest child."""
    from scripts.owned_worker_supervisor import ResidualObserver  # noqa: PLC0415

    return ResidualObserver(job)


def read_json(path: Path, limit: int = 131072) -> Any:  # noqa: ANN401 - validated at each admission
    """Read bounded JSON before operation-specific validation."""
    if path.stat().st_size > limit:
        raise ValueError("diagnostic JSON exceeds bound")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: object) -> None:
    """Atomically publish a small diagnostic receipt."""
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def digest(path: Path) -> str:
    """Hash an exact source or verified executable."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_inputs(source: Path) -> list[str]:
    """Reject altered fixed source bytes or test order."""
    inputs = read_json(ROOT / "scripts/qt_hang_diagnostic/inputs.json")
    prefix = ROOT / "scripts/qt_hang_diagnostic/prefix.json"
    if inputs["base"] != BASE or inputs["seed"] != SEED:
        raise ValueError("wrong fixed source identity")
    if digest(prefix) != inputs["prefix_sha256"]:
        raise ValueError("prefix digest mismatch")
    nodes = read_json(prefix)
    if len(nodes) != NODE_COUNT or len(set(nodes)) != NODE_COUNT:
        raise ValueError("expected exact 157 unique nodes")
    for name, expected in inputs["files"].items():
        if digest(source / name) != expected:
            raise ValueError(f"archived source mismatch: {name}")
    return cast("list[str]", nodes)


def checked_ready(value: dict[str, Any], epoch: str) -> int:
    """Validate session-bound identity before kernel membership checks."""
    if not isinstance(value, dict) or set(value) != {"epoch", "pid", "executable"}:
        raise ValueError("invalid child identity receipt")
    pid = value["pid"]
    if value["epoch"] != epoch or type(pid) is not int or not 0 < pid <= MAX_PID:
        raise ValueError("invalid child epoch/PID")
    if os.path.normcase(value["executable"]) != os.path.normcase(
        getattr(sys, "_base_executable", sys.executable)
    ):
        raise ValueError("unexpected actual Python executable")
    return pid


def write_once(path: Path, value: object) -> None:
    """Publish a complete immutable receipt without replacing an existing name."""
    pending = path.with_suffix(".pending")
    with pending.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")
    os.link(pending, path)
    pending.unlink()


class TargetLedger:
    """Exact ordered setup/call/teardown evidence; never retries a test."""

    def __init__(self, nodes: list[str], epoch: str, output: Path) -> None:
        self.nodes = nodes
        self.epoch = epoch
        self.output = output
        self.completed: list[dict[str, object]] = []
        self.current: str | None = None
        self.phases: dict[str, str] = {}
        self.target_started: float | None = None
        self.last_time: float | None = None
        self.failure: str | None = None
        self.source_sha256 = digest(ROOT / "scripts/qt_hang_diagnostic/inputs.json")
        self.events = 0
        self.journal_bytes = 0
        (output / "phases.jsonl").open("xb").close()

    def checkpoint(self, now: float) -> None:
        """Reject regressed/nonfinite native monotonic observations."""
        if self.failure is not None:
            raise ValueError(self.failure)
        current = finite_time(now)
        if self.last_time is not None and current < self.last_time:
            raise ValueError("child monotonic clock regressed")
        self.last_time = current

    def record(self, nodeid: str, phase: str, outcome: str | None, now: float) -> None:
        """Append one bounded event; a failed write permanently stops the ledger."""
        raw = (
            json.dumps(
                {
                    "epoch": self.epoch,
                    "source_sha256": self.source_sha256,
                    "sequence": self.events + 1,
                    "nodeid": nodeid,
                    "phase": phase,
                    "outcome": outcome,
                    "monotonic": now,
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if (
            self.events >= MAX_EVENTS
            or len(raw) > MAX_EVENT_BYTES
            or self.journal_bytes + len(raw) > MAX_JOURNAL_BYTES
        ):
            raise ValueError("phase journal bound exceeded")
        with (self.output / "phases.jsonl").open("ab") as stream:
            if stream.write(raw) != len(raw):
                raise OSError("incomplete phase journal write")
            stream.flush()
        self.events += 1
        self.journal_bytes += len(raw)

    def start(self, nodeid: str, now: float) -> None:
        """Latch the first start/record failure; later hooks cannot recover."""
        try:
            self._start(nodeid, now)
        except Exception as error:
            self.failure = self.failure or str(error) or type(error).__name__
            raise

    def _start(self, nodeid: str, now: float) -> None:
        """Start only the next node after all preceding phases passed."""
        self.checkpoint(now)
        index = len(self.completed)
        if (
            self.current is not None
            or index >= len(self.nodes)
            or self.nodes[index] != nodeid
        ):
            raise ValueError("duplicate or reordered node start")
        self.record(nodeid, "start", None, now)
        self.current = nodeid
        self.phases = {}
        if index == NODE_COUNT - 1:
            self.target_started = now
            write_once(
                self.output / "target.json",
                {
                    "epoch": self.epoch,
                    "source_sha256": digest(
                        ROOT / "scripts/qt_hang_diagnostic/inputs.json"
                    ),
                    "nodeid": nodeid,
                    "index": NODE_COUNT,
                    "started": now,
                    "prefix": self.completed,
                },
            )

    def report(self, nodeid: str, phase: str, outcome: str, now: float) -> None:
        """Latch all malformed, failed, or incomplete phase observations."""
        try:
            self._report(nodeid, phase, outcome, now)
        except Exception as error:
            self.failure = self.failure or str(error) or type(error).__name__
            raise

    def _report(self, nodeid: str, phase: str, outcome: str, now: float) -> None:
        """Require exactly three passed phases, including teardown."""
        self.checkpoint(now)
        expected = ("setup", "call", "teardown")
        if (
            nodeid != self.current
            or len(self.phases) >= len(expected)
            or phase != expected[len(self.phases)]
        ):
            raise ValueError("duplicate or reordered test phase")
        if outcome not in ("passed", "failed", "skipped"):
            raise ValueError("malformed test outcome")
        self.record(nodeid, phase, outcome, now)
        if outcome != "passed":
            self.failure = "prefix or target did not pass every phase"
            write_once(
                self.output / "phase-failure.json",
                {
                    "epoch": self.epoch,
                    "nodeid": nodeid,
                    "phase": phase,
                    "outcome": outcome,
                    "time": now,
                    "index": len(self.completed) + 1,
                },
            )
            raise ValueError(self.failure)
        self.phases[phase] = outcome
        write_json(
            self.output / "phases.json",
            {
                "epoch": self.epoch,
                "completed": self.completed,
                "current": nodeid,
                "phases": self.phases,
                "time": now,
            },
        )
        if phase == "teardown":
            self.completed.append({"nodeid": nodeid, "phases": self.phases.copy()})
            self.current = None
            if len(self.completed) == NODE_COUNT:
                write_once(
                    self.output / "target-done.json",
                    {
                        "epoch": self.epoch,
                        "nodeid": nodeid,
                        "started": self.target_started,
                        "finished": now,
                        "phases": self.phases,
                    },
                )


def finite_time(value: object) -> float:
    """Reject booleans, nonfinite values and non-numeric clock claims."""
    if type(value) not in (int, float) or not math.isfinite(cast("float", value)):
        raise ValueError("invalid monotonic timestamp")
    return float(cast("float", value))


class TargetWindow:
    """Admit one immutable target timestamp without extending either phase."""

    def __init__(self, launch: float, epoch: str, nodes: list[str]) -> None:
        self.launch = launch
        self.epoch = epoch
        self.nodes = nodes
        self.started: float | None = None
        self.identity: str | None = None
        self.last_time = launch

    def checkpoint(self, now: float) -> None:
        """The same-machine monotonic clock cannot move backward."""
        current = finite_time(now)
        if current < self.last_time:
            raise ValueError("supervisor monotonic clock regressed")
        self.last_time = current

    def admit(self, value: dict[str, Any], identity: str, now: float) -> None:
        """Validate exact prefix completion before adopting the target clock."""
        self.checkpoint(now)
        if self.identity is not None:
            if self.identity != identity:
                raise ValueError("target receipt rewritten")
            if self.started is None or now >= self.started + TOTAL:
                raise ValueError("target receipt cannot extend deadline")
            return
        if (
            set(value)
            != {"epoch", "source_sha256", "nodeid", "index", "started", "prefix"}
            or value["epoch"] != self.epoch
            or value["source_sha256"]
            != digest(ROOT / "scripts/qt_hang_diagnostic/inputs.json")
            or type(value["index"]) is not int
            or value["index"] != NODE_COUNT
            or value["nodeid"] != self.nodes[-1]
        ):
            raise ValueError("target identity mismatch")
        expected = [
            {
                "nodeid": node,
                "phases": {"setup": "passed", "call": "passed", "teardown": "passed"},
            }
            for node in self.nodes[:-1]
        ]
        if value["prefix"] != expected:
            raise ValueError("prefix completion evidence mismatch")
        started = finite_time(value["started"])
        if (
            not self.launch <= started < self.launch + PREPARATION
            or started > now
            or now >= started + TOTAL
        ):
            raise ValueError("target entry outside fixed preparation/target bounds")
        self.started = started
        self.identity = identity

    def completed(self, value: dict[str, Any], now: float) -> None:
        """Require timely target setup/call/teardown before process-exit admission."""
        self.checkpoint(now)
        if (
            self.started is None
            or set(value) != {"epoch", "nodeid", "started", "finished", "phases"}
            or value["epoch"] != self.epoch
            or value["nodeid"] != self.nodes[-1]
            or finite_time(value["started"]) != self.started
        ):
            raise ValueError("target completion identity mismatch")
        finished = finite_time(value["finished"])
        if (
            value["phases"]
            != {"setup": "passed", "call": "passed", "teardown": "passed"}
            or not self.started <= finished <= now < self.started + TOTAL
        ):
            raise ValueError("target completion phase/deadline mismatch")


def target_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    """Parse and hash the same bounded immutable receipt bytes."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("target receipt exceeds bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("target receipt must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def child(source: Path, output: Path, epoch: str) -> int:
    """Run the sole fixed prefix with file-only progress hooks."""
    log = (output / "pytest.log").open("w", encoding="utf-8", buffering=1)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    # Fixed test wrapper only; does not inspect or patch any Qt object/method.
    import faulthandler  # noqa: PLC0415 - child only

    import pytest  # noqa: PLC0415 - child only
    from PySide6 import __version__  # noqa: PLC0415 - child only
    from PySide6.QtCore import qVersion  # noqa: PLC0415 - child only
    from PySide6.QtGui import QGuiApplication  # noqa: PLC0415 - child only

    if (
        sys.version_info[:3] != (3, 13, 17)
        or __version__ != "6.11.3"
        or qVersion() != "6.11.3"
    ):
        raise ValueError("diagnostic runtime mismatch")
    nodes = validate_inputs(source)
    write_json(
        output / "runtime.json",
        {
            "python": list(sys.version_info[:3]),
            "pyside": __version__,
            "qt": qVersion(),
            "qpa_override": os.environ.get("QT_QPA_PLATFORM"),
        },
    )
    faulthandler.enable(file=log)
    write_json(
        output / "ready.json",
        {
            "epoch": epoch,
            "pid": os.getpid(),
            "executable": getattr(sys, "_base_executable", sys.executable),
        },
    )

    ledger = TargetLedger(nodes, epoch, output)

    class Progress:
        def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
            actual = [item.nodeid for item in items]
            write_json(output / "collected.json", actual)
            if actual != nodes:
                raise ValueError("collected order differs from fixed prefix")

        def pytest_runtest_call(self, item: pytest.Item) -> None:
            del item
            if (
                QGuiApplication.instance() is not None
                and not (output / "qpa.json").exists()
            ):
                write_json(
                    output / "qpa.json", {"platform": QGuiApplication.platformName()}
                )

        def pytest_runtest_logstart(self, nodeid: str, location: object) -> None:
            del location
            ledger.start(nodeid, time.monotonic())
            write_json(
                output / "progress.json",
                {"nodeid": nodeid, "monotonic": time.monotonic()},
            )

        def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
            ledger.report(
                report.nodeid, str(report.when), report.outcome, time.monotonic()
            )

    os.chdir(source)
    exit_code = pytest.main(
        [
            "--cov",
            "--cov-report=xml:" + str(output / "coverage.xml"),
            "--cov-report=term",
            "-o",
            "faulthandler_timeout=5",
            "-m",
            "not native_desktop",
            "--junitxml=" + str(output / "junit.xml"),
            "--ux-snapshots=" + str(output / "snapshots"),
            "--randomly-seed=" + str(SEED),
            "--randomly-dont-reorganize",
            *nodes,
        ],
        plugins=[Progress()],
    )

    sys.stdout.flush()
    sys.stderr.flush()
    log.flush()
    with (output / "pytest.log").open("rb") as stream:
        raw_log = stream.read(MAX_LOG + 1).decode("utf-8", errors="replace")
    coverage = re.search(r"FAIL Required test coverage[^\r\n]{0,300}", raw_log)
    write_once(
        output / "pytest-exit.json",
        {
            "epoch": epoch,
            "exit_code": exit_code,
            "target_phases_passed": len(ledger.completed) == NODE_COUNT,
            "coverage_failure": coverage.group(0) if coverage else None,
        },
    )
    return exit_code


def retire(owners: list[tuple[str, OwnedJob]], deadline: float) -> dict[str, Any]:  # noqa: C901, PLR0912 - every owner is retired despite individual failures
    """Terminate all owned trees, then observe under one shared cleanup ceiling."""
    results: dict[str, Any] = {
        name: {"confirmed": False, "errors": []} for name, _ in owners
    }
    for name, owner in owners:
        try:
            if not owner.api.TerminateJobObject(owner.handle, 1):
                results[name]["errors"].append("TerminateJobObject failed")
        except Exception as error:  # noqa: BLE001 - retire every owned tree
            results[name]["errors"].append(repr(error))
    try:
        while time.monotonic() < deadline:
            for name, owner in owners:
                try:
                    results[name]["remaining_pids"] = owner.pids()
                    results[name]["direct_exits"] = [
                        owner.wait(handle, 0) for handle in owner.processes
                    ]
                except Exception as error:  # noqa: BLE001 - preserve all cleanup evidence
                    results[name]["errors"].append(repr(error))
            if all(
                item.get("remaining_pids") == []
                and all(code is not None for code in item.get("direct_exits", [None]))
                for item in results.values()
            ):
                break
            time.sleep(0.01)
    finally:
        for name, owner in owners:
            for handle in [*owner.processes, owner.handle]:
                try:
                    if not owner.api.CloseHandle(handle):
                        results[name]["errors"].append("CloseHandle failed")
                except Exception as error:  # noqa: BLE001 - close every retained handle
                    results[name]["errors"].append(repr(error))
        expired = time.monotonic() >= deadline
        for item in results.values():
            item["deadline_exceeded"] = expired
            item["confirmed"] = (
                not expired
                and not item["errors"]
                and item.get("remaining_pids") == []
                and all(code is not None for code in item.get("direct_exits", [None]))
            )
    return results


def supervise(source: Path, output: Path) -> int:  # noqa: C901, PLR0912, PLR0915 - single fixed lifetime
    """Observe only the owned test process, with no debugger initialization."""
    output.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "base": BASE,
        "seed": SEED,
        "collection_scope": "explicit-prefix-not-full-suite",
        "collection_limitation": (
            "157 explicitly collected nodes omit possible imports/hooks from the "
            "original 3691-test full collection; non-reproduction is not resolution"
        ),
        "native_stack_available": False,
        "debugger_intervention": False,
        "python_faulthandler_seconds": 5,
        "runner_image": {k: os.environ.get(k) for k in ("ImageOS", "ImageVersion")},
        "status": "failed",
        "cleanup": {},
        "failure": None,
        "target_reached": False,
        "target_outcome": "unknown",
    }
    job = observer = None
    outer_deadline: float | None = None
    result_code = 1
    try:
        validate_inputs(ROOT)
        nodes = validate_inputs(source)
        epoch = uuid.uuid4().hex
        receipt["epoch"] = epoch
        job = new_job()
        started = time.monotonic()
        window = TargetWindow(started, epoch, nodes)
        deadline = started + PREPARATION
        outer_deadline = started + OUTER
        receipt.update(
            preparation_seconds=PREPARATION, target_seconds=TOTAL, outer_seconds=OUTER
        )
        _, process = job.start(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                "--source",
                str(source),
                "--output",
                str(output),
                "--epoch",
                epoch,
            ],
            source,
        )
        receipt["launch_seconds"] = time.monotonic() - started
        while True:
            window.checkpoint(time.monotonic())
            target_file = output / "target.json"
            if target_file.exists():
                value, identity = target_snapshot(target_file)
                window.admit(value, identity, time.monotonic())
                if window.started is None:
                    raise ValueError("target timestamp missing")  # noqa: TRY301
                deadline = window.started + TOTAL
                receipt.update(
                    target_reached=True,
                    target_started=window.started,
                    target_receipt_sha256=window.identity,
                )
            if time.monotonic() >= deadline:
                raise TimeoutError("fixed preparation/target deadline")  # noqa: TRY301
            if (output / "phase-failure.json").exists():
                receipt["phase_failure"] = read_json(output / "phase-failure.json")
                raise RuntimeError("prefix or target phase failed")  # noqa: TRY301
            done = output / "target-done.json"
            if done.exists():
                window.completed(read_json(done), time.monotonic())
                receipt["target_outcome"] = "passed"
            if (output / "pytest.log").exists() and (
                output / "pytest.log"
            ).stat().st_size > MAX_LOG:
                raise ValueError("diagnostic log bound exceeded")  # noqa: TRY301
            if observer is None and (output / "ready.json").exists():
                pid = checked_ready(read_json(output / "ready.json", 4096), epoch)
                observer = new_observer(job)
                observer.open(pid)
                witness = observer.snapshot(pid)
                receipt["actual_test_process"] = witness
                if witness.get("image_status") != "available" or os.path.normcase(
                    str(witness["image"])
                ) != os.path.normcase(getattr(sys, "_base_executable", sys.executable)):
                    raise ValueError("test process kernel identity mismatch")  # noqa: TRY301
            code = job.wait(process, 0)
            if code is not None and not job.pids():
                if observer is None or not done.exists():
                    raise RuntimeError(  # noqa: TRY301
                        "process exited without verified target completion"
                    )
                window.completed(read_json(done), time.monotonic())
                result = read_json(output / "pytest-exit.json")
                if (
                    result.get("epoch") != epoch
                    or type(result.get("exit_code")) is not int
                    or result["exit_code"] != code
                    or result.get("target_phases_passed") is not True
                ):
                    raise ValueError("pytest exit receipt mismatch")  # noqa: TRY301
                if time.monotonic() >= deadline:
                    raise TimeoutError("deadline after final process evidence")  # noqa: TRY301
                receipt.update(
                    status="non-reproduction",
                    exit_code=code,
                    elapsed_seconds=time.monotonic() - started,
                    pytest_exit=result,
                )
                result_code = 0 if code == 0 else 1
                break
            time.sleep(0.01)
    except Exception as error:  # noqa: BLE001 - preserve first failure before owned cleanup
        receipt["failure"] = repr(error)
        receipt["status"] = "failed"
        result_code = 1
    finally:
        cleanup_deadline = (
            min(time.monotonic() + 3.0, outer_deadline)
            if outer_deadline is not None
            else time.monotonic() + 3.0
        )
        receipt["cleanup"] = retire(
            [("pytest", job)] if job is not None else [], cleanup_deadline
        )
        if observer is not None:
            try:
                receipt["inspection_close_errors"] = observer.close()
            except Exception as error:  # noqa: BLE001 - preserve cleanup failures
                receipt["inspection_close_errors"] = [repr(error)]
        receipt["cleanup_confirmed"] = (
            time.monotonic() < cleanup_deadline
            and all(
                item.get("confirmed") is True for item in receipt["cleanup"].values()
            )
            and not receipt.get("inspection_close_errors")
        )
        if not receipt["cleanup_confirmed"]:
            receipt["status"] = "failed"
            receipt["failure"] = receipt["failure"] or "owned cleanup unconfirmed"
        write_json(output / "receipt.json", receipt)
    return result_code if receipt["cleanup_confirmed"] else 1


def main() -> int:
    """Run the fixed Python-only supervisor or its sole pytest child."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--epoch")
    args = parser.parse_args()
    if args.child:
        return child(args.source.resolve(), args.output.resolve(), args.epoch)
    return supervise(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
