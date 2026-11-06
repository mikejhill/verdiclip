"""Read installed WPT help only; no recording, fixture, symbols download or upload."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LIMIT = 262144
COMMANDS = {
    "xperf": ("-help", "stack"),
    "wpaexporter": ("-h",),
    "wpr": ("-help", "profiles"),
}
SIGNATURE = r"""
$ErrorActionPreference='Stop'
$p = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/Windows Performance Toolkit'
$items = @{
  xperf=(Join-Path $p 'xperf.exe')
  wpaexporter=(Join-Path $p 'wpaexporter.exe')
  wpr=(Join-Path $env:SystemRoot 'System32/wpr.exe')
}
$r=@{}
foreach($name in @('xperf','wpaexporter','wpr')) {
  $path=$items[$name]
  $ok=$false
  if(Test-Path -LiteralPath $path -PathType Leaf) {
    $s=Get-AuthenticodeSignature -LiteralPath $path
    $ok=$s.Status -eq 'Valid' -and
      $s.SignerCertificate.Subject -match '(^|,\s*)O=Microsoft Corporation(,|$)'
  }
  $v=$null
  if($ok) {
    $f=[Diagnostics.FileVersionInfo]::GetVersionInfo($path)
    $v=('{0}.{1}.{2}.{3}' -f $f.FileMajorPart,$f.FileMinorPart,
      $f.FileBuildPart,$f.FilePrivatePart)
  }
  $r[$name]=@{signed=[bool]$ok;version=$v}
}
$r | ConvertTo-Json -Compress
"""


def no_links(path: Path) -> bool:
    """Reject linked tool/temp ancestry rather than following alternate locations."""
    return all(
        not entry.is_symlink()
        and not (getattr(entry.lstat(), "st_file_attributes", 0) & 0x400)
        for entry in (path, *path.parents)
    )


def private_help(command: list[str], root: Path, label: str) -> str | None:
    """File-backed output avoids pipe locks; enforce size and owned-process bounds."""
    output, errors = root / (label + ".out"), root / (label + ".err")
    with output.open("xb") as stdout, errors.open("xb") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)  # noqa: S603
        try:
            deadline = time.monotonic() + 10
            while process.poll() is None:
                if time.monotonic() >= deadline or any(
                    p.stat().st_size > LIMIT for p in (output, errors)
                ):
                    return None
                time.sleep(0.02)
            if process.returncode != 0 or any(
                p.stat().st_size > LIMIT for p in (output, errors)
            ):
                return None
            return output.read_bytes().decode("utf-8-sig", errors="replace")
        finally:
            try:
                if process.poll() is None:
                    process.kill()
            finally:
                process.wait(timeout=2)


def summarize(help_text: str | None, name: str) -> dict[str, bool]:
    """Report fixed advertised switches, never echo help text or claim runtime proof."""
    text = (help_text or "").lower()
    return {
        "help_available": help_text is not None,
        "advertises_pid": "-pid" in text,
        "advertises_tid": "-tid" in text,
        "advertises_range": "-range" in text,
        "advertises_event": "-event" in text,
        "advertises_export": "-export" in text
        or (name == "wpaexporter" and "-profile" in text),
        "advertises_symbols": "-symbols" in text,
    }


def main() -> int:
    """Run only explicit disposable-host capability inspection, without native UI."""
    if sys.platform != "win32" or any(
        os.environ.get(k) != v
        for k, v in (
            ("GITHUB_ACTIONS", "true"),
            ("RUNNER_ENVIRONMENT", "github-hosted"),
            ("VERDICLIP_WPT_CAPABILITIES", "1"),
        )
    ):
        raise RuntimeError("Disposable capability inspection required")
    base = (
        Path(os.environ["PROGRAMFILES(X86)"])
        / "Windows Kits/10/Windows Performance Toolkit"
    )
    system = Path(os.environ["SYSTEMROOT"]) / "System32"
    tools = {
        "xperf": base / "xperf.exe",
        "wpaexporter": base / "wpaexporter.exe",
        "wpr": system / "wpr.exe",
    }
    powershell = system / "WindowsPowerShell/v1.0/powershell.exe"
    temp = Path(os.environ["RUNNER_TEMP"])
    if not no_links(temp) or not no_links(powershell):
        raise RuntimeError("Installed path ancestry unavailable")
    result: dict[str, object] = {
        "scope": "installed-help-only",
        "capture_performed": False,
        "managed_symbol_export_verified": False,
        "owned_only_capture_verified": False,
        "export_schema_verified": False,
        "status": "capability-only-inconclusive",
    }
    with tempfile.TemporaryDirectory(prefix="verdiclip-wpt-help-", dir=temp) as folder:
        root = Path(folder)
        signature = private_help(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", SIGNATURE],
            root,
            "signatures",
        )
        verified = json.loads(signature) if signature is not None else {}
        for name, path in tools.items():
            info = verified.get(name) if isinstance(verified, dict) else None
            accepted = (
                isinstance(info, dict) and info.get("signed") is True and no_links(path)
            )
            version = info.get("version") if isinstance(info, dict) else None
            version = (
                version
                if isinstance(version, str)
                and re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){3}", version)
                else None
            )
            text = (
                private_help([str(path), *COMMANDS[name]], root, name)
                if accepted
                else None
            )
            result[name] = {
                "signed_installed_tool": accepted,
                "version": version,
                **summarize(text, name),
            }
    # No tool output leaves the private directory; publish only after its removal.
    result["private_cleanup_confirmed"] = not root.exists()
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001 - never expose private tool output or paths
        sys.stdout.write(
            '{"status":"capability-inspection-failed","cleanup":"unconfirmed"}\n'
        )
        code = 2
    raise SystemExit(code)
