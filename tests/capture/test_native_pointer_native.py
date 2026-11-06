"""Hosted opt-in ordinary pointer route; no rendered/physical acceptance claim."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Sequence
from functools import partial
from pathlib import Path

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession
from tests.capture.hosted_fixture_input import require_hosted
from tests.capture.menu_peer import MenuPeer
from tests.capture.native_pointer_crash import CrashChild
from tests.capture.native_pointer_cursor import (
    Appearance,
    NativeReader,
    copy_appearance,
)
from tests.capture.native_pointer_driver import Driver, State
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.input_broker import NativeInputBroker
from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.input_routing import MoveMode
from verdiclip.capture.input_worker import CaptureInputWorker, InputWorkerOptions
from verdiclip.capture.models import Capture
from verdiclip.capture.native_pointer import PointerPhase
from verdiclip.capture.overlay import WindowTarget
from verdiclip.capture.service import CaptureService
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowInfo


class OwnedWindows:
    """Only the deliberately constructed fixture is offered as a capture target."""

    def __init__(self, driver: Driver) -> None:
        self.driver = driver

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[WindowTarget]:
        """No all-process enumeration or titles are required."""
        if self.driver.fixture in exclude:
            return ()
        return (
            WindowInfo(
                self.driver.fixture, "Owned pointer fixture", self.driver.client()
            ),
        )

    def foreground(self) -> WindowTarget:
        """Validate the existing foreground; never activate it."""
        self.driver.read()
        return WindowInfo(
            self.driver.fixture, "Owned pointer fixture", self.driver.client()
        )


def expected_cursor() -> Appearance:
    """Independent exact AND/XOR bytes from the owned CreateCursor specification."""
    mask = bytes(
        value for row in range(16) for value in (255 ^ (128 >> min(row, 7)), 255)
    )
    return Appearance(16, 16, (0, 0), mask + bytes(32), None)


@pytest.fixture
def owned_source(qtbot: QtBot, tmp_path: Path) -> Iterator[ScrollPeer]:
    """Retire the exact external fixture even when setup/first admission fails."""
    if os.environ.get("VERDICLIP_HOSTED_NATIVE_POINTER") != "1":
        pytest.skip("Dedicated disposable hosted native-pointer job only")
    require_hosted(os.environ)
    peer = ScrollPeer(
        tmp_path / "ordinary", provider="winforms", native_pointer_cursor=True
    )
    try:
        peer.start()
        qtbot.waitUntil(peer.poll, timeout=20000)
        yield peer
    finally:
        peer.close()


def terminal(  # noqa: PLR0913, PLR0917 - explicit independent terminal boundary
    service: CaptureService,
    driver: Driver,
    before: State,
    expected: Point,
    qtbot: QtBot,
    owned_handles: tuple[int, ...],
) -> None:
    """First snapshot after actual retirement cannot be replaced by later recovery."""
    qtbot.waitUntil(
        lambda: (
            not service.is_selecting
            and not any(driver.api.IsWindow(h) for h in owned_handles)
        ),
        timeout=5000,
    )
    after = driver.read()
    checks = {
        "visible": after.flags == 1,
        "position_preserved": after.point == expected,
        "foreign_gui_preserved": (after.foreground, after.gui)
        == (before.foreground, before.gui),
        "appearance_preserved": copy_appearance(after.cursor, NativeReader())
        == expected_cursor(),
        "physical_input_verified": False,
        "rendered_pointer_verified": False,
    }
    print("NATIVE_POINTER_TERMINAL=" + json.dumps(checks, sort_keys=True))  # noqa: T201 - fixed private comparison only
    assert all(
        value
        for key, value in checks.items()
        if key not in {"physical_input_verified", "rendered_pointer_verified"}
    )


@pytest.mark.native_desktop
def test_owned_native_pointer_stationary_retirement_and_input(  # noqa: PLR0915 - finite explicit native sequence
    qtbot: QtBot, owned_source: ScrollPeer
) -> None:
    """Actual OS moves, drag, keyboard and repeated cleanup stay in an owned client."""
    if os.environ.get("VERDICLIP_HOSTED_NATIVE_POINTER") != "1":
        pytest.skip("Dedicated disposable hosted native-pointer job only")
    require_hosted(os.environ)
    desktop = DesktopSession.current()
    assert desktop is not None
    assert desktop.state == 0
    assert desktop.flags == 1
    assert owned_source.process is not None
    driver = Driver(owned_source.handle, owned_source.process.pid)
    center = driver.client().center
    initial = Point(round(center.x), round(center.y))
    driver.move(initial)
    qtbot.wait(20)  # One finite pre-scope delivery interval, never restoration polling.
    before = driver.read()
    assert before.flags == 1
    baseline_position_matches = before.point == initial
    assert baseline_position_matches, "Initial pointer position differs"
    # Fail closed unless the deliberately owned monochrome cursor is actually active.
    matching = copy_appearance(before.cursor, NativeReader()) == expected_cursor()
    assert matching, "Owned static cursor baseline not established"
    owner = QObject()
    workers: list[CaptureInputWorker] = []

    def worker(point: Point, bounds: Rect) -> CaptureInputWorker:
        result = CaptureInputWorker(
            point,
            bounds,
            options=InputWorkerOptions(
                max_seconds=10, injected_move_mode=MoveMode.ABSOLUTE
            ),
        )
        workers.append(result)
        return result

    broker = NativeInputBroker(owner, factory=worker)
    source = MssScreenSource()
    service = CaptureService(
        source, OwnedWindows(driver), show_magnifier=False, input_broker=broker
    )
    service.enable_native_pointer()
    events: list[SelectionInput] = []
    captures: list[Capture] = []
    broker.input_event.connect(events.append)
    service.captured.connect(captures.append)
    try:
        for index, mode in enumerate(("cancel", "keyboard", "drag", "cancel")):
            before = driver.read()
            assert before.flags == 1
            service.capture_region()
            qtbot.waitUntil(lambda: bool(service.overlays), timeout=5000)
            pointer = service._native_pointer
            assert pointer is not None
            qtbot.waitUntil(
                lambda pointer=pointer: pointer.route.phase is not PointerPhase.WAITING,
                timeout=2000,
            )
            assert pointer.route.phase is PointerPhase.OWNED
            owned_handles = tuple(int(item.winId()) for item in service.overlays)
            driver.allowed = {
                driver.fixture: driver.fixture_pid,
                **{int(item.winId()): os.getpid() for item in service.overlays},
            }
            point = initial + Point(8 * (index + 1), 8)
            start = len(events)
            driver.move(point)
            qtbot.waitUntil(lambda start=start: len(events) > start, timeout=2000)
            event = events[start]
            actual = driver.read()
            shared = service._shared
            assert shared is not None
            checks = {
                "move_event": event.kind is InputKind.MOVE,
                "event_matches_os": event.point == actual.point == point,
                "selection_matches_os": shared.pointer == actual.point,
                "guide_maps_to_os": any(
                    (local := item._local_cursor()) is not None
                    and item._geometry.to_physical(local) == actual.point
                    for item in service.overlays
                ),
                "route_matches_os": pointer.route.point == actual.point,
                "cursor_visible": actual.flags == 1,
                "qt_cross_resource_matches": actual.cursor == driver.cross_cursor(),
                "foreground_unchanged": actual.foreground == before.foreground,
            }
            print("NATIVE_POINTER_MOVE=" + json.dumps(checks, sort_keys=True))  # noqa: T201 - fixed comparisons
            assert all(checks.values()), checks
            if mode == "keyboard":
                start = len(events)
                driver.key(0x27, down=True)
                driver.key(0x27, down=False)
                qtbot.waitUntil(lambda start=start: len(events) > start, timeout=2000)
                point = point + Point(1, 0)
                actual = driver.read()
                matched = actual.point == shared.pointer == pointer.route.point == point
                assert matched, "Keyboard pointer/model mismatch"
            if mode == "drag":
                driver.button(down=True)
                try:
                    qtbot.waitUntil(lambda shared=shared: shared.pressed, timeout=2000)
                    end = point + Point(24, 16)
                    start = len(events)
                    driver.move(end)
                    qtbot.waitUntil(
                        lambda start=start: len(events) > start, timeout=2000
                    )
                    matched = (
                        events[start].point
                        == driver.read().point
                        == shared.pointer
                        == end
                    )
                    assert matched, "Drag pointer/model mismatch"
                finally:
                    driver.button(down=False)
                point = end
            else:
                driver.key(0x1B, down=True)
                driver.key(0x1B, down=False)
            terminal(service, driver, before, point, qtbot, owned_handles)
            driver.allowed = {driver.fixture: driver.fixture_pid}
            assert all(item.stopped.is_set() and item.cleaned for item in workers)
        assert len(captures) == 1
        correct_area = captures[0].area == Rect(initial.x + 24, initial.y + 8, 24, 16)
        assert correct_area, "Selection rectangle differs from actual drag"
    finally:
        service.cancel()
        try:
            qtbot.waitUntil(lambda: not broker.busy, timeout=5000)
        finally:
            try:
                source.close()
            finally:
                service.deleteLater()
                owner.deleteLater()


def open_menu(peer: MenuPeer, qtbot: QtBot) -> None:
    """One outstanding correlated observation at a time within a fixed setup bound."""
    peer.start(handoff=True)
    deadline = time.monotonic() + 5
    while True:
        sequence = peer.poll()
        assert sequence is not None, "Menu observation unavailable"
        remaining = int((deadline - time.monotonic()) * 1000)
        assert remaining > 0, "Menu setup deadline"
        qtbot.waitUntil(partial(peer.acknowledged, sequence), timeout=remaining)
        assert time.monotonic() <= deadline, "Menu setup deadline"
        if not peer.preservation_errors():
            return
        assert not peer.error, "Menu setup failed"
        qtbot.wait(10)


class FixedWindows:
    """Do not enumerate foreign windows during the protected-menu negative case."""

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[WindowTarget]:
        """No candidate is returned regardless of exclusions."""
        del exclude
        return ()

    def foreground(self) -> WindowTarget | None:
        """Region selection needs no discovered target."""
        return None


@pytest.mark.native_desktop
@pytest.mark.parametrize("preexisting", [True, False])
def test_native_pointer_preserves_protected_menu(
    qtbot: QtBot, owned_source: ScrollPeer, *, preexisting: bool
) -> None:
    """Protected menu falls back before admission, or revokes an owned session."""
    assert owned_source.process is not None
    driver = Driver(owned_source.handle, owned_source.process.pid)
    center = driver.client().center
    driver.move(Point(round(center.x), round(center.y)))
    qtbot.wait(20)
    source = MssScreenSource()
    service = CaptureService(source, FixedWindows(), show_magnifier=False)
    service.enable_native_pointer()
    peer = MenuPeer()
    try:
        if preexisting:
            open_menu(peer, qtbot)
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays), timeout=5000)
        handles = tuple(int(item.winId()) for item in service.overlays)
        if preexisting:
            assert service._native_pointer is None, "Protected menu was admitted"
            service.cancel()
        else:
            pointer = service._native_pointer
            assert pointer is not None, "Ordinary route unavailable"
            qtbot.waitUntil(
                lambda: pointer.route.phase is not PointerPhase.WAITING, timeout=2000
            )
            assert pointer.route.phase is PointerPhase.OWNED
            open_menu(peer, qtbot)
        qtbot.waitUntil(
            lambda: (
                not service.is_selecting
                and not any(driver.api.IsWindow(h) for h in handles)
            ),
            timeout=5000,
        )
        sequence = peer.poll()
        assert sequence is not None, "Final menu observation unavailable"
        qtbot.waitUntil(partial(peer.acknowledged, sequence), timeout=2000)
        preserved = not peer.preservation_errors()
        print(  # noqa: T201 - fixed comparison
            "NATIVE_POINTER_MENU="
            + json.dumps({"preexisting": preexisting, "preserved": preserved})
        )
        assert preserved, "Protected menu was changed"
    finally:
        service.cancel()
        try:
            qtbot.waitUntil(lambda: not service.is_selecting, timeout=5000)
        finally:
            try:
                peer.close()
            finally:
                try:
                    source.close()
                finally:
                    service.deleteLater()


@pytest.mark.native_desktop
def test_native_pointer_abrupt_owned_process_exit(
    qtbot: QtBot, owned_source: ScrollPeer
) -> None:
    """Exact child exit, not normal cleanup, precedes the first restoration sample."""
    assert owned_source.process is not None
    driver = Driver(owned_source.handle, owned_source.process.pid)
    center = driver.client().center
    driver.move(Point(round(center.x), round(center.y)))
    qtbot.wait(20)
    before = driver.read()
    baseline = (
        before.flags == 1
        and copy_appearance(before.cursor, NativeReader()) == expected_cursor()
    )
    assert baseline, "Crash baseline unavailable"
    child = CrashChild()
    try:
        child.start()
        child.crash()
        retired = not any(driver.api.IsWindow(handle) for handle in child.handles)
        assert retired, "Crash-owned HWND retirement unavailable"
        after = driver.read()
        checks = {
            "visible": after.flags == 1,
            "position_preserved": after.point == before.point,
            "foreign_gui_preserved": (after.foreground, after.gui)
            == (before.foreground, before.gui),
            "appearance_preserved": copy_appearance(after.cursor, NativeReader())
            == expected_cursor(),
            "causal_exit_confirmed": True,
        }
        print("NATIVE_POINTER_CRASH=" + json.dumps(checks, sort_keys=True))  # noqa: T201 - fixed comparisons
        assert all(checks.values()), checks
    finally:
        child.close()
