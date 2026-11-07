"""Native Windows ownership proof, intentionally separate from GUI/product wiring.

Run: uv run python scripts/owned_helper_smoke.py --output <artifact-directory>
The child/owner modes are disposable fixtures. Test-only file receipts live in a
fresh private directory; they are not the proposed product transport.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from verdiclip.platform import owned_helper
from verdiclip.platform.owned_helper import HelperCommand, HelperStatus, OwnedHelper

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEADLINE_SECONDS = 5.0
_INHERIT_HANDLES = False
_TREE_SIZE = 2
_EARLY_EXIT = 23
_BOOTSTRAP = (
    "import runpy,sys;sys.path.insert(0,sys.argv[1]);"
    "sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name='__main__')"
)


def require(*, condition: bool, message: str) -> None:
    """Fail the native gate instead of silently converting missing proof to a skip."""
    if not condition:
        raise RuntimeError(message)


def command(arguments: Sequence[str], directory: Path) -> HelperCommand:
    """Launch the actual base producer with only an explicit source-root addition."""
    script = Path(__file__).resolve()
    source = script.parents[1] / "src"
    return HelperCommand.base_python(
        ("-c", _BOOTSTRAP, str(source), str(script), *arguments), directory
    )


def write_receipt(path: Path, value: object) -> None:
    """Atomically publish a fixture receipt without product protocol assumptions."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_receipt(path: Path) -> dict[str, object]:
    """Bound a disposable fixture's readiness wait."""
    deadline = time.monotonic() + _DEADLINE_SECONDS
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Malformed native fixture receipt")
    return {str(key): item for key, item in value.items()}


def receipt_pid(receipt: dict[str, object]) -> int:
    """Accept only an actual process identifier from a private fixture receipt."""
    pid = receipt.get("pid")
    if type(pid) is not int or pid <= 0:
        raise RuntimeError("Invalid native fixture PID")
    return pid


def worker(directory: Path, *, delay_ready: bool, exit_early: bool) -> None:
    """Stay alive with a real descendant, unless testing pre-ready failure."""
    if exit_early:
        sys.exit(_EARLY_EXIT)
    if delay_ready:
        time.sleep(60)
    descendant = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(60)"],
        creationflags=0x08000000,
        close_fds=True,
    )
    write_receipt(
        directory / "worker.json",
        {
            "pid": os.getpid(),
            "executable": sys.executable,
            "descendant_pid": descendant.pid,
        },
    )
    while True:
        time.sleep(0.1)


def await_closed(helper: OwnedHelper) -> HelperStatus:
    """Polling waits are permitted only in this out-of-process native harness."""
    deadline = time.monotonic() + _DEADLINE_SECONDS
    status = helper.poll()
    while not status.closed and time.monotonic() < deadline:
        time.sleep(0.01)
        status = helper.poll()
    require(condition=status.closed, message="Owned helper did not close by deadline")
    return status


def ready(helper: OwnedHelper, directory: Path) -> dict[str, object]:
    """Cross-check the direct producer and kernel image before considering it ready."""
    receipt = read_receipt(directory / "worker.json")
    executable = receipt.get("executable")
    if not isinstance(executable, str):
        raise TypeError("Missing fixture executable")
    status = helper.verify_ready(receipt_pid(receipt), executable)
    require(
        condition=status.producer_verified, message=f"Unverified producer: {status}"
    )
    return receipt


def owner(directory: Path, *, before_ready: bool) -> None:
    """Own a helper job and deliberately let the harness kill this parent process."""
    helper = OwnedHelper()
    arguments = ["--worker", str(directory)]
    if before_ready:
        arguments.append("--delay-ready")
    status = helper.start(command(arguments, directory))
    require(condition=status.pid is not None, message=f"Owner launch failed: {status}")
    receipt = {"pid": status.pid} if before_ready else ready(helper, directory)
    write_receipt(directory / "owner.json", receipt)
    while True:
        time.sleep(0.1)


class ProcessWitness:
    """Retain handles before killing the owner, avoiding PID-reuse observations."""

    def __init__(self, pids: Sequence[int]) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateProcess.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handles: list[int] = []
        try:
            for pid in pids:
                handle = self.kernel.OpenProcess(0x00100001, _INHERIT_HANDLES, pid)
                require(
                    condition=bool(handle),
                    message="Could not retain fixture process witness",
                )
                self.handles.append(int(handle))
        except BaseException:
            self.close()
            raise

    def all_exited(self) -> bool:
        """Read the witnessed original processes, not fresh potentially reused PIDs."""
        return all(
            self.kernel.WaitForSingleObject(handle, 0) == 0 for handle in self.handles
        )

    def close(self) -> None:
        """Clean up failed fixtures without treating fallback kills as proof."""
        for handle in self.handles:
            if self.kernel.WaitForSingleObject(handle, 0) != 0:
                self.kernel.TerminateProcess(handle, 1)
            self.kernel.CloseHandle(handle)
        self.handles.clear()


def prove_parent_death(directory: Path, *, before_ready: bool) -> dict[str, object]:
    """Kill only the owner and independently witness its entire helper tree die."""
    arguments = ["--owner", str(directory)]
    if before_ready:
        arguments.append("--before-ready")
    launch = command(arguments, directory)
    parent = subprocess.Popen(  # noqa: S603 - direct authored disposable owner
        [launch.executable, *launch.arguments],
        cwd=launch.directory,
        creationflags=0x08000000,
        close_fds=True,
    )
    witnesses: list[ProcessWitness] = []
    try:
        receipt = read_receipt(directory / "owner.json")
        pids = [receipt_pid(receipt)]
        child = receipt.get("descendant_pid")
        if type(child) is int:
            pids.append(child)
        require(
            condition=before_ready or len(pids) == _TREE_SIZE,
            message="Descendant fixture missing",
        )
        witness = ProcessWitness(pids)
        witnesses.append(witness)
        parent.kill()
        parent.wait(timeout=_DEADLINE_SECONDS)
        deadline = time.monotonic() + _DEADLINE_SECONDS
        while not witness.all_exited() and time.monotonic() < deadline:
            time.sleep(0.01)
        require(
            condition=witness.all_exited(),
            message="A helper survived abrupt owner death",
        )
        return {
            "passed": True,
            "owner_pid": parent.pid,
            "witnessed_pids": pids,
            "before_ready": before_ready,
        }
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=_DEADLINE_SECONDS)
        for witness in witnesses:
            witness.close()


def prove_explicit_cleanup(directory: Path) -> dict[str, object]:
    """Verify direct producer identity and job-wide asynchronous cleanup."""
    helper = OwnedHelper()
    try:
        helper.start(command(("--worker", str(directory)), directory))
        receipt = ready(helper, directory)
        membership = helper.poll().remaining_pids
        require(
            condition=membership is not None
            and receipt.get("descendant_pid") in membership,
            message="Descendant not contained in the exact job",
        )
        helper.close()
        status = await_closed(helper)
        require(
            condition=status.cleanup_confirmed, message=f"Unconfirmed cleanup: {status}"
        )
        return {"passed": True, "ready": receipt, "cleanup": asdict(status)}
    finally:
        helper.close()


def prove_startup_failures(directory: Path) -> dict[str, object]:
    """Cover both CreateProcess failure and exit before producing ready evidence."""
    absent = HelperCommand(str(directory / "absent-helper.exe"), (), str(directory))
    missing = OwnedHelper()
    missing_status = missing.start(absent)
    require(
        condition=missing_status.cleanup_confirmed
        and missing_status.failure is not None,
        message="CreateProcess failure leaked resources",
    )
    early = OwnedHelper()
    try:
        early.start(command(("--worker", str(directory), "--exit-early"), directory))
        status = await_closed(early)
        require(
            condition=status.cleanup_confirmed
            and status.exit_code == _EARLY_EXIT
            and not status.producer_verified,
            message=f"Unconfirmed pre-ready exit: {status}",
        )
        return {
            "passed": True,
            "missing": asdict(missing_status),
            "early_exit": asdict(status),
        }
    finally:
        early.close()


def main() -> int:
    """Write a native evidence receipt and return nonzero on any missing proof."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--owner", type=Path)
    parser.add_argument("--delay-ready", action="store_true")
    parser.add_argument("--exit-early", action="store_true")
    parser.add_argument("--before-ready", action="store_true")
    args = parser.parse_args()
    require(
        condition=sys.platform == "win32", message="This proof requires native Windows"
    )
    if args.worker is not None:
        worker(args.worker, delay_ready=args.delay_ready, exit_early=args.exit_early)
    if args.owner is not None:
        owner(args.owner, before_ready=args.before_ready)
    output = args.output
    if not isinstance(output, Path):
        raise TypeError("--output is required for the native proof")
    output.mkdir(parents=True, exist_ok=True)
    source = Path(owned_helper.__file__)
    report: dict[str, object] = {
        "python": sys.version,
        "executable": sys.executable,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "passed": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="owned-helper-") as temporary:
            root = Path(temporary)
            for name in ("cleanup", "startup", "parent-ready", "parent-starting"):
                (root / name).mkdir()
            report["explicit_cleanup"] = prove_explicit_cleanup(root / "cleanup")
            report["startup_failures"] = prove_startup_failures(root / "startup")
            report["parent_death"] = prove_parent_death(
                root / "parent-ready", before_ready=False
            )
            report["pre_ready_parent_death"] = prove_parent_death(
                root / "parent-starting", before_ready=True
            )
        report["passed"] = True
    except (OSError, RuntimeError, TypeError, subprocess.SubprocessError) as error:
        report["failure"] = str(error)
    write_receipt(output / "owned-helper.json", report)
    return 0 if report["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
