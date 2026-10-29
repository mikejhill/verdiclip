"""Application-owned broker lifecycle with a device-free replay platform."""

from __future__ import annotations

from typing import override

import pytest
import shiboken6
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot
from tests.capture.input_fixture import ReplayInputPlatform
from tests.capture.test_input_worker import ReplayRun

from verdiclip.capture.input_broker import (
    InputBroker,
    NativeInputBroker,
    make_input_broker,
)
from verdiclip.capture.input_policy import InputKind
from verdiclip.capture.input_worker import CaptureInputWorker
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect


class BrokerFixture:
    """Every created worker is explicitly bound to a replay API."""

    def __init__(self, *, cleanup: bool = True) -> None:
        self.platforms: list[ReplayInputPlatform] = []
        self.workers: list[CaptureInputWorker] = []
        self.owner = QObject()
        self.cleanup = cleanup
        self.broker = NativeInputBroker(
            self.owner, factory=self.make, require_windows=False
        )

    def make(self, _point: Point, _bounds: Rect) -> CaptureInputWorker:
        """Never create the production platform or install native hooks."""
        platform = ReplayInputPlatform()
        platform.close_ok = self.cleanup
        self.platforms.append(platform)
        worker = ReplayRun.make(platform)
        self.workers.append(worker)
        return worker

    def start(self) -> None:
        """Use a synthetic display independent of the host topology."""
        self.broker.start(Point(262, 168), Rect(-1920, -1080, 5760, 3240))


def test_broker_releases_after_selection_and_allows_safe_retry(qtbot: QtBot) -> None:
    """A semantic event reaches Qt; cleanup precedes output and next reservation."""
    fixture = BrokerFixture()
    broker = fixture.broker
    try:
        for _attempt in range(2):
            with qtbot.waitSignal(broker.ready):
                fixture.start()
            assert broker.active
            with pytest.raises(CaptureError, match="released"):
                fixture.start()
            with qtbot.waitSignal(broker.input_event) as routed:
                fixture.platforms[-1].mouse(0x200, Point(300, 200))
            assert routed.args[0].kind is InputKind.MOVE
            with qtbot.waitSignal(broker.closed) as closed:
                broker.release()
            assert closed.args == [True, ""]
            assert fixture.platforms[-1].closed
            assert not broker.busy
    finally:
        broker.release()
        fixture.owner.deleteLater()


def test_unconfirmed_cleanup_blocks_retry(qtbot: QtBot) -> None:
    """A failed native cleanup receipt cannot look like successful cancellation."""
    fixture = BrokerFixture(cleanup=False)
    with qtbot.waitSignal(fixture.broker.ready):
        fixture.start()
    with qtbot.waitSignal(fixture.broker.closed) as closed:
        fixture.broker.release()
    assert not closed.args[0]
    assert fixture.broker.busy
    with pytest.raises(CaptureError, match="released"):
        fixture.start()
    # This test API owns no OS resource; production retains an unconfirmed lease.
    fixture.broker.deleteLater()
    fixture.owner.deleteLater()


def test_reentrant_closed_slot_cannot_delete_replacement_before_cleanup(
    qtbot: QtBot,
) -> None:
    """Owner destruction inside old retirement must retain the newly started lease."""
    fixture = BrokerFixture()
    receipts: list[bool] = []
    destroyed: list[bool] = []

    def closed(*, cleaned: bool) -> None:
        receipts.append(cleaned)
        if len(receipts) == 1:
            fixture.start()
            shiboken6.delete(fixture.owner)

    fixture.broker.closed.connect(lambda cleaned, _reason: closed(cleaned=cleaned))
    fixture.broker.destroyed.connect(
        lambda: destroyed.append(all(p.closed for p in fixture.platforms))
    )
    with qtbot.waitSignal(fixture.broker.ready):
        fixture.start()
    with qtbot.waitSignal(fixture.broker.destroyed):
        fixture.broker.release()
    assert receipts == [True, True]
    assert destroyed == [True]
    assert len(fixture.workers) == 2
    assert all(worker.stopped.is_set() for worker in fixture.workers)


def test_unsupported_boundary_never_silently_reserves_input(qtbot: QtBot) -> None:
    """Unsupported surfaces and the base contract fail before any OS construction."""
    base = InputBroker()
    assert not base.active
    assert not base.busy
    with pytest.raises(CaptureError, match="unavailable"):
        base.start(Point(0, 0), Rect(0, 0, 100, 100))
    with qtbot.waitSignal(base.closed) as receipt:
        base.release()
    assert receipt.args == [True, ""]
    owner = QObject()
    broker = make_input_broker(owner)
    with pytest.raises(CaptureError, match="native Windows"):
        broker.start(Point(0, 0), Rect(0, 0, 100, 100))
    assert not broker.busy
    shiboken6.delete(owner)
    base.deleteLater()


class PendingWorker(CaptureInputWorker):
    """A startup that never acknowledges ready, without a thread or native API."""

    @override
    def start(self) -> None:
        pass

    @override
    def release(self) -> None:
        self.cleaned = True
        self.stopped.set()


def test_startup_deadline_retires_unready_worker_before_retry(qtbot: QtBot) -> None:
    """A missing readiness receipt has a finite timeout and confirmed cleanup."""
    owner = QObject()
    now = [0.0]
    broker = NativeInputBroker(
        owner,
        factory=PendingWorker,
        clock=lambda: now[0],
        require_windows=False,
    )
    broker.start(Point(0, 0), Rect(0, 0, 100, 100))
    now[0] = 3.0
    with qtbot.waitSignal(broker.closed) as receipt:
        broker._advance()
    assert receipt.args == [True, "Capture input did not become ready in time"]
    assert not broker.active
    assert not broker.busy
    shiboken6.delete(owner)


@pytest.mark.parametrize("cleanup", [True, False])
def test_process_shutdown_waits_for_only_owned_worker(
    qtbot: QtBot, *, cleanup: bool
) -> None:
    """The quit hook retires its installer thread without publishing a capture."""
    fixture = BrokerFixture(cleanup=cleanup)
    broker = fixture.broker
    with qtbot.waitSignal(broker.ready):
        fixture.start()
    receipts: list[object] = []
    broker.closed.connect(lambda *args: receipts.append(args))
    broker._shutdown()
    assert fixture.workers[0].stopped.is_set()
    assert fixture.platforms[0].closed
    assert broker.busy is not cleanup
    assert not receipts
    with pytest.raises(CaptureError, match="released"):
        fixture.start()
    broker.deleteLater()
    fixture.owner.deleteLater()
