"""Strict private marker ownership and clock bridging, without native tracing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO, cast

import pytest
from scripts import fixture_startup_summary as summary

SOURCE = "a" * 40
NONCE = "b" * 32


def ownership() -> tuple[dict[str, object], dict[str, object]]:
    """Describe the same actual child as the synthetic ETL lifetime."""
    return (
        {
            "pid": 10,
            "nonce": NONCE,
            "source_sha": SOURCE,
            "repair_base_sha": summary.REPAIR_BASE,
            "fixture_directory": "private/synthetic",
            "clock": "QueryPerformanceCounter()",
            "launch_begin_ns": 1,
            "launch_end_ns": 3_000_000,
        },
        {
            "pid": 10,
            "nonce": NONCE,
            "exit_ns": 101_000_000,
            "returncode": 0,
            "forced": False,
        },
    )


def markers() -> str:
    """A constructor interval, using the same raw 1kHz clock as the ETL."""
    return "".join(
        f"{i},{name},10,20,{tick},1000,{NONCE}\n"
        for i, (name, tick) in enumerate(
            (
                ("entry", 4),
                ("window_begin", 10),
                ("window_end", 90),
                ("retirement_begin", 91),
                ("fixture_retired", 92),
            ),
            1,
        )
    )


def rows() -> list[list[str]]:
    """Complete zero-loss evidence with all three scheduler states."""
    return [
        ["L", "2", "1", "10", "20"],
        ["S", "3", "20", "30", "5"],
        ["S", "20", "30", "20", "5"],
        ["R", "60", "20"],
        ["S", "80", "20", "30", "1"],
        ["L", "100", "2", "10", "20"],
        ["H", "1", "1000", "0", "0", "0", "0", "0", "7"],
    ]


def complete_markers() -> str:
    """Include every required startup phase; only the window span is nonzero."""
    names = [name for name in summary.MARKERS if name != "stop_observed"]
    ticks = {
        "entry": 4,
        "assemblies_begin": 5,
        "assemblies_end": 6,
        "identity_begin": 7,
        "identity_end": 8,
        "window_begin": 10,
        "retirement_begin": 91,
        "fixture_retired": 92,
    }
    return "".join(
        f"{i},{name},10,20,{ticks.get(name, 90)},1000,{NONCE}\n"
        for i, name in enumerate(names, 1)
    )


def test_owned_qpc_bridge_retains_exact_scheduler_totals() -> None:
    """No timestamp, PID, thread ID, nonce or path is public output."""
    owner, exited = ownership()
    report = summary.report_from_evidence(
        rows(),
        summary.parse_markers(complete_markers(), 10, NONCE),
        owner,
        exited,
        SOURCE,
    )
    assert report["status"] == "complete"
    scheduler = cast(dict[str, object], report["scheduler"])
    assert scheduler["analysis_complete"] is True
    assert scheduler["causal_claim"] is False
    phases = cast(list[dict[str, object]], scheduler["phases"])
    assert next(item for item in phases if item["phase"] == "window") == {
        "phase": "window",
        "capture_ordinal": 1,
        "wall_ms": 80.0,
        "scheduled_ms": 20.0,
        "waiting_ms": 40.0,
        "ready_ms": 20.0,
        "unknown_ms": 0.0,
        "state_complete": True,
    }
    text = json.dumps(report)
    assert NONCE not in text
    assert "private/synthetic" not in text
    assert '"pid"' not in text
    assert '"tid"' not in text
    assert '"tick"' not in text


@pytest.mark.parametrize("frequency", [3, 1000, 10_000_000, 999_999_937, 1_000_000_000])
@pytest.mark.parametrize("tick", [1, 11, 123_456_789, (1 << 53) + 13])
def test_integer_bridge_is_exact_even_beyond_float_precision(
    frequency: int, tick: int
) -> None:
    """Ceil-to-ns then reducer floor must recover the original integer QPC tick."""
    ns = summary.qpc_nanoseconds(tick, frequency)
    assert ns * frequency // summary.NANOSECONDS == tick
    assert 0 <= ns * frequency - tick * summary.NANOSECONDS < frequency


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", 1 << 63])
def test_numeric_coercions_and_out_of_range_clocks_are_refused(value: object) -> None:
    """Private sidecars are still untrusted input to the bounded adapter."""
    with pytest.raises(ValueError, match="integer"):
        summary.integer(value)


def test_unrepresentable_tick_is_not_rounded_into_another_tick() -> None:
    """No optimistic alignment when nanoseconds cannot represent the clock."""
    with pytest.raises(ValueError, match="unrepresentable"):
        summary.qpc_nanoseconds(1, 3_000_000_000)


@pytest.mark.parametrize(
    "change",
    [
        "partial",
        "extra",
        "name",
        "nonce",
        "pid",
        "tid",
        "frequency",
        "duplicate",
        "sequence",
        "inversion",
        "after_stop",
        "over_budget",
        "non_integer",
    ],
)
def test_corrupt_or_foreign_markers_fail_closed(change: str) -> None:
    """A partial prefix may not silently become trusted phase evidence."""
    text = markers()
    variants = {
        "partial": text[:-1],
        "extra": text + "\n",
        "name": text.replace("window_begin", "title_secret"),
        "nonce": text.replace(NONCE, "c" * 32, 1),
        "pid": text.replace(",10,20,", ",11,20,", 1),
        "tid": text.replace(",10,20,90,", ",10,21,90,"),
        "frequency": text.replace(",1000,", ",1001,", 1),
        "duplicate": text.replace("window_end", "window_begin"),
        "sequence": text.replace("2,window_begin", "9,window_begin"),
        "inversion": text.replace(",90,1000,", ",9,1000,"),
        "after_stop": text.replace("window_begin", "stop_observed"),
        "over_budget": (text.splitlines()[0] + "\n") * 27,
        "non_integer": text.replace(",1000,", ",1e3,", 1),
    }
    with pytest.raises(ValueError, match=r"marker|cancellation"):
        summary.parse_markers(variants[change], 10, NONCE)


@pytest.mark.parametrize(
    "field",
    [
        "pid",
        "nonce",
        "source_sha",
        "clock",
        "launch_begin_ns",
        "launch_end_ns",
        "fixture_directory",
    ],
)
def test_parent_manifest_mismatch_is_not_attributed(field: str) -> None:
    """Only actual child ownership, immutable source and Windows QPC are accepted."""
    owner, exited = ownership()
    owner[field] = None if field == "fixture_directory" else "foreign"
    with pytest.raises(ValueError, match=r"owner|integer|parent"):
        summary.validate_owner(owner, exited, SOURCE)


@pytest.mark.parametrize("field", ["pid", "nonce", "exit_ns", "returncode", "forced"])
def test_child_exit_receipt_is_required(field: str) -> None:
    """Missing or mismatched retirement cannot be replaced by process-name guessing."""
    owner, exited = ownership()
    exited[field] = "foreign"
    with pytest.raises(ValueError, match=r"owner|integer|parent"):
        summary.validate_owner(owner, exited, SOURCE)


@pytest.mark.parametrize(
    "change",
    [
        "frequency",
        "loss",
        "reuse",
        "missing_death",
        "wrong_thread",
        "birth_before_launch",
        "death_after_exit",
        "marker_after_death",
    ],
)
def test_trace_loss_lifetime_and_alignment_rules_stay_strict(change: str) -> None:
    """The adapter adds provenance checks, never relaxes the existing reducer."""
    data = rows()
    owner, exited = ownership()
    if change == "frequency":
        data[-1][2] = "1001"
    elif change == "loss":
        data[-1][3] = "1"
    elif change == "reuse":
        data.insert(1, data[0].copy())
    elif change == "missing_death":
        del data[-2]
    elif change == "wrong_thread":
        data[0][4] = "21"
    elif change == "birth_before_launch":
        owner["launch_begin_ns"] = 3_000_000
    elif change == "death_after_exit":
        exited["exit_ns"] = 95_000_000
    else:
        data[-2][1] = "89"
    with pytest.raises(ValueError, match=r"frequency|trace|thread|parent|marker"):
        summary.report_from_evidence(
            data, summary.parse_markers(markers(), 10, NONCE), owner, exited, SOURCE
        )


def test_unknown_state_remains_incomplete() -> None:
    """A valid marker does not manufacture attribution for unknown ETW state."""
    data = rows()
    data[2][4] = "99"
    owner, exited = ownership()
    report = summary.report_from_evidence(
        data, summary.parse_markers(markers(), 10, NONCE), owner, exited, SOURCE
    )
    assert report["status"] == "incomplete"
    scheduler = cast(dict[str, object], report["scheduler"])
    assert scheduler["analysis_complete"] is False


def test_sta_thread_may_be_born_after_popen_returns() -> None:
    """PID ownership does not imply the dispatcher is the initial process thread."""
    owner, exited = ownership()
    owner["launch_end_ns"] = 1_000_000
    report = summary.report_from_evidence(
        rows(), summary.parse_markers(markers(), 10, NONCE), owner, exited, SOURCE
    )
    assert report["clock_bridge_exact"] is True


def test_cancelled_phase_has_no_fabricated_endpoint() -> None:
    """A complete earlier pair can be reported while the interrupted one stays open."""
    text = (
        markers()
        .replace("4,retirement_begin,10,20,91", "4,controls_begin,10,20,91")
        .replace("5,fixture_retired,10,20,92", "5,stop_observed,10,20,92")
    )
    owner, exited = ownership()
    report = summary.report_from_evidence(
        rows(), summary.parse_markers(text, 10, NONCE), owner, exited, SOURCE
    )
    assert report["status"] == "incomplete"
    assert report["marker_capture_complete"] is False
    assert "controls" in cast(list[str], report["incomplete_phases"])


def test_fixed_failure_report_has_no_exception_or_private_content() -> None:
    """The runner can safely retain the reason without dumping failed inputs."""
    assert summary.unavailable(SOURCE)["reason"] == "evidence_unavailable"
    assert summary.unavailable(SOURCE)["scheduler"] is None


def test_private_paths_and_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linked, sibling, wrongly named, and oversized inputs are not admitted."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    owned = tmp_path / ("VerdiClip-" + NONCE)
    owned.mkdir()
    assert summary.private_directory(owned) == owned
    for path in (tmp_path, tmp_path / "missing", Path("relative")):
        with pytest.raises(ValueError, match="private directory"):
            summary.private_directory(path)
    receipt = owned / "owner.json"
    receipt.write_text("[]")
    with pytest.raises(TypeError):
        summary.read_receipt(receipt)
    with pytest.raises(ValueError, match="budget"):
        summary.read_private(receipt, 1)
    receipt.unlink()
    receipt.symlink_to(tmp_path / "foreign")
    with pytest.raises(ValueError, match="private file"):
        summary.read_private(receipt)


def test_collection_failure_is_staged_privately_never_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing files yield an honest unavailable result without public evidence."""
    directory = tmp_path / ("VerdiClip-" + NONCE)
    directory.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("VERDICLIP_BENCHMARK_SOURCE_SHA", SOURCE)
    monkeypatch.setattr("sys.argv", ["summary", "reader.exe", str(directory)])
    assert summary.main() == 2
    assert json.loads((directory / "analysis.json").read_text()) == summary.unavailable(
        SOURCE
    )
    assert capsys.readouterr() == ("", "")


def test_all_input_mutations_leave_original_evidence_untouched() -> None:
    """Aggregation does not modify the established reader's evidence or policy."""
    data = rows()
    before = deepcopy(data)
    owner, exited = ownership()
    summary.report_from_evidence(
        data, summary.parse_markers(markers(), 10, NONCE), owner, exited, SOURCE
    )
    assert data == before


def test_module_entry_resolves_unchanged_reducer_without_pythonpath(
    tmp_path: Path,
) -> None:
    """The real runner invocation works in a clean environment, without import hacks."""
    directory = tmp_path / ("VerdiClip-" + NONCE)
    directory.mkdir()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update(RUNNER_TEMP=str(tmp_path), VERDICLIP_BENCHMARK_SOURCE_SHA=SOURCE)
    result = subprocess.run(  # noqa: S603 - fixed repository module and private owned path
        [
            sys.executable,
            "-m",
            "scripts.fixture_startup_summary",
            "reader.exe",
            str(directory),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""
    assert (
        json.loads((directory / "analysis.json").read_text())["status"] == "unavailable"
    )


def test_missing_middle_phase_cannot_claim_complete_startup() -> None:
    """Even a cleanly retired stream explicitly reports missing required phases."""
    owner, exited = ownership()
    report = summary.report_from_evidence(
        rows(), summary.parse_markers(markers(), 10, NONCE), owner, exited, SOURCE
    )
    assert report["status"] == "incomplete"
    assert report["marker_capture_complete"] is False
    assert "identity" in cast(list[str], report["incomplete_phases"])


@pytest.mark.parametrize(
    "failure", ["none", "reader", "independent", "partial", "source"]
)
def test_private_collection_uses_actual_reader_and_independent_decoder_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Exercise complete file-to-report flow and fail closed on either decoder."""
    owner, exited = ownership()
    if failure == "source":
        owner["source_sha"] = "c" * 40
    (tmp_path / "owner.json").write_text(json.dumps(owner))
    (tmp_path / "exit.json").write_text(json.dumps(exited))
    (tmp_path / "markers.csv").write_text(
        complete_markers()[:-1] if failure == "partial" else complete_markers()
    )
    (tmp_path / "trace.etl").write_bytes(b"private ETL placeholder")
    exported = "\n".join(
        [
            "BeginHeader",
            "CSwitch, TimeStamp, New TID, Old TID, OldState",
            "ReadyThread, TimeStamp, ThreadID, Rdy TID",
            "EndHeader",
            "CSwitch, 3, 20, 30, Waiting",
            "CSwitch, 20, 30, 20, Waiting",
            "ReadyThread, 60, 30, 20",
            "CSwitch, 80, 20, 30, Ready",
        ]
    )
    (tmp_path / "exported.csv").write_text(exported if failure != "independent" else "")

    def reader(arguments: list[str], **options: object) -> SimpleNamespace:
        assert arguments == [
            "reviewed-reader.exe",
            str(tmp_path / "trace.etl"),
            "10",
            "20",
        ]
        assert options["timeout"] == 30
        assert options["stderr"] == subprocess.DEVNULL
        stream = cast(TextIO, options["stdout"])
        stream.write("\n".join(",".join(row) for row in rows()) + "\n")
        return SimpleNamespace(returncode=1 if failure == "reader" else 0)

    monkeypatch.setattr(subprocess, "run", reader)
    if failure == "none":
        assert (
            summary.collect(Path("reviewed-reader.exe"), tmp_path, SOURCE)["status"]
            == "complete"
        )
    else:
        with pytest.raises(ValueError, match=r"reader|independent|partial|owner"):
            summary.collect(Path("reviewed-reader.exe"), tmp_path, SOURCE)
