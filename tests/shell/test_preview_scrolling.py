"""Qualified composition is prepared offscreen without providers or input."""

from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from shiboken6 import isValid
from tests.capture.test_scroll_uia_protocol import Payload
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindows

from verdiclip.capture.scroll.native_backend import UiaFrameBackend
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_protocol import UiaReference
from verdiclip.capture.service import CaptureService
from verdiclip.preview_scrolling import (
    compose_preview_scrolling,
    native_preview_adapter,
)
from verdiclip.shell.controller import AppController
from verdiclip.verification import PreviewController, PreviewProfile


def test_default_has_no_spool_or_adapter(tmp_path: Path) -> None:
    """Unqualified composition must return before constructing any resource."""

    def forbidden() -> UiaAdapter:
        pytest.fail("Unqualified profile constructed native services")

    # The disabled path need not construct a controller or QApplication either.
    assert (
        compose_preview_scrolling(
            cast(AppController, object()),
            qualified=False,
            bundle_root=tmp_path,
            adapter_factory=forbidden,
        )
        is None
    )
    assert not (tmp_path / "spool").exists()


def test_scrolling_flag_requires_native_ui_readiness(tmp_path: Path) -> None:
    """Scrolling cannot be qualified while native presentation is held."""
    with pytest.raises(ValueError, match="requires native UI"):
        PreviewProfile(tmp_path, "a" * 40, native_scrolling_ready=True)


def test_native_adapter_constructs_no_worker(qapp: QApplication) -> None:
    """Idle composition owns no process or endpoint."""
    del qapp
    adapter = native_preview_adapter()
    try:
        assert not adapter.busy
        assert adapter._process.parent() is adapter
        assert adapter._process.worker_pid is None
        assert adapter._process._lease is None
    finally:
        adapter.deleteLater()


def test_composition_owns_backend_spool_and_live_input_predicate(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real ownership and the live input predicate are both retained."""
    del qapp
    controller = PreviewController(
        PreviewProfile(tmp_path, "a" * 40), FakeDesktop(), FakeWindows([])
    )
    adapter = UiaAdapter()
    try:
        workflow = compose_preview_scrolling(
            controller,
            qualified=True,
            bundle_root=tmp_path,
            adapter_factory=lambda: adapter,
        )
        assert workflow is not None
        assert adapter.parent() is workflow
        assert workflow.parent() is controller.capture
        assert workflow._spool_parent == tmp_path / "spool"
        backend = workflow._backend_factory(
            UiaReference.decode(Payload.reference()), adapter
        )
        try:
            assert isinstance(backend, UiaFrameBackend)
            assert backend.unavailable_reason is None
            assert backend._options.frame_parent == tmp_path / "spool"
            guard = backend._options.input_unchanged
            assert guard is not None
            assert not guard()
            monkeypatch.setattr(
                CaptureService, "input_active", property(lambda _: True)
            )
            assert guard()
            other = UiaAdapter()
            try:
                with pytest.raises(ValueError, match="own discovery"):
                    workflow._backend_factory(
                        UiaReference.decode(Payload.reference()), other
                    )
            finally:
                other.deleteLater()
        finally:
            backend.deleteLater()
        assert adapter._process.worker_pid is None
    finally:
        controller.shutdown()
        controller.workspace.parking.deleteLater()


def test_profile_flag_is_wired_into_actual_preview_controller(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actual preview constructor invokes its prepared composition."""
    del qapp
    calls: list[tuple[bool, Path]] = []

    def compose(
        _controller: PreviewController, *, qualified: bool, bundle_root: Path
    ) -> None:
        calls.append((qualified, bundle_root))

    monkeypatch.setattr("verdiclip.verification.compose_preview_scrolling", compose)
    controller = PreviewController(
        PreviewProfile(
            tmp_path, "a" * 40, native_ui_ready=True, native_scrolling_ready=True
        ),
        FakeDesktop(),
        FakeWindows([]),
    )
    try:
        assert calls == [(True, tmp_path)]
        assert controller.profile.describe()["native_scrolling_ready"] is True
    finally:
        controller.shutdown()
        controller.workspace.parking.deleteLater()


@pytest.mark.parametrize("owned", [False, True])
def test_declared_rejection_disposes_only_unowned_adapter(
    qapp: QApplication,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    owned: bool,
) -> None:
    """Rejected composition cannot destroy an existing Qt ownership graph."""
    del qapp
    controller = PreviewController(
        PreviewProfile(tmp_path, "a" * 40), FakeDesktop(), FakeWindows([])
    )
    owner = QObject()
    adapter = UiaAdapter()
    if owned:
        adapter.setParent(owner)

    def reject(*_args: object) -> None:
        raise ValueError("Declared rejection")

    monkeypatch.setattr(controller, "configure_scrolling", reject)
    try:
        with pytest.raises(ValueError, match="Declared rejection"):
            compose_preview_scrolling(
                controller,
                qualified=True,
                bundle_root=tmp_path,
                adapter_factory=lambda: adapter,
            )
        if owned:
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert isValid(adapter)
            assert adapter.parent() is owner
        else:
            qtbot.waitUntil(lambda: not isValid(adapter))
    finally:
        owner.deleteLater()
        controller.shutdown()
        controller.workspace.parking.deleteLater()


def test_spool_escape_refuses_before_resource_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An escaped spool cannot create an adapter or external directory."""
    original = Path.resolve

    def resolve(path: Path, *_args: object, **_kwargs: object) -> Path:
        if path == tmp_path / "spool":
            return tmp_path.parent / "outside-spool"
        return original(path)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="inside its bundle"):
        compose_preview_scrolling(
            cast(AppController, object()),
            qualified=True,
            bundle_root=tmp_path,
            adapter_factory=lambda: pytest.fail("Created native adapter"),
        )
    assert not (tmp_path / "spool").exists()
