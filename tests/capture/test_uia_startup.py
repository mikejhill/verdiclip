"""Keep discovery compilation separate from native-only scrolling operations."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll import uia_process


def test_discovery_compiles_only_read_only_native_resolver() -> None:
    """Portable guard against compiling the mutation client on discovery startup."""
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1").read_text()
    startup = helper.split("try {\n    if (-not $NativeOnly)", 1)[1].split(
        "} catch {", 1
    )[0]
    discovery, native = startup.split("} else {", 1)
    assert "uia_desktop.cs" in discovery
    assert "uia_discovery.cs" in discovery
    assert "uia_nofocus_interop.cs" in discovery
    assert '"uia_nofocus.cs"' not in discovery
    assert "NoFocusScroll" not in discovery
    assert "UIAutomationClient" not in discovery
    assert "UIAutomationTypes" not in discovery
    assert "WindowsBase" not in discovery
    assert "System.Drawing" in discovery
    assert "Add-Type -AssemblyName" not in native
    assert "uia_nofocus_interop.cs" in native
    assert "uia_nofocus.cs" in native
    assert "uia_desktop.cs" in native
    assert "::RequireNativeOnlyProcess()" in native


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
@pytest.mark.parametrize("native_only", [False, True])
def test_actual_startup_compiles_only_required_types(
    tmp_path: Path, *, native_only: bool
) -> None:
    """Compile the actual startup body in fresh processes, without UIA/desktop calls."""
    script = tmp_path / "startup-probe.ps1"
    script.write_text(
        r"""
param([string]$Helper, [int]$Native)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $Helper, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'Helper syntax failed' }
$startup = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.TryStatementAst]
})[0]
if ($null -eq $startup) { throw 'Startup block missing' }
$Source = Split-Path -Parent $Helper
$NativeOnly = [bool]$Native
$body = '$PSScriptRoot = $Source' + "`n" + (
    ($startup.Body.Statements | ForEach-Object { $_.Extent.Text }) -join "`n")
& ([scriptblock]::Create($body))
if ($null -eq ('CaptureDesktop' -as [type])) { throw 'Desktop checks missing' }
$mutator = 'VerdiClip.Automation.NoFocusScroll' -as [type]
$managed = 'System.Windows.Automation.AutomationElement' -as [type]
if ($NativeOnly) {
    if ($null -eq $mutator) { throw 'Native mutator missing' }
    if ($null -ne $managed) { throw 'Managed UIA loaded in native worker' }
    [VerdiClip.Automation.NoFocusScroll]::RequireNativeOnlyProcess()
} else {
    if ($null -ne $mutator) { throw 'Discovery compiled native mutation client' }
    if ($null -ne $managed) { throw 'Managed discovery UIA loaded' }
    $resolver = 'VerdiClip.Automation.DiscoverySession' -as [type]
    if ($null -eq $resolver) { throw 'Native discovery missing' }
}
[Console]::WriteLine('startup-compilation-passed')
""",
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
            "-Helper",
            str(Path(uia_process.__file__).with_name("uia_helper.ps1")),
            "-Native",
            str(int(native_only)),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "startup-compilation-passed"
