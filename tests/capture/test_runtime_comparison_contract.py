"""Portable admission/ownership fault tests and real Windows C# compilation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import cast

import pytest
from pytestqt.qtbot import QtBot
from tests.capture import runtime_comparison
from tests.capture.production_matrix import StrictFixtureSnapshot
from tests.capture.runtime_comparison import RuntimeComparison, decode_result
from tests.capture.test_scroll_metric_policy import run_policy_probe

from verdiclip.capture.scroll.uia_diagnostics import WorkerDiagnostics
from verdiclip.capture.scroll.uia_process import UiaProcess


def valid_result() -> dict[str, object]:
    """Build only fixed safe fields."""
    return {
        "ancestry": {
            "hit_category": "list_item",
            "scroll_available": False,
            "root_reached": True,
            "ancestor_count": 2,
            "walk_outcome": "verified_suffix",
        },
        "root": {"method": "valid", "property": "valid", "equal": True},
        "hit": {"method": "empty", "property": "valid", "equal": None},
        "ownership_verified": True,
        "foreground_unchanged": True,
        "native_only_verified": True,
        "release_confirmed": True,
        "dpi_restored": True,
    }


@pytest.mark.parametrize("shape", ["null", "empty", "oversized", "wrong_type", "valid"])
def test_fixed_shape_receipt(shape: str) -> None:
    """Preserve shape evidence without inferring valid identity."""
    result = valid_result()
    result["hit"] = {
        "method": shape,
        "property": "valid",
        "equal": False if shape == "valid" else None,
    }
    if shape in {"oversized", "wrong_type"}:
        with pytest.raises(ValueError, match="precondition or result failed"):
            decode_result(result)
        return
    if shape == "valid":
        result["ancestry"] = {
            "hit_category": "other",
            "scroll_available": False,
            "root_reached": False,
            "ancestor_count": 0,
            "walk_outcome": "identified_hit",
        }
    assert decode_result(result) == result


@pytest.mark.parametrize(
    "field",
    [
        "ownership_verified",
        "foreground_unchanged",
        "native_only_verified",
        "release_confirmed",
        "dpi_restored",
    ],
)
@pytest.mark.parametrize("value", [1, False, None, "true"])
def test_guard_flags_fail_closed(field: str, value: object) -> None:
    """Reject coerced or unconfirmed cleanup flags."""
    result = valid_result()
    result[field] = value
    with pytest.raises(ValueError, match="precondition or result failed"):
        decode_result(result)


@pytest.mark.parametrize(
    "pair",
    [
        None,
        [],
        {"method": "private"},
        {"method": "valid", "property": "valid", "equal": 1},
        {"method": "empty", "property": "valid", "equal": False},
        {"method": "valid", "property": "valid", "equal": None},
        {"method": [1], "property": "valid", "equal": None},
    ],
)
def test_bad_pairs_cannot_export_provider_data(pair: object) -> None:
    """Reject arbitrary provider data and inconsistent equality."""
    result = valid_result()
    result["hit"] = pair
    with pytest.raises(ValueError, match="precondition or result failed"):
        decode_result(result)


def test_source_and_workflow_isolation() -> None:
    """Restrict execution to a distinct owned diagnostic lane."""
    workflow = Path(".github/workflows/uia-ancestry.yml").read_text()
    assert "github.head_ref == 'test/native-identityless-ancestry'" in workflow
    assert "github.event.pull_request.draft == true" in workflow
    assert "provider: [winforms, wpf]" in workflow
    assert "contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "pull_request_target" not in workflow
    source = Path("tests/capture/uia_runtime_probe.cs").read_text()
    for forbidden in (
        "DiscoveryResolver",
        "DiscoverySession",
        "SetFocus(",
        "SendInput(",
        "CopyFromScreen(",
        "SetScrollPercent(",
    ):
        assert forbidden not in source
    assert source.count("RuntimeComparisonPolicy.Compare(") == 2
    assert "new NativeDiscoveryBackend(false)" in source
    runner = Path("scripts/test_runtime_comparison.ps1").read_text()
    assert "if (-not $RuntimeProbe -or -not $ScrollbarOnly" in runner
    assert "$limit = 75; $caseLimit = 65" in runner
    assert (
        "test_runtime_comparison_native.py::test_runtime_identity_comparison" in runner
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Actual installed Windows compiler")
def test_actual_runtime_policy_compiles_and_injected_faults(tmp_path: Path) -> None:
    """Compile actual native ABI and execute pure failure policies."""
    source = Path("src/verdiclip/capture/scroll").resolve()
    tests = Path("tests/capture").resolve()
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "Add-Type -AssemblyName System.Drawing\nAdd-Type -Path @("
        + ",".join(
            "'" + str(path) + "'"
            for path in [
                source / "uia_nofocus_interop.cs",
                source / "uia_desktop.cs",
                source / "uia_discovery.cs",
                tests / "uia_runtime_probe.cs",
                tests / "uia_ancestry_policy.cs",
                tests / "uia_ancestry_fake.cs",
                tests / "uia_runtime_probe_fake.cs",
            ]
        )
        + ")\n[RuntimeProbeTests]::Run() + [AncestryTests]::Run()\n",
        encoding="utf-8",
    )
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [str(host), "-NoProfile", "-NonInteractive", "-Mta", "-File", str(script)]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert int(result.stdout.strip()) >= 150


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

    case = RuntimeComparison(qtbot, tmp_path, "winforms")
    case.stage = "helper"
    case.process = cast(UiaProcess, Helper())
    monkeypatch.setattr(case.peer, "close", lambda: calls.append("fixture"))
    case.close()
    assert calls == ["shutdown", "dispose", "fixture"]
    receipt = json.loads((tmp_path / "runtime-comparison.json").read_text())
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
    case = RuntimeComparison(qtbot, tmp_path, "wpf")
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
        "VERDICLIP_RUNTIME_PROBE": "1",
        "GITHUB_HEAD_REF": "test/native-identityless-ancestry",
        "VERDICLIP_NATIVE_PROVIDER": "winforms",
        "QT_SCALE_FACTOR": "1",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(runtime_comparison, "qVersion", lambda: "6.12.0")
    monkeypatch.setattr(
        runtime_comparison.DesktopSession,
        "current",
        lambda: runtime_comparison.DesktopSession(0, 1),
    )


@pytest.mark.usefixtures("hosted_probe_environment")
def test_valid_hosted_gate_returns_only_reviewed_pair() -> None:
    """The read-only selector is admitted only after all existing gates."""
    pair = runtime_comparison.require_probe_environment()
    assert pair == "winforms"


@pytest.mark.usefixtures("hosted_probe_environment")
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_HEAD_REF", "main"),
        ("VERDICLIP_QUALIFICATION_DIRTY", "true"),
        ("VERDICLIP_RUNTIME_PROBE", "0"),
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
        runtime_comparison.require_probe_environment()


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

    case = RuntimeComparison(qtbot, tmp_path, "wpf")
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


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows PowerShell")
def test_actual_probe_request_admission(tmp_path: Path) -> None:
    """Execute real PS admission functions without opening a pipe or desktop."""
    helper = Path("tests/capture/uia_runtime_probe.ps1").resolve()
    script = tmp_path / "admission.ps1"
    script.write_text(
        "param([string]$Helper)\n$ErrorActionPreference='Stop'\n"
        "$tokens=$null;$errors=$null\n"
        "$ast=[Management.Automation.Language.Parser]::ParseFile($Helper,"
        "[ref]$tokens,[ref]$errors)\n"
        "if($errors.Count -ne 0){throw 'parse_failed'}\n"
        "$names=@('Test-ProbeFields','Test-ProbeInteger','Assert-ProbeRequest')\n"
        "$functions=$ast.FindAll({param($a) $a -is "
        "[Management.Automation.Language.FunctionDefinitionAst]},$false)\n"
        "foreach($f in $functions){if($names -ccontains $f.Name){"
        ". ([scriptblock]::Create($f.Extent.Text))}}\n"
        "$json='{" + '"request_id":"' + "a" * 32 + '","action":"inspect",'
        '"payload":{"window_handle":1,"process_id":2,"started_ticks":3,'
        '"foreground_handle":4,"x":-10,"y":20}}' + "'\n"
        "$request=$json|ConvertFrom-Json\nAssert-ProbeRequest $request\n"
        "foreach($field in @('window_handle','process_id','started_ticks',"
        "'foreground_handle','x','y')) {\n"
        "foreach($value in @($null,$true,'1',1.5)) {\n"
        "$request=$json|ConvertFrom-Json;$request.payload.$field=$value\n"
        "$rejected=$false;try{Assert-ProbeRequest $request}catch{$rejected=$true}\n"
        "if(-not $rejected){throw 'coercion_accepted'}\n}}\n"
        "Write-Output 'admission-passed'\n",
        encoding="utf-8",
    )
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [str(host), "-NoProfile", "-File", str(script), "-Helper", str(helper)]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "admission-passed"


@pytest.mark.parametrize("failure", ["admission", "construction"])
def test_early_failure_retains_fixed_receipt(
    failure: str, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission and construction errors leave evidence without exposing text."""
    calls: list[str] = []

    def admit() -> str:
        calls.append("admission")
        if failure == "admission":
            raise ValueError("private environment detail")
        return "wpf"

    def construct(*_args: object) -> None:
        calls.append("construction")
        raise ValueError("private constructor detail")

    monkeypatch.setattr(runtime_comparison, "require_probe_environment", admit)
    monkeypatch.setattr(runtime_comparison, "RuntimeComparison", construct)
    assert not runtime_comparison.run_comparison(qtbot, tmp_path)
    assert calls == (
        ["admission"] if failure == "admission" else ["admission", "construction"]
    )
    receipt = json.loads((tmp_path / "runtime-comparison.json").read_text())
    assert receipt == {
        "schema": 1,
        "outcome": "failed",
        "failure_stage": failure,
        "result": None,
    }


def test_pure_controls_cannot_resync_selected_qt() -> None:
    """The locked lower Qt must not overwrite the explicit comparison runtime."""
    workflow = Path(".github/workflows/uia-ancestry.yml").read_text()
    assert (
        "uv run --no-sync pytest tests/capture/test_runtime_comparison_contract.py"
        in workflow
    )
    assert "uv run pytest" not in workflow
    assert "assert qVersion() == '6.12.0'" in workflow
    assert workflow.index("assert qVersion()") < workflow.index(
        "./scripts/test_runtime_comparison.ps1"
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ancestor_count", True),
        ("ancestor_count", 32),
        ("ancestor_count", -1),
        ("ancestor_count", 0),
        ("root_reached", False),
        ("scroll_available", True),
        ("hit_category", "scrollbar"),
        ("hit_category", "private provider name"),
        ("walk_outcome", "private error"),
        ("extra", "private value"),
    ],
)
def test_ancestry_receipt_rejects_inconsistent_or_private_fields(
    key: str, value: object
) -> None:
    """A verified suffix cannot conceal an invalid leaf, bound, or extra data."""
    result = valid_result()
    cast(dict[str, object], result["ancestry"])[key] = value
    with pytest.raises(ValueError, match="precondition or result failed"):
        decode_result(result)


@pytest.mark.parametrize("category", ["scrollbar", "window", "list_item"])
def test_candidate_stop_is_evidence_not_ancestry(category: str) -> None:
    """Candidate leaves retain classification but make no root/ancestry claim."""
    result = valid_result()
    result["ancestry"] = {
        "hit_category": category,
        "scroll_available": category == "list_item",
        "root_reached": False,
        "ancestor_count": 0,
        "walk_outcome": "candidate_hit",
    }
    assert decode_result(result) == result


@pytest.mark.parametrize("shape", ["null", "empty", "oversized", "wrong_type"])
def test_ancestry_requires_valid_saved_root(shape: str) -> None:
    """Even a claimed verified suffix cannot admit an invalid saved root ID."""
    result = valid_result()
    result["root"] = {"method": shape, "property": "valid", "equal": None}
    with pytest.raises(ValueError, match="precondition or result failed"):
        decode_result(result)
