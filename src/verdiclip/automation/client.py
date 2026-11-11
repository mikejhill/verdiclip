"""Talk to the running app's control server without importing Qt."""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Final, Protocol

from verdiclip.automation.protocol import (
    AutomationError,
    ErrorCode,
    JsonObject,
    Request,
    Response,
    control_pipe_name,
)

DEFAULT_TIMEOUT_S: Final = 30.0
PIPE_BUSY_RETRY_S: Final = 0.05
CONNECT_RETRY_S: Final = 5.0
WINDOWS_PIPE_PREFIX: Final = "\\\\.\\pipe\\"


class Stream(Protocol):
    """A connected byte stream to the control server."""

    def read(self, size: int = -1, /) -> object:
        """Read up to ``size`` bytes; empty bytes at end of stream."""

    def write(self, data: bytes, /) -> object:
        """Write ``data``."""

    def flush(self) -> None:
        """Push buffered bytes to the server."""

    def close(self) -> None:
        """Hang up."""


type Connector = Callable[[str, float], Stream]


def connect_windows_pipe(name: str, timeout: float) -> Stream:
    """Open the named pipe Qt's ``QLocalServer`` creates on Windows.

    Raises:
        AutomationError: If no server is listening before ``timeout``.
    """
    path = Path(WINDOWS_PIPE_PREFIX + name)
    deadline = time.monotonic() + min(timeout, CONNECT_RETRY_S)
    while True:
        try:
            return path.open("r+b", buffering=0)
        except FileNotFoundError as err:
            raise _not_running() from err
        except OSError as err:
            # Every pipe instance is busy serving another client: retry
            if time.monotonic() >= deadline:
                raise _not_running() from err
            time.sleep(PIPE_BUSY_RETRY_S)


def connect_unix_socket(name: str, timeout: float) -> Stream:  # pragma: no cover
    """Open the Unix socket Qt's ``QLocalServer`` creates off Windows (POSIX).

    Raises:
        AutomationError: If no server is listening.
    """
    if sys.platform == "win32":
        raise _not_running()
    path = Path(name)
    if not path.is_absolute():
        path = Path(tempfile.gettempdir()) / name
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
    except OSError as err:
        client.close()
        raise _not_running() from err
    return client.makefile("rwb", buffering=0)


def _not_running() -> AutomationError:
    """Return the error for a missing server."""
    msg = "VerdiClip is not running; start it and try again"
    return AutomationError(ErrorCode.NOT_RUNNING, msg)


def default_connector() -> Connector:
    """Return the transport for this platform."""
    return connect_windows_pipe if sys.platform == "win32" else connect_unix_socket


class ControlClient:
    """Send requests to the control server and wait for their responses."""

    def __init__(
        self,
        name: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        connector: Connector | None = None,
    ) -> None:
        self._name = control_pipe_name() if name is None else name
        self._timeout = timeout
        self._connect = connector or default_connector()

    def call(self, method: str, params: JsonObject | None = None) -> JsonObject:
        """Run one request and return its result.

        Raises:
            AutomationError: If the app is unreachable or the request failed.
        """
        request = Request(method, params or {}, ident=1)
        outcome: list[Response | Exception] = []
        worker = threading.Thread(
            target=self._exchange, args=(request, outcome), daemon=True
        )
        worker.start()
        worker.join(self._timeout)
        if not outcome:
            msg = f"VerdiClip did not answer within {self._timeout:g} s"
            raise AutomationError(ErrorCode.BUSY, msg)
        result = outcome[0]
        if isinstance(result, Exception):
            raise result
        if result.error is not None:
            raise result.error
        return result.result or {}

    def _exchange(self, request: Request, outcome: list[Response | Exception]) -> None:
        """Write ``request`` and read one response line (on a worker thread)."""
        try:
            stream = self._connect(self._name, self._timeout)
            with closing(stream):
                stream.write(request.encode())
                stream.flush()
                outcome.append(Response.decode(self._read_line(stream)))
        except Exception as err:  # noqa: BLE001 - re-raised on the caller
            outcome.append(err)

    @staticmethod
    def _read_line(stream: Stream) -> bytes:
        """Read up to and excluding the first newline."""
        data = b""
        while b"\n" not in data:
            chunk = stream.read(65536)
            if not isinstance(chunk, bytes) or not chunk:
                msg = "VerdiClip closed the connection without answering"
                raise AutomationError(ErrorCode.FAILED, msg)
            data += chunk
        return data.split(b"\n", 1)[0]
