"""Hosted-only, read-only ListItem geometry evidence, independent of pixels.

Public identity and hit-test contracts:
https://learn.microsoft.com/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-elementfrompoint
https://learn.microsoft.com/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-compareelements
IUIAutomationElement's IID and IUnknown QueryInterface slot come from the SDK
UIAutomationClient.h; QueryInterface gives each retained item its own reference.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from typing import cast

from verdiclip.capture.scrolling import END_PERCENT, ScrollPosition
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect
from verdiclip.platform.scrolling import UiaScrollTarget, _Guid, _Interface

_LIST = 50008
_ITEM = 50007
_HEADER = 50034
_SCROLLBAR = 50014
_MAX_CHILDREN = 64
_MAX_ANCESTORS = 8
_PAIR_SIZE = 2
_SECONDS = 1.0
_ELEMENT_IID = "d22108aa-8ac5-49a5-837b-37bbb3d7591e"


def raw_bounds(value: object) -> Rect:
    """Decode numeric diagnostic rectangles without production layout helpers."""
    return Rect(**cast("dict[str, float]", value))


def header_layout(geometry: dict[str, object]) -> tuple[Rect, Rect]:
    """Derive the fixed band only from the raw standard control-role evidence."""
    area = raw_bounds(geometry["captured_bounds"])
    tree = cast("dict[str, object]", geometry["uia"])
    nodes = cast("list[dict[str, object]]", tree["nodes"])
    direct = [n for n in nodes if n["parent"] == 0 and not n["offscreen"]]
    headers = [raw_bounds(n["bounds"]) for n in direct if n["control_type"] == _HEADER]
    if tree["truncated"] or nodes[0]["control_type"] != _LIST or len(headers) != 1:
        raise CaptureError(
            "Native item witness requires complete standard Header evidence"
        )
    header = headers[0]
    if (
        header.left != area.left
        or header.top != area.top
        or not 0 < header.width <= area.width
        or not 0 < header.height < area.height
        or header.width != round(header.width)
        or header.height != round(header.height)
    ):
        raise CaptureError("Native item witness Header geometry is inconsistent")
    remainder = Rect(header.right, area.top, area.right - header.right, area.height)
    bars = [raw_bounds(n["bounds"]) for n in direct if n["control_type"] == _SCROLLBAR]
    if remainder.width and bars.count(remainder) != 1:
        raise CaptureError("Native item witness lacks the adjacent ScrollBar evidence")
    return area, header


@dataclass(frozen=True, slots=True)
class _Anchor:
    element: _Interface
    bounds: Rect


class ListItemWitness:
    """Retain two actual visible items, checking fresh hit-test identity per step.

    SDK IUIAutomationElement::QueryInterface retains only the selected pair;
    ElementFromPoint and CompareElements confirm current identities. References
    are never realized or scrolled. The 1-second budget is soft between COM calls;
    the ordinary helper's unchanged process deadline remains the hard bound.
    """

    def __init__(self, target: UiaScrollTarget, geometry: dict[str, object]) -> None:
        self.target, self.geometry = target, geometry
        area, header = header_layout(geometry)
        self._body = Rect(
            area.left, header.bottom, header.width, area.bottom - header.bottom
        )
        self._owners, self._retained = ExitStack(), ExitStack()
        self._anchors: list[_Anchor] = []
        self._index = 0
        self._deadline = 0.0
        target._owners.callback(self.close)
        pointer = c.c_void_p()
        target._ole.CoCreateInstance(
            c.byref(_Guid.parse("ff48dba4-60ef-4201-aa87-54103eef594e")),
            None,
            1,
            c.byref(_Guid.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")),
            c.byref(pointer),
        )
        self._automation = _Interface(pointer, self._owners)
        self._walker = self._automation.pointer_result(16, self._owners)

    def close(self) -> None:
        """Release evidence references before the target's COM apartment closes."""
        self._retained.close()
        self._owners.close()

    def _budget(self) -> None:
        if time.monotonic() >= self._deadline:
            raise CaptureError("Native item witness exceeded its read budget")

    def _visible(self, element: _Interface) -> Rect:
        self._budget()
        bounds = element.bounds()
        if (
            element.control_type() != _ITEM
            or element.offscreen()
            or bounds.is_empty
            or bounds.intersected(self._body) != bounds
        ):
            raise CaptureError("Native item witness is no longer fully visible")
        with ExitStack() as owners:
            parent = self._walker.parent(element, owners)
            if parent is None or not self._automation.same_element(
                parent, self.target._element
            ):
                raise CaptureError("Native item witness changed its direct parent")
        return bounds

    def _confirm(self, element: _Interface, bounds: Rect) -> None:
        self._budget()
        with ExitStack() as owners:
            current = self._automation.pointer_result(
                7,
                owners,
                (w.POINT, w.POINT(round(bounds.center.x), round(bounds.center.y))),
            )
            for _ in range(_MAX_ANCESTORS):
                self._budget()
                if current.control_type() == _ITEM:
                    visible = self._visible(current)
                    same = self._automation.same_element(element, current)
                    if visible != bounds or not same:
                        raise CaptureError("Native item witness identity changed")
                    return
                parent = self._walker.parent(current, owners)
                if parent is None:
                    break
                current = parent
        raise CaptureError("Native item witness has no current hit-tested ListItem")

    def _seed(self) -> None:
        with ExitStack() as temporary:
            candidates: list[_Anchor] = []
            child = self._walker.relative(self.target._element, temporary, 4)
            for _ in range(_MAX_CHILDREN):
                self._budget()
                if child is None:
                    break
                if child.control_type() == _ITEM and not child.offscreen():
                    bounds = child.bounds()
                    if not bounds.is_empty and bounds.intersected(self._body) == bounds:
                        candidates.append(_Anchor(child, bounds))
                child = self._walker.relative(child, temporary, 6)
            if child is not None or len(candidates) < _PAIR_SIZE:
                raise CaptureError(
                    "Native item witness cannot establish a bounded pair"
                )
            chosen = sorted(candidates, key=lambda item: item.bounds.top)[-_PAIR_SIZE:]
            first, last = chosen
            if first.bounds.bottom > last.bounds.top or self._automation.same_element(
                first.element, last.element
            ):
                raise CaptureError("Native item witness pair is ambiguous")
            with ExitStack() as retained:
                anchors = []
                for anchor in chosen:
                    self._visible(anchor.element)
                    self._confirm(anchor.element, anchor.bounds)
                    element = anchor.element.pointer_result(
                        0,
                        retained,
                        (c.POINTER(_Guid), c.byref(_Guid.parse(_ELEMENT_IID))),
                    )
                    anchors.append(_Anchor(element, anchor.bounds))
                self._retained, self._anchors = retained.pop_all(), anchors

    def observe(self, position: ScrollPosition) -> dict[str, object]:
        """Verify prior anchors, then retain the next pair unless at the final frame."""
        self._deadline = time.monotonic() + _SECONDS
        self.target.check()
        if self.target.position() != position:
            raise CaptureError("The scroll panel moved before native item evidence")
        items = []
        deltas = []
        for anchor in self._anchors:
            after = self._visible(anchor.element)
            self._confirm(anchor.element, after)
            if (anchor.bounds.x, anchor.bounds.width, anchor.bounds.height) != (
                after.x,
                after.width,
                after.height,
            ):
                raise CaptureError(
                    "Native item witness changed horizontal or size geometry"
                )
            deltas.append(anchor.bounds.y - after.y)
            items.append(
                {
                    "before": asdict(anchor.bounds),
                    "after": asdict(after),
                    "same_identity": True,
                    "same_parent": True,
                }
            )
        if self._index and (
            len(deltas) != _PAIR_SIZE
            or deltas[0] != deltas[1]
            or not 0 < deltas[0] < self._body.height
        ):
            raise CaptureError("Native item witnesses disagree about displacement")
        result = {
            "from_frame": self._index - 1 if self._index else None,
            "items": items,
        }
        self._retained.close()
        self._anchors = []
        if position.vertical < END_PERCENT:
            self._seed()
        self.target.check()
        if self.target.position() != position:
            raise CaptureError("The scroll panel moved during native item evidence")
        self._budget()
        self._index += 1
        return result
