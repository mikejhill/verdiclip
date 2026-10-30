"""Full self-driven selection/consent/review/delivery using declared OS fakes.

This module is executable on hosted Windows/offscreen Qt. It does not qualify
native keyboard packets, provider compatibility or real desktop pixels.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Never, cast
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot
from scripts.scroll_workflow_plan import Phase, PlanError
from tests.capture.scroll_journey_fixture import JourneyCase, ScrollJourney
from tests.capture.test_scroll_service import SnapshotWindows
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.reviewed import CaptureCompleteness
from verdiclip.capture.scroll.workflow import WorkflowState
from verdiclip.geometry import Point
from verdiclip.settings import CaptureSettings, Settings
from verdiclip.startup_lifetime import StartupLifetime

pytestmark = pytest.mark.skipif(
    os.environ.get("VERDICLIP_SCROLL_FIXTURE_SHA", os.environ.get("GITHUB_SHA"))
    is None,
    reason="Hosted fixture or separately authorized exact-source harness only",
)


@pytest.fixture(autouse=True)
def clean_clipboard() -> None:
    """Override legacy root teardown: this entire module avoids clipboard contents."""


@pytest.fixture(autouse=True)
def forbidden_native_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mistaken constructor substitution must fail before native work begins."""

    def refuse(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("Full workflow fixture attempted a forbidden native port")

    monkeypatch.setattr("verdiclip.capture.grabber.MssScreenSource.freeze", refuse)
    monkeypatch.setattr(
        "verdiclip.capture.input_native.Win32InputPlatform.install", refuse
    )
    monkeypatch.setattr(
        "verdiclip.capture.scroll.uia_adapter.UiaAdapter.inspect", refuse
    )
    monkeypatch.setattr("verdiclip.capture.scroll.uia_process.UiaProcess.start", refuse)
    monkeypatch.setattr("PySide6.QtGui.QGuiApplication.clipboard", refuse)


@pytest.fixture
def app_factory(request: pytest.FixtureRequest) -> AppFactory:
    """Retain real controller ownership and existing explicit fake OS adapters."""
    return cast(AppFactory, request.getfixturevalue(make_app.__name__))


@pytest.fixture
def journey(
    app_factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> Iterator[ScrollJourney]:
    """Every fixture owns its application, provider, private output and spool."""
    source = os.environ.get(
        "VERDICLIP_SCROLL_FIXTURE_SHA", os.environ.get("GITHUB_SHA")
    )
    if source is None:
        pytest.skip("Hosted fixture or separately authorized exact-source harness only")
    case = getattr(request, "param", {})
    app = app_factory(
        Settings(capture=CaptureSettings(open_editor=True, save_to_file=True)),
        windows=SnapshotWindows(),
    )
    value = ScrollJourney(
        app,
        qtbot,
        tmp_path / "spool",
        JourneyCase(source, case.get("scene", "popup"), case.get("hold_move", False)),
    )
    try:
        yield value
    finally:
        with StartupLifetime() as cleanup:
            cleanup.callback(write_receipt, value, source)
            cleanup.callback(value.close)


def write_receipt(value: ScrollJourney, source: str) -> None:
    """Always retain attempted/confirmed cleanup separately from JUnit outcome."""
    receipt_root = os.environ.get("VERDICLIP_SCROLL_FIXTURE_RECEIPTS")
    if receipt_root is not None:
        destination = Path(receipt_root).resolve()
        root = Path(__file__).resolve().parents[2]
        if not destination.is_relative_to(root):
            raise ValueError("Synthetic receipts must remain inside this checkout")
        destination.mkdir(parents=True, exist_ok=True)
        receipt = {
            "source_sha": source,
            "case_id": value.labels.case_id,
            "evidence": "full-workflow-over-explicit-os-fakes",
            "phase": value.plan.phase.value,
            "commands": value.command_trace,
            "capture_ids": list(
                dict.fromkeys(item.capture_id for item in value.requested)
            ),
            "cleanup_attempt": value.cleanup_attempts,
            "cleanup_attempted": value.cleanup_attempted,
            "cleanup_confirmed": value.cleanup_confirmed,
            "pytest_verdict": "See separate JUnit; a phase label is not a test verdict",
            "input_retired": not value.broker.busy and not value.broker.active,
            "provider_busy": value.adapter.busy,
            "native_input_qualified": False,
            "real_provider_qualified": False,
            "physical_input_qualified": False,
        }
        (
            destination / f"{value.labels.case_id}-{value.cleanup_attempts}.json"
        ).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def assert_exact_pixels(actual: QImage, expected: QImage) -> None:
    """Compare dimensions and every RGBA pixel across storage-format conversion."""
    assert not actual.isNull()
    assert actual.size() == expected.size()
    pixel_format = QImage.Format.Format_RGBA8888
    assert actual.convertToFormat(pixel_format) == expected.convertToFormat(
        pixel_format
    )


def assert_delivered_pixels(journey: ScrollJourney, expected: QImage) -> None:
    """Verify exact row content, ordinary document provenance and one real PNG."""
    assert len(journey.accepted) >= 1
    capture = journey.accepted[0]
    assert all(value is capture for value in journey.accepted)
    assert_exact_pixels(capture.image, expected)
    records = journey.app.controller.workspace.documents
    assert len(records) == 1
    assert_exact_pixels(records[0].session.document.image, expected)
    assert records[0].provenance is not None
    assert records[0].provenance.capture_id == capture.capture_id
    assert records[0].provenance.completeness is capture.completeness
    files = list(journey.app.store.load().output.directory.glob("*.png"))
    assert len(files) == 1
    assert_exact_pixels(QImage(str(files[0])), expected)
    assert journey.app.controller.pending_deliveries == ()
    journey.delivered()


def test_pixel_oracle_allows_storage_conversion_but_rejects_content_changes() -> None:
    """Format normalization cannot hide changed channels, alpha or dimensions."""
    expected = QImage(3, 2, QImage.Format.Format_ARGB32)
    expected.fill(0xFF305070)
    assert_exact_pixels(
        expected.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), expected
    )
    for pixel in (0xFF315070, 0x80305070):
        changed = expected.copy()
        changed.setPixel(1, 1, pixel)
        with pytest.raises(AssertionError):
            assert_exact_pixels(changed, expected)
    with pytest.raises(AssertionError):
        assert_exact_pixels(expected.copy(0, 0, 2, 2), expected)
    with pytest.raises(AssertionError):
        assert_exact_pixels(QImage(), expected)


@pytest.mark.parametrize(
    "journey", [{"scene": "popup"}, {"scene": "modal"}], indirect=True
)
@pytest.mark.parametrize("entry", ["scrollbar", "rectangle", "enter-region"])
@pytest.mark.parametrize("selected", [False, True])
def test_full_selection_confirmation_review_and_exact_output(
    journey: ScrollJourney, entry: str, *, selected: bool
) -> None:
    """All entry forms require real Start and explicit whole/range UI acceptance."""
    journey.select(entry)
    assert not journey.providers
    assert not list(journey.app.store.load().output.directory.glob("*.png"))
    journey.confirm()
    journey.await_review()
    journey.accept(selected=selected)
    assert not list(journey.app.store.load().output.directory.glob("*.png"))
    journey.acknowledge_input()
    top, height = (8, 24) if selected else (0, 96)
    assert_delivered_pixels(
        journey, journey.providers[0].oracle.copy(0, top, 90, height)
    )
    capture = journey.accepted[0]
    assert capture.completeness is (
        CaptureCompleteness.PARTIAL if selected else CaptureCompleteness.COMPLETE
    )
    assert capture.stop_reason == (
        "selected_range" if selected else "reached_reported_end"
    )
    before = tuple(journey.app.controller.workspace.documents)
    # Deliberate stale replay is not a new authorized command or native input.
    journey.broker.feed(SelectionInput(InputKind.ENTER, Point(0, 0)))
    assert tuple(journey.app.controller.workspace.documents) == before
    assert len(journey.accepted) == 1
    assert journey.plan.phase is Phase.DELIVERED
    with pytest.raises(AssertionError, match="active lease"):
        journey.feed(InputKind.ENTER)


@pytest.mark.parametrize(
    "journey",
    [{"scene": "popup", "hold_move": True}, {"scene": "modal", "hold_move": True}],
    indirect=True,
)
def test_real_stop_control_delivers_only_the_verified_prefix(
    journey: ScrollJourney,
) -> None:
    """A held fake move makes Stop reproducible; it cannot claim reported end."""
    journey.select("enter-region")
    journey.confirm()
    journey.stop()
    journey.await_review()
    journey.accept(selected=False)
    journey.acknowledge_input()
    assert_delivered_pixels(journey, journey.providers[0].oracle.copy(0, 0, 90, 48))
    assert journey.accepted[0].completeness is CaptureCompleteness.PARTIAL
    assert journey.accepted[0].stop_reason == "stopped_by_user"
    assert journey.providers[0].moves == 0


@pytest.mark.parametrize("stage", ["confirmation", "review"])
def test_escape_discards_only_this_journey_without_output(
    journey: ScrollJourney, stage: str
) -> None:
    """A target offer or retained review never implies Start or delivery consent."""
    journey.select("enter-region")
    if stage == "review":
        journey.confirm()
        journey.await_review()
    journey.cancel()
    assert journey.plan.phase is Phase.CANCELLED
    assert not journey.app.controller.pending_deliveries
    assert not list(journey.app.store.load().output.directory.glob("*.png"))
    if stage == "confirmation":
        assert not journey.providers


def test_failed_editor_admission_retries_the_same_pixels_without_saving_twice(
    journey: ScrollJourney, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retained UI retry acquires fresh cleanup and preserves the already-saved PNG."""
    journey.select("enter-region")
    journey.confirm()
    journey.await_review()
    workspace = journey.app.controller.workspace
    original = workspace.open_document
    monkeypatch.setattr(
        workspace,
        "open_document",
        Mock(side_effect=RuntimeError("Fixture admission refused")),
    )
    journey.accept(selected=False)
    journey.acknowledge_input()
    assert journey.workflow.state is WorkflowState.PAUSED
    capture = journey.app.controller.pending_deliveries[0]
    saved = next(journey.app.store.load().output.directory.glob("*.png"))
    contents, stamp = saved.read_bytes(), saved.stat().st_mtime_ns
    monkeypatch.setattr(workspace, "open_document", original)
    journey.retry()
    assert not workspace.documents
    journey.acknowledge_input()
    assert_delivered_pixels(journey, journey.providers[0].oracle.copy(0, 0, 90, 96))
    assert all(value is capture for value in journey.requested)
    assert all(value is capture for value in journey.accepted)
    assert saved.read_bytes() == contents
    assert saved.stat().st_mtime_ns == stamp
    assert journey.command_trace.count("accept-whole") == 1
    assert journey.command_trace.count("retry") == 1


def test_uncertain_input_cleanup_cannot_publish_or_claim_a_terminal_pass(
    journey: ScrollJourney,
) -> None:
    """An explicit negative receipt preserves review and blocks output/new leases."""
    journey.select("enter-region")
    journey.confirm()
    journey.await_review()
    journey.accept(selected=False)
    starts = journey.broker.starts
    journey.broker.interrupt("Fixture cleanup unconfirmed", cleaned=False)
    assert journey.workflow.state is WorkflowState.PAUSED
    assert not journey.accepted
    assert not journey.app.controller.workspace.documents
    assert not list(journey.app.store.load().output.directory.glob("*.png"))
    journey.app.press_hotkey("region")
    assert journey.broker.starts == starts
    with pytest.raises(PlanError, match="cleanup"):
        journey.plan.observe(
            replace(journey.observation(Phase.CLEANUP), phase=Phase.DELIVERED),
            time.monotonic(),
        )
    assert journey.plan.phase is Phase.FAILED


def test_unknown_recipe_refuses_before_selection(journey: ScrollJourney) -> None:
    """An invalid scripted goal cannot turn into an arbitrary UI action."""
    with pytest.raises(ValueError, match="Unknown deterministic"):
        journey.select("undeclared")
    assert journey.broker.starts == 0
    assert not journey.app.controller.capture.overlays


def test_configuration_rejects_undeclared_scene_before_window_setup() -> None:
    """Only declared synthetic contexts may be constructed by this fixture."""
    with pytest.raises(ValueError, match="declared synthetic"):
        JourneyCase("a" * 40, "foreign-window")


def test_cleanup_failure_does_not_skip_other_owners_or_its_receipt(
    journey: ScrollJourney, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a failing cancel must retire the scene and preserve uncertain evidence."""
    journey.select("enter-region")
    capture = journey.app.controller.capture
    original = capture.cancel

    def cancel_then_fail() -> None:
        original()
        raise RuntimeError("Injected owned cancel callback failure")

    receipt_root = os.environ.get(
        "VERDICLIP_SCROLL_FIXTURE_RECEIPTS",
        str(Path(__file__).resolve().parents[2] / "artifacts/scroll-workflow"),
    )
    with monkeypatch.context() as fault:
        fault.setenv("VERDICLIP_SCROLL_FIXTURE_RECEIPTS", receipt_root)
        fault.setattr(capture, "cancel", cancel_then_fail)

        def retire_with_receipt() -> None:
            with StartupLifetime() as owners:
                owners.callback(write_receipt, journey, journey.labels.source_sha)
                owners.callback(journey.close)

        with pytest.raises(RuntimeError, match="Injected owned cancel"):
            retire_with_receipt()
    receipt = Path(receipt_root) / f"{journey.labels.case_id}-1.json"
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["cleanup_attempted"] is True
    assert saved["cleanup_confirmed"] is False
    assert not journey.broker.busy
    assert not capture.is_selecting
    assert all(not window.isVisible() for window in journey.scene.windows)
