"""Keep app dialogs from blocking the capture overlays."""

from __future__ import annotations

from typing import override

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QWindow


class ModalityGuard(QObject):
    """Turn application-modal windows into window-modal ones as they show.

    Qt's default for a dialog's ``exec()`` (and ``QMessageBox.about`` and
    friends) is application modality, which blocks every other window,
    including the capture overlays a hotkey opens on top of the dialog.
    Window modality still blocks the dialog's parent (an editor), and blocks
    nothing for a dialog without one (from the tray). Applying it as each
    window shows covers every dialog, present and future (UX-G-10).
    """

    def __init__(self, app: QCoreApplication) -> None:
        super().__init__(app)
        app.installEventFilter(self)

    @classmethod
    def ensure(cls) -> None:
        """Install one guard on the running application, if not already."""
        app = QCoreApplication.instance()
        # One filter per app: each sees every event the app delivers
        if app is not None and app.findChild(cls) is None:
            cls(app)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Downgrade a modal window before Qt starts blocking with it."""
        # A QWindow's show event precedes Qt's modal bookkeeping for it
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QWindow)
            and watched.modality() == Qt.WindowModality.ApplicationModal
        ):
            watched.setModality(Qt.WindowModality.WindowModal)
        return False
