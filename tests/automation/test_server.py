"""The control server answers line-delimited requests on a real local socket."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from verdiclip.automation.client import ControlClient
from verdiclip.automation.protocol import (
    MAX_LINE_BYTES,
    AutomationError,
    Deferred,
    ErrorCode,
    JsonObject,
    Request,
    Response,
)
from verdiclip.automation.server import ControlServer


def echo(request: Request) -> JsonObject:
    """Answer with the request's method and params."""
    if request.method == "missing":
        raise AutomationError(ErrorCode.NOT_FOUND, "no such thing")
    if request.method == "crash":
        msg = "boom"
        raise RuntimeError(msg)
    return {"method": request.method, "params": request.params}


@pytest.fixture
def server(qtbot: QtBot) -> Iterator[ControlServer]:
    """A listening server under a unique name."""
    del qtbot
    owned = ControlServer(echo, name=f"verdiclip-control-test-{uuid.uuid4().hex}")
    assert owned.listen()
    assert owned.listening
    yield owned
    owned.close()
    assert not owned.listening


def answer(server: ControlServer, line: bytes) -> Response:
    """Return the decoded response to ``line``."""
    return Response.decode(server.respond(line).rstrip(b"\n"))


def test_respond_runs_handler() -> None:
    """A valid request gets the handler's result and its own id back."""
    server = ControlServer(echo, name="unused")

    response = answer(server, Request("list", {"a": 1}, ident=5).encode())

    assert response.ok
    assert response.ident == 5
    assert response.result == {"method": "list", "params": {"a": 1}}
    assert server.name == "unused"


def test_respond_reports_handler_errors() -> None:
    """Expected failures keep their code; unexpected ones become failed."""
    server = ControlServer(echo, name="unused")

    missing = answer(server, Request("missing", {}, ident=1).encode())
    crash = answer(server, Request("crash", {}, ident=2).encode())
    garbage = answer(server, b"not json")

    assert missing.error is not None
    assert missing.error.code is ErrorCode.NOT_FOUND
    assert missing.ident == 1
    assert crash.error is not None
    assert crash.error.code is ErrorCode.FAILED
    assert "boom" in crash.error.message
    assert crash.ident == 2
    assert garbage.error is not None
    assert garbage.error.code is ErrorCode.BAD_REQUEST
    assert garbage.ident is None


def test_disabled_server_refuses() -> None:
    """Turning automation off refuses every request with disabled."""
    enabled = [False]
    server = ControlServer(echo, name="unused", enabled=lambda: enabled[0])

    refused = answer(server, Request("list", {}, ident=1).encode())
    enabled[0] = True
    allowed = answer(server, Request("list", {}, ident=2).encode())

    assert refused.error is not None
    assert refused.error.code is ErrorCode.DISABLED
    assert allowed.ok


_CLIENTS: list[QLocalSocket] = []


@pytest.fixture(autouse=True)
def _retire_clients() -> Iterator[None]:
    """Hand raw clients to deferred deletion instead of Python's collector.

    Windows pipe callbacks can still be queued after a test ends; a client
    destroyed immediately at that point aborts the process during a later
    test's event loop (see ``SingleInstance._read``).
    """
    yield
    while _CLIENTS:
        socket = _CLIENTS.pop()
        if isValid(socket):  # The server may have deleted it already
            socket.abort()
            socket.deleteLater()


def connect(server: ControlServer, qtbot: QtBot) -> QLocalSocket:
    """Return a raw client connected to ``server``, owned by the application."""
    socket = QLocalSocket(QCoreApplication.instance())
    _CLIENTS.append(socket)
    socket.connectToServer(server.name)
    assert socket.waitForConnected(2000)
    qtbot.waitUntil(lambda: len(server._buffers) == 1)
    return socket


def read_lines(socket: QLocalSocket, qtbot: QtBot, count: int) -> list[bytes]:
    """Wait for ``count`` complete lines from the server."""
    data = [b""]

    def received() -> bool:
        data[0] += bytes(socket.readAll().data())
        return data[0].count(b"\n") >= count

    qtbot.waitUntil(received, timeout=3000)
    return data[0].split(b"\n")[:count]


def test_several_requests_on_one_connection_answer_in_order(
    server: ControlServer, qtbot: QtBot
) -> None:
    """Pipelined and split lines are answered once each, in order."""
    socket = connect(server, qtbot)
    first = Request("one", {}, ident=1).encode()
    second = Request("two", {}, ident=2).encode()

    socket.write(first + b"\n" + second[:5])
    socket.flush()
    qtbot.wait(20)
    socket.write(second[5:])
    socket.flush()

    lines = read_lines(socket, qtbot, 2)
    assert [Response.decode(line).ident for line in lines] == [1, 2]
    socket.disconnectFromServer()
    qtbot.waitUntil(lambda: not server._buffers)


def test_oversized_request_is_refused_and_disconnected(
    server: ControlServer, qtbot: QtBot
) -> None:
    """A client can't make the app buffer without bound."""
    socket = connect(server, qtbot)

    socket.write(b"x" * (MAX_LINE_BYTES + 10))
    socket.flush()

    line = read_lines(socket, qtbot, 1)[0]
    error = Response.decode(line).error
    assert error is not None
    assert error.code is ErrorCode.BAD_REQUEST
    qtbot.waitUntil(lambda: not server._buffers)


def test_oversized_complete_line_is_refused(
    server: ControlServer, qtbot: QtBot
) -> None:
    """The size limit holds even when the long line arrives with its newline."""
    socket = connect(server, qtbot)
    accepted = next(iter(server._buffers))
    deleted: list[bool] = []
    accepted.destroyed.connect(lambda: deleted.append(True))
    calls: list[bytes] = []
    original = server.answer
    server.answer = lambda line, reply: calls.append(line) or original(line, reply)

    socket.write(b"x" * (MAX_LINE_BYTES + 10) + b"\n")
    socket.flush()

    line = read_lines(socket, qtbot, 1)[0]
    error = Response.decode(line).error
    assert error is not None
    assert error.code is ErrorCode.BAD_REQUEST
    assert calls == []
    qtbot.waitUntil(lambda: not server._buffers)
    qtbot.waitUntil(lambda: bool(deleted))


def test_close_drops_open_clients(server: ControlServer, qtbot: QtBot) -> None:
    """Shutdown releases connected clients."""
    connect(server, qtbot)

    server.close()
    server.close()

    assert not server._buffers
    assert not server.listening


def test_client_round_trip(server: ControlServer, qtbot: QtBot) -> None:
    """The Qt-free client reaches the server through the real transport."""
    results: list[object] = []

    def call() -> None:
        try:
            results.append(ControlClient(server.name, timeout=5).call("hi", {"n": 1}))
        except AutomationError as err:
            results.append(err)

    worker = threading.Thread(target=call)
    worker.start()
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    worker.join()

    assert results == [{"method": "hi", "params": {"n": 1}}]


def test_listen_failure_is_reported(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name that can't be served leaves automation off without crashing."""
    del qtbot
    monkeypatch.setattr(QLocalServer, "listen", lambda *_args: False)
    server = ControlServer(echo, name=f"verdiclip-refused-{uuid.uuid4().hex}")

    assert not server.listen()
    assert not server.listening


def test_stale_notifications_are_ignored(qtbot: QtBot) -> None:
    """Signals queued for a closed server or a forgotten client do nothing."""
    del qtbot
    server = ControlServer(echo, name="unused")
    stray = QLocalSocket(QCoreApplication.instance())
    _CLIENTS.append(stray)

    server._on_connection()
    server._read(stray)
    server._disconnected(stray)

    assert not server._buffers


class Later:
    """A handler whose ``wait`` requests answer only when the test says so."""

    def __init__(self) -> None:
        self.waiting: list[Deferred[JsonObject]] = []

    def __call__(self, request: Request) -> JsonObject | Deferred[JsonObject]:
        """Defer ``wait`` and ``done`` (already settled); echo the rest."""
        if request.method == "wait":
            deferred: Deferred[JsonObject] = Deferred()
            self.waiting.append(deferred)
            return deferred
        if request.method == "done":
            settled: Deferred[JsonObject] = Deferred()
            settled.resolve({"done": True})
            return settled
        return echo(request)


def test_deferred_answers_hold_later_requests_in_order(qtbot: QtBot) -> None:
    """A deferred answer arrives when it settles, and requests behind it wait."""
    handler = Later()
    server = ControlServer(handler, name=f"verdiclip-control-test-{uuid.uuid4().hex}")
    assert server.listen()
    try:
        socket = connect(server, qtbot)
        socket.write(
            Request("wait", {}, ident=1).encode()
            + Request("after", {}, ident=2).encode()
        )
        socket.flush()
        qtbot.waitUntil(lambda: len(handler.waiting) == 1)
        qtbot.wait(30)

        assert socket.bytesAvailable() == 0
        handler.waiting[0].resolve({"captured": True})
        handled_at_once = len(server._queues[next(iter(server._queues))])
        lines = read_lines(socket, qtbot, 2)
        first, second = (Response.decode(line) for line in lines)
        assert (first.ident, first.result) == (1, {"captured": True})
        assert second.ident == 2
        assert handled_at_once == 1  # The next request waits for the event loop
        socket.disconnectFromServer()
        qtbot.waitUntil(lambda: not server._buffers)
    finally:
        server.close()


def test_deferred_errors_and_settled_deferreds() -> None:
    """A rejected deferred answers with its error; a settled one at once."""
    handler = Later()
    server = ControlServer(handler, name="unused")
    replies: list[bytes] = []

    later = server.answer(Request("wait", {}, ident=3).encode(), replies.append)
    handler.waiting[0].reject(AutomationError(ErrorCode.CANCELLED, "stopped"))
    settled = server.respond(Request("done", {}, ident=4).encode())

    assert later
    failure = Response.decode(replies[0].rstrip(b"\n"))
    assert failure.error is not None
    assert failure.error.code is ErrorCode.CANCELLED
    assert Response.decode(settled.rstrip(b"\n")).result == {"done": True}
    with pytest.raises(RuntimeError, match="deferred"):
        server.respond(Request("wait", {}, ident=5).encode())


def test_answer_after_the_client_left_is_dropped(qtbot: QtBot) -> None:
    """A capture finishing after its client hung up writes nothing."""
    handler = Later()
    server = ControlServer(handler, name=f"verdiclip-control-test-{uuid.uuid4().hex}")
    assert server.listen()
    try:
        socket = connect(server, qtbot)
        socket.write(Request("wait", {}, ident=1).encode())
        socket.flush()
        qtbot.waitUntil(lambda: len(handler.waiting) == 1)
        socket.disconnectFromServer()
        qtbot.waitUntil(lambda: not server._buffers)

        handler.waiting[0].resolve({"late": True})

        assert not server._waiting
    finally:
        server.close()
