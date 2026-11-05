"""Production eligibility/ownership with synthetic files and fake OS services."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from tests.capture.test_scroll_uia_protocol import Payload
from tests.ux.test_app_journeys import (
    make_app as make_app,  # noqa: PLC0414 - explicit pytest fixture re-export
)

from verdiclip import scrolling
from verdiclip.capture.scroll.native_backend import UiaFrameBackend
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_process import UiaProcess
from verdiclip.capture.scroll.uia_protocol import UiaReference
from verdiclip.exceptions import CaptureError
from verdiclip.shell.controller import AppController

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot
    from tests.ux.test_app_journeys import AppFactory


@pytest.fixture
def eligible_assets(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Declare synthetic host/files, without launching them or changing real QPA."""
    del qapp
    directory = tmp_path / "synthetic-helper"
    directory.mkdir()
    for name in scrolling.HELPER_ASSETS:
        (directory / name).write_text("synthetic; never executed", encoding="utf-8")
    monkeypatch.setattr(scrolling, "HOST_PLATFORM", "win32")
    monkeypatch.setattr(
        scrolling,
        "QGuiApplication",
        SimpleNamespace(instance=object, platformName=lambda: "windows"),
    )
    monkeypatch.setattr(
        UiaProcess,
        "_default_command",
        staticmethod(lambda: ("synthetic-host", str(directory / "uia_helper.ps1"))),
    )
    return directory


@pytest.mark.parametrize("missing", scrolling.HELPER_ASSETS)
def test_each_missing_asset_declines_before_allocating_services(
    eligible_assets: Path, missing: str, tmp_path: Path
) -> None:
    """An incomplete package never enables scrolling or creates spool storage."""
    (eligible_assets / missing).unlink()
    root = tmp_path / "application-data"
    assert scrolling.scrolling_unavailable_reason() is not None
    assert (
        scrolling.compose_scrolling(cast(AppController, object()), data_root=root)
        is None
    )
    assert not root.exists()


@pytest.mark.parametrize(
    ("platform", "qpa"), [("linux", "windows"), ("win32", "offscreen")]
)
def test_unsupported_runtime_declines_without_resources(
    eligible_assets: Path,
    platform: str,
    qpa: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OS and native Qt platform are independent mandatory eligibility gates."""
    del eligible_assets
    monkeypatch.setattr(scrolling, "HOST_PLATFORM", platform)
    monkeypatch.setattr(scrolling.QGuiApplication, "platformName", lambda: qpa)
    root = tmp_path / "application-data"
    assert (
        scrolling.compose_scrolling(cast(AppController, object()), data_root=root)
        is None
    )
    assert not root.exists()


def test_missing_standard_host_declines(
    eligible_assets: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing command validation failure is an unsupported capability."""
    del eligible_assets

    def unavailable() -> tuple[str, ...]:
        raise CaptureError("synthetic missing host")

    monkeypatch.setattr(UiaProcess, "_default_command", staticmethod(unavailable))
    assert scrolling.scrolling_unavailable_reason() is not None


def test_spool_escape_rejects_before_adapter_creation(
    eligible_assets: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A substituted spool cannot redirect capture files outside application data."""
    del eligible_assets
    root = tmp_path / "application-data"
    original = Path.resolve

    def resolve(path: Path, *_args: object, **_kwargs: object) -> Path:
        return tmp_path / "foreign" if path == root / "scroll-spool" else original(path)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="application data"):
        scrolling.compose_scrolling(cast(AppController, object()), data_root=root)
    assert not root.exists()


def test_spool_creation_failure_is_explicit_before_adapter_creation(
    eligible_assets: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Storage failure is a capture capability error, not partial ownership."""
    del eligible_assets
    failure = OSError("synthetic storage refusal")

    def refuse(_path: Path, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises(CaptureError, match="private scrolling storage") as caught:
        scrolling.compose_scrolling(cast(AppController, object()), data_root=tmp_path)
    assert caught.value.__cause__ is failure


def test_normal_controller_composes_before_start_without_starting_a_helper(
    eligible_assets: Path,
    make_app: AppFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual normal controller start accepts the owned idle composition seam."""
    del eligible_assets
    original = AppController.start
    seen: list[AppController] = []

    def start(controller: AppController) -> None:
        workflow = scrolling.compose_scrolling(controller, data_root=tmp_path)
        assert workflow is not None
        assert workflow.parent() is controller.capture
        assert workflow._spool_parent == tmp_path / "scroll-spool"
        adapter = workflow._adapter
        assert adapter.parent() is workflow
        assert adapter._process.parent() is adapter
        assert adapter._process.worker_pids == ()
        assert adapter._process._scope is None
        assert not adapter.busy
        backend = workflow._backend_factory(
            UiaReference.decode(Payload.reference()), adapter
        )
        try:
            assert isinstance(backend, UiaFrameBackend)
            assert backend._options.frame_parent is None
            guard = backend._options.input_unchanged
            assert guard is not None
            assert guard() is controller.capture.input_active is False
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
        seen.append(controller)
        original(controller)

    monkeypatch.setattr(AppController, "start", start)
    app = make_app()
    assert seen == [app.controller]
    assert app.controller._scroll_workflow is not None
    assert list((tmp_path / "scroll-spool").iterdir()) == []


@pytest.mark.parametrize("partial", [False, True])
def test_attachment_failure_disposes_only_proven_unowned_adapter(
    eligible_assets: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    *,
    partial: bool,
) -> None:
    """Unexpected partial ownership remains intact for explicit caller recovery."""
    del eligible_assets
    adapters: list[UiaAdapter] = []
    owner = QObject()

    def adapter_factory(*, process: UiaProcess) -> UiaAdapter:
        adapter = UiaAdapter(process=process)
        adapters.append(adapter)
        return adapter

    def reject(adapter: UiaAdapter, *_args: object) -> None:
        if partial:
            adapter.setParent(owner)
            raise RuntimeError("synthetic partial attachment")
        raise ValueError("synthetic pre-attachment rejection")

    monkeypatch.setattr(scrolling, "UiaAdapter", adapter_factory)
    controller = cast(AppController, SimpleNamespace(configure_scrolling=reject))
    try:
        with pytest.raises(RuntimeError if partial else ValueError):
            scrolling.compose_scrolling(controller, data_root=tmp_path)
        assert len(adapters) == 1
        if partial:
            assert isValid(adapters[0])
            assert adapters[0].parent() is owner
            assert adapters[0]._process.worker_pids == ()
        else:
            qtbot.waitUntil(lambda: not isValid(adapters[0]))
    finally:
        owner.deleteLater()
