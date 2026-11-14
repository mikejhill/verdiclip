"""Serve automation requests on a per-user local socket in the GUI thread."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from verdiclip.automation.protocol import (
    MAX_LINE_BYTES,
    AutomationError,
    Deferred,
    ErrorCode,
    JsonObject,
    JsonValue,
    Request,
    Response,
    control_pipe_name,
)

logger = logging.getLogger(__name__)

type Handler = Callable[[Request], JsonObject | Deferred[JsonObject]]
type Reply = Callable[[bytes], None]


class ControlServer(QObject):
    """Accept line-delimited JSON requests and answer each one in order.

    Handlers run on the thread that owns this object (the GUI thread), so they
    may read documents and render without locking. A handler may return a
    ``Deferred`` (a capture the user is still choosing); later requests on the
    same connection wait for it, so responses stay in order.
    """

    def __init__(
        self,
        handler: Handler,
        *,
        name: str | None = None,
        enabled: Callable[[], bool] = lambda: True,
    ) -> None:
        super().__init__()
        self._handler = handler
        self._enabled = enabled
        self._name = control_pipe_name() if name is None else name
        self._server: QLocalServer | None = None
        self._buffers: dict[QLocalSocket, bytes] = {}
        self._queues: dict[QLocalSocket, list[bytes]] = {}
        self._waiting: set[QLocalSocket] = set()

    @property
    def name(self) -> str:
        """Return the local socket name."""
        return self._name

    @property
    def listening(self) -> bool:
        """Return whether requests are being accepted."""
        return self._server is not None

    def listen(self) -> bool:
        """Start accepting connections from the current user only."""
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._name):
            logger.warning(
                "Automation unavailable on %s: %s", self._name, server.errorString()
            )
            return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def close(self) -> None:
        """Stop listening and drop open connections."""
        for socket in list(self._buffers):
            self._forget(socket)
            socket.abort()
            socket.deleteLater()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None

    def respond(self, line: bytes) -> bytes:
        """Return the encoded response to one request line answered at once.

        Raises:
            RuntimeError: If the handler deferred its answer.
        """
        replies: list[bytes] = []
        if self.answer(line, replies.append):
            msg = "The request was deferred; use answer()"
            raise RuntimeError(msg)
        return replies[0]

    def answer(self, line: bytes, reply: Reply) -> bool:
        """Pass the encoded response to one request line to ``reply``.

        Returns:
            True if the answer comes later, from a ``Deferred``.
        """
        ident: JsonValue = None
        try:
            request = Request.decode(line)
            ident = request.ident
            self._require_enabled()
            result = self._handler(request)
        except AutomationError as err:
            reply(Response(ident, error=err).encode())
            return False
        except Exception as err:
            logger.exception("Automation request failed")
            failure = AutomationError(ErrorCode.FAILED, f"Internal error: {err}")
            reply(Response(ident, error=failure).encode())
            return False
        if not isinstance(result, Deferred):
            reply(Response(ident, result=result).encode())
            return False

        def settled(value: JsonObject | None, error: AutomationError | None) -> None:
            reply(Response(ident, result=value, error=error).encode())

        result.then(settled)
        return not result.done

    def _require_enabled(self) -> None:
        """Refuse requests while automation is turned off.

        Raises:
            AutomationError: If automation is turned off.
        """
        if not self._enabled():
            msg = "Automation is turned off in VerdiClip Settings"
            raise AutomationError(ErrorCode.DISABLED, msg)

    def _on_connection(self) -> None:
        """Track each new client until it disconnects."""
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            # Windows pipe callbacks can still be queued after a disconnect, so
            # the client must outlive the server (see SingleInstance._read).
            socket.setParent(QCoreApplication.instance())
            self._buffers[socket] = b""
            self._queues[socket] = []
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._disconnected(s))
            # Bytes that arrived before the signals were connected
            self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        """Answer every complete line received so far."""
        if socket not in self._buffers:
            return
        data = self._buffers[socket] + bytes(socket.readAll().data())
        *lines, rest = data.split(b"\n")
        if any(len(line) > MAX_LINE_BYTES for line in (*lines, rest)):
            failure = AutomationError(ErrorCode.BAD_REQUEST, "Request is too large")
            socket.write(Response(None, error=failure).encode())
            socket.flush()
            self._forget(socket)
            socket.disconnectFromServer()
            return
        self._buffers[socket] = rest
        self._queues[socket].extend(line for line in lines if line.strip())
        self._drain(socket)

    def _drain(self, socket: QLocalSocket) -> None:
        """Answer queued lines in order, pausing while one is deferred."""
        queue = self._queues.get(socket, [])
        while queue and socket not in self._waiting:
            line = queue.pop(0)
            if self.answer(line, lambda data, s=socket: self._deliver(s, data)):
                self._waiting.add(socket)
        socket.flush()

    def _deliver(self, socket: QLocalSocket, data: bytes) -> None:
        """Write one response, then carry on with requests queued behind it."""
        if socket not in self._buffers:
            return  # The client hung up while it waited
        socket.write(data)
        if socket in self._waiting:
            self._waiting.discard(socket)
            self._drain(socket)

    def _disconnected(self, socket: QLocalSocket) -> None:
        """Release a client once it hangs up, including ones already refused."""
        self._forget(socket)
        socket.deleteLater()

    def _forget(self, socket: QLocalSocket) -> None:
        """Stop tracking ``socket``."""
        self._buffers.pop(socket, None)
        self._queues.pop(socket, None)
        self._waiting.discard(socket)
