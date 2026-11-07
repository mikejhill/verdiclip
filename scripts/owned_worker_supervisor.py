"""Bounded external owner of the inert GUI proof, not product orchestration.

Only this owned job is retired. A timeout, nonzero exit or descendant surviving
the original deadline
always fails qualification, even if forced cleanup subsequently succeeds.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes
from pathlib import Path

from scripts.owned_worker_smoke import CASES
from scripts.panel_owned_job import OwnedJob

TIMEOUT_SECONDS = 45.0
MAX_RECEIPT_BYTES = 131_072


MAX_RESIDUALS = 256
IMAGE_CHARS = 4096
DRAIN_POLL_SECONDS = 0.01
WAIT_TIMEOUT = 258


class ResidualInspectionError(OSError):
    """Retain immediate Win32 failure code and every already observed field."""

    def __init__(self, operation: str, witness: dict[str, object]) -> None:
        code = ctypes.get_last_error()
        self.witness = {**witness, "error": {"operation": operation, "winerror": code}}
        super().__init__(f"{operation} failed with Win32 error {code}")


class ResidualObserver:
    """Read-only kernel witnesses pinned to verified members of one owned job."""

    def __init__(self, job: OwnedJob) -> None:
        self.job = job
        self.api = job.api
        self.handles: dict[int, int] = {}
        for name, arguments, result in (
            (
                "OpenProcess",
                [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
                wintypes.HANDLE,
            ),
            (
                "IsProcessInJob",
                [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)],
                wintypes.BOOL,
            ),
            (
                "QueryFullProcessImageNameW",
                [
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.LPWSTR,
                    ctypes.POINTER(wintypes.DWORD),
                ],
                wintypes.BOOL,
            ),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result

    def open(self, pid: int) -> None:
        """Pin identity, then reject PID reuse before reading image or exit state."""
        if pid in self.handles:
            return
        if len(self.handles) >= MAX_RESIDUALS:
            raise RuntimeError("Residual witness bound exceeded")
        handle = self.api.OpenProcess(0x00101000, 0, pid)  # QUERY_LIMITED + SYNCHRONIZE
        if not handle:
            raise ResidualInspectionError("OpenProcess", {"pid": pid})
        self.handles[pid] = handle
        member = wintypes.BOOL()
        if not self.api.IsProcessInJob(handle, self.job.handle, ctypes.byref(member)):
            raise ResidualInspectionError("IsProcessInJob", {"pid": pid})
        if not member.value:
            raise RuntimeError("Residual PID no longer belongs to owned job")

    def snapshot(self, pid: int) -> dict[str, object]:
        """Read exit evidence first; never lose it when a later image query fails."""
        handle = self.handles[pid]
        witness: dict[str, object] = {"pid": pid, "image": None}
        wait = self.api.WaitForSingleObject(handle, 0)
        if wait not in (0, WAIT_TIMEOUT):
            raise ResidualInspectionError(
                "WaitForSingleObject", {**witness, "wait_result": wait}
            )
        witness.update(wait_result=wait, exited=wait == 0)
        code = wintypes.DWORD()
        if not self.api.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ResidualInspectionError("GetExitCodeProcess", witness)
        witness.update(
            raw_exit_code=code.value, exit_code=code.value if wait == 0 else None
        )
        size = wintypes.DWORD(IMAGE_CHARS)
        image = ctypes.create_unicode_buffer(IMAGE_CHARS)
        if not self.api.QueryFullProcessImageNameW(
            handle, 0, image, ctypes.byref(size)
        ):
            raise ResidualInspectionError("QueryFullProcessImageNameW", witness)
        witness["image"] = image.value
        return witness

    def close(self) -> list[str]:
        """Close every inspection handle, including failed membership checks."""
        errors = []
        for handle in self.handles.values():
            try:
                if not self.api.CloseHandle(handle):
                    errors.append(str(ResidualInspectionError("CloseHandle", {})))
            except Exception as error:  # noqa: BLE001 - attempt every retained handle
                errors.append(repr(error))
        self.handles.clear()
        return errors


def drain_naturally(
    job: OwnedJob, initial: list[int], deadline: float
) -> dict[str, object]:
    """Observe natural retirement within the original deadline, never terminate here."""
    result: dict[str, object] = {
        "initial_pids": initial,
        "final_pids": initial,
        "initial_witnesses": [],
        "final_witnesses": [],
        "natural_empty": False,
        "elapsed_seconds": 0.0,
        "errors": [],
    }
    if not initial:
        result["natural_empty"] = True
        return result
    started = time.monotonic()
    observer: ResidualObserver | None = None
    errors: list[str] = []
    first: list[dict[str, object]] = []
    final: list[dict[str, object]] = []
    active = first
    try:
        observer = ResidualObserver(job)
        pids = initial
        while pids and time.monotonic() < deadline:
            for pid in pids:
                if pid not in observer.handles:
                    observer.open(pid)
                    first.append(observer.snapshot(pid))
            if time.monotonic() >= deadline:
                break
            time.sleep(min(DRAIN_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
            pids = job.pids()
            result["final_pids"] = pids
        active = final
        final.extend(observer.snapshot(pid) for pid in observer.handles)
        result["natural_empty"] = (
            not pids
            and all(item["exited"] is True for item in final)
            and time.monotonic() < deadline
        )
    except Exception as error:  # noqa: BLE001 - retain partial observations and uncertainty
        if isinstance(error, ResidualInspectionError):
            active.append(error.witness)
        errors.append(repr(error))
    finally:
        result["initial_witnesses"] = first
        result["final_witnesses"] = final
        if observer is not None:
            errors.extend(observer.close())
        result["elapsed_seconds"] = time.monotonic() - started
        result["errors"] = errors
        if errors or time.monotonic() >= deadline:
            result["natural_empty"] = False
    return result


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
            drain = drain_naturally(job, pids, deadline)
            result["natural_drain"] = drain
            if not drain["natural_empty"]:
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
