"""Live-site readiness cannot be satisfied by a title, tab or fixture alone."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, Mock

import pytest
from scripts import scrolling_live_sites as live

from verdiclip.capture.scrolling import ScrollPosition, ScrollResult
from verdiclip.geometry import Rect
from verdiclip.platform.windows import WindowInfo


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


@pytest.mark.parametrize("owned", [True, False])
def test_native_document_observation_requires_owned_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, owned: bool
) -> None:
    """A matching title or foreign document cannot authorize a capture probe."""
    window = WindowInfo(1234, "ScrollPattern - Google Search", Rect(10, 20, 800, 600))
    names = ["Search Results", "learn.microsoft.com", "ScrollPattern Class"]
    observation = {
        "document_found": True,
        "owned_root_verified": owned,
        "names": names,
    }
    run = Mock(return_value=Mock(stdout=json.dumps(observation)))
    monkeypatch.setenv("WINDIR", "C:/Windows")
    monkeypatch.setattr(live.subprocess, "run", run)
    if owned:
        assert live._observe(window, tmp_path) == names
    else:
        with pytest.raises(RuntimeError, match="owned Edge window"):
            live._observe(window, tmp_path)
    assert (
        json.loads((tmp_path / "native-document-discovery.json").read_text())
        == observation
    )
    script = run.call_args.args[0][-1]
    assert "::FromPoint($point)" in script
    assert "::RawViewWalker" in script
    assert "[Windows.Point]::new(410, 320)" in script
    assert "::FromHandle([IntPtr]1234)" in script
    assert "::Compare($node, $root)" in script
    assert "[Windows.Automation.ControlType]::Document" in script
    assert "remote-debugging" not in script
    assert "javascript" not in script
    assert run.call_args.kwargs["timeout"] == 15


def test_chrome_only_observation_is_retained_but_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preserve the initial native failure as evidence instead of approving chrome."""
    window = WindowInfo(1234, "ScrollPattern - Google Search", Rect(10, 20, 800, 600))
    observation = {
        "document_found": False,
        "owned_root_verified": False,
        "names": [
            "ScrollPattern - Google Search",
            "Search tabs",
            "Address and search bar",
        ],
    }
    monkeypatch.setenv("WINDIR", "C:/Windows")
    monkeypatch.setattr(
        live.subprocess, "run", Mock(return_value=Mock(stdout=json.dumps(observation)))
    )
    with pytest.raises(RuntimeError, match="did not discover a Document"):
        live._observe(window, tmp_path)
    assert (tmp_path / "native-document-discovery.json").is_file()
    assert not live.google_results_ready(window.title, observation["names"])


@pytest.mark.parametrize(
    "label",
    [
        "learn.microsoft.com.evil.example",
        "https://learn.microsoft.com.evil.example",
        "evil.learn.microsoft.com",
        "https://evil.learn.microsoft.com",
        "https://evil.example/learn.microsoft.com",
        "https://evil.example/?site=https://learn.microsoft.com",
        "https://learn.microsoft.com@evil.example",
        "https://learn.microsoft.com/attacker-label",
        "IUIAutomationScrollPattern Microsoft Learn https://learn.microsoft.com",
        "Microsoft Learn",
    ],
)
def test_google_readiness_rejects_embedded_or_spoofed_site_labels(label: str) -> None:
    """Readiness requires an observed exact native label, not host-like prose."""
    assert not live.google_results_ready(
        "Windows UI Automation ScrollPattern - Google Search",
        ["Search Results", label],
    )


def test_google_readiness_accepts_retained_native_site_label() -> None:
    """Run2's raw UIA evidence includes this standalone label, not only titles."""
    link_label = (
        "IUIAutomationScrollPattern interface (uiautomationclient.h) "
        "Microsoft Learn https://learn.microsoft.com "
        "\u203a ... \u203a Uiautomationclient.h"
    )
    assert live.google_results_ready(
        "Windows UI Automation ScrollPattern - Google Search - Microsoft Edge",
        [
            "Search Results",
            link_label,
            "Microsoft Learn",
            "https://learn.microsoft.com",
        ],
    )


def test_document_point_rejects_nested_panel_with_matching_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only native CompareElements identity can admit the re-hit-tested point."""
    window = WindowInfo(1234, "Google", Rect(0, 0, 800, 600))
    target = Mock(area=window.bounds)
    target.position.return_value = ScrollPosition(-1, 37, 17)
    candidate = MagicMock(area=window.bounds)
    candidate.__enter__.return_value = candidate
    candidate.position.return_value = target.position()
    candidate._element.control_type.return_value = live._DOCUMENT_ROLE
    target._automation.same_element.side_effect = [False, True]
    factory = Mock(return_value=candidate)
    monkeypatch.setattr(live, "UiaScrollTarget", factory)
    report: dict[str, object] = {}
    point = live._document_point(target, window, report, "after_setup")
    assert point == live._candidate_points(window.bounds)[1]
    assert factory.call_count == 2
    assert target._automation.same_element.call_count == 2
    target.move.assert_not_called()
    candidate.move.assert_not_called()


def test_document_point_search_is_bounded_and_does_not_scroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing children cannot trigger unbounded probing or arbitrary capture."""
    window = WindowInfo(1234, "Google", Rect(0, 0, 800, 600))
    target = Mock(area=window.bounds)
    candidate = MagicMock(area=window.bounds)
    candidate.__enter__.return_value = candidate
    candidate.position.return_value = ScrollPosition(-1, 0, 88)
    target._automation.same_element.return_value = False
    factory = Mock(return_value=candidate)
    monkeypatch.setattr(live, "UiaScrollTarget", factory)
    with pytest.raises(RuntimeError, match="retained native Document"):
        live._document_point(target, window, {}, "after_setup")
    assert factory.call_count == len(live._candidate_points(window.bounds))
    target.move.assert_not_called()
    candidate.move.assert_not_called()


def test_open_document_rejects_nested_generic_scroll_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successfully discovered scroller is not necessarily the intended page."""
    window = WindowInfo(1234, "Google", Rect(0, 0, 800, 600))
    candidate = MagicMock(area=window.bounds)
    candidate.__enter__.return_value = candidate
    candidate._element.control_type.side_effect = [50026, live._DOCUMENT_ROLE]
    factory = Mock(return_value=candidate)
    monkeypatch.setattr(live, "UiaScrollTarget", factory)
    with ExitStack() as owners:
        assert live._open_document(window, owners, {}) is candidate
    assert factory.call_count == 2
    candidate.move.assert_not_called()


@pytest.mark.parametrize("changed_after_image", [False, True])
def test_failed_helper_diagnoses_retained_document_without_rediscovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, changed_after_image: bool
) -> None:
    """The same screen point may now hit AI Overview; retain the original page."""
    window = WindowInfo(1234, "Google", Rect(0, 0, 800, 600))
    start = ScrollPosition(-1, 37, 17)
    target = Mock(area=window.bounds)
    target.position.return_value = start
    changed = ScrollPosition(-1, 41, 17)
    if changed_after_image:
        target.position.side_effect = [start, changed]
    monkeypatch.setattr(live, "_open_document", Mock(return_value=target))
    monkeypatch.setattr(
        live,
        "_document_point",
        Mock(return_value=live._candidate_points(window.bounds)[0]),
    )
    monkeypatch.setattr(live.native, "_position_start", Mock(return_value=start))
    monkeypatch.setattr(
        live.native,
        "_capture",
        Mock(return_value=ScrollResult(None, "ambiguous pixels", "restored", 2)),
    )
    collect = Mock(return_value={"success": True, "product_qualification": False})
    monkeypatch.setattr(live, "collect", collect)
    factory = Mock()
    monkeypatch.setattr(live, "UiaScrollTarget", factory)
    report: dict[str, object] = {}
    error = (
        "starting position was not restored"
        if changed_after_image
        else "ambiguous pixels"
    )
    with pytest.raises(RuntimeError, match=error):
        live._capture_google("edge-google-live", window, tmp_path, report)
    if changed_after_image:
        collect.assert_not_called()
    else:
        assert collect.call_args.args[0] is target
    factory.assert_not_called()
    assert report["after"] == {
        "horizontal": -1,
        "vertical": 41 if changed_after_image else 37,
        "view_size": 17,
    }
    reads = cast("dict[str, dict[str, object]]", report["after_observations"])
    assert reads["before_image"]["position"] == asdict(start)
    assert reads["after_image"]["position"] == asdict(
        changed if changed_after_image else start
    )
    before_elapsed = reads["before_image"]["elapsed_seconds"]
    after_elapsed = reads["after_image"]["elapsed_seconds"]
    assert isinstance(before_elapsed, float)
    assert isinstance(after_elapsed, float)
    assert after_elapsed >= before_elapsed >= 0
    assert report.get("success") is not True


def test_unresolved_matrix_reads_only_same_element_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retained and fresh values remain observations, never writes."""
    monkeypatch.setattr(live.time, "sleep", Mock())
    target = Mock(area=Rect(0, 0, 976, 608))
    target._scroll.number.side_effect = lambda slot: {5: -1, 6: 17, 8: 26}[slot]
    fresh = Mock()
    fresh.number.side_effect = lambda slot: {5: -1, 6: 34, 8: 26}[slot]
    release = Mock()

    def acquire(owners: ExitStack) -> Mock:
        owners.callback(release)
        return fresh

    target._element.scroll_pattern.side_effect = acquire
    monkeypatch.setattr(live, "_observe_resolved_element", Mock())
    result = live._observe_unresolved_pattern(target, live.Point(1, 1), 1234)
    observations = cast("list[dict[str, object]]", result["observations"])
    assert len(observations) == 20
    for entry in observations:
        if entry["method"] == "resolved_element":
            continue
        assert entry["horizontal"] == -1
        assert entry["view_size"] == 26
        assert entry["vertical"] == (34 if entry["method"] == "fresh_pattern" else 17)
    assert release.call_count == 5
    target.move.assert_not_called()
    assert result["writes"] is False
    assert result["product_qualification"] is False


def test_unresolved_matrix_stops_on_guard_error() -> None:
    """Changed input or geometry stops every subsequent COM observation."""
    target = Mock(area=Rect(0, 0, 976, 608))
    target.check.side_effect = live.CaptureError("user input changed")
    result = live._observe_unresolved_pattern(target, live.Point(1, 1), 1234)
    assert result["error"] == "user input changed"
    target._scroll.number.assert_not_called()
    target._element.scroll_pattern.assert_not_called()
    target.move.assert_not_called()


def test_unresolved_matrix_deadline_blocks_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No observation is admitted after the bounded deadline."""
    monkeypatch.setattr(live.time, "monotonic", Mock(side_effect=[0, 10, 10]))
    target = Mock(area=Rect(0, 0, 976, 608))
    result = live._observe_unresolved_pattern(target, live.Point(1, 1), 1234)
    assert result["deadline_reached"] is True
    assert result["observations"] == []
    target.check.assert_not_called()


def test_unresolved_matrix_records_partial_com_error_and_releases() -> None:
    """A partial native failure retains evidence and releases its interface."""
    target = Mock(area=Rect(0, 0, 976, 608))
    target._scroll.number.side_effect = [-1, 17, 26]
    fresh = Mock()
    fresh.number.side_effect = [-1, live.CaptureError("HRESULT failure")]
    release = Mock()

    def acquire(owners: ExitStack) -> Mock:
        owners.callback(release)
        return fresh

    target._element.scroll_pattern.side_effect = acquire
    result = live._observe_unresolved_pattern(target, live.Point(1, 1), 1234)
    assert result["error"] == "HRESULT failure"
    observations = cast("list[dict[str, object]]", result["observations"])
    assert len(observations) == 2
    assert observations[-1]["horizontal"] == -1
    assert "vertical" not in observations[-1]
    release.assert_called_once()
    target.move.assert_not_called()


def test_unresolved_result_stays_failed_even_if_observation_catches_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Observational catch-up never permits diagnostic writes or qualification."""
    window = WindowInfo(1234, "Google", Rect(0, 0, 800, 600))
    start = ScrollPosition(-1, 37, 17)
    target = Mock(area=window.bounds)
    target.position.return_value = start
    monkeypatch.setattr(live, "_open_document", Mock(return_value=target))
    monkeypatch.setattr(live, "_document_point", Mock(return_value=live.Point(1, 1)))
    monkeypatch.setattr(live.native, "_position_start", Mock(return_value=start))
    restoration = "not restored: the provider write is unresolved"
    monkeypatch.setattr(
        live.native,
        "_capture",
        Mock(
            return_value=ScrollResult(
                None,
                "position uncertain",
                restoration,
                2,
            )
        ),
    )
    matrix = Mock(return_value={"writes": False})
    monkeypatch.setattr(live, "_observe_unresolved_pattern", matrix)
    collect = Mock()
    monkeypatch.setattr(live, "collect", collect)
    report: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="position uncertain"):
        live._capture_google("edge-google-live", window, tmp_path, report)
    matrix.assert_called_once_with(target, live.Point(1, 1), window.handle)
    collect.assert_not_called()
    target.move.assert_not_called()
    assert report["restoration"] == restoration
    assert report["message"] == "position uncertain"


@pytest.mark.parametrize("mismatch", [None, "identity", "area", "role"])
def test_resolved_observation_requires_same_document(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str | None,
) -> None:
    """A different target never contributes borrowed provider state."""
    target = Mock(area=Rect(22, 90, 976, 608))
    fresh = Mock(area=target.area)
    fresh._element.control_type.return_value = 50030 if mismatch != "role" else 50033
    if mismatch == "area":
        fresh.area = Rect(22, 90, 650, 297)
    target._automation.same_element.return_value = mismatch != "identity"
    fresh._scroll.number.side_effect = lambda slot: {5: -1, 6: 34, 8: 26}[slot]

    context = MagicMock()
    context.__enter__.return_value = fresh
    monkeypatch.setattr(live, "UiaScrollTarget", Mock(return_value=context))
    entry: dict[str, object] = {}
    live._observe_resolved_element(
        target, live.Point(51, 394), 1234, entry, target.check
    )
    assert entry["comparable"] is (mismatch is None)
    if mismatch is None:
        assert (entry["horizontal"], entry["vertical"], entry["view_size"]) == (
            -1,
            34,
            26,
        )
    else:
        fresh._scroll.number.assert_not_called()
    context.__exit__.assert_called_once()
    target.move.assert_not_called()
    fresh.move.assert_not_called()


def test_resolved_observation_releases_on_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native errors release the temporary target and cannot trigger a setter."""
    target = Mock(area=Rect(22, 90, 976, 608))
    fresh = Mock(area=target.area)
    fresh._element.control_type.return_value = 50030
    target._automation.same_element.return_value = True
    fresh._scroll.number.side_effect = live.CaptureError("native read failed")
    context = MagicMock()
    context.__enter__.return_value = fresh
    context.__exit__.return_value = False
    monkeypatch.setattr(live, "UiaScrollTarget", Mock(return_value=context))
    with pytest.raises(live.CaptureError, match="native read failed"):
        live._observe_resolved_element(
            target, live.Point(51, 394), 1234, {}, target.check
        )
    context.__exit__.assert_called_once()
    target.move.assert_not_called()
    fresh.move.assert_not_called()
