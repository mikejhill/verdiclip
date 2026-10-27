"""Deterministic bind-before-arm contracts; no native API or OS input."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from PySide6.QtCore import QElapsedTimer
from PySide6.QtWidgets import QApplication, QWidget
from tests.capture.production_matrix import (
    CASES,
    DriverJournal,
    InputProfile,
    ProductionBrokerDriver,
)
from tests.capture.test_broker_native import NativeTransport, ProductionRun
from tests.capture.test_capture_native import NativeRun
from tests.ux.test_app_journeys import AppFactory
from tests.ux.test_app_journeys import make_app as make_app  # noqa: PLC0414

from verdiclip.geometry import Rect


def test_popup_fixture_has_normal_discoverable_owner_before_binding(
    qapp: QApplication,
    make_app: AppFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A popup-only fixture is replaced by an ordinary editor-owned popup."""
    app = make_app()
    journal = DriverJournal(tmp_path, CASES[0])
    driver = ProductionBrokerDriver(InputProfile.DEFAULT)
    run = ProductionRun(app, qapp, NativeTransport(journal, driver), CASES[0], None)
    queued: list[object] = []
    monkeypatch.setattr(run, "schedule", queued.append)
    try:
        run._menu_scene("tray")
        menu = app.controller.tray.menu
        anchor = menu.parentWidget()
        assert anchor is not None
        assert anchor in app.controller.workspace.windows
        assert anchor.isWindow()
        assert anchor.isVisible()
        assert anchor in run.hosts
        assert menu.isVisible()
        assert len(queued) == 1
        assert not driver.workers
        assert journal.binding is None
        menu.hide()
        with pytest.raises(ValueError, match="popup was lost"):
            run.selection_area()
    finally:
        run.close()


def test_scope_is_revalidated_before_any_native_foreground_query(
    qapp: QApplication,
    make_app: AppFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Geometry changing after acknowledgement fails before capture admission."""
    app = make_app()
    journal = DriverJournal(tmp_path, CASES[0])
    journal.owned_handles = (42,)
    journal.binding_handle = 42
    original = Rect(1, 1, 20, 20)
    journal.prepare_binding(original)
    assert journal.binding is not None
    (tmp_path / "driver-bound.json").write_text(
        json.dumps({**journal.binding, "phase": "bound-no-input"})
    )
    assert journal.binding_ready(original)
    driver = ProductionBrokerDriver(InputProfile.DEFAULT)
    run = ProductionRun(app, qapp, NativeTransport(journal, driver), CASES[0], None)
    monkeypatch.setattr(run, "selection_area", lambda: Rect(2, 1, 20, 20))

    def forbidden(_name: str, **_kwargs: object) -> None:
        pytest.fail("Stale driver scope reached a native admission query")

    monkeypatch.setattr(
        "tests.capture.test_broker_native.ctypes.WinDLL", forbidden, raising=False
    )
    try:
        with pytest.raises(ValueError, match="scope changed"):
            run.budgets()
        assert not driver.workers
    finally:
        run.close()


@pytest.mark.parametrize(
    "timing",
    [
        (0, False),
        (19_999, False),
        (20_000, False),
        (0, True),
        (19_999, True),
        (20_000, True),
    ],
)
def test_binding_precedes_capture_and_late_binding_cannot_arm(  # noqa: PLR0915 - explicit before/after phase oracles
    qapp: QApplication,
    make_app: AppFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timing: tuple[int, bool],
) -> None:
    """Discovery gets its own deadline; it cannot spend the capture deadline."""
    elapsed, during_ack = timing
    app = make_app()
    journal = DriverJournal(tmp_path, CASES[0])
    journal.owned_handles = (42,)
    journal.binding_handle = 42
    driver = ProductionBrokerDriver(InputProfile.DEFAULT)
    run = ProductionRun(app, qapp, NativeTransport(journal, driver), CASES[0], None)
    host = QWidget()
    host.show()
    qapp.processEvents()
    area = Rect(-20, 50, 30, 20)
    monkeypatch.setattr(run, "selection_area", lambda: area)
    armed: list[object] = []
    monkeypatch.setattr(
        NativeRun, "schedule", lambda _self, action: armed.append(action)
    )
    clock = {"ms": 0}
    run._binding_elapsed = cast(
        QElapsedTimer, SimpleNamespace(start=lambda: None, elapsed=lambda: clock["ms"])
    )

    def action() -> None:
        pass

    try:
        run.schedule(action)
        run._binding_poll.stop()
        run._await_binding()
        assert journal.binding is not None
        assert not armed
        assert not driver.workers
        assert not journal.requests
        assert not (tmp_path / "driver-request.json").exists()
        acknowledgement = {**journal.binding, "phase": "bound-no-input"}
        (tmp_path / "driver-bound.json").write_text(json.dumps(acknowledgement))
        clock["ms"] = elapsed
        if during_ack:
            original_ready = journal.binding_ready

            def slow_ready(region: Rect) -> bool:
                ready = original_ready(region)
                clock["ms"] = 20_000
                return ready

            monkeypatch.setattr(journal, "binding_ready", slow_ready)
        run._await_binding()
        if elapsed < 20_000 and not during_ack:
            assert armed == [action]
            assert journal.bound
            assert not run.done
        else:
            assert armed == []
            assert run.done
            assert journal.retired
            assert run.errors == [
                "Driver target binding deadline expired before capture"
            ]
            run._await_binding()
            assert armed == []
        # Even readiness acknowledgement alone cannot create completion evidence.
        assert not driver.workers
        assert not journal.requests
    finally:
        run.close()
        host.close()


def test_malformed_binding_fails_before_native_worker_and_retirement_is_final(
    qapp: QApplication,
    make_app: AppFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreign acknowledgement and post-cancel callbacks never reach arming."""
    app = make_app()
    journal = DriverJournal(tmp_path, CASES[0])
    journal.owned_handles = (42,)
    journal.binding_handle = 42
    driver = ProductionBrokerDriver(InputProfile.DEFAULT)
    run = ProductionRun(app, qapp, NativeTransport(journal, driver), CASES[0], None)
    host = QWidget()
    host.show()
    qapp.processEvents()
    area = Rect(1, 1, 30, 20)
    monkeypatch.setattr(run, "selection_area", lambda: area)
    armed: list[object] = []
    monkeypatch.setattr(
        NativeRun, "schedule", lambda _self, action: armed.append(action)
    )
    try:
        run.schedule(lambda: None)
        run._binding_poll.stop()
        run._await_binding()
        assert journal.binding is not None
        (tmp_path / "driver-bound.json").write_text(
            json.dumps({**journal.binding, "nonce": "old", "phase": "bound-no-input"})
        )
        run._await_binding()
        assert run.done
        assert journal.retired
        assert run.errors == ["Driver binding acknowledgement is stale or unrelated"]
        (tmp_path / "driver-bound.json").write_text(
            json.dumps({**journal.binding, "phase": "bound-no-input"})
        )
        run._await_binding()
        assert not journal.binding_ready(area)
        assert not armed
        assert not driver.workers
        with pytest.raises(ValueError, match="bind"):
            journal.request("escape", area)
        assert not (tmp_path / "driver-request.json").exists()
    finally:
        run.close()
        host.close()
