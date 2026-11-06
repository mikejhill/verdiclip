"""Execute the fixture's fixed wire format without constructing any native UI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture.scroll_peer import ScrollPeer
from tests.capture.test_scroll_metric_policy import run_policy_probe

FIXTURE = Path(__file__).with_name("provider_fixture.ps1")
NONCE = "0123456789abcdef0123456789abcdef"

PROBE = r"""
param([string]$Fixture)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $Fixture, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'Fixture syntax failed' }
[Console]::Error.WriteLine('FIXTURE_JSON_PHASE=parsed')
foreach ($name in @('ConvertTo-FixtureJsonValue', 'ConvertTo-FixtureStateJson',
    'ConvertTo-FixtureAckJson', 'ConvertTo-FixtureRetiredJson', 'Write-FixtureState')) {
    $function = $ast.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true)
    if ($null -eq $function) { throw 'Fixture function missing' }
    . ([scriptblock]::Create($function.Extent.Text))
}
[Console]::Error.WriteLine('FIXTURE_JSON_PHASE=functions-bound')
function Assert-Refused([scriptblock]$action) {
    $failed = $false
    try { $null = & $action } catch { $failed = $true }
    if (-not $failed) { throw 'Unsafe fixture value accepted' }
}
$nonce = '0123456789abcdef0123456789abcdef'
$state = @{
    nonce=$nonce; pid=123; hwnd=[long]::MaxValue; started_ticks=[long]638980000000000001
    sequence=1; visible=$true; closed=$false; activation_complete=$true
    offset=[double]0; scrollable_height=[double]840.25; maximum_offset=([double]2/3)
    end_seen=$false; viewport_height=[double]280; content_rows=1120
    scrollbar_x=-2147483648; scrollbar_y=2147483647
    physical_rectangle=@{x=-2147483648;y=-16;width=384;height=280}
}
$ack = @{nonce=$nonce; sequence=[long]::MaxValue; offset=([double]1/3)}
$receipt = @{nonce=$nonce; pid=123; closed=$true}
foreach ($culture in @('en-US', 'de-DE', 'tr-TR')) {
    $selectedCulture = [Globalization.CultureInfo]::GetCultureInfo($culture)
    [Threading.Thread]::CurrentThread.CurrentCulture = $selectedCulture
    [Console]::WriteLine((ConvertTo-FixtureStateJson $state))
    [Console]::WriteLine((ConvertTo-FixtureAckJson $ack))
    [Console]::WriteLine((ConvertTo-FixtureRetiredJson $receipt))
}
foreach ($number in @([double]::MaxValue, [double]::Epsilon, [double]-1.25,
    [long]::MinValue, [long]::MaxValue)) {
    [Console]::WriteLine((ConvertTo-FixtureJsonValue $number 'number'))
}
foreach ($bad in @('"', '\', ($nonce + "`n"), ('a' * 31), ('a' * 33),
    ('A' * 32), ([string][char]0x00e9 * 32), $null, 123)) {
    Assert-Refused { ConvertTo-FixtureJsonValue $bad 'nonce' }
}
foreach ($bad in @('123', $true, [double]1, [decimal]1, $null)) {
    Assert-Refused { ConvertTo-FixtureJsonValue $bad 'integer' }
}
foreach ($bad in @([double]::NaN, [double]::PositiveInfinity,
    [double]::NegativeInfinity, '1e9', $false, [decimal]1.25, $null)) {
    Assert-Refused { ConvertTo-FixtureJsonValue $bad 'number' }
}
foreach ($bad in @('true', 1, $null)) {
    Assert-Refused { ConvertTo-FixtureJsonValue $bad 'boolean' }
}
Assert-Refused { ConvertTo-FixtureJsonValue 1 'unknown' }
$broken = $state.Clone(); $broken.hwnd = '9223372036854775807'
Assert-Refused { ConvertTo-FixtureStateJson $broken }
$broken = $state.Clone(); $broken.physical_rectangle = @{x='0';y=0;width=1;height=1}
Assert-Refused { ConvertTo-FixtureStateJson $broken }
$broken = $state.Clone(); $broken.physical_rectangle = 'untrusted content'
Assert-Refused { ConvertTo-FixtureStateJson $broken }
$broken = $state.Clone(); $broken.Remove('visible')
Assert-Refused { ConvertTo-FixtureStateJson $broken }
Assert-Refused {
    ConvertTo-FixtureAckJson @{nonce=$nonce;sequence=1;offset=[double]::NaN}
}
Assert-Refused { ConvertTo-FixtureRetiredJson @{nonce=$nonce;pid=123;closed='true'} }
[Console]::Error.WriteLine('FIXTURE_JSON_PHASE=values-verified')
# Execute the real entry guard before it can touch unavailable native geometry.
$fixtureDirectory = [IO.Path]::GetDirectoryName($PSCommandPath)
$stopPath = Join-Path $fixtureDirectory 'fixture-stop.json'
[IO.File]::WriteAllText($stopPath, '{}')
$ready = $true; $closed = $false
$window = [pscustomobject]@{ IsLoaded=$true }
Write-FixtureState
# Execute the actual atomic-publication tail with cancellation already present.
$source = [IO.File]::ReadAllText($Fixture)
$pattern = '(?s)(\$temporary = Join-Path \$fixtureDirectory ' +
    '''fixture-state.partial''.*?)Write-SnapshotPhase ''published'''
$match = [regex]::Match($source, $pattern)
if (-not $match.Success) { throw 'Fixture publication tail missing' }
$json = ConvertTo-FixtureStateJson $state
$sequence = 1
& ([scriptblock]::Create($match.Groups[1].Value))
$target = Join-Path $fixtureDirectory 'fixture-state-000001.json'
if ([IO.File]::Exists($target)) { throw 'Cancelled fixture published readiness' }
$partial = Join-Path $fixtureDirectory 'fixture-state.partial'
if ([IO.File]::ReadAllText($partial) -cne $json) { throw 'Private staging changed' }
[IO.File]::Delete($stopPath)
& ([scriptblock]::Create($match.Groups[1].Value))
if ([IO.File]::ReadAllText($target) -cne $json) {
    throw 'Live fixture publication changed'
}
if ([IO.File]::Exists($partial)) { throw 'Publication did not rename staging' }
[Console]::Error.WriteLine('FIXTURE_JSON_PHASE=completed')
"""


def test_wpf_serialization_and_cancellation_source_contract() -> None:
    """No general serializer or unguarded startup publication returns to WPF."""
    source = FIXTURE.read_text()
    assert "ConvertTo-Json" not in source
    assert "ConvertTo-FixtureStateJson $state" in source
    assert "ConvertTo-FixtureAckJson @{" in source
    assert "ConvertTo-FixtureRetiredJson @{" in source
    stop = "if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json')))"
    snapshot = source.split("function Write-FixtureState {", 1)[1].split("$timer =", 1)[
        0
    ]
    assert snapshot.index(stop) < snapshot.index("Write-SnapshotPhase 'geometry-start'")
    assert snapshot.rindex(stop) < snapshot.index("[IO.File]::Move($temporary,$target)")
    assert snapshot.index("[IO.File]::Move($temporary,$target)") < snapshot.index(
        "$script:snapshotTraceComplete = $true"
    )
    rendered = source.split("$window.Add_ContentRendered({", 1)[1].split("})", 1)[0]
    assert rendered.index(stop) < rendered.index("Confirm-FixtureActivation $window")
    assert (
        rendered.index("Write-FixtureState")
        < rendered.index("if ($script:snapshotTraceComplete)")
        < rendered.index("FIXTURE_PHASE=initial-snapshot-written")
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
def test_actual_fixture_frames_are_invariant_safe_and_peer_compatible(
    tmp_path: Path,
) -> None:
    """Parse the actual script and verify wire types, hostile values and retirement."""
    probe = tmp_path / "fixture-json.ps1"
    probe.write_text(PROBE, encoding="utf-8")
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
            str(probe),
            "-Fixture",
            str(FIXTURE),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    frames = [json.loads(line) for line in result.stdout.splitlines()]
    expected = {
        "nonce": NONCE,
        "pid": 123,
        "hwnd": 2**63 - 1,
        "started_ticks": 638980000000000001,
        "sequence": 1,
        "visible": True,
        "closed": False,
        "activation_complete": True,
        "offset": 0.0,
        "scrollable_height": 840.25,
        "maximum_offset": 2 / 3,
        "end_seen": False,
        "viewport_height": 280.0,
        "content_rows": 1120,
        "scrollbar_x": -(2**31),
        "scrollbar_y": 2**31 - 1,
        "physical_rectangle": {"x": -(2**31), "y": -16, "width": 384, "height": 280},
    }
    for index in range(0, 9, 3):
        state, ack, receipt = frames[index : index + 3]
        assert state == expected
        assert {key: type(value) for key, value in state.items()} == {
            key: type(value) for key, value in expected.items()
        }
        assert all(type(value) is int for value in state["physical_rectangle"].values())
        assert ack == {"nonce": NONCE, "sequence": 2**63 - 1, "offset": 1 / 3}
        assert type(ack["sequence"]) is int
        assert type(ack["offset"]) is float
        assert receipt == {"nonce": NONCE, "pid": 123, "closed": True}
        assert type(receipt["pid"]) is int
        assert type(receipt["closed"]) is bool
    assert frames[9:] == [sys.float_info.max, 5e-324, -1.25, -(2**63), 2**63 - 1]
    assert "FIXTURE_JSON_PHASE=completed" in result.stderr
    peer = ScrollPeer(tmp_path)
    peer.nonce = NONCE
    peer.process = cast(
        subprocess.Popen[bytes],
        SimpleNamespace(
            pid=123, poll=lambda: None, wait=lambda **_: None, returncode=0
        ),
    )
    assert peer.poll()
    assert peer.handle == 2**63 - 1
    assert peer.state == expected
    (tmp_path / "fixture-retired.json").write_text(json.dumps(frames[2]))
    peer.close()
    assert peer._closed
