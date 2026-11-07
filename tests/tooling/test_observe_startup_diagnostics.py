"""Portable failure-only startup evidence tests, never native capture proof."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke

from verdiclip.platform import owned_helper

Json = dict[str, Any]
SESSION = "a" * 32
BASE = r"C:\verified-runtime\python.exe"


def failure_context() -> Json:
    """The original rejected snapshot includes every guard input and timestamp."""
    return {
        "schema": 1,
        "session": SESSION,
        "snapshot": {"exit_code": None, "members": [11, 12, 13, 45, 44]},
        "snapshot_started": 6.8,
        "snapshot_finished": 6.9,
        "baseline": [11, 12, 13],
        "provisional_before": [44],
        "provisional_union": [44, 45],
        "peer_pid": 44,
        "completed_phases": ["pre-reference"],
        "preparation_start": 1.0,
        "case_deadline": 20.0,
        "absolute_deadline": 25.0,
        "failure_time": 7.0,
    }


@dataclass(frozen=True)
class Command:
    """Portable command boundary; no Windows process is ever started by tests."""

    executable: str
    arguments: tuple[str, ...]
    directory: str


@dataclass
class Startup:
    """Keep the diagnostic's independent native, timing, and file seams visible."""

    output: Path
    executor: Path
    script: Path
    sidecar: Path
    context: Json
    clock: list[float]
    gui: Mock
    owner: Mock
    factory: Mock
    command: Mock
    diagnose: Mock
    module: ModuleType
    owners: dict[str, Any]

    def run(self, case_deadline: float = 20.0, absolute_deadline: float = 25.0) -> Json:
        """Call the real failure-only coordinator, not a duplicate admission model."""
        return smoke.diagnose_startup_failure(
            self.output,
            self.executor,
            SESSION,
            self.context,
            self.gui,
            self.module,
            self.owners,
            {"executable": BASE},
            self.output / "app.whl",
            case_deadline,
            absolute_deadline,
        )


@pytest.fixture
def startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Startup:
    """Stub only native launch and the separate topology executor interaction."""
    executor = tmp_path / "executor"
    executor.mkdir()
    script = tmp_path / "installed_observe_smoke.py"
    sidecar = script.with_name("observe_process_diagnostics.py")
    script.write_bytes(b"fixed authored harness bytes, never executed")
    sidecar.write_bytes(b"fixed authored sidecar bytes, never executed")
    clock = [7.5]
    gui, owner = Mock(), Mock()
    factory = Mock(return_value=owner)
    command = Mock(side_effect=Command)
    diagnose = Mock(return_value={"final": {"processes": []}})
    module = ModuleType("startup_diagnostic_test_support")
    module.__dict__["ExternalOwner"] = factory
    monkeypatch.setattr(smoke, "__file__", str(script))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke, "diagnose_topology", diagnose)
    monkeypatch.setattr(owned_helper, "HelperCommand", command)
    return Startup(
        tmp_path,
        executor,
        script,
        sidecar,
        failure_context(),
        clock,
        gui,
        owner,
        factory,
        command,
        diagnose,
        module,
        {"gui": gui},
    )


def test_original_failure_context_is_detached_exact_and_published_once(
    tmp_path: Path,
) -> None:
    """Later mutations cannot rewrite the original snapshot or guard evidence."""
    context = failure_context()
    expected = deepcopy(context)
    primary = RuntimeError("original membership rejected")
    result: Json = {"passed": False, "first_failure": None}
    smoke.retain_membership_failure(tmp_path, result, context, primary)
    path = tmp_path / "membership-failure.json"
    original = path.read_bytes()
    assert result["first_failure"] == repr(primary)
    assert result["membership_failure"] == expected
    assert smoke.read(path) == expected
    context["snapshot"]["members"].clear()
    context["completed_phases"].append("pre-observe")
    context["provisional_union"].append(99)
    assert result["membership_failure"] == expected
    result["membership_failure"]["snapshot"]["members"].clear()
    assert path.read_bytes() == original
    assert result["passed"] is False


@pytest.mark.parametrize(
    "failure", [OSError("disk unavailable"), RuntimeError("x" * 10000)]
)
def test_persistence_failure_never_replaces_original_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception
) -> None:
    """The primary failure and frozen context survive evidence write failure."""
    context = failure_context()
    primary = RuntimeError("original rejection")
    result: Json = {"passed": False}
    write = Mock(side_effect=failure)
    monkeypatch.setattr(smoke, "write_handshake", write)
    smoke.retain_membership_failure(tmp_path, result, context, primary)
    assert result["first_failure"] == repr(primary)
    assert result["membership_failure"] == context
    assert result["membership_failure_persistence_error"] == repr(failure)[:2048]
    assert result["passed"] is False
    write.assert_called_once_with(tmp_path / "membership-failure.json", context)


def test_existing_failure_receipt_cannot_be_overwritten(tmp_path: Path) -> None:
    """A duplicate receipt reports persistence failure without replacing bytes."""
    path = tmp_path / "membership-failure.json"
    path.write_bytes(b"original immutable evidence")
    result: Json = {"passed": False}
    primary = RuntimeError("new diagnostic cannot replace primary")
    smoke.retain_membership_failure(tmp_path, result, failure_context(), primary)
    assert path.read_bytes() == b"original immutable evidence"
    assert result["first_failure"] == repr(primary)
    assert "Duplicate" in result["membership_failure_persistence_error"]


def test_startup_uses_only_exact_original_extra_pids_and_fixed_command(
    startup: Startup,
) -> None:
    """The verified base, isolation flags, fixed scripts, and hashes are explicit."""
    frozen = deepcopy(startup.context)
    result = startup.run()
    diagnostic = startup.output / "startup-diagnostics"
    assert result["status"] == "completed"
    assert result["deadline"] == 10.0
    assert "not identity proof at original failure" in result["scope"]
    assert startup.context == frozen
    request = smoke.read(diagnostic / "topology-request.json")
    assert request == {
        "schema": 1,
        "session": SESSION,
        "pids": [44, 45],
        "original_snapshot_time": 6.9,
        "deadline": 10.0,
    }
    command = startup.owner.start.call_args.args[0]
    assert isinstance(command, Command)
    assert command.executable == BASE
    assert command.directory == str(diagnostic)
    assert command.arguments == (
        "-I",
        "-S",
        str(startup.script),
        "--topology-child",
        "--wheel",
        str(startup.output / "app.whl"),
        "--output",
        str(diagnostic),
        "--session",
        SESSION,
        "--diagnostic-deadline",
        "10.0",
        "--diagnostic-harness-sha",
        hashlib.sha256(startup.script.read_bytes()).hexdigest(),
        "--diagnostic-sidecar-sha",
        hashlib.sha256(startup.sidecar.read_bytes()).hexdigest(),
    )
    assert startup.owners == {"gui": startup.gui, "startup-diagnostic": startup.owner}
    assert startup.diagnose.call_args.args == (
        diagnostic,
        SESSION,
        {"members": [44, 45]},
        6.9,
        startup.gui,
        7.0,
        10.0,
    )
    assert startup.diagnose.call_args.kwargs["request_written"] is True
    startup.gui.snapshot.assert_not_called()
    assert not (startup.executor / "ownership-pre-observe-ack.json").exists()
    assert not (startup.executor / "case-start.json").exists()


def test_original_gui_watchdog_is_preserved_inside_diagnostic_monitor(
    monkeypatch: pytest.MonkeyPatch, startup: Startup
) -> None:
    """A bounded extra-process query cannot suspend the original executor watchdog."""
    monitor = Mock()
    monkeypatch.setattr(smoke, "validate_calls", monitor)
    assert startup.run()["status"] == "completed"
    startup.diagnose.call_args.kwargs["monitor"]()
    monitor.assert_called_once_with(
        startup.executor / "calls.jsonl",
        1.0,
        smoke.time.monotonic,
        finished=False,
        deadline=10.0,
    )


@pytest.mark.parametrize(
    "phases",
    [
        ["pre-observe"],
        ["post-terminal"],
        ["pre-reference", "pre-observe"],
        ["pre-reference", "pre-observe", "post-terminal"],
    ],
)
def test_later_phase_failures_are_retention_only(
    startup: Startup, phases: list[str]
) -> None:
    """No diagnostic process may be started once any capture-stage phase completed."""
    startup.context["completed_phases"] = phases
    result = startup.run()
    assert result["status"] == "unavailable"
    assert result["reason"] == "Later-phase failure is retention only"
    startup.factory.assert_not_called()
    startup.command.assert_not_called()
    startup.diagnose.assert_not_called()
    assert not (startup.output / "startup-diagnostics").exists()


def test_existing_pre_observe_ack_blocks_startup_diagnosis(startup: Startup) -> None:
    """An acknowledgment blocks diagnostics before phase evidence catches up."""
    (startup.executor / "ownership-pre-observe-ack.json").write_bytes(
        b"already admitted"
    )
    result = startup.run()
    assert result["status"] == "unavailable"
    assert "acknowledgment already exists" in result["error"]
    startup.factory.assert_not_called()
    startup.diagnose.assert_not_called()


@pytest.mark.parametrize(
    "extra", [[], list(range(100, 109)), [True], [0], [-1], [2**32]]
)
def test_missing_oversized_or_invalid_extra_pid_scope_never_starts_owner(
    startup: Startup, extra: list[int]
) -> None:
    """An empty or out-of-bounds scope cannot become a guessed diagnostic target."""
    startup.context["snapshot"]["members"] = [*startup.context["baseline"], *extra]
    result = startup.run()
    assert result["status"] == "unavailable"
    assert "PID list" in result["error"]
    startup.factory.assert_not_called()
    startup.command.assert_not_called()
    startup.diagnose.assert_not_called()


def test_disappeared_extra_remains_in_original_requested_scope(
    startup: Startup,
) -> None:
    """Later disappearance cannot remove an originally rejected PID from scope."""
    startup.gui.snapshot.return_value = {"exit_code": None, "members": [11, 12, 13]}
    startup.diagnose.return_value = {
        "final": {
            "processes": [
                {"requested_pid": 44, "status": "unavailable"},
                {"requested_pid": 45, "status": "stale"},
            ]
        },
    }
    result = startup.run()
    assert result["status"] == "completed"
    assert startup.diagnose.call_args.args[2] == {"members": [44, 45]}
    assert result["result"] == startup.diagnose.return_value


@pytest.mark.parametrize(
    ("case", "absolute", "expected"),
    [(20.0, 25.0, 10.0), (9.0, 25.0, 9.0), (20.0, 8.0, 8.0)],
)
def test_diagnostic_deadline_is_minimum_without_restarting_case(
    startup: Startup, case: float, absolute: float, expected: float
) -> None:
    """Only the remaining original case, total, and three-second budgets are used."""
    result = startup.run(case, absolute)
    assert result["deadline"] == expected
    assert result["status"] == "completed"
    assert startup.diagnose.call_args.args[-1] == expected


@pytest.mark.parametrize("now", [9.999, 10.0, 10.001])
def test_exact_deadline_blocks_new_owner(startup: Startup, now: float) -> None:
    """A process is never constructed at or after the already fixed diagnostic limit."""
    startup.clock[0] = now
    result = startup.run()
    assert result["status"] == ("completed" if now < 10.0 else "unavailable")
    assert startup.factory.call_count == int(now < 10.0)
    assert startup.owner.start.call_count == int(now < 10.0)


@pytest.mark.parametrize("stage", ["command", "owner", "start", "result"])
def test_late_boundary_does_not_advance_to_the_next_stage(
    startup: Startup, stage: str
) -> None:
    """Slow construction, launch, and result consumption are rejected at exact limit."""
    if stage == "command":

        def command(
            executable: str, arguments: tuple[str, ...], directory: str
        ) -> Command:
            startup.clock[0] = 10.0
            return Command(executable, arguments, directory)

        startup.command.side_effect = command
    elif stage == "owner":

        def owner() -> Mock:
            startup.clock[0] = 10.0
            return startup.owner

        startup.factory.side_effect = owner
    elif stage == "start":

        def start(_command: object) -> None:
            assert startup.owners["startup-diagnostic"] is startup.owner
            startup.clock[0] = 10.0

        startup.owner.start.side_effect = start
    else:

        def result(*_args: object, **_kwargs: object) -> Json:
            startup.clock[0] = 10.0
            return {"later": "evidence"}

        startup.diagnose.side_effect = result
    value = startup.run()
    assert value["status"] == "unavailable"
    assert "Late" in value["error"]
    assert startup.factory.call_count == int(stage != "command")
    assert startup.owner.start.call_count == int(stage in ("start", "result"))
    assert startup.diagnose.call_count == int(stage == "result")
    assert ("startup-diagnostic" in startup.owners) is (stage != "command")


def test_start_failure_keeps_owner_registered_for_external_cleanup(
    startup: Startup,
) -> None:
    """A failed or uncertain native start is not retried and cannot lose its owner."""

    def start(_command: object) -> None:
        assert startup.owners["startup-diagnostic"] is startup.owner
        raise OSError("native startup failed after acquisition")

    startup.owner.start.side_effect = start
    result = startup.run()
    assert result["status"] == "unavailable"
    assert "native startup failed" in result["error"]
    assert startup.owners["startup-diagnostic"] is startup.owner
    startup.owner.start.assert_called_once()
    startup.diagnose.assert_not_called()
    startup.owner.close.assert_not_called()


def test_request_persistence_failure_prevents_process_construction(
    monkeypatch: pytest.MonkeyPatch, startup: Startup
) -> None:
    """No diagnostic process starts before its immutable request is written."""
    monkeypatch.setattr(
        smoke, "write_handshake", Mock(side_effect=OSError("write failed"))
    )
    result = startup.run()
    assert result["status"] == "unavailable"
    assert "write failed" in result["error"]
    startup.factory.assert_not_called()
    startup.owner.start.assert_not_called()


def child_args(startup: Startup) -> argparse.Namespace:
    """Use exact current bytes without executing the fixture source files."""
    return argparse.Namespace(
        output=startup.executor,
        session=SESSION,
        diagnostic_deadline=10.0,
        diagnostic_harness_sha=hashlib.sha256(startup.script.read_bytes()).hexdigest(),
        diagnostic_sidecar_sha=hashlib.sha256(startup.sidecar.read_bytes()).hexdigest(),
    )


@pytest.mark.parametrize("failure", [None, "collect-error", "late-journal"])
def test_topology_child_only_collects_fixed_metadata_and_always_closes_journal(
    monkeypatch: pytest.MonkeyPatch, startup: Startup, failure: str | None
) -> None:
    """A verified child never dispatches GUI/provider paths and closes on failure."""
    journals: list[smoke.Journal] = []
    journal_type = smoke.Journal

    def journal(path: Path, deadline: float) -> smoke.Journal:
        value = journal_type(path, deadline)
        journals.append(value)
        if failure == "late-journal":
            startup.clock[0] = 10.0
        return value

    monkeypatch.setattr(smoke, "Journal", journal)
    collector = Mock(
        side_effect=OSError("pin failure") if failure == "collect-error" else None
    )
    monkeypatch.setattr(smoke, "collect_topology", collector)
    forbidden = Mock(side_effect=AssertionError("capture path reached"))
    for name in ("child", "outer", "reference", "support"):
        monkeypatch.setattr(smoke, name, forbidden)
    if failure is None:
        assert smoke.topology_child(child_args(startup)) == 0
    else:
        with pytest.raises((OSError, RuntimeError), match=r"pin failure|deadline"):
            smoke.topology_child(child_args(startup))
    assert len(journals) == 1
    assert journals[0].stream.closed
    assert collector.call_count == int(failure != "late-journal")
    if failure != "late-journal":
        collector.assert_called_once_with(startup.executor, SESSION, journals[0])
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "bad",
    [
        "harness-hash",
        "sidecar-hash",
        "changed-harness",
        "changed-sidecar",
        "nan",
        "infinity",
        "exact-deadline",
    ],
)
def test_topology_child_refuses_bad_source_or_deadline_before_journal(
    monkeypatch: pytest.MonkeyPatch, startup: Startup, bad: str
) -> None:
    """Source identity and finite time are checked before any diagnostic collection."""
    args = child_args(startup)
    if bad == "harness-hash":
        args.diagnostic_harness_sha = "0" * 64
    elif bad == "sidecar-hash":
        args.diagnostic_sidecar_sha = "0" * 64
    elif bad == "changed-harness":
        startup.script.write_bytes(b"changed after arguments frozen")
    elif bad == "changed-sidecar":
        startup.sidecar.write_bytes(b"changed after arguments frozen")
    elif bad == "nan":
        args.diagnostic_deadline = float("nan")
    elif bad == "infinity":
        args.diagnostic_deadline = float("inf")
    else:
        startup.clock[0] = 10.0
    journal, collector = Mock(), Mock()
    monkeypatch.setattr(smoke, "Journal", journal)
    monkeypatch.setattr(smoke, "collect_topology", collector)
    with pytest.raises(RuntimeError, match="identity or deadline"):
        smoke.topology_child(args)
    journal.assert_not_called()
    collector.assert_not_called()


def test_outer_latches_rejection_before_diagnostics_and_retains_tight_cleanup(  # noqa: PLR0915 - complete fixed outer failure lifecycle
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The outer failure path cannot authorize capture or replace the first error."""
    fixture_exe = tmp_path / "fixture.exe"
    fixture_exe.write_bytes(b"fixture stub, never executed")
    args = argparse.Namespace(
        fixture=fixture_exe, wheel=tmp_path / "app.whl", output=tmp_path / "out"
    )
    fixture, gui, diagnostic, module = Mock(), Mock(), Mock(), Mock()
    fixture.process.pid = 321
    fixture.snapshot.return_value = {"exit_code": None, "members": [321]}
    gui.process.pid = 654
    rejected: Json = {"exit_code": None, "members": [654, 777, 778]}
    gui.snapshot.side_effect = [{"exit_code": None, "members": [654]}, rejected]
    module.ExternalOwner.side_effect = [fixture, gui, diagnostic]
    module.verify_wheel.return_value = {"executable": BASE}
    profile = {"request": {"pids": [654], "expected_base": BASE}}
    monkeypatch.setattr(smoke, "qualify_ownership", Mock(return_value=profile))
    monkeypatch.setattr(
        smoke,
        "preparation_receipt",
        Mock(return_value={"ready": 1.0, "executor_pid": 654}),
    )
    checkpoints = Mock(return_value={"phase": "pre-reference"})
    monkeypatch.setattr(smoke, "serve_checkpoint", checkpoints)
    monkeypatch.setattr(smoke, "support", lambda: module)
    monkeypatch.setattr(owned_helper, "HelperCommand", Command)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(smoke.time, "sleep", lambda _duration: None)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: SimpleNamespace(hex=SESSION))

    def gui_start(_command: object) -> None:
        executor = args.output / "executor"
        smoke.write(executor / "preparation-ready.json", {"stub": True})
        (executor / "calls.jsonl").write_text(
            json.dumps({"event": "process", "name": "executor", "monotonic": 1.0})
            + "\n"
        )

    gui.start.side_effect = gui_start

    def inspect(*_args: object, **kwargs: Any) -> Json:  # noqa: ANN401 - inspect callback protocol includes callable monitor
        frozen = smoke.read(args.output / "membership-failure.json")
        assert frozen["snapshot"] == {"exit_code": None, "members": [654, 777, 778]}
        assert frozen["completed_phases"] == ["pre-reference"]
        assert frozen["provisional_before"] == []
        assert frozen["provisional_union"] == [777, 778]
        assert frozen["failure_time"] == 1.0
        assert frozen["case_deadline"] == 16.0
        assert (args.output / "executor" / "case-start.json").exists()
        assert not (
            args.output / "executor" / "ownership-pre-observe-ack.json"
        ).exists()
        request = smoke.read(
            args.output / "startup-diagnostics" / "topology-request.json"
        )
        assert request["pids"] == [777, 778]
        kwargs["monitor"]()
        rejected["members"].clear()
        raise OSError("later inspection failed")

    inspect_mock = Mock(side_effect=inspect)
    monkeypatch.setattr(smoke, "diagnose_topology", inspect_mock)
    cleanup = Mock(return_value={"confirmed": True, "forced": False, "errors": []})
    monkeypatch.setattr(smoke, "release_owner", cleanup)
    assert smoke.outer(args) == 1
    result = smoke.read(args.output / "receipt.json")
    assert result["passed"] is False
    assert result["first_failure"] == repr(
        RuntimeError("Unexpected process during the attested helper lifecycle")
    )
    assert result["membership_failure"]["snapshot"]["members"] == [654, 777, 778]
    assert result["startup_diagnostics"]["status"] == "unavailable"
    assert "later inspection failed" in result["startup_diagnostics"]["error"]
    diagnostic.start.assert_called_once()
    inspect_mock.assert_called_once()
    checkpoints.assert_called_once()
    assert checkpoints.call_args.args[2] == "pre-reference"
    assert [call.args for call in cleanup.call_args_list] == [
        (diagnostic, 4.0),
        (gui, 16.0),
        (fixture, 16.0),
    ]
    assert not (args.output / "executor" / "ownership-pre-observe-ack.json").exists()
