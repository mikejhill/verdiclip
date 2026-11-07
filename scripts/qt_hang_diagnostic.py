"""One fixed failed-prefix experiment; no product or debugger fallback paths.

The external owner never waits inside Qt. Debugger attachment is an intervention,
not an unmodified reproduction. All elapsed budgets begin before process launch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
TOTAL = 15.0
ATTACH_AT = 8.0
DEBUGGER_SECONDS = 3.0
NODE_COUNT = 157
MAX_PID = 0xFFFFFFFF
MAX_LOG = 2 * 1024 * 1024
SDK_PATHS = (
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\8.1\Debuggers\x64\cdb.exe",
)


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


def debugger_command(path: str, pid: int, output: Path) -> list[str]:
    """Construct only the reviewed noninvasive stack command."""
    if path not in SDK_PATHS or type(pid) is not int or not 0 < pid <= MAX_PID:
        raise ValueError("invalid fixed debugger or process")
    return [
        path,
        "-pv",
        "-p",
        str(pid),
        "-sins",
        "-netsyms:no",
        "-noshell",
        "-y",
        str(output / "empty-symbols"),
        "-logo",
        str(output / "native-stacks.log"),
        "-c",
        "~* kn 40; qd",
    ]


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
            write_json(
                output / "progress.json",
                {"nodeid": nodeid, "monotonic": time.monotonic()},
            )

    os.chdir(source)
    return pytest.main(
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


def retire(owners: list[tuple[str, OwnedJob]], deadline: float) -> dict[str, Any]:  # noqa: C901, PLR0912 - every owner is retired despite individual failures
    """Terminate both trees first, then observe under one shared cleanup ceiling."""
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


def supervise(source: Path, output: Path, preflight: Path) -> int:  # noqa: C901, PLR0912, PLR0915 - explicit single experiment lifecycle
    """Own both process trees without entering the target Qt event loop."""
    output.mkdir(parents=True, exist_ok=True)
    metadata = read_json(preflight)
    receipt: dict[str, Any] = {
        "base": BASE,
        "seed": SEED,
        "debugger": metadata,
        "intervention": False,
        "runner_image": {k: os.environ.get(k) for k in ("ImageOS", "ImageVersion")},
        "status": "failed",
        "cleanup": {},
        "failure": None,
    }
    job = debugger = observer = None
    outer_deadline: float | None = None
    try:
        if metadata.get("status") == "skipped":
            receipt["status"] = "skipped"
            return 0
        if (
            metadata.get("status") != "available"
            or metadata.get("path") not in SDK_PATHS
            or metadata.get("signature_status") != "Valid"
            or metadata.get("machine") != "AMD64"
        ):
            raise ValueError("invalid debugger preflight")  # noqa: TRY301
        if digest(Path(metadata["path"])) != metadata["sha256"]:
            raise ValueError("debugger changed after signature verification")  # noqa: TRY301
        validate_inputs(ROOT)
        validate_inputs(source)
        (output / "empty-symbols").mkdir(exist_ok=True)
        epoch = uuid.uuid4().hex
        job = new_job()
        debugger = new_job()
        started = time.monotonic()
        deadline = started + TOTAL
        outer_deadline = deadline + 3.0
        receipt.update(
            workload_deadline_seconds=TOTAL, outer_deadline_seconds=TOTAL + 3.0
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
        attached = False
        debug_process = None
        debug_deadline = None
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("original 15s experiment deadline")  # noqa: TRY301
            if any(
                p.exists() and p.stat().st_size > MAX_LOG
                for p in (output / "pytest.log", output / "native-stacks.log")
            ):
                raise ValueError("diagnostic log bound exceeded")  # noqa: TRY301
            code = job.wait(process, 0)
            if code is not None and not job.pids() and debug_process is None:
                receipt.update(
                    status="completed",
                    exit_code=code,
                    elapsed_seconds=time.monotonic() - started,
                )
                break
            if not attached and now - started >= ATTACH_AT:
                attached = True
                pid = checked_ready(read_json(output / "ready.json", 4096), epoch)
                observer = new_observer(job)
                observer.open(pid)
                identity = observer.snapshot(pid)
                receipt["target"] = identity
                if identity["exited"]:
                    raise RuntimeError("target exited before attach; no retry")  # noqa: TRY301
                if identity.get("image_status") != "available" or os.path.normcase(
                    str(identity["image"])
                ) != os.path.normcase(getattr(sys, "_base_executable", sys.executable)):
                    raise ValueError("target kernel image mismatch")  # noqa: TRY301
                if (output / "progress.json").exists():
                    receipt["last_progress"] = read_json(output / "progress.json", 8192)
                if time.monotonic() >= deadline:
                    raise TimeoutError("deadline before debugger launch")  # noqa: TRY301
                if digest(Path(metadata["path"])) != metadata["sha256"]:
                    raise ValueError("debugger changed before attach")  # noqa: TRY301
                command = debugger_command(metadata["path"], pid, output)
                # These changes affect only the supervisor and its later debugger child.
                os.environ.pop("_NT_SYMBOL_PATH", None)
                os.environ.pop("_NT_ALT_SYMBOL_PATH", None)
                debug_deadline = min(deadline, time.monotonic() + DEBUGGER_SECONDS)
                receipt.update(intervention=True, debugger_command=command)
                write_json(output / "receipt.json", receipt)
                if time.monotonic() >= debug_deadline:
                    raise TimeoutError("deadline immediately before debugger launch")  # noqa: TRY301
                _, debug_process = debugger.start(command, output)
            if debug_process is not None:
                if debug_deadline is None:
                    raise RuntimeError("missing debugger deadline")  # noqa: TRY301
                if time.monotonic() >= debug_deadline:
                    raise TimeoutError("separate debugger deadline")  # noqa: TRY301
                result = debugger.wait(debug_process, 0)
                if result is not None:
                    receipt["debugger_exit"] = result
                    if result != 0:
                        raise RuntimeError("debugger failed; no fallback")  # noqa: TRY301
                    if debugger.pids():
                        raise RuntimeError("debugger descendants survive exit")  # noqa: TRY301
                    if time.monotonic() >= debug_deadline:
                        raise TimeoutError(  # noqa: TRY301
                            "deadline after debugger completion inspection"
                        )
                    debug_process = None
            time.sleep(0.01)
        if time.monotonic() >= deadline:
            raise TimeoutError("deadline at final admission")  # noqa: TRY301
        result_code = 0 if receipt["exit_code"] == 0 else 1
    except Exception as error:  # noqa: BLE001 - preserve diagnostic failure before cleanup
        receipt["failure"] = repr(error)
        receipt["status"] = "failed"
        result_code = 1
    finally:
        # One cleanup-only bound; never extends workload/result admission.
        cleanup_deadline = (
            min(time.monotonic() + 3.0, outer_deadline)
            if outer_deadline is not None
            else time.monotonic() + 3.0
        )
        owners = [
            (name, owner)
            for name, owner in (("debugger", debugger), ("pytest", job))
            if owner is not None
        ]
        receipt["cleanup"] = retire(owners, cleanup_deadline)
        if observer is not None:
            try:
                receipt["inspection_close_errors"] = observer.close()
            except Exception as error:  # noqa: BLE001 - receipt survives inspection close failure
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
    """Dispatch only supervisor or its fixed test child."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--epoch")
    args = parser.parse_args()
    if args.child:
        return child(args.source.resolve(), args.output.resolve(), args.epoch)
    result = supervise(args.source.resolve(), args.output.resolve(), args.preflight)
    receipt = read_json(args.output / "receipt.json")
    return result if receipt["cleanup_confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
