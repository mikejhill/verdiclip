"""First-request timing contracts, without native calls or desktop access."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace
from verdiclip.capture.scroll.uia_inspect_clock import (
    LABELS,
    PREFIX,
    InspectClockReceipt,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/verdiclip/capture/scroll"


def test_fragmented_fixed_clock_receipts_keep_first_values() -> None:
    """Fragmentation and duplicates cannot overwrite first evidence."""
    trace = WorkerTrace(lambda: 1.0)
    data = PREFIX + b"root_call:100\n" + PREFIX + b"root_return:200\n"
    for byte in data:
        assert trace.error_bytes(bytes([byte]))
    assert [(x.stage, x.microseconds) for x in trace.inspect_clock.events] == [
        ("root_call", 100),
        ("root_return", 200),
    ]
    trace.error_bytes(PREFIX + b"root_call:999\n")
    assert trace.inspect_clock.events[0].microseconds == 100
    assert trace.inspect_clock.unavailable
    assert not trace.events


@pytest.mark.parametrize("value", [b"-2", b"2147483648", b"1.0", b"secret", b"9" * 20])
def test_bad_clock_value_cannot_enter_receipt(value: bytes) -> None:
    """Reject malformed, oversized or private values."""
    receipt = InspectClockReceipt()
    receipt.feed(PREFIX + b"root_call:" + value + b"\n")
    assert not receipt.events
    assert receipt.unavailable


def test_unknown_text_and_overflow_are_bounded() -> None:
    """Arbitrary stderr cannot expand the fixed receipt."""
    receipt = InspectClockReceipt()
    receipt.feed(b"private arbitrary text" * 10000 + b"\n")
    assert not receipt.events
    assert len(receipt._line) == 0
    for i, label in enumerate(LABELS):
        receipt.feed(PREFIX + label.encode() + b":" + str(i).encode() + b"\n")
    before = tuple(receipt.events)
    receipt.feed(PREFIX + b"unknown:99\n" * 100)
    assert tuple(receipt.events) == before
    assert receipt.unavailable
    assert len(receipt.events) == 14


def test_unavailable_and_backward_clock_are_explicit() -> None:
    """Unreliable time is never represented as a trustworthy duration."""
    receipt = InspectClockReceipt()
    receipt.feed(PREFIX + b"armed:10\n" + PREFIX + b"request_read:-1\n")
    receipt.feed(PREFIX + b"json_parse:9\n")
    assert len(receipt.events) == 2
    assert receipt.events[1].microseconds is None
    assert receipt.unavailable


@pytest.mark.parametrize(
    "control", [KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_control_is_not_swallowed_by_parent_trace(
    monkeypatch: pytest.MonkeyPatch, control: type[BaseException]
) -> None:
    """Cancellation and process controls remain observable."""
    trace = WorkerTrace(lambda: 1.0)

    def fail(_data: bytes) -> None:
        raise control

    monkeypatch.setattr(trace.inspect_clock, "feed", fail)
    with pytest.raises(control):
        trace.error_bytes(b"x")


def test_added_markers_do_not_add_provider_calls() -> None:
    """Calls and ownership order are unchanged by marker insertion."""
    source = (SOURCE / "uia_discovery.cs").read_text()
    root = source.split("public IDiscoveryElement Root(long handle)")[1].split(
        "public IDiscoveryElement Hit"
    )[0]
    assert root.count("automation.ElementFromHandle(") == 1
    assert root.index('Mark("root_call")') < root.index("automation.ElementFromHandle(")
    assert root.index("automation.ElementFromHandle(") < root.index(
        'Mark("root_return")'
    )
    assert root.index("Own(value)") < root.index('Mark("root_owned")')
    assert root.index("Wrap(result)") < root.index('Mark("root_wrapped")')
    helper = (SOURCE / "uia_helper.ps1").read_text()
    assert "finally {\n    if ($script:firstInspectClock)" in helper
    assert helper.count("FirstInspectClock]::Arm()") == 1
    assert "VERDICLIP_HOSTED_SCROLLBAR -ceq '1'" in helper


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows compiler")
def test_actual_clock_producer_faults_without_native_calls(tmp_path: Path) -> None:
    """Compile real telemetry with pure injected clocks and writers."""
    # Compile the exact diagnostic class only, no native interface or UIA class.
    source = (SOURCE / "uia_discovery.cs").read_text().split(
        "public interface IDiscoveryElement"
    )[0] + "}\n"
    actual = tmp_path / "clock.cs"
    actual.write_text(source, encoding="utf-8")
    script = tmp_path / "clock.ps1"
    script.write_text(
        "param($Source,$Tests)\n$ErrorActionPreference='Stop'\n"
        "Add-Type -Path @($Source,$Tests)\n[InspectClockTests]::Run()\n",
        encoding="utf-8",
    )
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Source",
            str(actual),
            "-Tests",
            str(Path(__file__).with_name("inspect_clock_fake.cs")),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "passed"


@pytest.mark.parametrize("fault", ["label", "value", "shape"])
def test_public_summary_rejects_private_fields(fault: str) -> None:
    """Malformed child evidence cannot disclose data through the summary."""
    from tests.capture.inspect_clock_report import summarize  # noqa: PLC0415

    event: dict[str, object] = {"stage": "root_call", "microseconds": 5}
    if fault == "label":
        event["stage"] = "private provider text"
    elif fault == "value":
        event["microseconds"] = "private path"
    else:
        event["raw"] = "private identity"
    result = summarize(
        {
            "errors": ["secret"],
            "provider_worker_diagnostics": {
                "read": {
                    "first_request": {"action": "inspect"},
                    "inspect_clock": [event],
                    "inspect_clock_unavailable": False,
                }
            },
        }
    )
    assert result["events"] == []
    assert result["clock_unavailable"] is True
    assert "private" not in str(result)
    assert "secret" not in str(result)


def test_public_summary_contains_only_fixed_fields() -> None:
    """A passing parent receipt still does not certify child cleanup."""
    from tests.capture.inspect_clock_report import summarize  # noqa: PLC0415

    result = summarize(
        {
            "passed": True,
            "retired": True,
            "cleanup_errors": [],
            "provider_worker_diagnostics": {
                "read": {
                    "inspect_clock": [{"stage": "root_call", "microseconds": 5}],
                    "inspect_clock_unavailable": False,
                    "first_request": {"action": "inspect", "outcome": "success"},
                }
            },
        }
    )
    assert result["events"] == [{"stage": "root_call", "microseconds": 5}]
    assert result["first_action_inspect"] is True
    assert result["child_cleanup_proven"] is False


def test_workflow_is_one_isolated_opt_in_lane() -> None:
    """No automatic physical input, retry matrix or unpinned source is introduced."""
    workflow = (ROOT / ".github/workflows/first-inspect-boundaries.yml").read_text()
    assert "github.event.pull_request.draft == true" in workflow
    assert (
        "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    )
    assert "github.head_ref == 'test/first-inspect-boundaries'" in workflow
    assert workflow.count("-ScrollbarOnly -ExecuteAfterHandoff") == 1
    assert "-Physical" not in workflow
    assert "strategy:" not in workflow
    assert "if: always()" in workflow
    assert "VERDICLIP_FIRST_INSPECT_CLOCK = '1'" in workflow


@pytest.mark.parametrize(
    ("fault", "category"),
    [
        ("missing", "missing-or-multiple"),
        ("malformed", "malformed"),
        ("truncated", "malformed"),
        ("duplicate", "malformed"),
        ("nonobject", "shape"),
        ("read", "read"),
        ("oversize", "oversize"),
    ],
)
def test_actual_report_main_preserves_fixed_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    category: str,
) -> None:
    """Unavailable receipts never leak paths or disappear behind a traceback."""
    from tests.capture import inspect_clock_report as report  # noqa: PLC0415

    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".test-tmp/native-broker-owned/case/scrollbar-native.json"
    path.parent.mkdir(parents=True)
    payloads = {
        "malformed": b"private text",
        "truncated": b'{"private":',
        "duplicate": b'{"passed":true,"passed":false}',
        "nonobject": b"[]",
        "read": b"{}",
        "oversize": b"x" * (report.MAX_RECEIPT_BYTES + 1),
    }
    if fault != "missing":
        path.write_bytes(payloads[fault])
    if fault == "read":

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise PermissionError("private path")

        monkeypatch.setattr(Path, "open", refuse)
    report.main()
    output = capsys.readouterr()
    assert '"gap": "' + category + '"' in output.out
    assert '"receipt_present": false' in output.out
    assert "private" not in output.out
    assert not output.err


@pytest.mark.parametrize("outcome", ["success", "operation_timeout"])
def test_summary_uses_first_inspect_worker_not_current_mutation(outcome: str) -> None:
    """A later RESTORE worker cannot hide or replace first-INSPECT evidence."""
    from tests.capture.inspect_clock_report import summarize  # noqa: PLC0415

    inspect = {
        "first_request": {"action": "inspect", "outcome": outcome},
        "inspect_clock": [
            {"stage": "armed", "microseconds": 1},
            {"stage": "root_call", "microseconds": 100},
        ],
        "inspect_clock_unavailable": False,
    }
    mutation = {
        "first_request": {"action": "restore", "outcome": "success"},
        "inspect_clock": [],
        "inspect_clock_unavailable": False,
    }
    result = summarize(
        {
            "provider_diagnostics": mutation,
            "provider_worker_diagnostics": {
                "arbitrary-role-a": mutation,
                "arbitrary-role-b": inspect,
            },
        }
    )
    assert result["events"] == inspect["inspect_clock"]
    assert result["first_outcome"] == outcome
    assert result["clock_unavailable"] is False
    assert result["first_action_inspect"] is True


@pytest.mark.parametrize("count", [0, 2])
def test_summary_refuses_missing_or_ambiguous_inspect(count: int) -> None:
    """No fallback to the current worker or successful candidate is permitted."""
    from tests.capture.inspect_clock_report import summarize  # noqa: PLC0415

    diagnostic = {"first_request": {"action": "inspect", "outcome": "success"}}
    result = summarize(
        {
            "provider_diagnostics": diagnostic,
            "provider_worker_diagnostics": {str(i): diagnostic for i in range(count)},
        }
    )
    assert result["gap"] == "inspect-worker-missing-or-ambiguous"
    assert result["events"] == []
    assert result["clock_unavailable"] is True


@pytest.mark.parametrize("events", [[], [{"stage": "root_call", "microseconds": 1}]])
def test_summary_marks_missing_initial_clock_unavailable(events: list[object]) -> None:
    """A successful case and false error flag cannot manufacture a clock origin."""
    from tests.capture.inspect_clock_report import summarize  # noqa: PLC0415

    result = summarize(
        {
            "provider_worker_diagnostics": {
                "read": {
                    "first_request": {"action": "inspect", "outcome": "success"},
                    "inspect_clock": events,
                    "inspect_clock_unavailable": False,
                }
            }
        }
    )
    assert result["clock_unavailable"] is True
    assert result["gap"] == "inspect-clock-missing-origin"
