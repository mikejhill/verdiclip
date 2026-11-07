"""Portable prospective temporary-role handshakes, not native capture proof."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke
from scripts import observe_process_diagnostics as native

Json = dict[str, Any]
SESSION = "a" * 32
BASE = r"C:\runtime\python.exe"
CONSOLE = r"C:\Windows\System32\conhost.exe"


@pytest.fixture
def baseline(monkeypatch: pytest.MonkeyPatch) -> Json:
    """Keep native identity validation real while avoiding a real DLL or process."""
    monkeypatch.setattr(smoke, "topology_module", lambda: native)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: 2.0)
    return {
        "request": {"pids": [11, 12, 13], "expected_base": BASE},
        "roles": {"launcher": 11, "executor": 12, "console": 13},
        "console": CONSOLE,
        "records": {"processes": [{"process_id": 12, "creation_100ns": 112}]},
    }


@pytest.fixture
def peer() -> Json:
    """Use the fixed production channel's attested helper identity shape."""
    return {
        "pid": 44,
        "executable": BASE,
        "protocol_version": smoke.PROTOCOL_VERSION,
        "peer_schema": smoke.PEER_SCHEMA,
        "credit_schema": smoke.CREDIT_SCHEMA,
        "epoch": "b" * 32,
    }


@pytest.fixture
def prospective(peer: Json) -> Json:
    """A direct peer and one exact console child have independently pinned metadata."""
    return {
        "peer": deepcopy(peer),
        "console": CONSOLE,
        "roles": {"peer": 44, "console": 45},
        "records": {
            "processes": [
                {
                    "requested_pid": pid,
                    "process_id": pid,
                    "creation_100ns": 100 + pid,
                    "image_path": image,
                    "status": "available",
                    "errors": [],
                    "parent": {"status": "available", "pid": parent},
                }
                for pid, parent, image in ((44, 12, BASE), (45, 44, CONSOLE))
            ],
            "cleanup_errors": [],
        },
    }


def packet(field: str, value: Json) -> Json:
    """Build a fixed session-bound private exchange, never a capture request."""
    return {"schema": 1, "session": SESSION, field: value}


def targets(peer: Json) -> Json:
    """A pre-observation native membership snapshot selects exactly two targets."""
    return {
        "peer": deepcopy(peer),
        "pids": [44, 45],
        "deadline": 15.0,
        "started": 2.0,
        "finished": 2.0,
        "snapshot": {"exit_code": None, "members": [11, 12, 13, 44, 45]},
    }


def request_files(output: Path, peer: Json) -> None:
    """Publish only peer identity and prospective target selection request."""
    smoke.write(output / "peer-identity.json", packet("peer", peer))
    smoke.write(
        output / "temporary-request.json",
        packet(
            "request",
            {
                "peer": peer,
                "requested": 1.5,
                "deadline": 15.0,
            },
        ),
    )


def retirement(prospective: Json) -> Json:
    """Explicit zero-time signal proof is separate from production natural cleanup."""
    records = [
        {
            **deepcopy(original),
            "status": "retired",
            "observed_process_id": original["process_id"],
            "observed_creation_100ns": original["creation_100ns"],
            "identity_unchanged": True,
            "signaled": True,
            "wait_result": 0,
            "retired": True,
        }
        for original in prospective["records"]["processes"]
    ]
    return {
        "witness": {"retired": True, "processes": records, "cleanup_errors": []},
        "helper": {
            "pid": 44,
            "cleanup_confirmed": True,
            "closed": True,
            "producer_verified": True,
            "termination_requested": False,
            "exit_code": 0,
            "remaining_pids": [],
            "cleanup_errors": [],
            "failure": None,
        },
    }


@pytest.mark.parametrize("count", [1, 2])
def test_temporary_records_use_independent_baseline_expectations(
    baseline: Json, peer: Json, prospective: Json, count: int
) -> None:
    """The executor's original lifetime and system console path determine roles."""
    prospective["records"]["processes"] = prospective["records"]["processes"][:count]
    before = deepcopy(prospective)
    expected = {"peer": 44} if count == 1 else {"peer": 44, "console": 45}
    assert smoke.temporary_records(prospective, peer, baseline) == expected
    assert prospective == before


@pytest.mark.parametrize(
    "failure",
    [
        "peer",
        "console-expectation",
        "peer-image",
        "console-image",
        "executor-creation",
        "console-parent",
        "cleanup",
    ],
)
def test_temporary_records_reject_unqualified_prospective_facts(
    baseline: Json, peer: Json, prospective: Json, failure: str
) -> None:
    """Observations cannot redefine expected identity, ancestry, or cleanup status."""
    if failure == "peer":
        prospective["peer"]["pid"] = 99
    elif failure == "console-expectation":
        prospective["console"] = r"D:\other\conhost.exe"
    elif failure == "peer-image":
        prospective["records"]["processes"][0]["image_path"] = r"D:\other\python.exe"
    elif failure == "console-image":
        prospective["records"]["processes"][1]["image_path"] = r"D:\other\conhost.exe"
    elif failure == "executor-creation":
        baseline["records"]["processes"][0]["creation_100ns"] = 999
    elif failure == "console-parent":
        prospective["records"]["processes"][1]["parent"]["pid"] = 12
    else:
        prospective["records"]["cleanup_errors"] = ["snapshot close failed"]
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        smoke.temporary_records(prospective, peer, baseline)


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "missing-record",
        "unrequested-record",
        "bad-image",
        "collect-error",
        "publish-error",
        "late-collect",
    ],
)
def test_acquire_pins_only_exact_targets_and_never_authorizes_observe(  # noqa: C901, PLR0913, PLR0917
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    baseline: Json,
    peer: Json,
    prospective: Json,
    failure: str | None,
) -> None:
    """Pins remain held on success and close on any mismatch or failed publication."""
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    selected = targets(peer)
    if failure == "unrequested-record":
        selected["pids"] = [44]
        selected["snapshot"]["members"] = [11, 12, 13, 44]
    smoke.write(tmp_path / "temporary-targets.json", packet("targets", selected))
    pins = Mock()
    pins.collect.return_value = deepcopy(prospective["records"])
    pins.close.return_value = []
    if failure == "missing-record":
        pins.collect.return_value["processes"].pop()
    elif failure == "bad-image":
        pins.collect.return_value["processes"][1]["image_path"] = (
            r"D:\other\conhost.exe"
        )
    elif failure == "collect-error":
        pins.collect.side_effect = OSError("pin collect failed")
    elif failure == "late-collect":

        def collect() -> Json:
            clock[0] = 15.0
            return deepcopy(prospective["records"])

        pins.collect.side_effect = collect
    module = SimpleNamespace(
        PinnedProcesses=Mock(return_value=pins),
        validate_temporary_profile=native.validate_temporary_profile,
    )
    monkeypatch.setattr(smoke, "topology_module", lambda: module)
    if failure == "publish-error":
        original = smoke.write_handshake

        def publish(path: Path, value: Json) -> None:
            if path.name == "temporary-pinned.json":
                raise OSError("pin publication failed")
            original(path, value)

        monkeypatch.setattr(smoke, "write_handshake", publish)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 15.0)
    try:
        if failure is None:
            held, result = smoke.acquire_temporary(
                tmp_path, SESSION, peer, baseline, journal
            )
            assert held is pins
            assert result["roles"] == {"peer": 44, "console": 45}
            assert smoke.read(tmp_path / "temporary-pinned.json") == packet(
                "profile", result
            )
            pins.close.assert_not_called()
        else:
            with pytest.raises((ValueError, RuntimeError, OSError), match=r".+"):
                smoke.acquire_temporary(tmp_path, SESSION, peer, baseline, journal)
            pins.close.assert_called_once_with()
            assert not (tmp_path / "temporary-pinned.json").exists()
        module.PinnedProcesses.assert_called_once_with(
            tuple(selected["pids"]), 15.0, journal
        )
        assert not (tmp_path / "ownership-pre-observe-ack.json").exists()
        assert not (tmp_path / "case-start.json").exists()
    finally:
        journal.stream.close()


@pytest.mark.parametrize(
    "failure",
    [
        "wrong-session",
        "wrong-peer",
        "wrong-deadline",
        "third-target",
        "missing-peer",
        "late",
        "missing",
    ],
)
def test_acquire_rejects_bad_targets_before_native_pin_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    baseline: Json,
    peer: Json,
    failure: str,
) -> None:
    """Target metadata cannot change the attested peer or expand the bounded request."""
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda _delay: clock.__setitem__(0, 15.0))
    selected = targets(peer)
    value = packet("targets", selected)
    if failure == "wrong-session":
        value["session"] = "c" * 32
    elif failure == "wrong-peer":
        selected["peer"]["pid"] = 55
    elif failure == "wrong-deadline":
        selected["deadline"] = 16.0
    elif failure == "third-target":
        selected["pids"] = [44, 45, 46]
    elif failure == "missing-peer":
        selected["pids"] = [45]
    elif failure == "late":
        selected["finished"] = 15.0
    if failure != "missing":
        smoke.write(tmp_path / "temporary-targets.json", value)
    module = SimpleNamespace(PinnedProcesses=Mock())
    monkeypatch.setattr(smoke, "topology_module", lambda: module)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 15.0)
    try:
        with pytest.raises((ValueError, RuntimeError), match=r".+"):
            smoke.acquire_temporary(tmp_path, SESSION, peer, baseline, journal)
        module.PinnedProcesses.assert_not_called()
        assert not (tmp_path / "temporary-pinned.json").exists()
        assert not (tmp_path / "ownership-pre-observe-ack.json").exists()
    finally:
        journal.stream.close()


def test_serve_selects_targets_then_freezes_roles_without_capture_ack(
    tmp_path: Path, baseline: Json, peer: Json, prospective: Json
) -> None:
    """A successful prospective exchange is still not permission to observe pixels."""
    request_files(tmp_path, peer)
    gui = Mock()
    gui.snapshot.return_value = {"exit_code": None, "members": [11, 12, 13, 44, 45]}
    assert (
        smoke.serve_temporary(tmp_path, SESSION, gui, baseline, {44, 45}, 15.0) is None
    )
    original = (tmp_path / "temporary-targets.json").read_bytes()
    smoke.write(tmp_path / "temporary-pinned.json", packet("profile", prospective))
    assert (
        smoke.serve_temporary(tmp_path, SESSION, gui, baseline, {44, 45}, 15.0)
        == prospective
    )
    assert (tmp_path / "temporary-targets.json").read_bytes() == original
    gui.snapshot.assert_called_once_with()
    assert not (tmp_path / "ownership-pre-observe-ack.json").exists()


@pytest.mark.parametrize(
    "failure",
    [
        "third-member",
        "missing-peer",
        "missing-baseline",
        "exited-gui",
        "forgotten-history",
        "late-snapshot",
        "bad-peer",
    ],
)
def test_serve_rejects_unbounded_or_changed_membership_without_ack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    baseline: Json,
    peer: Json,
    failure: str,
) -> None:
    """No unknown or disappearing historical PID is silently omitted from selection."""
    request_files(tmp_path, peer)
    seen = {44, 45}
    snapshot: Json = {"exit_code": None, "members": [11, 12, 13, 44, 45]}
    if failure == "third-member":
        snapshot["members"].append(46)
    elif failure == "missing-peer":
        snapshot["members"].remove(44)
    elif failure == "missing-baseline":
        snapshot["members"].remove(12)
    elif failure == "exited-gui":
        snapshot["exit_code"] = 0
    elif failure == "forgotten-history":
        seen.add(46)
    elif failure == "bad-peer":
        smoke.write(
            tmp_path / "peer-identity.json", packet("peer", {**peer, "pid": 55})
        )
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    gui = Mock()

    def current() -> Json:
        if failure == "late-snapshot":
            clock[0] = 15.0
        return snapshot

    gui.snapshot.side_effect = current
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        smoke.serve_temporary(tmp_path, SESSION, gui, baseline, seen, 15.0)
    assert not (tmp_path / "temporary-targets.json").exists()
    assert not (tmp_path / "ownership-pre-observe-ack.json").exists()


@pytest.mark.parametrize(
    "failure", ["role-map", "substitute-console", "unknown-image", "history", "late"]
)
def test_serve_rejects_profile_changed_from_frozen_targets(  # noqa: PLR0913, PLR0917
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    baseline: Json,
    peer: Json,
    prospective: Json,
    failure: str,
) -> None:
    """A prospective console cannot be swapped after the exact target set is frozen."""
    request_files(tmp_path, peer)
    smoke.write(tmp_path / "temporary-targets.json", packet("targets", targets(peer)))
    seen = {44, 45}
    if failure == "role-map":
        prospective["roles"]["console"] = 46
    elif failure == "substitute-console":
        prospective["roles"]["console"] = 46
        prospective["records"]["processes"][1].update(requested_pid=46, process_id=46)
    elif failure == "unknown-image":
        prospective["records"]["processes"][1]["image_path"] = r"D:\other\conhost.exe"
    elif failure == "history":
        seen.add(46)
    else:
        monkeypatch.setattr(smoke.time, "monotonic", lambda: 15.0)
    smoke.write(tmp_path / "temporary-pinned.json", packet("profile", prospective))
    gui = Mock()
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        smoke.serve_temporary(tmp_path, SESSION, gui, baseline, seen, 15.0)
    gui.snapshot.assert_not_called()
    assert not (tmp_path / "ownership-pre-observe-ack.json").exists()


def test_retirement_requires_both_original_signals_and_natural_helper(
    prospective: Json,
) -> None:
    """Explicit retained-handle retirement and production status are separate proofs."""
    value = retirement(prospective)
    before = deepcopy(value)
    smoke.validate_temporary_retirement(value, prospective)
    assert value == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signaled", False),
        ("signaled", 1),
        ("identity_unchanged", False),
        ("retired", False),
        ("wait_result", 258),
        ("wait_result", False),
        ("status", "stale"),
        ("errors", ["native failure"]),
        ("observed_process_id", 46),
        ("observed_process_id", 45.0),
        ("observed_creation_100ns", 999),
        ("observed_creation_100ns", 145.0),
        ("process_id", 46),
        ("creation_100ns", 145.0),
        ("image_path", r"D:\other\conhost.exe"),
    ],
)
def test_generic_stale_or_numeric_alias_cannot_replace_retirement_witness(
    prospective: Json, field: str, value: object
) -> None:
    """Each console witness must match the originally pinned native identity."""
    result = retirement(prospective)
    result["witness"]["processes"][1][field] = value
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        smoke.validate_temporary_retirement(result, prospective)


@pytest.mark.parametrize(
    "case", ["aggregate-false", "cleanup", "missing", "extra", "duplicate", "different"]
)
def test_retirement_has_exact_frozen_pid_set(prospective: Json, case: str) -> None:
    """Missing, replaced, duplicated, or additional processes cannot be retired."""
    result = retirement(prospective)
    witness = result["witness"]
    if case == "aggregate-false":
        witness["retired"] = False
    elif case == "cleanup":
        witness["cleanup_errors"] = ["failed close"]
    elif case == "missing":
        witness["processes"].pop()
    elif case == "extra":
        witness["processes"].append({**witness["processes"][1], "requested_pid": 46})
    elif case == "duplicate":
        witness["processes"][1] = deepcopy(witness["processes"][0])
    else:
        witness["processes"][1]["requested_pid"] = 46
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        smoke.validate_temporary_retirement(result, prospective)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pid", 45),
        ("pid", 44.0),
        ("cleanup_confirmed", False),
        ("closed", False),
        ("producer_verified", False),
        ("termination_requested", True),
        ("exit_code", 1),
        ("exit_code", False),
        ("remaining_pids", [44]),
        ("cleanup_errors", ["failed close"]),
        ("failure", "forced"),
    ],
)
def test_forced_or_incomplete_helper_cleanup_cannot_be_retirement(
    prospective: Json, field: str, value: object
) -> None:
    """Even signaled pins cannot hide forced or unconfirmed production cleanup."""
    result = retirement(prospective)
    result["helper"][field] = value
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        smoke.validate_temporary_retirement(result, prospective)


@pytest.mark.parametrize("phase", ["pre-observe", "post-terminal"])
@pytest.mark.parametrize("failure", [None, "identity", "membership"])
def test_stable_phase_ack_requires_qualified_profile_and_exact_current_membership(  # noqa: PLR0913, PLR0917
    tmp_path: Path,
    baseline: Json,
    peer: Json,
    prospective: Json,
    phase: str,
    failure: str | None,
) -> None:
    """No capture or final acknowledgment follows changed roles or failed retirement."""
    smoke.write(tmp_path / "peer-identity.json", packet("peer", peer))
    smoke.write(
        tmp_path / f"ownership-{phase}-request.json",
        packet(
            "request",
            {
                "phase": phase,
                "deadline": 15.0,
                "requested": 1.5,
                "peer": peer if phase == "pre-observe" else None,
            },
        ),
    )
    result = retirement(prospective)
    if failure == "identity":
        if phase == "pre-observe":
            prospective["records"]["processes"][1]["image_path"] = (
                r"D:\other\conhost.exe"
            )
        else:
            result["helper"]["termination_requested"] = True
    smoke.write(tmp_path / "temporary-retired.json", packet("retired", result))
    members = [11, 12, 13, 44, 45] if phase == "pre-observe" else [11, 12, 13]
    if failure == "membership":
        members.append(46)
    gui = Mock()
    gui.snapshot.return_value = {"exit_code": None, "members": members}
    if failure is None:
        assert (
            smoke.serve_checkpoint(
                tmp_path, SESSION, phase, gui, baseline, 15.0, prospective
            )
            is not None
        )
        assert (tmp_path / f"ownership-{phase}-ack.json").exists()
    else:
        with pytest.raises((ValueError, RuntimeError), match=r".+"):
            smoke.serve_checkpoint(
                tmp_path, SESSION, phase, gui, baseline, 15.0, prospective
            )
        assert not (tmp_path / f"ownership-{phase}-ack.json").exists()
        if failure == "identity":
            gui.snapshot.assert_not_called()


def test_frozen_roles_never_forget_a_transient_or_substituted_pid() -> None:
    """At most two provisional identities must become precisely the frozen role set."""
    baseline = {11, 12, 13}
    seen: set[int] = set()
    smoke.track_temporary_member(
        baseline | {44, 45}, baseline, seen, None, retiring=False
    )
    smoke.track_temporary_member(
        baseline | {44, 45}, baseline, seen, 44, retiring=False, admitted={44, 45}
    )
    with pytest.raises(RuntimeError, match="Unexpected"):
        smoke.track_temporary_member(
            baseline | {44, 46}, baseline, seen, 44, retiring=False, admitted={44, 45}
        )
    assert seen == {44, 45, 46}
    with pytest.raises(RuntimeError, match="Unexpected"):
        smoke.track_temporary_member(
            baseline | {44, 45}, baseline, seen, 44, retiring=False, admitted={44, 45}
        )


@pytest.mark.parametrize("extra", [{44}, {45}, {44, 45}])
def test_retirement_phase_cannot_hide_a_remaining_frozen_member(
    extra: set[int],
) -> None:
    """Every temporary process must leave the owning job before final acknowledgment."""
    with pytest.raises(RuntimeError, match="after helper retirement"):
        smoke.track_temporary_member(
            {11, 12, 13} | extra,
            {11, 12, 13},
            {44, 45},
            44,
            retiring=True,
            admitted={44, 45},
        )


def test_cancel_retirement_requires_intentional_termination(prospective: Json) -> None:
    """Only explicit cancellation mode permits a terminated helper, not outer force."""
    value = retirement(prospective)
    value["helper"].update(termination_requested=True, exit_code=1)
    smoke.validate_temporary_retirement(value, prospective, cancelled=True)
    with pytest.raises(RuntimeError):
        smoke.validate_temporary_retirement(value, prospective)
    value["helper"]["termination_requested"] = False
    with pytest.raises(RuntimeError):
        smoke.validate_temporary_retirement(value, prospective, cancelled=True)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("cleanup_confirmed", False),
        ("cleanup_errors", ["failed"]),
        ("remaining_pids", [44]),
        ("failure", "timeout"),
        ("exit_code", None),
    ],
)
def test_cancel_never_waives_cleanup_evidence(
    prospective: Json, field: str, bad: object
) -> None:
    """Intentional termination does not excuse late, incomplete or failed cleanup."""
    value = retirement(prospective)
    value["helper"].update(termination_requested=True, exit_code=1)
    value["helper"][field] = bad
    with pytest.raises(RuntimeError):
        smoke.validate_temporary_retirement(value, prospective, cancelled=True)
