"""Exact fixed failure evidence without leaking native IDs or exception text."""

import asyncio
import io
import subprocess
import time
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture import cursor_state_child as child
from tests.capture import cursor_state_experiment as experiment
from tests.capture import cursor_state_peer as peer_module
from tests.capture.cursor_admission_policy import PreservedState
from tests.capture.cursor_admission_surface import NativeState
from tests.capture.cursor_state_evidence import CheckError, ChildError, Evidence, kind
from tests.capture.cursor_state_peer import Peer, Wire
from tests.capture.cursor_state_runtime import LaunchPlan


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("secret"), "timeout"),
        (OSError("secret"), "os-error"),
        (ValueError("secret"), "value-error"),
        (TypeError("secret"), "type-error"),
        (CheckError("client-foreground"), "check"),
        (RuntimeError("secret"), "runtime-error"),
        (KeyError("secret"), "other"),
    ],
)
def test_error_categories_never_include_messages(
    error: Exception, expected: str
) -> None:
    """Only the reviewed category is emitted, never a repr or exception message."""
    assert kind(error) == expected
    evidence = Evidence()
    evidence.mark("client-ready")
    result = evidence.fail(error)
    assert result["error_kind"] == expected
    assert "secret" not in str(result)
    assert evidence.fail(OSError("cleanup secret")) == result


def test_untrusted_child_labels_are_not_forwarded() -> None:
    """Private protocol bugs cannot turn arbitrary strings into public evidence."""
    value = Evidence().fail(ChildError("secret handle 123", "secret class"))
    assert value == {"failure_stage": "unknown", "error_kind": "other"}


def test_actual_peer_retains_allowlisted_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child setup failure is not collapsed into an unexpected phase error."""
    peer = object.__new__(Peer)
    peer.nonce = "scope"
    peer.startup_deadline = time.monotonic() + 5
    monkeypatch.setattr(
        peer,
        "wire",
        cast(
            Wire,
            SimpleNamespace(
                receive=lambda **_kw: {
                    "nonce": "scope",
                    "phase": "failed",
                    "failure_stage": "client-set-foreground",
                    "error_kind": "runtime-error",
                    "secret": "never forward",
                }
            ),
        ),
        raising=False,
    )
    with pytest.raises(ChildError) as captured:
        peer.receive("ready")
    assert Evidence().fail(captured.value) == {
        "failure_stage": "client-set-foreground",
        "error_kind": "runtime-error",
    }


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("spawn", "client-spawn"),
        ("ready", "client-ready"),
        ("identity", "peer-process-pid"),
        ("snapshot", "client-snapshot"),
        ("baseline", "baseline-read"),
        ("child", "client-set-foreground"),
        ("owner", "owner-spawn"),
    ],
)
def test_actual_case_records_setup_boundary_and_still_cleans(
    monkeypatch: pytest.MonkeyPatch, fault: str, expected: str
) -> None:
    """No owner is created after failed setup, and existing fixture cleanup runs."""
    events: list[str] = []
    baseline = PreservedState(1, (0, 1, 2, 0, 0, 0, 0), (1, 4, 10, 20))

    def read() -> PreservedState:
        if fault == "baseline":
            raise OSError("secret native failure")
        return baseline

    def receive(_phase: str, **_kw: object) -> dict[str, object]:
        if fault == "ready":
            raise TimeoutError("secret receipt")
        if fault == "child":
            raise ChildError("client-set-foreground", "runtime-error")
        return {"hwnd": 1, "pid": 9 if fault == "identity" else 7}

    def snapshot() -> dict[str, object]:
        if fault == "snapshot":
            raise ValueError("secret payload")
        return {"thread_cursor": 4, "class_cursor": 4}

    def peer(role: str, _mode: str) -> object:
        events.append(role)
        if fault == "spawn" or role == "owner":
            raise OSError("secret spawn")
        return SimpleNamespace(
            receive=receive,
            snapshot=snapshot,
            child=SimpleNamespace(pid=7),
            close=lambda: events.append("close"),
        )

    monkeypatch.setattr(
        experiment,
        "NativeState",
        lambda: SimpleNamespace(read=read, hit=lambda _s: 1, process=lambda _h: 7),
    )
    monkeypatch.setattr(experiment, "Peer", peer)
    result = experiment.run_case("arrow", "normal")
    assert result["accepted"] is False
    assert result["failure_stage"] == expected
    assert "secret" not in str(result)
    assert result["cleanup"] is True
    assert ("owner" in events) == (fault == "owner")
    assert ("close" in events) == (fault != "spawn")


@pytest.mark.parametrize(
    ("fault", "stage"),
    [
        ("foreground", "client-foreground"),
        ("hit", "client-physical-hit"),
        ("thread", "client-thread-cursor"),
        ("class", "client-class-cursor"),
        ("global", "client-global-cursor"),
        ("flags", "client-cursor-flags"),
        ("capture", "client-capture-menu"),
    ],
)
def test_baseline_predicates_remain_distinct_and_fail_closed(
    fault: str, stage: str
) -> None:
    """The same combined original oracle now exposes its exact first predicate."""
    baseline = PreservedState(
        9 if fault == "foreground" else 1,
        (0, 1, 2, 9 if fault == "capture" else 0, 0, 0, 0),
        (0 if fault == "flags" else 1, 9 if fault == "global" else 4, 10, 20),
    )
    before: dict[str, object] = {
        "thread_cursor": 0 if fault == "thread" else 4,
        "class_cursor": 9 if fault == "class" else 4,
    }
    state = cast(
        NativeState, SimpleNamespace(hit=lambda _s: 9 if fault == "hit" else 1)
    )
    with pytest.raises(CheckError) as captured:
        experiment.verify_baseline(state, 1, before, baseline, Evidence())
    assert captured.value.stage == stage


@pytest.mark.parametrize(
    ("fault", "stage"),
    [
        ("global", "client-global-snapshot"),
        ("thread", "client-thread-snapshot"),
        ("class", "client-class-snapshot"),
    ],
)
def test_child_snapshot_stage_precedes_each_native_read(fault: str, stage: str) -> None:
    """Actual UI-thread snapshot routine records which native read failed."""

    def operation(label: str, value: object) -> object:
        if fault == label:
            raise OSError("secret identity")
        return value

    state = cast(
        NativeState,
        SimpleNamespace(
            read=lambda: operation("global", SimpleNamespace()),
            api=SimpleNamespace(
                GetCursor=lambda: operation("thread", 4),
                GetClassLongPtrW=lambda *_args: operation("class", 4),
            ),
        ),
    )
    commands = cast(child.Commands, SimpleNamespace(evidence=Evidence()))
    with pytest.raises(OSError, match="secret identity"):
        child.report_client_snapshot(
            state, cast(child.ClientWindow, SimpleNamespace(handle=1)), commands, 1
        )
    assert commands.evidence.stage == stage


@pytest.mark.parametrize(
    ("close_false", "later"), [(False, "release"), (False, "wait"), (True, "wait")]
)
def test_first_surface_cleanup_failure_survives_broker_failure(
    monkeypatch: pytest.MonkeyPatch, *, close_false: bool, later: str
) -> None:
    """Nested cleanup cannot replace the first fixed location/category."""
    events: list[str] = []
    evidence = Evidence()

    def close() -> bool:
        events.append("close")
        if not close_false:
            raise OSError("secret close error")
        return False

    def release() -> None:
        events.append("release")
        if later == "release":
            raise RuntimeError("secret release error")

    def wait(_predicate: object, seconds: float) -> None:
        assert seconds == 1.5
        events.append("wait")
        raise TimeoutError("secret wait error")

    monkeypatch.setattr(child, "wait_for", wait)
    with pytest.raises((RuntimeError, TimeoutError)) as caught:
        child.retire_owner(
            cast(child.CursorStateSurface, SimpleNamespace(close=close)),
            cast(child.NativeInputBroker, SimpleNamespace(release=release)),
            [],
            cast(child.Commands, SimpleNamespace(evidence=evidence)),
            destroyed=False,
        )
    result = evidence.fail(caught.value)
    assert result == {
        "failure_stage": "owner-cleanup",
        "error_kind": "runtime-error" if close_false else "os-error",
    }
    assert events == (
        ["close", "release"] if later == "release" else ["close", "release", "wait"]
    )
    assert "secret" not in str(result)


@pytest.mark.parametrize(
    "fault", ["reader-init", "reader-live", "termination", "control", "cancelled"]
)
def test_spawned_child_stays_owned_when_reader_initialization_fails(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Preserve the original exception and never confirm uncertain retirement."""
    retained: list[Peer] = []
    initializing: list[Peer] = []
    events: list[str] = []
    monkeypatch.setattr(peer_module, "_UNCONFIRMED", retained)
    monkeypatch.setattr(peer_module, "_INITIALIZING", initializing)
    monkeypatch.setattr(
        peer_module, "build_launch_plan", lambda: LaunchPlan("python", {}, {})
    )
    original = {
        "control": KeyboardInterrupt("private reader initialization details"),
        "cancelled": asyncio.CancelledError("private reader initialization details"),
    }.get(fault, ValueError("private reader initialization details"))

    def wait(*, timeout: float) -> None:
        assert timeout == 2
        events.append("wait")
        if fault == "termination":
            raise subprocess.TimeoutExpired("private child", timeout)

    fake = cast(
        subprocess.Popen[str],
        SimpleNamespace(
            stdout=io.StringIO(),
            stdin=io.StringIO(),
            poll=lambda: None,
            wait=wait,
            kill=lambda: events.append("kill"),
            returncode=0,
        ),
    )
    monkeypatch.setattr(peer_module.subprocess, "Popen", lambda *_args, **_kw: fake)

    def broken(self: Wire, _stream: object) -> None:
        assert len(initializing) == 1
        assert initializing[0].wire is self
        if fault == "reader-live":
            self.reader = cast(
                peer_module.threading.Thread,
                SimpleNamespace(
                    join=lambda **_kw: events.append("join"),
                    is_alive=lambda: True,
                ),
            )
        raise original

    monkeypatch.setattr(Wire, "__init__", broken)
    with pytest.raises(
        type(original), match="private reader initialization details"
    ) as captured:
        Peer("client", "arrow")
    assert captured.value is original
    assert not initializing
    assert peer_module.cleanup_confirmed() is (
        fault in {"reader-init", "control", "cancelled"}
    )
    if fault in {"reader-init", "control", "cancelled"}:
        assert fake.stdout is not None
        assert fake.stdout.closed
        assert events == ["wait"]
    else:
        assert retained
        assert fake.stdout is not None
        assert not fake.stdout.closed
        assert events == (
            ["wait", "join"] if fault == "reader-live" else ["wait", "kill", "wait"]
        )


@pytest.mark.parametrize("fault", ["join", "alive"])
def test_reader_control_retains_owner_before_propagating(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Cancellation cannot drop a partially retired stream/reader reference."""
    retained: list[Peer] = []
    monkeypatch.setattr(peer_module, "_UNCONFIRMED", retained)
    peer = object.__new__(Peer)
    peer.crashed = False
    control = asyncio.CancelledError("private cancellation")

    def check(label: str) -> bool:
        if fault == label:
            raise control
        return True

    fake = cast(
        subprocess.Popen[str],
        SimpleNamespace(
            poll=lambda: 0, returncode=0, stdin=io.StringIO(), stdout=io.StringIO()
        ),
    )
    peer.child = fake
    peer.wire = cast(
        Wire,
        SimpleNamespace(
            reader=SimpleNamespace(
                join=lambda **_kw: check("join"), is_alive=lambda: check("alive")
            )
        ),
    )
    with pytest.raises(asyncio.CancelledError) as captured:
        peer.close()
    assert captured.value is control
    assert retained == [peer]
    assert fake.stdout is not None
    assert not fake.stdout.closed
    assert not peer.retirement_confirmed
