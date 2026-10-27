"""Isolated editor-only smoke application: no capture or shell services exist."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QImage, QImageReader, QPainter
from PySide6.QtWidgets import QApplication, QMessageBox

from verdiclip.document.document import Document
from verdiclip.editor.session import EditorSession
from verdiclip.editor.style_memory import StyleMemory
from verdiclip.editor.window import EditorOptions, EditorWindow
from verdiclip.editor.workspace import Workspace
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import SettingsStore
from verdiclip.shell.instance import SingleInstance
from verdiclip.verification import PreviewProfile, preview_logging


def _label(profile: PreviewProfile) -> str:
    return f"VerdiClip Editor Smoke {profile.commit[:8]}"


def _instance_name(profile: PreviewProfile) -> str:
    return profile.instance_name + "-EditorOnly"


class EditorSmoke:
    """Compose only document/editor services; never construct AppController."""

    def __init__(self, profile: PreviewProfile) -> None:
        if profile.native_ui_ready or profile.native_scrolling_ready:
            raise ValueError("Editor smoke requires unchanged false readiness flags")
        self.profile = profile
        self.label = _label(profile)
        store = SettingsStore(profile.settings_path)
        self.settings = profile.settings(store.load())
        store.save(self.settings)
        self.styles = StyleMemory(profile.settings_path.with_name("styles.json"))
        self.workspace = Workspace()
        self.workspace.window_added.connect(self._wire)

    def _wire(self, host: EditorWindow) -> None:
        host.set_application_label(self.label)
        # No capture, tray, hooks, registration, updater, associations or startup
        # object exists in this composition. Detached windows receive this too.
        for name in ("capture_scrolling", "settings", "about", "help", "print"):
            host.action(name).setEnabled(False)
        host.open_image_requested.connect(
            lambda path, target=host: self.open_file(path, target)
        )

    def open_image(
        self, image: QImage, title: str, target: EditorWindow | None = None
    ) -> EditorWindow:
        """Admit a copied image into the real document workspace."""
        if image.isNull():
            raise ValueError("Cannot open an empty smoke document")
        session = EditorSession(
            Document(image),
            self.settings.editor,
            remembered=self.styles.load(),
            on_style_change=self.styles.remember,
            delivered=True,
        )
        # Imported files are copies: no source_path can become an overwrite target.
        return self.workspace.open_document(
            session,
            ImageDelivery(self.settings.output),
            EditorOptions(title=title, confirm_close=True),
            target=target,
        )

    def open_file(self, path: Path, target: EditorWindow | None = None) -> None:
        """Read a user-chosen file without adopting its overwrite path."""
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            QMessageBox.warning(
                target,
                self.label,
                f"Could not read {path.name}: {reader.errorString()}",
            )
            return
        self.open_image(image, path.stem, target)

    def open_synthetic(self) -> None:
        """Create two distinctive documents without reading user content."""
        for index, color in enumerate(("#d8eefb", "#e5f5d4"), 1):
            image = QImage(800, 500, QImage.Format.Format_RGB32)
            image.fill(QColor(color))
            painter = QPainter(image)
            try:
                for x in range(40, 800, 80):
                    painter.fillRect(
                        x, 40, 35, 420, QColor("#315a7a" if index == 1 else "#45712e")
                    )
            finally:
                painter.end()
            self.open_image(image, f"Synthetic {index}")

    def handle_forwarded(self, files: list[str]) -> None:
        """Treat a second explicit launch as an open/restore user action."""
        for path in files:
            self.open_file(Path(path))
        if not files and self.workspace.windows:
            host = self.workspace.windows[-1]
            if host.isMinimized():
                host.showNormal()
            else:
                host.show()
            host.raise_()
            host.activateWindow()


def _load_profile(manifest: Path) -> PreviewProfile:
    payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("mode") != "editor-only-smoke":
        raise ValueError("A dedicated editor-only smoke manifest is required")
    profile = PreviewProfile.load(manifest)
    if profile.native_ui_ready or profile.native_scrolling_ready:
        raise ValueError("Editor smoke requires unchanged false readiness flags")
    return profile


def _run(profile: PreviewProfile, files: list[Path]) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    if not isinstance(app, QApplication):
        raise TypeError("Editor smoke requires a widget application")
    app.setApplicationName(_label(profile))
    app.setQuitOnLastWindowClosed(True)
    instance = SingleInstance(_instance_name(profile))
    if instance.forward([str(path.resolve()) for path in files]):
        return 0
    if not instance.listen():
        raise ValueError("Could not reserve the isolated editor smoke instance")
    try:
        with preview_logging(profile):
            smoke = EditorSmoke(profile)
            instance.message_received.connect(smoke.handle_forwarded)
            if files:
                for path in files:
                    smoke.open_file(path)
            else:
                smoke.open_synthetic()
            if not smoke.workspace.windows:
                return 2
            return app.exec()
    finally:
        instance.close()


def main(argv: list[str] | None = None) -> int:
    """Check the dedicated manifest or run only the isolated editor composition."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("files", type=Path, nargs="*")
    args = parser.parse_args(argv)
    try:
        profile = _load_profile(args.manifest)
        if args.self_check:
            sys.stdout.write(
                json.dumps(
                    {
                        **profile.describe(),
                        "label": _label(profile),
                        "instance_name": _instance_name(profile),
                        "mode": "editor-only-smoke",
                        "capture": False,
                        "scrolling": False,
                    }
                )
                + "\n"
            )
            return 0
        return _run(profile, args.files)
    except (OSError, ValueError, TypeError) as error:
        sys.stderr.write(f"Editor smoke unavailable: {error}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
