"""Standalone fixture parsing/draw-contract checks; never construct native UI."""

# ruff: noqa: PT009 - plain unittest avoids repository Qt conftest
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/capture/provider_winforms_fixture.ps1"


@unittest.skipUnless(os.name == "nt", "Installed Windows PowerShell parser")
class WinFormsFixtureSourceTests(unittest.TestCase):
    """Exercise the actual row-draw callback with a fake drawing destination."""

    def test_parser_and_actual_draw_callback_preserve_all_source_rows(self) -> None:
        """First/last rows map exactly; invalid indexes draw nothing, no WinForms."""
        script = r"""
param([string]$Fixture)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$null=[Management.Automation.Language.Parser]::ParseFile($Fixture,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PowerShell parse failure'}
Add-Type -AssemblyName System.Drawing
$source=[IO.File]::ReadAllText($Fixture)
$match=[regex]::Match($source,'(?s)\$viewer\.Add_DrawItem\(\{(.*?)\n    \}\)')
if(-not $match.Success){throw 'Actual callback missing'}
$callback=[scriptblock]::Create($match.Groups[1].Value)
$bitmap='synthetic-pixels';$script:draws=@()
$graphics=[pscustomobject]@{}
$graphics|Add-Member ScriptMethod DrawImage {
    param($image,$destination,$x,$y,$width,$height,$unit)
    $script:draws+=,@($image,$destination.X,$destination.Y,$destination.Width,
        $destination.Height,$x,$y,$width,$height,$unit)
}
foreach($index in @(-1,0,139,140)){
    $event=[pscustomobject]@{
        Index=$index;Bounds=[Drawing.Rectangle]::new(0,24,384,8);Graphics=$graphics
    }
    & $callback $null $event
}
if($draws.Count -ne 2){throw 'Invalid row drawn or valid row missing'}
foreach($draw in $draws){
    if($draw[0] -ne 'synthetic-pixels' -or $draw[1] -ne 0 -or $draw[2] -ne 24 -or
       $draw[3] -ne 384 -or $draw[4] -ne 8 -or $draw[5] -ne 0 -or
       $draw[7] -ne 384 -or $draw[8] -ne 8 -or
       $draw[9] -ne [Drawing.GraphicsUnit]::Pixel){throw 'Changed draw geometry'}
}
if($draws[0][6] -ne 0 -or $draws[1][6]+$draws[1][8] -ne 1120){throw 'Source rows lost'}
[Console]::WriteLine('parser-and-fake-draw-passed')
"""
        temporary_root = ROOT / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="winforms-source-", dir=temporary_root
        ) as directory:
            path = Path(directory) / "check.ps1"
            path.write_text(script, encoding="utf-8")
            host = (
                Path(os.environ["SYSTEMROOT"])
                / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            result = subprocess.run(  # noqa: S603 - fixed parser/fake drawing script
                [
                    str(host),
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(path),
                    "-Fixture",
                    str(FIXTURE),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "parser-and-fake-draw-passed")

    def test_entry_and_owned_protocol_are_preserved(self) -> None:
        """Source contract cannot silently add input or move the execution gate."""
        source = FIXTURE.read_text(encoding="utf-8")
        self.assertLess(
            source.index("if (-not $ExecuteAfterHandoff)"), source.index("Add-Type")
        )
        for forbidden in (
            "SendInput",
            "SetForegroundWindow",
            "mouse_event",
            "keybd_event",
        ):
            self.assertNotIn(forbidden, source)
        for field in (
            "activation_complete",
            "physical_rectangle",
            "started_ticks",
            "end_seen",
            "viewport_height",
            "content_rows",
        ):
            self.assertIn(field, source)
        self.assertIn("$clock.Elapsed.TotalSeconds -ge 90", source)
        self.assertIn("fixture-retired.json", source)
        self.assertIn("SetUnhandledExceptionMode", source)


if __name__ == "__main__":
    unittest.main()
