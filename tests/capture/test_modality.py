"""Test that modal windows are made window-modal as they show."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication

from verdiclip.capture.modality import ModalityGuard


class TestModalityGuard:
    """One guard per app, downgrading only application modality."""

    def test_ensure_installs_one_guard(self, qapp: QApplication) -> None:
        """Repeated calls (one per capture service) share one filter."""
        ModalityGuard.ensure()
        ModalityGuard.ensure()

        assert len(qapp.findChildren(ModalityGuard)) == 1

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            (Qt.WindowModality.ApplicationModal, Qt.WindowModality.WindowModal),
            (Qt.WindowModality.WindowModal, Qt.WindowModality.WindowModal),
            (Qt.WindowModality.NonModal, Qt.WindowModality.NonModal),
        ],
    )
    def test_showing_sets_window_modality(
        self,
        qapp: QApplication,
        before: Qt.WindowModality,
        after: Qt.WindowModality,
    ) -> None:
        """Only an application-modal window changes, and before Qt blocks."""
        del qapp
        ModalityGuard.ensure()
        window = QWindow()
        window.setModality(before)
        window.show()
        try:
            assert window.modality() == after
            assert QApplication.modalWindow() is (
                None if before == Qt.WindowModality.NonModal else window
            )
        finally:
            window.destroy()
