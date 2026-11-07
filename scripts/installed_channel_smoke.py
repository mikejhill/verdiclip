"""Fresh-wheel inert channel proof; copy beside installed_runtime_smoke.py.

Run outside the checkout with the fresh environment's Python. No launch builder
is patched. The external owner, not the GUI event loop, enforces retirement.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
import sys
import time
import zipfile
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

import mss
import shiboken6
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from verdiclip.capture import (
    frame_credit,
    frame_envelope,
    observe_native,
    observe_protocol,
    observe_provider,
    retained_frame,
    scroll_stitch,
)
from verdiclip.platform import (
    helper_bootstrap,
    helper_channel,
    helper_peer,
    helper_protocol,
)
from verdiclip.platform.helper_runtime import inspect_runtime
from verdiclip.platform.owned_helper import HelperCommand, NativeProcess, Win32HelperApi

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent, QPaintEvent

CASES = (
    "ping-finish",
    "cancel-immediate",
    "cancel-ready",
    "delete-startup",
    "parent-death",
)
MAX_ERRORS = 8
WAIT_TIMEOUT = 258
DEATH_SECONDS = 5.0
DEATH_CODE = 17
CASE_SECONDS = 15.0
MAX_RECEIPT_BYTES = 131_072


def write_receipt(path: Path, value: object) -> None:
    """Atomically retain bounded evidence without appending ambiguous receipts."""
    data = json.dumps(value, allow_nan=False, sort_keys=True).encode()
    if len(data) > MAX_RECEIPT_BYTES:
        raise ValueError("Receipt exceeds bound")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def read_receipt(path: Path) -> dict[str, object]:
    """Reject missing, oversized or non-object evidence."""
    if path.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError("Receipt exceeds bound")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise TypeError("Receipt must be an object")
    return cast("dict[str, object]", value)


def verify_wheel(wheel: Path) -> dict[str, object]:
    """Reuse the copied independent verifier, then check actual channel origins."""
    sibling = Path(__file__).with_name("installed_runtime_smoke.py")
    spec = importlib.util.spec_from_file_location("installed_channel_verifier", sibling)
    if spec is None or spec.loader is None:
        raise RuntimeError("Copied installed runtime verifier is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    runtime = module.inspect_runtime(wheel)
    root = Path(runtime.origins[0][1]).parent.parent
    loaded_modules = (
        helper_channel,
        helper_peer,
        helper_protocol,
        helper_bootstrap,
        scroll_stitch,
        frame_envelope,
        observe_protocol,
        observe_native,
        observe_provider,
        retained_frame,
        frame_credit,
    )
    with zipfile.ZipFile(wheel) as archive:
        for loaded in loaded_modules:
            relative = loaded.__name__.replace(".", "/") + ".py"
            expected = (root / relative).resolve(strict=True)
            if Path(loaded.__file__).resolve(strict=True) != expected:
                raise RuntimeError(
                    "Channel module came from outside the verified wheel"
                )
            if archive.read(relative) != expected.read_bytes():
                raise ValueError("Wheel lacks the exact loaded channel primitives")
    return cast("dict[str, object]", asdict(runtime))


def admit_terminal(case: str, receipt: dict[str, object], replies: int) -> bool:
    """Cancellation is expected failure, never evidence of successful cleanup."""
    helper = receipt.get("helper")
    cleaned = bool(
        isinstance(helper, dict)
        and helper.get("cleanup_confirmed") is True
        and helper.get("closed") is True
        and not helper.get("cleanup_errors")
        and helper.get("failure") is None
    )
    if receipt.get("cleanup_failure") is not None:
        return False
    if case == "ping-finish":
        return bool(
            cleaned
            and isinstance(helper, dict)
            and helper.get("producer_verified") is True
            and helper.get("exit_code") == 0
            and receipt.get("failure") is None
            and receipt.get("passed") is True
            and replies == 1
        )
    unstarted = (
        case in ("cancel-immediate", "delete-startup")
        and receipt.get("not_launched") is True
        and helper is None
    )
    if case == "cancel-ready" and (
        not isinstance(helper, dict) or helper.get("producer_verified") is not True
    ):
        return False
    return (
        case in CASES[1:4]
        and receipt.get("passed") is False
        and receipt.get("failure") == "cancelled"
        and (cleaned or unstarted)
        and replies == 0
    )


def admit_child(case: str, value: dict[str, object]) -> bool:
    """Recheck structured case evidence instead of trusting a summary flag."""
    receipt, replies = value.get("terminal"), value.get("replies")
    if not isinstance(receipt, dict) or type(replies) is not int:
        return False
    witnesses, identity = value.get("witnesses"), value.get("identity")
    if not isinstance(witnesses, dict):
        return False
    if case in ("ping-finish", "cancel-ready") and (
        witnesses.get("ready_observed") is not True
        or not isinstance(identity, dict)
        or type(identity.get("pid")) is not int
    ):
        return False
    if case in ("ping-finish", "cancel-ready"):
        helper = receipt.get("helper")
        if (
            not isinstance(helper, dict)
            or not isinstance(identity, dict)
            or type(identity.get("pid")) is not int
            or cast("int", identity["pid"]) <= 0
            or identity["pid"] != helper.get("pid")
        ):
            return False
    if case == "delete-startup" and (
        witnesses.get("facade_deleted") is not True
        or witnesses.get("lifetime_retained") is not True
    ):
        return False
    return (
        value.get("case") == case
        and value.get("passed") is True
        and value.get("generation") == 1
        and value.get("errors") == []
        and type(value.get("heartbeat_count")) is int
        and cast("int", value["heartbeat_count"]) >= 0
        and type(value.get("paint_count")) is int
        and cast("int", value["paint_count"]) >= 0
        and admit_terminal(case, receipt, replies)
    )


class WitnessWindow(QWidget):
    """Exercise real GUI callbacks without permitting premature event-loop exit."""

    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.active_paints = 0
        self.active_beats = 0
        self.paints = 0
        self.beats = 0
        self.maximum_gap = 0.0
        self.previous: float | None = None

    def heartbeat(self) -> None:
        """Retain constant-space responsiveness observations."""
        now = time.monotonic()
        if self.active:
            self.active_beats += 1
        if self.active and self.previous is not None:
            self.maximum_gap = max(self.maximum_gap, now - self.previous)
        self.previous = now if self.active else None
        self.beats += 1
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Count actual paint delivery rather than update requests."""
        self.paints += 1
        if self.active:
            self.active_paints += 1
        super().paintEvent(event)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Only terminal retirement may end this fixture event loop."""
        event.ignore()


def child(case: str, output: Path, wheel: Path) -> int:  # noqa: C901, PLR0915 - fixed experimental Qt wiring
    """Run the actual installed facade and fixed peer without launch overrides."""
    provenance = verify_wheel(wheel)
    # Fixed bootstrap imports initialize the exact inspected package identities.
    _ = mss, helper_bootstrap
    runtime = inspect_runtime()
    application = QApplication([])
    window = WitnessWindow()
    window.resize(240, 100)
    window.show()
    timer = QTimer(window)
    timer.timeout.connect(window.heartbeat)
    timer.start(10)
    channel = helper_channel.HelperChannel()
    result: dict[str, object] = {"case": case, "passed": False, "pid": os.getpid()}
    replies = 0
    generation = 0
    identity: dict[str, object] = {}
    witnesses = {
        "ready_observed": False,
        "facade_deleted": False,
        "lifetime_retained": False,
    }
    errors: list[str] = []

    def exception_hook(_kind: object, error: BaseException, _trace: object) -> None:
        if len(errors) < MAX_ERRORS:
            errors.append(repr(error)[:2048])
        try:
            write_receipt(
                output.with_suffix(".diagnostic.json"), {"case": case, "errors": errors}
            )
        except Exception as diagnostic_error:  # noqa: BLE001 - still request cancellation
            if len(errors) < MAX_ERRORS:
                errors.append(repr(diagnostic_error)[:2048])
        if shiboken6.isValid(channel):
            channel.cancel()

    def identified(value: int, payload: bytes) -> None:
        if value != generation or identity:
            raise RuntimeError("Unexpected peer identity")
        identity.update(json.loads(payload))

    def death_ready() -> None:
        if not identity:
            raise RuntimeError("Missing live GUI or peer identity witness")
        write_receipt(
            output.with_suffix(".ready.json"),
            {
                "pid": os.getpid(),
                "peer": identity,
                "case": case,
                "heartbeat_count": window.active_beats,
                "paint_count": window.active_paints,
                "active_gui_observed": window.active_beats > 0
                and window.active_paints > 0,
                "maximum_heartbeat_gap": window.maximum_gap,
                "responsiveness_qualified": False,
            },
        )

    def ready(value: int) -> None:
        witnesses["ready_observed"] = True
        if value != generation:
            raise RuntimeError("Stale ready generation")
        if case == "parent-death":
            QTimer.singleShot(30, death_ready)
        elif case == "cancel-ready":
            channel.cancel()
        elif case == "ping-finish":
            channel.submit(1, b'{"operation":"ping"}')
        else:
            raise RuntimeError("Unexpected ready after cancellation/deletion")

    def reply(value: int, caller: int, payload: bytes) -> None:
        nonlocal replies
        if value != generation or caller != 1 or replies:
            raise RuntimeError("Unexpected reply correlation")
        if json.loads(payload) != {"operation": "pong"}:
            raise RuntimeError("Unexpected inert ping response")
        replies += 1
        channel.finish()

    def terminal(value: int, payload: bytes) -> None:
        receipt = json.loads(payload)
        window.active = False
        result.update(
            terminal=receipt,
            identity=identity,
            witnesses=witnesses,
            generation=value,
            replies=replies,
            heartbeat_count=window.active_beats,
            paint_count=window.active_paints,
            active_gui_observed=window.active_beats > 0 and window.active_paints > 0,
            pre_start_heartbeat_count=window.beats - window.active_beats,
            pre_start_paint_count=window.paints - window.active_paints,
            responsiveness_qualified=False,
            maximum_heartbeat_gap=window.maximum_gap,
            heartbeat_gap_scope="between active callbacks; excludes boundary gaps",
            errors=errors,
            provenance=provenance,
            passed=value == generation
            and not errors
            and admit_terminal(case, receipt, replies),
        )
        write_receipt(output, result)
        QCoreApplication.exit(0 if result["passed"] else 1)

    def start() -> None:
        nonlocal generation
        window.active = True
        generation = channel.start(runtime)
        if case == "delete-startup":
            # Read-only observation of the retained lifetime, never a launch seam.
            lifetime = channel._lifetime
            if lifetime is None:
                raise RuntimeError("Missing retained lifetime")
            lifetime.terminal.connect(terminal)
            shiboken6.delete(channel)
            witnesses["facade_deleted"] = not shiboken6.isValid(channel)
            witnesses["lifetime_retained"] = shiboken6.isValid(lifetime)
        else:
            channel.terminal.connect(terminal)
        if case == "cancel-immediate":
            channel.cancel()

    channel.identity.connect(identified)
    channel.ready.connect(ready)
    channel.reply.connect(reply)
    previous_hook = sys.excepthook
    sys.excepthook = exception_hook
    QTimer.singleShot(0, start)
    try:
        return application.exec()
    finally:
        sys.excepthook = previous_hook


class ExternalOwner:
    """Contain the fixture without interpreting its exit as a helper protocol event.

    Production OwnedHelper intentionally terminates descendants after an abnormal
    producer exit. That would invalidate this experiment's parent-death evidence.
    Reuse only the atomically-contained native API, not that lifecycle policy.
    """

    def __init__(self) -> None:
        self.api = Win32HelperApi()
        self.job: int | None = None
        self.process: NativeProcess | None = None

    def start(self, command: HelperCommand) -> None:
        """Keep job and process owners before every fallible next operation."""
        self.job = self.api.create_job()
        self.api.kill_on_close(self.job)
        self.process = self.api.create(self.job, command)
        self.api.resume(self.process.thread)

    def snapshot(self) -> dict[str, object]:
        """Read-only zero-wait inspection never terminates a surviving peer."""
        if self.job is None or self.process is None:
            raise RuntimeError("External owner was not started")
        return {
            "exit_code": self.api.exit_code(self.process.process),
            "members": list(self.api.members(self.job)),
        }

    def close(self) -> list[str]:
        """Fallback kill happens only after the verdict; close every owned handle."""
        errors: list[str] = []
        handles = []
        if self.process is not None:
            handles.extend((self.process.thread, self.process.process))
        if self.job is not None:
            handles.append(self.job)
        for handle in handles:
            try:
                self.api.close_handle(handle)
            except OSError as error:
                errors.append(repr(error))
        self.process, self.job = None, None
        return errors


class DeathWitness:
    """Pin two exact owned process handles before killing only the GUI owner."""

    def __init__(self, job: int, gui_pid: int, peer_pid: int) -> None:
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handles: list[int] = []
        for name, args, result in (
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
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        try:
            if (
                type(gui_pid) is not int
                or type(peer_pid) is not int
                or gui_pid == peer_pid
            ):
                raise ValueError("Invalid parent/peer identity")  # noqa: TRY301 - retained handle cleanup
            for pid, rights in ((gui_pid, 0x00101001), (peer_pid, 0x00101000)):
                handle = self.api.OpenProcess(rights, 0, pid)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())  # noqa: TRY301 - retained handle cleanup
                self.handles.append(handle)
                member = wintypes.BOOL()
                if (
                    not self.api.IsProcessInJob(handle, job, ctypes.byref(member))
                    or not member.value
                ):
                    raise RuntimeError("Death witness is not in the exact owned job")  # noqa: TRY301 - retained handle cleanup
                if self.api.WaitForSingleObject(handle, 0) != WAIT_TIMEOUT:
                    raise RuntimeError("Death witness was not alive before termination")  # noqa: TRY301 - retained handle cleanup
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup:  # noqa: BLE001 - preserve primary failure
                primary.add_note(f"Handle cleanup failed: {cleanup!r}")
            raise

    def kill_parent(self) -> None:
        """Terminate only the pinned fixture GUI, never its peer or outer job."""
        if not self.api.TerminateProcess(self.handles[0], DEATH_CODE):
            raise ctypes.WinError(ctypes.get_last_error())

    def exited(self) -> bool:
        """Query both pinned handles without waits or success-after-fallback."""
        results = [self.api.WaitForSingleObject(handle, 0) for handle in self.handles]
        if any(value not in (0, WAIT_TIMEOUT) for value in results):
            raise RuntimeError("Death witness wait failed")
        return results == [0, 0]

    def close(self) -> None:
        """Attempt every close, and never hide failed handle retirement."""
        errors = []
        for handle in self.handles:
            if not self.api.CloseHandle(handle):
                errors.append(ctypes.get_last_error())  # noqa: PERF401 - capture immediately
        self.handles.clear()
        if errors:
            raise RuntimeError(f"Death witness close failures: {errors}")


def observe_parent_death(
    helper: ExternalOwner, ready: dict[str, object], deadline: float
) -> dict[str, object]:
    """No fallback may contribute to this verdict; retain handles through death."""
    peer = ready.get("peer")
    job = helper.job
    if not isinstance(peer, dict) or job is None:
        raise RuntimeError("Missing owned peer witness")
    witness = DeathWitness(
        job, cast("int", ready.get("pid")), cast("int", peer.get("pid"))
    )
    result: dict[str, object] = {"passed": False, "ready": ready}
    try:
        end = min(deadline, time.monotonic() + DEATH_SECONDS)
        if time.monotonic() >= end:
            raise RuntimeError("Parent death deadline expired before termination")
        witness.kill_parent()
        while time.monotonic() < end:
            exited = witness.exited()
            status = helper.snapshot()
            if exited and status["exit_code"] is not None and status["members"] == []:
                result.update(passed=True, helper=status, pinned_processes_exited=True)
                break
            time.sleep(0.01)
        witness.close()
        if time.monotonic() >= end:
            result["passed"] = False
        return result
    finally:
        primary = sys.exception()
        try:
            witness.close()
        except BaseException as cleanup:
            if primary is None:
                raise
            primary.add_note(f"Witness cleanup failed: {cleanup!r}")


def cleanup_owner(owner: ExternalOwner | None) -> dict[str, object]:
    """Observe fallback retirement separately; it can never repair a verdict."""
    report: dict[str, object] = {"confirmed": False, "forced": False, "errors": []}
    if owner is None:
        report["confirmed"] = True
        return report
    deadline = time.monotonic() + DEATH_SECONDS
    errors: list[str] = []
    try:
        if owner.process is None:
            report["confirmed"] = True
        else:
            status = owner.snapshot()
            if status["exit_code"] is None or status["members"] != []:
                report["forced"] = True
                if owner.job is None:
                    raise RuntimeError("Lost external job before cleanup")  # noqa: TRY301 - close owners
                owner.api.terminate(owner.job)
            while time.monotonic() < deadline:
                status = owner.snapshot()
                if status["exit_code"] is not None and status["members"] == []:
                    report["confirmed"] = True
                    break
                time.sleep(0.01)
    except Exception as error:  # noqa: BLE001 - close remaining independent owners
        errors.append(repr(error))
    finally:
        errors.extend(owner.close())
        if errors or time.monotonic() >= deadline:
            report["confirmed"] = False
        report["errors"] = errors
    return report


def run_case(case: str, output: Path, wheel: Path) -> dict[str, object]:
    """Own the whole GUI process tree; fallback cleanup cannot create success."""
    receipt = output / f"{case}.json"
    helper: ExternalOwner | None = None
    started = time.monotonic()
    deadline = started + CASE_SECONDS
    result: dict[str, object] = {"case": case, "passed": False}
    try:
        helper = ExternalOwner()
        helper.start(
            HelperCommand(
                str(Path(sys.executable).resolve()),
                (
                    "-I",
                    str(Path(__file__).resolve()),
                    "--child",
                    case,
                    "--output",
                    str(receipt),
                    "--wheel",
                    str(wheel),
                ),
                str(output),
            )
        )
        while time.monotonic() < deadline:
            if case == "parent-death" and receipt.with_suffix(".ready.json").exists():
                ready = read_receipt(receipt.with_suffix(".ready.json"))
                result.update(observe_parent_death(helper, ready, deadline))
                break
            status = helper.snapshot()
            if status["exit_code"] is not None and status["members"] == []:
                value = read_receipt(receipt)
                result.update(child=value, helper=status)
                result["passed"] = (
                    status["exit_code"] == 0
                    and admit_child(case, value)
                    and time.monotonic() < deadline
                )
                break
            time.sleep(0.01)
        if not result["passed"]:
            result["failure"] = "Missing timely successful terminal and natural cleanup"
    except Exception as error:  # noqa: BLE001 - retain original fixture failure
        result["failure"] = repr(error)
    finally:
        cleanup = cleanup_owner(helper)
        result["cleanup"] = cleanup
        if (
            cleanup["confirmed"] is not True
            or cleanup.get("forced") is not False
            or time.monotonic() >= deadline
        ):
            result["passed"] = False
        result["elapsed_seconds"] = time.monotonic() - started
    return result


def main() -> int:
    """Require a fresh destination and preserve every independent case verdict."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", choices=CASES)
    args = parser.parse_args()
    wheel, output = args.wheel.resolve(strict=True), args.output.resolve()
    if args.child:
        try:
            return child(args.child, output, wheel)
        except Exception as error:  # noqa: BLE001 - preserve startup diagnostic
            write_receipt(
                output, {"case": args.child, "passed": False, "failure": repr(error)}
            )
            return 1
    output.mkdir(exist_ok=False)
    report: dict[str, object] = {"passed": False, "parent_death_qualified": False}
    try:
        report["provenance"] = verify_wheel(wheel)
        results = []
        for case in CASES:
            result = run_case(case, output, wheel)
            results.append(result)
            cleanup = result.get("cleanup")
            if not isinstance(cleanup, dict) or cleanup.get("confirmed") is not True:
                break
        passed = len(results) == len(CASES) and all(
            item["passed"] is True for item in results
        )
        report.update(cases=results, passed=passed, parent_death_qualified=passed)
    except Exception as error:  # noqa: BLE001 - retain independent setup failure
        report["failure"] = repr(error)
    write_receipt(output / "installed-channel.json", report)
    return 0 if report.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
