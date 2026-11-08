"""Read bounded numeric UIA geometry in the hosted diagnostic helper only.

Call ``probe_target(target)`` after the real UiaScrollTarget has opened. The
returned JSON-compatible dict contains no element names, values, or text.
Nothing here selects a crop, scrolls, injects input, or changes production code.

WinSDK UIAutomationClient.h: IUIAutomation::get_RawViewWalker slot 16;
IUIAutomationTreeWalker::{GetFirstChildElement, GetNextSiblingElement} slots 4, 6.
Microsoft API contracts:
https://learn.microsoft.com/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtreewalker
https://learn.microsoft.com/windows/win32/winauto/uiauto-supportheadercontroltype
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getclientrect
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-clienttoscreen
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getdpiforwindow
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import time
from collections import deque
from contextlib import ExitStack
from dataclasses import asdict

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget, _Guid, _Interface

_MAX_NODES = 256
_MAX_DEPTH = 2
_MAX_SECONDS = 2.0
_HEADER = 50034
_HEADER_ITEM = 50035


def window_dpi(target: UiaScrollTarget) -> int:
    """Read the selected window's DPI without walking UIA descendants.

    This is GetDpiForWindow's value, not an independent monitor-scaling
    measurement: unaware windows report 96, system-aware windows report system
    DPI, and per-monitor-aware windows report their monitor's DPI.
    """
    target.check()
    getter = target._user.GetDpiForWindow
    getter.argtypes, getter.restype = (w.HWND,), w.UINT
    value = int(getter(target._window))
    if value == 0:
        raise CaptureError("Header probe could not read the selected window DPI")
    target.check()
    return value


def _relative(
    walker: _Interface, element: _Interface, owners: ExitStack, slot: int
) -> _Interface | None:
    """Read a nullable first-child/next-sibling result with local ownership."""
    pointer = c.c_void_p()
    walker.call(
        slot,
        (c.c_void_p, c.POINTER(c.c_void_p)),
        element._pointer,
        c.byref(pointer),
    )
    return None if pointer.value is None else _Interface(pointer, owners)


def _node(element: _Interface, parent: int | None, depth: int) -> dict[str, object]:
    """Read only public numeric metadata, including standard Header identifiers."""
    kind = element.control_type()
    offscreen = w.BOOL()
    element.call(38, (c.POINTER(w.BOOL),), c.byref(offscreen))
    return {
        "parent": parent,
        "depth": depth,
        "control_type": kind,
        "header": kind == _HEADER,
        "header_item": kind == _HEADER_ITEM,
        "bounds": asdict(element.bounds()),
        "native_handle": element.native_handle(),
        "offscreen": bool(offscreen.value),
    }


def _tree(root: _Interface, walker: _Interface, owners: ExitStack) -> dict[str, object]:
    """Visit shallow siblings first so a long list cannot bury its header."""
    deadline = time.monotonic() + _MAX_SECONDS
    nodes = [_node(root, None, 0)]
    pending = deque([(root, 0, 0)])
    while pending:
        parent, parent_index, depth = pending.popleft()
        if depth >= _MAX_DEPTH:
            continue
        if time.monotonic() >= deadline or len(nodes) >= _MAX_NODES:
            return {"nodes": nodes, "truncated": True}
        child = _relative(walker, parent, owners, 4)
        while child is not None:
            if time.monotonic() >= deadline or len(nodes) >= _MAX_NODES:
                return {"nodes": nodes, "truncated": True}
            index = len(nodes)
            nodes.append(_node(child, parent_index, depth + 1))
            pending.append((child, index, depth + 1))
            child = _relative(walker, child, owners, 6)
    return {"nodes": nodes, "truncated": False}


def _native_client(target: UiaScrollTarget) -> dict[str, object] | None:
    """Independently report the selected element's native client rectangle."""
    handle = target._native_handle
    if handle is None:
        return None
    bounds = w.RECT()
    if not target._user.GetClientRect(handle, c.byref(bounds)):
        raise CaptureError("Header probe could not read the native client rectangle")
    first, last = w.POINT(bounds.left, bounds.top), w.POINT(bounds.right, bounds.bottom)
    if not target._user.ClientToScreen(
        handle, c.byref(first)
    ) or not target._user.ClientToScreen(handle, c.byref(last)):
        raise CaptureError("Header probe could not map the native client rectangle")
    return {
        "native_handle": handle,
        "bounds": asdict(
            Rect.from_points(Point(first.x, first.y), Point(last.x, last.y))
        ),
    }


def probe_target(target: UiaScrollTarget) -> dict[str, object]:
    """Observe an already-open target on its existing helper COM thread.

    The root is ``uia.nodes[0]``; each other node's ``parent`` indexes that list.
    Coordinates are the public API's screen rectangles, with x/y/width/height.
    Scan limits are explicit; a truncated report is not proof of no header.
    The time budget is checked between synchronous calls, not a COM timeout.
    Provider/guard failures propagate to the diagnostic caller. All temporary
    COM references are released before return; the target remains owned by its
    existing context. This function writes no files and never changes geometry.
    """
    target.check()
    with ExitStack() as owners:
        pointer = c.c_void_p()
        target._ole.CoCreateInstance(
            c.byref(_Guid.parse("ff48dba4-60ef-4201-aa87-54103eef594e")),
            None,
            1,
            c.byref(_Guid.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")),
            c.byref(pointer),
        )
        automation = _Interface(pointer, owners)
        walker = automation.pointer_result(16, owners)
        result = {
            "limits": {
                "nodes": _MAX_NODES,
                "depth": _MAX_DEPTH,
                "seconds": _MAX_SECONDS,
            },
            "provider_bounds": asdict(target._provider_bounds),
            "captured_bounds": asdict(target.area),
            "position": asdict(target.position()),
            "window_dpi": window_dpi(target),
            "native_client": _native_client(target),
            "uia": _tree(target._element, walker, owners),
        }
    target.check()
    return result
