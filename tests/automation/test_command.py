"""``verdiclip ctl`` prints JSON and exits with the documented codes."""

from __future__ import annotations

import io
import json
from typing import override

import pytest

from verdiclip.automation import command as command_module
from verdiclip.automation.client import DEFAULT_TIMEOUT_S, ControlClient
from verdiclip.automation.command import run_ctl
from verdiclip.automation.protocol import (
    AutomationError,
    ErrorCode,
    ExitCode,
    JsonObject,
)


class FakeClient(ControlClient):
    """Return a canned result or raise a canned error."""

    def __init__(self, outcome: JsonObject | AutomationError) -> None:
        super().__init__("unused")
        self.outcome = outcome
        self.calls: list[tuple[str, JsonObject | None]] = []

    @override
    def call(self, method: str, params: JsonObject | None = None) -> JsonObject:
        """Record the call and answer."""
        self.calls.append((method, params))
        if isinstance(self.outcome, AutomationError):
            raise self.outcome
        return self.outcome


def test_success_prints_result() -> None:
    """The result is printed as JSON and the exit code is 0."""
    client = FakeClient({"documents": [], "title": "Café"})
    out, err = io.StringIO(), io.StringIO()

    code = run_ctl("list", {"doc": "a"}, out, err, client=client)

    assert code == ExitCode.OK
    assert json.loads(out.getvalue()) == {"documents": [], "title": "Café"}
    assert err.getvalue() == ""
    assert client.calls == [("list", {"doc": "a"})]


def test_failure_prints_error_and_exit_code() -> None:
    """A failure prints the error object, a one-line reason, and its code."""
    client = FakeClient(AutomationError(ErrorCode.NOT_RUNNING, "VerdiClip is off"))
    out, err = io.StringIO(), io.StringIO()

    code = run_ctl("status", {}, out, err, client=client)

    assert code == ExitCode.UNAVAILABLE
    assert json.loads(out.getvalue()) == {
        "ok": False,
        "error": {"code": "not_running", "message": "VerdiClip is off"},
    }
    assert err.getvalue() == "verdiclip ctl status: VerdiClip is off\n"


def test_default_client_reports_missing_app() -> None:
    """Without a running app, the real client exits with code 3."""
    out, err = io.StringIO(), io.StringIO()
    absent = ControlClient("verdiclip-absent-for-command-test", timeout=1)

    assert run_ctl("status", {}, out, err, client=absent) == ExitCode.UNAVAILABLE


def test_output_survives_a_narrow_redirect_encoding() -> None:
    """Non-ASCII titles and messages never break a cp1252 pipe."""
    out_raw, err_raw = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(out_raw, encoding="cp1252", newline="\n")
    err = io.TextIOWrapper(err_raw, encoding="cp1252", newline="\n")

    ok = run_ctl("list", {}, out, err, client=FakeClient({"title": "スクショ"}))
    failed = run_ctl(
        "get",
        {},
        out,
        err,
        client=FakeClient(AutomationError(ErrorCode.NOT_FOUND, "no 画像")),
    )
    out.flush()
    err.flush()

    first, second = out_raw.getvalue().decode("cp1252").split("\n}\n", 1)
    assert (ok, failed) == (ExitCode.OK, ExitCode.NOT_FOUND)
    assert json.loads(first + "}") == {"title": "スクショ"}
    assert json.loads(second)["error"]["message"] == "no 画像"
    assert err_raw.getvalue().decode("cp1252") == (
        "verdiclip ctl get: no \\u753b\\u50cf\n"
    )


def test_delay_and_timeout_stay_on_this_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """The delay sleeps first and the timeout sets the client; neither is sent."""
    built: list[float] = []
    client = FakeClient({"id": "d1"})

    def make_client(*, timeout: float) -> FakeClient:
        built.append(timeout)
        return client

    monkeypatch.setattr(command_module, "ControlClient", make_client)
    slept: list[float] = []
    out, err = io.StringIO(), io.StringIO()

    code = run_ctl(
        "capture",
        {"mode": "universal", "delay": 1.5, "timeout": 300.0},
        out,
        err,
        sleep=slept.append,
    )

    assert code == ExitCode.OK
    assert slept == [1.5]
    assert built == [300.0]
    assert client.calls == [("capture", {"mode": "universal"})]


def test_no_delay_means_no_sleep_and_the_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without delay nothing sleeps; without timeout the client default applies."""
    built: list[float] = []

    def make_client(*, timeout: float) -> FakeClient:
        built.append(timeout)
        return FakeClient({})

    monkeypatch.setattr(command_module, "ControlClient", make_client)
    slept: list[float] = []

    run_ctl(
        "list",
        {"delay": True, "timeout": "x"},
        io.StringIO(),
        io.StringIO(),
        sleep=slept.append,
    )

    assert slept == []
    assert built == [DEFAULT_TIMEOUT_S]
