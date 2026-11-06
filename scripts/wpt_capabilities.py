"""Read installed WPT help only; no recording, fixture, symbols download or upload."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

LIMIT = 262144
COMMANDS = {
    "xperf": ("-help", "stack"),
    "wpaexporter": ("-h",),
    "wpr": ("-help", "profiles"),
}
SIGNATURE = r"""
$ErrorActionPreference='Stop'
try {
  $p = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/Windows Performance Toolkit'
  $items = @{
    xperf=(Join-Path $p 'xperf.exe')
    wpaexporter=(Join-Path $p 'wpaexporter.exe')
    wpr=(Join-Path $env:SystemRoot 'System32/wpr.exe')
  }
  $r=@{}
  foreach($name in @('xperf','wpaexporter','wpr')) {
    $path=$items[$name]
    $v=@{state='probe-error';stage='exists';exists=$null;signature=$null;version=$null}
    try {
      $v.exists=[bool](Test-Path -LiteralPath $path -PathType Leaf)
      if(-not $v.exists) { $v.state='missing' }
      else {
        $v.stage='signature'
        $s=Get-AuthenticodeSignature -LiteralPath $path
        $known=@('Valid','NotSigned','HashMismatch','NotTrusted','UnknownError',
          'NotSupportedFileFormat','Incompatible')
        $v.signature=if($s.Status.ToString() -cin $known) {
          $s.Status.ToString()
        } else { 'other' }
        if($v.signature -cne 'Valid') { $v.state='signature-refused' }
        else {
          $v.stage='publisher'
          if($s.SignerCertificate.Subject -notmatch
            '(^|,\s*)O=Microsoft Corporation(,|$)') { $v.state='publisher-refused' }
          else {
            $v.stage='version'
            $f=[Diagnostics.FileVersionInfo]::GetVersionInfo($path)
            $v.version=('{0}.{1}.{2}.{3}' -f $f.FileMajorPart,$f.FileMinorPart,
              $f.FileBuildPart,$f.FilePrivatePart)
            $v.stage='complete';$v.state='verified'
          }
        }
      }
    } catch { $v.state='probe-error' }
    $r[$name]=$v
  }
  @{schema=1;status='complete';tools=$r} | ConvertTo-Json -Depth 4 -Compress
} catch {
  [Console]::Out.WriteLine('{"schema":1,"status":"bootstrap-error","tools":null}')
}
"""
SIGNATURE_STATES = frozenset(
    {
        "Valid",
        "NotSigned",
        "HashMismatch",
        "NotTrusted",
        "UnknownError",
        "NotSupportedFileFormat",
        "Incompatible",
        "other",
    }
)


@dataclass(frozen=True)
class Outcome:
    """Private text is separate from fixed publishable process evidence."""

    status: str
    exit_code: int | None = None
    text: str | None = None
    error_category: str | None = None

    def public(self) -> dict[str, object]:
        """Never serialize tool output."""
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "error_category": self.error_category,
        }


def error_category(text: str) -> str:
    """Classify only fixed PowerShell error identifiers, never expose stderr."""
    for known in (
        "ParserError",
        "CommandNotFoundException",
        "ParameterBindingException",
        "UnauthorizedAccessException",
        "FileNotFoundException",
    ):
        if known in text:
            return known
    return "unclassified"


def no_links(path: Path) -> bool:
    """Reject linked tool/temp ancestry rather than following alternate locations."""
    return all(
        not entry.is_symlink()
        and not (getattr(entry.lstat(), "st_file_attributes", 0) & 0x400)
        for entry in (path, *path.parents)
    )


def completed_help(code: object, output: Path, errors: Path) -> Outcome:
    """Classify completed output without releasing raw text to the public receipt."""
    if type(code) is not int or not -(2**31) <= code < 2**32:
        return Outcome("exit-code-invalid")
    if code != 0:
        return Outcome(
            "nonzero-exit",
            code,
            error_category=error_category(
                errors.read_bytes().decode("utf-8-sig", errors="replace")
            ),
        )
    try:
        text = output.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        return Outcome("decode-error", code)
    return Outcome("completed", code, text)


def private_help(command: list[str], root: Path, label: str) -> Outcome:
    """Keep the existing ten-second execution and independent two-second reap."""
    output, errors = root / (label + ".out"), root / (label + ".err")
    with output.open("xb") as stdout, errors.open("xb") as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)  # noqa: S603
        except OSError:
            return Outcome("spawn-error")
        try:
            deadline = time.monotonic() + 10
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    return Outcome("timeout")
                if any(p.stat().st_size > LIMIT for p in (output, errors)):
                    return Outcome("output-limit")
                time.sleep(0.02)
            if any(p.stat().st_size > LIMIT for p in (output, errors)):
                return Outcome("output-limit")
            return completed_help(process.returncode, output, errors)
        finally:
            try:
                if process.poll() is None:
                    process.kill()
            finally:
                process.wait(timeout=2)


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Duplicate helper keys are ambiguous and cannot authorize tool execution."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def valid_tool(value: object) -> bool:
    """Validate exact fixed schema and state consistency before accepting proof."""
    if not isinstance(value, dict) or set(value) != {
        "state",
        "stage",
        "exists",
        "signature",
        "version",
    }:
        return False
    state, stage, exists, signature, version = (
        value[k] for k in ("state", "stage", "exists", "signature", "version")
    )
    if exists is not None and type(exists) is not bool:
        return False
    if signature is not None and (
        not isinstance(signature, str) or signature not in SIGNATURE_STATES
    ):
        return False
    if version is not None and (
        not isinstance(version, str)
        or re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){3}", version) is None
    ):
        return False
    return valid_state(state, stage, exists, signature, version)


def valid_state(
    state: object, stage: object, exists: object, signature: object, version: object
) -> bool:
    """Reject inconsistent helper claims before any tool execution."""
    if state == "missing":
        return (
            stage == "exists"
            and exists is False
            and signature is None
            and version is None
        )
    if state == "signature-refused":
        return (
            stage == "signature"
            and exists is True
            and signature is not None
            and signature != "Valid"
            and version is None
        )
    if state == "publisher-refused":
        return (
            stage == "publisher"
            and exists is True
            and signature == "Valid"
            and version is None
        )
    if state == "verified":
        return (
            stage == "complete"
            and exists is True
            and signature == "Valid"
            and version is not None
        )
    return (
        state == "probe-error"
        and isinstance(stage, str)
        and stage in {"exists", "signature", "publisher", "version"}
        and version is None
    )


def decode_signature(outcome: Outcome) -> tuple[str, dict[str, object]]:
    """Unknown helper evidence stays unknown, never three false tool judgments."""
    if outcome.status != "completed":
        return "helper-unavailable", {}
    try:
        value = json.loads(outcome.text or "", object_pairs_hook=unique_object)
    except ValueError:
        return "json-invalid", {}
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "status", "tools"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
    ):
        return "schema-invalid", {}
    if value["status"] == "bootstrap-error" and value["tools"] is None:
        return "bootstrap-error", {}
    tools = value["tools"]
    if (
        value["status"] != "complete"
        or not isinstance(tools, dict)
        or set(tools) != set(COMMANDS)
        or not all(valid_tool(v) for v in tools.values())
    ):
        return "schema-invalid", {}
    return "validated", cast("dict[str, object]", tools)


def inspect_tool(name: str, path: Path, root: Path, info: object) -> dict[str, object]:
    """No unknown or refused verification can reach the help subprocess."""
    if not valid_tool(info):
        return {
            "verification": "unknown",
            "signed_installed_tool": None,
            "help": {"status": "not-run-unverified"},
        }
    assert isinstance(info, dict)  # noqa: S101 - validated schema
    report: dict[str, object] = dict(info)
    report["signed_installed_tool"] = (
        True
        if info["state"] == "verified"
        else None
        if info["state"] == "probe-error"
        else False
    )
    if info["state"] != "verified":
        report["help"] = {"status": "not-run-unverified"}
        return report
    try:
        accepted = no_links(path)
    except OSError:
        report["path_status"] = "unavailable"
        accepted = False
    else:
        report["path_status"] = "accepted" if accepted else "refused"
    if not accepted:
        report["help"] = {"status": "not-run-path"}
        return report
    outcome = private_help([str(path), *COMMANDS[name]], root, name)
    report["help"] = outcome.public()
    report.update(summarize(outcome.text, name))
    return report


def summarize(help_text: str | None, name: str) -> dict[str, bool | None]:
    """Report fixed advertised switches, never echo help text or claim runtime proof."""
    text = (help_text or "").lower()
    observed = {
        "help_available": help_text is not None,
        "advertises_pid": "-pid" in text,
        "advertises_tid": "-tid" in text,
        "advertises_range": "-range" in text,
        "advertises_event": "-event" in text,
        "advertises_export": "-export" in text
        or (name == "wpaexporter" and "-profile" in text),
        "advertises_symbols": "-symbols" in text,
    }

    return {
        key: value if help_text is not None or key == "help_available" else None
        for key, value in observed.items()
    }


def main(progress: dict[str, str]) -> int:
    """Run only explicit disposable-host capability inspection, without native UI."""
    progress["stage"] = "host-guard"
    if sys.platform != "win32" or any(
        os.environ.get(k) != v
        for k, v in (
            ("GITHUB_ACTIONS", "true"),
            ("RUNNER_ENVIRONMENT", "github-hosted"),
            ("VERDICLIP_WPT_CAPABILITIES", "1"),
        )
    ):
        raise RuntimeError("Disposable capability inspection required")
    progress["stage"] = "installed-paths"
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
    progress["stage"] = "private-directory"
    with tempfile.TemporaryDirectory(prefix="verdiclip-wpt-help-", dir=temp) as folder:
        root = Path(folder)
        progress["stage"] = "signature-helper"
        signature = private_help(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", SIGNATURE],
            root,
            "signatures",
        )
        progress["stage"] = "signature-schema"
        schema_status, verified = decode_signature(signature)
        result["signature_helper"] = signature.public()
        result["signature_schema"] = schema_status
        for name, path in tools.items():
            progress["stage"] = name + "-help"
            result[name] = inspect_tool(name, path, root, verified.get(name))
        progress["stage"] = "private-cleanup"
    # No tool output leaves the private directory; publish only after its removal.
    result["private_cleanup_confirmed"] = not root.exists()
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    progress: dict[str, str] = {}
    try:
        code = main(progress)
    except Exception:  # noqa: BLE001 - never expose private tool output or paths
        sys.stdout.write(
            json.dumps(
                {
                    "status": "capability-inspection-failed",
                    "cleanup": "unconfirmed",
                    "stage": progress.get("stage", "entry"),
                }
            )
            + "\n"
        )
        code = 2
    raise SystemExit(code)
