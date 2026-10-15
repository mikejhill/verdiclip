"""Tests for the style bar."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from verdiclip.editor.style_bar import LABEL_MARGINS, StyleBar


def test_field_labels_keep_clear_of_the_separator_before_them(qtbot: QtBot) -> None:
    """Each field label has room on its left, so "Text" doesn't touch the bar edge."""
    bar = StyleBar()
    qtbot.addWidget(bar)

    labels = bar.findChildren(QLabel)

    assert len(labels) == 3
    for label in labels:
        margins = label.contentsMargins()
        assert margins.left() == LABEL_MARGINS[0] > 0
        assert margins.right() == LABEL_MARGINS[2]
