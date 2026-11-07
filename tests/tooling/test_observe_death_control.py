"""Portable outer death coordinator tests: no native calls or proof qualification."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke
from scripts import observe_death_control as control
from scripts import observe_process_diagnostics as topology

from verdiclip.platform import helper_bootstrap, helper_runtime, owned_helper

Json = dict[str, Any]


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Launch a fake owner but exercise actual file exchange and watchdog parser."""
    monkeypatch.setattr(owned_helper, "HelperCommand", Mock())
    now = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(control.time, "monotonic_ns", lambda: int(now[0] * 1e9))
    monkeypatch.setattr(
        control.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    output = tmp_path / "output"
    output.mkdir()
    executor = output / "executor"
    executor.mkdir()
    owner = Mock()
    owner.process.pid = 90
    owner.snapshot.return_value = {"exit_code": None, "members": [90]}
    gui = Mock()
    gui.snapshot.return_value = {"exit_code": None, "members": [11, 12]}
    fixture = Mock()
    fixture.process.pid = 30
    owners = {"gui": gui, "fixture": fixture}
    runtime = helper_runtime.RuntimeManifest(
        str(tmp_path / "base-python"),
        (str(tmp_path),),
        (str(tmp_path / "module.py"),) * 11,
        ("1", "1"),
        (3, 13, 5),
        64,
    )
    monkeypatch.setattr(helper_runtime, "inspect_runtime", lambda: runtime)
    value = control.DeathControl(
        smoke,
        SimpleNamespace(ExternalOwner=lambda: owner),
        owners,
        output=output,
        executor=executor,
        states=output / "states",
        wheel=output / "fixed.whl",
        session="a" * 32,
        started=100.0,
        provenance={
            "executable": str(tmp_path / "base-python"),
            "roots": (str(tmp_path),),
            "python_version": (3, 13, 5),
        },
    )
    (value.output / "death-calls.jsonl").write_text(
        json.dumps({"event": "process", "name": "executor", "monotonic": 100.0}) + "\n"
    )
    smoke.write(
        value.output / "death-ready.json",
        {
            "schema": 1,
            "session": value.session,
            "ready": {
                "pid": 90,
                "ready": 100.0,
                "sources": value.sources,
                "provenance": value.provenance,
            },
        },
    )
    assert value.preparation_ready()
    parent = {"status": "available", "pid": 10}

    def record(pid: int) -> Json:
        return {
            "requested_pid": pid,
            "process_id": pid,
            "creation_100ns": 1234 + pid,
            "image_path": "C:\\Python\\python.exe",
            "status": "available",
            "errors": [],
            "parent": parent,
        }

    profile = {
        "request": {"pids": [11]},
        "roles": {"executor": 11},
        "records": {"processes": [record(11)], "cleanup_errors": []},
    }
    identity = {"pid": 12, "epoch": "b" * 32}
    temporary = {
        "roles": {"peer": 12},
        "records": {"processes": [record(12)], "cleanup_errors": []},
    }
    reference = {
        "schema": 1,
        "session": value.session,
        "fixture": {"pid": 30},
        "expected": {"rect": [0, 0, 512, 512]},
        "reference_initial": {"input_tick": 7},
        "before_rgb_sha256": "c" * 64,
        "before_rgb_byte_count": 1728000,
    }
    smoke.write(executor / "death-reference.json", reference)
    now[0] = 101.0
    assert not value.before_observe(profile, temporary, identity, 115.0)
    # Use the real producer's detached records, not a separately guessed schema.
    collector = object.__new__(topology.PinnedProcesses)
    collector.collected = collector.closed = False
    collector.pins = [
        topology.Pin(
            requested_pid=pid,
            process_id=pid,
            creation_100ns=1234 + pid,
            image_path="C:\\Python\\python.exe",
            status="available",
        )
        for pid in (11, 12)
    ]
    collector.journal = Mock()
    collector.journal.call.side_effect = lambda _name, operation: operation()
    monkeypatch.setattr(collector, "_recheck", lambda _pin: None)

    def enumerate_parents(metadata: Json) -> dict[int, int]:
        metadata.update(entries=4, complete=True)
        return {11: 10, 12: 10}

    monkeypatch.setattr(collector, "_enumerate", enumerate_parents)
    pins = {
        "roles": {"executor": 11, "peer": 12},
        "records": collector.collect(),
        "reference_initial": reference["reference_initial"],
        "pinned_ns": 101_000_000_000,
    }
    smoke.write(
        value.output / "death-pinned.json",
        {"schema": 1, "session": value.session, "pinned": pins},
    )
    assert value.before_observe(profile, temporary, identity, 115.0)
    hold = {
        "session": value.session,
        "generation": 1,
        "observe_called_ns": 101_000_000_000,
        "token": {
            "credit_schema": 1,
            "epoch": "b" * 32,
            "frame_id": 1,
            "chunk_index": 0,
            "offset": 0,
            "count": 65536,
        },
        "wait_received_ns": 101_100_000_000,
        "no_credit_issued": True,
        "no_cancel_issued": True,
        "harness_sha256": value.sources["installed_observe_smoke.py"],
    }
    now[0] = 101.2
    return SimpleNamespace(
        value=value,
        owner=owner,
        gui=gui,
        now=now,
        hold=hold,
        profile=profile,
        temporary=temporary,
        identity=identity,
        pins=pins,
    )


def publish_hold(prepared: SimpleNamespace) -> None:
    """Publish the fixed held token with no GUI terminal or finally receipt."""
    value = prepared.value
    smoke.write(
        value.executor / "death-held.json",
        {"schema": 1, "session": value.session, "hold": prepared.hold},
    )


def result_for(prepared: SimpleNamespace) -> Json:
    """Construct expected same-handle successful evidence, not native qualification."""
    value = prepared.value
    records = []
    for original in prepared.pins["records"]["processes"]:
        records.append(  # noqa: PERF401 - explicit identity fixture
            {
                **original,
                "status": "retired",
                "observed_process_id": original["process_id"],
                "observed_creation_100ns": original["creation_100ns"],
                "identity_unchanged": True,
                "signaled": True,
                "retired": True,
                "wait_result": 0,
                "exit_code": 17 if original["process_id"] == 11 else 1,
            }
        )
    return {
        "passed": True,
        "failure": None,
        "cleanup_errors": [],
        "completed_ns": 101_500_000_000,
        "provenance": value.provenance,
        "sources": value.sources,
        "begin": value.begin,
        "request": value.request,
        "retirement_deadline_ns": int(value.retirement_deadline * 1e9),
        "action": {
            "attempted": True,
            "returned": True,
            "before_ns": 101_300_000_000,
            "after_ns": 101_400_000_000,
        },
        "retirement": {"retired": True, "cleanup_errors": [], "processes": records},
        "reference": {
            "root_sha256": "c" * 64,
            "panel_sha256": "d" * 64,
            "root_bytes": 1728000,
            "panel_bytes": 786432,
            "exact_equal": True,
        },
    }


def arrange_result(prepared: SimpleNamespace, fault: str | None = None) -> None:  # noqa: C901 - proof fault matrix
    """Make an external witness complete only after the one kill request exists."""
    value = prepared.value

    def snapshot() -> Json:  # noqa: C901 - independent proof fault matrix
        assert value.request is not None
        result = result_for(prepared)
        if fault == "cleanup":
            result["cleanup_errors"] = ["close failed"]
        elif fault == "uncertain":
            result["action"]["returned"] = None
        elif fault == "late-action":
            result["action"]["after_ns"] = 103_000_000_000
        elif fault == "late-result":
            result["completed_ns"] = 107_000_000_000
        elif fault == "drift":
            result["reference"]["root_sha256"] = "e" * 64
        elif fault == "wrong-exit":
            result["retirement"]["processes"][0]["exit_code"] = 0
        elif fault == "changed-pin":
            result["retirement"]["processes"][1]["observed_creation_100ns"] += 1
        elif fault == "missing-pin":
            result["retirement"]["processes"].pop()
        elif fault == "extra-pin":
            result["retirement"]["processes"].append(
                deepcopy(result["retirement"]["processes"][0])
            )
        elif fault == "source":
            result["sources"] = {}
        smoke.write(
            value.output / "death-result.json",
            {"schema": 1, "session": value.session, "result": result},
        )
        prepared.now[0] = max(prepared.now[0], 101.6)
        prepared.gui.snapshot.return_value = {
            "exit_code": 17,
            "members": [] if fault != "gui-live" else [12],
        }
        return {"exit_code": 0, "members": [] if fault != "witness-live" else [90]}

    prepared.owner.snapshot.side_effect = snapshot


def test_natural_death_state_path_preserves_open_owners(
    prepared: SimpleNamespace,
) -> None:
    """Only both natural empty jobs plus validated proof return expected containment."""
    publish_hold(prepared)
    arrange_result(prepared)
    result = prepared.value.finish_if_held()
    assert result["passed"] is True
    assert result["live_gui"]["snapshot"]["members"] == [11, 12]
    assert result["gui"] == {"exit_code": 17, "members": []}
    assert result["witness"] == {"exit_code": 0, "members": []}
    assert prepared.value.retirement_deadline == 106.2
    assert not (prepared.value.executor / "result.json").exists()
    prepared.owner.api.terminate.assert_not_called()
    prepared.gui.api.terminate.assert_not_called()
    prepared.owner.close.assert_not_called()
    prepared.gui.close.assert_not_called()
    with pytest.raises(RuntimeError, match="Repeated"):
        prepared.value.finish_if_held()


@pytest.mark.parametrize(
    "fault",
    [
        "cleanup",
        "uncertain",
        "late-action",
        "late-result",
        "drift",
        "wrong-exit",
        "changed-pin",
        "missing-pin",
        "extra-pin",
        "source",
        "witness-live",
    ],
)
def test_bad_final_proof_never_requests_fallback(
    prepared: SimpleNamespace, fault: str
) -> None:
    """Missing, substituted, late and uncertain evidence fails permanently."""
    publish_hold(prepared)
    arrange_result(prepared, fault)
    with pytest.raises((ValueError, RuntimeError)):
        prepared.value.finish_if_held()
    assert prepared.value.failed
    prepared.owner.api.terminate.assert_not_called()
    prepared.gui.api.terminate.assert_not_called()
    with pytest.raises(RuntimeError, match="Repeated"):
        prepared.value.finish_if_held()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation", True),
        ("no_credit_issued", False),
        ("no_cancel_issued", False),
        ("harness_sha256", "0" * 64),
        ("observe_called_ns", 99_000_000_000),
        ("session", "wrong"),
    ],
)
def test_bad_hold_never_emits_kill(
    prepared: SimpleNamespace, field: str, value: object
) -> None:
    """A malformed or late first-wait receipt cannot create a death request."""
    prepared.hold[field] = value
    publish_hold(prepared)
    with pytest.raises(ValueError, match=r".+"):
        prepared.value.finish_if_held()
    assert not (prepared.value.output / "death-kill-request.json").exists()


@pytest.mark.parametrize("members", [[11], [11, 12, 13], [11, 11, 12]])
def test_membership_change_rejects_before_kill(
    prepared: SimpleNamespace, members: list[int]
) -> None:
    """Missing, extra and duplicate job members cannot preserve the pinned phase."""
    publish_hold(prepared)
    prepared.gui.snapshot.return_value = {"exit_code": None, "members": members}
    with pytest.raises((ValueError, RuntimeError)):
        prepared.value.finish_if_held()
    assert not (prepared.value.output / "death-kill-request.json").exists()


def test_late_live_snapshot_cannot_issue_kill(prepared: SimpleNamespace) -> None:
    """The two-second hold admission is checked again after the owner snapshot."""
    publish_hold(prepared)

    def snapshot() -> Json:
        prepared.now[0] = 103.0
        return {"exit_code": None, "members": [11, 12]}

    prepared.gui.snapshot.side_effect = snapshot
    with pytest.raises(ValueError, match="timing"):
        prepared.value.finish_if_held()
    assert not (prepared.value.output / "death-kill-request.json").exists()


def test_no_hold_and_early_witness_exit_cannot_pass(prepared: SimpleNamespace) -> None:
    """No hold is ordinary pending work, but early witness exit is terminal failure."""
    assert prepared.value.finish_if_held() is None
    prepared.owner.snapshot.return_value = {"exit_code": 0, "members": []}
    with pytest.raises(RuntimeError, match="without held"):
        prepared.value.finish_if_held()


def test_blocked_witness_call_is_rejected(prepared: SimpleNamespace) -> None:
    """Real journal parsing enforces the unchanged three-second native-call cap."""
    path = prepared.value.output / "death-calls.jsonl"
    with path.open("a") as stream:
        stream.write(
            json.dumps({"event": "call_start", "name": "native", "monotonic": 101.0})
            + "\n"
        )
    prepared.now[0] = 104.0
    with pytest.raises(RuntimeError, match="blocked"):
        prepared.value.finish_if_held()


def test_bounded_packet_rejects_duplicate_extra_and_oversized(tmp_path: Path) -> None:
    """No duplicate field replacement or unbounded read enters control metadata."""
    path = tmp_path / "packet.json"
    for raw in [
        b'{"schema":1,"schema":1,"session":"s","hold":{}}',
        b'{"schema":1,"session":"s","hold":{},"extra":1}',
        b"x" * 131073,
    ]:
        path.write_bytes(raw)
        with pytest.raises(ValueError, match=r".+"):
            control.read_packet(path, "s", "hold")


def test_gui_job_must_empty_before_original_retirement_cap(
    prepared: SimpleNamespace,
) -> None:
    """A clean witness alone cannot turn a live peer or launcher into success."""
    publish_hold(prepared)
    arrange_result(prepared, "gui-live")
    with pytest.raises(RuntimeError, match="deadline"):
        prepared.value.finish_if_held()
    assert prepared.now[0] >= prepared.value.retirement_deadline
    prepared.gui.api.terminate.assert_not_called()
    prepared.gui.close.assert_not_called()


def test_preparation_ready_cache_does_not_reset_case_clock(
    prepared: SimpleNamespace,
) -> None:
    """Repeated readiness checks after the single transition do not reapply prep cap."""
    prepared.now[0] = 111.0
    assert prepared.value.preparation_ready()
    assert prepared.value.deadline == 115.0


def test_case_handoff_admitted_once_before_reference(prepared: SimpleNamespace) -> None:
    """Late case progress cannot re-run the preparation-only admission check."""
    value = prepared.value
    value.begin = value.pinned = None
    smoke.write(
        value.executor / "case-start.json",
        {
            "schema": 1,
            "session": value.session,
            "case_start": 101.0,
            "deadline": 116.0,
        },
    )
    assert value.finish_if_held() is None
    prepared.now[0] = 111.0
    assert value.finish_if_held() is None
    assert value.deadline == 116.0


def test_post_result_watchdog_reread_rejects_late_journal(
    prepared: SimpleNamespace,
) -> None:
    """A final native failure written after the prior poll cannot escape admission."""
    publish_hold(prepared)
    arrange_result(prepared)
    original = prepared.value._admit_result

    def admit(result: Json, until: int) -> None:
        original(result, until)
        with (prepared.value.output / "death-calls.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"event": "call_start", "name": "late", "monotonic": 101.5})
                + "\n"
            )

    prepared.value._admit_result = admit
    with pytest.raises(RuntimeError, match="incomplete"):
        prepared.value.finish_if_held()


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("complete", False),
        ("error", "failed"),
        ("entries", True),
        ("entries", 4097),
        ("started_monotonic", 97.0),
        ("finished_monotonic", 102.0),
    ],
)
def test_pin_enumeration_is_retained_and_bounded(
    prepared: SimpleNamespace, field: str, bad: object
) -> None:
    """Actual collect schema remains visible and incomplete/late snapshots fail."""
    prepared.pins["records"]["enumeration"][field] = bad
    value = prepared.value
    smoke.write(
        value.output / "death-pinned.json",
        {"schema": 1, "session": value.session, "pinned": prepared.pins},
    )
    with pytest.raises(ValueError, match="enumeration"):
        value.before_observe(
            prepared.profile, prepared.temporary, prepared.identity, 115.0
        )


@pytest.mark.parametrize("fault", ["missing", "source", "late", "pid", "early-exit"])
def test_readiness_requires_exact_live_prepared_witness(
    prepared: SimpleNamespace, fault: str
) -> None:
    """Neither wrong provenance nor premature/late readiness admits a handoff."""
    value = prepared.value
    value.ready = None
    value.deadline = value.preparation_deadline
    path = value.output / "death-ready.json"
    ready = smoke.read(path)
    if fault == "missing":
        path.unlink()
        assert not value.preparation_ready()
        return
    if fault == "source":
        ready["ready"]["sources"] = {}
    elif fault == "pid":
        ready["ready"]["pid"] = 91
    elif fault == "late":
        prepared.now[0] = 110.0
    else:
        prepared.owner.snapshot.return_value = {"exit_code": 0, "members": []}
    smoke.write(path, ready)
    with pytest.raises((ValueError, RuntimeError)):
        value.preparation_ready()


def test_fixed_launch_and_owner_registration(prepared: SimpleNamespace) -> None:
    """One owned fixed base interpreter/script launches, never a selectable module."""
    assert prepared.value.owners["death-witness"] is prepared.owner
    command = owned_helper.HelperCommand
    assert isinstance(command, Mock)
    args = command.call_args.args
    assert args[0] == prepared.value.provenance["executable"]
    assert args[1][:3] == ("-I", "-S", "-c")
    assert args[1][3] == helper_bootstrap.BOOTSTRAP_SOURCE + control._WITNESS_SUFFIX
    assert Path(args[1][5]).name == "observe_death_witness.py"
    prepared.owner.start.assert_called_once()


@pytest.mark.parametrize(
    "fault",
    ["result-extra", "retirement-extra", "reference-extra", "bad-code", "bad-digest"],
)
def test_independent_result_schema_rejects_surplus_and_bad_status(
    prepared: SimpleNamespace, fault: str
) -> None:
    """Even an otherwise successful native report must match the fixed schema."""
    value = prepared.value
    value.request = {
        "schema": 1,
        "session": value.session,
        "requested_ns": 101_200_000_000,
        "hold": prepared.hold,
    }
    value.retirement_deadline = 106.2
    result = result_for(prepared)
    prepared.now[0] = 101.6
    if fault == "result-extra":
        result["extra"] = True
    elif fault == "retirement-extra":
        result["retirement"]["extra"] = True
    elif fault == "reference-extra":
        result["reference"]["extra"] = True
    elif fault == "bad-code":
        result["retirement"]["processes"][1]["exit_code"] = True
    else:
        result["reference"]["panel_sha256"] = "z" * 64
    with pytest.raises(ValueError, match=r".+"):
        value._admit_result(result, 106_200_000_000)


@pytest.mark.parametrize("fault", ["changed", "early-exit", "late-pins", "unready"])
def test_before_observe_cannot_admit_changed_or_unready_pins(
    prepared: SimpleNamespace, fault: str
) -> None:
    """The original pre-observe handoff remains fail-closed on every poll."""
    value = prepared.value
    if fault == "changed":
        prepared.identity = {**prepared.identity, "pid": 99}
    elif fault == "early-exit":
        prepared.owner.snapshot.return_value = {"exit_code": 0, "members": []}
    elif fault == "unready":
        value.ready = None
    else:
        prepared.pins["pinned_ns"] = 102_000_000_000
        smoke.write(
            value.output / "death-pinned.json",
            {"schema": 1, "session": value.session, "pinned": prepared.pins},
        )
    with pytest.raises((ValueError, RuntimeError)):
        value.before_observe(
            prepared.profile, prepared.temporary, prepared.identity, 115.0
        )


def test_failure_context_retains_rejected_membership_without_new_queries(
    prepared: SimpleNamespace,
) -> None:
    """Fallback cannot replace the exact membership snapshot that failed admission."""
    publish_hold(prepared)
    prepared.gui.snapshot.return_value = {"exit_code": None, "members": [11, 12, 99]}
    with pytest.raises(RuntimeError, match="membership"):
        prepared.value.finish_if_held()
    calls = prepared.gui.snapshot.call_count
    saved = prepared.value.failure_context()
    assert saved["last_membership"]["snapshot"]["members"] == [11, 12, 99]
    assert prepared.gui.snapshot.call_count == calls
    saved["last_membership"]["snapshot"]["members"].clear()
    assert prepared.value.last_membership["snapshot"]["members"] == [11, 12, 99]


def test_failed_production_bootstrap_never_dispatches_witness(tmp_path: Path) -> None:
    """The real isolated prefix rejects bad attestation before fixture-only runpy."""
    marker = tmp_path / "witness-ran"
    witness = tmp_path / "observe_death_witness.py"
    witness.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    result = subprocess.run(  # noqa: S603 - fixed isolated prefix and owned local fixture
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            helper_bootstrap.BOOTSTRAP_SOURCE + control._WITNESS_SUFFIX,
            '{"schema_version":3}',
            str(witness),
        ],
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert b"Unexpected isolated interpreter identity" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("phase", ["inspection", "launch"])
def test_bootstrap_and_launch_bracket_original_preparation_deadline(
    prepared: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Slow attestation cannot start late; late launch stays owned for outer cleanup."""
    runtime = helper_runtime.inspect_runtime()
    owner = Mock()
    output = prepared.value.output.parent / "second"
    output.mkdir()
    owners = {"fixture": prepared.value.owners["fixture"]}
    if phase == "inspection":

        def inspect() -> helper_runtime.RuntimeManifest:
            prepared.now[0] = 110.0
            return runtime

        monkeypatch.setattr(helper_runtime, "inspect_runtime", inspect)
    else:
        owner.start.side_effect = lambda _: prepared.now.__setitem__(0, 110.0)
    with pytest.raises(RuntimeError, match="Late death"):
        control.DeathControl(
            smoke,
            SimpleNamespace(ExternalOwner=lambda: owner),
            owners,
            output=output,
            executor=output / "executor",
            states=output / "states",
            wheel=output / "fixed.whl",
            session="a" * 32,
            started=100.0,
            provenance=prepared.value.provenance,
        )
    assert owners["death-witness"] is owner
    assert owner.start.call_count == (0 if phase == "inspection" else 1)
    owner.close.assert_not_called()


def test_local_provenance_is_an_independent_wire_snapshot() -> None:
    """Local tuple normalization neither mutates inputs nor weakens equality."""
    original = {"roots": ("installed",), "python_version": (3, 13, 5)}
    actual = control.canonical_provenance(original)
    assert actual == {"roots": ["installed"], "python_version": [3, 13, 5]}
    assert original["roots"] == ("installed",)
    actual["roots"].append("other")
    assert original["roots"] == ("installed",)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_local_provenance_is_rejected(value: float) -> None:
    """The local snapshot never creates nonstandard JSON numeric evidence."""
    with pytest.raises(ValueError, match="Out of range float"):
        control.canonical_provenance({"invalid": value})


def test_local_provenance_bound_and_object_shape() -> None:
    """Even trusted expected metadata remains bounded and object-shaped."""
    with pytest.raises(ValueError, match="bound"):
        control.canonical_provenance({"large": "x" * control.MAX_JSON})
    with pytest.raises(ValueError, match="object"):
        control.canonical_provenance([])  # ty: ignore[invalid-argument-type]


def test_live_pinned_schema_rejects_invented_cleanup_field(
    prepared: SimpleNamespace,
) -> None:
    """A collect packet cannot masquerade as later close or retirement evidence."""
    value = prepared.value
    prepared.pins["records"]["cleanup_errors"] = []
    smoke.write(
        value.output / "death-pinned.json",
        {"schema": 1, "session": value.session, "pinned": prepared.pins},
    )
    with pytest.raises(ValueError, match="fields differ"):
        value.before_observe(
            prepared.profile, prepared.temporary, prepared.identity, 115.0
        )
