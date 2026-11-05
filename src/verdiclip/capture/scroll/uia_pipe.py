"""Private per-worker local IPC, independent of PowerShell host standard input."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from PySide6.QtCore import QObject, QSignalBlocker, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from verdiclip.exceptions import CaptureError


class UiaPipe(QObject):
    """Accept one connection through an unpredictable, current-user-only endpoint."""

    received = Signal(bytes)
    failed = Signal(str)
    connected = Signal()

    def __init__(self, parent: QObject) -> None:
        """Own only this worker's ephemeral server and accepted stream."""
        super().__init__(parent)
        self.name = "verdiclip-uia-" + uuid4().hex
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._connected)
        self._socket: QLocalSocket | None = None
        self._closed = False

    def listen(self) -> None:
        """Create no shared or reusable application endpoint."""
        if self._closed or not self._server.listen(self.name):
            raise CaptureError("Could not prepare private UI Automation transport")

    def _connected(self) -> None:
        # Qt returns nullptr for an empty queue; PySide 6.11 omits it in its stub.
        socket = cast("QLocalSocket | None", self._server.nextPendingConnection())
        if socket is None:
            return
        if self._closed or self._socket is not None:
            socket.abort()
            socket.deleteLater()
            return
        self._socket = socket
        socket.setParent(self)
        self._server.close()
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self._disconnected)
        self.connected.emit()
        self._read()

    def _read(self) -> None:
        socket = self._socket
        if not self._closed and socket is not None and socket.bytesAvailable():
            self.received.emit(socket.readAll().data())

    def drain(self) -> None:
        """Read only bytes already available, without waiting or pumping events."""
        if (
            not self._closed
            and self._socket is None
            and self._server.hasPendingConnections()
        ):
            self._connected()
        self._read()

    def _disconnected(self) -> None:
        if not self._closed:
            self.failed.emit("The private UI Automation transport disconnected")

    def write(self, data: bytes) -> None:
        """Only the accepted current worker can receive protocol bytes."""
        socket = self._socket
        if self._closed or socket is None or socket.write(data) != len(data):
            raise CaptureError("The private UI Automation transport is unavailable")

    def close(self) -> None:
        """Retire only the endpoint owned by this lease, without callbacks."""
        self._closed = True
        self._server.close()
        socket, self._socket = self._socket, None
        if socket is not None:
            with QSignalBlocker(socket):
                socket.abort()
            socket.deleteLater()
        self.deleteLater()
