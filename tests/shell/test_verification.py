"""Offscreen preview composition cannot modify the normal app or capture input."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindows

from verdiclip import verification
from verdiclip.platform.associations import RegistryValue
from verdiclip.platform.shortcuts import Shortcut
from verdiclip.settings import (
    IntegrationSettings,
    Settings,
    SettingsStore,
    StartupSettings,
)
from verdiclip.verification import (
    PreviewAssociations,
    PreviewController,
    PreviewHotkeys,
    PreviewProfile,
    PreviewRunKey,
    PreviewShortcuts,
)

COMMIT = "a" * 40


def manifest(tmp_path: Path, **values: str | bool) -> Path:
    """Write only a test-owned bundle manifest."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"commit": COMMIT, **values}), encoding="utf-8")
    return path


def test_profile_paths_namespace_and_settings_are_private(tmp_path: Path) -> None:
    """Two bundles cannot collide with normal or each other's instances."""
    profile = PreviewProfile.load(manifest(tmp_path))
    other = PreviewProfile(tmp_path / "other", COMMIT)
    assert profile.instance_name != other.instance_name
    assert profile.instance_name != "VerdiClip"
    assert profile.settings_path == tmp_path / "state" / "settings.json"
    settings = profile.settings(Settings())
    assert settings.hotkeys.region == settings.hotkeys.window == ""
    assert settings.hotkeys.fullscreen == settings.hotkeys.repeat == ""
    assert settings.integration == IntegrationSettings(
        open_with=False, start_menu=False
    )
    assert not settings.startup.run_at_login
    assert not settings.updates.check
    assert not settings.updates.install
    assert settings.output.directory == tmp_path / "output"
    assert profile.label in profile.describe().values()


@pytest.mark.parametrize("commit", ["", "abcdef", "A" * 40])
def test_invalid_build_identity_is_rejected(tmp_path: Path, commit: str) -> None:
    """A preview cannot silently present an ambiguous candidate."""
    with pytest.raises(ValueError, match="commit SHA"):
        PreviewProfile(tmp_path, commit)


@pytest.mark.parametrize("payload", [[], {}, {"commit": 3}])
def test_invalid_manifest_is_rejected(tmp_path: Path, payload: object) -> None:
    """Malformed launch metadata fails before GUI/services are constructed."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(TypeError, match="requires a commit"):
        PreviewProfile.load(path)


def test_main_self_check_and_unqualified_ui_never_construct_app(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staged artifact remains noninteractive until native qualification."""
    path = manifest(tmp_path)

    def forbidden(*_args: object) -> None:
        pytest.fail("Self-check attempted to construct GUI or capture input")

    monkeypatch.setattr(verification, "QApplication", forbidden)
    monkeypatch.setattr(verification, "MssScreenSource", forbidden)
    monkeypatch.setattr(verification, "WindowLocator", forbidden)
    assert verification.main(["--manifest", str(path)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["native_ui_ready"] is False
    assert verification.main(["--manifest", str(path), "--run-ui"]) == 2
    assert "qualification" in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


def test_main_handles_unreadable_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absent manifest does not start the normal application instead."""
    assert verification.main(["--manifest", str(tmp_path / "missing")]) == 2
    assert "Preview unavailable" in capsys.readouterr().err


def test_guard_backends_never_touch_windows_integrations(tmp_path: Path) -> None:
    """Even settings-policy mistakes cannot construct a real backend here."""
    shortcut = PreviewShortcuts()
    assert shortcut.read(tmp_path) is None
    shortcut.delete(tmp_path)
    with pytest.raises(OSError, match="unavailable"):
        shortcut.write(
            tmp_path,
            Shortcut(
                tmp_path / "python.exe", "", tmp_path, tmp_path / "icon.ico", "Preview"
            ),
        )
    run = PreviewRunKey()
    assert run.read_value("name") is None
    run.delete_value("name")
    with pytest.raises(OSError, match="unavailable"):
        run.write_value("name", "value")
    assoc = PreviewAssociations()
    assert assoc.read_value("key", "name") is None
    assoc.delete_value("key", "name")
    assoc.delete_tree("key")
    assoc.notify_changed()
    with pytest.raises(OSError, match="unavailable"):
        assoc.write_value(RegistryValue("key", "name", "value"))
    keys = PreviewHotkeys()
    assert not keys.register(1, 2, 3, 4)
    assert keys.unregister(1, 2)


def test_preview_controller_keeps_policy_and_labels_after_detach(
    qapp: QApplication,
    tmp_path: Path,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real offscreen hosts use private state and never acquire live input."""
    del qapp

    def forbidden() -> Path:
        pytest.fail("Preview consulted the normal settings path")

    monkeypatch.setattr(SettingsStore, "default_path", forbidden)
    profile = PreviewProfile(tmp_path, COMMIT)
    controller = PreviewController(profile, FakeDesktop(), FakeWindows([]))
    controller.start()
    try:
        host = controller.open_editor(sample_image, title="Document", delivered=True)
        controller.open_editor(sample_image, title="Other", delivered=True)
        assert profile.label in host.windowTitle()
        assert controller.workspace.move(host, host.pages[0])
        assert all(
            profile.label in window.windowTitle()
            for window in controller.workspace.windows
        )
        assert controller.tray.toolTip() == profile.label
        controller.apply_settings(
            replace(
                Settings(),
                startup=StartupSettings(run_at_login=True),
                integration=IntegrationSettings(open_with=True, start_menu=True),
            )
        )
        assert controller.settings == profile.settings(Settings())
        controller.make_default_image_app()
        assert controller.pending_deliveries == ()
        assert not (tmp_path / "disabled-integration").exists()
        for window in controller.workspace.windows:
            with pytest.raises(ValueError, match="nonempty"):
                window.set_application_label(" ")
    finally:
        controller.workspace.close_all()
        controller.shutdown()
        controller.workspace.parking.deleteLater()


def test_ready_main_dispatches_to_explicit_preview_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ready candidate still uses only the dedicated composition root."""
    path = manifest(tmp_path, native_ui_ready=True)
    calls: list[PreviewProfile] = []

    def launch(profile: PreviewProfile, _files: list[Path]) -> int:
        calls.append(profile)
        return 7

    monkeypatch.setattr(verification, "run_gui", launch)
    assert verification.main(["--manifest", str(path), "--run-ui"]) == 7
    assert calls == [PreviewProfile(tmp_path, COMMIT, native_ui_ready=True)]


def test_run_gui_composes_only_fake_sources_offscreen(
    qapp: QApplication,
    tmp_path: Path,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real launch wiring without live capture or an event loop."""
    profile = PreviewProfile(tmp_path, COMMIT, native_ui_ready=True)
    image = tmp_path / "example.png"
    assert sample_image.save(str(image))
    created: list[PreviewController] = []
    original = PreviewController
    previous_name = qapp.applicationName()
    previous_quit = qapp.quitOnLastWindowClosed()

    def controller(
        profile: PreviewProfile, _screens: object, _windows: object
    ) -> PreviewController:
        result = original(profile, FakeDesktop(), FakeWindows([]))
        created.append(result)
        return result

    def finish() -> int:
        assert len(created) == 1
        assert created[0].editors[0].saved_path == image
        assert profile.label in created[0].editors[0].windowTitle()
        assert created[0].workspace.close_all()
        return 0

    monkeypatch.setattr(verification, "MssScreenSource", lambda: None)
    monkeypatch.setattr(verification, "WindowLocator", lambda: None)
    monkeypatch.setattr(verification, "PreviewController", controller)
    monkeypatch.setattr(qapp, "exec", finish)
    try:
        assert verification.run_gui(profile, [image]) == 0
        assert not created[0].workspace.has_work
        assert (tmp_path / "logs" / "verification.log").is_file()
    finally:
        qapp.setApplicationName(previous_name)
        qapp.setQuitOnLastWindowClosed(previous_quit)
        for item in created:
            item.workspace.close_all()
            item.shutdown()
            item.workspace.parking.deleteLater()


@pytest.mark.parametrize("outcome", ["forwarded", "unavailable"])
def test_run_gui_instance_outcomes_cannot_construct_capture(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Forward/failure exits happen before real screen/window sources exist."""
    previous_name = qapp.applicationName()
    previous_quit = qapp.quitOnLastWindowClosed()
    profile = PreviewProfile(tmp_path, COMMIT, native_ui_ready=True)

    class Instance:
        def __init__(self, name: str) -> None:
            assert name == profile.instance_name

        def forward(self, _files: list[str]) -> bool:
            return outcome == "forwarded"

        def listen(self) -> bool:
            return False

    def forbidden() -> None:
        pytest.fail("Early-exit preview constructed native capture sources")

    monkeypatch.setattr(verification, "SingleInstance", Instance)
    monkeypatch.setattr(verification, "MssScreenSource", forbidden)
    try:
        if outcome == "forwarded":
            assert verification.run_gui(profile, []) == 0
        else:
            with pytest.raises(ValueError, match="reserve"):
                verification.run_gui(profile, [])
    finally:
        qapp.setApplicationName(previous_name)
        qapp.setQuitOnLastWindowClosed(previous_quit)


def test_non_widget_application_is_rejected_before_sources(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflicting Qt application type is not a reason to create another."""
    del qapp
    with monkeypatch.context() as patch:
        patch.setattr(verification.QApplication, "instance", object)
        with pytest.raises(TypeError, match="widget application"):
            verification.run_gui(
                PreviewProfile(tmp_path, COMMIT, native_ui_ready=True), []
            )
