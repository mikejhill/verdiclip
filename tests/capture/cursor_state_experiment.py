"""Independent observer of bounded owned cursor transitions, never rendered proof."""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtTest import QTest
from tests.capture.cursor_admission_policy import PreservedState
from tests.capture.cursor_admission_surface import NativeState
from tests.capture.cursor_state_peer import Peer
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
        raise RuntimeError(label)


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


def finish_case(
    mode: str, owner: Peer, fixture: Peer, state: NativeState, observation: Observation
) -> None:
    """Compare first correlated terminal observations, then fixed later samples."""
    baseline, before = observation.baseline, observation.before
    owned_handle, result = observation.owned_handle, observation.result
    if mode == "crash":
        owner.crash()
        # First sample after exact process exit. No owner finally can run.
        first = state.read()
        result["first_restored"] = first == baseline
        require(
            first == baseline and state.process(owned_handle) == 0,
            "crash-first-restoration",
        )
        require(policy_equal(before, fixture.snapshot()), "crash-client-policy")
        result["phase"] = "crashed"
    else:
        owner.command("observe")
        require(
            owner.receive("observed", timeout=2).get("preserved") is True,
            "owned-preservation",
        )
        require(policy_equal(before, fixture.snapshot()), "observed-client-policy")
        result["phase"] = "destroy"
        owner.command("destroy")
        destroyed = owner.receive("destroyed")
        first = state.read()
        result["first_restored"] = (
            first == baseline and destroyed.get("first_restored") is True
        )
        require(
            result["first_restored"] is True and state.process(owned_handle) == 0,
            "destroy-first-restoration",
        )
        require(policy_equal(before, fixture.snapshot()), "destroy-client-policy")
        owner.command("drain")
        drained = owner.receive("drained", timeout=2)
        require(
            drained.get("cleanup") is True and drained.get("preserved") is True,
            "broker-drain",
        )
        require(
            state.read() == baseline and policy_equal(before, fixture.snapshot()),
            "drain-client-policy",
        )
        result["phase"] = "drained"
        owner.command("finish")
    # Fixed later observations reveal delayed interference; never erase failure.
    for _ in range(10):
        QTest.qWait(100)
        require(state.read() == baseline, "late-global-state")
        require(policy_equal(before, fixture.snapshot()), "late-client-policy")
    if mode != "crash":
        require(
            owner.receive("done").get("preserved") is True,
            "owner-final-preservation",
        )
        owner.command("close")
        owner.child.wait(timeout=2)


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
    state = NativeState()
    fixture = Peer("client", client_mode)
    owner: Peer | None = None
    try:
        handle = verify_peer(state, fixture, fixture.receive("ready", timeout=5))
        before = fixture.snapshot()
        baseline = state.read()
        require(
            baseline.foreground == handle and state.hit(baseline) == handle,
            "client-not-recipient",
        )
        require(
            before["thread_cursor"]
            == before["class_cursor"]
            == baseline.cursor[1]
            != 0,
            "client-cursor-baseline",
        )
        require(
            baseline.cursor[0] == 1
            and not baseline.gui[3]
            and not baseline.gui[4]
            and not baseline.gui[0] & 0x14,
            "unsupported-baseline",
        )
        result["phase"] = "admission"
        owner = Peer("owner", mode)
        owned = owner.receive("owned", timeout=5)
        owned_handle = verify_peer(state, owner, owned)
        writes = owned.get("writes")
        require(
            type(writes) is int and 0 < writes <= 64,
            "missing-owned-write",
        )
        result["writes"] = owned["writes"]
        result["phase"] = "owned"
        during = state.read()
        require(
            during.foreground == baseline.foreground
            and during.gui == baseline.gui
            and during.cursor == (0, 0, *baseline.cursor[2:]),
            "owned-global-state",
        )
        require(policy_equal(before, fixture.snapshot()), "owned-client-policy")
        finish_case(
            mode,
            owner,
            fixture,
            state,
            Observation(baseline, before, owned_handle, result),
        )
        result["phase"] = "complete"
        result["accepted"] = True
    except Exception:  # noqa: BLE001 - fixed phase receipt, never serialize native IDs
        result["accepted"] = False
    finally:
        errors = retire_pair(owner, fixture)
        result["cleanup"] = errors == 0
        if errors:
            result["accepted"] = False
    return result


def retire_pair(owner: Peer | None, fixture: Peer) -> int:
    """Always attempt both retirements, including on a process-control exception."""
    errors = 0
    try:
        if owner is not None:
            try:
                owner.close()
            except Exception:  # noqa: BLE001 - retain cleanup failure, never success
                errors += 1
    finally:
        try:
            fixture.close()
        except Exception:  # noqa: BLE001 - do not mask an original control exception
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
