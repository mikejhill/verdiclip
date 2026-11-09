"""Observe real public sites in installed Edge, never a local-site substitute.

Run only on a disposable hosted Windows desktop. Google is a capture/restoration
smoke gate, not an independent completeness oracle. The To Do access probe
cannot qualify a signed-in task/settings panel and never reports capture success.
All observations use native windows, UIA and screenshots; no DOM or injected code.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import cast
from unittest.mock import patch

from scripts import simple_scroll_native as native

from verdiclip.platform.scrolling import UiaScrollTarget
from verdiclip.platform.windows import WindowInfo, WindowLocator

_URLS = {
    "edge-google-live": "https://www.google.com/search?q=Windows+UI+Automation+ScrollPattern",
    "edge-todo-access": "https://to-do.live.com/tasks/",
}


def require_hosted() -> None:
    """Never launch an unrequested live-site test on a personal desktop."""
    native._require(
        sys.platform == "win32"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
        "Live-site diagnostics require a disposable hosted Windows desktop",
    )


def _pid(handle: int) -> int:
    user = c.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = (w.HWND, c.POINTER(w.DWORD))
    user.GetWindowThreadProcessId.restype = w.DWORD
    pid = w.DWORD()
    native._require(
        user.GetWindowThreadProcessId(handle, c.byref(pid)), "Missing window owner"
    )
    return pid.value


def _observe(window: WindowInfo) -> list[str]:
    """Read bounded native accessible names from this exact owned HWND."""
    powershell = (
        Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    script = r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$walker = [Windows.Automation.TreeWalker]::ControlViewWalker
$queue = [Collections.Generic.Queue[object]]::new()
$queue.Enqueue(@($root, 0))
$names = [Collections.Generic.List[string]]::new()
$deadline = [DateTime]::UtcNow.AddSeconds(5)
$count = 0
while ($queue.Count -gt 0 -and $count -lt 500 -and [DateTime]::UtcNow -lt $deadline) {
  $entry = $queue.Dequeue(); $node = $entry[0]; $depth = $entry[1]
  $count++
  $name = $node.Current.Name
  if ($name) { $names.Add($name.Substring(0, [Math]::Min(500, $name.Length))) }
  if ($depth -lt 12) {
    $child = $walker.GetFirstChild($node)
    while ($null -ne $child -and $queue.Count -lt 500 -and
           [DateTime]::UtcNow -lt $deadline) {
      $queue.Enqueue(@($child, ($depth + 1)))
      $child = $walker.GetNextSibling($child)
    }
  }
}
ConvertTo-Json -InputObject @($names) -Compress
""".replace("HANDLE", str(window.handle))
    result = subprocess.run(  # noqa: S603 - bounded read-only native UIA observation
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    names = json.loads(result.stdout)
    native._require(
        isinstance(names, list) and all(isinstance(name, str) for name in names),
        "UIA observation did not return accessible names",
    )
    return cast("list[str]", names)


def google_results_ready(title: str, names: list[str]) -> bool:
    """Require actual result landmarks, not a browser tab or query URL alone."""
    joined = "\n".join(names).casefold()
    return (
        "google" in title.casefold()
        and "scrollpattern" in title.casefold()
        and any(name.casefold() in ("search results", "main") for name in names)
        and "learn.microsoft.com" in joined
        and not any(
            marker in joined
            for marker in (
                "unusual traffic",
                "before you continue to google",
                "verify you are human",
                "can't reach this page",
            )
        )
    )


class LiveSite:
    """Own one fresh Edge process/profile and inspect only its native window."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None

    def launch(
        self, kind: str, directory: Path, report: dict[str, object]
    ) -> subprocess.Popen[bytes]:
        """Use an actual public URL and never import personal browser state."""
        require_hosted()
        url = _URLS[kind]
        candidates = [
            Path(os.environ.get(key, "")) / "Microsoft/Edge/Application/msedge.exe"
            for key in ("ProgramFiles(x86)", "ProgramFiles")
        ]
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise RuntimeError("Installed Microsoft Edge was not found")
        report.update(
            requested_url=url,
            executable=str(executable),
            oracle="live-site capture/restoration smoke; human content review required",
            product_capture_qualification=False,
            fresh_unsigned_profile=True,
        )
        native._record_version(executable, report)
        self.process = subprocess.Popen(  # noqa: S603 - installed Edge, fixed public URL
            [
                str(executable),
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                f"--user-data-dir={directory / 'profile'}",
                url,
            ]
        )
        return self.process

    def window(self, *, deadline: float | None = None) -> WindowInfo:
        """Bind identity to the launched process, not a matching page title."""
        if deadline is None:
            deadline = time.monotonic() + 20
        if self.process is None:
            raise RuntimeError("No owned Edge process")
        while time.monotonic() < deadline:
            matches = [
                window
                for window in WindowLocator().visible_windows()
                if _pid(window.handle) == self.process.pid
            ]
            native._require(len(matches) <= 1, "Ambiguous owned Edge windows")
            if matches:
                return matches[0]
            time.sleep(0.1)
        raise RuntimeError("The owned Edge process did not expose a visible window")

    def case(self, kind: str, output: Path, report: dict[str, object]) -> None:
        """Retain evidence before any readiness failure, then run the real helper."""
        window = native._prepare(self.window())
        report["window"] = asdict(window)
        # Public networking is asynchronous. This fixed allowance is setup only.
        time.sleep(8)
        window = self.window()
        report["loaded_window"] = asdict(window)
        native._desktop_evidence(output, report)
        names = _observe(window)
        (output / "accessible-names.json").write_text(
            json.dumps(names, indent=2), encoding="utf-8"
        )
        if kind == "edge-todo-access":
            report.update(
                success=False,
                access_probe_completed=True,
                capture_attempted=False,
                prerequisite="Signed-in Microsoft To Do with the user's reported "
                "task/settings list open is required; this fresh profile cannot "
                "qualify that panel. Inspect the public-page screenshot/UIA names.",
            )
            return
        native._require(
            google_results_ready(window.title, names),
            "Google search results not verified; inspect live screenshot and UIA "
            "names for consent, challenge, network failure or changed page layout",
        )
        point = window.bounds.center
        with UiaScrollTarget(point, window=window.handle) as target:
            start = native._position_start(
                target, report, nested=False, deadline=time.monotonic() + 10
            )
            area = target.area
            report["start"] = asdict(start)
            native._require(
                target.frame().save(str(output / "before.png")), "Save failed"
            )
            tick = target._input_tick
        result = native._capture(
            point, output, kind, window=window.handle, input_tick=tick
        )
        report.update(
            capture_attempted=True,
            message=result.message,
            restoration=result.restoration,
            frames=result.frames,
        )
        if result.image is not None:
            native._require(
                result.image.save(str(output / "result.png")), "Save failed"
            )
        with UiaScrollTarget(point, window=window.handle) as target:
            after = target.position()
            report["after"] = asdict(after)
            native._require(
                target.frame().save(str(output / "after.png")), "Save failed"
            )
        if (output / "frame-0.png").is_file():
            native._verify_selected_target(output, area, start)
        native._require(after == start, "Live-site starting position was not restored")
        native._require(
            result.image is not None and result.restoration == "restored",
            result.message or result.restoration,
        )
        report.update(success=True, capture_restoration_smoke_passed=True)


def main() -> None:
    """Keep a failed live probe visibly separate from owned fixture passes."""
    require_hosted()
    native._require(
        len(sys.argv[1:]) == 1 and sys.argv[1] in _URLS, "Unknown live-site case"
    )
    owner = LiveSite()
    with (
        patch.object(native, "_launch", owner.launch),
        patch.object(native, "_window", owner.window),
    ):
        native.main(case=owner.case)


if __name__ == "__main__":
    main()
