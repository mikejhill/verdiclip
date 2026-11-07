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
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

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
    write_handshake(output / "topology-request.json", request)
    result: Json = {"request": request, "original_snapshot": status}
    acknowledged = False
    while time.monotonic() < deadline:
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
        "case": "already-open-owned-wpf-single-observation",
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

    def identified(value: int, payload: bytes) -> None:
        if value != generation or identity:
            raise RuntimeError("Unexpected observe peer identity")
        identity.update(json.loads(payload))

    def ready(value: int) -> None:
        nonlocal submitted
        if value != generation or submitted or target is None or guard is None:
            raise RuntimeError("Unexpected observe ready")
        journal.call("reference.before_observe", guard.check)
        if fixture_key(latest(args.states)) != expected:
            raise RuntimeError("Fixture changed before observe")
        submitted = True
        channel.observe(1, target)

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
            generation=value,
        )
        app.exit(0)

    def start() -> None:
        nonlocal guard, generation, target, expected, before_root, before_panel
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
        handoff = prepare_case(
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
        if result["first_failure"] is None:
            result["first_failure"] = repr(error)
        write(args.output / "diagnostic.json", result)
        if generation and channel._state not in ("idle", "terminal"):
            channel.cancel()
        else:
            app.exit(1)

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
    result.update(
        contract="already-open-panel-v1",
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
                str(Path(sys.executable).resolve()),
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
                if gui.process is None or status["members"] != [gui.process.pid]:
                    original = RuntimeError(
                        "Prepared executor no longer solely owned/alive"
                    )
                    result["first_failure"] = repr(original)
                    try:
                        result["topology"] = diagnose_topology(
                            executor,
                            session,
                            status,
                            snapshot_finished,
                            gui,
                            started,
                            deadline,
                        )
                    except Exception as diagnostic_error:  # noqa: BLE001 - preserve original rejection
                        result["topology_error"] = repr(diagnostic_error)[:2048]
                    raise original  # noqa: TRY301 - unchanged primary ownership rejection
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
            if status["exit_code"] is not None and not status["members"]:
                value = read(executor / "result.json")
                result.update(child=value, gui=status)
                if (
                    case_start is None
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
            name: release_owner(owner, deadline)
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
