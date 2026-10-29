"""Portable qualification of the native matrix; never actual hook evidence."""

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Never, cast

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMenu, QWidget
from pytestqt.qtbot import QtBot
from tests.capture.input_fixture import ReplayInputPlatform
from tests.capture.menu_peer import MenuPeer, decode_menu_state
from tests.capture.preservation_harness import (
    CaptureHarness,
    HarnessBudgets,
    PreservationSnapshot,
)
from tests.capture.production_matrix import (
    CASES,
    DriverJournal,
    InputProfile,
    MemoryCaptureSink,
    ObservedWin32Platform,
    ProductionBrokerDriver,
    StrictFixtureSnapshot,
    cleanup_confirmed,
    fixture_foreground_errors,
    native_action_errors,
    qualification_plan,
)
from tests.capture.test_popup_capture import CaptureContext, ExistingScene
from tests.capture.test_preservation_harness import SemanticDriver
from tests.ux.test_app_journeys import AppFactory
from tests.ux.test_app_journeys import (
    make_app as make_app,  # noqa: PLC0414 - pytest fixture re-export
)

from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.settings import CaptureSettings, Settings


def menu_reply(**changes: object) -> str:
    """A matching open-popup state includes its actual GUI owner."""
    return (
        json.dumps(
            {
                "nonce": "scope",
                "sequence": 1,
                "owner": 42,
                "menu_owner": 42,
                "flags": 0x14,
                "visible": True,
                "foreground": 42,
                "valid": True,
                "rect": [0, 0, 640, 480],
                **changes,
            }
        )
        + "\n"
    )


def test_plan_and_unstarted_driver_are_not_native_evidence(tmp_path: Path) -> None:
    """Source preparation cannot install a hook or silently qualify hardware."""
    plan = qualification_plan()
    assert plan["execute"] is False
    assert plan["input_generated_by_harness"] is False
    assert plan["exclusive_minutes"] == 8
    driver = ProductionBrokerDriver(InputProfile.DEFAULT)
    worker = driver.worker(Point(10, 10), Rect(0, 0, 640, 480))
    assert worker._thread is None
    assert worker.options.injected_move_mode is None
    assert driver.platforms == []
    assert driver.receipt()["native_hooks"] is False
    peer = MenuPeer()
    with pytest.raises(ValueError, match="handoff"):
        peer.start(handoff=False)
    assert peer.process is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("platform", ["offscreen", "windows"])
def test_portable_native_guard_never_constructs_a_real_worker(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    """Observe the QPA guard through a forbidden factory on every actual QPA."""
    del qapp
    driver = ProductionBrokerDriver(InputProfile.DEFAULT)
    calls: list[bool] = []

    def forbidden(_initial: Point, _bounds: Rect) -> Never:
        calls.append(True)
        raise CaptureError("Fixture factory forbids a real native worker")

    monkeypatch.setattr(driver, "worker", forbidden)
    monkeypatch.setattr(
        "verdiclip.capture.input_broker.QGuiApplication.platformName",
        staticmethod(lambda: platform),
    )
    owner = QObject()
    broker = driver.broker(owner)
    try:
        expected = "native Windows" if platform == "offscreen" else "Fixture factory"
        with pytest.raises(CaptureError, match=expected):
            broker.start(Point(0, 0), Rect(0, 0, 100, 100))
        assert calls == ([] if platform == "offscreen" else [True])
        assert not driver.workers
    finally:
        owner.deleteLater()


def test_replay_injection_never_counts_as_production_hook_proof() -> None:
    """Portable worker scheduling remains useful but accurately labelled."""
    platform = ReplayInputPlatform()
    driver = ProductionBrokerDriver(
        InputProfile.INJECTED, platform_factory=lambda: platform
    )
    worker = driver.worker(platform.anchor, Rect(-2000, -1000, 6000, 4000))
    platform.key(0x100, 0x1B)
    platform.key(0x101, 0x1B)
    platform.actions.put(worker.release)
    worker.start()
    try:
        assert worker.stopped.wait(2)
        assert worker.cleaned
        assert not driver.cleanup_errors()
        assert driver.receipt()["native_hooks"] is False
        assert driver.receipt()["physical_raw_input_qualified"] is False
    finally:
        worker.release()
        assert worker.stopped.wait(2)


def test_driver_journal_is_scoped_single_use_and_not_a_completion_receipt(
    tmp_path: Path,
) -> None:
    """Only the capture coordinator may establish success, not an output file."""
    journal = DriverJournal(tmp_path, CASES[0])
    with pytest.raises(ValueError, match="nonempty"):
        journal.request("escape", Rect(1, 1, 20, 20))
    journal.owned_handles = (42,)
    journal.binding_handle = 42
    with pytest.raises(ValueError, match="bind"):
        journal.request("escape", Rect(1, 1, 20, 20))
    journal.prepare_binding(Rect(1, 1, 20, 20))
    assert journal.binding is not None
    acknowledgement = {**journal.binding, "phase": "bound-no-input"}
    (tmp_path / "driver-bound.json").write_text(json.dumps(acknowledgement))
    assert journal.binding_ready(Rect(1, 1, 20, 20))
    assert journal.requests == []
    journal.request("escape", Rect(1, 1, 20, 20))
    assert json.loads((tmp_path / "driver-request.json").read_text())[
        "owned_hwnds"
    ] == [42]
    assert not hasattr(journal, "completed")
    with pytest.raises(ValueError, match="once"):
        journal.request("escape", Rect(1, 1, 20, 20))


@pytest.mark.parametrize(
    "change",
    [
        {"nonce": "old"},
        {"phase": "input-now"},
        {"binding_hwnd": True},
        {"binding_hwnd": 43},
        {"owned_hwnds": [43]},
        {"owned_hwnds": [True]},
    ],
)
def test_binding_rejects_foreign_or_malformed_acknowledgements(
    tmp_path: Path, change: dict[str, object]
) -> None:
    """A file from another case or a loose JSON identity cannot arm capture."""
    journal = DriverJournal(tmp_path, CASES[0])
    journal.owned_handles = (42,)
    journal.binding_handle = 42
    area = Rect(1, 1, 20, 20)
    journal.prepare_binding(area)
    assert journal.binding is not None
    payload = {**journal.binding, "phase": "bound-no-input", **change}
    (tmp_path / "driver-bound.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="stale or unrelated"):
        journal.binding_ready(area)
    assert not journal.bound
    assert not journal.requests


def test_binding_waits_for_complete_matching_scope_and_never_completes_capture(
    tmp_path: Path,
) -> None:
    """Absent/partial acknowledgements cannot consume the actual input window."""
    journal = DriverJournal(tmp_path, CASES[0])
    journal.owned_handles = (1, 2)
    journal.binding_handle = 1
    area = Rect(-30, 50, 20, 20)
    journal.prepare_binding(area)
    assert not journal.binding_ready(area)
    path = tmp_path / "driver-bound.json"
    path.write_text('{"nonce":')
    assert not journal.binding_ready(area)
    path.write_text("x" * 4097)
    with pytest.raises(ValueError, match="frame bound"):
        journal.binding_ready(area)
    path.unlink()
    journal.owned_handles = (1, 3)
    with pytest.raises(ValueError, match="scope changed"):
        journal.binding_ready(area)
    journal.owned_handles = (1, 2)
    with pytest.raises(ValueError, match="scope changed"):
        journal.binding_ready(Rect(-29, 50, 20, 20))
    assert not journal.requests
    assert not hasattr(journal, "completed")


@pytest.mark.parametrize(
    "rectangle",
    [
        None,
        {},
        {"x": 2, "y": 1, "width": 20, "height": 20},
        {"x": True, "y": 1, "width": 20, "height": 20},
        {"x": 1.0, "y": 1, "width": 20, "height": 20},
        {"x": 1, "y": 1, "width": 20, "height": 20, "extra": 0},
    ],
)
def test_binding_acknowledgement_requires_exact_integer_geometry(
    tmp_path: Path,
    rectangle: object,
) -> None:
    """A nonce/owner match cannot acknowledge a different or ambiguous region."""
    journal = DriverJournal(tmp_path, CASES[0])
    journal.owned_handles = (42,)
    journal.binding_handle = 42
    area = Rect(1, 1, 20, 20)
    journal.prepare_binding(area)
    assert journal.binding is not None
    (tmp_path / "driver-bound.json").write_text(
        json.dumps(
            {
                **journal.binding,
                "phase": "bound-no-input",
                "physical_rectangle": rectangle,
            }
        )
    )
    with pytest.raises(ValueError, match="geometry"):
        journal.binding_ready(area)
    assert not journal.bound
    assert not journal.requests


@pytest.mark.parametrize(
    "changes",
    [
        {"nonce": "foreign"},
        {"valid": False},
        {"owner": True},
        {"rect": [0, 0, float("nan"), 4]},
        {"rect": [0, 0, 0, 4]},
        {"visible": "yes"},
        {"flags": "20"},
    ],
)
def test_untrusted_menu_metadata_never_becomes_owner_proof(
    changes: dict[str, object],
) -> None:
    """Malformed or foreign observations cannot authorize a native request."""
    with pytest.raises(ValueError, match=r"invalid|unrelated"):
        decode_menu_state(menu_reply(**changes), "scope", 100)


def test_native_menu_requires_flags_owner_visibility_foreground_and_freshness() -> None:
    """An existing HMENU alone is insufficient preservation evidence."""
    state = decode_menu_state(menu_reply(), "scope", 100)
    assert not state.errors(42, 100.5)
    for changed in (
        replace(state, flags=0),
        replace(state, menu_owner=3),
        replace(state, visible=False),
        replace(state, foreground=7),
    ):
        assert changed.errors(42, 100.5)
    assert state.errors(42, 102)
    peer = MenuPeer()
    peer._read(StringIO("x" * 4097 + "\n"))
    assert peer.error
    assert peer.state is None


@pytest.mark.parametrize("area", [Rect(24, 24, 40, 30), Rect(-5000, -5000, 40, 30)])
def test_declared_fixture_selection_preserves_original_harness_contract(
    qapp: QApplication,
    qtbot: QtBot,
    area: Rect,
) -> None:
    """An unsafe rectangle cancels before any driver instruction is issued."""
    scene, context = ExistingScene("popup", qapp), CaptureContext(qapp)
    driver = SemanticDriver(context)
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, tuple(scene.windows)),
        driver.send,
        outcome="drag",
        budgets=HarnessBudgets(selection_area=area),
    )
    try:
        harness.start()
        context.service.capture_region()
        qtbot.waitUntil(lambda: harness.done)
        scene.assert_preserved()
        assert not context.service.is_selecting
        if area.x < 0:
            assert harness.errors
            assert not driver.sent
        else:
            assert not harness.errors
            assert harness.captures[0].area == area
    finally:
        harness.close()
        scene.close()
        context.service.deleteLater()


def test_post_terminal_menu_receipt_requires_correlated_request() -> None:
    """A recent queued pre-terminal observation cannot establish final state."""
    peer = MenuPeer()
    peer.owner = 42
    peer.process = cast("subprocess.Popen[str]", SimpleNamespace(poll=lambda: None))
    peer._sequence = 8
    peer.state = decode_menu_state(menu_reply(sequence=7), "scope", 100)
    assert not peer.acknowledged(8)
    peer.state = decode_menu_state(menu_reply(sequence=8), "scope", 100)
    assert peer.acknowledged(8)
    assert not peer.acknowledged(0)


def test_idle_facade_does_not_authorize_foreign_teardown() -> None:
    """Independent native cleanup is mandatory even when the facade says idle."""
    driver = ProductionBrokerDriver(InputProfile.DEFAULT)
    worker = driver.worker(Point(0, 0), Rect(0, 0, 100, 100))
    assert not cleanup_confirmed(driver, selecting=False)
    worker.stopped.set()
    assert not cleanup_confirmed(driver, selecting=False)
    worker.cleaned = True
    assert cleanup_confirmed(driver, selecting=False)
    assert not cleanup_confirmed(driver, selecting=True)


def test_native_action_receipt_is_scoped_to_current_run() -> None:
    """Old packets and hook installation cannot manufacture a requested pair."""
    driver = ProductionBrokerDriver(InputProfile.DEFAULT)
    packet = SimpleNamespace(
        escape_down=1, escape_up=1, left_down=1, left_up=1, mouse_moves=1
    )
    driver.platforms.append(cast(ObservedWin32Platform, packet))
    assert not native_action_errors(driver, "escape")
    assert not native_action_errors(driver, "drag")
    assert native_action_errors(driver, "escape", start=1)
    packet.escape_up = 0
    packet.left_up = 0
    assert native_action_errors(driver, "escape")
    assert native_action_errors(driver, "drag")


def test_unsolicited_cancel_is_not_declared_escape(qapp: QApplication) -> None:
    """A terminal signal arriving before an instruction fails qualification."""
    scene, context = ExistingScene("popup", qapp), CaptureContext(qapp)
    harness = CaptureHarness(
        context.service,
        PreservationSnapshot(qapp, tuple(scene.windows)),
        lambda *_: None,
        outcome="escape",
    )
    try:
        harness.start()
        harness._terminal()
        harness.advance()
        assert harness.done
        assert any("before declared" in error for error in harness.errors)
    finally:
        harness.close()
        scene.close()
        context.service.deleteLater()


@pytest.mark.parametrize("mode", [CaptureMode.FULLSCREEN, CaptureMode.REGION])
def test_memory_lane_never_persists_unexpected_pixels(
    make_app: AppFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: CaptureMode,
) -> None:
    """Unexpected full-frame/Escape images cannot reach any application sink."""
    app = make_app(
        settings=replace(
            Settings(),
            capture=CaptureSettings(
                open_editor=True, save_to_file=True, copy_to_clipboard=True
            ),
        )
    )
    sink = MemoryCaptureSink()
    monkeypatch.setattr(app.controller, "deliver_reviewed_capture", sink.accept)
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    before = QApplication.clipboard().image()
    app.controller.capture.captured.emit(
        Capture(
            image,
            mode,
            Rect(0, 0, 640, 480),
            "unexpected",
            datetime.now(UTC),
            preserve_foreground=True,
        )
    )
    assert sink.count == 1
    assert QApplication.clipboard().image() == before
    assert not list(tmp_path.rglob("*.png"))
    assert not app.controller.workspace.windows


def test_foreign_foreground_refuses_native_instruction() -> None:
    """A setup activation refusal cannot target a normal user window."""
    assert not fixture_foreground_errors(42, (42, 43))
    assert fixture_foreground_errors(100, (42, 43))
    assert fixture_foreground_errors(0, (42, 43))


def test_strict_fixture_snapshot_detects_focus_and_menu_action_changes(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity changes cannot hide in informational transition traces."""
    menu = QMenu()
    first, second = menu.addAction("First"), menu.addAction("Second")
    menu.setActiveAction(first)
    focus = QWidget()
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: focus))
    snapshot = StrictFixtureSnapshot(qapp, (menu,))
    try:
        menu.setActiveAction(second)
        monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: None))
        errors = snapshot.errors()
        assert "Fixture focus widget changed" in errors
        assert "Fixture menu action changed" in errors
    finally:
        snapshot.close()
        menu.deleteLater()
        focus.deleteLater()


def test_exited_peer_cannot_reuse_cached_open_state() -> None:
    """A dead child cannot acknowledge a request that was never sent."""
    peer = MenuPeer()
    peer.process = cast("subprocess.Popen[str]", SimpleNamespace(poll=lambda: 0))
    peer.owner = 42
    peer._sequence = 7
    peer.state = decode_menu_state(menu_reply(sequence=7), "scope", 100)
    assert peer.poll() is None
    assert not peer.acknowledged(7)
    assert peer.preservation_errors()


def test_foreign_cleanup_write_error_is_retained_after_child_exit() -> None:
    """A broken control pipe cannot silently become a clean fixture result."""
    closed: list[bool] = []

    def broken_write(_text: str) -> None:
        raise OSError("fixture pipe closed")

    stream = SimpleNamespace(
        write=broken_write, flush=lambda: None, close=lambda: closed.append(True)
    )
    child = SimpleNamespace(
        poll=lambda: None,
        stdin=stream,
        stdout=None,
        stderr=None,
        wait=lambda **_: 0,
        returncode=0,
    )
    peer = MenuPeer()
    peer.process = cast("subprocess.Popen[str]", child)
    with pytest.raises(OSError, match="cleanup request failed") as error:
        peer.close()
    assert isinstance(error.value.__cause__, OSError)
    assert closed == [True]
