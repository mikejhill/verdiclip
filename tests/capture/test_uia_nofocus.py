"""Compile actual native bindings; exercise policy with fakes, never desktop COM."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll import uia_process


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows .NET compiler")
def test_nofocus_policy_with_compiled_fake_client(tmp_path: Path) -> None:
    """Wrong target/policy refuses mutation; valid calls preserve client and order."""
    folder = Path(uia_process.__file__).parent
    script = tmp_path / "compile-policy.ps1"
    script.write_text(
        "param([string]$Source,[string]$Tests)\n"
        "$ErrorActionPreference='Stop'\n"
        "Add-Type -Path @((Join-Path $Source 'uia_nofocus_interop.cs'), "
        "(Join-Path $Source 'uia_nofocus.cs'), $Tests)\n"
        "[NoFocusPolicyTests]::Run()\n",
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
            str(folder),
            "-Tests",
            str(Path(__file__).with_name("uia_nofocus_fake.cs")),
        ],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "65"


def test_mutation_uses_packaged_same_client_no_focus_implementation() -> None:
    """No managed mutation fallback may silently reintroduce default focusing."""
    folder = Path(uia_process.__file__).parent
    helper = (folder / "uia_helper.ps1").read_text()
    assert "$pattern.SetScrollPercent" not in helper
    assert "native_process_required" in helper
    assert "Invoke-NativeScrollRequest $request" in helper
    native = (folder / "uia_native_request.ps1").read_text()
    assert "[VerdiClip.Automation.NoFocusScroll]::Mutate(" in native
    for name in ("uia_nofocus_interop.cs", "uia_nofocus.cs", "uia_desktop.cs"):
        assert (folder / name).is_file()
        assert name in helper


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
def test_actual_provider_render_callback_activates_only_once(tmp_path: Path) -> None:
    """Cancellation cannot claim publication; repeated renders never reactivate."""
    fixture = Path(__file__).with_name("provider_fixture.ps1").read_text()
    activation = (
        "function Confirm-FixtureActivation"
        + fixture.split("function Confirm-FixtureActivation", 1)[1].split(
            "function Write-FixtureState", 1
        )[0]
    )
    script = tmp_path / "render-policy.ps1"
    script.write_text(
        "param([string]$Fixture)\n"
        "$ErrorActionPreference='Stop'\n"
        "$clock=[Diagnostics.Stopwatch]::StartNew()\n"
        "function Write-RenderPhase([string]$Phase) {\n"
        "[Console]::Error.WriteLine(('render-phase={0};elapsed-ms={1}' "
        "-f $Phase,$clock.ElapsedMilliseconds))\n"
        "}\n"
        "Write-RenderPhase 'entry'\n"
        "$source=[IO.File]::ReadAllText($Fixture)\n"
        "$fixtureDirectory=[IO.Path]::GetDirectoryName($PSCommandPath)\n"
        "Write-RenderPhase 'source-read'\n"
        "$pattern='(?s)\\$window\\.Add_ContentRendered\\(\\{(.*?)\\}\\)'\n"
        "$match=[regex]::Match($source,$pattern)\n"
        "if(-not $match.Success){throw 'callback not found'}\n"
        "Write-RenderPhase 'callback-extracted'\n"
        "$script:ready=$false;$script:renderObserved=$false;$script:activations=0;$script:states=0\n"
        "$script:stopDuringActivation=$false\n"
        "$window=[pscustomobject]@{IsActive=$false;IsLoaded=$true}\n"
        "$window|Add-Member ScriptMethod Activate "
        "{$script:activations++;$this.IsActive=$true;"
        "if($script:stopDuringActivation){[IO.File]::WriteAllText($stopPath,'{}')};"
        "return $true}\n" + activation + "\n"
        "$tokens=$null;$errors=$null\n"
        "$ast=[Management.Automation.Language.Parser]::ParseFile("
        "$Fixture,[ref]$tokens,[ref]$errors)\n"
        "if($errors.Count){throw 'Fixture syntax failed'}\n"
        "$stateFunction=$ast.Find({param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Write-FixtureState'},$true)\n"
        "if($null -eq $stateFunction){throw 'Snapshot function missing'}\n"
        ". ([scriptblock]::Create($stateFunction.Extent.Text))\n"
        "$actualWriteFixtureState=${function:Write-FixtureState}\n"
        "function Write-FixtureState {\n"
        "$script:states++\n"
        "if($script:stopDuringActivation){& $actualWriteFixtureState;return}\n"
        "$script:snapshotTraceComplete=$true\n"
        "}\n"
        "$callback=[Action][scriptblock]::Create($match.Groups[1].Value)\n"
        "Write-RenderPhase 'callback-bound'\n"
        "$stopPath=Join-Path $fixtureDirectory 'fixture-stop.json'\n"
        "[IO.File]::WriteAllText($stopPath,'{}')\n"
        "$callback.Invoke()\n"
        "if($script:activations -ne 0 -or $script:states -ne 0 "
        "-or $script:ready){throw 'Cancelled render activated or published'}\n"
        "[IO.File]::Delete($stopPath)\n"
        "Write-RenderPhase 'cancelled-returned'\n"
        "$script:stopDuringActivation=$true;$closed=$false\n"
        "$callback.Invoke()\n"
        "if($script:activations -ne 1 -or $script:states -ne 1 "
        "-or $script:snapshotTraceComplete){throw 'Late cancellation published'}\n"
        "Write-RenderPhase 'late-cancel-returned'\n"
        "[IO.File]::Delete($stopPath)\n"
        "$script:stopDuringActivation=$false;$script:ready=$false\n"
        "$script:activations=0;$script:states=0;$window.IsActive=$false\n"
        "$callback.Invoke();Write-RenderPhase 'first-returned'\n"
        "$callback.Invoke();Write-RenderPhase 'second-returned'\n"
        "$callback.Invoke();Write-RenderPhase 'third-returned'\n"
        "if($script:activations -ne 1 -or $script:states -ne 1 "
        "-or -not $script:ready){throw 'repeat activation'}\n"
        "[Console]::WriteLine('one-activation')\n",
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
            "-Fixture",
            str(Path(__file__).with_name("provider_fixture.ps1")),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "one-activation"
    assert result.stderr.count("FIXTURE_PHASE=initial-activation") == 2
    assert result.stderr.count("FIXTURE_PHASE=repeat-render-ignored") == 1
    assert result.stderr.count("FIXTURE_PHASE=initial-snapshot-written") == 1
    assert (
        "FIXTURE_PHASE=initial-snapshot-written"
        not in result.stderr.split("render-phase=late-cancel-returned", 1)[0]
    )
    phases = [
        line.split(";", 1)[0].removeprefix("render-phase=")
        for line in result.stderr.splitlines()
        if line.startswith("render-phase=")
    ]
    assert phases == [
        "entry",
        "source-read",
        "callback-extracted",
        "callback-bound",
        "cancelled-returned",
        "late-cancel-returned",
        "first-returned",
        "second-returned",
        "third-returned",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
def test_actual_native_request_conversion_refuses_unsafe_inputs() -> None:
    """Execute production conversion against a fake mutator, without desktop UIA."""
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
            str(Path(__file__).with_name("uia_native_request_fake.ps1")),
            "-Source",
            str(Path(uia_process.__file__).with_name("uia_native_request.ps1")),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "native-request-policy-passed"
