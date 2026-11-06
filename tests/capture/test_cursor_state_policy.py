"""Portable adversarial controls for the disposable scoped-write experiment."""

import io
import queue
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture import cursor_state_child as child_module
from tests.capture import cursor_state_experiment as experiment
from tests.capture import cursor_state_peer as peer_module
from tests.capture.cursor_admission_policy import PreservedState
from tests.capture.cursor_admission_surface import NativeState
from tests.capture.cursor_state_peer import Peer, Wire
from tests.capture.cursor_state_policy import CursorStatePolicy
from tests.capture.cursor_state_surface import CursorStateSurface


def original() -> PreservedState:
    """Opaque fake identities stay in unit-test memory."""
    return PreservedState(1, (0, 1, 2, 0, 0, 0, 0), (1, 4, -100, 200))


def hidden() -> PreservedState:
    """Logical NULL evidence is not a rendered pointer assertion."""
    before = original()
    return PreservedState(before.foreground, before.gui, (0, 0, -100, 200))


@pytest.mark.parametrize("fault", ["foreground", "gui", "position", "flags", "handle"])
def test_owned_phase_changes_never_relax_foreign_state(fault: str) -> None:
    """Only the intended NULL state is admitted after a scoped write."""
    policy = CursorStatePolicy(original())
    assert policy.admit_write(original(), owned=True, safe=True)
    policy.wrote()
    value = hidden()
    changed = PreservedState(
        9 if fault == "foreground" else value.foreground,
        (0, 1, 8, 0, 0, 0, 0) if fault == "gui" else value.gui,
        {
            "position": (0, 0, -99, 200),
            "flags": (1, 0, -100, 200),
            "handle": (0, 8, -100, 200),
        }.get(fault, value.cursor),
    )
    assert not policy.observe(changed, safe=True)
    assert not policy.observe(hidden(), safe=True)


@pytest.mark.parametrize("first_good", [False, True])
def test_first_restoration_is_final(*, first_good: bool) -> None:
    """Later equality cannot turn a failed first observation into success."""
    policy = CursorStatePolicy(original())
    assert policy.admit_write(original(), owned=True, safe=True)
    policy.wrote()
    assert policy.retire(original() if first_good else hidden()) is first_good
    assert policy.observe(original(), safe=True) is first_good
    assert not policy.admit_write(original(), owned=True, safe=True)


@pytest.mark.parametrize(
    "fault", ["capture", "menu", "menu-flags", "unsafe", "foreign", "budget"]
)
def test_no_write_admission_on_unsupported_state(fault: str) -> None:
    """No borrowed capture/menu ownership or unlimited callback loop is accepted."""
    before = original()
    gui = list(before.gui)
    if fault == "capture":
        gui[3] = 9
    if fault == "menu":
        gui[4] = 9
    if fault == "menu-flags":
        gui[0] = 0x14
    value = PreservedState(before.foreground, tuple(gui), before.cursor)
    policy = CursorStatePolicy(value)
    policy.messages = 64 if fault == "budget" else 0
    assert not policy.admit_write(
        value, owned=fault != "foreign", safe=fault != "unsafe"
    )
    assert policy.writes == 0


@pytest.mark.parametrize(
    "fault", [None, "window", "hit", "nonclient", "unsafe", "read"]
)
def test_actual_handler_is_only_null_write_site(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    """No synthetic dispatch is used by the harness; inject only portable unit fakes."""
    policy = CursorStatePolicy(original())
    calls: list[object] = []
    surface = object.__new__(CursorStateSurface)
    surface.handle, surface.policy, surface.admission = 5, policy, policy

    def read() -> PreservedState:
        if fault == "read":
            raise OSError("unavailable")
        return hidden() if calls else original()

    surface.state = cast(
        NativeState,
        SimpleNamespace(read=read, hit=lambda _state: 8 if fault == "hit" else 5),
    )
    surface.safe = lambda: fault != "unsafe"
    monkeypatch.setattr(
        surface, "api", SimpleNamespace(SetCursor=calls.append), raising=False
    )
    assert (
        surface._message(
            5, 0x20, 8 if fault == "window" else 5, 2 if fault == "nonclient" else 1
        )
        == 1
    )
    assert calls == ([None] if fault is None else [])
    assert policy.writes == (1 if fault is None else 0)


def test_wire_first_reply_is_not_retried() -> None:
    """An invalid first reply cannot be replaced with a later favorable one."""
    wire = object.__new__(Wire)
    wire.lines = queue.Queue()
    wire.lines.put("[]\n")
    wire.lines.put('{"ok": true}\n')
    with pytest.raises(TypeError):
        wire.receive()
    assert wire.lines.qsize() == 1


@pytest.mark.parametrize("fault", ["nonce", "phase", "sequence"])
def test_peer_snapshot_correlates_exact_request(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Stale or cross-process state cannot qualify preservation."""
    peer = object.__new__(Peer)
    peer.nonce = "correct"
    monkeypatch.setattr(peer, "command", lambda _op: 7)
    peer.wire = cast(
        Wire,
        SimpleNamespace(
            receive=lambda **_kw: {
                "nonce": "wrong" if fault == "nonce" else "correct",
                "phase": "wrong" if fault == "phase" else "snapshot",
                "sequence": 6 if fault == "sequence" else 7,
            }
        ),
    )
    with pytest.raises(ValueError, match=r"correlation|sequence"):
        peer.snapshot()


@pytest.mark.parametrize("failure_index", [0, 2, 5, 6, None])
def test_case_failure_stops_all_later_cases(
    monkeypatch: pytest.MonkeyPatch, failure_index: int | None
) -> None:
    """Crash cases require every ordinary case; failure cannot be overwritten."""
    monkeypatch.setenv("CURSOR_STATE_DISPOSABLE", "1")
    monkeypatch.setattr(experiment, "require_hosted", lambda _env: None)
    monkeypatch.setattr(
        experiment.DesktopSession, "current", lambda: SimpleNamespace(state=0, flags=1)
    )
    seen: list[tuple[str, str]] = []

    def run(client: str, mode: str) -> dict[str, object]:
        seen.append((client, mode))
        return {"accepted": len(seen) - 1 != failure_index}

    monkeypatch.setattr(experiment, "run_case", run)
    results = experiment.run_experiment()
    assert len(results) == (8 if failure_index is None else failure_index + 1)
    assert all(mode != "crash" for _, mode in seen[:6])
    if len(seen) > 6:
        assert seen[6][1] == "crash"


def test_write_surface_has_no_restore_or_input_api() -> None:
    """The only scoped write is NULL; no saved handle or global visibility repair."""
    source = Path(__file__).with_name("cursor_state_surface.py").read_text()
    assert source.count("self.api.SetCursor(None)") == 1
    for forbidden in (
        "ShowCursor",
        "SetCursorPos",
        "SetCapture",
        "AttachThreadInput",
        "SendInput",
        "MagShow",
        "SetClassLong",
    ):
        assert forbidden not in source


def test_policy_comparison_ignores_only_global_shape() -> None:
    """Other client's thread and class state remain exact even during owned NULL."""
    before: dict[str, object] = {
        "thread_cursor": 4,
        "class_cursor": 4,
        "foreground": 1,
        "gui": [1],
        "pid": 7,
        "hwnd": 8,
        "cursor": [1, 4],
    }
    assert experiment.policy_equal(before, {**before, "cursor": [0, 0]})
    assert not experiment.policy_equal(before, {**before, "thread_cursor": 0})
    assert not experiment.policy_equal(before, {**before, "class_cursor": 0})


def test_wire_eof_is_not_success() -> None:
    """A crashed peer cannot satisfy an outstanding snapshot request."""
    wire = Wire(io.StringIO(""))
    wire.reader.join(timeout=1)
    with pytest.raises(ValueError, match="Expecting value"):
        wire.receive()


@pytest.mark.parametrize("fault", ["first", "policy", "drain", "late"])
def test_actual_terminal_path_stops_at_first_bad_sample(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """No later good state, drain or finish command repairs a first mismatch."""
    events: list[str] = []
    reads = 0
    snapshots = 0

    def read() -> PreservedState:
        nonlocal reads
        reads += 1
        return (
            hidden()
            if (fault == "first" and reads == 1) or (fault == "late" and reads == 3)
            else original()
        )

    def snapshot() -> dict[str, object]:
        nonlocal snapshots
        snapshots += 1
        return {"thread_cursor": 9 if fault == "policy" and snapshots == 2 else 4}

    def receive(phase: str, **_kwargs: object) -> dict[str, object]:
        events.append(phase)
        return {
            "preserved": not (fault == "drain" and phase == "drained"),
            "first_restored": True,
            "cleanup": True,
        }

    monkeypatch.setattr(experiment.QTest, "qWait", lambda _ms: None)
    owner = cast(Peer, SimpleNamespace(command=events.append, receive=receive))
    fixture = cast(Peer, SimpleNamespace(snapshot=snapshot))
    state = cast(NativeState, SimpleNamespace(read=read, process=lambda _handle: 0))
    receipt: dict[str, object] = {}
    observation = experiment.Observation(original(), {"thread_cursor": 4}, 5, receipt)
    with pytest.raises(RuntimeError, match=r"restoration|policy|drain|late"):
        experiment.finish_case("normal", owner, fixture, state, observation)
    if fault in {"first", "policy"}:
        assert "drain" not in events
    if fault == "drain":
        assert "finish" not in events
    assert "done" not in events


@pytest.mark.parametrize("send_fault", [False, True])
def test_peer_cleanup_terminates_exact_child_on_timeout(
    monkeypatch: pytest.MonkeyPatch, *, send_fault: bool
) -> None:
    """A failed control write cannot skip bounded exact-child retirement."""
    events: list[str] = []
    calls = 0

    def wait(*, timeout: float) -> None:
        nonlocal calls
        assert timeout == 2
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("owned", timeout)

    def command(_op: str) -> int:
        if send_fault:
            raise BrokenPipeError("closed pipe")
        return 1

    peer = object.__new__(Peer)
    peer.crashed = False
    peer.child = cast(
        subprocess.Popen[str],
        SimpleNamespace(
            poll=lambda: None,
            wait=wait,
            kill=lambda: events.append("kill"),
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            returncode=0,
        ),
    )
    peer.wire = cast(
        Wire,
        SimpleNamespace(
            reader=SimpleNamespace(
                join=lambda **_kw: events.append("join"), is_alive=lambda: False
            )
        ),
    )
    monkeypatch.setattr(peer, "command", command)
    with pytest.raises(RuntimeError, match="forced cleanup"):
        peer.close()
    assert calls == 2
    assert events == ["kill", "join"]


@pytest.mark.parametrize("fault", ["kill", "wait", "reader"])
def test_uncertain_peer_cleanup_retains_stream_owners(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Never close a buffered stream whose reader could still hold its lock."""
    events: list[str] = []
    retained: list[Peer] = []
    monkeypatch.setattr(peer_module, "_UNCONFIRMED", retained)
    peer = object.__new__(Peer)
    peer.crashed = False

    def wait(*, timeout: float) -> None:
        raise subprocess.TimeoutExpired("owned", timeout)

    def kill() -> None:
        events.append("kill")
        if fault == "kill":
            raise OSError("kill unavailable")

    peer.child = cast(
        subprocess.Popen[str],
        SimpleNamespace(
            poll=lambda: 0 if fault == "reader" else None,
            wait=wait,
            kill=kill,
            returncode=0,
            stdin=SimpleNamespace(close=lambda: events.append("close-stdin")),
            stdout=SimpleNamespace(close=lambda: events.append("close-stdout")),
        ),
    )
    peer.wire = cast(
        Wire,
        SimpleNamespace(
            reader=SimpleNamespace(
                join=lambda **_kw: events.append("join"),
                is_alive=lambda: True,
            )
        ),
    )
    monkeypatch.setattr(peer, "command", lambda _op: 1)
    with pytest.raises((OSError, subprocess.TimeoutExpired, RuntimeError)):
        peer.close()
    assert retained == [peer]
    assert not any(event.startswith("close-") for event in events)
    assert events == (["join"] if fault == "reader" else ["kill"])


def test_control_during_owner_cleanup_still_retires_fixture() -> None:
    """Do not suppress a control exception or strand the independently owned peer."""
    events: list[str] = []

    def owner_close() -> None:
        events.append("owner")
        raise KeyboardInterrupt

    owner = cast(Peer, SimpleNamespace(close=owner_close))
    fixture = cast(Peer, SimpleNamespace(close=lambda: events.append("fixture")))
    with pytest.raises(KeyboardInterrupt):
        experiment.retire_pair(owner, fixture)
    assert events == ["owner", "fixture"]


def test_child_reader_retires_after_close_command() -> None:
    """Normal child exit must not leave a daemon holding the stdin buffer lock."""
    wire = Wire(io.StringIO('{"op":"close"}\nnot-read\n'), stop_on_close=True)
    wire.reader.join(timeout=1)
    assert not wire.reader.is_alive()
    assert wire.receive() == {"op": "close"}
    assert wire.lines.empty()


def test_owner_waits_for_natural_admission_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Showing the HWND is not itself evidence that its natural callback ran."""
    events: list[str] = []
    policy = CursorStatePolicy(original())

    def wait(predicate: object, _seconds: float) -> None:
        events.append("wait")
        assert policy.writes == 0
        assert policy.admit_write(original(), owned=True, safe=True)
        policy.wrote()
        assert callable(predicate)
        assert predicate()

    monkeypatch.setattr(child_module, "wait_for", wait)
    state = cast(NativeState, SimpleNamespace(read=original))
    surface = cast(CursorStateSurface, SimpleNamespace(handle=5, close=lambda: True))
    commands = cast(
        child_module.Commands,
        SimpleNamespace(
            report=lambda phase, **_kw: events.append(phase),
            expect=events.append,
        ),
    )
    context = child_module.OwnedContext(state, policy, surface, lambda: True, 0)
    assert child_module.owned_interval(commands, context)
    assert events[0:2] == ["wait", "owned"]
    assert policy.first_restored is True


def test_missing_natural_message_fails_without_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deadline cannot be satisfied by synthesized cursor/input activity."""
    policy = CursorStatePolicy(original())

    def wait(_predicate: object, _seconds: float) -> None:
        raise TimeoutError("admission deadline")

    monkeypatch.setattr(child_module, "wait_for", wait)
    context = child_module.OwnedContext(
        cast(NativeState, SimpleNamespace()),
        policy,
        cast(CursorStateSurface, SimpleNamespace()),
        lambda: True,
        0,
    )
    with pytest.raises(TimeoutError, match="admission deadline"):
        child_module.owned_interval(
            cast(child_module.Commands, SimpleNamespace()), context
        )
    assert policy.writes == 0
