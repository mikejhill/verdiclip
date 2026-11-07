"""One fresh-wheel owned WPF observation; no scrolling or phase-cancel claims.

Copy beside installed_channel_smoke.py and installed_runtime_smoke.py. The outer
owner never captures pixels: an owned GUI/reference executor runs all possibly
blocking reference calls under external3s/15s watchdogs after separately bounded
10s owned fixture preparation. Total including cleanup is capped at25s. This
already-open-panel contract excludes cold startup from capture latency. No
production overrides. Earlier cold-start-inclusive failures remain failures.
"""

from __future__ import annotations

import argparse
import ctypes as c
import hashlib
import importlib.util
import json
import math
import os
import re
import struct
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast, override

PREPARATION_SECONDS = 10.0
OUTER_SECONDS = 25.0
CASE_SECONDS = 15.0
CALL_SECONDS = 3.0
REFERENCE_SECONDS = 2.0
CLEANUP_SECONDS = 5.0
QUIET_SECONDS = 0.2
MAX_SAMPLES = 16
MAX_PIXELS = 2_000_000
MAX_JSON = 131_072
MAX_CALLS = 512
WAIT_TIMEOUT = 258
RECT_FIELDS = 4
NATIVE_DPI = 96
FIXED_VERTICAL = 119
MAX_STATES = 256
MAX_TOPOLOGY_PIDS = 8
MAX_TEMPORARY_PIDS = 2
PEER_SCHEMA = 3
PROTOCOL_VERSION = 2
CREDIT_SCHEMA = 1
CREDIT_BYTES = 65536
MAX_CREDIT_CHUNKS = 24
CREDIT_PREFIX_BYTES = 4
CREDIT_RECORD_BYTES = 512
CREDIT_EVENT_FIELDS = 4
MARKER_COUNT = 4
RGB_CHANNELS = 3
MAX_CHANNEL = 255
STABLE_SAMPLES = 3
WITNESS_X = 960
WITNESS_Y = 704
Json = dict[str, Any]


def write(path: Path, value: object) -> None:
    """Atomic bounded receipts, never append an uncertain final result."""
    data = json.dumps(value, allow_nan=False, sort_keys=True).encode()
    if len(data) > MAX_JSON:
        raise ValueError("Observe proof receipt exceeds bound")
    pending = path.with_suffix(".tmp")
    pending.write_bytes(data)
    pending.replace(path)


def write_handshake(path: Path, value: Json) -> None:
    """A private fixed-path handshake may be published only once per session."""
    if path.exists():
        raise RuntimeError("Duplicate observation handshake")
    write(path, value)


def read(path: Path) -> Json:
    """Read bounded closed immutable evidence only."""
    with path.open("rb") as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError("Observe evidence exceeds bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise TypeError("Observe evidence is not an object")
    return value


def support() -> ModuleType:
    """Load only the exact copied verifier/ownership harness, never a launch seam."""
    path = Path(__file__).with_name("installed_channel_smoke.py")
    spec = importlib.util.spec_from_file_location("installed_observe_support", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Copied installed channel verifier missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def topology_module() -> ModuleType:
    """Load only the fixed copied owned-process diagnostic sidecar."""
    path = Path(__file__).with_name("observe_process_diagnostics.py")
    spec = importlib.util.spec_from_file_location("owned_observe_topology", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Copied topology diagnostic is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def topology_pids(value: object) -> tuple[int, ...]:
    """Never inspect more than eight distinct positive uint32 process IDs."""
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= MAX_TOPOLOGY_PIDS
        or any(type(pid) is not int or not 0 < pid < 2**32 for pid in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("Invalid bounded topology PID list")
    return tuple(int(pid) for pid in value)


def topology_request(
    value: Json, session: str, deadline: float, now: float
) -> tuple[int, ...]:
    """An immutable diagnostic request cannot extend the original preparation."""
    if set(value) != {
        "schema",
        "session",
        "pids",
        "original_snapshot_time",
        "deadline",
    }:
        raise ValueError("Topology request fields differ")
    stamp = value["original_snapshot_time"]
    if (
        type(value["schema"]) is not int
        or value["schema"] != 1
        or value["session"] != session
        or value["deadline"] != deadline
        or type(stamp) not in (int, float)
        or not math.isfinite(stamp)
        or not deadline - PREPARATION_SECONDS <= stamp <= now < deadline
    ):
        raise ValueError("Topology request identity/timing invalid")
    return topology_pids(value["pids"])


def topology_envelope(
    value: Json, session: str, pids: tuple[int, ...], field: str
) -> Json:
    """Match one fixed diagnostic response to exactly the requested targets."""
    if (
        set(value) != {"schema", "session", "pids", field}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["session"] != session
        or topology_pids(value["pids"]) != pids
        or not isinstance(value[field], dict)
    ):
        raise ValueError("Topology response identity/schema differs")
    return value[field]


def topology_targets(value: Json, pids: tuple[int, ...]) -> None:
    """No missing, repeated or unrequested process record enters evidence."""
    records = value.get("processes")
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError("Invalid topology process records")
    reported = topology_pids([r.get("requested_pid") for r in records])
    if set(reported) != set(pids):
        raise ValueError("Pinned topology targets differ")


def collect_topology(output: Path, session: str, journal: Journal) -> None:
    """Metadata-only evidence; pins survive the later outer membership snapshot."""
    request = read(output / "topology-request.json")
    pids = topology_request(request, session, journal.deadline, time.monotonic())
    module = journal.call("topology.import", topology_module)
    pins = module.PinnedProcesses(pids, journal.deadline, journal)
    try:
        collected = pins.collect()
        journal.check()
        write_handshake(
            output / "topology-pinned.json",
            {
                "schema": 1,
                "session": session,
                "pids": list(pids),
                "collected": collected,
            },
        )
        ack_path = output / "topology-membership.json"
        while not ack_path.exists():
            journal.check()
            time.sleep(0.01)
        ack = topology_envelope(read(ack_path), session, pids, "membership")
        begin, end = ack["started"], ack["finished"]
        now = time.monotonic()
        if (
            any(
                type(t) not in (int, float) or not math.isfinite(t)
                for t in (begin, end)
            )
            or not request["original_snapshot_time"]
            <= begin
            <= end
            <= now
            < journal.deadline
        ):
            raise ValueError("Topology membership acknowledgment timing invalid")
        journal.check()
        final = pins.finish()
        journal.check()
        write_handshake(
            output / "topology-result.json",
            {"schema": 1, "session": session, "pids": list(pids), "final": final},
        )
    finally:
        primary = sys.exception()
        try:
            cleanup_errors = pins.close()
            if cleanup_errors:
                write_handshake(
                    output / "topology-cleanup.json",
                    {
                        "schema": 1,
                        "session": session,
                        "pids": list(pids),
                        "cleanup_errors": cleanup_errors,
                    },
                )
        except Exception as cleanup_error:
            if primary is None:
                raise
            primary.add_note(f"Topology cleanup evidence failed: {cleanup_error!r}")


def diagnose_topology(  # noqa: PLR0913, PLR0917 - explicit fixed diagnostic scope
    output: Path,
    session: str,
    status: Json,
    snapshot_time: float,
    gui: Owner,
    started: float,
    deadline: float,
    *,
    request_written: bool = False,
    monitor: Callable[[], None] | None = None,
) -> Json:
    """Observe the rejected tree without changing the original failure/admission."""
    pids = topology_pids(status["members"])
    request = {
        "schema": 1,
        "session": session,
        "pids": list(pids),
        "original_snapshot_time": snapshot_time,
        "deadline": deadline,
    }
    topology_request(request, session, deadline, time.monotonic())
    if not request_written:
        write_handshake(output / "topology-request.json", request)
    result: Json = {"request": request, "original_snapshot": status}
    acknowledged = False
    while time.monotonic() < deadline:
        if monitor is not None:
            monitor()
        validate_calls(
            output / "calls.jsonl",
            started,
            time.monotonic,
            finished=False,
            deadline=deadline,
            diagnostic=True,
        )
        pinned_path = output / "topology-pinned.json"
        if not acknowledged and pinned_path.exists():
            collected = topology_envelope(read(pinned_path), session, pids, "collected")
            topology_targets(collected, pids)
            result["collected"] = collected
            if time.monotonic() >= deadline:
                raise RuntimeError("Late topology pin evidence")
            begin = time.monotonic()
            later = gui.snapshot()
            end = time.monotonic()
            result["later_membership"] = {
                "started": begin,
                "finished": end,
                "snapshot": later,
            }
            if end >= deadline:
                raise RuntimeError("Late topology membership snapshot")
            write_handshake(
                output / "topology-membership.json",
                {
                    "schema": 1,
                    "session": session,
                    "pids": list(pids),
                    "membership": result["later_membership"],
                },
            )
            acknowledged = True
        final_path = output / "topology-result.json"
        if acknowledged and final_path.exists():
            result["final"] = topology_envelope(
                read(final_path), session, pids, "final"
            )
            topology_targets(result["final"], pids)
            validate_calls(
                output / "calls.jsonl",
                started,
                time.monotonic,
                finished=False,
                deadline=deadline,
                diagnostic=True,
            )
            if monitor is not None:
                monitor()
            if time.monotonic() >= deadline:
                raise RuntimeError("Late topology final evidence")
            return result
        time.sleep(0.01)
    raise RuntimeError("Topology diagnosis exceeded original preparation deadline")


def rect(value: object) -> tuple[int, int, int, int]:
    """No rounding, fitted translation or negative-size crop admission."""
    if (
        not isinstance(value, list)
        or len(value) != RECT_FIELDS
        or any(
            type(x) not in (int, float)
            or not math.isfinite(x)
            or int(x) != x
            or not -(2**31) <= x < 2**31
            for x in value
        )
    ):
        raise ValueError("Nonintegral physical rectangle")
    a, b, d, e = (int(x) for x in value)
    if not a < d or not b < e:
        raise ValueError("Empty physical rectangle")
    return a, b, d, e


def fixture_key(value: Json) -> Json:
    """Freeze independent authored geometry/state, not a provider observation."""
    if (
        value.get("mode") != "observe-only"
        or value.get("alive") is not True
        or value.get("dpi_x") != NATIVE_DPI
        or value.get("dpi_y") != NATIVE_DPI
        or type(value.get("process_created_100ns")) is not int
        or not 0 < value["process_created_100ns"] < 2**64
    ):
        raise ValueError("Unqualified observe fixture mode/DPI")
    target = rect(value["rect"])
    if (target[2] - target[0], target[3] - target[1]) != (510, 430):
        raise ValueError("Full fixture panel must remain510x430")
    state = value["state"]["target"]
    if (
        type(state["horizontal"]) not in (int, float)
        or type(state["vertical"]) not in (int, float)
        or state["horizontal"] != 0
        or state["vertical"] != FIXED_VERTICAL
    ):
        raise ValueError("Observe fixture pose changed")
    return {
        k: value[k]
        for k in (
            "pid",
            "process_created_100ns",
            "root_hwnd",
            "rect",
            "content_rect",
            "state",
            "dpi_x",
            "dpi_y",
            "markers",
            "authored_layout_rect",
        )
    }


def latest(states: Path) -> Json:
    """Fixed bounded sequence; no browser or arbitrary user source."""
    files = sorted(states.glob("[0-9][0-9][0-9][0-9].json"))
    if not files or len(files) > MAX_STATES:
        raise RuntimeError("Missing/bounded fixture telemetry")
    value = read(files[-1])
    if type(value.get("sequence")) is not int or value["sequence"] != int(
        files[-1].stem
    ):
        raise RuntimeError("Fixture telemetry sequence mismatch")
    return value


def preparation_receipt(
    value: Json, session: str, pid: int, started: float, now: float
) -> Json:
    """Validate owned metadata only; never treat it as pixel/provider evidence."""
    if set(value) != {
        "schema",
        "session",
        "fixture_pid",
        "executor_pid",
        "ready",
        "fixture",
        "native",
    }:
        raise ValueError("Preparation receipt fields differ")
    if (
        type(value["schema"]) is not int
        or value["schema"] != 1
        or value["session"] != session
        or type(value["fixture_pid"]) is not int
        or value["fixture_pid"] != pid
        or type(value["executor_pid"]) is not int
        or not 0 < value["executor_pid"] < 2**32
    ):
        raise ValueError("Preparation receipt identity differs")
    ready = value["ready"]
    if (
        type(ready) not in (int, float)
        or not math.isfinite(ready)
        or not started <= ready <= now < started + PREPARATION_SECONDS
    ):
        raise ValueError("Preparation receipt timing invalid")
    fixture = value["fixture"]
    if (
        type(fixture.get("sequence")) is not int
        or not 0 <= fixture["sequence"] < MAX_STATES
        or type(fixture.get("root_hwnd")) is not int
        or not 0 < fixture["root_hwnd"] < 2**64
    ):
        raise ValueError("Preparation initialized telemetry invalid")
    key = fixture_key(fixture)
    native = value["native"]
    if (
        fixture["pid"] != pid
        or set(native) != {"root_hwnd", "pid", "rect", "input_tick", "creation_100ns"}
        or native["pid"] != pid
        or native["root_hwnd"] != fixture["root_hwnd"]
        or native["creation_100ns"] != fixture["process_created_100ns"]
        or type(native["input_tick"]) is not int
        or not 0 <= native["input_tick"] < 2**32
    ):
        raise ValueError("Preparation native identity differs")
    root, panel = rect(native["rect"]), rect(key["rect"])
    if not (
        root[0] <= panel[0] < panel[2] <= root[2]
        and root[1] <= panel[1] < panel[3] <= root[3]
    ):
        raise ValueError("Preparation panel outside root")
    return value


def case_handoff(
    value: Json, session: str, prepared: float, started: float, now: float
) -> float:
    """One outer-authored case clock, strictly within the original preparation."""
    if set(value) != {"schema", "session", "case_start", "deadline"}:
        raise ValueError("Case handoff fields differ")
    begin, deadline = value["case_start"], value["deadline"]
    if (
        type(value["schema"]) is not int
        or value["schema"] != 1
        or value["session"] != session
        or any(
            type(t) not in (int, float) or not math.isfinite(t)
            for t in (begin, deadline)
        )
        or not started <= prepared <= begin <= now < started + PREPARATION_SECONDS
        or deadline != min(begin + CASE_SECONDS, started + OUTER_SECONDS)
    ):
        raise ValueError("Case handoff identity/timing invalid")
    return float(deadline)


def crop(
    rgb: bytes, root: tuple[int, int, int, int], selected: tuple[int, int, int, int]
) -> bytes:
    """Crop only by pre-observation authored R and independent native root origin."""
    a, b, d, e = root
    left, t, r, bt = selected
    width, height = d - a, e - b
    if width * height > MAX_PIXELS or len(rgb) != width * height * 3:
        raise ValueError("Reference root pixel bound or shape")
    if not (a <= left < r <= d and b <= t < bt <= e):
        raise ValueError("Full selected panel outside native root")
    return b"".join(
        rgb[((y - b) * width + left - a) * 3 : ((y - b) * width + r - a) * 3]
        for y in range(t, bt)
    )


def markers(rgb: bytes, root: tuple[int, int, int, int], expected: Json) -> None:
    """Exact predicted off-panel coordinates only; never search for marker colors."""
    values = expected["markers"]
    if not isinstance(values, list) or len(values) != MARKER_COUNT:
        raise ValueError("Four fixed authored markers required")
    selected = rect(expected["rect"])
    seen = set()
    for marker in values:
        position = rect(marker["rect"])
        left, t, r, b = position
        if marker["id"] in seen or (r - left, b - t) != (4, 4):
            raise ValueError("Invalid fixed marker identity/size")
        seen.add(marker["id"])
        if not (
            r <= selected[0]
            or selected[2] <= left
            or b <= selected[1]
            or selected[3] <= t
        ):
            raise ValueError("Marker overlaps selected panel")
        color = marker["rgb"]
        if (
            not isinstance(color, list)
            or len(color) != RGB_CHANNELS
            or any(type(x) is not int or not 0 <= x <= MAX_CHANNEL for x in color)
        ):
            raise ValueError("Invalid marker RGB")
        if crop(rgb, root, position) != bytes(color) * 16:
            raise ValueError("Predicted off-panel marker pixels differ")


class Journal:
    """Externally visible calls bound blocking native reads, including cleanup."""

    def __init__(self, path: Path, deadline: float) -> None:
        self.path, self.deadline, self.count = path, deadline, 0
        self.stream = path.open("x", encoding="utf-8")
        self.emit("process", "executor")

    def check(self) -> None:
        """The absolute case deadline never restarts between phases."""
        if time.monotonic() >= self.deadline:
            raise RuntimeError("Observe case deadline expired")

    def emit(self, event: str, name: str, error: str | None = None) -> None:
        """Bound and flush each record before entering a blocking call."""
        if self.count >= MAX_CALLS:
            raise RuntimeError("Observe native call log exceeded bound")
        self.count += 1
        value = {"event": event, "name": name, "monotonic": time.monotonic()}
        if error is not None:
            value["error"] = error[:2048]
        self.stream.write(json.dumps(value) + "\n")
        self.stream.flush()

    def cleanup[T](self, name: str, operation: Callable[[], T]) -> T:
        """Logging/deadline failure must never prevent owned resource release."""
        logging_failure = None
        started = time.monotonic()
        try:
            self.emit("call_start", name)
        except BaseException as error:  # noqa: BLE001 - release even if logging fails
            logging_failure = error
        result = operation()
        if logging_failure is not None:
            raise logging_failure
        self.emit("call_end", name)
        if time.monotonic() - started >= CALL_SECONDS:
            raise RuntimeError("Native cleanup exceeded3s")
        return result

    def call[T](self, name: str, operation: Callable[[], T]) -> T:
        """Reject late completed native calls as well as externally killed hangs."""
        self.check()
        start = time.monotonic()
        self.emit("call_start", name)
        try:
            result = operation()
        except BaseException as error:
            try:
                self.emit("call_error", name, repr(error))
            except BaseException as logging_error:  # noqa: BLE001 - preserve primary
                error.add_note(f"Native failure log failed: {logging_error!r}")
            raise
        self.emit("call_end", name)
        if time.monotonic() - start >= CALL_SECONDS:
            raise RuntimeError("Observe completed native call exceeded3s")
        self.check()
        return result


class InputInfo(c.Structure):
    """Windows LASTINPUTINFO ABI."""

    _fields_ = [("cbSize", c.c_uint32), ("dwTime", c.c_uint32)]


class ReferenceGuard:
    """Independent Win32 root/process/input evidence; no provider geometry reuse."""

    def __init__(self, fixture: Json, pid: int) -> None:
        self.pid, self.hwnd = pid, fixture["root_hwnd"]
        if fixture["pid"] != pid:
            raise RuntimeError("Fixture PID differs from owned producer")
        self.user = c.WinDLL("user32", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.handle = None
        for api, name, args, result in (
            (self.user, "GetForegroundWindow", [], c.c_void_p),
            (
                self.user,
                "GetWindowThreadProcessId",
                [c.c_void_p, c.POINTER(c.c_uint32)],
                c.c_uint32,
            ),
            (
                self.user,
                "GetWindowRect",
                [c.c_void_p, c.POINTER(c.c_int32 * 4)],
                c.c_int32,
            ),
            (self.user, "GetLastInputInfo", [c.POINTER(InputInfo)], c.c_int32),
            (self.user, "GetDpiForWindow", [c.c_void_p], c.c_uint32),
            (self.user, "GetThreadDpiAwarenessContext", [], c.c_void_p),
            (
                self.user,
                "AreDpiAwarenessContextsEqual",
                [c.c_void_p, c.c_void_p],
                c.c_int32,
            ),
            (
                self.kernel,
                "OpenProcess",
                [c.c_uint32, c.c_int32, c.c_uint32],
                c.c_void_p,
            ),
            (
                self.kernel,
                "GetProcessTimes",
                [c.c_void_p, *([c.POINTER(c.c_uint64)] * 4)],
                c.c_int32,
            ),
            (self.kernel, "WaitForSingleObject", [c.c_void_p, c.c_uint32], c.c_uint32),
            (self.kernel, "CloseHandle", [c.c_void_p], c.c_int32),
        ):
            fn = getattr(api, name)
            fn.argtypes = args
            fn.restype = result
        try:
            self.handle = self.kernel.OpenProcess(0x00101000, 0, pid)
            if not self.handle:
                raise c.WinError(c.get_last_error())  # noqa: TRY301 - retained handle cleanup
            times = [c.c_uint64() for _ in range(4)]
            if not self.kernel.GetProcessTimes(
                self.handle, *(c.byref(t) for t in times)
            ):
                raise c.WinError(c.get_last_error())  # noqa: TRY301 - retained handle cleanup
            self.created = times[0].value
            if self.created != fixture.get("process_created_100ns"):
                raise RuntimeError("Pinned creation time differs from authored fixture")  # noqa: TRY301 - retained handle cleanup
            self.initial = self.snapshot()
        except BaseException as primary:
            try:
                self.close()
            except BaseException as error:  # noqa: BLE001 - preserve original cleanup failure
                primary.add_note(repr(error))
            raise

    def snapshot(self) -> Json:
        """No unowned HWND or changed input/geometry may enter a reference frame."""
        if self.kernel.WaitForSingleObject(self.handle, 0) != WAIT_TIMEOUT:
            raise RuntimeError("Pinned fixture process no longer alive")
        hwnd = self.user.GetForegroundWindow()
        pid = c.c_uint32()
        bounds = (c.c_int32 * 4)()
        info = InputInfo(c.sizeof(InputInfo), 0)
        if (
            hwnd != self.hwnd
            or not self.user.GetWindowThreadProcessId(hwnd, c.byref(pid))
            or pid.value != self.pid
            or not self.user.GetWindowRect(hwnd, c.byref(bounds))
            or not self.user.GetLastInputInfo(c.byref(info))
            or self.user.GetDpiForWindow(hwnd) != NATIVE_DPI
            or not self.user.AreDpiAwarenessContextsEqual(
                self.user.GetThreadDpiAwarenessContext(), c.c_void_p(-4)
            )
        ):
            raise RuntimeError("Independent owned root/input/DPI guard failed")
        return {
            "root_hwnd": hwnd,
            "pid": pid.value,
            "rect": list(bounds),
            "input_tick": info.dwTime,
            "creation_100ns": self.created,
        }

    def check(self) -> Json:
        """Require the same pinned process, root, input epoch and rectangle."""
        current = self.snapshot()
        if current != self.initial:
            raise RuntimeError("Frozen reference native guard changed")
        return current

    def excluded_window(self, hwnd: int) -> list[int]:
        """Verify actual witness geometry, not merely its requested Qt coordinates."""
        bounds = (c.c_int32 * 4)()
        if not self.user.GetWindowRect(hwnd, c.byref(bounds)):
            raise c.WinError(c.get_last_error())
        a, b, d, e = rect(self.initial["rect"])
        left, top, right, bottom = rect(list(bounds))
        if not (right <= a or d <= left or bottom <= b or e <= top):
            raise RuntimeError("GUI witness actually overlaps reference root")
        return list(bounds)

    def close(self) -> None:
        """Release the exact retained process handle once, even after failed guards."""
        handle, self.handle = self.handle, None
        if handle and not self.kernel.CloseHandle(handle):
            raise c.WinError(c.get_last_error())


def reference(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917 - fixed bounded reference protocol
    journal: Journal,
    guard: ReferenceGuard,
    states: Path,
    expected: Json,
    output: Path,
    phase: str,
) -> tuple[bytes, bytes]:
    """Cursor-free full-root independent reference with fresh bracketing telemetry."""
    import mss  # noqa: PLC0415 - isolated executor/runtime import
    import mss.tools  # noqa: PLC0415 - isolated executor/runtime import

    started = time.monotonic()
    previous = None
    count = 0
    equal_since = started
    sources: list[mss.MSS] = []
    root = rect(guard.initial["rect"])
    selected = rect(expected["rect"])
    width, height = root[2] - root[0], root[3] - root[1]
    if width * height > MAX_PIXELS:
        raise ValueError("Full root exceeds reference budget")
    try:
        journal.emit("call_start", "reference.MSS")
        source = mss.MSS(with_cursor=False)
        sources.append(source)
        journal.emit("call_end", "reference.MSS")
        journal.check()
        if time.monotonic() - started >= REFERENCE_SECONDS:
            raise RuntimeError("Reference setup exceeded phase deadline")
        for index in range(MAX_SAMPLES):
            journal.check()
            if time.monotonic() - started >= REFERENCE_SECONDS:
                break
            before = latest(states)
            if fixture_key(before) != expected:
                raise RuntimeError("Authored reference changed")
            journal.call("reference.guard.before", guard.check)
            shot = journal.call(
                "reference.grab",
                lambda: source.grab(
                    {"left": root[0], "top": root[1], "width": width, "height": height}
                ),
            )
            journal.call("reference.guard.after", guard.check)
            rgb = shot.rgb
            if (shot.width, shot.height) != (width, height):
                raise RuntimeError("Reference shape changed")
            after = latest(states)
            while (
                after["sequence"] <= before["sequence"]
                and time.monotonic() - started < REFERENCE_SECONDS
            ):
                time.sleep(0.01)
                after = latest(states)
            if (
                after["sequence"] <= before["sequence"]
                or fixture_key(after) != expected
            ):
                raise RuntimeError("Reference lacks fresh unchanged telemetry")
            markers(rgb, root, expected)
            now = time.monotonic()
            if rgb == previous:
                count += 1
            else:
                previous, count, equal_since = rgb, 1, now
            write(
                output / f"{phase}-{index:02}.json",
                {
                    "before": before,
                    "after": after,
                    "native": guard.initial,
                    "matched": count,
                    "quiet_seconds": now - equal_since,
                },
            )
            if count >= STABLE_SAMPLES and now - equal_since >= QUIET_SECONDS:
                journal.call("reference.final_guard", guard.check)
                if time.monotonic() - started >= REFERENCE_SECONDS:
                    break
                panel = crop(rgb, root, selected)
                mss.tools.to_png(
                    rgb, (width, height), output=str(output / f"{phase}-root.png")
                )
                journal.check()
                if time.monotonic() - started >= REFERENCE_SECONDS:
                    break
                return rgb, panel
            time.sleep(0.05)
        raise RuntimeError("Reference did not settle inside2s")
    finally:
        primary = sys.exception()
        for retained_source in sources:
            try:
                journal.cleanup("reference.MSS.close", retained_source.close)
                if primary is None:
                    journal.check()
                    if time.monotonic() - started >= REFERENCE_SECONDS:
                        raise RuntimeError("Reference cleanup exceeded phase deadline")  # noqa: TRY301 - preserve primary error
            except BaseException as error:
                if primary is None:
                    raise
                primary.add_note(f"Reference cleanup failed: {error!r}")


def terminal_matches(
    receipt: Json, expected: Json, digest: str, target: Json, identity: Json
) -> bool:
    """Consume real terminal guarantees, without inventing exposed EOF fields."""
    helper = receipt.get("helper", {})
    observe = receipt.get("observe", {})
    observation = observe.get("observation")
    if (
        receipt.get("passed") is not True
        or receipt.get("failure") is not None
        or receipt.get("cleanup_failure") is not None
        or receipt.get("not_launched") is not False
        or helper.get("cleanup_confirmed") is not True
        or helper.get("producer_verified") is not True
        or helper.get("closed") is not True
        or helper.get("termination_requested") is not False
        or helper.get("remaining_pids") != []
        or helper.get("cleanup_errors") != []
        or helper.get("failure") is not None
        or helper.get("exit_code") != 0
        or helper.get("pid") != identity.get("pid")
        or not identity
        or observe.get("image_sha256") != digest
        or observe.get("bytes") != 510 * 430 * 3
        or not isinstance(observation, dict)
    ):
        return False
    from verdiclip.capture.observe_protocol import (  # noqa: PLC0415 - isolated runtime import
        Observation,
    )

    normalized = Observation.parse(json.dumps(observation).encode())
    if normalized.target.value() != target:
        return False
    guard = observation["guard"]
    times = [
        observe.get(k)
        for k in ("first_body_ns", "validated_ns", "finished_ns", "deadline_ns")
    ]
    return bool(
        all(type(t) is int and t > 0 for t in times)
        and times[0] <= times[1] <= times[2] < times[3]
        and guard["target_rect"] == expected["rect"]
        and guard["capture_rect"] == expected["rect"]
        and guard["input_tick"] == expected.get("reference_input_tick")
    )


class CreditFacade(Protocol):
    """Only the public capacity method is available to this proof adapter."""

    def grant_frame_credit(
        self, generation: int, request_id: int, chunk_index: int
    ) -> None:
        """Grant the current immutable token once."""


def unique_credit_fields(pairs: list[tuple[str, object]]) -> Json:
    """Independent strict parser; duplicate fields cannot hide wrong identity."""
    result: Json = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate credit proof field")
        result[key] = value
    return result


class CreditTranscript:
    """Independent bounded success transcript, not a protocol implementation."""

    def __init__(self, generation: int, epoch: str, size: int) -> None:
        if (
            type(generation) is not int
            or generation <= 0
            or type(epoch) is not str
            or re.fullmatch(r"[0-9a-f]{32}", epoch) is None
            or type(size) is not int
            or not 0 < size <= 1024 * 512 * 3
        ):
            raise ValueError("Invalid independent credit proof identity")
        self.generation, self.epoch, self.size = generation, epoch, size
        self.count = (size + CREDIT_BYTES - 1) // CREDIT_BYTES
        self.events: list[Json] = []
        self.failed = False

    def grant(
        self, generation: int, raw: bytes, facade: CreditFacade, journal: Journal
    ) -> None:
        """Validate one announced capacity token and call the public method once."""
        try:
            journal.check()
            if (
                self.failed
                or type(generation) is not int
                or generation != self.generation
                or type(raw) is not bytes
                or not CREDIT_PREFIX_BYTES < len(raw) <= CREDIT_RECORD_BYTES
                or struct.unpack(">I", raw[:CREDIT_PREFIX_BYTES])[0]
                != len(raw) - CREDIT_PREFIX_BYTES
                or len(self.events) >= self.count
            ):
                raise ValueError("Invalid credit proof event")  # noqa: TRY301 - latch before propagation
            message = json.loads(
                raw[CREDIT_PREFIX_BYTES:], object_pairs_hook=unique_credit_fields
            )
            index = len(self.events)
            payload = {
                "credit_schema": CREDIT_SCHEMA,
                "epoch": self.epoch,
                "frame_id": 1,
                "chunk_index": index,
                "offset": index * CREDIT_BYTES,
                "count": min(CREDIT_BYTES, self.size - index * CREDIT_BYTES),
            }
            expected = {
                "version": PROTOCOL_VERSION,
                "kind": "frame_wait",
                "request_id": 1,
                "payload": payload,
            }
            # Canonical comparison also rejects bool/int equivalence and floats.
            if json.dumps(message, sort_keys=True, allow_nan=False) != json.dumps(
                expected, sort_keys=True
            ):
                raise ValueError("Credit proof token differs from fixed sequence")  # noqa: TRY301
            event: Json = {
                "token": payload,
                "wait_received_ns": time.monotonic_ns(),
                "grant_called_ns": time.monotonic_ns(),
                "grant_returned_ns": None,
            }
            self.events.append(event)  # Consume before a reentrant public call.
            journal.call(
                "credit.grant", lambda: facade.grant_frame_credit(generation, 1, index)
            )
            event["grant_returned_ns"] = time.monotonic_ns()
            journal.check()
        except BaseException:
            self.failed = True
            raise

    def verify(self, receipt: Json) -> Json:  # noqa: C901 - independent terminal proof
        """Cross-check public requests against worker reservations and byte totals."""
        observe = receipt.get("observe", {})
        state = observe.get("credit")
        observation = observe.get("observation", {})
        if (
            not isinstance(observation, dict)
            or observation.get("epoch") != self.epoch
            or type(observation.get("request_id")) is not int
            or observation["request_id"] != 1
            or type(observation.get("frame_id")) is not int
            or observation["frame_id"] != 1
        ):
            raise ValueError("Credit receipt identity differs")
        if (
            self.failed
            or receipt.get("passed") is not True
            or len(self.events) != self.count
            or not isinstance(state, dict)
            or set(state)
            != {
                "pending",
                "granted_bytes",
                "accepted_bytes",
                "last_grant_sequence",
                "cancel_sequence",
                "cancel_time_ns",
                "observed_sequence",
                "observed_time_ns",
                "failed",
                "events",
            }
            or observe.get("credit_cancellation") is not None
        ):
            raise ValueError("Incomplete credit proof transcript")
        for key, expected in {
            "granted_bytes": self.size,
            "accepted_bytes": self.size,
            "last_grant_sequence": self.count * 2,
            "cancel_sequence": 0,
            "cancel_time_ns": 0,
            "observed_sequence": 0,
            "observed_time_ns": 0,
            "transfer_messages": 4 + 2 * self.count,
        }.items():
            actual = observe.get(key) if key == "transfer_messages" else state.get(key)
            if type(actual) is not int or actual != expected:
                raise ValueError("Credit terminal counters differ")
        if state["pending"] is not None or state["failed"] is not False:
            raise ValueError("Credit transfer did not finish cleanly")
        worker_events = state["events"]
        deadline, finished = observe.get("deadline_ns"), observe.get("finished_ns")
        if (
            type(deadline) is not int
            or type(finished) is not int
            or not 0 < finished < deadline
            or type(worker_events) is not list
            or len(worker_events) != 2 * self.count
        ):
            raise ValueError("Invalid credit proof ordering")
        previous = 0
        for index, event in enumerate(self.events):
            wait, grant = worker_events[2 * index : 2 * index + 2]
            for value, kind, sequence in (
                (wait, "wait", 2 * index + 1),
                (grant, "grant", 2 * index + 2),
            ):
                if (
                    type(value) is not list
                    or len(value) != CREDIT_EVENT_FIELDS
                    or type(value[0]) is not int
                    or type(value[1]) is not int
                    or type(value[3]) is not int
                    or value[0] != sequence
                    or value[2] != kind
                    or value[3] != index
                ):
                    raise ValueError("Invalid worker credit event")
            received, called, returned = (
                event[k]
                for k in ("wait_received_ns", "grant_called_ns", "grant_returned_ns")
            )
            if (
                any(type(t) is not int for t in (received, called, returned))
                or not previous <= wait[1] <= received <= called <= returned < deadline
                or not called <= grant[1] <= finished
                or not grant[1] < deadline
            ):
                raise ValueError("Credit event crossed original deadline or order")
            previous = grant[1]
        return {
            "schema": CREDIT_SCHEMA,
            "chunks": self.count,
            "bytes": self.size,
            "public_events": self.events,
            "worker_events": worker_events,
            "exact_sequence": True,
        }


def compare_retained_frame(
    frame: object, reference_rgb: bytes, receipt: bytes, expected_rect: tuple[int, ...]
) -> Json:
    """Compare every retained byte off the GUI thread, without fitting a crop."""
    from verdiclip.capture.observe_protocol import (  # noqa: PLC0415 - installed consumer
        Observation,
    )
    from verdiclip.capture.retained_frame import (  # noqa: PLC0415 - installed consumer
        VerifiedFrame,
    )

    if threading.current_thread() is threading.main_thread():
        raise RuntimeError("Retained comparison requires the independent consumer")
    if type(frame) is not VerifiedFrame or type(reference_rgb) is not bytes:
        raise ValueError("Expected immutable retained frame and reference")
    observation = Observation.parse(frame.observation)
    identity = frame.identity
    bounds = (
        identity.x,
        identity.y,
        identity.x + identity.width,
        identity.y + identity.height,
    )
    if (
        bounds != expected_rect
        or identity != observation.frame_identity()
        or frame.receipt_sha256 != hashlib.sha256(receipt).digest()
        or json.loads(frame.observation)
        != json.loads(receipt)["observe"]["observation"]
        or len(reference_rgb) != identity.payload_bytes
        or frame.rgb != reference_rgb
    ):
        raise ValueError("Retained full-panel bytes or identity differ from reference")
    return {
        "full_rgb_exact": True,
        "bytes": len(reference_rgb),
        "rect": list(bounds),
        "consumer_off_gui": True,
        "receipt_sha256": frame.receipt_sha256.hex(),
    }


class RetainedConsumer(threading.Thread):
    """One supervised fixture-only consumer; output contains no image references."""

    def __init__(
        self,
        frame: object,
        reference_rgb: bytes,
        receipt: bytes,
        expected_rect: tuple[int, ...],
    ) -> None:
        super().__init__(name="owned-retained-pixel-consumer", daemon=False)
        self.inputs: tuple[object, bytes, bytes, tuple[int, ...]] | None = (
            frame,
            reference_rgb,
            receipt,
            expected_rect,
        )
        self.result: Json | None = None
        self.error: str | None = None

    def begin(self, journal: Journal) -> float:
        """Start under the existing call/case limits, retaining failure evidence."""
        try:
            journal.check()
            started = time.monotonic()
            journal.emit("call_start", "retained.compare")
            journal.check()
            if time.monotonic() - started >= CALL_SECONDS:
                raise RuntimeError("Retained comparison exceeded3s before start")  # noqa: TRY301 - discard untransferred inputs
            self.start()
        except BaseException:
            self.inputs = None
            raise
        return started

    def poll(self, journal: Journal, started: float) -> Json | None:
        """Admit completed comparison only before the original fixed deadlines."""
        journal.check()
        if time.monotonic() - started >= CALL_SECONDS:
            raise RuntimeError("Retained comparison exceeded3s")
        if self.is_alive():
            return None
        self.join(timeout=0)
        journal.emit("call_end", "retained.compare")
        journal.check()
        if time.monotonic() - started >= CALL_SECONDS:
            raise RuntimeError("Retained comparison exceeded3s")
        if self.error is not None or self.result is None:
            raise RuntimeError(f"Retained comparison failed: {self.error}")
        return self.result

    @override
    def run(self) -> None:
        """Drop input aliases before completion; retain no traceback."""
        inputs, self.inputs = self.inputs, None
        try:
            if inputs is None:
                raise RuntimeError("Missing retained consumer inputs")  # noqa: TRY301 - bounded worker failure
            self.result = compare_retained_frame(*inputs)
        except BaseException as error:  # noqa: BLE001 - bounded diagnostic, no traceback pixels
            self.error = repr(error)[:2048]
        finally:
            inputs = None


class BaselinePins(Protocol):
    """Retained exact-process witnesses, never an owning job capability."""

    deadline: float

    def observe(self) -> Json:
        """Recheck the held process identities without releasing them."""
        ...

    def finish(self) -> Json:
        """Recheck and release all baseline pins before executor exit."""
        ...

    def close(self) -> list[str]:
        """Idempotently release every remaining pin."""
        ...


def ownership_interval(value: Json, lower: float, deadline: float) -> None:
    """Reject malformed, stale or late native snapshot timing without rounding."""
    begin, end = value.get("started"), value.get("finished")
    if (
        type(begin) not in (int, float)
        or type(end) not in (int, float)
        or not isinstance(begin, (int, float))
        or not isinstance(end, (int, float))
        or not math.isfinite(begin)
        or not math.isfinite(end)
        or not lower <= begin <= end <= time.monotonic() < deadline
    ):
        raise ValueError("Ownership snapshot interval invalid")


def ownership_packet(value: Json, session: str, field: str) -> Json:
    """Only a fixed session-bound ownership exchange is accepted."""
    if (
        set(value) != {"schema", "session", field}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["session"] != session
        or not isinstance(value[field], dict)
    ):
        raise ValueError("Ownership packet identity/schema differs")
    return value[field]


def wait_owned_file(path: Path, journal: Journal) -> Json:
    """Wait under the current original deadline, never creating a fresh budget."""
    while not path.exists():
        journal.check()
        time.sleep(0.01)
    value = read(path)
    journal.check()
    return value


def profile_records(
    module: ModuleType, records: Json, request: Json, console: str
) -> Json:
    """Resolve only the exact independently expected process roles."""
    topology_targets(records, topology_pids(request["pids"]))
    if records.get("cleanup_errors", []) != []:
        raise ValueError("Baseline pin cleanup was not clean")
    return dict(
        module.validate_profile(
            records["processes"],
            request["launch_pid"],
            request["executor_pid"],
            request["expected_launch"],
            request["expected_base"],
            console,
        )
    )


def acquire_ownership(
    output: Path, session: str, started: float, journal: Journal
) -> tuple[BaselinePins, Json]:
    """Hold baseline pins through capture, never retaining an owning job handle."""
    request = ownership_packet(
        wait_owned_file(output / "ownership-request.json", journal), session, "request"
    )
    required = {
        "pids",
        "launch_pid",
        "executor_pid",
        "expected_launch",
        "expected_base",
        "snapshot_time",
        "deadline",
    }
    if set(request) != required or request["deadline"] != journal.deadline:
        raise ValueError("Ownership request fields/deadline differ")
    pids = topology_pids(request["pids"])
    if (
        request["executor_pid"] != os.getpid()
        or type(request["launch_pid"]) is not int
        or request["launch_pid"] not in pids
        or type(request["executor_pid"]) is not int
        or request["executor_pid"] not in pids
        or type(request["snapshot_time"]) not in (int, float)
        or not math.isfinite(request["snapshot_time"])
        or not started
        <= request["snapshot_time"]
        <= time.monotonic()
        < journal.deadline
    ):
        raise ValueError("Ownership request lifetime differs")
    module = journal.call("ownership.import", topology_module)
    console = module.expected_console_image(journal, journal.deadline)
    pins = module.PinnedProcesses(pids, journal.deadline, journal)
    try:
        records = pins.collect()
        roles = profile_records(module, records, request, console)
        pinned = {
            "request": request,
            "console": console,
            "roles": roles,
            "records": records,
        }
        journal.check()
        write_handshake(
            output / "ownership-pinned.json",
            {"schema": 1, "session": session, "pinned": pinned},
        )
        membership = ownership_packet(
            wait_owned_file(output / "ownership-membership.json", journal),
            session,
            "membership",
        )
        ownership_interval(membership, request["snapshot_time"], journal.deadline)
        if (
            set(membership) != {"started", "finished", "snapshot"}
            or membership["snapshot"]["exit_code"] is not None
            or set(topology_pids(membership["snapshot"]["members"])) != set(pids)
        ):
            raise ValueError("Baseline is not currently contained/alive")  # noqa: TRY301 - release owned resources and preserve failure
        current = pins.observe()
        if (
            profile_records(module, current, request, console) != roles
            or current["processes"] != records["processes"]
        ):
            raise ValueError("Baseline identity changed across membership snapshot")  # noqa: TRY301 - release owned resources and preserve failure
        journal.check()
        write_handshake(
            output / "ownership-ready.json",
            {"schema": 1, "session": session, "ready": pinned},
        )
        return cast("BaselinePins", pins), pinned
    except BaseException:
        pins.close()
        raise


def prepare_owned_case(  # noqa: PLR0913, PLR0917 - explicit fixed ownership scope
    output: Path,
    states: Path,
    session: str,
    started: float,
    pid: int,
    fixture: Json,
    expected: Json,
    guard: ReferenceGuard,
    journal: Journal,
) -> tuple[Json, BaselinePins, Json]:
    """Prepare the already-open target and admit only the explicit ownership profile."""
    native = journal.call("preparation.ready_guard", guard.check)
    if fixture_key(latest(states)) != expected:
        raise RuntimeError("Fixture changed during preparation")
    ready = time.monotonic()
    receipt = {
        "schema": 1,
        "session": session,
        "fixture_pid": pid,
        "executor_pid": os.getpid(),
        "ready": ready,
        "fixture": fixture,
        "native": native,
    }
    preparation_receipt(receipt, session, pid, started, ready)
    write_handshake(output / "preparation-ready.json", receipt)
    pins, profile = acquire_ownership(output, session, started, journal)
    try:
        handoff = wait_owned_file(output / "case-start.json", journal)
        deadline = case_handoff(handoff, session, ready, started, time.monotonic())
        journal.deadline = deadline
        pins.deadline = deadline  # Same single preparation-to-case transition.
        journal.call("case.initial_guard", guard.check)
        if fixture_key(latest(states)) != expected:
            raise RuntimeError("Fixture changed across ownership handoff")  # noqa: TRY301 - release owned resources and preserve failure
        return {**handoff, "ready": ready}, pins, profile  # noqa: TRY300 - ownership transferred only on success
    except BaseException:
        pins.close()
        raise


def qualify_ownership(  # noqa: PLR0913, PLR0917 - explicit fixed ownership scope
    output: Path,
    session: str,
    status: Json,
    stamp: float,
    gui: Owner,
    started: float,
    deadline: float,
    launch_pid: int,
    executor_pid: int,
    expected_launch: str,
    expected_base: str,
) -> Json:
    """Use held identities and an exact later job snapshot, not name exemptions."""
    pids = topology_pids(status["members"])
    request = {
        "pids": list(pids),
        "launch_pid": launch_pid,
        "executor_pid": executor_pid,
        "expected_launch": expected_launch,
        "expected_base": expected_base,
        "snapshot_time": stamp,
        "deadline": deadline,
    }
    write_handshake(
        output / "ownership-request.json",
        {"schema": 1, "session": session, "request": request},
    )
    pinned = None
    module = (
        topology_module()
    )  # Pure validation only; no native calls in the outer owner.
    while time.monotonic() < deadline:
        validate_calls(
            output / "calls.jsonl",
            started,
            time.monotonic,
            finished=False,
            deadline=deadline,
        )
        path = output / "ownership-pinned.json"
        if pinned is None and path.exists():
            candidate = ownership_packet(read(path), session, "pinned")
            if (
                set(candidate) != {"request", "console", "roles", "records"}
                or candidate["request"] != request
            ):
                raise ValueError("Ownership response substituted launch expectations")
            roles = profile_records(
                module, candidate["records"], request, candidate["console"]
            )
            if roles != candidate["roles"]:
                raise ValueError("Ownership roles differ")
            begin = time.monotonic()
            if begin >= deadline:
                raise RuntimeError("Late ownership profile")
            current = gui.snapshot()
            end = time.monotonic()
            if (
                end >= deadline
                or current["exit_code"] is not None
                or set(topology_pids(current["members"])) != set(pids)
            ):
                raise RuntimeError("Ownership job changed while pins were retained")
            membership = {"started": begin, "finished": end, "snapshot": current}
            write_handshake(
                output / "ownership-membership.json",
                {"schema": 1, "session": session, "membership": membership},
            )
            pinned = candidate
        path = output / "ownership-ready.json"
        if pinned is not None and path.exists():
            ready = ownership_packet(read(path), session, "ready")
            if ready != pinned:
                raise ValueError("Ownership ready differs from pinned profile")
            validate_calls(
                output / "calls.jsonl",
                started,
                time.monotonic,
                finished=False,
                deadline=deadline,
            )
            if time.monotonic() >= deadline:
                raise RuntimeError("Late ownership readiness")
            return pinned
        time.sleep(0.01)
    raise RuntimeError("Ownership qualification exceeded original preparation deadline")


OWNERSHIP_PHASES = ("pre-reference", "pre-observe", "post-terminal")


def temporary_records(value: Json, peer: Json, baseline: Json) -> dict[str, int]:
    """Validate exact prospective roles using independent baseline expectations."""
    module = topology_module()
    executor = baseline["roles"]["executor"]
    original = next(
        r for r in baseline["records"]["processes"] if r["process_id"] == executor
    )
    if (
        value.get("peer") != peer
        or value.get("console") != baseline["console"]
        or value["records"].get("cleanup_errors", []) != []
    ):
        raise ValueError("Temporary profile identity or console expectation changed")
    return dict(
        module.validate_temporary_profile(
            value["records"]["processes"],
            peer["pid"],
            executor,
            original["creation_100ns"],
            baseline["request"]["expected_base"],
            baseline["console"],
        )
    )


def acquire_temporary(
    output: Path, session: str, peer: Json, baseline: Json, journal: Journal
) -> tuple[Any, Json]:
    """Pin the fixed temporary roles in the supervised GUI before observe."""
    requested = time.monotonic()
    write_handshake(
        output / "temporary-request.json",
        {
            "schema": 1,
            "session": session,
            "request": {
                "peer": peer,
                "requested": requested,
                "deadline": journal.deadline,
            },
        },
    )
    targets = ownership_packet(
        wait_owned_file(output / "temporary-targets.json", journal), session, "targets"
    )
    ownership_interval(targets, requested, journal.deadline)
    if (
        set(targets) != {"peer", "pids", "deadline", "started", "finished", "snapshot"}
        or targets["peer"] != peer
        or targets["deadline"] != journal.deadline
    ):
        raise ValueError("Temporary targets changed")
    pids = topology_pids(targets["pids"])
    if not 1 <= len(pids) <= MAX_TEMPORARY_PIDS or peer["pid"] not in pids:
        raise ValueError("Temporary target set invalid")
    module = journal.call("temporary.import", topology_module)
    journal.check()
    pins = module.PinnedProcesses(pids, journal.deadline, journal)
    try:
        value = {
            "peer": peer,
            "console": baseline["console"],
            "records": pins.collect(),
        }
        value["roles"] = temporary_records(value, peer, baseline)
        if set(value["roles"].values()) != set(pids):
            raise RuntimeError("Collected temporary identities differ from targets")  # noqa: TRY301 - close pins on failed admission
        journal.check()
        write_handshake(
            output / "temporary-pinned.json",
            {"schema": 1, "session": session, "profile": value},
        )
    except BaseException as error:
        try:
            pins.close()
        except Exception as cleanup_error:  # noqa: BLE001 - preserve primary
            error.add_note(f"Temporary pin cleanup failed: {cleanup_error!r}")
        raise
    else:
        return pins, value


def serve_temporary(  # noqa: PLR0913, PLR0917 - fixed handshake
    output: Path,
    session: str,
    gui: Owner,
    baseline: Json,
    seen: set[int],
    deadline: float,
) -> Json | None:
    """Select bounded targets only; this exchange never authorizes observation."""
    path = output / "temporary-request.json"
    if not path.exists():
        return None
    request = ownership_packet(read(path), session, "request")
    peer = ownership_packet(read(output / "peer-identity.json"), session, "peer")
    if (
        set(request) != {"peer", "requested", "deadline"}
        or request["peer"] != peer
        or request["deadline"] != deadline
        or type(peer.get("pid")) is not int
        or not 0 < peer["pid"] < 2**32
        or peer.get("executable") != baseline["request"]["expected_base"]
        or type(peer.get("protocol_version")) is not int
        or type(peer.get("peer_schema")) is not int
        or type(peer.get("credit_schema")) is not int
        or peer.get("protocol_version") != PROTOCOL_VERSION
        or peer.get("peer_schema") != PEER_SCHEMA
        or peer.get("credit_schema") != CREDIT_SCHEMA
        or not isinstance(peer.get("epoch"), str)
        or re.fullmatch(r"[0-9a-f]{32}", peer["epoch"]) is None
    ):
        raise ValueError("Temporary request lacks exact channel identity")
    ownership_interval(
        {"started": request["requested"], "finished": request["requested"]},
        deadline - CASE_SECONDS,
        deadline,
    )
    target_path = output / "temporary-targets.json"
    if not target_path.exists():
        begin = time.monotonic()
        if begin >= deadline:
            raise RuntimeError("Late temporary target snapshot")
        status = gui.snapshot()
        end = time.monotonic()
        base = set(baseline["request"]["pids"])
        extra = set(topology_pids(status["members"])) - base
        if (
            end >= deadline
            or status["exit_code"] is not None
            or not base <= set(topology_pids(status["members"]))
            or not 1 <= len(extra) <= MAX_TEMPORARY_PIDS
            or peer["pid"] not in extra
            or seen - extra
        ):
            raise RuntimeError("Temporary target membership/history changed")
        write_handshake(
            target_path,
            {
                "schema": 1,
                "session": session,
                "targets": {
                    "peer": peer,
                    "pids": sorted(extra),
                    "deadline": deadline,
                    "started": begin,
                    "finished": end,
                    "snapshot": status,
                },
            },
        )
    pinned = output / "temporary-pinned.json"
    if not pinned.exists():
        return None
    value = ownership_packet(read(pinned), session, "profile")
    roles = temporary_records(value, peer, baseline)
    targets = ownership_packet(read(target_path), session, "targets")
    if (
        value.get("roles") != roles
        or set(roles.values()) != set(targets["pids"])
        or seen - set(roles.values())
    ):
        raise RuntimeError("Frozen temporary roles or history changed")
    if time.monotonic() >= deadline:
        raise RuntimeError("Late temporary role admission")
    return value


def ownership_checkpoint(  # noqa: PLR0913, PLR0917 - explicit fixed ownership scope
    output: Path,
    session: str,
    phase: str,
    peer: Json | None,
    pins: BaselinePins,
    profile: Json,
    journal: Journal,
    temporary: Json | None = None,
) -> None:
    """A stable-phase exact membership observation precedes pixels or admission."""
    if phase not in OWNERSHIP_PHASES:
        raise ValueError("Unknown ownership checkpoint")
    requested = time.monotonic()
    request = {
        "phase": phase,
        "deadline": journal.deadline,
        "peer": peer,
        "requested": requested,
    }
    journal.check()
    write_handshake(
        output / f"ownership-{phase}-request.json",
        {"schema": 1, "session": session, "request": request},
    )
    response = ownership_packet(
        wait_owned_file(output / f"ownership-{phase}-ack.json", journal), session, "ack"
    )
    if set(response) != {"phase", "deadline", "started", "finished", "snapshot"}:
        raise ValueError("Ownership checkpoint acknowledgment fields differ")
    ownership_interval(response, requested, journal.deadline)
    wanted = set(profile["request"]["pids"])
    if phase == "pre-observe":
        if peer is None or not peer:
            raise ValueError("Pre-observe lacks attested helper identity")
        wanted.update(
            temporary["roles"].values() if temporary is not None else [peer["pid"]]
        )
    if (
        response["phase"] != phase
        or response["deadline"] != journal.deadline
        or response["snapshot"]["exit_code"] is not None
        or set(topology_pids(response["snapshot"]["members"])) != wanted
    ):
        raise RuntimeError("Stable ownership checkpoint changed")
    current = pins.observe()
    if (
        current["processes"] != profile["records"]["processes"]
        or current["cleanup_errors"]
    ):
        raise RuntimeError("Held baseline identity changed before capture")
    journal.check()


def track_temporary_member(  # noqa: PLR0913 - bounded ownership inputs
    members: set[int],
    baseline: set[int],
    seen: set[int],
    peer_pid: int | None,
    *,
    retiring: bool,
    admitted: set[int] | None = None,
) -> None:
    """At most two provisional PIDs must become exactly the pinned temporary roles."""
    if not retiring and not baseline <= members:
        raise RuntimeError("A retained baseline process disappeared")
    extra = members - baseline
    if retiring and extra:
        # Post-terminal cleanup no longer retains an authenticated helper identity.
        raise RuntimeError("Unexpected process after helper retirement")
    seen.update(extra)
    allowed = admitted if admitted is not None else {peer_pid}
    if len(seen) > MAX_TEMPORARY_PIDS or (peer_pid is not None and seen - allowed):
        raise RuntimeError("Unexpected process during the attested helper lifecycle")


def retain_membership_failure(
    output: Path, result: Json, context: Json, error: Exception
) -> None:
    """Latch the original rejection before optional evidence reads or scheduling."""
    result["first_failure"] = repr(error)
    frozen = json.loads(json.dumps(context, allow_nan=False))
    result["membership_failure"] = frozen
    try:
        write_handshake(output / "membership-failure.json", frozen)
    except Exception as failure:  # noqa: BLE001 - evidence cannot replace rejection
        result["membership_failure_persistence_error"] = repr(failure)[:2048]


def diagnose_startup_failure(  # noqa: PLR0913, PLR0917 - fixed failure-only scope
    output: Path,
    executor: Path,
    session: str,
    context: Json,
    gui: Owner,
    module: ModuleType,
    owners: dict[str, Any],
    provenance: Json,
    wheel: Path,
    case_deadline: float,
    absolute_deadline: float,
) -> Json:
    """Later pinned identities never retroactively authorize the rejected snapshot."""
    failure_time = context["failure_time"]
    deadline = min(failure_time + CALL_SECONDS, case_deadline, absolute_deadline)
    evidence: Json = {
        "deadline": deadline,
        "status": "unavailable",
        "scope": "later process identities; not identity proof at original failure",
    }
    try:
        if any(
            phase in context["completed_phases"]
            for phase in ("pre-observe", "post-terminal")
        ):
            evidence["reason"] = "Later-phase failure is retention only"
            return evidence
        if (executor / "ownership-pre-observe-ack.json").exists():
            raise RuntimeError("Observation acknowledgment already exists")  # noqa: TRY301 - retain diagnostic failure only
        pids = topology_pids(
            sorted(set(context["snapshot"]["members"]) - set(context["baseline"]))
        )
        if time.monotonic() >= deadline:
            raise RuntimeError("No remaining startup diagnostic budget")  # noqa: TRY301 - retain diagnostic failure only
        diagnostic = output / "startup-diagnostics"
        diagnostic.mkdir()
        request = {
            "schema": 1,
            "session": session,
            "pids": list(pids),
            "original_snapshot_time": context["snapshot_finished"],
            "deadline": deadline,
        }
        write_handshake(diagnostic / "topology-request.json", request)
        from verdiclip.platform.owned_helper import (  # noqa: PLC0415 - outer only
            HelperCommand,
        )

        script = Path(__file__).resolve()
        sidecar = script.with_name("observe_process_diagnostics.py")
        command = HelperCommand(
            provenance["executable"],
            (
                "-I",
                "-S",
                str(script),
                "--topology-child",
                "--wheel",
                str(wheel),
                "--output",
                str(diagnostic),
                "--session",
                session,
                "--diagnostic-deadline",
                str(deadline),
                "--diagnostic-harness-sha",
                hashlib.sha256(script.read_bytes()).hexdigest(),
                "--diagnostic-sidecar-sha",
                hashlib.sha256(sidecar.read_bytes()).hexdigest(),
            ),
            str(diagnostic),
        )
        if time.monotonic() >= deadline:
            raise RuntimeError("Late diagnostic owner construction")  # noqa: TRY301 - retain diagnostic failure only
        owner = module.ExternalOwner()
        owners["startup-diagnostic"] = owner
        if time.monotonic() >= deadline:
            raise RuntimeError("Late diagnostic native launch")  # noqa: TRY301 - retain diagnostic failure only
        owner.start(command)
        if time.monotonic() >= deadline:
            raise RuntimeError("Late diagnostic native launch completion")  # noqa: TRY301 - retain diagnostic failure only
        evidence["result"] = diagnose_topology(
            diagnostic,
            session,
            {"members": list(pids)},
            context["snapshot_finished"],
            gui,
            failure_time,
            deadline,
            request_written=True,
            monitor=lambda: validate_calls(
                executor / "calls.jsonl",
                context["preparation_start"],
                time.monotonic,
                finished=False,
                deadline=deadline,
            ),
        )
        if time.monotonic() >= deadline:
            raise RuntimeError("Late startup diagnostic result")  # noqa: TRY301 - retain diagnostic failure only
        evidence["status"] = "completed"
    except Exception as error:  # noqa: BLE001 - original rejection remains authoritative
        evidence["error"] = repr(error)[:2048]
    return evidence


def topology_child(args: argparse.Namespace) -> int:
    """Fixed stdlib-only diagnostic entry; no GUI, reference or provider dispatch."""
    script = Path(__file__).resolve()
    if (
        hashlib.sha256(script.read_bytes()).hexdigest() != args.diagnostic_harness_sha
        or hashlib.sha256(
            script.with_name("observe_process_diagnostics.py").read_bytes()
        ).hexdigest()
        != args.diagnostic_sidecar_sha
        or not math.isfinite(args.diagnostic_deadline)
        or time.monotonic() >= args.diagnostic_deadline
    ):
        raise RuntimeError("Diagnostic source identity or deadline invalid")
    journal = Journal(args.output / "calls.jsonl", args.diagnostic_deadline)
    try:
        journal.check()
        collect_topology(args.output, args.session, journal)
        return 0
    finally:
        journal.stream.close()


def validate_temporary_retirement(value: Json, profile: Json) -> None:
    """Require explicit signaled original handles and natural production cleanup."""
    retired, helper = value["witness"], value["helper"]
    expected = {r["process_id"]: r for r in profile["records"]["processes"]}
    records = retired.get("processes", [])
    if (
        retired.get("retired") is not True
        or retired.get("cleanup_errors") != []
        or len(records) != len(expected)
        or set(topology_pids([r.get("requested_pid") for r in records]))
        != set(expected)
    ):
        raise RuntimeError("Temporary retirement witness incomplete")
    for record in records:
        original = expected[record["requested_pid"]]
        if (
            record.get("signaled") is not True
            or record.get("identity_unchanged") is not True
            or record.get("retired") is not True
            or type(record.get("wait_result")) is not int
            or record.get("wait_result") != 0
            or any(
                type(record.get(k)) is not int
                for k in (
                    "process_id",
                    "creation_100ns",
                    "observed_process_id",
                    "observed_creation_100ns",
                )
            )
            or record.get("observed_process_id") != original["process_id"]
            or record.get("observed_creation_100ns") != original["creation_100ns"]
            or record.get("errors") != []
            or record.get("status") != "retired"
            or any(
                record.get(k) != original[k]
                for k in ("process_id", "creation_100ns", "image_path")
            )
        ):
            raise RuntimeError("Temporary handle retirement is not proven")
    if (
        type(helper.get("pid")) is not int
        or helper.get("pid") != profile["peer"]["pid"]
        or helper.get("cleanup_confirmed") is not True
        or helper.get("closed") is not True
        or helper.get("producer_verified") is not True
        or helper.get("termination_requested") is not False
        or type(helper.get("exit_code")) is not int
        or helper.get("exit_code") != 0
        or helper.get("remaining_pids") != []
        or helper.get("cleanup_errors") != []
        or helper.get("failure") is not None
    ):
        raise RuntimeError("Production helper did not retire naturally")


def serve_checkpoint(  # noqa: C901, PLR0913, PLR0917 - explicit fixed ownership scope
    output: Path,
    session: str,
    phase: str,
    gui: Owner,
    profile: Json,
    deadline: float,
    temporary: Json | None = None,
) -> Json | None:
    """Only existing channel-attested helper identity may extend the baseline set."""
    path = output / f"ownership-{phase}-request.json"
    if not path.exists():
        return None
    request = ownership_packet(read(path), session, "request")
    if (
        set(request) != {"phase", "deadline", "peer", "requested"}
        or request["phase"] != phase
        or request["deadline"] != deadline
    ):
        raise ValueError("Ownership checkpoint request differs")
    stamp = request["requested"]
    if (
        type(stamp) not in (int, float)
        or not math.isfinite(stamp)
        or not deadline - CASE_SECONDS <= stamp <= time.monotonic() < deadline
    ):
        raise ValueError("Ownership checkpoint request timing invalid")
    wanted = set(profile["request"]["pids"])
    if phase == "pre-observe":
        peer = ownership_packet(read(output / "peer-identity.json"), session, "peer")
        if (
            request["peer"] != peer
            or type(peer.get("pid")) is not int
            or not 0 < peer["pid"] < 2**32
            or peer["pid"] in wanted
            or peer.get("executable") != profile["request"]["expected_base"]
            or type(peer.get("protocol_version")) is not int
            or type(peer.get("peer_schema")) is not int
            or type(peer.get("credit_schema")) is not int
            or peer.get("protocol_version") != PROTOCOL_VERSION
            or peer.get("peer_schema") != PEER_SCHEMA
            or peer.get("credit_schema") != CREDIT_SCHEMA
            or not isinstance(peer.get("epoch"), str)
            or re.fullmatch(r"[0-9a-f]{32}", peer["epoch"]) is None
        ):
            raise ValueError("Temporary member is not the attested direct helper")
        wanted.update(
            temporary["roles"].values() if temporary is not None else [peer["pid"]]
        )
    elif request["peer"] is not None:
        raise ValueError("Unexpected temporary peer at baseline checkpoint")
    if phase == "pre-observe" and (
        temporary is None
        or temporary_records(temporary, request["peer"], profile) != temporary["roles"]
    ):
        raise RuntimeError("Missing pinned temporary profile")
    if phase == "post-terminal":
        if temporary is None:
            raise RuntimeError("Missing frozen temporary profile")
        value = ownership_packet(
            read(output / "temporary-retired.json"), session, "retired"
        )
        validate_temporary_retirement(value, temporary)
    begin = time.monotonic()
    if begin >= deadline:
        raise RuntimeError("Late ownership checkpoint")
    status = gui.snapshot()
    end = time.monotonic()
    if (
        end >= deadline
        or status["exit_code"] is not None
        or set(topology_pids(status["members"])) != wanted
    ):
        raise RuntimeError("Unexpected member at stable ownership checkpoint")
    ack = {
        "phase": phase,
        "deadline": deadline,
        "started": begin,
        "finished": end,
        "snapshot": status,
    }
    write_handshake(
        output / f"ownership-{phase}-ack.json",
        {"schema": 1, "session": session, "ack": ack},
    )
    return ack


def pin_preparation(
    states: Path, pid: int, journal: Journal
) -> tuple[Json, Json, ReferenceGuard]:
    """Admit telemetry before acquiring native resources, retaining slow returns."""
    fixture = latest(states)
    expected = fixture_key(fixture)
    journal.check()
    started = time.monotonic()
    journal.emit("call_start", "reference.pin")
    journal.check()
    guard = ReferenceGuard(fixture, pid)
    try:
        journal.emit("call_end", "reference.pin")
        journal.check()
        if time.monotonic() - started >= CALL_SECONDS:
            raise RuntimeError("Reference pin exceeded3s")  # noqa: TRY301 - release retained native pin
    except BaseException as primary:
        try:
            journal.cleanup("reference.pin.close", guard.close)
        except BaseException as error:  # noqa: BLE001 - preserve primary during cleanup
            primary.add_note(f"Preparation pin cleanup failed: {error!r}")
        raise
    return fixture, expected, guard


def prepare_case(  # noqa: PLR0913, PLR0917 - explicit immutable handshake inputs
    output: Path,
    states: Path,
    session: str,
    started: float,
    pid: int,
    fixture: Json,
    expected: Json,
    guard: ReferenceGuard,
    journal: Journal,
) -> Json:
    """Retain pinned preparation identity across a single metadata-only handoff."""
    native = journal.call("preparation.ready_guard", guard.check)
    if fixture_key(latest(states)) != expected:
        raise RuntimeError("Fixture changed during preparation")
    ready_time = time.monotonic()
    receipt = {
        "schema": 1,
        "session": session,
        "fixture_pid": pid,
        "executor_pid": os.getpid(),
        "ready": ready_time,
        "fixture": fixture,
        "native": native,
    }
    preparation_receipt(receipt, session, pid, started, ready_time)
    write_handshake(output / "preparation-ready.json", receipt)
    journal.check()
    handoff_path = output / "case-start.json"
    while not handoff_path.exists():
        journal.check()
        if (output / "topology-request.json").exists():
            collect_topology(output, session, journal)
            raise RuntimeError(
                "Topology recorded; original ownership rejection remains"
            )
        time.sleep(0.01)
    if (output / "topology-request.json").exists():
        raise RuntimeError("Conflicting capture and diagnostic requests")
    handoff = read(handoff_path)
    deadline = case_handoff(handoff, session, ready_time, started, time.monotonic())
    journal.deadline = deadline  # Exactly one transition, never a reset.
    journal.call("case.initial_guard", guard.check)
    if fixture_key(latest(states)) != expected:
        raise RuntimeError("Fixture changed across preparation handoff")
    return {**handoff, "ready": ready_time}


def child(args: argparse.Namespace) -> int:  # noqa: C901, PLR0915 - fixed owned executor lifecycle
    """All native references run here, never in the outer watchdog process."""
    preparation_deadline = args.preparation_start + PREPARATION_SECONDS
    absolute_deadline = args.preparation_start + OUTER_SECONDS
    journal = Journal(args.output / "calls.jsonl", preparation_deadline)
    module = journal.call("installed.imports", support)
    provenance = journal.call(
        "installed.verify", lambda: module.verify_wheel(args.wheel)
    )
    from PySide6.QtCore import (  # noqa: PLC0415 - isolated executor/runtime import
        Qt,
        QTimer,
    )
    from PySide6.QtWidgets import (  # noqa: PLC0415 - isolated executor/runtime import
        QApplication,
    )

    from verdiclip.capture.observe_protocol import (  # noqa: PLC0415 - isolated runtime import
        ObserveTarget,
    )
    from verdiclip.platform.helper_channel import (  # noqa: PLC0415 - isolated runtime import
        HelperChannel,
    )
    from verdiclip.platform.helper_runtime import (  # noqa: PLC0415 - isolated runtime import
        inspect_runtime,
    )

    setup_started = time.monotonic()
    journal.emit("call_start", "gui.setup")
    app = QApplication([])
    window = module.WitnessWindow()
    window.setWindowFlags(
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window.setGeometry(WITNESS_X, WITNESS_Y, 32, 32)
    window.show()
    heartbeat = QTimer(window)
    heartbeat.timeout.connect(window.heartbeat)
    heartbeat.start(10)
    channel = HelperChannel()
    journal.emit("call_end", "gui.setup")
    journal.check()
    if time.monotonic() - setup_started >= CALL_SECONDS:
        raise RuntimeError("GUI setup exceeded3s")
    result: Json = {
        "passed": False,
        "first_failure": None,
        "responsiveness_qualified": False,
        "case": "already-open-owned-wpf-explicit-ownership",
        "cold_startup_in_capture_latency": False,
        "preparation_start": args.preparation_start,
        "provenance": provenance,
    }
    guard = None
    generation = 0
    identity: Json = {}
    target = None
    expected: Json = {}
    before_root = b""
    before_panel = b""
    submitted = False
    baseline_pins = None
    baseline_profile: Json = {}
    temporary_pins = None
    temporary_profile: Json = {}
    consumer: RetainedConsumer | None = None
    consumer_started = 0.0
    consumer_receipt: Json = {}
    consumer_timer = QTimer(window)
    credit_transcript: CreditTranscript | None = None

    def identified(value: int, payload: bytes) -> None:
        if value != generation or identity:
            raise RuntimeError("Unexpected observe peer identity")
        identity.update(json.loads(payload))
        write_handshake(
            args.output / "peer-identity.json",
            {"schema": 1, "session": args.session, "peer": identity},
        )

    def ready(value: int) -> None:
        nonlocal submitted, temporary_pins, temporary_profile, credit_transcript
        if value != generation or submitted or target is None or guard is None:
            raise RuntimeError("Unexpected observe ready")
        journal.call("reference.before_observe", guard.check)
        if fixture_key(latest(args.states)) != expected:
            raise RuntimeError("Fixture changed before observe")
        if baseline_pins is None:
            raise RuntimeError("Missing retained baseline ownership")
        temporary_pins, temporary_profile = acquire_temporary(
            args.output, args.session, identity, baseline_profile, journal
        )
        ownership_checkpoint(
            args.output,
            args.session,
            "pre-observe",
            identity,
            baseline_pins,
            baseline_profile,
            journal,
            temporary_profile,
        )
        current = temporary_pins.observe()
        if (
            current["cleanup_errors"]
            or current["processes"] != temporary_profile["records"]["processes"]
        ):
            raise RuntimeError("Temporary identity changed before observe")
        result["temporary_profile"] = temporary_profile
        journal.call("reference.after_ownership", guard.check)
        submitted = True
        credit_transcript = CreditTranscript(
            generation, identity["epoch"], len(before_panel)
        )
        result["credit_public_events"] = credit_transcript.events
        channel.observe(1, target)

    def grant_credit(value: int, raw: bytes) -> None:
        if credit_transcript is None or result["first_failure"] is not None:
            raise RuntimeError("Unexpected credit event outside successful observation")
        credit_transcript.grant(value, raw, channel, journal)

    def terminal(value: int, payload: bytes) -> None:
        if value != generation or guard is None or target is None:
            raise RuntimeError("Unexpected observe terminal")
        receipt = json.loads(payload)
        result["terminal"] = receipt
        journal.check()
        if result["first_failure"] is not None or not terminal_matches(
            receipt,
            {**expected, "reference_input_tick": guard.initial["input_tick"]},
            hashlib.sha256(before_panel).hexdigest(),
            target.value(),
            identity,
        ):
            raise RuntimeError("Observe terminal does not match independent reference")
        nonlocal consumer, consumer_started, consumer_receipt
        if credit_transcript is None:
            raise RuntimeError("Missing independent credit proof")
        result["credit_transcript"] = credit_transcript.verify(receipt)
        frame = channel.take_frame(value, 1)
        try:
            channel.take_frame(value, 1)
        except (ValueError, RuntimeError):
            result["second_take_rejected"] = True
        else:
            raise RuntimeError("Retained frame was transferable twice")
        consumer_receipt = receipt
        consumer = RetainedConsumer(
            frame, before_panel, payload, rect(expected["rect"])
        )
        frame = None
        consumer_started = consumer.begin(journal)
        consumer_timer.start(1)

    def consumed() -> None:
        if consumer is None:
            raise RuntimeError("Missing retained consumer")
        evidence = consumer.poll(journal, consumer_started)
        if evidence is None:
            return
        consumer_timer.stop()
        result["retained_pixels"] = evidence
        receipt = consumer_receipt
        if guard is None or target is None:
            raise RuntimeError("Missing retained reference guard")
        if baseline_pins is None:
            raise RuntimeError("Missing retained baseline ownership")
        if temporary_pins is None:
            raise RuntimeError("Missing retained temporary handles")
        retired = temporary_pins.retired()
        if retired.get("retired") is not True:
            raise RuntimeError("Temporary retained handles have not retired")
        retirement = {"witness": retired, "helper": receipt["helper"]}
        validate_temporary_retirement(retirement, temporary_profile)
        result["temporary_retirement"] = retirement
        write_handshake(
            args.output / "temporary-retired.json",
            {
                "schema": 1,
                "session": args.session,
                "retired": retirement,
            },
        )
        ownership_checkpoint(
            args.output,
            args.session,
            "post-terminal",
            None,
            baseline_pins,
            baseline_profile,
            journal,
        )
        after_root, after_panel = reference(
            journal, guard, args.states, expected, args.output, "after"
        )
        if before_root != after_root or before_panel != after_panel:
            raise RuntimeError("Post-terminal independent pixels changed")
        journal.call("reference.post_terminal", guard.check)
        journal.check()
        result.update(
            passed=True,
            identity=identity,
            target=target.value(),
            reference={
                "rect": expected["rect"],
                "root": guard.initial,
                "state": expected,
                "rgb_sha256": hashlib.sha256(before_panel).hexdigest(),
                "full_root_sha256": hashlib.sha256(before_root).hexdigest(),
                "pre_post_exact": True,
            },
            generation=generation,
        )
        app.exit(0)

    def start() -> None:
        nonlocal guard, generation, target, expected
        nonlocal before_root, before_panel, baseline_pins, baseline_profile
        wait_until = preparation_deadline
        while not list(args.states.glob("[0-9][0-9][0-9][0-9].json")):
            if time.monotonic() >= wait_until:
                raise RuntimeError("Fixture telemetry startup missing")
            time.sleep(0.01)
        fixture, expected, guard = pin_preparation(
            args.states, args.fixture_pid, journal
        )
        # The witness widget is fixed outside both the selected panel and root.
        root = rect(guard.initial["rect"])
        if not (root[2] <= WITNESS_X or root[3] <= WITNESS_Y):
            raise RuntimeError("GUI witness would overlap reference root")
        reference_guard = guard
        result["witness_rect"] = journal.call(
            "reference.widget_geometry",
            lambda: reference_guard.excluded_window(int(window.winId())),
        )
        handoff, baseline_pins, baseline_profile = prepare_owned_case(
            args.output,
            args.states,
            args.session,
            args.preparation_start,
            args.fixture_pid,
            fixture,
            expected,
            guard,
            journal,
        )
        result.update(
            preparation_ready=handoff["ready"],
            case_start=handoff["case_start"],
            case_deadline=journal.deadline,
            absolute_deadline=absolute_deadline,
        )
        result["ownership_profile"] = baseline_profile
        ownership_checkpoint(
            args.output,
            args.session,
            "pre-reference",
            None,
            baseline_pins,
            baseline_profile,
            journal,
        )
        before_root, before_panel = reference(
            journal, guard, args.states, expected, args.output, "before"
        )
        selected = rect(expected["rect"])
        target = ObserveTarget(
            args.fixture_pid,
            guard.created,
            guard.hwnd,
            ((selected[0] + selected[2]) // 2, (selected[1] + selected[3]) // 2),
        )
        runtime = journal.call("installed.runtime", inspect_runtime)
        launch_started = time.monotonic()
        journal.emit("call_start", "channel.start")
        generation = channel.start(runtime)
        journal.emit("call_end", "channel.start")
        journal.check()
        if time.monotonic() - launch_started >= CALL_SECONDS:
            raise RuntimeError("Channel launch exceeded3s")

    def failed(_type: object, error: BaseException, _trace: object) -> None:
        consumer_timer.stop()
        if result["first_failure"] is None:
            result["first_failure"] = repr(error)
        write(args.output / "diagnostic.json", result)
        if generation and channel._state not in ("idle", "terminal"):
            channel.cancel()
        else:
            app.exit(1)

    consumer_timer.timeout.connect(consumed)
    channel.credit_required.connect(grant_credit)
    channel.identity.connect(identified)
    channel.ready.connect(ready)
    channel.terminal.connect(terminal)
    old_hook = sys.excepthook
    sys.excepthook = failed
    QTimer.singleShot(0, start)
    try:
        code = app.exec()
    finally:
        sys.excepthook = old_hook
        if temporary_pins is not None:
            try:
                errors = temporary_pins.close()
                result["temporary_pin_cleanup"] = errors
                if errors:
                    result["passed"] = False
                    result["first_failure"] = result["first_failure"] or repr(errors)
            except Exception as error:  # noqa: BLE001 - continue baseline cleanup
                result["passed"] = False
                result["first_failure"] = result["first_failure"] or repr(error)
        if baseline_pins is not None:
            try:
                final_pins = baseline_pins.finish()
                result["baseline_pin_cleanup"] = final_pins
                if (
                    final_pins["cleanup_errors"]
                    or final_pins["processes"]
                    != baseline_profile["records"]["processes"]
                ):
                    raise RuntimeError("Baseline pre-exit pin cleanup/identity failed")  # noqa: TRY301 - release owned resources and preserve failure
            except Exception as error:  # noqa: BLE001 - preserve first failure while releasing pins
                result["passed"] = False
                result["first_failure"] = result["first_failure"] or repr(error)
            finally:
                baseline_pins.close()
        if guard is not None:
            try:
                journal.cleanup("reference.pin.close", guard.close)
            except Exception as error:  # noqa: BLE001 - preserve first failure
                result["passed"] = False
                result["first_failure"] = result["first_failure"] or repr(error)
        if time.monotonic() >= journal.deadline:
            result["passed"] = False
            result["first_failure"] = result["first_failure"] or "Case deadline expired"
        result.update(heartbeat_count=window.beats, paint_count=window.paints)
        write(args.output / "result.json", result)
        journal.stream.close()
    return 0 if code == 0 and result.get("passed") is True else 1


def validate_calls(  # noqa: C901, PLR0912, PLR0913 - explicit watchdog event parser
    path: Path,
    started: float,
    clock: Callable[[], float],
    *,
    finished: bool,
    deadline: float | None = None,
    diagnostic: bool = False,
) -> None:
    """Externally reject missing startup, blocked calls and late completed calls."""
    until = started + CASE_SECONDS if deadline is None else deadline
    now = clock()
    if now >= until:
        raise RuntimeError("External observe case deadline")
    if not path.exists():
        now = clock()
        if now >= until:
            raise RuntimeError("External observe case deadline")
        if finished or now - started >= CALL_SECONDS:
            raise RuntimeError("Observe executor startup missing")
        return
    with path.open("rb") as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise RuntimeError("Observe call log oversized")
    records = [
        json.loads(line)
        for line in data.splitlines(keepends=True)
        if line.endswith(b"\n")
    ]
    now = clock()
    if now >= until:
        raise RuntimeError("External observe case deadline")
    if not records or len(records) > MAX_CALLS:
        raise RuntimeError("Observe call log empty/oversized")
    pending = None
    previous = started
    for i, record in enumerate(records):
        timestamp = record["monotonic"]
        if (
            type(timestamp) not in (int, float)
            or not math.isfinite(timestamp)
            or not previous <= timestamp <= now
        ):
            raise RuntimeError("Invalid reference event time")
        previous = timestamp
        if i == 0:
            if record["event"] != "process" or timestamp - started >= CALL_SECONDS:
                raise RuntimeError("Observe process startup late")
        elif record["event"] == "call_start" and pending is None:
            pending = record
        elif (
            (
                record["event"] == "call_end"
                or (
                    diagnostic
                    and record["event"] == "call_error"
                    and str(record["name"]).startswith("topology.")
                )
            )
            and pending
            and pending["name"] == record["name"]
        ):
            if timestamp - pending["monotonic"] >= CALL_SECONDS:
                raise RuntimeError("Reference native call late")
            pending = None
        else:
            raise RuntimeError(f"Reference native call failure/pairing: {record}")
    now = clock()
    if now >= until:
        raise RuntimeError("External observe case deadline")
    if pending and (finished or now - pending["monotonic"] >= CALL_SECONDS):
        raise RuntimeError("Reference native call incomplete/blocked")
    if finished and not data.endswith(b"\n"):
        raise RuntimeError("Partial final reference event")


class Owner(Protocol):
    """Existing external ownership interface used by bounded retirement."""

    api: Any
    job: int | None

    def snapshot(self) -> dict[str, object]:
        """Read process exit and exact job membership."""
        ...

    def close(self) -> list[str]:
        """Release owned handles without inventing successful retirement."""
        ...


def release_owner(owner: Owner, deadline: float) -> Json:
    """Cleanup is nested in the original case; forced retirement cannot pass."""
    until = min(deadline, time.monotonic() + CLEANUP_SECONDS)
    result: Json = {"confirmed": False, "forced": False, "errors": []}
    try:
        state = owner.snapshot()
        if state["exit_code"] is None or state["members"]:
            result["forced"] = True
            owner.api.terminate(owner.job)
        while time.monotonic() < until:
            state = owner.snapshot()
            if time.monotonic() >= until:
                break
            if state["exit_code"] is not None and not state["members"]:
                result["confirmed"] = True
                break
            time.sleep(0.01)
    except Exception as error:  # noqa: BLE001 - close every handle
        result["errors"].append(repr(error))
    finally:
        result["errors"].extend(owner.close())
        if result["errors"] or time.monotonic() >= until:
            result["confirmed"] = False
    return result


def outer(args: argparse.Namespace) -> int:  # noqa: C901, PLR0912, PLR0915 - owned lifetime
    """No MSS/UIA/reference read here: only owned process polls and bounded files."""
    module = support()
    provenance = module.verify_wheel(args.wheel)
    from verdiclip.platform.owned_helper import (  # noqa: PLC0415 - isolated runtime import
        HelperCommand,
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    states = output / "states"
    states.mkdir()
    executor = output / "executor"
    executor.mkdir()
    result: Json = {
        "passed": False,
        "first_failure": None,
        "provenance": provenance,
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "candidate_head": os.environ.get("QUALIFICATION_HEAD"),
    }
    owners = {}
    started = time.monotonic()
    preparation_deadline = started + PREPARATION_SECONDS
    absolute_deadline = started + OUTER_SECONDS
    deadline = preparation_deadline
    session = uuid.uuid4().hex
    case_start = None
    profile: Json = {}
    checkpoints: list[Json] = []
    peer_pid = None
    provisional_members: set[int] = set()
    temporary_profile: Json | None = None
    expected_launch = str(Path(sys.executable).resolve())
    result.update(
        contract="already-open-panel-explicit-ownership-v2",
        preparation_start=started,
        preparation_deadline=preparation_deadline,
        absolute_deadline=absolute_deadline,
        cold_startup_in_capture_latency=False,
        session=session,
    )
    try:
        fixture = module.ExternalOwner()
        owners["fixture"] = fixture
        fixture.start(
            HelperCommand(str(args.fixture), (str(states), "observe-only"), str(output))
        )
        if fixture.process is None:
            raise RuntimeError("Fixture process not retained")  # noqa: TRY301 - preserve first failure
        gui = module.ExternalOwner()
        owners["gui"] = gui
        gui.start(
            HelperCommand(
                expected_launch,
                (
                    "-I",
                    str(Path(__file__).resolve()),
                    "--child",
                    "--wheel",
                    str(args.wheel),
                    "--output",
                    str(executor),
                    "--states",
                    str(states),
                    "--fixture-pid",
                    str(fixture.process.pid),
                    "--preparation-start",
                    str(started),
                    "--session",
                    session,
                ),
                str(output),
            )
        )
        while time.monotonic() < deadline:
            snapshot_started = time.monotonic()
            status = gui.snapshot()
            snapshot_finished = time.monotonic()
            validate_calls(
                executor / "calls.jsonl",
                started,
                time.monotonic,
                finished=status["exit_code"] is not None,
                deadline=deadline,
            )
            ready_path = executor / "preparation-ready.json"
            if (
                case_start is None
                and ready_path.exists()
                and status["exit_code"] is None
            ):
                prepared = preparation_receipt(
                    read(ready_path),
                    session,
                    fixture.process.pid,
                    started,
                    time.monotonic(),
                )
                result["preparation_ownership"] = {
                    "session": session,
                    "ready": prepared["ready"],
                    "executor_pid": prepared["executor_pid"],
                    "gui_launch_pid": None if gui.process is None else gui.process.pid,
                    "gui_snapshot": status,
                    "gui_snapshot_started": snapshot_started,
                    "gui_snapshot_finished": snapshot_finished,
                }
                if gui.process is None:
                    raise RuntimeError("Missing retained executor launch")  # noqa: TRY301 - release owned resources and preserve failure
                profile = qualify_ownership(
                    executor,
                    session,
                    status,
                    snapshot_finished,
                    gui,
                    started,
                    deadline,
                    gui.process.pid,
                    prepared["executor_pid"],
                    expected_launch,
                    provenance["executable"],
                )
                result["ownership_profile"] = profile
                fixture_state = fixture.snapshot()
                result["preparation_ownership"].update(
                    fixture_launch_pid=fixture.process.pid,
                    fixture_snapshot=fixture_state,
                )
                if fixture_state["exit_code"] is not None or fixture_state[
                    "members"
                ] != [fixture.process.pid]:
                    raise RuntimeError("Prepared fixture no longer solely owned/alive")  # noqa: TRY301 - preserve first failure
                case_start = time.monotonic()
                if case_start >= preparation_deadline:
                    raise RuntimeError("Late preparation admission")  # noqa: TRY301 - preserve first failure
                deadline = min(case_start + CASE_SECONDS, absolute_deadline)
                handoff = {
                    "schema": 1,
                    "session": session,
                    "case_start": case_start,
                    "deadline": deadline,
                }
                case_handoff(handoff, session, prepared["ready"], started, case_start)
                write_handshake(executor / "case-start.json", handoff)
                if time.monotonic() >= preparation_deadline:
                    raise RuntimeError("Late preparation handoff publication")  # noqa: TRY301 - preserve first failure
                result.update(
                    preparation_ready=prepared["ready"],
                    case_start=case_start,
                    case_deadline=deadline,
                    preparation=prepared,
                )
            if case_start is not None:
                if status["exit_code"] is None:
                    context = {
                        "schema": 1,
                        "session": session,
                        "snapshot": status,
                        "snapshot_started": snapshot_started,
                        "snapshot_finished": snapshot_finished,
                        "baseline": list(profile["request"]["pids"]),
                        "provisional_before": sorted(provisional_members),
                        "provisional_union": sorted(
                            provisional_members
                            | (
                                set(topology_pids(status["members"]))
                                - set(profile["request"]["pids"])
                            )
                        ),
                        "peer_pid": peer_pid,
                        "completed_phases": [item["phase"] for item in checkpoints],
                        "preparation_start": started,
                        "case_deadline": deadline,
                        "absolute_deadline": absolute_deadline,
                    }
                    try:
                        track_temporary_member(
                            set(topology_pids(status["members"])),
                            set(profile["request"]["pids"]),
                            provisional_members,
                            peer_pid,
                            retiring=len(checkpoints) == len(OWNERSHIP_PHASES),
                            admitted=set(temporary_profile["roles"].values())
                            if temporary_profile is not None
                            else None,
                        )
                    except Exception as error:
                        context["failure_time"] = time.monotonic()
                        retain_membership_failure(output, result, context, error)
                        # Optional files are later observations, not guard inputs.
                        phase_evidence = {}
                        for name in ("peer-identity", "ownership-pre-observe-request"):
                            try:
                                path = executor / f"{name}.json"
                                phase_evidence[name] = (
                                    read(path) if path.exists() else None
                                )
                            except Exception as read_error:  # noqa: BLE001 - retain original rejection
                                phase_evidence[name] = {
                                    "error": repr(read_error)[:2048]
                                }
                        result["failure_phase_evidence"] = phase_evidence
                        try:
                            write_handshake(
                                output / "failure-phase-evidence.json",
                                {
                                    "session": session,
                                    "observed": time.monotonic(),
                                    "evidence": phase_evidence,
                                },
                            )
                        except Exception as persistence_error:  # noqa: BLE001 - preserve rejection
                            result["phase_persistence_error"] = repr(persistence_error)[
                                :2048
                            ]
                        result["startup_diagnostics"] = diagnose_startup_failure(
                            output,
                            executor,
                            session,
                            context,
                            gui,
                            module,
                            owners,
                            provenance,
                            args.wheel,
                            deadline,
                            absolute_deadline,
                        )
                        raise
                if len(checkpoints) < len(OWNERSHIP_PHASES):
                    phase = OWNERSHIP_PHASES[len(checkpoints)]
                    if phase == "pre-observe" and temporary_profile is None:
                        temporary_profile = serve_temporary(
                            executor,
                            session,
                            gui,
                            profile,
                            provisional_members,
                            deadline,
                        )
                        if temporary_profile is not None:
                            peer_pid = temporary_profile["peer"]["pid"]
                            result["temporary_profile"] = temporary_profile
                    if (
                        phase == "pre-observe"
                        and (executor / "ownership-pre-observe-request.json").exists()
                        and (
                            temporary_profile is None
                            or provisional_members
                            - set(temporary_profile["roles"].values())
                        )
                    ):
                        raise RuntimeError(  # noqa: TRY301 - no pixels authorized
                            "A provisional member was not the attested helper"
                        )
                    ack = serve_checkpoint(
                        executor,
                        session,
                        phase,
                        gui,
                        profile,
                        deadline,
                        temporary_profile,
                    )
                    if ack is not None:
                        checkpoints.append(ack)
                        result["ownership_checkpoints"] = checkpoints
                        if phase == "pre-observe":
                            peer = ownership_packet(
                                read(executor / "peer-identity.json"), session, "peer"
                            )
                            peer_pid = peer["pid"]
                            result["attested_peer"] = peer
            if status["exit_code"] is not None and not status["members"]:
                value = read(executor / "result.json")
                result.update(child=value, gui=status)
                if (
                    case_start is None
                    or len(checkpoints) != len(OWNERSHIP_PHASES)
                    or value.get("temporary_profile") != temporary_profile
                    or value.get("temporary_pin_cleanup") != []
                    or value.get("ownership_profile") != profile
                    or not isinstance(value.get("baseline_pin_cleanup"), dict)
                    or value["baseline_pin_cleanup"].get("cleanup_errors") != []
                    or value["baseline_pin_cleanup"].get("processes")
                    != profile["records"]["processes"]
                    or status["exit_code"] != 0
                    or value.get("passed") is not True
                    or value.get("case_start") != case_start
                    or value.get("case_deadline") != deadline
                ):
                    raise RuntimeError(  # noqa: TRY301 - preserve first failure
                        f"Observe executor failed: {value.get('first_failure')}"
                    )
                if time.monotonic() >= deadline:
                    raise RuntimeError("Late observe receipt")  # noqa: TRY301 - preserve first failure
                (states / "stop").write_bytes(b"")
                while time.monotonic() < deadline:
                    fs = fixture.snapshot()
                    if fs["exit_code"] is not None and not fs["members"]:
                        result["fixture_exit"] = fs
                        result["passed"] = (
                            fs["exit_code"] == 0 and time.monotonic() < deadline
                        )
                        break
                    time.sleep(0.01)
                break
            time.sleep(0.01)
        if not result["passed"]:
            raise RuntimeError("No timely natural successful observation")  # noqa: TRY301 - preserve first failure
    except Exception as error:  # noqa: BLE001 - first failure before fallback
        result["first_failure"] = repr(error)
    finally:
        result["cleanup"] = {
            name: release_owner(
                owner,
                min(deadline, result["startup_diagnostics"]["deadline"])
                if name == "startup-diagnostic"
                else deadline,
            )
            for name, owner in reversed(tuple(owners.items()))
        }
        if any(not x["confirmed"] or x["forced"] for x in result["cleanup"].values()):
            result["passed"] = False
        result["elapsed_seconds"] = time.monotonic() - started
        write(output / "receipt.json", result)
    return int(not result["passed"])


def main() -> int:
    """Fixed success case only; no entrypoint selectors or test launch overrides."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--topology-child", action="store_true")
    parser.add_argument("--diagnostic-deadline", type=float)
    parser.add_argument("--diagnostic-harness-sha")
    parser.add_argument("--diagnostic-sidecar-sha")
    parser.add_argument("--states", type=Path)
    parser.add_argument("--fixture-pid", type=int)
    parser.add_argument("--preparation-start", type=float)
    parser.add_argument("--session")
    args = parser.parse_args()
    if (
        sys.platform != "win32"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or re.fullmatch(r"[0-9a-f]{40}", os.environ.get("QUALIFICATION_HEAD", ""))
        is None
    ):
        parser.error("Owned observe proof requires exact-head disposable Windows CI")
    args.wheel = args.wheel.resolve(strict=True)
    args.output = args.output.resolve()
    if args.topology_child:
        try:
            return topology_child(args)
        except Exception as error:
            try:
                write(
                    args.output / "topology-fatal.json", {"error": repr(error)[:2048]}
                )
            except Exception as persistence_error:  # noqa: BLE001 - preserve primary
                error.add_note(
                    f"Diagnostic error retention failed: {persistence_error!r}"
                )
            raise
    if args.child:
        try:
            return child(args)
        except Exception as error:  # noqa: BLE001 - preserve pre-event-loop failures
            write(args.output / "fatal.json", {"first_failure": repr(error)})
            return 1
    if args.fixture is None:
        parser.error("--fixture required for outer owner")
    args.fixture = args.fixture.resolve(strict=True)
    return outer(args)


if __name__ == "__main__":
    raise SystemExit(main())
