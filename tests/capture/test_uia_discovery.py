"""Native discovery contracts and real Windows compiler tests without desktop use."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll import uia_process
from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace
from verdiclip.capture.scroll.uia_inspect_clock import LABELS

FOLDER = Path(uia_process.__file__).parent


def test_discovery_cannot_mutate_or_load_managed_uia() -> None:
    """Discovery owns cleanup before response and has no mutation fallback."""
    source = (FOLDER / "uia_discovery.cs").read_text()
    for forbidden in (
        "SetScrollPercent(",
        ".SetFocus(",
        "RunOnMta",
        "Thread.Start",
        "FinalReleaseComObject",
    ):
        assert forbidden not in source
    helper = (FOLDER / "uia_helper.ps1").read_text()
    assert "System.Windows.Automation" not in helper
    assert "System.Windows.Point" not in helper
    assert "DiscoverySession" in helper
    assert helper.index("$discovery.Dispose()") < helper.rindex("ok=$true")
    assert "[AggregateException]::new" in helper
    assert "$operationFailure = $_.Exception" in helper
    assert "Math.Floor(value)" in source
    assert "depth<32" in source
    assert "seen.Add(runtime)" in source
    assert "DiscoveryPolicy.Boolean(Property(30034))" in source
    assert "catch (COMException" not in source


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows compiler")
def test_native_discovery_injected_policy(tmp_path: Path) -> None:
    """Compile actual backend ABI but execute only pure resolver and release fakes."""
    script = tmp_path / "discovery-policy.ps1"
    script.write_text(
        "param([string]$Source,[string]$Tests)\n"
        "$ErrorActionPreference='Stop'\n"
        "Add-Type -AssemblyName System.Drawing\n"
        "Add-Type -Path @((Join-Path $Source 'uia_nofocus_interop.cs'),"
        "(Join-Path $Source 'uia_desktop.cs'),"
        "(Join-Path $Source 'uia_discovery.cs'),$Tests)\n"
        "[DiscoveryTests]::Run()\n",
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
            "-Mta",
            "-File",
            str(script),
            "-Source",
            str(FOLDER),
            "-Tests",
            str(Path(__file__).with_name("uia_discovery_fake.cs")),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert int(result.stdout.strip()) >= 60


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
def test_actual_discovery_operation_with_fake_session() -> None:
    """Execute real admission/operations/cleanup with no provider or screen access."""
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-Mta",
            "-File",
            str(Path(__file__).with_name("uia_discovery_request_fake.ps1")),
            "-Helper",
            str(FOLDER / "uia_helper.ps1"),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "discovery-operation-policy-passed"


def test_provider_reads_cross_powershell_as_methods() -> None:
    """Property-getter failures cannot be swallowed by the PowerShell adapter."""
    helper = (FOLDER / "uia_helper.ps1").read_text()
    for getter in (
        "$element.Current.",
        "$pattern.Current.",
        "$element.RuntimeIdentity",
        "$candidate.RuntimeIdentity",
        "$root.RuntimeIdentity",
    ):
        assert getter not in helper
    for method in (
        "ReadBounds()",
        "ReadRuntime()",
        "ReadControlType()",
        "ReadOrientation()",
        "ReadOffscreen()",
        "ReadEnabled()",
        "ReadPosition()",
        "ReadViewSize()",
    ):
        assert method in helper


def test_discovery_failure_markers_are_fixed_and_bounded() -> None:
    """Retain fragmented failure scope while excluding arbitrary provider text."""
    native = (FOLDER / "uia_discovery.cs").read_text()
    phases = set(re.findall(r'(?:[Mm]ark\(|TryMark\(mark,)"([a-z_]+)"', native))
    assert phases
    assert phases <= set(WorkerTrace.MARKERS.values()) | set(LABELS)
    trace = WorkerTrace(lambda: 1.0)
    expected = [
        "inspect_ancestor_process_read",
        "inspect_ancestor_process_guard",
        "discovery_runtime_guard_before",
        "discovery_runtime_call",
        "discovery_runtime_array",
        "discovery_release",
        "discovery_released",
        "discovery_restore",
        "discovery_restore_failed",
    ]
    payload = b"private-provider-text\n" + b"".join(
        f"VERDICLIP_UIA_PHASE={phase}\r\n".encode() for phase in expected
    )
    for byte in payload:
        accepted = trace.error_bytes(bytes([byte]))
        assert accepted
    assert [event.stage for event in trace.events] == expected
    assert trace.dropped_events == 0
    assert trace.MAX_EVENTS == 32
    assert trace.MAX_ERROR_BYTES == 65_536
