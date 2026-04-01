"""Shared test fixtures for VerdiClip tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from PySide6.QtWidgets import QApplication

from verdiclip.config import Config

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


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
