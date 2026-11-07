"""Unused lifecycle contracts; private fake seams are not native qualification."""

from __future__ import annotations

import ctypes
import gc
import json
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
import shiboken6
from PySide6.QtCore import (
    QByteArray,
    QCoreApplication,
    QEvent,
    QObject,
    QThread,
    QTimer,
    Signal,
)
from pytestqt.qtbot import QtBot

from verdiclip.capture.frame_envelope import header_for_rgb
from verdiclip.platform import helper_channel as channel
from verdiclip.platform.helper_protocol import Message, ProtocolError, encode
from verdiclip.platform.helper_runtime import RuntimeManifest
from verdiclip.platform.owned_helper import HelperStatus


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeManifest:
    """Provide bounded immutable data without claiming an installed runtime."""
    return RuntimeManifest(
        str(tmp_path / "python.exe"),
        (str(tmp_path),),
        (
            str(tmp_path / "a"),
            str(tmp_path / "b"),
            str(tmp_path / "c"),
            str(tmp_path / "d"),
            str(tmp_path / "e"),
            *(str(tmp_path / name) for name in ("f", "g", "h", "i", "j", "k")),
        ),
        ("1", "2"),
        (3, 13, 1),
        64,
    )


@pytest.fixture
def worker(
    runtime: RuntimeManifest, qapp: QCoreApplication
) -> Iterator[channel._Worker]:
    """Own state-only workers through explicit destruction on their GUI affinity."""
    value = channel._Worker(1, runtime, threading.Event())
    destroyed = []
    value.destroyed.connect(lambda: destroyed.append(True))
    value.session_deadline = value.deadline = time.monotonic() + 20
    value.launch_returned = True
    yield value
    if shiboken6.isValid(value):
        assert value.thread() == qapp.thread(), (
            "State worker moved to a live foreign thread"
        )
        value.deleteLater()
        QCoreApplication.sendPostedEvents(value, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(value)
    assert destroyed == [True]


def helper_status(
    *, retired: bool = False, exit_code: int | None = None
) -> HelperStatus:
    """Return immutable process evidence for deterministic callback ordering."""
    return HelperStatus(
        pid=7,
        producer_verified=True,
        exit_code=exit_code,
        remaining_pids=() if retired else (7,),
        termination_requested=False,
        cleanup_confirmed=retired,
        closed=retired,
        failure=None,
        cleanup_errors=(),
    )


def test_invalid_installed_runtime_rejects_and_retires_real_thread(
    runtime: RuntimeManifest, qtbot: QtBot
) -> None:
    """Invalid installed runtime closes exactly once without starting a process."""
    facade = channel.HelperChannel()
    results = []
    facade.terminal.connect(
        lambda generation, receipt: results.append((generation, json.loads(receipt)))
    )
    facade.start(runtime)
    qtbot.waitUntil(lambda: bool(results), timeout=2000)
    assert len(results) == 1
    assert results[0][1]["failure"]
    assert results[0][1]["passed"] is False
    assert results[0][1]["not_launched"] is True
    assert not channel._RETAINED
    assert facade._state == "terminal"


def test_deleted_facade_keeps_pending_worker_owned(
    runtime: RuntimeManifest, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Facade destruction latches cancellation without destroying a running QThread."""
    entered, release = threading.Event(), threading.Event()
    affinity = []

    def blocked_builder(*_args: object) -> None:
        affinity.append(QThread.currentThread())
        entered.set()
        release.wait(1)
        raise RuntimeError("test launch blocked")

    monkeypatch.setattr(channel, "_build_command", blocked_builder)
    facade = channel.HelperChannel()
    facade.start(runtime)
    qtbot.waitUntil(entered.is_set, timeout=1000)
    lifetime = facade._lifetime
    assert lifetime is not None
    facade.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert lifetime.cancelled.is_set()
    assert lifetime in channel._RETAINED
    assert affinity[0] != QThread.currentThread()
    release.set()
    qtbot.waitUntil(lambda: lifetime not in channel._RETAINED, timeout=2000)


@pytest.mark.parametrize(
    "payload", [bytearray(b"{}"), b"[]", b'{"x":NaN}', b'{"x":1,"x":2}']
)
def test_payload_admission_is_strict(payload: bytes) -> None:
    """Queue admission rejects mutable, nonobject and malformed JSON."""
    with pytest.raises((ValueError, ProtocolError)):
        channel._payload(payload)


def test_facade_single_flight_and_reentrant_reply(qapp: QCoreApplication) -> None:
    """State changes before signal emission; a second queued request is rejected."""
    del qapp
    facade = channel.HelperChannel()
    facade._state = "ready"
    facade._generation = 1
    emitted = []
    facade._request.connect(lambda caller, payload: emitted.append((caller, payload)))
    facade.submit(4, b"{}")
    with pytest.raises(RuntimeError):
        facade.submit(5, b"{}")
    facade.reply.connect(lambda *_: facade.submit(5, b"{}"))
    facade._reply(1, 4, b"{}")
    assert emitted == [(4, b"{}"), (5, b"{}")]
    facade.cancel()
    facade._reply(1, 5, b"{}")
    assert facade._state == "closing"
    assert len(emitted) == 2


def test_facade_rejects_stale_terminal_and_cancelled_success(
    qapp: QCoreApplication,
) -> None:
    """Cancellation prevents a queued success from becoming a successful result."""
    del qapp
    facade = channel.HelperChannel()
    facade._generation, facade._state = 2, "ready"
    results = []
    facade.terminal.connect(lambda _, value: results.append(json.loads(value)))
    facade._retired(1, b'{"passed":true}')
    assert not results
    facade.cancel()
    facade._retired(2, b'{"passed":true}')
    facade._retired(2, b'{"passed":true}')
    assert len(results) == 1
    assert results[0]["passed"] is False
    assert results[0]["failure"] == "cancelled"


def test_cancel_during_ready_verification_blocks_send(worker: channel._Worker) -> None:
    """A slow verification callback cannot admit readiness after cancellation."""
    worker.helper = Mock()
    worker.peer = 7
    worker.helper.verify_ready.side_effect = lambda *_: (
        worker.cancelled.set(),
        helper_status(),
    )[1]
    with pytest.raises(ProtocolError, match="cancelled"):
        worker._message(
            Message(
                "ready",
                0,
                {
                    "pid": 7,
                    "executable": "owned",
                    "epoch": worker.epoch,
                    "protocol_version": channel.VERSION,
                    "peer_schema": channel.PEER_SCHEMA,
                    "credit_schema": channel.CREDIT_SCHEMA,
                },
            )
        )
    assert worker.phase == "starting"


def test_worker_queued_submit_after_cancel_writes_nothing(
    worker: channel._Worker,
) -> None:
    """Queued-before-cancel work is checked again on the lifecycle thread."""
    worker.phase = "ready"
    worker.socket = Mock()
    worker.cancelled.set()
    worker.submit(1, b"{}")
    worker.socket.write.assert_not_called()
    assert worker.failure == "cancelled"


def test_slow_send_validation_rechecks_deadline(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No native write occurs if serialization crosses the deadline."""
    worker.socket = Mock()
    original = channel.encode

    def late_encode(message: Message) -> bytes:
        data = original(message)
        worker.deadline = 0
        return data

    monkeypatch.setattr(channel, "encode", late_encode)
    with pytest.raises(ProtocolError, match="timeout"):
        worker._send({})
    worker.socket.write.assert_not_called()


def test_trailing_duplicate_prevents_reply_notification(
    worker: channel._Worker,
) -> None:
    """Validate all bytes in the current batch before exposing the first reply."""
    worker.phase = "busy"
    worker.flight.begin({})
    wire = encode(Message("response", 1, {})) * 2
    worker.socket = Mock()
    worker.socket.bytesAvailable.side_effect = [len(wire), 0]
    worker.socket.read.return_value = QByteArray(wire)
    replies = []
    worker.reply.connect(lambda *_: replies.append(True))
    worker._readable()
    assert not replies
    assert worker.failure == "unsolicited-message"


def test_retirement_waits_for_clean_eof(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kernel retirement alone cannot discard late trailing pipe bytes."""
    worker.phase = "retiring"
    worker.helper = Mock()
    worker.helper.poll.return_value = helper_status(retired=True, exit_code=0)
    terminal = Mock()
    monkeypatch.setattr(worker, "_terminal", terminal)
    worker._poll()
    terminal.assert_not_called()
    worker.eof = True
    worker.frame_eof = True
    worker._poll()
    terminal.assert_called_once()


def test_cleanup_timeout_releases_uncertain_handles(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timeout has explicit unconfirmed cleanup rather than a success receipt."""
    worker.phase = "retiring"
    worker.deadline = 0
    worker.helper = Mock()
    terminal = Mock()
    monkeypatch.setattr(worker, "_terminal", terminal)
    worker._poll()
    worker.helper.release_unconfirmed.assert_called_once()
    terminal.assert_called_once()
    assert worker.cleanup_failure == "cleanup-timeout"


@pytest.mark.parametrize("caller", [True, 0, -1, 128, "1"])
def test_caller_id_rejection_precedes_queue(
    caller: object, qapp: QCoreApplication
) -> None:
    """Malformed or reserved correlations cannot enqueue a worker operation."""
    del qapp
    facade = channel.HelperChannel()
    facade._state = "ready"
    sent = []
    facade._request.connect(lambda *_: sent.append(True))
    with pytest.raises((ValueError, RuntimeError)):
        facade.submit(caller, b"{}")  # ty: ignore[invalid-argument-type] - hostile caller
    assert not sent


def test_finish_rejects_outstanding_request_and_cancel_wake_is_bounded(
    qapp: QCoreApplication,
) -> None:
    """Cancellation latches once; finishing never silently queues behind work."""
    del qapp
    facade = channel.HelperChannel()
    facade._state = "busy"
    wakes = []
    facade._cancel.connect(lambda: wakes.append(True))
    with pytest.raises(RuntimeError):
        facade.finish()
    for _ in range(100):
        facade.cancel()
    assert wakes == [True]


def test_native_peer_mismatch_prevents_ready_validation(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A self-reported PID cannot replace the kernel pipe-client identity."""
    worker.server = Mock()
    worker.socket = None
    worker.helper = Mock()
    worker.helper.poll.return_value = helper_status()
    monkeypatch.setattr(channel, "_peer_pid", lambda _: 999)
    worker._connected()
    assert worker.failure == "wrong-peer-pid"
    worker.helper.verify_ready.assert_not_called()


def test_nonzero_exit_after_finish_is_failure(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protocol completion cannot launder a failed producer exit."""
    worker.phase = "retiring"
    worker.eof = True
    worker.frame_eof = True
    worker.helper = Mock()
    worker.helper.poll.return_value = helper_status(retired=True, exit_code=9)
    monkeypatch.setattr(worker, "_terminal", Mock())
    worker._poll()
    assert worker.failure == "producer-failed"


def test_expired_final_reply_rejects_before_cleanup_transition(
    worker: channel._Worker,
) -> None:
    """A message callback cannot win an already-expired deadline's timer race."""
    worker.phase = "busy"
    worker.finish_requested = True
    worker.flight.begin({})
    worker.deadline = 0
    with pytest.raises(ProtocolError, match="timeout"):
        worker._message(Message("response", 1, {"operation": "finished"}))
    assert worker.phase == "busy"


def test_partial_trailing_frame_fails_before_reply(worker: channel._Worker) -> None:
    """Even incomplete unsolicited bytes prevent exposing an apparently valid reply."""
    worker.phase = "busy"
    worker.flight.begin({})
    wire = encode(Message("response", 1, {})) + b"\x00"
    worker.socket = Mock()
    worker.socket.bytesAvailable.side_effect = [len(wire), 0, 0]
    worker.socket.read.return_value = QByteArray(wire)
    replies = []
    worker.reply.connect(lambda *_: replies.append(True))
    worker._readable()
    assert not replies
    assert worker.failure == "unsolicited-trailing-bytes"


def test_lifetime_requires_both_notifications_and_latches_before_emit(
    runtime: RuntimeManifest,
    qapp: QCoreApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test the completion latch without moving an owner to an unstarted thread."""
    del qapp
    # This state-only seam keeps deferred deletion on the active GUI loop.
    # The separate real-QThread roundtrip still exercises actual affinity.
    monkeypatch.setattr(channel._Worker, "moveToThread", lambda _self, _thread: None)
    lifetime = channel._Lifetime(3, runtime, threading.Event())
    assert lifetime.worker.thread() == QThread.currentThread()
    destroyed = []
    lifetime.worker.destroyed.connect(lambda: destroyed.append("worker"))
    lifetime.worker_thread.destroyed.connect(lambda: destroyed.append("thread"))
    events = []
    lifetime.terminal.connect(lambda *_: (events.append(True), lifetime._complete()))
    lifetime._retired(2, b"wrong")
    assert lifetime.receipt is None
    lifetime._retired(3, b"receipt")
    assert not events
    lifetime._stopped()
    assert events == [True]
    assert lifetime not in channel._RETAINED
    lifetime.worker.deleteLater()  # GUI affinity in this state-only test.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    assert sorted(destroyed) == ["thread", "worker"]


def test_real_qt_worker_roundtrip_uses_only_private_fake_boundary(  # noqa: C901 - complete fake peer
    runtime: RuntimeManifest,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise thread ownership and retirement with a fake peer, not native proof."""
    shared: dict[str, Peer] = {}
    epochs: list[str] = []
    affinities = []

    options = channel.QLocalServer.SocketOption

    class Peer(QObject):
        SocketOption = options
        newConnection = Signal()  # noqa: N815 - Qt-shaped test boundary
        readyRead = Signal()  # noqa: N815 - Qt-shaped test boundary
        disconnected = Signal()

        def __init__(self, owner: QObject) -> None:
            super().__init__(owner)
            self.data = b""
            self.decoder = channel.Decoder()
            self.status = helper_status()
            affinities.append(QThread.currentThread())

        def setSocketOptions(self, _option: object) -> None:  # noqa: N802
            pass

        def setMaxPendingConnections(self, _count: int) -> None:  # noqa: N802
            pass

        def listen(self, name: str) -> bool:
            shared["frame" if name.endswith("-frame") else "peer"] = self
            return True

        def nextPendingConnection(self) -> Peer:  # noqa: N802
            return self

        def setReadBufferSize(self, _size: int) -> None:  # noqa: N802
            pass

        def socketDescriptor(self) -> int:  # noqa: N802
            return 88

        def bytesAvailable(self) -> int:  # noqa: N802
            return len(self.data)

        def read(self, size: int) -> QByteArray:
            data, self.data = self.data[:size], self.data[size:]
            return QByteArray(data)

        def bytesToWrite(self) -> int:  # noqa: N802
            return 0

        def write(self, data: bytes) -> int:
            request = self.decoder.feed(data)[0]
            finishing = request.payload == {"operation": "finish"}
            response: dict[str, object] = (
                {"operation": "finished"} if finishing else {"echo": request.payload}
            )
            self.data += encode(Message("response", request.request_id, response))
            QTimer.singleShot(0, self.readyRead.emit)
            if finishing:
                QTimer.singleShot(5, self.end)
            return len(data)

        def end(self) -> None:
            self.status = helper_status(retired=True, exit_code=0)
            shared["frame"].disconnected.emit()
            self.disconnected.emit()

        def close(self) -> None:
            pass

        def abort(self) -> None:
            self.status = helper_status(retired=True, exit_code=1)

    class Helper:
        def __init__(self) -> None:
            self.peer = shared["peer"]

        @property
        def status(self) -> HelperStatus:
            return self.peer.status

        def start(self, _command: object) -> None:
            self.peer.data = encode(
                Message(
                    "ready",
                    0,
                    {
                        "pid": 7,
                        "executable": "fake",
                        "epoch": epochs[0],
                        "protocol_version": channel.VERSION,
                        "peer_schema": channel.PEER_SCHEMA,
                        "credit_schema": channel.CREDIT_SCHEMA,
                    },
                )
            )
            QTimer.singleShot(0, shared["frame"].newConnection.emit)
            QTimer.singleShot(0, self.peer.newConnection.emit)

        def poll(self) -> HelperStatus:
            return self.status

        def verify_ready(self, _pid: int, _executable: str) -> HelperStatus:
            return self.status

        def terminate(self) -> None:
            self.peer.abort()

    def fixed_test_builder(
        _runtime: RuntimeManifest, launch: channel.PeerLaunch
    ) -> None:
        epochs.append(launch.epoch)

    monkeypatch.setattr(channel, "_build_command", fixed_test_builder)
    monkeypatch.setattr(channel, "QLocalServer", Peer)
    monkeypatch.setattr(channel, "OwnedHelper", Helper)
    monkeypatch.setattr(channel, "_peer_pid", lambda _: 7)
    facade = channel.HelperChannel()
    replies, receipts = [], []
    facade.ready.connect(lambda _: facade.submit(4, b'{"value":1}'))
    facade.reply.connect(
        lambda _generation, caller, payload: (
            replies.append((caller, json.loads(payload))),
            facade.finish(),
        )
    )
    facade.terminal.connect(lambda _, payload: receipts.append(json.loads(payload)))
    facade.start(runtime)
    qtbot.waitUntil(lambda: bool(receipts), timeout=2000)
    assert replies == [(4, {"echo": {"value": 1}})]
    assert receipts[0]["passed"] is True
    assert affinities[0] != QThread.currentThread()
    assert not channel._RETAINED


@pytest.mark.parametrize("finished", [9.999, 10.0, 10.001])
def test_write_cannot_reset_elapsed_deadline(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
    finished: float,
) -> None:
    """A slow successful write cannot erase the prior operation deadline."""
    now = [9.0]
    monkeypatch.setattr(channel.time, "monotonic", lambda: now[0])
    worker.deadline, worker.session_deadline, worker.phase = 10, 20, "ready"
    worker.socket = Mock()
    worker.socket.bytesToWrite.return_value = 0

    def write(data: bytes) -> int:
        now[0] = finished
        return len(data)

    worker.socket.write.side_effect = write
    if finished < 10:
        worker._send({})
        assert worker.phase == "busy"
    else:
        with pytest.raises(ProtocolError, match="timeout"):
            worker._send({})
        assert worker.phase == "ready"
        assert worker.deadline == 10
    worker.socket.write.assert_called_once()


@pytest.mark.parametrize("finished", [9.999, 10.0, 10.001])
def test_response_serialization_cannot_reset_elapsed_deadline(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
    finished: float,
) -> None:
    """A response is not admitted if serialization crosses its old deadline."""
    now = [9.0]
    monkeypatch.setattr(channel.time, "monotonic", lambda: now[0])
    worker.deadline, worker.session_deadline, worker.phase = 10, 20, "busy"
    worker.flight.begin({})
    serialize = channel._serialized

    def delayed(value: object) -> bytes:
        now[0] = finished
        return serialize(value)

    monkeypatch.setattr(channel, "_serialized", delayed)
    if finished < 10:
        worker._message(Message("response", 1, {}))
        assert worker.phase == "ready"
    else:
        with pytest.raises(ProtocolError, match="timeout"):
            worker._message(Message("response", 1, {}))
        assert worker.pending_reply is None
        assert worker.phase == "busy"
        assert worker.deadline == 10


@pytest.mark.parametrize("stage", ["close", "serialize"])
@pytest.mark.parametrize("finished", [9.999, 10.0, 10.001])
def test_terminal_work_cannot_cross_cleanup_admission_deadline(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    finished: float,
) -> None:
    """Terminal resource close and receipt serialization cannot admit late success."""
    now = [9.0]
    monkeypatch.setattr(channel.time, "monotonic", lambda: now[0])
    worker.deadline = 10
    worker.phase = "retiring"
    worker.finish_requested = worker.eof = worker.frame_eof = True
    worker.helper = Mock()
    worker.helper.status = helper_status(retired=True, exit_code=0)
    worker.socket = Mock()
    if stage == "close":
        worker.socket.close.side_effect = lambda: now.__setitem__(0, finished)
    else:
        serialize = channel._serialized

        def delayed(value: object) -> bytes:
            now[0] = finished
            return serialize(value)

        monkeypatch.setattr(channel, "_serialized", delayed)
    monkeypatch.setattr(channel.QThread, "currentThread", Mock)
    receipts = []
    worker.terminal.connect(lambda _, value: receipts.append(json.loads(value)))
    worker._terminal()
    worker._terminal()
    assert len(receipts) == 1
    assert receipts[0]["passed"] == (finished < 10)
    if finished >= 10:
        assert receipts[0]["cleanup_failure"] == "cleanup-timeout"


def test_finish_acceptance_rechecks_after_flight_validation(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final response cannot reset the deadline after delayed correlation checks."""
    worker.phase = "busy"
    worker.finish_requested = True
    worker.flight.begin({})
    accept = worker.flight.accept

    def delayed(message: Message) -> None:
        accept(message)
        worker.deadline = 0

    monkeypatch.setattr(worker.flight, "accept", delayed)
    with pytest.raises(ProtocolError, match="timeout"):
        worker._message(Message("response", 1, {"operation": "finished"}))
    assert worker.phase == "busy"


@pytest.mark.parametrize("stage", ["write", "serialize"])
def test_reentrant_cancellation_blocks_transition(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    """A cancellation latched inside slow work prevents its result admission."""
    if stage == "write":
        worker.phase = "ready"
        worker.socket = Mock()
        worker.socket.bytesToWrite.return_value = 0

        def write(data: bytes) -> int:
            worker.cancelled.set()
            return len(data)

        worker.socket.write.side_effect = write
        with pytest.raises(ProtocolError, match="cancelled"):
            worker._send({})
        assert worker.phase == "ready"
    else:
        worker.phase = "busy"
        worker.flight.begin({})
        serialize = channel._serialized

        def cancelled(value: object) -> bytes:
            worker.cancelled.set()
            return serialize(value)

        monkeypatch.setattr(channel, "_serialized", cancelled)
        with pytest.raises(ProtocolError, match="cancelled"):
            worker._message(Message("response", 1, {}))
        assert worker.phase == "busy"
        assert worker.pending_reply is None


def test_ready_epoch_mismatch_precedes_producer_validation(
    worker: channel._Worker,
) -> None:
    """A different session cannot authenticate by reusing a valid PID/executable."""
    worker.helper = Mock()
    worker.peer = 7
    with pytest.raises(ProtocolError, match="wrong-session-epoch"):
        worker._message(
            Message(
                "ready",
                0,
                {
                    "pid": 7,
                    "executable": "owned",
                    "epoch": "f" * 32,
                    "protocol_version": channel.VERSION,
                    "peer_schema": channel.PEER_SCHEMA,
                    "credit_schema": channel.CREDIT_SCHEMA,
                },
            )
        )
    worker.helper.verify_ready.assert_not_called()


def test_fixed_builder_validates_current_parent_before_launch_target(
    runtime: RuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even the private fixed builder cannot launch a caller-selected stale runtime."""
    launch = channel.PeerLaunch("VerdiClip-helper-" + "a" * 32, "b" * 32, 7)
    monkeypatch.setattr(channel, "inspect_runtime", lambda: None)
    with pytest.raises(ValueError, match="differs from installed parent"):
        channel._fixed_peer_command(runtime, launch)


def test_fixed_builder_uses_only_attested_inert_peer(
    runtime: RuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No caller-controlled source/module or working directory enters command argv."""
    launch = channel.PeerLaunch("VerdiClip-helper-" + "a" * 32, "b" * 32, 7)
    monkeypatch.setattr(channel, "inspect_runtime", lambda: runtime)
    command = Mock()
    monkeypatch.setattr(channel, "HelperCommand", command)
    channel._fixed_peer_command(runtime, launch)
    executable, arguments, directory = command.call_args.args
    assert executable == runtime.executable
    assert arguments == channel.peer_arguments(runtime.serialize(), launch.serialize())
    assert directory == str(Path(runtime.executable).parent)
    channel._build_command(runtime, launch)
    assert command.call_count == 2
    assert command.call_args.args == (executable, arguments, directory)


def test_identity_notification_cancellation_suppresses_ready(
    qapp: QCoreApplication,
) -> None:
    """Ready cannot escape after an identity observer cancels the startup."""
    del qapp
    facade = channel.HelperChannel()
    facade._generation, facade._state = 1, "starting"
    ready = []
    facade.identity.connect(lambda *_: facade.cancel())
    facade.ready.connect(lambda *_: ready.append(True))
    facade._identity(1, b'{"pid":7}')
    facade._ready(1)
    assert not ready


@pytest.mark.parametrize(
    "change",
    [
        {"failure": "inspection failed"},
        {"closed": True},
        {"termination_requested": True},
        {"exit_code": 0},
        {"cleanup_errors": ("inspection failed",)},
        {"remaining_pids": ()},
        {"cleanup_confirmed": True},
        {"pid": 99},
    ],
)
def test_verified_flag_does_not_admit_failed_lifetime(
    worker: channel._Worker,
    change: dict[str, object],
) -> None:
    """Verification's final poll must still prove a live, healthy owned producer."""
    worker.helper = Mock()
    worker.peer = 7
    worker.helper.verify_ready.return_value = replace(helper_status(), **change)
    with pytest.raises(ProtocolError, match="unverified-producer"):
        worker._message(
            Message(
                "ready",
                0,
                {
                    "pid": 7,
                    "executable": "owned",
                    "epoch": worker.epoch,
                    "protocol_version": channel.VERSION,
                    "peer_schema": channel.PEER_SCHEMA,
                    "credit_schema": channel.CREDIT_SCHEMA,
                },
            )
        )
    assert worker.phase == "starting"
    assert worker.verified_identity is None


def test_default_builder_has_only_fixed_inert_dispatch(
    runtime: RuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default construction delegates only to the fixed attested internal target."""
    launch = channel.PeerLaunch("VerdiClip-helper-" + "a" * 32, "b" * 32, 7)
    fixed = Mock()
    monkeypatch.setattr(channel, "_fixed_peer_command", fixed)
    assert channel._build_command(runtime, launch) is fixed.return_value
    fixed.assert_called_once_with(runtime, launch)


def synchronous_peer(worker: channel._Worker) -> tuple[Mock, Mock]:
    """Keep deterministic callback coverage separate from the real QThread proof."""
    socket = Mock()
    socket.bytesAvailable.return_value = 0
    socket.bytesToWrite.return_value = 0
    socket.write.side_effect = len
    helper = Mock()
    helper.status = helper_status()
    helper.poll.return_value = helper.status
    helper.verify_ready.return_value = helper.status
    worker.socket, worker.helper = socket, helper
    worker.frame_socket = Mock()
    worker.frame_socket.bytesAvailable.return_value = 0
    return socket, helper


def feed_peer(worker: channel._Worker, socket: Mock, message: Message) -> None:
    """Deliver one exact batch through the real decoder and callback admission."""
    payload = encode(message)
    pending = bytearray(payload)
    socket.bytesAvailable.side_effect = lambda: len(pending)

    def read(size: int) -> QByteArray:
        data = bytes(pending[:size])
        del pending[:size]
        return QByteArray(data)

    socket.read.side_effect = read
    worker._readable()
    socket.bytesAvailable.side_effect = None
    socket.bytesAvailable.return_value = 0


def test_direct_callbacks_complete_verified_roundtrip(worker: channel._Worker) -> None:
    """Assert identity, correlation, finish and EOF on every synchronous transition."""
    socket, helper = synchronous_peer(worker)
    worker.peer = 7
    identities, ready, replies = [], [], []
    worker.identity.connect(
        lambda generation, data: identities.append((generation, json.loads(data)))
    )
    worker.ready.connect(ready.append)
    worker.reply.connect(
        lambda gen, caller, data: replies.append((gen, caller, json.loads(data)))
    )
    identity: dict[str, object] = {
        "pid": 7,
        "executable": "fake",
        "epoch": worker.epoch,
        "protocol_version": channel.VERSION,
        "peer_schema": channel.PEER_SCHEMA,
        "credit_schema": channel.CREDIT_SCHEMA,
    }
    feed_peer(worker, socket, Message("ready", 0, identity))
    assert identities == [(1, identity)]
    assert ready == [1]
    helper.verify_ready.assert_called_once_with(7, "fake")
    worker.submit(4, b'{"operation":"ping"}')
    assert worker.phase == "busy"
    assert worker.caller_id == 4
    feed_peer(worker, socket, Message("response", 1, {"operation": "pong"}))
    assert replies == [(1, 4, {"operation": "pong"})]
    worker.finish()
    assert worker.finish_requested
    feed_peer(worker, socket, Message("response", 2, {"operation": "finished"}))
    assert worker.phase == "retiring"
    worker._disconnected()
    assert worker.eof
    assert worker.failure is None
    assert socket.write.call_count == 2


@pytest.mark.parametrize(
    "failure", [None, "listen", "launch", "cancel-before", "cancel-after"]
)
def test_start_resource_admission_and_failure_cleanup(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    """Retain created resources and latch cancellation on both sides of launch."""
    server, timer, helper = Mock(), Mock(), Mock()
    server.listen.return_value = failure != "listen"
    factory = Mock(return_value=server)
    factory.SocketOption = channel.QLocalServer.SocketOption
    monkeypatch.setattr(channel, "QLocalServer", factory)
    monkeypatch.setattr(channel, "QTimer", Mock(return_value=timer))
    monkeypatch.setattr(channel, "OwnedHelper", Mock(return_value=helper))
    builder = Mock(return_value="fixed-test-command")
    monkeypatch.setattr(channel, "_build_command", builder)
    if failure == "cancel-before":
        worker.cancelled.set()
    elif failure == "cancel-after":
        helper.start.side_effect = lambda _command: worker.cancelled.set()
    elif failure == "launch":
        helper.start.side_effect = OSError("native launch failure")
    worker.start()
    assert worker.launch_returned
    timer.start.assert_called_once()
    if failure is None:
        assert worker.failure is None
        assert worker.phase == "starting"
        helper.start.assert_called_once_with("fixed-test-command")
    else:
        assert worker.failure is not None
        assert worker.phase == "retiring"
        if failure in ("launch", "cancel-after"):
            helper.terminate.assert_called_once()
        else:
            helper.start.assert_not_called()
    if failure == "cancel-before":
        builder.assert_not_called()
    else:
        assert server.setMaxPendingConnections.call_count == (
            1 if failure == "listen" else 2
        )
        server.setMaxPendingConnections.assert_called_with(1)


@pytest.mark.parametrize(
    "mode", ["valid", "no-server", "no-socket", "no-helper", "duplicate"]
)
def test_connection_admits_only_one_owned_peer(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Wire notifications only after exact native PID and membership checks."""
    socket, helper = synchronous_peer(worker)
    server = Mock()
    server.nextPendingConnection.return_value = socket
    worker.server = server
    worker.socket = None
    monkeypatch.setattr(channel, "_peer_pid", lambda _descriptor: 7)
    if mode == "no-server":
        worker.server = None
    elif mode == "no-socket":
        server.nextPendingConnection.return_value = None
    elif mode == "no-helper":
        worker.helper = None
    elif mode == "duplicate":
        worker.socket = socket
    worker._connected()
    if mode == "valid":
        assert worker.peer == 7
        assert worker.failure is None
        socket.readyRead.connect.assert_called_once()
        socket.disconnected.connect.assert_called_once()
    else:
        assert worker.failure in ("unexpected-connection", "missing-owned-peer")
        helper.verify_ready.assert_not_called()


@pytest.mark.parametrize(
    "mode", ["busy", "reserved", "caller", "no-socket", "buffered", "short-write"]
)
def test_submission_failures_send_no_second_command(
    worker: channel._Worker, mode: str
) -> None:
    """Invalid phase/correlation and failed writes latch retirement without retries."""
    socket, helper = synchronous_peer(worker)
    worker.phase = "ready"
    if mode == "busy":
        worker.phase = "busy"
    elif mode == "reserved":
        worker.flight.completed = channel.MAX_REQUESTS - 1
    elif mode == "caller":
        worker.caller_id = 1
    elif mode == "no-socket":
        worker.socket = None
    elif mode == "buffered":
        socket.bytesToWrite.return_value = 1
    elif mode == "short-write":
        socket.write.side_effect = None
        socket.write.return_value = 0
    worker.submit(1, b"{}")
    assert worker.failure is not None
    assert worker.phase == "retiring"
    helper.terminate.assert_called_once()
    assert socket.write.call_count == int(mode == "short-write")


@pytest.mark.parametrize(
    "mode",
    [
        "finish-busy",
        "cancel",
        "invalid-ready",
        "bad-pid",
        "no-helper",
        "bad-finished",
        "missing-identity",
        "early-eof",
    ],
)
def test_protocol_failures_preserve_no_success(
    worker: channel._Worker, mode: str
) -> None:
    """Exercise each rejection without replacing decoder or admission decisions."""
    socket, _helper = synchronous_peer(worker)
    worker.peer = 7
    if mode == "finish-busy":
        worker.finish()
        assert worker.failure == "finish-while-busy"
    elif mode == "cancel":
        worker.cancel()
        assert worker.failure == "cancelled"
    elif mode == "early-eof":
        worker._disconnected()
        assert worker.failure == "unexpected-eof"
    elif mode == "missing-identity":
        with pytest.raises(ProtocolError, match="missing-verified-identity"):
            worker._notify_ready()
    elif mode == "bad-finished":
        worker.phase = "ready"
        worker.finish()
        feed_peer(worker, socket, Message("response", 1, {"operation": "pong"}))
        assert worker.failure == "invalid-finished"
    else:
        payload: dict[str, object] = {
            "pid": 7,
            "executable": "fake",
            "epoch": worker.epoch,
            "protocol_version": channel.VERSION,
            "peer_schema": channel.PEER_SCHEMA,
            "credit_schema": channel.CREDIT_SCHEMA,
        }
        if mode == "invalid-ready":
            payload["extra"] = 1
        elif mode == "bad-pid":
            payload["pid"] = True
        else:
            worker.helper = None
        with pytest.raises(ProtocolError, match=r"invalid-ready|unverified-producer"):
            worker._message(Message("ready", 0, payload))


@pytest.mark.parametrize(
    "mode", ["done", "cancel", "expired", "no-helper", "poll-error", "late-poll"]
)
def test_poll_is_nonblocking_and_cannot_admit_late_success(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """One timer callback retains cleanup responsibility across each failure."""
    _socket, helper = synchronous_peer(worker)
    clock = [1.0]
    worker.deadline = worker.session_deadline = 2.0
    monkeypatch.setattr(channel.time, "monotonic", lambda: clock[0])
    terminal = Mock()
    monkeypatch.setattr(worker, "_terminal", terminal)
    if mode == "done":
        worker.done = True
    elif mode == "cancel":
        worker.cancelled.set()
    elif mode == "expired":
        clock[0] = 2.0
    elif mode == "no-helper":
        worker.helper = None
    elif mode == "poll-error":
        helper.poll.side_effect = OSError("inspection failed")
    else:

        def late() -> HelperStatus:
            clock[0] = 2.0
            return helper_status(retired=True, exit_code=0)

        helper.poll.side_effect = late
        worker.phase = "retiring"
    worker._poll()
    if mode == "no-helper":
        terminal.assert_called_once()
    else:
        terminal.assert_not_called()
    if mode in ("cancel", "expired", "poll-error"):
        assert worker.failure is not None
        helper.terminate.assert_called_once()
    if mode == "done":
        helper.poll.assert_not_called()
        worker._fail("ignored after terminal")
        assert worker.failure is None


def test_terminal_cancellation_closes_qt_resources_before_delivery(
    worker: channel._Worker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during terminal construction cannot expose passed true."""
    worker.cancelled.set()
    timer, server, thread = Mock(), Mock(), Mock()
    worker.timer, worker.server = timer, server
    monkeypatch.setattr(channel.QThread, "currentThread", lambda: thread)
    receipts = []
    worker.terminal.connect(
        lambda _generation, payload: receipts.append(json.loads(payload))
    )
    worker._terminal()
    assert receipts[0]["passed"] is False
    assert receipts[0]["failure"] == "cancelled"
    timer.stop.assert_called_once()
    server.close.assert_called_once()
    thread.quit.assert_called_once()


@pytest.mark.parametrize("success", [True, False])
def test_kernel_peer_pid_uses_exact_descriptor_and_error(
    monkeypatch: pytest.MonkeyPatch,
    *,
    success: bool,
) -> None:
    """Native server identity is not replaced with an advertised PID."""

    def query(descriptor: int, output: object) -> int:
        assert descriptor == 88
        ctypes.cast(cast("int", output), ctypes.POINTER(channel.wintypes.ULONG))[0] = 7
        return int(success)

    function = Mock(side_effect=query)
    kernel = Mock(GetNamedPipeClientProcessId=function)
    monkeypatch.setattr(
        channel.ctypes, "WinDLL", lambda *_a, **_kw: kernel, raising=False
    )
    monkeypatch.setattr(channel.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        channel.ctypes,
        "WinError",
        lambda code: OSError(f"native {code}"),
        raising=False,
    )
    if success:
        assert channel._peer_pid(88) == 7
    else:
        with pytest.raises(OSError, match="native 5"):
            channel._peer_pid(88)
    function.assert_called_once()


@pytest.mark.parametrize("remaining", [0, 1])
def test_empty_read_does_not_spin_or_admit_buffer_overflow(
    worker: channel._Worker,
    remaining: int,
) -> None:
    """A stalled read is bounded; unread trailing data disqualifies the session."""
    socket, _helper = synchronous_peer(worker)
    socket.bytesAvailable.side_effect = [1, remaining]
    socket.read.return_value = QByteArray()
    worker._readable()
    socket.read.assert_called_once()
    assert worker.failure == ("buffer-limit" if remaining else None)
    assert worker.verified_identity is None


def test_facade_wrong_thread_and_repeated_start_never_launch(
    runtime: RuntimeManifest,
    qapp: QCoreApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid facade use fails before constructing a lifetime or worker thread."""
    del qapp
    facade = channel.HelperChannel()
    lifetime = Mock()
    monkeypatch.setattr(channel, "_Lifetime", lifetime)
    facade._state = "starting"
    with pytest.raises(RuntimeError, match="cannot start"):
        facade.start(runtime)
    monkeypatch.setattr(channel.QThread, "currentThread", object)
    with pytest.raises(RuntimeError, match="owning thread"):
        facade.start(runtime)
    lifetime.assert_not_called()


def observe_value(
    worker: channel._Worker, width: int = 4, height: int = 3
) -> channel.Observation:
    """Independent exact schema for a full-panel binary transport test."""
    return channel.Observation.parse(
        json.dumps(
            {
                "observation_schema": 1,
                "epoch": worker.epoch,
                "request_id": 1,
                "frame_id": 1,
                "target": {
                    "pid": 7,
                    "process_created_100ns": 1234,
                    "hwnd": 99,
                    "point": [1, 1],
                    "framework": "WPF",
                    "runtime_id": [1, 2],
                },
                "guard": {
                    "foreground_hwnd": 99,
                    "foreground_pid": 7,
                    "input_tick": 123,
                    "root_client_rect": [0, 0, 1024, 512],
                    "target_rect": [0, 0, width, height],
                    "capture_rect": [0, 0, width, height],
                    "dpi": [96, 96],
                    "monitors": [[1, 0, 0, 1024, 512, True]],
                    "visibility_evidence": {
                        "scope": "owned-wpf-fixture-external-check-v1",
                        "root_visible": True,
                        "root_minimized": False,
                        "root_cloaked": False,
                        "external_intersection_count": 0,
                        "point_root_checks": 6,
                        "internal_overlay_assessment": "fixture-assumption-only",
                    },
                },
                "scroll": {
                    "horizontal": -1,
                    "vertical": 0,
                    "horizontal_view": 100,
                    "vertical_view": 25,
                    "horizontal_scrollable": False,
                    "vertical_scrollable": True,
                },
                "settled": {"samples": 3, "quiet_ns": 200000000},
            }
        ).encode()
    )


def buffered_socket() -> tuple[Mock, bytearray]:
    """Use actual bounded QByteArray reads with explicit buffered-disconnect order."""
    data = bytearray()
    socket = Mock()
    socket.bytesAvailable.side_effect = lambda: len(data)

    def read(size: int) -> QByteArray:
        result = bytes(data[:size])
        del data[:size]
        return QByteArray(result)

    socket.read.side_effect = read
    return socket, data


@pytest.fixture
def observing(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> tuple[channel._Worker, Mock, Mock, bytearray, list[int]]:
    """Start a real single-flight observe with deterministic same-host clocks."""
    clock = [1_000_000_000]
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: clock[0])
    monkeypatch.setattr(channel.time, "monotonic", lambda: clock[0] / 1_000_000_000)
    worker.session_deadline = worker.deadline = 20.0
    worker.phase = "ready"
    control, helper = synchronous_peer(worker)
    frame, data = buffered_socket()
    worker.frame_socket = frame
    target = channel.ObserveTarget(7, 1234, 99, (1, 1))
    worker.submit(
        1,
        json.dumps(
            {
                "operation": "observe",
                "observe_schema": 1,
                "deadline_ns": 3_000_000_000,
                "request": json.loads(target.serialize()),
            }
        ).encode(),
    )
    assert worker.failure is None
    assert worker.deadline == 3.0

    def grant(generation: int, raw: bytes) -> None:
        wait = channel.Decoder().feed(raw)[0]
        worker.grant_credit(
            generation, encode(Message("frame_credit", wait.request_id, wait.payload))
        )

    worker.credit_required.connect(grant)
    return worker, control, helper, data, clock


def observed_response(
    worker: channel._Worker, control: Mock, metadata: channel.Observation
) -> None:
    """Deliver metadata through the actual framed control decoder."""
    token = channel.CreditToken.for_chunk(
        worker.epoch, 1, metadata.frame_identity().payload_bytes, 0
    )
    messages = [
        Message(
            "response",
            1,
            {"operation": "observed", "observation": json.loads(metadata.canonical)},
        ),
        Message("frame_wait", 1, token.payload()),
    ]
    pending = bytearray(b"".join(encode(message) for message in messages))
    control.bytesAvailable.side_effect = lambda: len(pending)

    def read(size: int) -> QByteArray:
        payload = bytes(pending[:size])
        del pending[:size]
        return QByteArray(payload)

    control.read.side_effect = read
    worker._readable()
    control.bytesAvailable.side_effect = None
    control.bytesAvailable.return_value = 0


@pytest.mark.parametrize(
    "order", ["metadata-first", "binary-first", "disconnect-before-metadata"]
)
def test_observe_waits_for_paired_eof_and_natural_retirement(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]],
    monkeypatch: pytest.MonkeyPatch,
    order: str,
) -> None:
    """No provisional reply, no pixels in signals, no success before both EOFs."""
    worker, control, helper, data, _clock = observing
    metadata = observe_value(worker)
    pixels = bytes(range(36))

    wire = header_for_rgb(metadata.frame_identity(), pixels).encode() + pixels
    replies, receipts = [], []
    worker.reply.connect(lambda *args: replies.append(args))
    worker.terminal.connect(lambda _g, raw: receipts.append(json.loads(raw)))
    if order == "metadata-first":
        observed_response(worker, control, metadata)
    data.extend(wire)
    worker._frame_readable()
    if order != "metadata-first":
        assert worker.frame_header_read == 128
        assert worker.frame_bytes == 0
        assert len(data) == 36
        assert not worker.frame_drain_queued
        if order == "disconnect-before-metadata":
            worker._frame_disconnected()
        observed_response(worker, control, metadata)
    assert replies == []
    if order != "disconnect-before-metadata":
        assert not worker.finish_requested
        worker._frame_disconnected()
    assert worker.frame_eof
    assert worker.frame_bytes == 36
    assert worker.deadline == 3.0
    assert control.write.call_count == 3  # observe, credit, automatic reserved finish
    feed_peer(worker, control, Message("response", 2, {"operation": "finished"}))
    assert worker.phase == "retiring"
    assert worker.deadline == 6.0
    assert not receipts
    worker._disconnected()
    helper.status = helper_status(retired=True, exit_code=0)
    helper.poll.return_value = helper.status
    monkeypatch.setattr(channel.QThread, "currentThread", Mock)
    worker._poll()
    assert len(receipts) == 1
    assert receipts[0]["passed"]
    assert receipts[0]["observe"]["observation"] == json.loads(metadata.canonical)
    assert (
        receipts[0]["observe"]["validated_ns"] < receipts[0]["observe"]["deadline_ns"]
    )
    assert receipts[0]["observe"]["finished_ns"] < receipts[0]["observe"]["deadline_ns"]
    assert replies == []


def test_frame_drain_is_bounded_and_single_continuation(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large buffered frames drain in bounded turns, never a queued chunk backlog."""
    worker, control, _helper, data, _clock = observing
    metadata = observe_value(worker, 256, 256)
    pixels = bytes(256 * 256 * 3)
    observed_response(worker, control, metadata)
    queued = Mock()
    monkeypatch.setattr(channel.QTimer, "singleShot", queued)
    data.extend(
        header_for_rgb(metadata.frame_identity(), pixels).encode() + pixels[:65536]
    )
    worker._frame_readable()
    assert len(data) == 128
    assert worker.frame_drain_queued
    queued.assert_called_once()
    worker._queue_frame()
    queued.assert_called_once()
    while data:
        worker._resume_frame()
    for index in (1, 2):
        token = channel.CreditToken.for_chunk(worker.epoch, 1, len(pixels), index)
        feed_peer(worker, control, Message("frame_wait", 1, token.payload()))
        data.extend(pixels[index * 65536 : (index + 1) * 65536])
        worker._frame_readable()
    worker._frame_disconnected()
    assert worker.frame_eof
    assert worker.failure is None
    assert all(
        call.args[0] <= 65536
        for call in cast(Mock, worker.frame_socket).read.call_args_list
    )


@pytest.mark.parametrize(
    "failure",
    [
        "digest",
        "trailer",
        "short-header",
        "short-body",
        "duplicate-metadata",
        "cancel",
        "deadline",
        "finish-deadline",
    ],
)
def test_observe_bad_stream_never_publishes(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]], failure: str
) -> None:
    """Every failure keeps first cause and discards provisional observation evidence."""
    worker, control, _helper, data, clock = observing
    metadata = observe_value(worker)
    pixels = bytes(range(36))
    header = header_for_rgb(metadata.frame_identity(), pixels).encode()
    observed_response(worker, control, metadata)
    if failure == "duplicate-metadata":
        feed_peer(
            worker,
            control,
            Message(
                "response",
                1,
                {
                    "operation": "observed",
                    "observation": json.loads(metadata.canonical),
                },
            ),
        )
    else:
        data.extend(header[:5] if failure == "short-header" else header)
        if failure != "short-header":
            data.extend(
                bytes(36)
                if failure == "digest"
                else pixels[:-1]
                if failure == "short-body"
                else pixels
            )
        if failure == "trailer":
            data.extend(b"x")
        elif failure == "cancel":
            worker.cancelled.set()
        elif failure == "deadline":
            clock[0] = 3_000_000_000
        worker._frame_disconnected()
        if failure == "finish-deadline":
            clock[0] = 3_000_000_000
            feed_peer(
                worker, control, Message("response", 2, {"operation": "finished"})
            )
    assert worker.failure is not None
    first = worker.failure
    worker._frame_readable()
    assert worker.failure == first
    assert worker.pending_reply is None


@pytest.mark.parametrize("frame_first", [True, False])
def test_inert_empty_binary_eof_is_order_independent(
    worker: channel._Worker, *, frame_first: bool
) -> None:
    """Inert finish still requires the empty data endpoint, in either callback order."""
    control, _helper = synchronous_peer(worker)
    worker.phase = "ready"
    worker.finish()
    if frame_first:
        worker._frame_disconnected()
    feed_peer(worker, control, Message("response", 1, {"operation": "finished"}))
    worker._disconnected()
    if not frame_first:
        worker._frame_disconnected()
    assert worker.eof
    assert worker.frame_eof
    assert worker.failure is None


def test_authoritative_ns_boundary_overrides_float_rounding(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A float clock just below deadline cannot admit the exact ns boundary."""
    worker, _control, _helper, _data, clock = observing
    clock[0] = 3_000_000_000
    monkeypatch.setattr(channel.time, "monotonic", lambda: 2.999999999)
    with pytest.raises(ProtocolError, match="deadline"):
        worker._admit()


def test_observe_facade_stamps_before_serializing_and_consumes_failure(
    runtime: RuntimeManifest, qapp: QCoreApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed/expired attempt cannot be retried or gain a worker-arrival budget."""
    del runtime, qapp
    facade = channel.HelperChannel()
    facade._state = "ready"
    now = [1_000_000_000]
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: now[0])
    target = channel.ObserveTarget(7, 1234, 99, (1, 1))
    original = channel.ObserveTarget.serialize

    def delayed(value: channel.ObserveTarget) -> bytes:
        now[0] = 3_000_000_000
        return original(value)

    monkeypatch.setattr(channel.ObserveTarget, "serialize", delayed)
    requests = []
    facade._request.connect(lambda *args: requests.append(args))
    with pytest.raises(ProtocolError, match="deadline"):
        facade.observe(1, target)
    assert facade._observe_consumed
    assert facade._cancelled.is_set()
    assert requests == []
    with pytest.raises(RuntimeError, match="cannot observe"):
        facade.observe(2, target)


@pytest.mark.parametrize("order", ["control-first", "frame-first"])
def test_ready_requires_both_authenticated_endpoints(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, order: str
) -> None:
    """A valid control handshake is provisional until the second kernel peer binds."""
    control, _helper = synchronous_peer(worker)
    worker.frame_socket = None
    worker.peer = 7
    frame, _data = buffered_socket()
    server = Mock()
    server.nextPendingConnection.return_value = frame
    worker.frame_server = server
    monkeypatch.setattr(channel, "_peer_pid", lambda _descriptor: 7)
    ready = []
    worker.ready.connect(ready.append)
    message = Message(
        "ready",
        0,
        {
            "pid": 7,
            "executable": "fake",
            "epoch": worker.epoch,
            "protocol_version": channel.VERSION,
            "peer_schema": channel.PEER_SCHEMA,
            "credit_schema": channel.CREDIT_SCHEMA,
        },
    )
    if order == "frame-first":
        worker._frame_connected()
    else:
        feed_peer(worker, control, message)
    assert ready == []
    if order == "frame-first":
        feed_peer(worker, control, message)
    else:
        worker._frame_connected()
    assert ready == [1]
    assert worker.failure is None
    worker._notify_ready()
    assert ready == [1]


@pytest.mark.parametrize(
    "failure",
    [
        "wrong-pid",
        "missing",
        "duplicate",
        "closed",
        "unrequested-eof",
        "unsolicited",
        "control-trailer",
    ],
)
def test_binary_endpoint_failures_never_admit_ready(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Actual peer ownership and startup stream validity are independent gates."""
    control, helper = synchronous_peer(worker)
    frame, data = buffered_socket()
    worker.frame_socket = frame if failure == "duplicate" else None
    worker.frame_server = Mock()
    worker.frame_server.nextPendingConnection.return_value = (
        None if failure == "missing" else frame
    )
    monkeypatch.setattr(
        channel, "_peer_pid", lambda _descriptor: 8 if failure == "wrong-pid" else 7
    )
    if failure == "closed":
        helper.poll.return_value = replace(helper_status(), closed=True)
    elif failure == "unsolicited":
        data.extend(b"x")
    elif failure == "control-trailer":
        worker.peer = 7
        worker.verified_identity = b"{}"
        worker.decoder.buffer.extend(b"x")
    ready = []
    worker.ready.connect(ready.append)
    worker._frame_connected()
    if failure == "unrequested-eof":
        worker._frame_disconnected()
    assert worker.failure is not None
    assert ready == []
    control.write.assert_not_called()


def test_session_cap_only_shortens_observe_deadline(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remaining session time cannot be renewed by beginning an observe command."""
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_000_000_000)
    monkeypatch.setattr(channel.time, "monotonic", lambda: 1.0)
    worker.deadline = worker.session_deadline = 1.5
    worker.phase = "ready"
    control, _helper = synchronous_peer(worker)
    target = channel.ObserveTarget(7, 1234, 99, (1, 1))
    worker.submit(
        1,
        json.dumps(
            {
                "operation": "observe",
                "observe_schema": 1,
                "deadline_ns": 3_000_000_000,
                "request": json.loads(target.serialize()),
            }
        ).encode(),
    )
    assert worker.observe_deadline_ns == 1_500_000_000
    decoded = channel.Decoder().feed(control.write.call_args.args[0])
    assert decoded[0].payload["deadline_ns"] == 1_500_000_000


def test_submit_cannot_bypass_typed_observe_api(qapp: QCoreApplication) -> None:
    """Public raw payload submission exposes no deadline override."""
    del qapp
    facade = channel.HelperChannel()
    facade._state = "ready"
    with pytest.raises(ValueError, match="typed one-shot"):
        facade.submit(1, b'{"operation":"observe"}')
    assert facade._state == "ready"


def test_empty_first_failure_is_stable(worker: channel._Worker) -> None:
    """A later cleanup callback cannot replace an empty original failure."""
    worker._fail("")
    worker._fail("later")
    assert worker.failure == "channel-failure"


def test_typed_observe_admission_and_metadata_progress(
    qapp: QCoreApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only immutable bounded metadata crosses facade signals, once per attempt."""
    del qapp
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_000_000_000)
    facade = channel.HelperChannel()
    facade._state = "ready"
    requests, progress = [], []
    facade._request.connect(
        lambda caller, payload: requests.append((caller, json.loads(payload)))
    )
    facade.progress.connect(
        lambda generation, payload: progress.append((generation, payload))
    )
    target = channel.ObserveTarget(7, 1234, 99, (1, 1))
    facade.observe(1, target)
    assert requests == [
        (
            1,
            {
                "operation": "observe",
                "observe_schema": 1,
                "deadline_ns": 3_000_000_000,
                "request": json.loads(target.serialize()),
            },
        )
    ]
    facade._progress(facade._generation, b'{"phase":"frame-body","bytes":1}')
    assert len(progress) == 1
    facade.cancel()
    facade._progress(facade._generation, b"{}")
    facade._progress(facade._generation + 1, b"{}")
    assert len(progress) == 1
    with pytest.raises(RuntimeError, match="cannot observe"):
        facade.observe(2, target)


@pytest.mark.parametrize("invalid", ["caller", "target"])
def test_invalid_typed_observe_consumes_attempt(
    qapp: QCoreApplication, invalid: str
) -> None:
    """Even invalid input cannot leave an admitted one-shot operation reusable."""
    del qapp
    facade = channel.HelperChannel()
    facade._state = "ready"
    with pytest.raises(
        ProtocolError, match=r"Invalid observe caller|immutable observation"
    ):
        facade.observe(
            True if invalid == "caller" else 1,
            {} if invalid == "target" else channel.ObserveTarget(7, 1, 99, (1, 1)),  # ty: ignore[invalid-argument-type]
        )
    assert facade._observe_consumed
    assert facade._cancelled.is_set()


def test_duplicate_ready_before_frame_is_rejected(worker: channel._Worker) -> None:
    """A pending second endpoint does not create a second control handshake slot."""
    worker.verified_identity = b"{}"
    with pytest.raises(ProtocolError, match="duplicate-ready"):
        worker._message(Message("ready", 0, {}))


@pytest.mark.parametrize("failure", ["exception", "boolean", "float", "regression"])
def test_facade_clock_failure_cancels_admitted_lifetime(
    qapp: QCoreApplication, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Clock acquisition belongs inside the cancellation-on-failure boundary."""
    del qapp
    facade = channel.HelperChannel()
    facade._state = "ready"
    calls = (
        Mock(side_effect=RuntimeError("clock unavailable"))
        if failure == "exception"
        else Mock(return_value=True if failure == "boolean" else 1.0)
    )
    if failure == "regression":
        calls.side_effect = [1_000_000_000, 900_000_000]
    monkeypatch.setattr(channel.time, "monotonic_ns", calls)
    requests = []
    facade._request.connect(lambda *args: requests.append(args))
    with pytest.raises((RuntimeError, ProtocolError)):
        facade.observe(1, channel.ObserveTarget(7, 1, 99, (1, 1)))
    assert facade._observe_consumed
    assert facade._cancelled.is_set()
    assert facade._state == "closing"
    assert requests == []


def test_worker_ns_regression_after_frame_validation_is_terminal(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]],
) -> None:
    """Once pairing is terminal, the finish path still retains the ns history."""
    worker, control, _helper, data, clock = observing
    metadata = observe_value(worker)
    observed_response(worker, control, metadata)
    data.extend(
        header_for_rgb(metadata.frame_identity(), bytes(36)).encode() + bytes(36)
    )
    worker._frame_disconnected()
    assert worker.frame_eof
    clock[0] = 900_000_000
    feed_peer(worker, control, Message("response", 2, {"operation": "finished"}))
    assert worker.failure == "invalid-or-regressed-monotonic-ns-clock"
    assert worker.observe_finished_ns is None


def test_partial_early_finished_prefix_cannot_be_completed_after_frame_eof(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]],
) -> None:
    """Busy frame reception is not permission to pre-send the next control response."""
    worker, control, _helper, data, _clock = observing
    metadata = observe_value(worker)
    early_finish = encode(Message("response", 2, {"operation": "finished"}))
    combined = (
        encode(
            Message(
                "response",
                1,
                {
                    "operation": "observed",
                    "observation": json.loads(metadata.canonical),
                },
            )
        )
        + early_finish[:9]
    )
    control.bytesAvailable.side_effect = [len(combined), 0, 0]
    control.read.return_value = QByteArray(combined)
    worker._readable()
    assert worker.failure is None  # A fragmented wait is legal, not an early finish.
    assert worker.credit_notified is None
    control.bytesAvailable.side_effect = [len(early_finish) - 9, 0]
    control.read.return_value = QByteArray(early_finish[9:])
    worker._readable()
    assert worker.failure == "wrong-id"
    first = worker.failure
    data.extend(
        header_for_rgb(metadata.frame_identity(), bytes(36)).encode() + bytes(36)
    )
    worker._frame_disconnected()
    assert worker.failure == first
    assert not worker.finish_requested
    assert control.write.call_count == 1
