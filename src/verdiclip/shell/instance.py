"""Single-instance guard and message passing to the running instance."""

from __future__ import annotations

import getpass
import json
import logging
from collections.abc import Sequence
from typing import Final

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS: Final = 500


class SingleInstance(QObject):
    """Ensure one tray instance per user; later launches forward their request."""

    message_received = Signal(list)  # list[str]: the forwarded arguments

    def __init__(self, name: str | None = None) -> None:
        super().__init__()
        self._name = name or f"VerdiClip-{getpass.getuser()}"
        self._server: QLocalServer | None = None

    @property
    def name(self) -> str:
        """Return the local socket name."""
        return self._name

    def forward(self, arguments: Sequence[str]) -> bool:
        """Send ``arguments`` to a running instance; True if one received them."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write(json.dumps(list(arguments)).encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        return True

    def listen(self) -> bool:
        """Become the primary instance; return False if the name is unavailable."""
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        if not server.listen(self._name):
            logger.warning(
                "Could not listen on %s: %s", self._name, server.errorString()
            )
            return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def close(self) -> None:
        """Stop listening."""
        if self._server is not None:
            self._server.close()
            self._server = None

    def _on_connection(self) -> None:
        """Read each forwarded message once its sender has finished."""
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            # The sender writes one message then disconnects; reading at that
            # point avoids missing data that arrived before signals connected.
            if socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                self._read(socket)
                continue
            socket.disconnected.connect(lambda s=socket: self._read(s))

    def _read(self, socket: QLocalSocket) -> None:
        """Decode and emit a forwarded argument list."""
        payload = bytes(socket.readAll().data()).decode("utf-8", errors="replace")
        socket.deleteLater()
        try:
            decoded: object = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed instance message")
            return
        if isinstance(decoded, list):
            self.message_received.emit([str(item) for item in decoded])
