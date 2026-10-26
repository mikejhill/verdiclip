"""Ephemeral user-scoped IPC without UIA, a display or input devices."""

import pytest
from PySide6.QtCore import QObject, QSignalBlocker
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from pytestqt.qtbot import QtBot

from verdiclip.capture.scroll.uia_pipe import UiaPipe
from verdiclip.exceptions import CaptureError


def test_private_pipe_round_trip_and_owner_cleanup(qtbot: QtBot) -> None:
    """One accepted stream exchanges bytes and closes only its own endpoint."""
    owner = QObject()
    pipe = UiaPipe(owner)
    server = pipe.findChild(QLocalServer)
    assert server is not None
    assert server.socketOptions() == (QLocalServer.SocketOption.UserAccessOption)
    pipe.listen()
    with pytest.raises(CaptureError, match="unavailable"):
        pipe.write(b"not connected")
    client = QLocalSocket()
    incoming: list[bytes] = []
    pipe.received.connect(incoming.append)
    client.connectToServer(pipe.name)
    qtbot.waitUntil(
        lambda: client.state() is QLocalSocket.LocalSocketState.ConnectedState
    )
    client.write(b"fixture\n")
    qtbot.waitUntil(lambda: bool(incoming))
    assert b"".join(incoming) == b"fixture\n"
    pipe.write(b"reply\n")
    qtbot.waitUntil(lambda: client.bytesAvailable() > 0)
    assert client.readAll().data() == b"reply\n"
    pipe.close()
    with pytest.raises(CaptureError, match="unavailable"):
        pipe.write(b"closed")
    with pytest.raises(CaptureError, match="prepare"):
        pipe.listen()
    qtbot.waitUntil(
        lambda: client.state() is QLocalSocket.LocalSocketState.UnconnectedState
    )
    client.deleteLater()
    owner.deleteLater()


def test_lost_worker_connection_reports_failure(qtbot: QtBot) -> None:
    """A vanished peer cannot leave an apparently healthy warm worker."""
    owner = QObject()
    pipe = UiaPipe(owner)
    pipe.listen()
    client = QLocalSocket()
    client.connectToServer(pipe.name)
    qtbot.waitUntil(
        lambda: client.state() is QLocalSocket.LocalSocketState.ConnectedState
    )
    client.write(b"ready")
    with qtbot.waitSignal(pipe.received):
        client.flush()
    with qtbot.waitSignal(pipe.failed):
        client.abort()
    pipe.close()
    client.deleteLater()
    owner.deleteLater()


def test_buffered_pipe_receipt_does_not_require_ready_read_delivery(
    qtbot: QtBot,
) -> None:
    """A due deadline can inspect buffered bytes even if readyRead was overtaken."""
    owner = QObject()
    pipe = UiaPipe(owner)
    pipe.listen()
    client = QLocalSocket()
    incoming: list[bytes] = []
    pipe.received.connect(incoming.append)
    client.connectToServer(pipe.name)
    try:
        qtbot.waitUntil(lambda: pipe.findChild(QLocalSocket) is not None)
        accepted = pipe.findChild(QLocalSocket)
        assert accepted is not None
        with QSignalBlocker(accepted):
            client.write(b'{"ready":true,"protocol":1}\n')
            client.flush()
            qtbot.waitUntil(lambda: accepted.bytesAvailable() > 0)
            assert not incoming
            pipe.drain()
            assert incoming == [b'{"ready":true,"protocol":1}\n']
            pipe.drain()
            assert len(incoming) == 1
    finally:
        pipe.close()
        client.abort()
        client.deleteLater()
        owner.deleteLater()
