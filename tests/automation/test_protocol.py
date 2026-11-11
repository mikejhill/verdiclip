"""The line protocol: request and response round trips and their failures."""

from __future__ import annotations

import pytest

from verdiclip.automation.protocol import (
    PROTOCOL_VERSION,
    AutomationError,
    ErrorCode,
    ExitCode,
    Request,
    Response,
    control_pipe_name,
)


def test_pipe_name_is_per_user() -> None:
    """Each Windows user gets their own control pipe."""
    assert control_pipe_name("ada") == "VerdiClip-ada-control"
    assert control_pipe_name().startswith("VerdiClip-")
    assert control_pipe_name().endswith("-control")


def test_request_round_trip() -> None:
    """A request survives encoding as exactly one line."""
    request = Request("render", {"doc": "ab", "grid": 50}, ident=7)

    line = request.encode()

    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert Request.decode(line.rstrip(b"\n")) == request


@pytest.mark.parametrize(
    ("line", "code", "match"),
    [
        (b"{not json", ErrorCode.BAD_REQUEST, "Malformed request"),
        (b"\xff\xfe", ErrorCode.BAD_REQUEST, "Malformed request"),
        (b"[1, 2]", ErrorCode.BAD_REQUEST, "must be a JSON object"),
        (b'{"method": "list"}', ErrorCode.UNSUPPORTED_VERSION, "None"),
        (b'{"v": 2, "method": "list"}', ErrorCode.UNSUPPORTED_VERSION, "2"),
        (b'{"v": 1}', ErrorCode.BAD_REQUEST, "method"),
        (b'{"v": 1, "method": ""}', ErrorCode.BAD_REQUEST, "method"),
        (b'{"v": 1, "method": 3}', ErrorCode.BAD_REQUEST, "method"),
        (b'{"v": 1, "method": "get", "params": [1]}', ErrorCode.BAD_REQUEST, "params"),
    ],
)
def test_bad_requests_are_rejected(line: bytes, code: ErrorCode, match: str) -> None:
    """Every malformed request gets a stable code and a reason."""
    with pytest.raises(AutomationError, match=match) as caught:
        Request.decode(line)

    assert caught.value.code is code


def test_missing_or_null_params_mean_none() -> None:
    """Params are optional."""
    assert Request.decode(b'{"v": 1, "method": "list"}').params == {}
    assert Request.decode(b'{"v": 1, "method": "list", "params": null}').params == {}


def test_success_response_round_trip() -> None:
    """A result comes back with its request id."""
    line = Response(3, result={"documents": []}).encode()

    decoded = Response.decode(line.rstrip(b"\n"))

    assert decoded.ok
    assert decoded.ident == 3
    assert decoded.result == {"documents": []}
    assert Response.decode(Response(4).encode()).result == {}


def test_error_response_round_trip() -> None:
    """A failure keeps its code and message."""
    error = AutomationError(ErrorCode.NOT_FOUND, "No such document")

    decoded = Response.decode(Response(9, error=error).encode())

    assert not decoded.ok
    assert decoded.error is not None
    assert decoded.error.code is ErrorCode.NOT_FOUND
    assert decoded.error.message == "No such document"
    assert decoded.ident == 9


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (b'{"ok": true, "result": 5}', "result must be an object"),
        (b'{"ok": false}', "no error object"),
    ],
)
def test_malformed_responses_fail(line: bytes, message: str) -> None:
    """A response the client can't interpret is a failure, not a crash."""
    with pytest.raises(AutomationError, match=message) as caught:
        Response.decode(line)

    assert caught.value.code is ErrorCode.FAILED


def test_unknown_error_code_from_newer_app_is_failed() -> None:
    """A code this client doesn't know still reports as a failure."""
    decoded = Response.decode(b'{"ok": false, "error": {"code": "new"}}')

    assert decoded.error is not None
    assert decoded.error.code is ErrorCode.FAILED
    assert decoded.error.message == "failed"


def test_requests_carry_the_protocol_version() -> None:
    """The version field lets the app reject clients it can't serve."""
    assert f'"v":{PROTOCOL_VERSION}'.encode() in Request("list", {}).encode()


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.NOT_RUNNING, ExitCode.UNAVAILABLE),
        (ErrorCode.DISABLED, ExitCode.UNAVAILABLE),
        (ErrorCode.UNSUPPORTED_VERSION, ExitCode.UNAVAILABLE),
        (ErrorCode.BAD_REQUEST, ExitCode.USAGE),
        (ErrorCode.NOT_FOUND, ExitCode.NOT_FOUND),
        (ErrorCode.CONFLICT, ExitCode.CONFLICT),
        (ErrorCode.BUSY, ExitCode.BUSY),
        (ErrorCode.FAILED, ExitCode.FAILED),
    ],
)
def test_exit_codes(code: ErrorCode, status: ExitCode) -> None:
    """Scripts branch on the documented exit codes."""
    assert ExitCode.for_error(code) is status
    assert int(ExitCode.OK) == 0
