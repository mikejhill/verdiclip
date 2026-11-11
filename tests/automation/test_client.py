"""The Qt-free client: transports, timeouts, and error mapping."""

from __future__ import annotations

import sys
import threading
import uuid
from typing import override

import pytest

from verdiclip.automation import client as client_module
from verdiclip.automation.client import ControlClient, Stream
from verdiclip.automation.protocol import (
    AutomationError,
    ErrorCode,
    Request,
    Response,
)


class FakeStream:
    """Answer with canned bytes, delivered in small chunks."""

    def __init__(self, answer: bytes, *, chunk: int = 4) -> None:
        self.answer = answer
        self.chunk = chunk
        self.written = b""
        self.closed = False

    def read(self, size: int = -1, /) -> object:
        """Return the next chunk of the answer."""
        del size
        data, self.answer = self.answer[: self.chunk], self.answer[self.chunk :]
        return data

    def write(self, data: bytes, /) -> object:
        """Remember what the client sent."""
        self.written += data
        return len(data)

    def flush(self) -> None:
        """Nothing is buffered."""

    def close(self) -> None:
        """Record the hang-up."""
        self.closed = True


def connector_for(stream: Stream) -> client_module.Connector:
    """Return a connector that hands out ``stream``."""
    return lambda _name, _timeout: stream


def test_call_returns_result_and_sends_one_request() -> None:
    """A successful call sends one line and returns the result object."""
    stream = FakeStream(Response(1, result={"documents": []}).encode() + b"tail")
    client = ControlClient("pipe", connector=connector_for(stream))

    result = client.call("list", {"doc": "x"})

    assert result == {"documents": []}
    sent = Request.decode(stream.written.rstrip(b"\n"))
    assert (sent.method, sent.params) == ("list", {"doc": "x"})
    assert stream.closed


def test_call_raises_server_error() -> None:
    """A failure response is raised with its code."""
    error = AutomationError(ErrorCode.NOT_FOUND, "gone")
    stream = FakeStream(Response(1, error=error).encode())
    client = ControlClient("pipe", connector=connector_for(stream))

    with pytest.raises(AutomationError, match="gone") as caught:
        client.call("get")

    assert caught.value.code is ErrorCode.NOT_FOUND


def test_hang_up_without_answer_fails() -> None:
    """A dropped connection is reported, not waited on forever."""
    client = ControlClient("pipe", connector=connector_for(FakeStream(b'{"ok"')))

    with pytest.raises(AutomationError, match="without answering") as caught:
        client.call("status")

    assert caught.value.code is ErrorCode.FAILED


def test_lost_connection_is_a_structured_error() -> None:
    """A pipe broken mid-request is reported as failed, not a traceback."""

    class Broken(FakeStream):
        @override
        def write(self, data: bytes, /) -> object:
            raise BrokenPipeError(32, "Broken pipe")

    client = ControlClient("pipe", connector=connector_for(Broken(b"")))

    with pytest.raises(AutomationError, match="Lost the connection") as caught:
        client.call("list")

    assert caught.value.code is ErrorCode.FAILED


def test_connect_failure_propagates() -> None:
    """A missing app reports not_running."""

    def refuse(_name: str, _timeout: float) -> Stream:
        raise client_module._not_running()

    with pytest.raises(AutomationError, match="not running") as caught:
        ControlClient("pipe", connector=refuse).call("status")

    assert caught.value.code is ErrorCode.NOT_RUNNING


def test_unresponsive_app_times_out_as_busy() -> None:
    """A hung app returns busy after the timeout instead of hanging the CLI."""
    release = threading.Event()

    class Silent(FakeStream):
        @override
        def read(self, size: int = -1, /) -> object:
            release.wait(5)
            return b""

    client = ControlClient("pipe", timeout=0.05, connector=connector_for(Silent(b"")))
    try:
        with pytest.raises(AutomationError, match="did not answer") as caught:
            client.call("status")
    finally:
        release.set()

    assert caught.value.code is ErrorCode.BUSY


def test_default_name_and_connector() -> None:
    """The client targets this user's pipe with the platform transport."""
    expected = (
        client_module.connect_windows_pipe
        if sys.platform == "win32"
        else client_module.connect_unix_socket
    )

    assert client_module.default_connector() is expected


def test_missing_server_is_not_running() -> None:
    """Connecting where nothing listens says the app is not running."""
    name = f"verdiclip-absent-{uuid.uuid4().hex}"

    with pytest.raises(AutomationError) as caught:
        client_module.default_connector()(name, 0.2)

    assert caught.value.code is ErrorCode.NOT_RUNNING


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes")
def test_busy_pipe_retries_until_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pipe whose instances are all busy is retried, then reported."""
    calls: list[str] = []

    def busy(self: object, *_args: object, **_kwargs: object) -> object:
        calls.append(str(self))
        raise OSError(231, "All pipe instances are busy")

    monkeypatch.setattr(client_module.Path, "open", busy)

    with pytest.raises(AutomationError) as caught:
        client_module.connect_windows_pipe("busy", 0.12)

    assert caught.value.code is ErrorCode.NOT_RUNNING
    assert len(calls) >= 2
    assert calls[0] == "\\\\.\\pipe\\busy"
