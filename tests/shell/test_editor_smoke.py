"""Editor-only smoke isolation contracts; all widget work uses offscreen QPA."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from verdiclip import editor_smoke, verification
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
