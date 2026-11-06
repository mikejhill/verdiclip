"""Portable guards for the read-only cold comparison, never native qualification."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from pytestqt.qtbot import QtBot
from tests.capture import root_comparison
from tests.capture.production_matrix import StrictFixtureSnapshot
from tests.capture.root_comparison import RootComparison, decode_result

from verdiclip.capture.scroll.uia_diagnostics import WorkerDiagnostics
from verdiclip.capture.scroll.uia_messages import UiaAction, UiaRequest
from verdiclip.capture.scroll.uia_process import UiaProcess


def valid_result(client: str = "native") -> dict[str, object]:
    """Only fixed labels, finite scalars and verified guard booleans are allowed."""
    return {
        "client": client,
        "apartment": "MTA",
        "identity_verified": True,
        "foreground_unchanged": True,
        "native_only_verified": client == "native",
        "release_confirmed": True,
        "release_kind": "native_rcw_release_completed"
        if client == "native"
        else "managed_reference_dropped",
        "dpi_restored": True,
        "durations_us": {
            "construct": None,
            "root": 170398,
            "validate": 0,
            "release": 1,
        },
        "runtime": {
            "powershell": "5.1.26100.1",
            "clr": "4.0.30319.42000",
            "architecture": "x64",
        },
    }


@pytest.mark.parametrize("client", ["managed", "native"])
def test_exact_completed_result_is_safe(client: str) -> None:
    """A schema-complete result retains nullable timing without inferring causes."""
    result = valid_result(client)
    decoded = decode_result(result, client)
    assert decoded == result


@pytest.mark.parametrize(
    "key",
    ["identity_verified", "foreground_unchanged", "release_confirmed", "dpi_restored"],
)
@pytest.mark.parametrize("value", [False, None, 1, "True"])
def test_unconfirmed_guards_are_never_success(key: str, value: object) -> None:
    """No truthy coercion or omitted release/restore condition can pass."""
    result = valid_result()
    result[key] = value
    with pytest.raises(ValueError, match="precondition or result failed"):
        decode_result(result, "native")


@pytest.mark.parametrize(
    "value", [True, -1, 1000000000, 1.5, "123", float("inf"), float("nan")]
)
def test_invalid_timing_cannot_enter_receipt(value: object) -> None:
    """The diagnostic numeric bound does not authorize a larger operation budget."""
    result = valid_result()
    cast(dict[str, object], result["durations_us"])["root"] = value
    with pytest.raises(ValueError, match="precondition or result failed"):
        decode_result(result, "native")


@pytest.mark.parametrize("location", ["top", "timings", "runtime"])
def test_extra_provider_content_is_rejected(location: str) -> None:
    """Reject entire unexpected objects, never sanitize by copying unknown fields."""
    result = valid_result()
    target = (
        result
        if location == "top"
        else cast(
            dict[str, object],
            result["durations_us" if location == "timings" else "runtime"],
        )
    )
    target["private-title"] = "private provider text"
    with pytest.raises(ValueError, match="precondition or result failed"):
        decode_result(result, "native")


@pytest.mark.parametrize(
    "value", ["private-path", "5.1\nsecret", "5.1" + "9" * 40, "\u0665.\u0661"]
)
def test_runtime_metadata_is_bounded_numeric_only(value: str) -> None:
    """Runtime provenance cannot become an arbitrary-string disclosure channel."""
    result = valid_result()
    cast(dict[str, object], result["runtime"])["powershell"] = value
    with pytest.raises(ValueError, match="precondition or result failed"):
        decode_result(result, "native")


def test_callback_does_not_retain_rejected_payload_or_error_text() -> None:
    """A bad reply produces missing result, not an uncaught Qt callback exception."""
    case = object.__new__(RootComparison)
    case.client, case.done, case.result = "native", False, None
    request = UiaRequest("a" * 32, UiaAction.INSPECT, b"{}")
    reply = deepcopy(valid_result())
    reply["unexpected"] = "private value"
    case.completed(request, {"result": reply})
    assert case.done
    assert case.result is None
    case.failed(request, "private provider error")
    assert case.result is None


def test_workflow_is_one_isolated_four_cell_comparison() -> None:
    """No shared VM, fallback, warmup, expanded permissions or broad trigger."""
    source = Path(".github/workflows/uia-root-comparison.yml").read_text()
    for text in (
        "contents: read",
        "persist-credentials: false",
        "runs-on: windows-2025",
        "github.head_ref == 'diagnose/uia-root-comparison'",
        "github.event.pull_request.draft == true",
        "github.event.pull_request.head.repo.full_name == 'mikejhill/verdiclip'",
        "provider: [winforms, wpf]",
        "client: [managed, native]",
        "fail-fast: false",
        "enable-cache: false",
        "ref: ${{ github.event.pull_request.head.sha }}",
        "outcome='missing'",
        "if: always()",
    ):
        assert text in source
    assert "workflow_dispatch" not in source
    assert "pull_request_target" not in source
    assert "contents: write" not in source
    assert source.count("./scripts/test_capture_broker_native.ps1") == 1
    assert "stdout.log" not in source
    assert "fixture-stderr.log" not in source


def test_wrapper_selector_preserves_original_lane_and_budgets() -> None:
    """A read-only selector can never select cursor/input preparation or drivers."""
    source = Path("scripts/test_capture_broker_native.ps1").read_text()
    assert "$RootProbeClient -and -not $ScrollbarOnly" in source
    assert "$env:VERDICLIP_ROOT_PROBE -cne '1'" in source
    assert "$env:VERDICLIP_ROOT_CLIENT -cne $RootProbeClient" in source
    assert (
        "if ($ScrollbarOnly) { $selector = 'tests/capture/test_broker_native.py"
        "::test_native_scrollbar_selection_preserves_popup' }" in source
    )
    assert (
        "if ($RootProbeClient) { $selector = 'tests/capture/"
        "test_root_comparison_native.py"
        "::test_one_cold_root_lookup' }" in source
    )
    assert "$limit = 75; $caseLimit = 65" in source
    assert "90 - $overall.Elapsed.TotalSeconds" in source
    harness = Path("tests/capture/root_comparison.py").read_text()
    assert "timeout_ms=2000" in harness
    assert "(20 - (time.monotonic()" in harness
    assert "UiaProcess.STARTUP_MS + UiaProcess.DEADLINE_MS" in harness
    assert (
        harness.count("self.process.start(UiaAction.INSPECT") == 0
    )  # Formatter splits arguments.
    assert harness.count("UiaAction.INSPECT,") == 1
    for forbidden in (
        "HostedFixtureClick(",
        "SendInput(",
        "prepare_cursor",
        "RealDesktop(",
        "SetScrollPercent(",
    ):
        assert forbidden not in harness


@pytest.mark.parametrize("fault", ["shutdown", "diagnostics", "dispose"])
def test_helper_fault_cannot_skip_fixture_retirement_or_hide_primary_failure(
    fault: str, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every independent cleanup executes and no raw exception enters receipts."""
    calls: list[str] = []

    class Helper:
        worker_pid = None

        def shutdown(self, *, timeout_ms: int) -> None:
            assert timeout_ms == 2000
            calls.append("shutdown")
            if fault == "shutdown":
                raise RuntimeError("private helper details")

        @property
        def diagnostics(self) -> WorkerDiagnostics:
            if fault == "diagnostics":
                raise RuntimeError("private diagnostic details")
            return WorkerDiagnostics(
                phase="idle",
                process_state="NotRunning",
                cleanup="confirmed",
                request_sent=True,
                events=(),
                dropped_events=0,
                stdout_bytes=0,
                stderr_bytes=0,
                pipe_bytes=0,
                max_deadline_lateness_ms=0,
            )

        def deleteLater(self) -> None:  # noqa: N802 - exact Qt API stand-in
            calls.append("dispose")
            if fault == "dispose":
                raise RuntimeError("private disposal details")

    case = RootComparison(qtbot, tmp_path, "native", "winforms")
    case.stage = "helper"
    case.process = cast(UiaProcess, Helper())
    monkeypatch.setattr(case.peer, "close", lambda: calls.append("fixture"))
    case.close()
    assert calls == ["shutdown", "dispose", "fixture"]
    receipt = json.loads((tmp_path / "root-comparison.json").read_text())
    assert receipt["failure_stage"] == "helper"
    assert receipt["retirement_failed"] is True
    assert receipt["fixture_retired"] is True
    assert "private" not in repr(receipt)
    assert receipt["outcome"] == "failed"
    qtbot.wait(1)


def test_absent_popup_does_not_relabel_fixture_readiness_failure(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readiness failure does not fabricate a later popup/retirement failure."""
    case = RootComparison(qtbot, tmp_path, "managed", "wpf")
    case.stage = "fixture"
    monkeypatch.setattr(case.peer, "close", lambda: None)
    case.close()
    assert case.receipt["failure_stage"] == "fixture"
    assert case.receipt["retirement_failed"] is False
    assert case.receipt["popup_preserved"] is False
    assert case.receipt["fixture_started"] is False
    qtbot.wait(1)


@pytest.fixture
def hosted_probe_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build only inert environment/session metadata; never query Windows APIs."""
    values = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Windows",
        "RUNNER_ARCH": "X64",
        "GITHUB_REPOSITORY": "mikejhill/verdiclip",
        "VERDICLIP_HOSTED_SCROLLBAR": "1",
        "VERDICLIP_QUALIFICATION_HEAD": "a" * 40,
        "VERDICLIP_HOSTED_SOURCE": "a" * 40,
        "VERDICLIP_QUALIFICATION_DIRTY": "false",
        "VERDICLIP_ROOT_PROBE": "1",
        "GITHUB_HEAD_REF": "diagnose/uia-root-comparison",
        "VERDICLIP_ROOT_CLIENT": "managed",
        "VERDICLIP_NATIVE_PROVIDER": "winforms",
        "QT_SCALE_FACTOR": "1",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(root_comparison, "qVersion", lambda: "6.12.0")
    monkeypatch.setattr(
        root_comparison.DesktopSession,
        "current",
        lambda: root_comparison.DesktopSession(0, 1),
    )


@pytest.mark.usefixtures("hosted_probe_environment")
def test_valid_hosted_gate_returns_only_reviewed_pair() -> None:
    """The read-only selector is admitted only after all existing gates."""
    pair = root_comparison.require_probe_environment()
    assert pair == ("managed", "winforms")


@pytest.mark.usefixtures("hosted_probe_environment")
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_HEAD_REF", "main"),
        ("VERDICLIP_QUALIFICATION_DIRTY", "true"),
        ("VERDICLIP_ROOT_PROBE", "0"),
        ("VERDICLIP_ROOT_CLIENT", "fallback"),
        ("VERDICLIP_NATIVE_PROVIDER", "foreign"),
        ("QT_SCALE_FACTOR", "1.5"),
        ("VERDICLIP_HOSTED_SOURCE", "b" * 40),
    ],
)
def test_root_selector_cannot_widen_hosted_source_or_profile_gates(
    key: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejected environments cannot start a fixture, helper or input path."""
    monkeypatch.setenv(key, value)
    with pytest.raises(
        (ValueError, RuntimeError),
        match=r"Root comparison|Hosted fixture|Native fixture",
    ):
        root_comparison.require_probe_environment()


@pytest.mark.parametrize(
    ("fault", "has_snapshot"),
    [
        ("observation", True),
        ("filter", True),
        ("menu_close", True),
        ("menu_dispose", True),
        ("menu_close", False),
    ],
)
def test_popup_cleanup_attempts_every_independent_action(
    fault: str,
    *,
    has_snapshot: bool,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An observation or Qt teardown error cannot hide primary failure or leak peers."""
    calls: list[str] = []

    class Snapshot:
        def errors(self) -> list[str]:
            calls.append("observation")
            if fault == "observation":
                raise RuntimeError("private observation details")
            return []

        def close(self) -> None:
            calls.append("filter")
            if fault == "filter":
                raise RuntimeError("private filter details")

    case = RootComparison(qtbot, tmp_path, "managed", "wpf")
    case.stage = "fixture"
    if has_snapshot:
        case.snapshot = cast(StrictFixtureSnapshot, Snapshot())
    original_close, original_dispose = case.menu.close, case.menu.deleteLater

    def close_menu() -> None:
        calls.append("menu_close")
        original_close()
        if fault == "menu_close":
            raise RuntimeError("private close details")

    def dispose_menu() -> None:
        calls.append("menu_dispose")
        original_dispose()
        if fault == "menu_dispose":
            raise RuntimeError("private dispose details")

    monkeypatch.setattr(case.menu, "close", close_menu)
    monkeypatch.setattr(case.menu, "deleteLater", dispose_menu)
    monkeypatch.setattr(case.peer, "close", lambda: calls.append("fixture"))
    case.close()
    expected = (["observation", "filter"] if has_snapshot else []) + [
        "menu_close",
        "menu_dispose",
        "fixture",
    ]
    assert calls == expected
    assert case.receipt["failure_stage"] == "fixture"
    assert case.receipt["retirement_failed"] is True
    assert case.receipt["fixture_retired"] is True
    assert "private" not in repr(case.receipt)
    qtbot.wait(1)
