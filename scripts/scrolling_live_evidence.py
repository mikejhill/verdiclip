"""Bounded Google-native observations, separate from product acceptance.

This owned harness never supplies crops, masks or registration hints to capture.
Raw UIA roles may fail to explain painted chrome; absence is evidence, not a
license to infer a scrollbar or header from its location or pixel appearance.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import cast

from scripts import simple_scroll_native as native

from verdiclip.capture.scrolling import END_PERCENT, ScrollPosition
from verdiclip.platform.scrolling import UiaScrollTarget
from verdiclip.platform.windows import WindowInfo

_FRAMES = 4
_SECONDS = 70
_HEADER_ROLES = (50034, 50035)
_BAR_ROLE = 50014
_DOCUMENT_ROLE = 50030
_GEOMETRY = r"""
function Snapshot($node, $parent) {
  $current = $node.Current
  $rect = $current.BoundingRectangle
  $bounds = $null
  if (-not $rect.IsEmpty) {
    $bounds = @($rect.Left, $rect.Top, $rect.Width, $rect.Height)
  }
  $range = $null
  $pattern = $null
  if ($node.TryGetCurrentPattern(
      [Windows.Automation.RangeValuePattern]::Pattern, [ref]$pattern)) {
    $value = $pattern.Current
    $range = @{ minimum=$value.Minimum; maximum=$value.Maximum;
      value=$value.Value; large_change=$value.LargeChange;
      small_change=$value.SmallChange; read_only=$value.IsReadOnly }
  }
  return @{ runtime_id=@($node.GetRuntimeId()); parent_runtime_id=$parent;
    role=$current.ControlType.Id; bounds=$bounds;
    hwnd=$current.NativeWindowHandle; pid=$current.ProcessId;
    orientation=[int]$current.Orientation; offscreen=$current.IsOffscreen;
    range=$range }
}
$documentRecord = $null
if ($null -ne $document) {
  $parent = $walker.GetParent($document)
  $documentRecord = Snapshot $document @($parent.GetRuntimeId())
}
$queue = [Collections.Generic.Queue[object]]::new()
$queue.Enqueue(@($root, 0, $null))
$nodes = [Collections.Generic.List[object]]::new()
$truncated = $false
while ($queue.Count -gt 0 -and $nodes.Count -lt 1200 -and
       [DateTime]::UtcNow -lt $deadline) {
  $entry = $queue.Dequeue(); $node = $entry[0]; $depth = $entry[1]
  $record = Snapshot $node $entry[2]
  $nodes.Add($record)
  if ($depth -ge 48) { $truncated = $true; continue }
  $child = $walker.GetFirstChild($node)
  while ($null -ne $child -and $queue.Count -lt 1200 -and
         [DateTime]::UtcNow -lt $deadline) {
    $queue.Enqueue(@($child, ($depth + 1), $record.runtime_id))
    $child = $walker.GetNextSibling($child)
  }
  if ($null -ne $child) { $truncated = $true }
}
@{ document=$documentRecord; root=(Snapshot $root $null);
   owned_root_verified=($null -ne $document); nodes=$nodes.ToArray();
   truncated=($truncated -or $queue.Count -gt 0);
   deadline_reached=([DateTime]::UtcNow -ge $deadline)
} | ConvertTo-Json -Depth 10 -Compress
"""


def geometry_script(discovery: str) -> str:
    """Reuse exactly the owned point-to-document discovery, not its name scan."""
    prefix, marker, _suffix = discovery.partition("$queue =")
    native._require(
        marker and "::FromPoint($point)" in prefix, "Missing native discovery"
    )
    return prefix + _GEOMETRY


def _geometry(window: WindowInfo, script: str, output: Path) -> dict[str, object]:
    powershell = (
        Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = subprocess.run(  # noqa: S603 - owned read-only UIA, no browser injection
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    data = json.loads(result.stdout)
    output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    native._require(
        isinstance(data, dict)
        and data.get("owned_root_verified") is True
        and isinstance(data.get("document"), dict)
        and isinstance(data.get("root"), dict)
        and data["root"].get("hwnd") == window.handle,
        "Diagnostic lost its owned native document",
    )
    return cast("dict[str, object]", data)


def document_identity(data: dict[str, object]) -> tuple[object, ...]:
    """Require actual Document identity/bounds, not a title or pixel heuristic."""
    document = data.get("document")
    native._require(isinstance(document, dict), "Missing native Document")
    record = cast("dict[str, object]", document)
    native._require(
        record.get("role") == _DOCUMENT_ROLE
        and record.get("runtime_id")
        and record.get("parent_runtime_id")
        and record.get("bounds"),
        "Incomplete native Document identity",
    )
    return tuple(
        record.get(key)
        for key in ("runtime_id", "parent_runtime_id", "role", "bounds", "pid", "hwnd")
    )


def positions(view: float) -> list[float]:
    """Four half-viewport steps, chosen independently from matcher outcomes."""
    native._require(0 < view < END_PERCENT, "Diagnostic document is not scrollable")
    step = view / (100 - view) * 50
    values = [min(100.0, index * step) for index in range(_FRAMES)]
    native._require(len(set(values)) == _FRAMES, "Document too short for four samples")
    return values


def _settle(target: UiaScrollTarget, percent: float, deadline: float) -> ScrollPosition:
    previous = None
    while time.monotonic() < deadline:
        target.check()
        current = target.position()
        if current == previous and math.isclose(current.vertical, percent, abs_tol=0.1):
            return current
        previous = current
        time.sleep(0.08)
    raise RuntimeError("Diagnostic UIA position did not settle within its deadline")


def collect(  # noqa: PLR0915 - one bounded observation/restoration lifecycle
    target: UiaScrollTarget,
    window: WindowInfo,
    output: Path,
    discovery: Callable[[WindowInfo], str],
) -> dict[str, object]:
    """Retain failed observations and restore only proven, still-guarded state."""
    output.mkdir(exist_ok=True)
    deadline = time.monotonic() + _SECONDS
    original = target.position()
    area = target.area
    receipt: dict[str, object] = {
        "success": False,
        "product_qualification": False,
        "original": asdict(original),
        "area": asdict(area),
        "restoration": "not_needed",
        "frames": [],
    }
    frames: list[dict[str, object]] = []
    receipt["frames"] = frames
    proven = original
    uncertain = False
    identity: tuple[object, ...] | None = None
    script = geometry_script(discovery(window))
    try:
        initial = _geometry(window, script, output / "original-native.json")
        identity = document_identity(initial)
        for index, percent in enumerate(positions(original.view_size)):
            target.check()
            native._require(time.monotonic() < deadline, "Diagnostic deadline expired")
            native._require(target.position() == proven, "Diagnostic state changed")
            uncertain = True
            target.move(percent)
            current = _settle(target, percent, deadline)
            native._require(
                current.horizontal == original.horizontal
                and current.view_size == original.view_size
                and target.area == area,
                "Diagnostic document extent/bounds changed",
            )
            proven, uncertain = current, False
            entry: dict[str, object] = {"position": asdict(current)}
            frames.append(entry)
            screenshots = []
            for repeat in range(2):
                target.check()
                native._require(
                    time.monotonic() < deadline, "Diagnostic deadline expired"
                )
                data = _geometry(
                    window, script, output / f"f{index}-{repeat}-native.json"
                )
                native._require(
                    document_identity(data) == identity, "Document identity changed"
                )
                native._require(
                    target.position() == proven, "Diagnostic position changed"
                )
                image = target.frame()
                native._require(
                    image.save(str(output / f"f{index}-{repeat}.png")), "Save failed"
                )
                screenshots.append(image)
                nodes = cast("list[dict[str, object]]", data["nodes"])
                entry[f"roles_{repeat}"] = [
                    node
                    for node in nodes
                    if node.get("role") in (*_HEADER_ROLES, _BAR_ROLE)
                ]
                time.sleep(0.08)
            entry["same_position_pixels_equal"] = screenshots[0] == screenshots[1]
            native._require(
                entry["same_position_pixels_equal"], "Same-position pixels changed"
            )
        receipt["success"] = True
    except Exception as error:  # noqa: BLE001 - preserve evidence and guarded restoration
        receipt["error"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            target.check()
            native._require(
                not uncertain and target.position() == proven,
                "No proven eligible state for diagnostic restoration",
            )
            current_geometry = _geometry(window, script, output / "restore-native.json")
            native._require(
                identity is not None
                and document_identity(current_geometry) == identity
                and target.position() == proven
                and target.area == area,
                "Document changed before diagnostic restoration",
            )
            if proven != original:
                target.move(original.vertical)
                restored = _settle(target, original.vertical, time.monotonic() + 3)
                native._require(restored == original, "Diagnostic restore differs")
                receipt["restoration"] = "restored"
            else:
                receipt["restoration"] = "unchanged"
        except Exception as error:  # noqa: BLE001 - never hide original diagnostic outcome
            receipt.update(
                success=False,
                restoration="unproven",
                restoration_error=f"{type(error).__name__}: {error}",
            )
        (output / "result.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )
    return receipt
