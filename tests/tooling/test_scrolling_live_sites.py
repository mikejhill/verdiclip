"""Live-site readiness cannot be satisfied by a title, tab or fixture alone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import scrolling_live_sites as live


@pytest.mark.parametrize(
    ("title", "names", "expected"),
    [
        (
            "Windows UI Automation ScrollPattern - Google Search",
            ["Search Results", "learn.microsoft.com", "ScrollPattern Class"],
            True,
        ),
        ("ScrollPattern - Google Search", ["Google", "ScrollPattern"], False),
        ("VerdiClip scrolling check", ["Main", "learn.microsoft.com"], False),
        (
            "ScrollPattern - Google Search",
            ["Main", "learn.microsoft.com", "Before you continue to Google"],
            False,
        ),
        (
            "ScrollPattern - Google Search",
            ["Main", "learn.microsoft.com", "Unusual traffic"],
            False,
        ),
    ],
)
def test_google_readiness(title: str, names: list[str], *, expected: bool) -> None:
    """Consent and challenges remain blockers rather than silent passes."""
    assert live.google_results_ready(title, names) is expected


def test_non_hosted_refuses_before_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not open public sites on a user's actual desktop by accident."""
    monkeypatch.setattr(live.sys, "platform", "linux")
    launch = Mock()
    monkeypatch.setattr(live.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="disposable hosted Windows"):
        live.main()
    launch.assert_not_called()


def test_live_workflow_preserves_trust_and_unsigned_probe_boundary() -> None:
    """Actual sites cannot be replaced with a passing signed-out To Do capture."""
    workflow = Path(".github/workflows/simple-scroll-native.yml").read_text()
    live = workflow.split("  live-sites:\n", 1)[1]
    condition = live.split("    name:", 1)[0]
    assert "github.repository == 'mikejhill/verdiclip'" in condition
    assert (
        "github.event.pull_request.head.repo.full_name == 'mikejhill/verdiclip'"
        in condition
    )
    assert "github.head_ref == 'fix/real-app-scrolling'" in condition
    assert "github.event.pull_request.draft == true" in condition
    assert "workflow_dispatch" not in condition
    assert "permissions:\n  contents: read\n" in workflow
    assert "persist-credentials: false" in live
    assert "$actual -ne $env:QUALIFICATION_HEAD" in live
    assert "continue-on-error" not in live
    assert "$result.access_probe_completed -ne $true" in live
    assert "$result.product_capture_qualification -ne $false" in live
    assert "$result.capture_attempted -ne $false" in live
    assert "$result.success -ne $false" in live


def test_preflight_runs_unchanged_quality_gate_before_pull_request() -> None:
    """A fixed-branch Windows run supplies real IPC, not skipped local tests."""
    workflow = Path(".github/workflows/scroll-fix-preflight.yml").read_text()
    assert "branches: [fix/real-app-scrolling]" in workflow
    assert "pull_request:" not in workflow
    assert "contents: read" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "run: uv run poe check" in workflow
    assert "--require-interactive-capture" in workflow
    assert "continue-on-error" not in workflow
    assert (
        "edge-google-live, edge-todo-access, edge-owned-search, edge-owned-settings"
        in workflow
    )
