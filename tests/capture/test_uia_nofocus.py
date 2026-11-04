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
    assert result.stdout.strip() == "45"


def test_mutation_uses_packaged_same_client_no_focus_implementation() -> None:
    """No managed mutation fallback may silently reintroduce default focusing."""
    folder = Path(uia_process.__file__).parent
    helper = (folder / "uia_helper.ps1").read_text()
    assert "$pattern.SetScrollPercent" not in helper
    assert "[VerdiClip.Automation.NoFocusScroll]::SetPercent(" in helper
    for name in ("uia_nofocus_interop.cs", "uia_nofocus.cs"):
        assert (folder / name).is_file()
        assert name in helper
