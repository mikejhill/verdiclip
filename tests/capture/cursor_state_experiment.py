"""Independent observer of bounded owned cursor transitions, never rendered proof."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtTest import QTest
from tests.capture.cursor_admission_policy import PreservedState
from tests.capture.cursor_admission_surface import NativeState
from tests.capture.cursor_state_evidence import CheckError, Evidence
from tests.capture.cursor_state_peer import Peer, cleanup_confirmed
from tests.capture.desktop import DesktopSession
from tests.capture.hosted_fixture_input import require_hosted


def policy_equal(before: dict[str, object], after: dict[str, object]) -> bool:
    """Global cursor is intentionally separate from foreign UI-thread policy."""
    return all(
        before.get(key) == after.get(key)
        for key in (
            "thread_cursor",
            "class_cursor",
            "foreground",
            "gui",
            "pid",
            "hwnd",
        )
    )


def require(value: object, label: str) -> None:
    """Reject a failed finite check with a fixed diagnostic label."""
    if value is not True:
        raise CheckError(label)


def verify_peer(state: NativeState, peer: Peer, receipt: dict[str, object]) -> int:
    """Bind an opaque HWND to the exact Popen-owned process."""
    handle, pid = receipt.get("hwnd"), receipt.get("pid")
    require(type(handle) is int and type(pid) is int, "peer-identity-shape")
    assert isinstance(handle, int)
    assert isinstance(pid, int)
    require(
        handle != 0 and pid == peer.child.pid and state.process(handle) == pid,
        "peer-identity",
    )
    return handle


@dataclass
class Observation:
    """Independent baseline and fixed receipt, with identities kept private."""

    baseline: PreservedState
    before: dict[str, object]
    owned_handle: int
    result: dict[str, object]
    evidence: Evidence = field(default_factory=Evidence)


def finish_case(
    mode: str, owner: Peer, fixture: Peer, state: NativeState, observation: Observation
) -> None:
    """Compare first correlated terminal observations, then fixed later samples."""
    baseline, before = observation.baseline, observation.before
    owned_handle, result = observation.owned_handle, observation.result
    evidence = observation.evidence
    if mode == "crash":
        evidence.mark("crash-process")
        owner.crash()
        # First sample after exact process exit. No owner finally can run.
        evidence.mark("crash-state-read")
        first = state.read()
        result["first_restored"] = first == baseline
        require(first == baseline, "crash-first-restoration")
        evidence.mark("crash-hwnd-liveness")
        require(state.process(owned_handle) == 0, "crash-hwnd-liveness")
        evidence.mark("crash-policy-read")
        require(policy_equal(before, fixture.snapshot()), "crash-client-policy")
        result["phase"] = "crashed"
    else:
        evidence.mark("observe-command")
        owner.command("observe")
        evidence.mark("observed-reply")
        require(
            owner.receive("observed", timeout=2).get("preserved") is True,
            "owned-preservation",
        )
        evidence.mark("observed-policy-read")
        require(policy_equal(before, fixture.snapshot()), "observed-client-policy")
        result["phase"] = "destroy"
        evidence.mark("destroy-command")
        owner.command("destroy")
        evidence.mark("destroyed-reply")
        destroyed = owner.receive("destroyed")
        evidence.mark("destroy-state-read")
        first = state.read()
        result["first_restored"] = (
            first == baseline and destroyed.get("first_restored") is True
        )
        require(result["first_restored"] is True, "destroy-first-restoration")
        evidence.mark("destroy-hwnd-liveness")
        require(state.process(owned_handle) == 0, "destroy-hwnd-liveness")
        evidence.mark("destroy-policy-read")
        require(policy_equal(before, fixture.snapshot()), "destroy-client-policy")
        evidence.mark("drain-command")
        owner.command("drain")
        evidence.mark("drained-reply")
        drained = owner.receive("drained", timeout=2)
        require(
            drained.get("cleanup") is True and drained.get("preserved") is True,
            "broker-drain",
        )
        evidence.mark("drain-state-read")
        require(state.read() == baseline, "drain-global-state")
        evidence.mark("drain-policy-read")
        require(policy_equal(before, fixture.snapshot()), "drain-client-policy")
        result["phase"] = "drained"
        evidence.mark("finish-command")
        owner.command("finish")
    finish_observation(mode, owner, fixture, state, observation)


def finish_observation(
    mode: str, owner: Peer, fixture: Peer, state: NativeState, observation: Observation
) -> None:
    """Retain fixed late observations, never poll for a passing cursor state."""
    evidence, baseline, before = (
        observation.evidence,
        observation.baseline,
        observation.before,
    )
    # Fixed later observations reveal delayed interference; never erase failure.
    for _ in range(10):
        QTest.qWait(100)
        evidence.mark("late-state-read")
        require(state.read() == baseline, "late-global-state")
        evidence.mark("late-policy-read")
        require(policy_equal(before, fixture.snapshot()), "late-client-policy")
    if mode != "crash":
        evidence.mark("done-reply")
        require(
            owner.receive("done").get("preserved") is True,
            "owner-final-preservation",
        )
        evidence.mark("close-command")
        owner.command("close")
        evidence.mark("owner-exit")
        owner.child.wait(timeout=2)


def verify_baseline(
    state: NativeState,
    handle: int,
    before: dict[str, object],
    baseline: PreservedState,
    evidence: Evidence,
) -> None:
    """Keep each original admission predicate distinct without changing the oracle."""
    evidence.mark("client-foreground")
    require(baseline.foreground == handle, "client-foreground")
    evidence.mark("client-physical-hit")
    require(state.hit(baseline) == handle, "client-physical-hit")
    evidence.mark("client-thread-cursor")
    require(before["thread_cursor"] != 0, "client-thread-cursor")
    evidence.mark("client-class-cursor")
    require(before["thread_cursor"] == before["class_cursor"], "client-class-cursor")
    evidence.mark("client-global-cursor")
    require(before["class_cursor"] == baseline.cursor[1], "client-global-cursor")
    evidence.mark("client-cursor-flags")
    require(baseline.cursor[0] == 1, "client-cursor-flags")
    evidence.mark("client-capture-menu")
    require(
        not baseline.gui[3] and not baseline.gui[4] and not baseline.gui[0] & 0x14,
        "client-capture-menu",
    )


def run_case(client_mode: str, mode: str) -> dict[str, object]:
    """Fail once; neither eventual baseline equality nor later cases can cure it."""
    result: dict[str, object] = {
        "client": client_mode,
        "mode": mode,
        "accepted": False,
        "presentation_verified": False,
        "phase": "setup",
        "cleanup": False,
    }
    evidence = Evidence()
    fixture: Peer | None = None
    owner: Peer | None = None
    try:
        evidence.mark("baseline-read")
        state = NativeState()
        evidence.mark("client-spawn")
        fixture = Peer("client", client_mode)
        evidence.mark("client-ready")
        ready = fixture.receive("ready", timeout=5)
        evidence.mark("client-identity")
        handle = verify_peer(state, fixture, ready)
        evidence.mark("client-snapshot")
        before = fixture.snapshot()
        evidence.mark("baseline-read")
        baseline = state.read()
        verify_baseline(state, handle, before, baseline, evidence)
        result["phase"] = "admission"
        evidence.mark("owner-spawn")
        owner = Peer("owner", mode)
        evidence.mark("owner-ready")
        owned = owner.receive("owned", timeout=5)
        evidence.mark("owner-identity")
        owned_handle = verify_peer(state, owner, owned)
        writes = owned.get("writes")
        require(
            type(writes) is int and 0 < writes <= 64,
            "missing-owned-write",
        )
        result["writes"] = owned["writes"]
        result["phase"] = "owned"
        evidence.mark("owned-state-read")
        during = state.read()
        require(
            during.foreground == baseline.foreground
            and during.gui == baseline.gui
            and during.cursor == (0, 0, *baseline.cursor[2:]),
            "owned-global-state",
        )
        evidence.mark("owned-policy-read")
        require(policy_equal(before, fixture.snapshot()), "owned-client-policy")
        finish_case(
            mode,
            owner,
            fixture,
            state,
            Observation(baseline, before, owned_handle, result, evidence),
        )
        result["phase"] = "complete"
        result["accepted"] = True
    except Exception as error:  # noqa: BLE001 - allowlisted evidence only
        result["accepted"] = False
        result.update(evidence.fail(error))
    finally:
        errors = retire_pair(owner, fixture, evidence)
        result["cleanup"] = errors == 0 and cleanup_confirmed()
        if result["cleanup"] is not True:
            result["accepted"] = False
            if evidence.first is not None:
                result.update(evidence.first)
    return result


def retire_pair(
    owner: Peer | None, fixture: Peer | None, evidence: Evidence | None = None
) -> int:
    """Always attempt both retirements, including on a process-control exception."""
    errors = 0
    trace = evidence or Evidence()
    try:
        if owner is not None:
            try:
                trace.mark("owner-cleanup")
                owner.close()
            except Exception as error:  # noqa: BLE001 - fixed cleanup category
                trace.fail(error)
                errors += 1
    finally:
        try:
            if fixture is not None:
                trace.mark("client-cleanup")
                fixture.close()
        except Exception as error:  # noqa: BLE001 - preserve original control exception
            trace.fail(error)
            errors += 1
    return errors


def run_experiment() -> list[dict[str, object]]:
    """Stop the entire finite sequence at its first unsupported or unsafe case."""
    require_hosted(os.environ)
    require(os.environ.get("CURSOR_STATE_DISPOSABLE") == "1", "disposable-only")
    session = DesktopSession.current()
    require(
        session is not None and session.state == 0 and session.flags == 1,
        "desktop-unavailable",
    )
    results = []
    # All six ordinary cases must succeed before either crash case is attempted.
    cases = [
        (client, mode)
        for client in ("arrow", "cross")
        for mode in ("normal", "cancel", "normal")
    ]
    cases.extend((client, "crash") for client in ("arrow", "cross"))
    for client, mode in cases:
        result = run_case(client, mode)
        results.append(result)
        if result["accepted"] is not True:
            break
    return results
