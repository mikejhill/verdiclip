"""Independent retained-link observations; never product registration evidence."""

from __future__ import annotations

import ctypes.wintypes as w
import json
import math
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import TypeVar, cast

from PySide6.QtGui import QImage

from verdiclip.capture.scrolling import END_PERCENT, ScrollPosition
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect
from verdiclip.platform.scrolling import UiaScrollTarget, _Interface

_LINK = 50005
_DOCUMENT = 50030
_NODES = 1200
_DEPTH = 48
_SECONDS = 70
_PAIR = 2
_REFRESH_SAMPLE = 7
_T = TypeVar("_T")


def failed_pair(states: list[ScrollPosition], height: float) -> tuple[float, float]:
    """Use the final reacquired anchor/trial, not a matcher-selected offset."""
    if len(states) < _PAIR:
        raise CaptureError("No retained anchor/trial pair")
    first, second = states[-2:]
    if first not in states[:-2]:
        raise CaptureError("Final anchor was not independently reacquired in trace")
    if not 0 < first.view_size < END_PERCENT or any(
        state.horizontal != first.horizontal
        or state.view_size != first.view_size
        or not 0 <= state.vertical <= END_PERCENT
        for state in states
    ):
        raise CaptureError("Retained frame extent changed")
    distance = (
        (second.vertical - first.vertical)
        * height
        / first.view_size
        * (1 - first.view_size / 100)
    )
    if distance <= 0 or not math.isclose(distance, round(distance), abs_tol=1e-6):
        raise CaptureError("Final frame pair is not a positive integral movement")
    return first.vertical, second.vertical


def _inside(rect: Rect, area: Rect) -> bool:
    return (
        rect.width > 0
        and rect.height > 0
        and rect.left >= area.left
        and rect.top >= area.top
        and rect.right <= area.right
        and rect.bottom <= area.bottom
    )


def _disjoint(a: Rect, b: Rect) -> bool:
    return (
        a.right <= b.left or b.right <= a.left or a.bottom <= b.top or b.bottom <= a.top
    )


class _Observer:
    """Keep references and guard every native call within one admitted interval."""

    def __init__(
        self, target: UiaScrollTarget, deadline: float, owners: ExitStack
    ) -> None:
        self.target, self.deadline, self.owners = target, deadline, owners
        self.original = target.position()
        self.area = target.area
        self.proven = self.original
        self.uncertain = False
        self.chains: list[tuple[_Interface, list[_Interface]]] = []
        self.native_calls = 0

    def guard(self) -> None:
        """Ownership guards stay tied to the original input/focus/geometry."""
        if time.monotonic() >= self.deadline:
            raise CaptureError("Link probe admission deadline expired")
        self.target.check()
        if self.target.area != self.area or time.monotonic() >= self.deadline:
            raise CaptureError("Link probe geometry/deadline changed")

    def call(self, function: Callable[..., _T], *args: object) -> _T:
        """Guard both sides; never use the result of a failed post-call guard."""
        self.guard()
        self.native_calls += 1
        result = function(*args)
        self.guard()
        return result

    def same(self, first: _Interface, second: _Interface) -> bool:
        """Compare native identities, never runtime IDs or pointer addresses."""
        return self.call(self.target._automation.same_element, first, second)

    def state(self) -> ScrollPosition:
        """Read and enforce invariant native extent and horizontal position."""
        state = self.call(self.target.position)
        if (state.horizontal, state.view_size) != (
            self.original.horizontal,
            self.original.view_size,
        ):
            raise CaptureError("Link probe extent changed")
        return state

    def move(self, percent: float) -> None:
        """One setter, then guarded reads; an uncertain write is never retried."""
        if self.state() != self.proven:
            raise CaptureError("Link probe state changed before write")
        self.uncertain = True
        self.call(self.target.move, percent)
        previous = None
        for sample in range(25):
            current = self.state()
            if (
                current
                == previous
                == ScrollPosition(
                    self.original.horizontal, percent, self.original.view_size
                )
            ):
                self.proven, self.uncertain = current, False
                return
            previous = current
            if sample == _REFRESH_SAMPLE:
                self.call(self.target.refresh_position, self.guard, time.sleep)
            time.sleep(0.08)
        raise CaptureError("Link probe provider write remains unresolved")

    def links(self) -> list[_Interface]:
        """Enumerate all raw descendants or fail explicitly, retaining all links."""
        queue = [(self.target._element, 0)]
        links: list[_Interface] = []
        count = 0
        while queue:
            node, depth = queue.pop(0)
            count += 1
            if count > _NODES or depth > _DEPTH:
                raise CaptureError(
                    "Link probe raw traversal incomplete: node/depth cap"
                )
            if self.call(node.control_type) == _LINK:
                links.append(node)
            child = self.call(self.target._walker.relative, node, self.owners, 4)
            while child is not None:
                if count + len(queue) >= _NODES:
                    raise CaptureError("Link probe raw traversal incomplete: node cap")
                queue.append((child, depth + 1))
                child = self.call(self.target._walker.relative, child, self.owners, 6)
        return links

    def ancestry(self, link: _Interface) -> bool:
        """Require retained Document ancestry and exclude nested scroll providers."""
        chain: list[_Interface] = []
        node = self.call(self.target._walker.parent, link, self.owners)
        for _ in range(_DEPTH):
            if node is None:
                return False
            chain.append(node)
            if self.same(node, self.target._element):
                return self._stable_chain(link, chain)
            if (
                self.call(node.control_type) == _DOCUMENT
                or self.call(node.scroll_pattern, self.owners) is not None
            ):
                return False
            node = self.call(self.target._walker.parent, node, self.owners)
        raise CaptureError("Link probe ancestry incomplete")

    def _stable_chain(self, link: _Interface, chain: list[_Interface]) -> bool:
        for retained, original in self.chains:
            if self.same(retained, link):
                return len(original) == len(chain) and all(
                    self.same(a, b) for a, b in zip(original, chain, strict=True)
                )
        self.chains.append((link, chain))
        return True

    def hit(self, link: _Interface, rect: Rect) -> bool:
        """A native center hit must be the exact link or its descendant."""
        point = w.POINT(round(rect.center.x), round(rect.center.y))
        node = self.call(
            self.target._automation.pointer_result, 7, self.owners, (w.POINT, point)
        )
        for _ in range(_DEPTH):
            if self.same(node, link):
                return True
            if self.same(node, self.target._element):
                return False
            parent = self.call(self.target._walker.parent, node, self.owners)
            if parent is None:
                return False
            node = parent
        raise CaptureError("Link probe hit ancestry incomplete")

    def observe(self, link: _Interface) -> dict[str, object]:
        """Retain explicit reasons for every unqualified or occluded link."""
        rect = self.call(link.bounds)
        record: dict[str, object] = {"bounds": asdict(rect), "eligible": False}
        if self.call(link.control_type) != _LINK:
            record["reason"] = "role_changed"
        elif self.call(link.offscreen) or not _inside(rect, self.area):
            record["reason"] = "offscreen_or_not_fully_contained"
        elif not self.ancestry(link):
            record["reason"] = "wrong_document_or_nested_scroller"
        elif not self.hit(link, rect):
            record["reason"] = "center_hit_not_confirmed"
        elif self.call(link.bounds) != rect:
            record["reason"] = "bounds_changed_during_observation"
        else:
            record["eligible"] = True
        return record

    def frame(self, path: Path) -> QImage:
        """Save raw pixels only at an unchanged acknowledged position."""
        if self.state() != self.proven:
            raise CaptureError("Link probe position changed")
        image = self.call(self.target.frame)
        if self.state() != self.proven or not image.save(str(path)):
            raise CaptureError("Link probe frame state/save failed")
        return image


def motions(
    records: list[tuple[dict[str, object], dict[str, object]]],
) -> dict[str, object]:
    """Evaluate every eligible link, including stationary/conflicting witnesses."""
    shifts: list[float] = []
    rectangles: list[tuple[Rect, Rect]] = []
    for first, second in records:
        if first.get("eligible") is not True or second.get("eligible") is not True:
            continue
        a = Rect(**cast("dict[str, float]", first["bounds"]))
        b = Rect(**cast("dict[str, float]", second["bounds"]))
        shift = a.top - b.top
        second["motion"] = {
            "dx": a.left - b.left,
            "dy": shift,
            "same_size": (a.width, a.height) == (b.width, b.height),
        }
        shifts.append(
            shift
            if a.left == b.left and (a.width, a.height) == (b.width, b.height)
            else math.nan
        )
        rectangles.append((a, b))
    positive = bool(shifts) and all(
        math.isfinite(s) and s > 0 and s == round(s) and s == shifts[0] for s in shifts
    )
    pair = any(
        _disjoint(a[0], b[0]) and _disjoint(a[1], b[1])
        for i, a in enumerate(rectangles)
        for b in rectangles[i + 1 :]
    )
    return {
        "eligible_pair_count": len(shifts),
        "stationary_count": sum(s == 0 for s in shifts),
        "unanimous_positive_integral_motion": positive,
        "distinct_nonoverlapping_pair": pair,
        "motion_witness_observed": positive and pair,
        "conflicting_motion": len(set(shifts)) > 1
        or any(not math.isfinite(s) for s in shifts),
    }


def _paired_observations(
    observer: _Observer,
    links: list[_Interface],
    first: list[dict[str, object]],
    receipt: dict[str, object],
) -> None:
    """Compare retained objects with a complete new enumeration, not IDs alone."""
    current = observer.links()
    receipt["enumeration_b_complete"] = True
    receipt["links_b"] = len(current)
    paired: list[tuple[dict[str, object], dict[str, object]]] = []
    records: list[dict[str, object]] = []
    receipt["links"] = records
    seen: set[int] = set()
    for index, (link, before) in enumerate(zip(links, first, strict=True)):
        matches = [i for i, node in enumerate(current) if observer.same(link, node)]
        record: dict[str, object] = {"retained_index": index, "before": before}
        records.append(record)
        if len(matches) != 1:
            record["status"] = "lost_or_duplicate_identity"
            continue
        seen.add(matches[0])
        after = observer.observe(link)
        record.update(status="retained_same_identity", after=after)
        paired.append((before, after))
    receipt["new_links"] = [
        {"enumeration_index": i, "observation": observer.observe(link)}
        for i, link in enumerate(current)
        if i not in seen
    ]
    raw_motion = motions(paired)
    receipt["raw_paired_motion"] = raw_motion
    incomplete = any(record["status"] != "retained_same_identity" for record in records)
    new_links = receipt["new_links"]
    unknown_eligible = any(
        cast("dict[str, object]", item["observation"]).get("eligible") is True
        for item in new_links
    )
    eligibility_transitions = sum(
        (before.get("eligible") is True) != (after.get("eligible") is True)
        for before, after in paired
    )
    receipt["eligibility_transition_count"] = eligibility_transitions
    receipt["complete_motion_witness_observed"] = (
        raw_motion["motion_witness_observed"] is True
        and not incomplete
        and not unknown_eligible
        and not eligibility_transitions
    )
    receipt["identity_set_complete_for_witness"] = (
        not incomplete and not unknown_eligible and not eligibility_transitions
    )


def _run_pair(
    observer: _Observer,
    output: Path,
    states: list[ScrollPosition],
    original_image: QImage,
    receipt: dict[str, object],
) -> None:
    """Acquire exactly two resolved positions with complete retained observations."""
    target = observer.target
    first, second = failed_pair(states, target.area.height)
    if (
        states[0].view_size != observer.original.view_size
        or states[0].horizontal != observer.original.horizontal
    ):
        raise CaptureError("Current extent differs from retained failure")
    if observer.call(target._element.control_type) != _DOCUMENT:
        raise CaptureError("Link probe requires the retained Document")
    target.refresh_position(observer.guard, time.sleep)
    if observer.frame(output / "original.png") != original_image:
        raise CaptureError("Link probe original pixels were not restored")
    receipt.update(
        pair_eligible=True,
        original=asdict(observer.original),
        area=asdict(target.area),
        selected_pair=[first, second],
        pair_source="last retained anchor/trial frames",
    )
    receipt["unresolved_write"] = True
    _checkpoint(output, receipt, "move_a_pending")
    observer.move(first)
    target.refresh_position(observer.guard, time.sleep)
    receipt["unresolved_write"] = False
    receipt["position_a"] = asdict(observer.proven)
    _checkpoint(output, receipt, "enumerating_a")
    image_a = observer.frame(output / "a.png")
    links = observer.links()
    receipt.update(enumeration_a_complete=True, links_a=len(links))
    observations: list[dict[str, object]] = []
    receipt["links_a_observations"] = observations
    for link in links:
        observations.append(observer.observe(link))
        _checkpoint(output, receipt, "observing_a")
    if observer.frame(output / "a-after-observation.png") != image_a:
        raise CaptureError("Link probe A pixels changed during observation")
    receipt["unresolved_write"] = True
    _checkpoint(output, receipt, "move_b_pending")
    observer.move(second)
    target.refresh_position(observer.guard, time.sleep)
    receipt["unresolved_write"] = False
    receipt["position_b"] = asdict(observer.proven)
    _checkpoint(output, receipt, "enumerating_b")
    image_b = observer.frame(output / "b.png")
    _paired_observations(observer, links, observations, receipt)
    if observer.frame(output / "b-after-observation.png") != image_b:
        raise CaptureError("Link probe B pixels changed during observation")


def _restore(observer: _Observer, output: Path, original_image: QImage) -> None:
    """One restore only from resolved state, preserving the original authority."""
    target = observer.target
    if observer.uncertain or observer.state() != observer.proven:
        raise CaptureError("No resolved eligible state for link restoration")
    target.refresh_position(observer.guard, time.sleep)
    if observer.state() != observer.proven:
        raise CaptureError("Native state changed before link restoration")
    if observer.proven != observer.original:
        observer.move(observer.original.vertical)
    if observer.state() != observer.original:
        raise CaptureError("Link probe original position differs")
    restored = observer.frame(output / "restored.png")
    if restored != original_image:
        raise CaptureError("Link probe original pixels differ")


def _checkpoint(output: Path, receipt: dict[str, object], phase: str) -> None:
    """Leave explicit pending evidence if the outer CI watchdog kills COM."""
    receipt["phase"] = phase
    (output / "result.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def _cleanup_checkpoint(output: Path, receipt: dict[str, object], phase: str) -> None:
    """A persistence failure must never skip an otherwise eligible restoration."""
    try:
        _checkpoint(output, receipt, phase)
    except OSError as error:
        errors = cast("list[str]", receipt.setdefault("persistence_errors", []))
        errors.append(f"{phase}: {type(error).__name__}: {error}")
        receipt.update(success=False, complete_motion_witness_observed=False)


def collect_links(
    target: UiaScrollTarget,
    output: Path,
    states: list[ScrollPosition],
    original_image: QImage,
) -> dict[str, object]:
    """Observe one failed pair after exact restoration, then restore once safely."""
    output.mkdir(exist_ok=True)
    receipt: dict[str, object] = {
        "success": False,
        "pair_eligible": False,
        "product_qualification": False,
        "restoration": "not_needed",
        "enumeration_a_complete": False,
        "enumeration_b_complete": False,
        "limits": {
            "nodes_per_tree": _NODES,
            "depth": _DEPTH,
            "seconds": _SECONDS,
            "restore_seconds": 10,
        },
    }
    started = time.monotonic()
    receipt["restoration"] = "unknown_pending_probe"
    _checkpoint(output, receipt, "started")
    with ExitStack() as owners:
        observer = _Observer(target, started + _SECONDS, owners)
        try:
            _run_pair(observer, output, states, original_image, receipt)
            receipt["success"] = True
        except (CaptureError, OSError, RuntimeError, ValueError) as error:
            receipt["error"] = f"{type(error).__name__}: {error}"
            receipt["complete_motion_witness_observed"] = False
        finally:
            # Admission expiry does not permit abandoning a resolved state. A
            # separate bounded restoration interval admits at most one setter.
            observer.deadline = time.monotonic() + 10
            try:
                receipt["restoration"] = "pending"
                _cleanup_checkpoint(output, receipt, "restoration_pending")
                _restore(observer, output, original_image)
                receipt["restoration"] = "restored"
            except (CaptureError, OSError, RuntimeError) as error:
                receipt.update(
                    success=False,
                    complete_motion_witness_observed=False,
                    restoration="unproven",
                    restoration_error=f"{type(error).__name__}: {error}",
                )
            receipt["elapsed_seconds"] = time.monotonic() - started
            receipt["guarded_native_calls"] = observer.native_calls
            receipt["unresolved_write"] = observer.uncertain
            _cleanup_checkpoint(output, receipt, "complete")
    return receipt
