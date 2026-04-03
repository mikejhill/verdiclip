"""Shared test fixtures for VerdiClip tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from verdiclip.config import Config

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from verdiclip.editor.tools.base import BaseTool


# ---------------------------------------------------------------------------
# Application / config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    """Provide a QApplication instance for the entire test session."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def tmp_config(tmp_path: Path) -> Config:
    """Provide a Config instance backed by a temporary file."""
    config_path = tmp_path / "config.json"
    return Config(config_path=config_path)


@pytest.fixture()
def sample_config_file(tmp_path: Path) -> Path:
    """Create a sample config file and return its path."""
    config_path = tmp_path / "config.json"
    config_data = {
        "capture": {"default_action": "clipboard"},
        "save": {"default_format": "jpg", "jpg_quality": 75},
    }
    config_path.write_text(json.dumps(config_data), encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# Drawing / tool test helpers
# ---------------------------------------------------------------------------


def make_mouse_event(
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> MagicMock:
    """Create a mock QMouseEvent with the given button and modifiers."""
    event = MagicMock()
    event.button.return_value = button
    event.modifiers.return_value = modifiers
    return event


def simulate_draw(
    tool: BaseTool,
    scene: QGraphicsScene,
    view: QGraphicsView,
    start: QPointF,
    end: QPointF,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    """Activate a tool and simulate a full press-move-release sequence."""
    tool.activate(scene, view)
    press_event = make_mouse_event()
    move_event = make_mouse_event(modifiers=modifiers)
    release_event = make_mouse_event()
    tool.mouse_press(start, press_event)
    tool.mouse_move(end, move_event)
    tool.mouse_release(end, release_event)


def make_scene_with_background(
    width: int = 200, height: int = 200,
) -> tuple[QGraphicsScene, QGraphicsPixmapItem]:
    """Create a scene with a background QGraphicsPixmapItem at zValue -1000."""
    scene = QGraphicsScene()
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(100, 150, 200))
    bg = QGraphicsPixmapItem(pixmap)
    bg.setZValue(-1000)
    scene.addItem(bg)
    return scene, bg


@pytest.fixture()
def drawing_context(qapp) -> tuple[QGraphicsScene, QGraphicsView]:
    """Provide a fresh QGraphicsScene and QGraphicsView pair for tool tests."""
    scene = QGraphicsScene()
    view = QGraphicsView(scene)
    return scene, view


@pytest.fixture()
def drawing_context_with_bg(qapp) -> tuple[QGraphicsScene, QGraphicsView, QGraphicsPixmapItem]:
    """Provide a scene with a background pixmap, view, and the background item."""
    scene, bg = make_scene_with_background()
    view = QGraphicsView(scene)
    return scene, view, bg
