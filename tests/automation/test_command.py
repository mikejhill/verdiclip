"""``verdiclip ctl`` prints JSON and exits with the documented codes."""

from __future__ import annotations

import io
import json
from typing import override

from verdiclip.automation.client import ControlClient
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
    assert "Café" in out.getvalue()
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
