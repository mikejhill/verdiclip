"""Cold root timing is bounded, correlated evidence, not a performance gate."""

import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll import uia_process
from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace
from verdiclip.capture.scroll.uia_messages import UiaAction, UiaRequest
from verdiclip.capture.scroll.uia_worker import UiaWorkerLease

FIRST = "a" * 32
SECOND = "b" * 32


def request(identity: str = FIRST, action: UiaAction = UiaAction.INSPECT) -> UiaRequest:
    """Only correlation is needed; no provider or native process is involved."""
    return UiaRequest(identity, action, b"{}")


def record(value: bytes = b"2365000", identity: str = FIRST) -> bytes:
    """Produce one exact diagnostic wire line."""
    return WorkerTrace.ROOT_PREFIX + identity.encode() + b":" + value + b"\n"


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
def test_duration_survives_every_fragment_boundary(ending: bytes) -> None:
    """Completion timing is separate from parent clock stamps and private IDs."""
    wire = record().removesuffix(b"\n") + ending
    for split in range(1, len(wire)):
        trace = WorkerTrace(lambda: 50)
        trace.begin_request(request())
        accepted = trace.error_bytes(wire[:split])
        assert accepted
        assert trace.root_duration_us is None
        accepted = trace.error_bytes(wire[split:])
        assert accepted
        assert trace.root_duration_us == 2365000
        assert trace.first_inspect_root_duration_us == 2365000
        assert not trace.events


@pytest.mark.parametrize(
    "value",
    [
        b"",
        b"-1",
        b"+1",
        b"1.5",
        b"1e3",
        b"NaN",
        b"1000000000",
        pytest.param(b"9" * 70000, id="oversized-70kb"),
        b" 1",
        b"1 ",
        b"1\r2",
        b"1\r\r",
        b"private-provider-title",
    ],
)
def test_invalid_duration_is_not_retained(
    value: bytes, request: pytest.FixtureRequest
) -> None:
    """No culture-dependent, signed, huge, or injected value enters a snapshot."""
    # pytest-qt exports the node name in the environment on Windows.
    assert len(request.node.name) < 128
    trace = WorkerTrace(lambda: 0)
    trace.begin_request(UiaRequest(FIRST, UiaAction.INSPECT, b"{}"))
    accepted = trace.error_bytes(record(value))
    assert accepted == (len(record(value)) <= trace.MAX_ERROR_BYTES)
    assert trace.root_duration_us is None
    assert trace.first_inspect_root_duration_us is None
    assert not trace.events
    assert len(trace._marker) <= trace.MAX_ROOT_RECORD + 1


@pytest.mark.parametrize("value", [b"0", b"999999999"])
def test_numeric_wire_bounds_are_not_latency_thresholds(value: bytes) -> None:
    """Zero and the maximum representable duration are valid evidence."""
    trace = WorkerTrace(lambda: 0)
    trace.begin_request(request())
    accepted = trace.error_bytes(record(value))
    assert accepted
    assert trace.root_duration_us == int(value)


def test_coalesced_lines_keep_first_valid_duration_and_fixed_markers() -> None:
    """Malformed data and duplicates cannot replace the first completed duration."""
    trace = WorkerTrace(lambda: 0)
    trace.begin_request(request())
    data = (
        b"private-title\n"
        + record(b"bad")
        + record(b"0")
        + record(b"99")
        + b"VERDICLIP_UIA_PHASE=inspect_process\n"
    )
    accepted = trace.error_bytes(data)
    assert accepted
    assert trace.root_duration_us == 0
    assert trace.first_inspect_root_duration_us == 0
    assert [event.stage for event in trace.events] == ["inspect_process"]


def test_late_cold_record_is_not_assigned_to_warm_request() -> None:
    """A completion callback may submit again before prior stderr is delivered."""
    trace = WorkerTrace(lambda: 0)
    trace.begin_request(request())
    wire = record()
    accepted = trace.error_bytes(wire[:35])
    assert accepted
    trace.begin_request(request(SECOND))
    accepted = trace.error_bytes(wire[35:] + record(b"10", SECOND))
    assert accepted
    assert trace.first_inspect_root_duration_us == 2365000
    assert trace.root_duration_us == 10
    trace.begin_request(request("c" * 32, UiaAction.OBSERVE))
    accepted = trace.error_bytes(record(b"99", SECOND))
    assert accepted
    assert trace.root_duration_us is None
    assert trace.first_inspect_root_duration_us == 2365000


def test_missing_first_duration_is_never_promoted_from_warm_inspect() -> None:
    """Timeout/exception without a record stays unknown, not a warm substitute."""
    trace = WorkerTrace(lambda: 0)
    trace.begin_request(request())
    trace.begin_request(request(SECOND))
    accepted = trace.error_bytes(record(b"5", SECOND))
    assert accepted
    assert trace.root_duration_us == 5
    assert trace.first_inspect_root_duration_us is None


@pytest.mark.parametrize("action", list(UiaAction))
def test_only_admitted_inspect_ids_can_supply_timing(action: UiaAction) -> None:
    """Unknown IDs, startup lines, and non-inspect requests cannot claim timing."""
    trace = WorkerTrace(lambda: 0)
    accepted = trace.error_bytes(record())
    assert accepted
    trace.begin_request(request(action=action))
    accepted = trace.error_bytes(record(identity=SECOND))
    assert accepted
    assert trace.root_duration_us is None
    accepted = trace.error_bytes(record())
    assert accepted
    expected = 2365000 if action is UiaAction.INSPECT else None
    assert trace.root_duration_us == expected
    assert trace.first_inspect_root_duration_us == expected


def test_cold_duration_survives_ring_rollover_without_expanding_caps() -> None:
    """One extra scalar cannot grow with frames or evade stderr's lifetime cap."""
    trace = WorkerTrace(lambda: 0)
    trace.begin_request(request())
    accepted = trace.error_bytes(record())
    assert accepted
    for _ in range(40):
        trace.mark("inspect_parent")
    assert len(trace.events) == 32
    assert trace.dropped_events == 8
    assert trace.first_inspect_root_duration_us == 2365000
    accepted = trace.error_bytes(b"x" * (65536 - trace.stderr_bytes))
    assert accepted
    accepted = trace.error_bytes(b"x")
    assert not accepted


def test_worker_admission_resets_current_but_snapshot_retains_cold(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise real admission/snapshot wiring without launching any child."""
    lease = UiaWorkerLease()
    monkeypatch.setattr(lease, "_dispatch", lambda *_args: None)
    lease.submit(request(), ("unused",), deadline_ms=3000, startup_ms=8000)
    accepted = lease._trace.error_bytes(record())
    assert accepted
    snapshot = asdict(lease.diagnostics)
    assert snapshot["root_duration_us"] == 2365000
    assert snapshot["first_inspect_root_duration_us"] == 2365000
    assert FIRST not in repr(snapshot)
    process = lease._process
    lease._accept_line(b'{"request_id":"' + FIRST.encode() + b'","ok":true}\n')
    lease.submit(request(SECOND), ("unused",), deadline_ms=3000, startup_ms=8000)
    assert lease._process is process
    assert lease._deadline_phase == "operation"
    assert lease.diagnostics.root_duration_us is None
    assert lease.diagnostics.first_inspect_root_duration_us == 2365000
    lease.cancel()
    assert lease._process is None
    assert lease.diagnostics.cleanup == "confirmed"
    qtbot.wait(1)
    lease.deleteLater()


def test_root_timing_brackets_only_original_call_and_does_not_catch_it() -> None:
    """No retry, initialization or exception conversion is introduced by timing."""
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1").read_text()
    before, rest = helper.split("    $rootStarted = $null\n", 1)
    body, after = rest.split(
        "    if ($request.action -eq 'inspect') { [Console]::Error.WriteLine("
        "'VERDICLIP_UIA_PHASE=inspect_process') }",
        1,
    )
    assert before.endswith(
        "# No added UIA initialization, warmup, retry, or provider call.\n"
    )
    assert body.startswith(
        "    if ($request.action -eq 'inspect') {\n"
        "        try { $rootStarted = [Diagnostics.Stopwatch]::GetTimestamp() }"
        " catch { }\n    }\n"
        "    $root = [System.Windows.Automation.AutomationElement]"
        "::FromHandle($handle)\n"
    )
    assert body.count("::FromHandle(") == 1
    assert body.count("::GetTimestamp()") == 2
    assert "[Diagnostics.Stopwatch]::Frequency" in body
    assert "[Globalization.CultureInfo]::InvariantCulture" in body
    assert "-ge 0 -and $rootMicroseconds -le 999999999" in body
    assert "catch { } # Missing optional timing" in body
    assert after.startswith(
        "\n    $process = Get-Process -Id $root.Current.ProcessId\n"
    )
    assert "finally {\n    if ($null -ne $process) { $process.Dispose();" in helper


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
@pytest.mark.parametrize("action", ["inspect", "observe"])
@pytest.mark.parametrize("fault", ["none", "start", "finish", "write", "root"])
def test_actual_timing_wrapper_preserves_results_and_exceptions(
    tmp_path: Path, action: str, fault: str
) -> None:
    """Run the real wrapper with one inert stand-in call, never Windows UIA."""
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1").read_text()
    body = helper.split("    $rootStarted = $null\n", 1)[1].split(
        "    if ($request.action -eq 'inspect') { [Console]::Error.WriteLine("
        "'VERDICLIP_UIA_PHASE=inspect_process') }",
        1,
    )[0]
    body = "$rootStarted = $null\n" + body.replace(
        "$root = [System.Windows.Automation.AutomationElement]::FromHandle($handle)",
        "$root = Invoke-ProbeRoot",
    )
    replacements = {
        "start": "$rootStarted = [Diagnostics.Stopwatch]::GetTimestamp()",
        "finish": "$rootFinished = [Diagnostics.Stopwatch]::GetTimestamp()",
        "write": "[Console]::Error.WriteLine(('VERDICLIP_UIA_ROOT_US={0}:{1}'"
        " -f $requestId, $rootText))",
    }
    if fault in replacements:
        assert replacements[fault] in body
        body = body.replace(replacements[fault], "throw 'diagnostic-fault'")
    root_result = "throw 'root-fault'" if fault == "root" else "return 'owned-root'"
    script = tmp_path / "root-timing-wrapper.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "[Threading.Thread]::CurrentThread.CurrentCulture = "
        "[Globalization.CultureInfo]::GetCultureInfo('ar-SA')\n"
        f"$request = @{{action='{action}'}}; $requestId='{FIRST}'\n"
        "$calls=0; $result='ok'; $root='missing'\n"
        "function Invoke-ProbeRoot { $script:calls++; " + root_result + " }\n"
        "try {\n" + body + "\n} catch { $result=$_.Exception.Message }\n"
        "[Console]::WriteLine(('{0}:{1}:{2}' -f $calls,$result,$root))\n",
        encoding="utf-8",
    )
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [str(host), "-NoProfile", "-NonInteractive", "-Mta", "-File", str(script)]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "1:root-fault:missing" if fault == "root" else "1:ok:owned-root"
    assert result.stdout.strip() == expected
    trace = WorkerTrace(lambda: 0)
    trace.begin_request(request())
    accepted = trace.error_bytes(result.stderr.encode())
    assert accepted
    if action == "inspect" and fault == "none":
        assert trace.root_duration_us is not None
    else:
        assert trace.root_duration_us is None
        assert not result.stderr


@pytest.mark.parametrize("identity", ["é" * 32, "A" * 32, "", "a" * 1000, "g" * 32])
def test_malformed_identity_cannot_make_optional_diagnostics_throw(
    identity: str, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave actual acceptance to the original protocol and keep cleanup reachable."""
    lease = UiaWorkerLease()
    monkeypatch.setattr(lease, "_dispatch", lambda *_args: None)
    malformed = request(identity)
    lease.submit(malformed, ("unused",), deadline_ms=3000, startup_ms=8000)
    assert lease._pending is malformed
    assert lease.diagnostics.root_duration_us is None
    assert lease.diagnostics.first_inspect_root_duration_us is None
    lease._trace.begin_request(request(SECOND))
    accepted = lease._trace.error_bytes(record(b"10", SECOND))
    assert accepted
    assert lease.diagnostics.root_duration_us == 10
    assert lease.diagnostics.first_inspect_root_duration_us is None
    lease.cancel()
    assert lease._process is None
    assert lease.diagnostics.cleanup == "confirmed"
    qtbot.wait(1)
    lease.deleteLater()
