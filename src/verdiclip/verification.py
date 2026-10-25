"""Isolated local verification composition; never upgrade the normal install."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from importlib.metadata import version
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import override

from PySide6.QtCore import qVersion
from PySide6.QtWidgets import QApplication

from verdiclip.capture.grabber import MssScreenSource, ScreenSource
from verdiclip.capture.service import WindowSource
from verdiclip.editor.window import EditorWindow
from verdiclip.platform.associations import FileAssociations, RegistryValue
from verdiclip.platform.hotkeys import HotkeyService
from verdiclip.platform.shortcuts import Shortcut, ShortcutRegistration
from verdiclip.platform.startup import StartupRegistration
from verdiclip.platform.windows import WindowLocator
from verdiclip.settings import (
    HotkeySettings,
    IntegrationSettings,
    Settings,
    SettingsStore,
    StartupSettings,
    UpdateSettings,
)
from verdiclip.shell.controller import AppController, ShellIntegration
from verdiclip.shell.instance import SingleInstance


@dataclass(frozen=True, slots=True)
class PreviewProfile:
    """Derive every private path and instance name from one bundle manifest."""

    root: Path
    commit: str
    native_ui_ready: bool = False

    def __post_init__(self) -> None:
        """Reject ambiguous build identity and resolve one private bundle root."""
        if re.fullmatch(r"[0-9a-f]{40}", self.commit) is None:
            raise ValueError("Preview manifest requires a full lowercase commit SHA")
        object.__setattr__(self, "root", self.root.resolve())

    @classmethod
    def load(cls, manifest: Path) -> PreviewProfile:
        """Read readiness from the reviewed bundle, not an override CLI flag."""
        payload: object = json.loads(manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("commit"), str):
            raise TypeError("Preview manifest requires a commit")
        return cls(
            manifest.resolve().parent,
            payload["commit"],
            payload.get("native_ui_ready") is True,
        )

    @property
    def label(self) -> str:
        """Identify every visible host without changing document titles."""
        return f"VerdiClip Preview {self.commit[:8]}"

    @property
    def instance_name(self) -> str:
        """Never forward to the normal application's default instance."""
        location = hashlib.sha256(str(self.root).casefold().encode()).hexdigest()[:12]
        return f"VerdiClip-Preview-{self.commit[:12]}-{location}"

    @property
    def settings_path(self) -> Path:
        """Keep preferences and adjacent style memory inside the bundle."""
        return self.root / "state" / "settings.json"

    def settings(self, value: Settings) -> Settings:
        """Keep preview settings incapable of enabling Windows integrations."""
        return replace(
            value,
            output=replace(value.output, directory=self.root / "output"),
            hotkeys=HotkeySettings("", "", "", ""),
            startup=StartupSettings(run_at_login=False),
            integration=IntegrationSettings(open_with=False, start_menu=False),
            updates=UpdateSettings(check=False, install=False),
        )

    def describe(self) -> dict[str, str | bool]:
        """Self-check imports/paths/runtime without constructing QApplication."""
        return {
            "commit": self.commit,
            "native_ui_ready": self.native_ui_ready,
            "label": self.label,
            "instance_name": self.instance_name,
            "settings": str(self.settings_path),
            "styles": str(self.settings_path.with_name("styles.json")),
            "output": str(self.root / "output"),
            "logs": str(self.root / "logs"),
            "python": sys.version.split()[0],
            "pyside6": version("pyside6-essentials"),
            "qt": qVersion(),
            "mss": version("mss"),
        }


class PreviewShortcuts:
    """An empty private shortcut namespace with writes unavailable."""

    def read(self, path: Path) -> Shortcut | None:
        """No installed shortcut is consulted."""
        del path
        return None

    def write(self, path: Path, shortcut: Shortcut) -> None:
        """Refuse a preview registration rather than pretending it succeeded."""
        del path, shortcut
        raise OSError("Windows integration is unavailable in this preview")

    def delete(self, path: Path) -> None:
        """The private virtual namespace contains nothing to remove."""
        del path


class PreviewRunKey:
    """An empty private legacy namespace; never load winreg."""

    def read_value(self, name: str) -> str | None:
        """No normal Run-key value is read."""
        del name
        return None

    def write_value(self, name: str, value: str) -> None:
        """Refuse persistence outside the preview state."""
        del name, value
        raise OSError("Windows integration is unavailable in this preview")

    def delete_value(self, name: str) -> None:
        """The private virtual namespace contains nothing to remove."""
        del name


class PreviewAssociations:
    """Keep even direct make-default actions away from the real registry."""

    def read_value(self, key: str, name: str) -> str | None:
        """No normal association is read."""
        del key, name
        return None

    def write_value(self, value: RegistryValue) -> None:
        """Refuse a preview registration."""
        del value
        raise OSError("Windows integration is unavailable in this preview")

    def delete_value(self, key: str, name: str) -> None:
        """The private virtual namespace has no values."""
        del key, name

    def delete_tree(self, key: str) -> None:
        """The private virtual namespace has no trees."""
        del key

    def notify_changed(self) -> None:
        """No Explorer notification is sent."""


class PreviewHotkeys:
    """Never register or release a normal application's global shortcut."""

    def register(self, hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
        """Fail closed if a caller bypasses the empty-binding settings policy."""
        del hwnd, hotkey_id, modifiers, vk
        return False

    def unregister(self, hwnd: int, hotkey_id: int) -> bool:
        """This adapter never owns a real registration."""
        del hwnd, hotkey_id
        return True


class PreviewController(AppController):
    """Use production document behavior with private, constrained composition."""

    def __init__(
        self, profile: PreviewProfile, screens: ScreenSource, windows: WindowSource
    ) -> None:
        self.profile = profile
        store = SettingsStore(profile.settings_path)
        store.save(profile.settings(store.load()))
        shortcuts = PreviewShortcuts()
        private = profile.root / "disabled-integration"
        super().__init__(
            store,
            screens,
            windows,
            HotkeyService(PreviewHotkeys()),
            ShellIntegration(
                StartupRegistration(
                    ShortcutRegistration(private / "startup", shortcuts),
                    PreviewRunKey(),
                    value_name=profile.instance_name,
                ),
                FileAssociations(PreviewAssociations()),
                ShortcutRegistration(private / "programs", shortcuts),
            ),
        )
        self.tray.setToolTip(profile.label)

    @override
    def _wire_editor(self, editor: EditorWindow) -> None:
        super()._wire_editor(editor)
        editor.set_application_label(self.profile.label)

    @override
    def apply_settings(self, settings: Settings) -> None:
        """Keep integrations disabled after every settings edit too."""
        super().apply_settings(self.profile.settings(settings))

    @override
    def make_default_image_app(self) -> None:
        """Do not open Windows Settings or register the preview as an image app."""
        self._notify(
            self.profile.label, "Windows integration is disabled in this preview."
        )


@contextmanager
def preview_logging(profile: PreviewProfile) -> Generator[None]:
    """Retain candidate diagnostics only inside its own bundle."""
    directory = profile.root / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "verification.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    logger = logging.getLogger("verdiclip")
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()


def run_gui(profile: PreviewProfile, files: list[Path]) -> int:
    """Launch only a qualified bundle; this function is never a self-check."""
    if not profile.native_ui_ready:
        raise ValueError("This bundle has not passed native UI/capture qualification")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    if not isinstance(app, QApplication):
        raise TypeError("Preview requires a widget application")
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(profile.label)
    instance = SingleInstance(profile.instance_name)
    if instance.forward([str(path.resolve()) for path in files]):
        return 0
    if not instance.listen():
        raise ValueError("Could not reserve the preview instance")
    try:
        with preview_logging(profile):
            controller = PreviewController(profile, MssScreenSource(), WindowLocator())
            instance.message_received.connect(controller.handle_forwarded)
            app.aboutToQuit.connect(controller.shutdown)
            try:
                controller.start()
                for path in files:
                    controller.open_image(path)
                return app.exec()
            finally:
                app.aboutToQuit.disconnect(controller.shutdown)
                controller.shutdown()
    finally:
        instance.close()


def main(argv: list[str] | None = None) -> int:
    """Default to a noninteractive runtime check; readiness cannot be overridden."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-ui", action="store_true")
    parser.add_argument("files", type=Path, nargs="*")
    args = parser.parse_args(argv)
    try:
        profile = PreviewProfile.load(args.manifest)
        if not args.run_ui:
            sys.stdout.write(json.dumps(profile.describe(), indent=2) + "\n")
            return 0
        return run_gui(profile, args.files)
    except (OSError, ValueError, TypeError) as error:
        sys.stderr.write(f"Preview unavailable: {error}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
