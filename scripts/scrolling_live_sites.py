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
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import cast
from unittest.mock import patch

from scripts import simple_scroll_native as native
from scripts.scrolling_live_evidence import collect

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget
from verdiclip.platform.windows import WindowInfo, WindowLocator

_DOCUMENT_ROLE = 50030
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


def _observation_script(window: WindowInfo) -> str:
    """Start at the capture point to request Chromium's native document provider."""
    return (
        r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase
$ErrorActionPreference = 'Stop'
$root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$walker = [Windows.Automation.TreeWalker]::RawViewWalker
$point = [Windows.Point]::new(POINT_X, POINT_Y)
$document = $null
$attempts = 0
$deadline = [DateTime]::UtcNow.AddSeconds(9)
$discoveryDeadline = [DateTime]::UtcNow.AddSeconds(3)
# ElementFromPoint causes the browser to expose the web accessibility provider.
# Reading the chrome's ControlView alone can return no webpage at all.
while ($null -eq $document -and [DateTime]::UtcNow -lt $discoveryDeadline) {
  $attempts++
  $node = [Windows.Automation.AutomationElement]::FromPoint($point)
  $candidate = $null
  for ($depth = 0; $null -ne $node -and $depth -lt 64; $depth++) {
    if ([DateTime]::UtcNow -ge $discoveryDeadline) { break }
    if ([Windows.Automation.Automation]::Compare($node, $root)) {
      $document = $candidate
      break
    }
    if ($node.Current.ControlType -eq
        [Windows.Automation.ControlType]::Document) {
      $candidate = $node
    }
    $node = $walker.GetParent($node)
  }
  if ($null -eq $document) { Start-Sleep -Milliseconds 100 }
}
$queue = [Collections.Generic.Queue[object]]::new()
if ($null -ne $document) { $queue.Enqueue(@($document, 0)) }
$names = [Collections.Generic.List[string]]::new()
$count = 0
while ($queue.Count -gt 0 -and $count -lt 1200 -and
       [DateTime]::UtcNow -lt $deadline) {
  $entry = $queue.Dequeue(); $node = $entry[0]; $depth = $entry[1]
  $count++
  $name = $node.Current.Name
  if ($name) { $names.Add($name.Substring(0, [Math]::Min(500, $name.Length))) }
  if ($depth -lt 48) {
    $child = $walker.GetFirstChild($node)
    while ($null -ne $child -and $queue.Count -lt 1200 -and
           [DateTime]::UtcNow -lt $deadline) {
      $queue.Enqueue(@($child, ($depth + 1)))
      $child = $walker.GetNextSibling($child)
    }
  }
}
@{
  document_found=($null -ne $document)
  owned_root_verified=($null -ne $document)
  point=@(POINT_X, POINT_Y)
  discovery_attempts=$attempts
  nodes_read=$count
  pending_nodes=$queue.Count
  deadline_reached=([DateTime]::UtcNow -ge $deadline)
  names=@($names)
} | ConvertTo-Json -Depth 4 -Compress
""".replace("HANDLE", str(window.handle))
        .replace("POINT_X", str(round(window.bounds.center.x)))
        .replace("POINT_Y", str(round(window.bounds.center.y)))
    )


def _observe(window: WindowInfo, output: Path) -> list[str]:
    """Read only a verified owned native Document, preserving failed discovery."""
    powershell = (
        Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = subprocess.run(  # noqa: S603 - bounded read-only native UIA observation
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _observation_script(window),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    observation = json.loads(result.stdout)
    (output / "native-document-discovery.json").write_text(
        json.dumps(observation, indent=2), encoding="utf-8"
    )
    native._require(
        isinstance(observation, dict)
        and observation.get("document_found") is True
        and observation.get("owned_root_verified") is True,
        "Native UIA did not discover a Document under the owned Edge window",
    )
    names = observation.get("names")
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
        # Observed Google UIA exposes this standalone site label in addition
        # to composite link names. Do not accept a host buried in other text.
        and any(
            name.strip().casefold()
            in ("learn.microsoft.com", "https://learn.microsoft.com")
            for name in names
        )
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
        names = _observe(window, output)
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
        _capture_google(kind, window, output, report)


def _candidate_points(bounds: Rect) -> list[Point]:
    """Propose a small bounded set; native identity, not blank pixels, admits it."""
    return [
        Point(bounds.left + bounds.width * x, bounds.top + bounds.height * y)
        for x, y in ((0.03, 0.5), (0.94, 0.5), (0.03, 0.75), (0.94, 0.75))
    ]


def _open_document(
    window: WindowInfo, owners: ExitStack, report: dict[str, object]
) -> UiaScrollTarget:
    """Select an actual Document rather than a center-point nested panel."""
    attempts: list[dict[str, object]] = []
    report["document_discovery"] = attempts
    for point in _candidate_points(window.bounds):
        entry: dict[str, object] = {"point": asdict(point)}
        attempts.append(entry)
        try:
            target = owners.enter_context(UiaScrollTarget(point, window=window.handle))
            role = target._element.control_type()
            entry.update(role=role, area=asdict(target.area))
            if role == _DOCUMENT_ROLE:
                target.check()
                return target
        except CaptureError as error:
            entry["error"] = str(error)
    raise RuntimeError("Fixture setup could not select a native Document")


def _document_point(
    target: UiaScrollTarget, window: WindowInfo, report: dict[str, object], phase: str
) -> Point:
    """Re-hit-test bounded candidates and require retained native element identity."""
    attempts: list[dict[str, object]] = []
    report[f"document_point_{phase}"] = attempts
    for point in _candidate_points(target.area):
        entry: dict[str, object] = {"point": asdict(point)}
        attempts.append(entry)
        target.check()
        try:
            with UiaScrollTarget(point, window=window.handle) as current:
                same = target._automation.same_element(
                    target._element, current._element
                )
                entry.update(
                    same_document=same,
                    role=current._element.control_type(),
                    area=asdict(current.area),
                    position=asdict(current.position()),
                )
                if (
                    same
                    and current.area == target.area
                    and current.position() == target.position()
                ):
                    target.check()
                    return point
        except CaptureError as error:
            entry["error"] = str(error)
    raise RuntimeError("Fixture point no longer selects the retained native Document")


def _observe_resolved_element(
    target: UiaScrollTarget,
    point: Point,
    window: int,
    entry: dict[str, object],
    guard: Callable[[], None],
) -> None:
    """Observe a new proxy only when native identity and geometry agree."""
    guard()
    with UiaScrollTarget(point, window=window) as fresh:
        guard()
        fresh.check()
        guard()
        same = target._automation.same_element(target._element, fresh._element)
        guard()
        role = fresh._element.control_type()
        guard()
        entry.update(same_document=same, role=role, area=asdict(fresh.area))
        entry["comparable"] = (
            same and role == _DOCUMENT_ROLE and fresh.area == target.area
        )
        if not entry["comparable"]:
            return
        for name, slot in (("horizontal", 5), ("vertical", 6), ("view_size", 8)):
            guard()
            fresh.check()
            guard()
            entry[name] = fresh._scroll.number(slot)
            guard()
            fresh.check()
            guard()


def _observe_pattern_fields(
    target: UiaScrollTarget,
    method: str,
    entry: dict[str, object],
    guard: Callable[[], None],
) -> None:
    """Release temporary interfaces even when a guarded observation fails."""
    with ExitStack() as owners:
        guard()
        pattern = (
            target._element.scroll_pattern(owners)
            if method == "fresh_pattern"
            else target._scroll
        )
        guard()
        if pattern is None:
            raise CaptureError("Retained element has no scroll pattern")
        for name, slot in (
            ("horizontal", 5),
            ("vertical", 6),
            ("view_size", 8),
        ):
            guard()
            entry[name] = pattern.number(slot)
            guard()


def _observe_unresolved_pattern(
    target: UiaScrollTarget, point: Point, window: int
) -> dict[str, object]:
    """Compare retained/fresh native interfaces without changing or borrowing state.

    The ten-second admission bound cannot interrupt an individual COM call;
    the hosted process watchdog remains the outer bound. Results never qualify
    a capture or authorize restoration.
    """
    limit_seconds = 10
    started = time.monotonic()
    observations: list[dict[str, object]] = []
    result: dict[str, object] = {
        "observations": observations,
        "writes": False,
        "product_qualification": False,
        "identity_source": "retained element or CompareElements-verified fresh proxy",
        "point": asdict(point),
        "window": window,
        "area": asdict(target.area),
    }

    def guard() -> None:
        if time.monotonic() - started >= limit_seconds:
            raise TimeoutError("Read-only observation deadline reached")
        target.check()
        if time.monotonic() - started >= limit_seconds:
            raise TimeoutError("Read-only observation deadline reached")

    try:
        for round_number, scheduled in enumerate((0, 0.25, 1, 3, 6)):
            remaining = scheduled - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
            for method in (
                "retained_before",
                "fresh_pattern",
                "resolved_element",
                "retained_after",
            ):
                guard()
                entry: dict[str, object] = {
                    "round": round_number,
                    "method": method,
                    "elapsed_seconds": time.monotonic() - started,
                }
                observations.append(entry)
                if method == "resolved_element":
                    _observe_resolved_element(target, point, window, entry, guard)
                    entry["completed_elapsed_seconds"] = time.monotonic() - started
                    continue
                _observe_pattern_fields(target, method, entry, guard)
                entry["completed_elapsed_seconds"] = time.monotonic() - started
    except (CaptureError, OSError, RuntimeError) as error:
        result["error"] = str(error)
        result["error_type"] = type(error).__name__
        result["deadline_reached"] = isinstance(error, TimeoutError)
    return result


def _capture_google(
    kind: str, window: WindowInfo, output: Path, report: dict[str, object]
) -> None:
    """Keep the intended Document alive through setup, helper and diagnostics."""
    with ExitStack() as owners:
        target = _open_document(window, owners, report)
        report["document_area"] = asdict(target.area)
        _document_point(target, window, report, "before_setup")
        start = native._position_start(
            target, report, nested=False, deadline=time.monotonic() + 10
        )
        point = _document_point(target, window, report, "after_setup")
        area = target.area
        report.update(start=asdict(start), selected_point=asdict(point))
        native._require(target.frame().save(str(output / "before.png")), "Save failed")
        result = native._capture(
            point, output, kind, window=window.handle, input_tick=target._input_tick
        )
        helper_completed_at = time.monotonic()
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
        target.check()
        after = target.position()
        target.check()
        before_image_elapsed = time.monotonic() - helper_completed_at
        native._require(target.frame().save(str(output / "after.png")), "Save failed")
        target.check()
        after_image = target.position()
        target.check()
        report["after"] = asdict(after_image)
        report["after_observations"] = {
            "before_image": {
                "position": asdict(after),
                "elapsed_seconds": before_image_elapsed,
            },
            "after_image": {
                "position": asdict(after_image),
                "elapsed_seconds": time.monotonic() - helper_completed_at,
            },
        }
        if result.restoration == "not restored: the provider write is unresolved":
            report["unresolved_pattern_observations"] = _observe_unresolved_pattern(
                target, point, window.handle
            )
        # Independent evidence observes the retained Document even if a later
        # point resolves to a different child. Product acceptance stays separate.
        if (
            after == after_image == start
            and result.restoration != "not restored: the provider write is unresolved"
        ):
            report["native_diagnostic"] = collect(
                target, window, output / "native-diagnostic", _observation_script
            )
            native._require(target.position() == start, "Diagnostic changed start")
        if (output / "frame-0.png").is_file():
            native._verify_selected_target(output, area, start)
        native._require(
            after == after_image == start,
            "Live-site starting position was not restored",
        )
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
