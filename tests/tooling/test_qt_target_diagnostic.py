"""Target-aware phase and deadline proofs; no debugger or native process runs."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import qt_target_diagnostic as target


@pytest.fixture
def nodes() -> list[str]:
    """Use exactly the published fixed prefix."""
    return target.validate_inputs(target.ROOT)


def admitted(nodes: list[str], started: object = 10.0) -> dict[str, Any]:
    """Build complete independent prefix evidence for parser tests."""
    return {
        "epoch": "epoch",
        "source_sha256": target.digest(
            target.ROOT / "scripts/qt_hang_diagnostic/inputs.json"
        ),
        "nodeid": nodes[-1],
        "index": 157,
        "started": started,
        "prefix": [
            {
                "nodeid": node,
                "phases": {"setup": "passed", "call": "passed", "teardown": "passed"},
            }
            for node in nodes[:-1]
        ],
    }


def test_exact_ledger_and_target_teardown(nodes: list[str], tmp_path: Path) -> None:
    """Target starts only after156 complete successful node lifecycles."""
    ledger = target.TargetLedger(nodes, "epoch", tmp_path)
    for node in nodes[:-1]:
        ledger.start(node, 0.0)
        for phase in ("setup", "call", "teardown"):
            ledger.report(node, phase, "passed", 0.0)
    ledger.start(nodes[-1], 10.0)
    value, identity = target.target_snapshot(tmp_path / "target.json")
    assert value == admitted(nodes)
    window = target.TargetWindow(0.0, "epoch", nodes)
    window.admit(value, identity, 10.0)
    for phase in ("setup", "call"):
        ledger.report(nodes[-1], phase, "passed", 11.0)
    assert not (tmp_path / "target-done.json").exists()
    ledger.report(nodes[-1], "teardown", "passed", 12.0)
    window.completed(target.read_json(tmp_path / "target-done.json"), 12.0)
    records = [
        json.loads(line)
        for line in (tmp_path / "phases.jsonl").read_text().splitlines()
    ]
    assert len(records) == target.MAX_EVENTS == 628
    assert [row["sequence"] for row in records] == list(range(1, 629))
    assert all(
        row["epoch"] == "epoch" and row["source_sha256"] == ledger.source_sha256
        for row in records
    )
    before = (tmp_path / "phases.jsonl").read_bytes()
    with pytest.raises(ValueError, match="reordered"):
        ledger.start(nodes[-1], 13)
    assert (tmp_path / "phases.jsonl").read_bytes() == before


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_prefix_phase_failure_prevents_target(
    nodes: list[str], tmp_path: Path, phase: str
) -> None:
    """No failed prefix phase can produce target admission evidence."""
    ledger = target.TargetLedger(nodes, "epoch", tmp_path)
    ledger.start(nodes[0], 0.0)
    for earlier in ("setup", "call", "teardown"):
        if earlier == phase:
            break
        ledger.report(nodes[0], earlier, "passed", 0.0)
    with pytest.raises(ValueError, match="did not pass"):
        ledger.report(nodes[0], phase, "failed", 0.0)
    assert target.read_json(tmp_path / "phase-failure.json")["phase"] == phase
    assert not (tmp_path / "target.json").exists()


@pytest.mark.parametrize(
    ("started", "now"),
    [
        (-1, 1),
        (30, 30),
        (31, 31),
        (10, 9),
        (10, 25),
        (True, 1),
        (float("nan"), 1),
        (float("inf"), 1),
        ("10", 11),
    ],
)
def test_target_time_bounds(nodes: list[str], started: object, now: float) -> None:
    """Preparation and target deadlines admit neither future nor late timestamps."""
    window = target.TargetWindow(0, "epoch", nodes)
    with pytest.raises(ValueError, match=r"timestamp|bounds"):
        window.admit(admitted(nodes, started), "hash", now)
    assert window.started is None


@pytest.mark.parametrize(
    "mutation",
    ["epoch", "source", "order", "missing-phase", "count", "index-bool", "node"],
)
def test_exact_target_identity_and_prefix(nodes: list[str], mutation: str) -> None:
    """Self-reported count alone cannot stand in for ordered phase evidence."""
    value = admitted(nodes)
    if mutation == "epoch":
        value["epoch"] = "other"
    elif mutation == "source":
        value["source_sha256"] = "other"
    elif mutation == "order":
        value["prefix"][0], value["prefix"][1] = value["prefix"][1], value["prefix"][0]
    elif mutation == "missing-phase":
        del value["prefix"][0]["phases"]["teardown"]
    elif mutation == "count":
        value["prefix"].pop()
    elif mutation == "index-bool":
        value["index"] = True
    else:
        value["nodeid"] = nodes[0]
    with pytest.raises(ValueError, match=r"identity|completion"):
        target.TargetWindow(0, "epoch", nodes).admit(value, "hash", 10)


def test_receipt_cannot_rewrite_or_reset_clock(nodes: list[str]) -> None:
    """Repeated receipt reads retain the first timestamp and absolute deadline."""
    value = admitted(nodes)
    window = target.TargetWindow(0, "epoch", nodes)
    window.admit(value, "hash", 10)
    window.admit(value, "hash", 20)
    assert window.started == 10
    with pytest.raises(ValueError, match="rewritten"):
        window.admit(value, "new-hash", 21)
    with pytest.raises(ValueError, match="extend"):
        window.admit(value, "hash", 25)


def test_write_once_never_replaces_target(tmp_path: Path) -> None:
    """Second publication fails without changing the accepted bytes."""
    path = tmp_path / "target.json"
    target.write_once(path, {"a": 1})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        target.write_once(path, {"a": 2})
    assert path.read_bytes() == before


def test_reordered_or_overlapping_child_nodes_rejected(
    nodes: list[str], tmp_path: Path
) -> None:
    """Starting the next node is prohibited before the current teardown."""
    ledger = target.TargetLedger(nodes, "epoch", tmp_path)
    with pytest.raises(ValueError, match="reordered"):
        ledger.start(nodes[1], 0)
    assert ledger.failure == "duplicate or reordered node start"
    with pytest.raises(ValueError, match="reordered"):
        ledger.start(nodes[0], 0)
    with pytest.raises(ValueError, match="reordered"):
        ledger.start(nodes[1], 1)
    with pytest.raises(ValueError, match="reordered"):
        ledger.report(nodes[0], "call", "passed", 1)


def test_child_and_parent_clock_regression(nodes: list[str], tmp_path: Path) -> None:
    """Regressing observations cannot reset an accepted lifetime."""
    ledger = target.TargetLedger(nodes, "epoch", tmp_path)
    ledger.start(nodes[0], 2)
    with pytest.raises(ValueError, match="regressed"):
        ledger.report(nodes[0], "setup", "passed", 1)
    window = target.TargetWindow(0, "epoch", nodes)
    window.admit(admitted(nodes), "hash", 10)
    with pytest.raises(ValueError, match="regressed"):
        window.admit(admitted(nodes), "hash", 9)


def test_active_workflow_has_no_debugger_path() -> None:
    """This is an explicit Python-only experiment, never automatic fallback."""
    workflow = (target.ROOT / ".github/workflows/qt-hang-diagnostic.yml").read_text()
    assert "scripts/qt_target_diagnostic.py" in workflow
    assert "preflight" not in workflow
    assert "cdb.exe" not in workflow
    assert "steps.debugger" not in workflow
    assert "git -c core.autocrlf=false archive" in workflow
    assert 'python-version: "3.13.17"' in workflow
    assert "branches: [diagnostic/qt6113-owned-stacks]" in workflow


@pytest.mark.parametrize("code", [0, 1])
@pytest.mark.parametrize("cleanup_ok", [True, False])
def test_supervisor_separates_target_coverage_and_cleanup(
    nodes: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    *,
    cleanup_ok: bool,
) -> None:
    """Real receipt validation admits target completion but never erases exit1."""
    clock = [0.0]
    job = Mock()
    observer = Mock()
    executable = getattr(target.sys, "_base_executable", target.sys.executable)
    observer.snapshot.return_value = {"image_status": "available", "image": executable}
    observer.close.return_value = []
    job.wait.return_value = code
    job.pids.return_value = []

    def start(*_: object) -> tuple[int, int]:
        target.write_once(tmp_path / "target.json", admitted(nodes))
        target.write_once(
            tmp_path / "target-done.json",
            {
                "epoch": "epoch",
                "nodeid": nodes[-1],
                "started": 10,
                "finished": 11,
                "phases": {"setup": "passed", "call": "passed", "teardown": "passed"},
            },
        )
        target.write_json(
            tmp_path / "ready.json",
            {"epoch": "epoch", "pid": 123, "executable": executable},
        )
        target.write_once(
            tmp_path / "pytest-exit.json",
            {
                "epoch": "epoch",
                "exit_code": code,
                "target_phases_passed": True,
                "coverage_failure": "FAIL Required test coverage" if code else None,
            },
        )
        clock[0] = 12.0
        return 123, 456

    job.start.side_effect = start
    monkeypatch.setattr(target, "new_job", lambda: job)
    monkeypatch.setattr(target, "new_observer", lambda _: observer)
    monkeypatch.setattr(target.uuid, "uuid4", lambda: SimpleNamespace(hex="epoch"))
    monkeypatch.setattr(target.time, "monotonic", lambda: clock[0])
    retire = Mock(return_value={"pytest": {"confirmed": cleanup_ok}})
    monkeypatch.setattr(target, "retire", retire)
    assert target.supervise(target.ROOT, tmp_path) == (code if cleanup_ok else 1)
    receipt = target.read_json(tmp_path / "receipt.json")
    assert receipt["collection_scope"] == "full-collection-prefix-execution"
    assert "nextitem" in receipt["collection_limitation"]
    assert receipt["target_outcome"] == "passed"
    assert receipt["pytest_exit"]["exit_code"] == code
    assert receipt["status"] == ("non-reproduction" if cleanup_ok else "failed")
    assert receipt["debugger_intervention"] is False
    assert retire.call_args.args[1] == 15.0
    observer.close.assert_called_once()
    job.start.assert_called_once()


@pytest.mark.parametrize("launch_return", [30.0, 45.0, 49.0])
def test_missing_target_cannot_reset_preparation_or_cleanup(
    nodes: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launch_return: float,
) -> None:
    """Slow launch never receives another preparation or per-owner cleanup budget."""
    del nodes
    clock = [0.0]
    job = Mock()

    def start(*_: object) -> tuple[int, int]:
        clock[0] = launch_return
        return 123, 456

    job.start.side_effect = start
    monkeypatch.setattr(target, "new_job", lambda: job)
    monkeypatch.setattr(target.time, "monotonic", lambda: clock[0])
    retire = Mock(return_value={"pytest": {"confirmed": True}})
    monkeypatch.setattr(target, "retire", retire)
    assert target.supervise(target.ROOT, tmp_path) == 1
    receipt = target.read_json(tmp_path / "receipt.json")
    assert receipt["target_reached"] is False
    assert "deadline" in receipt["failure"]
    assert retire.call_args.args[1] == min(launch_return + 3, 48)
    job.wait.assert_not_called()


def test_latest_legal_entry_has_no_later_than_original_target_end(
    nodes: list[str],
) -> None:
    """A late but timely entry gets one15s window, never another30s."""
    window = target.TargetWindow(0, "epoch", nodes)
    window.admit(admitted(nodes, 29.9), "hash", 30.1)
    window.admit(admitted(nodes, 29.9), "hash", 44.899)
    with pytest.raises(ValueError, match="extend"):
        window.admit(admitted(nodes, 29.9), "hash", 44.9)


@pytest.mark.parametrize("outcome", ["failed", "skipped", "unknown", "", None, 1])
def test_journal_preserves_earlier_prefix_and_terminal_failure(
    nodes: list[str],
    tmp_path: Path,
    outcome: str,
) -> None:
    """An invalid later phase cannot erase earlier records or resume the prefix."""
    ledger = target.TargetLedger(nodes, "epoch", tmp_path)
    ledger.start(nodes[0], 0)
    for phase in ("setup", "call", "teardown"):
        ledger.report(nodes[0], phase, "passed", 1)
    ledger.start(nodes[1], 2)
    before = (tmp_path / "phases.jsonl").read_bytes()
    with pytest.raises(ValueError, match=r"malformed|did not pass"):
        ledger.report(nodes[1], "setup", outcome, 3)
    after = (tmp_path / "phases.jsonl").read_bytes()
    assert after.startswith(before)
    assert len(after.splitlines()) == (6 if outcome in ("failed", "skipped") else 5)
    failure = ledger.failure
    with pytest.raises(ValueError, match=r"malformed|did not pass"):
        ledger.report(nodes[1], "setup", "passed", 4)
    assert ledger.failure == failure
    assert (tmp_path / "phases.jsonl").read_bytes() == after
    assert not (tmp_path / "target.json").exists()


@pytest.mark.parametrize(
    "limit", ["MAX_EVENTS", "MAX_EVENT_BYTES", "MAX_JOURNAL_BYTES"]
)
def test_journal_limits_fail_before_append(
    nodes: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    """Each independent bound fails closed without a partial accepted event."""
    ledger = target.TargetLedger(nodes, "epoch", tmp_path)
    monkeypatch.setattr(target, limit, 0)
    with pytest.raises(ValueError, match="bound"):
        ledger.start(nodes[0], 0)
    assert (tmp_path / "phases.jsonl").read_bytes() == b""
    assert ledger.failure == "phase journal bound exceeded"


def test_child_clock_retained_before_fixture_monkeypatch(
    nodes: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing shared time for product tests cannot corrupt journal timestamps."""
    original = target.CHILD_MONOTONIC
    before = original()
    monkeypatch.setattr(target.time, "monotonic", lambda: -1.0)
    assert target.time.monotonic() == -1.0
    assert target.CHILD_MONOTONIC is original
    ledger = target.TargetLedger(nodes, "epoch", tmp_path)
    ledger.start(nodes[0], target.CHILD_MONOTONIC())
    for phase in ("setup", "call", "teardown"):
        ledger.report(nodes[0], phase, "passed", target.CHILD_MONOTONIC())
    records = [
        json.loads(line)
        for line in (tmp_path / "phases.jsonl").read_text().splitlines()
    ]
    times = [row["monotonic"] for row in records]
    assert before <= times[0] <= times[-1]
    assert times == sorted(times)
    assert len(ledger.completed) == 1


def collection_case(
    nodes: list[str], tmp_path: Path
) -> tuple[target.FullCollection, SimpleNamespace]:
    """Model pytest Item identity, retaining the full discovered graph."""
    proof = target.FullCollection(nodes, "epoch", tmp_path)
    selected = [SimpleNamespace(nodeid=name) for name in nodes]
    selected += [
        SimpleNamespace(nodeid=f"other/test.py::test_{index}")
        for index in range(3691 - len(nodes))
    ]
    excluded = [SimpleNamespace(nodeid=name) for name in sorted(proof.EXCLUDED)]
    for item in [*selected, *excluded]:
        proof.collect(item)
    proof.excluded.extend(excluded)
    return proof, SimpleNamespace(items=selected)


def test_full_collection_retained_and_prefix_not_reordered(
    nodes: list[str], tmp_path: Path
) -> None:
    """Truncation retains every original object including the unexecuted suffix."""
    proof, session = collection_case(nodes, tmp_path)
    original = list(session.items)
    proof.admit(session)
    assert session.items == original[:157]
    assert len(proof.all_items) == 3696
    assert all(any(item is kept for kept in proof.all_items) for item in original)
    receipt = target.read_json(tmp_path / "full-collection.json", 1024 * 1024)
    assert receipt["selected"] == [item.nodeid for item in original]
    assert receipt["original_nextitem"] == original[157].nodeid
    assert receipt["historical_windows_suffix_verified"] is False
    with pytest.raises(ValueError, match="mismatch"):
        proof.admit(session)


@pytest.mark.parametrize(
    "change", ["count", "excluded", "reorder", "foreign", "duplicate", "receipt-bound"]
)
def test_full_collection_mismatch_never_fits_prefix(
    nodes: list[str],
    tmp_path: Path,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counts, identities, exact exclusions and first157 are admission gates."""
    proof, session = collection_case(nodes, tmp_path)
    if change == "count":
        proof.all_items.pop()
    elif change == "excluded":
        proof.excluded[0].nodeid = "other-exclusion"
    elif change == "reorder":
        session.items[0], session.items[1] = session.items[1], session.items[0]
    elif change == "foreign":
        session.items[-1] = SimpleNamespace(nodeid=session.items[-1].nodeid)
    elif change == "duplicate":
        session.items[-1] = session.items[-2]
    else:
        monkeypatch.setattr(proof, "MAX_ORDER_BYTES", 1)
    before = list(session.items)
    with pytest.raises(ValueError, match=r"mismatch|bound"):
        proof.admit(session)
    assert session.items == before
    assert not proof.admitted
    assert not (tmp_path / "full-collection.json").exists()


def test_full_discovery_count_is_bounded(nodes: list[str], tmp_path: Path) -> None:
    """The Item retention list cannot silently grow past the fixed source count."""
    proof, _ = collection_case(nodes, tmp_path)
    with pytest.raises(ValueError, match="count exceeded"):
        proof.collect(SimpleNamespace(nodeid="extra"))
    assert len(proof.all_items) == 3696


def test_child_uses_root_collection_and_post_collection_admission() -> None:
    """Audit the actual pytest call: no explicit nodes or reordering suppression."""
    import inspect  # noqa: PLC0415 - inspect only this fixed diagnostic function

    source = inspect.getsource(target.child)
    assert '"--randomly-dont-reorganize"' not in source
    assert "*nodes" not in source
    assert '"--randomly-seed=" + str(SEED)' in source
    assert '"not native_desktop"' in source
    assert "pytest_collection_modifyitems" not in source
    assert "@pytest.hookimpl(tryfirst=True)" in source
    assert "def pytest_runtestloop" in source
    assert "collection.admit(session)" in source
