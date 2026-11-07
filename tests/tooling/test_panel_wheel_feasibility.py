"""Synthetic contracts for the isolated wheel experiment, not native proof."""

import argparse
import ctypes as c
from dataclasses import replace
from pathlib import Path
from typing import cast, override

import mss
import pytest
from scripts import panel_wheel_feasibility as supervisor
from scripts import panel_wheel_probe as probe
from scripts.panel_uia import PanelUia, ScrollState, Structure
from scripts.panel_uia_probe import NativeGuard, Probe


class FakeApi:
    """Verify FakeApi."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []
        self.failure = False
        self.blocked = False

    def check(self) -> None:
        """Verify check."""
        if self.blocked:
            raise RuntimeError("guard changed")

    def send(self, wparam: int, lparam: int) -> None:
        """Verify send."""
        self.sent.append((wparam, lparam))
        if self.failure:
            raise OSError("uncertain send")


@pytest.mark.parametrize(("x", "y"), [(-32768, -1), (32767, 32767), (0, 0)])
@pytest.mark.parametrize("delta", [-120, 120])
def test_wheel_packing(x: int, y: int, delta: int) -> None:
    """Verify wheel packing."""
    wparam, lparam = probe.wheel_parameters(x, y, delta)
    assert c.c_int16(wparam >> 16).value == delta
    assert wparam & 0xFFFF == 0
    assert c.c_int16(lparam).value == x
    assert c.c_int16(lparam >> 16).value == y


@pytest.mark.parametrize(
    "values",
    [
        (32768, 0, 120),
        (-32769, 0, 120),
        (0, 32768, 120),
        (True, 0, 120),
        (0, 0, 1),
        (0, 0, float("nan")),
    ],
)
def test_invalid_wheel_parameters(values: tuple[int, int, int]) -> None:
    """Verify invalid wheel parameters."""
    with pytest.raises(ValueError, match=r".+"):
        probe.wheel_parameters(*values)


def test_no_retry_until_settled_acknowledgement() -> None:
    """Verify no retry until settled acknowledgement."""
    api = FakeApi()
    writer = probe.WheelWriter(api, -10, 20)
    writer.step(-120)
    assert writer.uncertain
    with pytest.raises(RuntimeError, match="prohibited"):
        writer.step(-120)
    writer.acknowledge()
    writer.step(120)
    assert len(api.sent) == 2


def test_uncertain_and_guard_failures_never_retry() -> None:
    """Verify uncertain and guard failures never retry."""
    api = FakeApi()
    writer = probe.WheelWriter(api, 0, 0)
    api.blocked = True
    with pytest.raises(RuntimeError, match="guard"):
        writer.step(-120)
    assert not api.sent
    api.blocked = False
    api.failure = True
    with pytest.raises(OSError, match="uncertain"):
        writer.step(-120)
    assert writer.uncertain
    with pytest.raises(RuntimeError, match="prohibited"):
        writer.step(-120)
    assert len(api.sent) == 1
    with pytest.raises(RuntimeError, match="cannot be acknowledged"):
        writer.acknowledge()


@pytest.mark.parametrize("elapsed", [3.0, 3.1])
def test_completed_send_deadline(
    elapsed: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify completed send deadline."""
    now = [0.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    api = FakeApi()
    writer = probe.WheelWriter(api, 0, 0)

    def late(_wparam: int, _lparam: int) -> None:
        """Verify late."""
        now[0] = elapsed

    monkeypatch.setattr(api, "send", late)
    with pytest.raises(RuntimeError, match="after deadline"):
        writer.step(-120)
    assert writer.uncertain


def test_write_count_and_absolute_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify write count and absolute deadline."""
    now = [0.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    api = FakeApi()
    writer = probe.WheelWriter(api, 0, 0)
    for _ in range(probe.MAX_WRITES):
        writer.step(-120)
        writer.acknowledge()
    with pytest.raises(RuntimeError, match="prohibited"):
        writer.step(-120)
    writer = probe.WheelWriter(api, 0, 0)
    now[0] = 90
    with pytest.raises(RuntimeError, match="deadline"):
        writer.step(-120)
    with pytest.raises(RuntimeError, match="deadline"):
        writer.acknowledge()


def test_deadline_rechecked_after_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify deadline rechecked after guard."""
    now = [0.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    api = FakeApi()
    writer = probe.WheelWriter(api, 0, 0)
    monkeypatch.setattr(api, "check", lambda: now.__setitem__(0, 90.0))
    with pytest.raises(RuntimeError, match="before send"):
        writer.step(-120)
    assert not api.sent


STATE = ScrollState(
    10, 10, 40, 20, horizontal_scrollable=True, vertical_scrollable=True
)


class SequenceProbe(probe.WheelProbe):
    """Verify SequenceProbe."""

    def __init__(self, offsets: list[float], mode: str = "interior") -> None:
        self.initial = self.last = STATE
        self.report = {}
        self.args = argparse.Namespace(mode=mode)
        self.offsets = iter(offsets)
        self.deltas: list[int] = []

    @override
    def stable(self, source: mss.MSS) -> tuple[ScrollState, bytes]:
        """Verify stable."""
        del source
        assert self.last is not None
        return self.last, str(self.last.vertical).encode()

    @override
    def move(self, source: mss.MSS, delta: int) -> tuple[ScrollState, bytes]:
        """Verify move."""
        self.deltas.append(delta)
        self.last = replace(STATE, vertical=next(self.offsets))
        return self.stable(source)


def test_interior_exact_roundtrip() -> None:
    """Verify interior exact roundtrip."""
    candidate = SequenceProbe([20, 10])
    candidate.sequence(cast("mss.MSS", object()))
    assert candidate.report["restored"]
    assert candidate.deltas == [-120, 120]


@pytest.mark.parametrize("offsets", [[10], [20, 9]])
def test_interior_rejects_no_progress_or_bad_restore(offsets: list[float]) -> None:
    """Verify interior rejects no progress or bad restore."""
    candidate = SequenceProbe(offsets)
    with pytest.raises(RuntimeError, match=r"did not move|not exact"):
        candidate.sequence(cast("mss.MSS", object()))
    candidate.restore_after_failure()
    assert candidate.report["restoration_prohibited"]
    assert not candidate.report.get("restored")


def test_endpoint_restoration_clipping_rejected() -> None:
    """Verify endpoint restoration clipping rejected."""
    candidate = SequenceProbe([20, 10, 50, 100, 100, 50, 0], "endpoint")
    with pytest.raises(RuntimeError, match="overshot"):
        candidate.sequence(cast("mss.MSS", object()))
    assert candidate.report["endpoint_repeat_exact"]
    assert not candidate.report.get("restored")


def sample(
    sequence: int,
    vertical: float,
    outer: int = 0,
    sibling: int = 0,
    horizontal: int = 47,
) -> supervisor.Json:
    """Verify sample."""
    return {
        "sequence": sequence,
        "state": {
            "target": {
                "vertical": vertical,
                "horizontal": horizontal,
                "extent_height": 1200,
                "viewport_height": 200,
            },
            "outer": {"vertical": outer},
            "sibling": {"vertical": sibling},
        },
    }


def test_independent_oracle() -> None:
    """Verify independent oracle."""
    states = [sample(0, 100), sample(1, 200), sample(2, 100)]
    observations = [{"state": {"vertical": value}} for value in (10, 20, 10)]
    assert supervisor.audit(states, observations)["exact_restoration"]


@pytest.mark.parametrize(
    "changed",
    [sample(1, 200, outer=3), sample(1, 200, sibling=3), sample(1, 200, horizontal=46)],
)
def test_independent_propagation_and_horizontal_rejection(
    changed: supervisor.Json,
) -> None:
    """Verify independent propagation and horizontal rejection."""
    with pytest.raises(ValueError, match=r"propagated|horizontal"):
        supervisor.audit(
            [sample(0, 100), changed, sample(2, 100)], [{"state": {"vertical": 10}}]
        )


def test_oracle_order_and_exact_restore() -> None:
    """Verify oracle order and exact restore."""
    with pytest.raises(ValueError, match="disagrees"):
        supervisor.audit(
            [sample(0, 100), sample(1, 200), sample(2, 100)],
            [{"state": {"vertical": v}} for v in (20, 10, 20)],
        )
    with pytest.raises(ValueError, match="restoration"):
        supervisor.audit(
            [sample(0, 100), sample(1, 200)], [{"state": {"vertical": 10}}]
        )


@pytest.mark.parametrize("elapsed", [3.0, 3.1])
def test_supervisor_rejects_late_completed_call(elapsed: float) -> None:
    """Verify supervisor rejects late completed call."""
    records = [
        {"event": "process", "monotonic": 0.0},
        {"event": "call_start", "monotonic": 0.1, "name": "send"},
        {"event": "call_end", "monotonic": 0.1 + elapsed, "name": "send"},
    ]
    with pytest.raises(RuntimeError, match="completed call"):
        supervisor.validate_log(records, 0, 10, finished=True)


def test_supervisor_rejects_incomplete_call() -> None:
    """Verify supervisor rejects incomplete call."""
    records = [
        {"event": "process", "monotonic": 0.0},
        {"event": "call_start", "monotonic": 0.1, "name": "send"},
    ]
    with pytest.raises(RuntimeError, match="incomplete"):
        supervisor.validate_log(records, 0, 1, finished=True)


def test_owned_fixture_only_configuration() -> None:
    """Verify owned fixture only configuration."""
    assert "target.scrollTop = 0;" in supervisor.fixture_page("top")
    assert "target.scrollTop = 119;" in supervisor.fixture_page("middle")
    with pytest.raises(ValueError, match="Unexpected"):
        supervisor.fixture_page("arbitrary")


@pytest.mark.parametrize("blocked_key", [0, *probe._MODIFIERS])
def test_native_modifier_gate(
    blocked_key: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every held modifier/button prevents the fixed HWND operation."""
    from types import SimpleNamespace  # noqa: PLC0415 - fake native boundary

    native = object.__new__(probe.NativeWheel)
    native.hwnd, native.pid, native.cursor = 123, 456, (10, 20)

    def pid(_hwnd: int, pointer: object) -> int:
        c.cast(cast("c.c_void_p", pointer), c.POINTER(c.c_uint32)).contents.value = 456
        return 1

    user = SimpleNamespace(
        WindowFromPoint=lambda _point: 123,
        GetWindowThreadProcessId=pid,
        GetAsyncKeyState=lambda key: 0x8000 if key == blocked_key else 0,
    )
    native.user = cast("c.WinDLL", user)
    native.guard = cast(
        "probe.NativeGuard",
        SimpleNamespace(check=lambda: None, point=probe.Point(10, 20)),
    )
    monkeypatch.setattr(native, "cursor_position", lambda: (10, 20))
    if blocked_key:
        with pytest.raises(RuntimeError, match="modifier guard"):
            native.check()
    else:
        native.check()


def test_native_send_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly one bounded message carries no pointer moves or modifier bits."""
    from types import SimpleNamespace  # noqa: PLC0415 - fake native boundary

    calls: list[tuple[object, ...]] = []
    native = object.__new__(probe.NativeWheel)
    native.hwnd = 123

    def send(*args: object) -> int:
        calls.append(args)
        return 1

    native.user = cast("c.WinDLL", SimpleNamespace(SendMessageTimeoutW=send))
    native.send(*probe.wheel_parameters(-10, 20, -120))
    assert len(calls) == 1
    assert calls[0][:2] == (123, 0x020A)
    assert calls[0][4:6] == (0x23, 1000)
    monkeypatch.setattr(native.user, "SendMessageTimeoutW", lambda *_args: 0)
    with pytest.raises(OSError, match="uncertain"):
        native.send(*probe.wheel_parameters(-10, 20, -120))


@pytest.mark.parametrize("elapsed", [89.9, 90.0, 90.1])
@pytest.mark.parametrize("phase", ["read", "admit"])
def test_post_read_and_final_admission_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, elapsed: float, phase: str
) -> None:
    """A late file read or validation cannot pass the completed helper gate."""
    now = [0.0]
    records = [
        {"event": "process", "monotonic": 0.0},
        {"event": "wheel_observation", "monotonic": 0.1},
    ]

    class Job:
        def start(self, _command: list[str], _cwd: Path) -> tuple[int, int]:
            """Return an owned fake process without native launch."""
            return 1, 2

        def wait(self, _handle: int, _timeout: int) -> int:
            """Complete before the evidence read advances time."""
            return 0

    def read(_path: Path) -> list[supervisor.Json]:
        if phase == "read":
            now[0] = elapsed
            records[-1]["monotonic"] = elapsed
        return records

    original = supervisor.validate_log

    def validate(
        records: list[supervisor.Json], start: float, current: float, *, finished: bool
    ) -> None:
        original(records, start, current, finished=finished)
        if phase == "admit":
            now[0] = elapsed

    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(supervisor, "events", read)
    monkeypatch.setattr(supervisor, "validate_log", validate)
    monkeypatch.setattr(supervisor.os, "environ", {})
    if phase == "admit":
        records[-1]["monotonic"] = 0.0
    if elapsed < 90:
        assert (
            supervisor.launch_probe(
                cast("supervisor.OwnedJob", Job()),
                tmp_path / "fixture.json",
                tmp_path,
                "interior",
            )["exit_code"]
            == 0
        )
    else:
        with pytest.raises(RuntimeError, match="total deadline"):
            supervisor.launch_probe(
                cast("supervisor.OwnedJob", Job()),
                tmp_path / "fixture.json",
                tmp_path,
                "interior",
            )


@pytest.mark.parametrize("event", ["cleanup_error", "first_failure", "call_error"])
def test_error_events_disqualify_completion(event: str) -> None:
    """A stale completion flag cannot erase failure evidence."""
    with pytest.raises(RuntimeError, match="failure event"):
        supervisor.validate_log(
            [{"event": "process", "monotonic": 0}, {"event": event, "monotonic": 1}],
            0,
            2,
            finished=True,
        )
    with pytest.raises(RuntimeError, match="Wheel probe failed"):
        supervisor.admit(
            {"helper": {"exit_code": 0}},
            {
                "wheel_sequence_completed": True,
                "restored": True,
                "first_failure": "original",
            },
        )


def test_endpoint_geometry_change_prevents_inverse_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual stable guard rejects ancestor-induced movement before any inverse step."""
    from types import SimpleNamespace  # noqa: PLC0415 - isolated native seam

    from scripts.panel_uia import Structure  # noqa: PLC0415 - owned fixture model

    candidate = object.__new__(probe.WheelProbe)
    identity = Structure(1, 2, 3, "Chrome", (0, 0, 100, 100), offscreen=False)
    candidate.identity = identity
    candidate.initial = STATE
    candidate.last = replace(STATE, vertical=100)
    candidate.report = {}
    candidate.uia = cast(
        "probe.PanelUia",
        SimpleNamespace(
            target=object(),
            structure=lambda _target: replace(identity, rect=(0, -10, 100, 90)),
            read_state=lambda: candidate.last,
        ),
    )
    api = FakeApi()
    candidate.wheel = probe.WheelWriter(api, 10, 10)
    events: list[dict[str, object]] = []
    monkeypatch.setattr(candidate, "emit", events.append)
    monkeypatch.setattr(candidate, "validate", lambda: None)
    source = cast("mss.MSS", object())
    original_stable = candidate.stable
    calls = [0]

    def stable(source: mss.MSS) -> tuple[ScrollState, bytes]:
        calls[0] += 1
        if calls[0] == 1:
            return replace(STATE, vertical=100), b"endpoint"
        return original_stable(source)

    monkeypatch.setattr(candidate, "stable", stable)
    with pytest.raises(RuntimeError, match="geometry"):
        candidate.endpoint(source, (STATE, b"initial"))
    assert len(api.sent) == 1
    assert c.c_int16(api.sent[0][0] >> 16).value == -120
    assert candidate.wheel.uncertain


@pytest.mark.parametrize("primary_failure", [False, True])
def test_mss_lifecycle_logged_and_first_failure_preserved(
    monkeypatch: pytest.MonkeyPatch, *, primary_failure: bool
) -> None:
    """MSS setup, monitor enumeration and failing release all retain timed events."""
    from scripts.panel_uia import Structure  # noqa: PLC0415 - fixture data

    class Source:
        @property
        def monitors(self) -> list[dict[str, int]]:
            """Provide one bounded synthetic monitor."""
            return [{}, {"left": 0, "top": 0, "width": 100, "height": 100}]

        def close(self) -> None:
            """Reproduce a native cleanup exception."""
            raise OSError("cleanup failed")

    candidate = object.__new__(probe.WheelProbe)
    candidate.identity = Structure(1, 2, 3, "Chrome", (0, 0, 50, 50), offscreen=False)
    candidate.wheel = probe.WheelWriter(FakeApi(), 10, 10)
    candidate.report = {}
    records: list[dict[str, object]] = []
    monkeypatch.setattr(candidate, "emit", records.append)
    monkeypatch.setattr(probe.mss, "MSS", Source)

    def sequence(_source: mss.MSS) -> None:
        if primary_failure:
            raise RuntimeError("first operation failed")

    monkeypatch.setattr(candidate, "sequence", sequence)
    expected = RuntimeError if primary_failure else OSError
    with pytest.raises(expected, match=r"first operation|cleanup failed"):
        candidate.capture_sequence()
    assert [
        record["name"] for record in records if record["event"] == "call_start"
    ] == ["wheel.mss.create", "wheel.mss.monitors", "wheel.mss.close"]
    assert records[-1]["event"] == "cleanup_error"
    assert "cleanup failed" in str(candidate.report["cleanup_error"])


@pytest.mark.parametrize(
    "operation", ["wheel.mss.create", "wheel.mss.monitors", "wheel.mss.close"]
)
def test_late_completed_mss_calls_fail(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lifecycle call completing exactly at3s remains a timed-out failure."""
    candidate = object.__new__(probe.WheelProbe)
    records: list[dict[str, object]] = []
    now = [0.0]
    monkeypatch.setattr(candidate, "emit", records.append)
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])

    def late() -> None:
        now[0] = 3.0

    with pytest.raises(RuntimeError, match="after deadline"):
        candidate.native_call(operation, late)
    assert records == [
        {"event": "call_start", "name": operation},
        {"event": "call_end", "name": operation},
    ]


@pytest.mark.parametrize("elapsed", [3.0, 3.1])
def test_slow_mss_creation_retained_and_closed(
    monkeypatch: pytest.MonkeyPatch, elapsed: float
) -> None:
    """A successfully returned but late native owner closes once without capture."""
    from scripts.panel_uia import Structure  # noqa: PLC0415 - fixture data

    now = [0.0]
    closes: list[str] = []
    sequences: list[str] = []
    records: list[dict[str, object]] = []

    class Source:
        def __init__(self) -> None:
            now[0] = elapsed

        def close(self) -> None:
            """Track explicit release of the successfully allocated owner."""
            closes.append("closed")

    candidate = object.__new__(probe.WheelProbe)
    candidate.identity = Structure(1, 2, 3, "Chrome", (0, 0, 50, 50), offscreen=False)
    candidate.wheel = probe.WheelWriter(FakeApi(), 10, 10)
    candidate.report = {}
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(candidate, "emit", records.append)
    monkeypatch.setattr(candidate, "sequence", lambda _source: sequences.append("ran"))
    monkeypatch.setattr(probe.mss, "MSS", Source)
    with pytest.raises(RuntimeError, match="creation completed after deadline"):
        candidate.capture_sequence()
    assert closes == ["closed"]
    assert sequences == []
    assert candidate.wheel.writes == 0
    assert records == [
        {"event": "call_start", "name": "wheel.mss.create"},
        {"event": "call_end", "name": "wheel.mss.create"},
        {"event": "call_start", "name": "wheel.mss.close"},
        {"event": "call_end", "name": "wheel.mss.close"},
    ]


@pytest.mark.parametrize("miss", [False, True])
def test_real_discovery_emitter_to_wheel_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, miss: bool
) -> None:
    """Nested calls finish before discovery's receipt, including cold misses."""
    now = [0.1]
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    instance = Probe(argparse.Namespace(output=tmp_path / "probe", x=50, y=50))
    instance.guard = object.__new__(NativeGuard)
    instance.uia = object.__new__(PanelUia)
    instance.uia.ancestors = []
    target = Structure(123, 0, 50033, "Chrome", (10, 20, 100, 200), offscreen=False)
    monkeypatch.setattr(NativeGuard, "check", lambda _: None)

    def discover(self: PanelUia, _x: int, _y: int) -> Structure:
        now[0] = 0.2
        instance.emit({"event": "call_start", "slot": 7})
        now[0] = 0.3
        instance.emit({"event": "call_end", "slot": 7})
        self.ancestors.append(target)
        if miss:
            raise OSError(
                "No vertical ScrollPattern: ancestor chain ended after 1 nodes"
            )
        return target

    monkeypatch.setattr(PanelUia, "discover", discover)
    try:
        if miss:
            with pytest.raises(OSError, match="No vertical ScrollPattern"):
                instance.discovery_observation("cold")
        else:
            assert instance.discovery_observation("cold") == target
    finally:
        instance.log.close()
    records = supervisor.events(tmp_path / "probe" / "calls.jsonl")
    assert records[-1]["monotonic"] == 0.3
    assert records[-1]["discovery_started_monotonic"] == 0.1
    assert ("error" in records[-1]) is miss
    supervisor.validate_log(records, 0.0, 0.4, finished=True)
    records[-1]["monotonic"] = 0.1
    with pytest.raises(RuntimeError, match="Wheel event time invalid"):
        supervisor.validate_log(records, 0.0, 0.4, finished=True)
