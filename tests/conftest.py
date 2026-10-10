"""Shared pytest configuration and fixtures for VerdiClip tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

from verdiclip.document.document import Document
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.session import EditorSession
from verdiclip.settings import EditorSettings, OutputSettings

WINDOWS_FONTS = Path("C:/Windows/Fonts")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add ``--ux-snapshots DIR`` to save a PNG for each journey step."""
    parser.addoption(
        "--ux-snapshots",
        default="",
        help="Directory to write UX journey step screenshots into.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Run Qt headless with real fonts so rendering matches the desktop."""
    del config
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if WINDOWS_FONTS.is_dir():
        os.environ.setdefault("QT_QPA_FONTDIR", str(WINDOWS_FONTS))


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer "Discard" to unsaved-change prompts so teardown never blocks."""
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.DISCARD)


@pytest.fixture(autouse=True)
def clean_clipboard() -> Iterator[None]:
    """Clear the clipboard after each Qt test.

    Qt's offscreen platform crashes at exit if the clipboard still owns
    Python-created QMimeData; real Windows does not.
    """
    yield
    if QGuiApplication.instance() is not None:
        QGuiApplication.clipboard().clear()


@pytest.fixture
def sample_image() -> QImage:
    """A 400x300 white image with a dark bar and a black square, for pixel checks."""
    image = QImage(400, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.fillRect(40, 40, 160, 20, QColor(30, 30, 30))
    painter.fillRect(300, 200, 50, 50, QColor("black"))
    painter.end()
    return image


@pytest.fixture
def document(sample_image: QImage) -> Document:
    """A document over ``sample_image``."""
    return Document(sample_image)


@pytest.fixture
def session(document: Document) -> EditorSession:
    """An editor session with default editor settings."""
    return EditorSession(document, EditorSettings())


@pytest.fixture
def output_settings(tmp_path: Path) -> OutputSettings:
    """Output settings that save into a temporary folder."""
    return OutputSettings(directory=tmp_path / "out")


@pytest.fixture
def snapshot_dir(request: pytest.FixtureRequest) -> Iterator[Path | None]:
    """Directory for UX snapshots, or None when not requested."""
    option = str(request.config.getoption("--ux-snapshots"))
    if not option:
        yield None
        return
    path = Path(option)
    path.mkdir(parents=True, exist_ok=True)
    yield path
