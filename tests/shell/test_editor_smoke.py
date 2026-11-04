"""Editor-only smoke isolation contracts; all widget work uses offscreen QPA."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import override

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageWriter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot
from tests.editor.test_tab_bar import mouse_move
from tests.editor.test_workspace import add_rectangle

from verdiclip import editor_smoke, verification
from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.geometry import Rect
from verdiclip.settings import SettingsStore
from verdiclip.verification import PreviewProfile

COMMIT = "a" * 40


def test_self_check_cannot_start_gui_or_consult_normal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Self-check and rejected manifests cannot construct a GUI or live source."""

    def forbidden(*_args: object) -> None:
        pytest.fail("Smoke self-check constructed a GUI or live service")

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"mode": "editor-only-smoke", "commit": COMMIT}))
    monkeypatch.setattr(editor_smoke, "QApplication", forbidden)
    monkeypatch.setattr(verification, "MssScreenSource", forbidden)
    monkeypatch.setattr(verification, "WindowLocator", forbidden)
    monkeypatch.setattr(SettingsStore, "default_path", forbidden)
    assert editor_smoke.main(["--manifest", str(manifest), "--self-check"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["capture"] is receipt["scrolling"] is False
    assert receipt["label"] == "VerdiClip Editor Smoke aaaaaaaa"
    assert receipt["instance_name"].endswith("-EditorOnly")
    assert receipt["native_ui_ready"] is receipt["native_scrolling_ready"] is False
    assert not (tmp_path / "state").exists()
    manifest.write_text(
        json.dumps(
            {"mode": "editor-only-smoke", "commit": COMMIT, "native_ui_ready": True}
        )
    )
    assert editor_smoke.main(["--manifest", str(manifest), "--self-check"]) == 2
    assert "false readiness" in capsys.readouterr().err


def test_real_workspace_preserves_editor_only_policy_through_transfer(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real detach retains disabled actions and private delivery paths."""
    del qapp

    def forbidden(*_args: object) -> None:
        pytest.fail(
            "Editor smoke touched a normal setting or constructed a live service"
        )

    monkeypatch.setattr(SettingsStore, "default_path", forbidden)
    monkeypatch.setattr(verification, "PreviewController", forbidden)
    monkeypatch.setattr(verification, "MssScreenSource", forbidden)
    monkeypatch.setattr(verification, "WindowLocator", forbidden)
    smoke = editor_smoke.EditorSmoke(PreviewProfile(tmp_path, COMMIT))
    smoke.open_synthetic()
    try:
        assert len(smoke.workspace.documents) == 2
        host = smoke.workspace.windows[0]
        page = host.pages[0]
        assert smoke.workspace.move(host, page)
        assert len(smoke.workspace.documents) == 2
        assert len(smoke.workspace.windows) == 2
        smoke.workspace.windows[-1].hide()
        assert not smoke.workspace.windows[-1].isVisible()
        smoke.handle_forwarded([])
        assert smoke.workspace.windows[-1].isVisible()
        for window in smoke.workspace.windows:
            assert smoke.label in window.windowTitle()
            for name in ("capture_scrolling", "settings", "about", "help", "print"):
                assert not window.action(name).isEnabled()
            for document in window.pages:
                assert document.record.saved_path is None
                assert (
                    document.record.delivery.settings.directory == tmp_path / "output"
                )
        assert smoke.settings.hotkeys.region == ""
        assert not smoke.settings.updates.check
        assert not smoke.settings.updates.install
        assert not smoke.settings.startup.run_at_login
        assert not smoke.settings.integration.open_with
        assert not smoke.settings.integration.start_menu
        assert (tmp_path / "state" / "settings.json").is_file()
        assert not (tmp_path / "logs").exists()
    finally:
        for window in smoke.workspace.windows:
            window.set_confirm_close(confirm=False)
            window.close()
        smoke.workspace.parking.deleteLater()


def test_user_import_is_a_copy_and_default_save_stays_private(
    qapp: QApplication, tmp_path: Path, sample_image: QImage
) -> None:
    """Saving an imported document cannot overwrite the chosen source by default."""
    del qapp
    original = tmp_path / "original.png"
    assert sample_image.save(str(original))
    original_bytes = original.read_bytes()
    smoke = editor_smoke.EditorSmoke(PreviewProfile(tmp_path / "bundle", COMMIT))
    smoke.open_file(original)
    try:
        host = smoke.workspace.windows[0]
        assert host.saved_path is None
        assert host.save()
        assert host.saved_path is not None
        assert host.saved_path.parent == tmp_path / "bundle" / "output"
        assert original.read_bytes() == original_bytes
    finally:
        for window in smoke.workspace.windows:
            window.set_confirm_close(confirm=False)
            window.close()
        smoke.workspace.parking.deleteLater()


@pytest.mark.parametrize("scene", ["synthetic", "import"])
def test_launch_lifetime_uses_only_private_editor_and_retires_ipc(
    qapp: QApplication,
    tmp_path: Path,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    scene: str,
) -> None:
    """The real composition runs behind a fake event loop/IPC, never native UI."""
    del qapp
    closed: list[bool] = []
    connections: list[object] = []
    created: list[editor_smoke.EditorSmoke] = []
    implementation = editor_smoke.EditorSmoke

    class Application:
        """Control event-loop exit without changing the actual offscreen app."""

        @classmethod
        def instance(cls) -> "Application":
            return cls()

        def setApplicationName(self, name: str) -> None:  # noqa: N802
            assert name == "VerdiClip Editor Smoke aaaaaaaa"

        def setQuitOnLastWindowClosed(self, value: bool) -> None:  # noqa: N802, FBT001
            assert value is True

        def exec(self) -> int:
            assert len(created) == 1
            return 17

    def compose(profile: PreviewProfile) -> editor_smoke.EditorSmoke:
        smoke = implementation(profile)
        created.append(smoke)
        return smoke

    def instance(name: str) -> SimpleNamespace:
        assert name.endswith("-EditorOnly")
        return SimpleNamespace(
            forward=lambda _files: False,
            listen=lambda: True,
            close=lambda: closed.append(True),
            message_received=SimpleNamespace(connect=connections.append),
        )

    monkeypatch.setattr(editor_smoke, "QApplication", Application)
    monkeypatch.setattr(editor_smoke, "EditorSmoke", compose)
    monkeypatch.setattr(editor_smoke, "SingleInstance", instance)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"mode": "editor-only-smoke", "commit": COMMIT}))
    arguments = ["--manifest", str(manifest)]
    if scene == "import":
        selected = tmp_path / "selected.png"
        assert sample_image.save(str(selected))
        arguments.append(str(selected))
    try:
        assert editor_smoke.main(arguments) == 17
        assert closed == [True]
        assert len(connections) == 1
        assert len(created[0].workspace.documents) == (2 if scene == "synthetic" else 1)
        assert all(
            record.saved_path is None for record in created[0].workspace.documents
        )
        assert (tmp_path / "logs" / "verification.log").exists()
        assert not (tmp_path / "spool").exists()
    finally:
        for smoke in created:
            for window in smoke.workspace.windows:
                window.set_confirm_close(confirm=False)
                window.close()
            smoke.workspace.parking.deleteLater()


@pytest.mark.parametrize("failed_save", [False, True])
def test_smoke_checklist_retains_edits_through_cancel_and_save_close(  # noqa: PLR0915 - one user journey preserves state across every transition
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_save: bool,
) -> None:
    """Automate the accepted smoke journey, including failed-save recovery."""
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    smoke = editor_smoke.EditorSmoke(PreviewProfile(tmp_path, COMMIT))
    smoke.open_synthetic()
    owner = smoke.workspace
    host = owner.windows[0]
    page, neighbor = host.pages
    try:
        host.activate_page(page)
        add_rectangle(page)
        page.canvas.set_zoom(1.75)
        edited = host.flattened()
        selection = page.record.session.selection.ids
        host.activate_page(neighbor)
        host.activate_page(page)
        assert host.flattened() == edited
        host.tab_bar.moveTab(0, 1)
        assert host.pages == (neighbor, page)
        assert owner.move(host, page)
        detached = owner.host_of(page.record.identifier)
        assert detached is not None
        assert detached is not host
        assert detached.flattened() == edited
        assert owner.move(detached, page, host, 0)
        assert host.pages == (page, neighbor)
        assert owner.windows == (host,)
        host.activate_page(page)
        assert host.canvas.zoom == 1.75
        assert page.record.session.selection.ids == selection
        assert host.flattened() == edited
        host.action("undo").trigger()
        assert not page.record.session.document.annotations
        host.action("redo").trigger()
        assert host.flattened() == edited

        bar = host.tab_bar
        cancelled: list[bool] = []
        bar.drag_cancelled.connect(lambda: cancelled.append(True))
        QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
        mouse_move(bar, 40)
        QTest.keyClick(bar, Qt.Key.Key_Escape)
        qtbot.wait(0)
        assert cancelled == [True]
        assert host.pages == (page, neighbor)
        assert len(owner.documents) == 2
        assert host.flattened() == edited

        assert host.save()
        saved = page.record.saved_path
        assert saved is not None
        assert saved.parent == tmp_path / "output"
        assert QImage(str(saved)).convertToFormat(edited.format()) == edited
        saved_bytes = saved.read_bytes()
        page.record.session.history.execute(
            AddAnnotations(
                [RectangleShape(rect=Rect(220, 180, 90, 70))], "Distinct edit"
            )
        )
        newer = host.flattened()
        assert newer != edited
        assert len(page.record.session.document.annotations) == 2
        monkeypatch.setattr(ClosePrompt, "ask", lambda *_: CloseChoice.CANCEL)
        assert not host.close_tab(0)
        assert host.pages == (page, neighbor)
        assert not page.record.session.history.is_delivered
        assert host.flattened() == newer
        monkeypatch.setattr(ClosePrompt, "ask", lambda *_: CloseChoice.SAVE)
        if failed_save:

            class FailingWriter(QImageWriter):
                @override
                def write(self, image: QImage) -> bool:
                    """Fail real atomic encoding after writing temporary bytes."""
                    del image
                    self.device().write(b"partial")
                    return False

            with monkeypatch.context() as patch:
                patch.setattr("verdiclip.output.delivery.QImageWriter", FailingWriter)
                assert not host.close_tab(0)
            assert saved.read_bytes() == saved_bytes
            assert host.pages == (page, neighbor)
            assert not page.record.session.history.is_delivered
            assert host.flattened() == newer
        assert len(warnings) == int(failed_save)
        if warnings:
            assert "Could not save" in warnings[0]
        assert host.close_tab(0)
        assert host.pages == (neighbor,)
        assert len(owner.documents) == 1
        assert QImage(str(saved)).convertToFormat(newer.format()) == newer
        assert host.close_tab()
        assert not owner.documents
        assert not owner.windows
    finally:
        for window in owner.windows:
            window.set_confirm_close(confirm=False)
        owner.close_all()
        owner.parking.deleteLater()
        qtbot.wait(0)
