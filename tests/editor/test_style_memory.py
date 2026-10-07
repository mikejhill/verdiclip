"""Tests for remembering per-tool styles."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from verdiclip.document.document import Document
from verdiclip.document.style import Color, Style
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.editor.style_memory import StyleMemory
from verdiclip.settings import EditorSettings


class TestStyleMemory:
    """Tests for StyleMemory."""

    def test_remembered_style_round_trips(self, tmp_path: Path) -> None:
        """A remembered style comes back from a fresh instance."""
        style = Style(stroke=Color(1, 2, 3), fill=Color(4, 5, 6, 7), width=9)
        StyleMemory(tmp_path / "styles.json").remember(ToolId.RECTANGLE, style)

        loaded = StyleMemory(tmp_path / "styles.json").load()

        assert loaded == {ToolId.RECTANGLE: style}

    def test_missing_or_corrupt_file_means_nothing_remembered(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unreadable memory is ignored with a warning, never an error."""
        path = tmp_path / "styles.json"
        missing = StyleMemory(path).load()
        path.write_text("{not json", encoding="utf-8")

        corrupt = StyleMemory(path).load()

        assert missing == {}
        assert corrupt == {}
        assert "Ignoring unreadable" in caplog.text

    def test_bad_entries_are_skipped_good_ones_kept(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """One invalid tool entry does not lose the others."""
        memory = StyleMemory(tmp_path / "styles.json")
        memory.remember(ToolId.ARROW, Style(width=7))
        text = memory.path.read_text(encoding="utf-8")
        memory.path.write_text(
            text.replace("{", '{"nonsense": {"stroke": 1}, ', 1), encoding="utf-8"
        )

        loaded = memory.load()

        assert loaded == {ToolId.ARROW: Style(width=7)}
        assert "nonsense" in caplog.text

    def test_forget_and_remember_all(self, tmp_path: Path) -> None:
        """Forget clears everything; remember_all replaces everything."""
        memory = StyleMemory(tmp_path / "styles.json")
        memory.remember_all({ToolId.TEXT: Style(font_size=40)})
        replaced = memory.load()

        memory.forget()

        assert replaced == {ToolId.TEXT: Style(font_size=40)}
        assert memory.load() == {}

    def test_write_failure_is_logged_not_raised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Remembering into an unwritable place never interrupts editing."""
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")

        StyleMemory(blocker / "styles.json").remember(ToolId.LINE, Style())

        assert "Could not remember" in caplog.text


class TestSessionRemembersStyles:
    """Sessions start from remembered styles and report changes."""

    def test_session_uses_remembered_and_reports_changes(self, tmp_path: Path) -> None:
        """A new session starts from memory; style edits are written back."""
        memory = StyleMemory(tmp_path / "styles.json")
        memory.remember(ToolId.RECTANGLE, Style(stroke=Color(0, 0, 255)))
        image = QImage(10, 10, QImage.Format.Format_RGB32)
        session = EditorSession(
            Document(image),
            EditorSettings(),
            remembered=memory.load(),
            on_style_change=memory.remember,
        )
        started_blue = session.styles.get(ToolId.RECTANGLE).stroke

        session.styles.set(ToolId.HIGHLIGHT, Style(fill=Color(0, 255, 0)))

        assert started_blue == Color(0, 0, 255)
        assert memory.load()[ToolId.HIGHLIGHT].fill == Color(0, 255, 0)
