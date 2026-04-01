"""About dialog with version info and Greenshot attribution."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from verdiclip import __app_name__, __version__


class AboutDialog(QDialog):
    """About dialog showing version, description, and attribution."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {__app_name__}")
        self.setFixedSize(420, 320)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Title
        title = QLabel(f"{__app_name__}")
        title_font = QFont("Segoe UI", 22, QFont.Weight.Bold)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Version
        version = QLabel(f"Version {__version__}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        # Description
        desc = QLabel("A performant screenshot and annotation tool for Windows.")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Attribution
        attribution = QLabel(
            '<p style="color: #666;">'
            "Inspired by <a href='https://getgreenshot.org/'>Greenshot</a>, "
            "the excellent open-source screenshot tool.<br>"
            '"Verdi" is Italian for "green" — a respectful nod to Greenshot.</p>'
            "<p>VerdiClip is an independent, clean-room implementation. "
            "No code or assets from Greenshot are used.</p>"
        )
        attribution.setAlignment(Qt.AlignmentFlag.AlignCenter)
        attribution.setWordWrap(True)
        attribution.setOpenExternalLinks(True)
        attribution.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(attribution)

        # License
        license_label = QLabel("Licensed under the MIT License.")
        license_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(license_label)

        layout.addStretch()

        # Close button
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
