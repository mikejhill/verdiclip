"""One pre-scope MOVE after arming an owned normal-cursor-handler observation."""

from __future__ import annotations

import ctypes.wintypes as w
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import uuid4

from tests.capture.native_pointer_driver import Driver
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.geometry import Point


def unique(fields: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate protocol fields."""
    value: dict[str, object] = {}
    for key, item in fields:
        if key in value:
            raise ValueError("pointer-setup-duplicate")
        value[key] = item
    return value


def read(path: Path) -> dict[str, object] | None:
    """Read at most one small complete owned receipt, without unbounded read_text."""
    if not path.exists():
        return None
    if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & 0x400:
        raise RuntimeError("pointer-setup-linked-file")
    with path.open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise RuntimeError("pointer-setup-oversized-file")
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise TypeError("pointer-setup-invalid-object")
    return cast("dict[str, object]", value)


def wait_receipt(
    path: Path,
    expected: dict[str, object],
    deadline: float,
    pause: Callable[[int], None],
) -> None:
    """Wait only for exact event receipt arrival, never for cursor shape recovery."""
    while True:
        if time.monotonic() >= deadline:
            raise RuntimeError("pointer-setup-deadline")
        value = read(path)
        if value is not None:
            if time.monotonic() >= deadline:
                raise RuntimeError("pointer-setup-deadline")
            if value != expected or any(
                type(value[key]) is not type(item) for key, item in expected.items()
            ):
                raise RuntimeError("pointer-setup-receipt-invalid")
            return
        pause(10)


def prepare(driver: Driver, peer: ScrollPeer, pause: Callable[[int], None]) -> Point:
    """One MOVE and handler readiness; the caller owns the sole baseline copy."""
    if not peer.native_pointer_cursor or peer.identity is None or peer.process is None:
        raise RuntimeError("pointer-setup-owner-unavailable")
    if (
        peer.process.poll() is not None
        or peer.identity[0] != driver.fixture_pid
        or peer.identity[2] != driver.fixture
    ):
        raise RuntimeError("pointer-setup-owner-changed")
    original = driver.read().point
    center = driver.client().center
    target = Point(round(center.x), round(center.y))
    if target == original:
        target = target + Point(1, 0)
    driver.guard(target)
    target_handle = int(
        driver.api.WindowFromPoint(w.POINT(int(target.x), int(target.y))) or 0
    )
    if not target_handle:
        raise RuntimeError("pointer-setup-hit-unavailable")
    request_id = uuid4().hex
    paths = [
        peer.directory / name
        for name in (
            "pointer-setup-request.partial",
            "pointer-setup-request.json",
            "pointer-setup-armed.json",
            "pointer-setup-ready.json",
        )
    ]
    if any(path.exists() or path.is_symlink() for path in paths):
        raise RuntimeError("pointer-setup-already-used")
    request = {
        "nonce": peer.nonce,
        "operation": "cursor-setup",
        "request_id": request_id,
        "x": int(target.x),
        "y": int(target.y),
        "target_hwnd": target_handle,
    }
    with paths[0].open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(request, separators=(",", ":")))
    paths[0].rename(paths[1])
    expected = {
        "nonce": peer.nonce,
        "request_id": request_id,
        "pid": peer.process.pid,
        "hwnd": driver.fixture,
    }
    deadline = time.monotonic() + 2
    wait_receipt(paths[2], {**expected, "armed": True}, deadline, pause)
    if peer.process.poll() is not None:
        raise RuntimeError("pointer-setup-owner-exited")
    driver.move(target)
    wait_receipt(paths[3], {**expected, "handled": True, "count": 1}, deadline, pause)
    if peer.process.poll() is not None:
        raise RuntimeError("pointer-setup-owner-exited")
    return target
