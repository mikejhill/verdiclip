"""Exercise the actual native-mode worker through a replay OS boundary."""

from typing import cast

import pytest
from tests.capture.input_fixture import ReplayInputPlatform
from tests.capture.test_input_worker import ReplayRun

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.input_raw import RawPointer
from verdiclip.capture.input_routing import MoveMode
from verdiclip.capture.native_pointer import NativePointerRoute
from verdiclip.capture.native_pointer_win32 import NativePointerSession
from verdiclip.geometry import Point


class Pointer:
    """No native calls; preserve the real route's cancellation latch."""

    def __init__(self) -> None:
        self.route = NativePointerRoute()
        self.route.bind(frozenset({7}))
        self.route.admit(7, lambda: True)
        self.permitted = True
        self.nudges: list[Point] = []

    def safe(self) -> bool:
        """Inject an ownership transition."""
        return self.permitted

    def move(self, point: Point) -> bool:
        """Publish absolute coordinates through the production route policy."""
        return self.route.move(point, self.safe)

    def nudge(self, point: Point) -> bool:
        """Record one physical-nudge request without changing a real pointer."""
        self.nudges.append(point)
        return self.move(point)

    def close(self) -> None:
        """Revoke before worker release."""
        self.route.close()


@pytest.mark.parametrize("raw_first", [False, True])
def test_raw_and_hook_order_cannot_duplicate_buttons(*, raw_first: bool) -> None:
    """Retain deterministic ownership and routing without native execution."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    pointer = Pointer()
    worker.native_pointer = cast(NativePointerSession, pointer)
    at = Point(300, 200)
    raw = RawPointer(
        Point(900, 900), MoveMode.RELATIVE_DELTA, ((0x201, 0), (0x202, 0)), wheel=False
    )
    if raw_first:
        platform.raw(raw)
    platform.mouse(0x200, at, injected=False)
    platform.mouse(0x201, at, injected=False)
    platform.mouse(0x202, at, injected=False)
    if not raw_first:
        platform.raw(raw)
    platform.actions.put(worker.release)
    ReplayRun.complete(worker)
    events = worker.drain()
    assert [event.kind for event in events if event.kind is not InputKind.MOVE] == [
        InputKind.PRESS,
        InputKind.RELEASE,
    ]
    assert all(event.point == at for event in events)
    assert platform.forwarded == [0x200]
    assert worker._pending_raw is None
    assert worker.cleaned


def test_arrow_and_following_hook_move_are_absolute_not_double_delta() -> None:
    """Retain deterministic ownership and routing without native execution."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    pointer = Pointer()
    worker.native_pointer = cast(NativePointerSession, pointer)
    platform.key(0x100, 0x27)
    platform.mouse(0x200, Point(263, 168), injected=False)
    platform.key(0x101, 0x27)
    platform.mouse(0x201, Point(263, 168), injected=False)
    platform.mouse(0x202, Point(263, 168), injected=False)
    platform.actions.put(worker.release)
    ReplayRun.complete(worker)
    assert pointer.nudges == [Point(263, 168)]
    assert all(event.point == Point(263, 168) for event in worker.drain())
    assert worker._pending_raw is None
    assert not worker.failure


def test_new_capture_revokes_and_consumes_owned_button_release() -> None:
    """Retain deterministic ownership and routing without native execution."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    pointer = Pointer()
    worker.native_pointer = cast(NativePointerSession, pointer)
    platform.mouse(0x201, platform.anchor, injected=False)
    platform.actions.put(lambda: setattr(pointer, "permitted", False))
    platform.mouse(0x202, platform.anchor, injected=False)
    ReplayRun.complete(worker)
    assert not platform.forwarded
    assert InputKind.CANCEL in [event.kind for event in worker.drain()]
    assert worker.cleaned


@pytest.mark.parametrize("cancel", ["wheel", "escape"])
def test_semantic_cancel_blocks_move_before_gui_drains(cancel: str) -> None:
    """Closing semantic selection closes native forwarding synchronously."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    pointer = Pointer()
    worker.native_pointer = cast(NativePointerSession, pointer)
    worker._api = platform
    worker.foreground = platform.owner
    platform.install(worker._mouse_callback, worker._key_callback, worker._raw_pointer)
    if cancel == "wheel":
        platform.mouse(0x20A, platform.anchor)
    else:
        platform.key(0x100, 0x1B)
        platform.key(0x101, 0x1B)
    platform.mouse(0x200, Point(300, 200))
    while not platform.actions.empty():
        platform.actions.get_nowait()()
    assert not platform.forwarded
    assert InputKind.CANCEL in [event.kind for event in worker.drain()]


def test_queue_overflow_revokes_before_forwarding_current_move() -> None:
    """Publication failure cannot forward using a pre-publication admission."""
    platform = ReplayInputPlatform()
    worker = ReplayRun.make(platform)
    pointer = Pointer()
    worker.native_pointer = cast(NativePointerSession, pointer)
    worker._api = platform
    worker.foreground = platform.owner
    platform.install(worker._mouse_callback, worker._key_callback, worker._raw_pointer)
    platform.mouse(0x200, platform.anchor)
    for _ in range(worker.QUEUE_LIMIT):
        worker._events.put_nowait(SelectionInput(InputKind.MOVE, platform.anchor))
    platform.actions.get_nowait()()
    assert not platform.forwarded
    assert "event budget" in worker.failure
