"""Fixed surviving owned-fixture parent-death witness, never a job owner.

Only the already-pinned actual GUI executor receives PROCESS_TERMINATE rights.
A failed or uncertain death action is never retried. The outer retains both jobs
and independently proves natural job emptiness; this process cannot do so.
"""

from __future__ import annotations

# ruff: noqa: ANN401 - fixed copied modules and ctypes ABI are loaded after attestation
import argparse
import ctypes as c
import hashlib
import importlib.util
import json
import math
import ntpath
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

Json = dict[str, Any]
DEATH_CODE = 17
ROOT_BYTES = 1_728_000
NANOSECOND = 1_000_000_000
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
PROCESS_RIGHTS = 0x00101001
MAX_JSON = 131_072
MAX_IMAGE_CHARS = 1024


def fixed_smoke() -> ModuleType:
    """Load only the adjacent fixed source, with no selectable entry point."""
    path = Path(__file__).with_name("installed_observe_smoke.py")
    spec = importlib.util.spec_from_file_location("death_observe_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Fixed observe harness unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _int(value: object, low: int = 1, high: int = 2**63 - 1) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Invalid death witness integer")
    return value


def _exact(actual: object, expected: object) -> None:
    if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(
        expected, sort_keys=True, allow_nan=False
    ):
        raise ValueError("Death witness identity or metadata changed")


def _fields(value: Json, names: str) -> None:
    if type(value) is not dict or set(value) != set(names.split()):
        raise ValueError("Death witness packet fields differ")


def check_time(deadline_ns: int) -> int:
    """Reject exact-boundary expiry; clocks never restart on progress."""
    now = time.monotonic_ns()
    if now >= deadline_ns:
        raise RuntimeError("Death witness original deadline expired")
    return now


def read_packet(path: Path) -> Json:
    """Read bounded immutable JSON with duplicate keys rejected."""

    def unique(pairs: list[tuple[str, object]]) -> Json:
        result: Json = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate death witness field")
            result[key] = value
        return result

    with path.open("rb") as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError("Death witness packet exceeds bound")
    value = json.loads(data, object_pairs_hook=unique)
    if type(value) is not dict:
        raise ValueError("Death witness packet is not an object")
    return value


def wait_packet(path: Path, journal: Any) -> Json:
    """Poll only a fixed owned handshake under the current original deadline."""
    while not path.exists():
        journal.check()
        time.sleep(0.01)
    journal.check()
    value = read_packet(path)
    journal.check()
    return value


def validate_begin(value: Json, session: str, case_ns: int, absolute_ns: int) -> None:
    """Bind the frozen reference and profiles to the authoritative case handoff."""
    _fields(
        value,
        "schema session case_deadline_ns absolute_deadline_ns identity "
        "baseline_profile temporary_profile fixture expected reference_initial "
        "before_rgb_sha256 before_rgb_byte_count",
    )
    _exact(value["schema"], 1)
    _exact(value["session"], session)
    _exact(value["case_deadline_ns"], case_ns)
    _exact(value["absolute_deadline_ns"], absolute_ns)
    _exact(value["before_rgb_byte_count"], ROOT_BYTES)
    digest = value["before_rgb_sha256"]
    if type(digest) is not str or re.fullmatch("[0-9a-f]{64}", digest) is None:
        raise ValueError("Invalid frozen reference digest")
    check_time(min(case_ns, absolute_ns))


def validate_kill(
    value: Json, begin: Json, session: str, source_hash: str
) -> tuple[int, int]:
    """Admit only first-token no-credit death under the original two-second cap."""
    _fields(value, "schema session requested_ns hold")
    _exact(value["schema"], 1)
    _exact(value["session"], session)
    hold = value["hold"]
    _fields(
        hold,
        "session generation observe_called_ns token wait_received_ns "
        "no_credit_issued no_cancel_issued harness_sha256",
    )
    for key, expected in {
        "session": session,
        "generation": 1,
        "no_credit_issued": True,
        "no_cancel_issued": True,
        "harness_sha256": source_hash,
    }.items():
        _exact(hold[key], expected)
    epoch = begin["identity"]["epoch"]
    if (
        type(epoch) is not str
        or re.fullmatch("[0-9a-f]{32}", epoch) is None
        or epoch == "0" * 32
    ):
        raise ValueError("Invalid pinned credit epoch")
    _exact(
        hold["token"],
        {
            "credit_schema": 1,
            "epoch": epoch,
            "frame_id": 1,
            "chunk_index": 0,
            "offset": 0,
            "count": 65_536,
        },
    )
    called = _int(hold["observe_called_ns"])
    received = _int(hold["wait_received_ns"], called)
    requested = _int(value["requested_ns"], received)
    before = min(
        called + 2 * NANOSECOND,
        _int(begin["case_deadline_ns"]),
        _int(begin["absolute_deadline_ns"]),
    )
    now = check_time(before)
    if requested > now:
        raise ValueError("Death request is from the future")
    retirement = min(
        requested + 5 * NANOSECOND,
        _int(begin["case_deadline_ns"]),
        _int(begin["absolute_deadline_ns"]),
    )
    return before, retirement


def read_reference(output: Path, begin: Json, smoke: Any) -> tuple[bytes, bytes]:
    """Freeze exactly the fixed bounded RGB file; never decode arbitrary images."""
    with (output / "before-root.rgb").open("rb") as stream:
        root = stream.read(ROOT_BYTES + 1)
    if (
        len(root) != ROOT_BYTES
        or hashlib.sha256(root).hexdigest() != begin["before_rgb_sha256"]
    ):
        raise ValueError("Frozen independent RGB bytes differ")
    bounds = smoke.rect(begin["reference_initial"]["rect"])
    if (bounds[2] - bounds[0], bounds[3] - bounds[1]) != (900, 640):
        raise ValueError("Frozen independent root geometry differs")
    smoke.markers(root, bounds, begin["expected"])
    panel = smoke.crop(root, bounds, smoke.rect(begin["expected"]["rect"]))
    return root, cast("bytes", panel)


@dataclass(frozen=True, slots=True)
class _LiveIdentity:
    """Immutable evidence from a successful verification before the fixed action."""

    pid: int
    created: int
    image: str
    verified_ns: int


def _native_failure(label: str) -> RuntimeError:
    code = getattr(c, "get_last_error", lambda: 0)()
    return RuntimeError(f"{label} failed (GetLastError={code})")


class ExecutorHandle:
    """Retained executor handle; no other role receives terminate rights."""

    def __init__(self, kernel: Any, journal: Any, record: Json) -> None:
        self.kernel, self.journal = kernel, journal
        self.record = dict(record)
        self._original_handle: int | None = None
        self._closed = False
        self._live_identity: _LiveIdentity | None = None
        self._action_finished_ns: int | None = None
        self._retirement_attempted = False
        self.handle: int | None = None
        self.attempted = False
        self.action: Json = {
            "attempted": False,
            "returned": None,
            "before_ns": None,
            "after_ns": None,
        }
        self._bind()
        try:

            def acquire() -> None:
                handle = self.kernel.OpenProcess(
                    PROCESS_RIGHTS, 0, record["process_id"]
                )
                if not handle:
                    raise RuntimeError("Executor OpenProcess failed")  # noqa: TRY301 - retain acquired handle
                self.handle = self._original_handle = handle

            self.journal.call("death.executor.OpenProcess", acquire)
            self.verify_live()
        except BaseException as primary:
            try:
                self.close()
            except BaseException as error:  # noqa: BLE001 - preserve primary cleanup failure
                primary.add_note(f"Executor close failed: {error!r}")
            raise

    def _bind(self) -> None:
        for name, args, result in (
            ("OpenProcess", [c.c_uint32, c.c_int32, c.c_uint32], c.c_void_p),
            ("GetProcessId", [c.c_void_p], c.c_uint32),
            (
                "GetProcessTimes",
                [c.c_void_p, *([c.POINTER(c.c_uint64)] * 4)],
                c.c_int32,
            ),
            (
                "QueryFullProcessImageNameW",
                [c.c_void_p, c.c_uint32, c.POINTER(c.c_wchar), c.POINTER(c.c_uint32)],
                c.c_int32,
            ),
            ("WaitForSingleObject", [c.c_void_p, c.c_uint32], c.c_uint32),
            ("GetExitCodeProcess", [c.c_void_p, c.POINTER(c.c_uint32)], c.c_int32),
            ("TerminateProcess", [c.c_void_p, c.c_uint32], c.c_int32),
            ("CloseHandle", [c.c_void_p], c.c_int32),
        ):
            fn = getattr(self.kernel, name)
            fn.argtypes, fn.restype = args, result

    def verify_live(self) -> Json:
        """Recheck the same handle's PID, creation, full image and liveness."""
        self._owned_handle()
        if self.attempted:
            raise RuntimeError("Executor live verification follows death attempt")

        def inspect() -> Json:
            times = [c.c_uint64() for _ in range(4)]
            image = c.create_unicode_buffer(32768)
            size = c.c_uint32(len(image))
            pid = int(self.kernel.GetProcessId(self.handle))
            if not self.kernel.GetProcessTimes(
                self.handle, *(c.byref(t) for t in times)
            ):
                raise _native_failure("Executor creation query")
            if not self.kernel.QueryFullProcessImageNameW(
                self.handle, 0, image, c.byref(size)
            ):
                raise _native_failure("Executor image query")
            if not 0 < size.value <= MAX_IMAGE_CHARS:
                raise RuntimeError("Executor image query bound exceeded")
            value = {
                "pid": pid,
                "created": times[0].value,
                "image": c.wstring_at(image, size.value),
            }
            expected = {
                "pid": self.record["process_id"],
                "created": self.record["creation_100ns"],
                "image": self.record["image_path"],
            }
            if (value["pid"], value["created"], ntpath.normcase(value["image"])) != (
                expected["pid"],
                expected["created"],
                ntpath.normcase(expected["image"]),
            ):
                raise RuntimeError("Executor retained identity changed")
            if self.kernel.WaitForSingleObject(self.handle, 0) != WAIT_TIMEOUT:
                raise RuntimeError("Executor no longer live")
            return value

        value = self.journal.call("death.executor.identity", inspect)
        self._live_identity = _LiveIdentity(
            value["pid"], value["created"], value["image"], time.monotonic_ns()
        )
        return value

    def _owned_handle(self) -> int:
        if (
            self._closed
            or type(self.handle) is not int
            or self.handle <= 0
            or self.handle != self._original_handle
        ):
            raise RuntimeError("Executor original retained handle is unavailable")
        return self.handle

    def verify_retired(self) -> Json:
        """Verify the continuously held process object, never its post-exit image."""
        handle = self._owned_handle()
        live = self._live_identity
        action_finished = self._action_finished_ns
        if (
            live is None
            or action_finished is None
            or self._retirement_attempted
            or live.verified_ns > action_finished
        ):
            raise RuntimeError(
                "Executor lacks successful original action/live evidence"
            )
        self._retirement_attempted = True
        deadline_ns = int(self.journal.deadline * NANOSECOND)

        def inspect() -> Json:
            check_time(deadline_ns)
            pid = self.kernel.GetProcessId(handle)
            if not pid:
                raise _native_failure("Executor retired PID query")
            times = [c.c_uint64() for _ in range(4)]
            if not self.kernel.GetProcessTimes(handle, *(c.byref(t) for t in times)):
                raise _native_failure("Executor retired creation query")
            if (
                type(pid) is not int
                or pid != live.pid
                or times[0].value != live.created
            ):
                raise RuntimeError("Executor retired PID or creation changed")
            state = self._retired_wait(handle)
            code = c.c_uint32()
            if not self.kernel.GetExitCodeProcess(handle, c.byref(code)):
                raise _native_failure("Executor retired exit query")
            if code.value != DEATH_CODE:
                raise RuntimeError("Executor diagnostic exit status differs")
            observed = check_time(deadline_ns)
            if observed < action_finished:
                raise RuntimeError("Executor retirement precedes its action")
            return {
                "pre_action_identity": {
                    "pid": live.pid,
                    "created": live.created,
                    "image": live.image,
                },
                "observed_process_id": pid,
                "observed_creation_100ns": times[0].value,
                "wait_result": state,
                "signaled": True,
                "exit_code": code.value,
                "observed_ns": observed,
            }

        return self.journal.call("death.executor.retired", inspect)

    def _retired_wait(self, handle: int) -> int:
        state = self.kernel.WaitForSingleObject(handle, 0)
        if state == WAIT_FAILED:
            raise _native_failure("Executor retired wait")
        if type(state) is not int or state != 0:
            raise RuntimeError("Executor retained process is not signaled")
        return state

    def terminate(self, admission_ns: int) -> Json:
        """Make one fixed action, bracketing the native call with admission checks."""
        if self.attempted or self.handle is None:
            raise RuntimeError("Executor death action cannot be retried")
        self._owned_handle()
        if self._live_identity is None:
            raise RuntimeError("Executor lacks verified pre-action identity")
        self.attempted = True

        def action() -> None:
            self.action["before_ns"] = check_time(admission_ns)
            self.action["attempted"] = True
            result = self.kernel.TerminateProcess(self.handle, DEATH_CODE)
            self.action["returned"] = bool(result)
            self.action["after_ns"] = time.monotonic_ns()
            check_time(admission_ns)
            if not result:
                raise RuntimeError("Executor termination failed or uncertain")

        self.journal.call("death.executor.TerminateProcess", action)
        self._action_finished_ns = self.action["after_ns"]
        return dict(self.action)

    def close(self) -> None:
        """Release once even when logging or original deadline checking fails."""
        handle, self._original_handle = self._original_handle, None
        self.handle = None
        self._closed = True
        if handle is None:
            return

        def release() -> None:
            if not self.kernel.CloseHandle(handle):
                raise RuntimeError("Executor CloseHandle failed")

        self.journal.cleanup("death.executor.CloseHandle", release)


def qualify_profiles(
    begin: Json, smoke: Any, topology: Any, journal: Any
) -> tuple[Json, list[Json]]:
    """Independently resolve existing exact profiles; reject substituted role PIDs."""
    baseline, temporary = begin["baseline_profile"], begin["temporary_profile"]
    console = topology.expected_console_image(journal, journal.deadline)
    _exact(console, baseline["console"])
    roles = smoke.profile_records(
        topology, baseline["records"], baseline["request"], console
    )
    _exact(roles, baseline["roles"])
    _exact(
        smoke.temporary_records(temporary, begin["identity"], baseline),
        temporary["roles"],
    )
    executor = next(
        record
        for record in baseline["records"]["processes"]
        if record["process_id"] == roles["executor"]
    )
    records = [executor, *temporary["records"]["processes"]]
    if len({record["process_id"] for record in records}) != len(records):
        raise ValueError("Death witness roles overlap")
    return {"executor": roles["executor"], **temporary["roles"]}, records


def check_pins(pins: Any, records: list[Json]) -> None:
    """Retained read-only pins must still be exactly the frozen live identities."""
    value = pins.observe()
    _exact(value["cleanup_errors"], [])
    _exact(value["processes"], records)


def wait_retired(pins: Any, journal: Any, deadline_ns: int) -> Json:
    """Wait on original handles only and retain actual exit statuses for every role."""
    for pin in pins.pins:
        while True:
            check_time(deadline_ns)
            state = journal.call(
                "death.retirement.wait",
                lambda pin=pin: int(pins.kernel.WaitForSingleObject(pin.handle, 100)),
            )
            check_time(deadline_ns)
            if state == 0:
                break
            if state != WAIT_TIMEOUT:
                raise RuntimeError("Pinned retirement wait failed")
    retired = pins.retired()
    if retired["retired"] is not True or retired["cleanup_errors"]:
        raise RuntimeError("Pinned process retirement was not proved")
    for pin, record in zip(pins.pins, retired["processes"], strict=True):

        def read_exit(pin: Any = pin) -> int:
            code = c.c_uint32()
            if not pins.kernel.GetExitCodeProcess(pin.handle, c.byref(code)):
                raise RuntimeError("Pinned exit status unavailable")
            return code.value

        record["exit_code"] = journal.call("death.retirement.exit", read_exit)
    check_time(deadline_ns)
    return retired


class Witness:
    """One fixed process lifetime with cleanup independent of the dead executor."""

    def __init__(self, args: argparse.Namespace, smoke: Any) -> None:
        self.args, self.smoke = args, smoke
        self.journal: Any = None
        self.pins: Any = None
        self.guard: Any = None
        self.executor: ExecutorHandle | None = None
        self.user: Any = None
        self.previous_dpi: Any = None
        self.result: Json = {"passed": False, "failure": None, "cleanup_errors": []}

    def run(self) -> int:
        """Retain failure before cleanup; cleanup failure can only downgrade proof."""
        try:
            self._run()
        except BaseException as error:  # noqa: BLE001 - preserve evidence before fallback
            self.result["failure"] = repr(error)
        finally:
            self._close()
        self.result["passed"] = (
            self.result["failure"] is None and not self.result["cleanup_errors"]
        )
        self.result["completed_ns"] = time.monotonic_ns()
        self.smoke.write_handshake(
            self.args.output / "death-result.json",
            {"schema": 1, "session": self.args.session, "result": self.result},
        )
        return 0 if self.result["passed"] else 1

    def _run(self) -> None:  # noqa: PLR0915 - explicit fixed witness lifecycle
        args, smoke = self.args, self.smoke
        if type(args.preparation_start) not in (int, float) or not math.isfinite(
            args.preparation_start
        ):
            raise ValueError("Invalid original preparation time")
        absolute_ns = int((args.preparation_start + 25.0) * NANOSECOND)
        self.journal = smoke.Journal(
            args.output / "death-calls.jsonl", args.preparation_start + 10.0
        )
        journal = self.journal
        support = journal.call("death.installed.imports", smoke.support)
        self.result["provenance"] = journal.call(
            "death.installed.verify", lambda: support.verify_wheel(args.wheel)
        )
        self.result["sources"] = {
            name: hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()
            ).hexdigest()
            for name in (
                "observe_death_witness.py",
                "installed_observe_smoke.py",
                "installed_channel_smoke.py",
                "installed_runtime_smoke.py",
                "observe_process_diagnostics.py",
                "observe_death_control.py",
            )
        }
        topology = journal.call("death.topology.import", smoke.topology_module)
        self.user = c.WinDLL("user32", use_last_error=True)
        self.user.SetThreadDpiAwarenessContext.argtypes = [c.c_void_p]
        self.user.SetThreadDpiAwarenessContext.restype = c.c_void_p

        def dpi() -> None:
            self.previous_dpi = self.user.SetThreadDpiAwarenessContext(c.c_void_p(-4))
            if not self.previous_dpi:
                raise RuntimeError("Witness PMv2 setup failed")

        journal.call("death.dpi.setup", dpi)
        prepared = time.monotonic()
        journal.check()
        smoke.write_handshake(
            args.output / "death-ready.json",
            {
                "schema": 1,
                "session": args.session,
                "ready": {
                    "pid": os.getpid(),
                    "ready": prepared,
                    "provenance": self.result["provenance"],
                    "sources": self.result["sources"],
                },
            },
        )
        handoff = wait_packet(
            args.output.parent / "executor" / "case-start.json", journal
        )
        journal.deadline = smoke.case_handoff(
            handoff, args.session, prepared, args.preparation_start, time.monotonic()
        )
        case_ns = int(journal.deadline * NANOSECOND)
        begin = wait_packet(args.output / "death-begin.json", journal)
        validate_begin(begin, args.session, case_ns, absolute_ns)
        self.result["begin"] = begin
        _exact(begin["fixture"]["pid"], args.fixture_pid)
        _exact(smoke.fixture_key(begin["fixture"]), begin["expected"])
        _exact(smoke.fixture_key(smoke.latest(args.states)), begin["expected"])

        def acquire_guard() -> None:
            self.guard = smoke.ReferenceGuard(begin["fixture"], args.fixture_pid)

        journal.call("death.reference.guard", acquire_guard)
        _exact(self.guard.initial, begin["reference_initial"])
        before_root, before_panel = journal.call(
            "death.reference.load", lambda: read_reference(args.output, begin, smoke)
        )
        roles, expected_records = qualify_profiles(begin, smoke, topology, journal)
        self.pins = topology.PinnedProcesses(
            [r["process_id"] for r in expected_records], journal.deadline, journal
        )
        collected = self.pins.collect()
        _exact(collected["processes"], expected_records)
        self.executor = ExecutorHandle(self.pins.kernel, journal, expected_records[0])
        check_pins(self.pins, expected_records)
        journal.call("death.reference.handoff", self.guard.check)
        smoke.write_handshake(
            args.output / "death-pinned.json",
            {
                "schema": 1,
                "session": args.session,
                "pinned": {
                    "roles": roles,
                    "records": collected,
                    "reference_initial": self.guard.initial,
                    "pinned_ns": time.monotonic_ns(),
                },
            },
        )
        request = wait_packet(args.output / "death-kill-request.json", journal)
        admission_ns, retirement_ns = validate_kill(
            request,
            begin,
            args.session,
            self.result["sources"]["installed_observe_smoke.py"],
        )
        self.result["request"] = request
        self.result["retirement_deadline_ns"] = retirement_ns
        journal.deadline = retirement_ns / NANOSECOND
        self.pins.deadline = journal.deadline
        check_pins(self.pins, expected_records)
        self.executor.verify_live()
        journal.call("death.reference.prekill", self.guard.check)
        _exact(smoke.fixture_key(smoke.latest(args.states)), begin["expected"])
        check_time(admission_ns)
        self.result["action"] = self.executor.terminate(admission_ns)
        retired = wait_retired(self.pins, journal, retirement_ns)
        self.result["retirement"] = retired
        executor_exit = next(
            r["exit_code"]
            for r in retired["processes"]
            if r["process_id"] == roles["executor"]
        )
        _exact(executor_exit, DEATH_CODE)
        self.result["executor_retirement"] = self.executor.verify_retired()
        after_root, after_panel = smoke.reference(
            journal,
            self.guard,
            args.states,
            begin["expected"],
            args.output,
            "after-death",
        )
        if before_root != after_root or before_panel != after_panel:
            raise RuntimeError("Surviving post-death reference drifted")
        check_time(retirement_ns)
        self.result["reference"] = {
            "root_sha256": hashlib.sha256(after_root).hexdigest(),
            "panel_sha256": hashlib.sha256(after_panel).hexdigest(),
            "root_bytes": len(after_root),
            "panel_bytes": len(after_panel),
            "exact_equal": True,
        }

    def _close(self) -> None:
        if self.executor is not None:
            self.result["action"] = dict(self.executor.action)
        for name, operation in (
            ("executor", self.executor.close if self.executor is not None else None),
            ("pins", self.pins.close if self.pins is not None else None),
            ("reference", self.guard.close if self.guard is not None else None),
            ("dpi", self._restore_dpi if self.previous_dpi else None),
        ):
            if operation is None:
                continue
            try:
                if name == "reference":
                    self.journal.cleanup("death.reference.close", operation)
                else:
                    result = operation()
                    if name == "pins" and result:
                        self.result["cleanup_errors"].extend(result)
            except BaseException as error:  # noqa: BLE001 - attempt every acquired resource
                self.result["cleanup_errors"].append(f"{name}: {error!r}")
        if self.journal is not None:
            try:
                self.journal.check()
            except BaseException as error:  # noqa: BLE001 - preserve late cleanup
                self.result["cleanup_errors"].append(repr(error))
            finally:
                try:
                    self.journal.stream.close()
                except BaseException as error:  # noqa: BLE001 - preserve close failure
                    self.result["cleanup_errors"].append(repr(error))

    def _restore_dpi(self) -> None:
        previous, self.previous_dpi = self.previous_dpi, None

        def restore() -> None:
            if not self.user.SetThreadDpiAwarenessContext(previous):
                raise RuntimeError("Witness DPI restoration failed")

        self.journal.cleanup("death.dpi.restore", restore)


def main() -> int:
    """Expose only the fixed witness role and owned artifact locations."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("wheel", "output", "states"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--session", required=True)
    parser.add_argument("--fixture-pid", required=True, type=int)
    parser.add_argument("--preparation-start", required=True, type=float)
    args = parser.parse_args()
    return Witness(args, fixed_smoke()).run()


if __name__ == "__main__":
    raise SystemExit(main())
