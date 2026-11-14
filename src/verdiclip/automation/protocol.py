"""Wire format shared by the control server and the Qt-free client.

Each message is one UTF-8 JSON object on its own line. A connection may carry
several requests; every request gets exactly one response, in order.
"""

from __future__ import annotations

import getpass
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Final, cast

from verdiclip.document.annotations import JsonObject, JsonValue
from verdiclip.exceptions import AppError

PROTOCOL_VERSION: Final = 1
MAX_LINE_BYTES: Final = 1024 * 1024
DEFAULT_MAX_SIDE: Final = 1568


def control_pipe_name(user: str | None = None) -> str:
    """Return the per-user local socket name of the control server."""
    return f"VerdiClip-{user or getpass.getuser()}-control"


class ErrorCode(StrEnum):
    """Stable failure reasons; the CLI maps each to an exit code."""

    NOT_RUNNING = "not_running"
    DISABLED = "disabled"
    BAD_REQUEST = "bad_request"
    UNSUPPORTED_VERSION = "unsupported_version"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    BUSY = "busy"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ExitCode(IntEnum):
    """Process exit status of ``verdiclip ctl``."""

    OK = 0
    FAILED = 1
    USAGE = 2
    UNAVAILABLE = 3
    CONFLICT = 4
    NOT_FOUND = 5
    BUSY = 6
    CANCELLED = 7

    @classmethod
    def for_error(cls, code: ErrorCode) -> ExitCode:
        """Return the exit status that reports ``code``."""
        return _EXIT_CODES.get(code, cls.FAILED)


_EXIT_CODES: Final = {
    ErrorCode.NOT_RUNNING: ExitCode.UNAVAILABLE,
    ErrorCode.DISABLED: ExitCode.UNAVAILABLE,
    ErrorCode.BAD_REQUEST: ExitCode.USAGE,
    ErrorCode.UNSUPPORTED_VERSION: ExitCode.UNAVAILABLE,
    ErrorCode.NOT_FOUND: ExitCode.NOT_FOUND,
    ErrorCode.CONFLICT: ExitCode.CONFLICT,
    ErrorCode.BUSY: ExitCode.BUSY,
    ErrorCode.CANCELLED: ExitCode.CANCELLED,
}


class AutomationError(AppError):
    """A request that could not be served, with a stable reason."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_json(self) -> JsonObject:
        """Return the ``error`` member of a failure response."""
        return {"code": self.code.value, "message": self.message}


type Settled[T] = Callable[[T | None, AutomationError | None], None]


class Deferred[T]:
    """A result that arrives later, such as a capture the user is still choosing.

    Settles once, with a value or an error; listeners added after that run at once.
    """

    def __init__(self) -> None:
        self._done = False
        self._value: T | None = None
        self._error: AutomationError | None = None
        self._listeners: list[Settled[T]] = []

    @property
    def done(self) -> bool:
        """Return whether a value or an error has arrived."""
        return self._done

    def resolve(self, value: T) -> None:
        """Settle with ``value``; later calls are ignored."""
        self._settle(value, None)

    def reject(self, error: AutomationError) -> None:
        """Settle with ``error``; later calls are ignored."""
        self._settle(None, error)

    def then(self, listener: Settled[T]) -> None:
        """Call ``listener(value, error)`` once this settles."""
        if self._done:
            listener(self._value, self._error)
        else:
            self._listeners.append(listener)

    def map[U](self, convert: Callable[[T], U]) -> Deferred[U]:
        """Return a deferred settling with ``convert(value)``, or the same error.

        An ``AutomationError`` raised by ``convert`` becomes the error.
        """
        mapped: Deferred[U] = Deferred()

        def forward(value: T | None, error: AutomationError | None) -> None:
            if error is not None:
                mapped.reject(error)
                return
            try:
                mapped.resolve(convert(cast(T, value)))
            except AutomationError as err:
                mapped.reject(err)

        self.then(forward)
        return mapped

    def _settle(self, value: T | None, error: AutomationError | None) -> None:
        if self._done:
            return
        self._done, self._value, self._error = True, value, error
        listeners, self._listeners = self._listeners, []
        for listener in listeners:
            listener(value, error)


@dataclass(frozen=True, slots=True)
class Request:
    """One decoded request line."""

    method: str
    params: JsonObject
    ident: JsonValue = None

    def encode(self) -> bytes:
        """Return the request as one newline-terminated line."""
        message: JsonObject = {
            "v": PROTOCOL_VERSION,
            "id": self.ident,
            "method": self.method,
            "params": self.params,
        }
        return _line(message)

    @classmethod
    def decode(cls, line: bytes) -> Request:
        """Parse one request line.

        Raises:
            AutomationError: If the line is not a valid request.
        """
        message = _object(line, "request")
        ident = message.get("id")
        version = message.get("v")
        if version != PROTOCOL_VERSION:
            msg = f"Protocol version {version!r} is not supported; expected 1"
            raise AutomationError(ErrorCode.UNSUPPORTED_VERSION, msg)
        method = message.get("method")
        if not isinstance(method, str) or not method:
            msg = "Request needs a method name"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            msg = "Request params must be an object"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        return cls(method, params, ident)


@dataclass(frozen=True, slots=True)
class Response:
    """One response line: a result or an error."""

    ident: JsonValue
    result: JsonObject | None = None
    error: AutomationError | None = None

    @property
    def ok(self) -> bool:
        """Return whether the request succeeded."""
        return self.error is None

    def encode(self) -> bytes:
        """Return the response as one newline-terminated line."""
        message: JsonObject = {"id": self.ident, "ok": self.ok}
        if self.error is not None:
            message["error"] = self.error.to_json()
        else:
            message["result"] = self.result if self.result is not None else {}
        return _line(message)

    @classmethod
    def decode(cls, line: bytes) -> Response:
        """Parse one response line.

        Raises:
            AutomationError: If the line is not a valid response.
        """
        message = _object(line, "response")
        ident = message.get("id")
        if message.get("ok") is True:
            result = message.get("result", {})
            if not isinstance(result, dict):
                msg = "Response result must be an object"
                raise AutomationError(ErrorCode.FAILED, msg)
            return cls(ident, result=result)
        error = message.get("error")
        if not isinstance(error, dict):
            msg = "Failure response has no error object"
            raise AutomationError(ErrorCode.FAILED, msg)
        try:
            code = ErrorCode(str(error.get("code")))
        except ValueError:
            code = ErrorCode.FAILED
        text = error.get("message")
        return cls(ident, error=AutomationError(code, str(text or code.value)))


def _line(message: JsonObject) -> bytes:
    """Encode ``message`` compactly with a trailing newline."""
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8") + b"\n"


def _object(line: bytes, what: str) -> JsonObject:
    """Decode one line holding a JSON object."""
    try:
        # json.loads only ever produces JSON-compatible values
        decoded = cast(JsonValue, json.loads(line.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        msg = f"Malformed {what}: {err}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg) from err
    if not isinstance(decoded, dict):
        msg = f"A {what} must be a JSON object"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return decoded
