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
    assert result.stdout.strip() == "49"


def test_mutation_uses_packaged_same_client_no_focus_implementation() -> None:
    """No managed mutation fallback may silently reintroduce default focusing."""
    folder = Path(uia_process.__file__).parent
    helper = (folder / "uia_helper.ps1").read_text()
    assert "$pattern.SetScrollPercent" not in helper
    assert "[VerdiClip.Automation.NoFocusScroll]::SetPercent(" in helper
    for name in ("uia_nofocus_interop.cs", "uia_nofocus.cs"):
        assert (folder / name).is_file()
        assert name in helper


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
def test_actual_provider_render_callback_activates_only_once(tmp_path: Path) -> None:
    """Invoke the actual callback three times with fakes; never create WPF UI."""
    script = tmp_path / "render-policy.ps1"
    script.write_text(
        "param([string]$Fixture)\n"
        "$ErrorActionPreference='Stop'\n"
        "$source=[IO.File]::ReadAllText($Fixture)\n"
        "$pattern='(?s)\\$window\\.Add_ContentRendered\\(\\{(.*?)\\}\\)'\n"
        "$match=[regex]::Match($source,$pattern)\n"
        "if(-not $match.Success){throw 'callback not found'}\n"
        "$script:ready=$false;$script:renderObserved=$false;$script:activations=0;$script:states=0\n"
        "$window=[pscustomobject]@{}\n"
        "$window|Add-Member ScriptMethod Activate "
        "{$script:activations++;return $true}\n"
        "function Write-FixtureState {$script:states++}\n"
        "$callback=[Action][scriptblock]::Create($match.Groups[1].Value)\n"
        "$callback.Invoke();$callback.Invoke();$callback.Invoke()\n"
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
    assert result.stderr.count("FIXTURE_PHASE=initial-activation") == 1
    assert result.stderr.count("FIXTURE_PHASE=repeat-render-ignored") == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
def test_actual_mutation_guard_uses_only_win32_checks(tmp_path: Path) -> None:
    """Actual delegate executes with fake OS checks and no managed UIA objects."""
    script = tmp_path / "guard-policy.ps1"
    script.write_text(
        "param([string]$Helper)\n"
        "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n"
        "Add-Type -TypeDefinition @'\n"
        "using System; public static class CaptureDesktop {\n"
        "public static int Reject;\n"
        "public static bool Interactive(){return Reject!=1;}\n"
        "public static bool IsWindowVisible(IntPtr h){return Reject!=2;}\n"
        "public static bool IsIconic(IntPtr h){return Reject==3;}\n"
        "public static bool Unoccluded(IntPtr h,int x,int y,int w,int z)"
        "{return Reject!=4 && h.ToInt64()==17 && x==-20 && y==30 "
        "&& w==100 && z==80;} }\n'@\n"
        "$source=[IO.File]::ReadAllText($Helper)\n"
        "$pattern='(?s)\\$permitMutation = \\[Func\\[bool\\]\\] \\{(.*?)\\}'\n"
        "$match=[regex]::Match($source,$pattern)\n"
        "if(-not $match.Success){throw 'delegate missing'}\n"
        "$handle=[IntPtr]17;$area=@(-20,30,100,80)\n"
        "$callback=[Func[bool]][scriptblock]::Create($match.Groups[1].Value)\n"
        "foreach($reject in 0..4){[CaptureDesktop]::Reject=$reject;"
        "if($callback.Invoke() -ne ($reject -eq 0)){throw 'guard mismatch'}}\n"
        "[Console]::WriteLine('five-guard-cases')\n",
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
            "-Helper",
            str(Path(uia_process.__file__).with_name("uia_helper.ps1")),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "five-guard-cases"
