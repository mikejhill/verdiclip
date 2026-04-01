"""Tests for verdiclip.__main__."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from verdiclip.__main__ import _setup_logging, main

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


class TestSetupLogging:
    """_setup_logging configures the verdiclip logger."""

    @pytest.fixture(autouse=True)
    def _cleanup_handlers(self) -> Generator[None, None, None]:
        """Remove handlers added during each test."""
        logger = logging.getLogger("verdiclip")
        original_handlers = list(logger.handlers)
        yield
        for handler in logger.handlers[:]:
            if handler not in original_handlers:
                handler.close()
                logger.removeHandler(handler)

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        with patch("verdiclip.__main__.Path.home", return_value=tmp_path):
            _setup_logging()
        log_dir = tmp_path / "AppData" / "Roaming" / "VerdiClip" / "logs"
        assert log_dir.is_dir(), "Expected log_dir.is_dir() to be truthy"

    def test_adds_rotating_file_handler(self, tmp_path: Path) -> None:
        with patch("verdiclip.__main__.Path.home", return_value=tmp_path):
            _setup_logging()
        logger = logging.getLogger("verdiclip")
        assert any(isinstance(h, RotatingFileHandler) for h in logger.handlers), (
            "Assertion failed: any((isinstance(h, RotatingFileHandler) ..."
        )

    def test_adds_stream_handler(self, tmp_path: Path) -> None:
        with patch("verdiclip.__main__.Path.home", return_value=tmp_path):
            _setup_logging()
        logger = logging.getLogger("verdiclip")
        stream_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        ]
        assert len(stream_handlers) >= 1, (
            f"Expected len(stream_handlers) >= 1, got {len(stream_handlers)}"
        )

    def test_root_logger_level_is_debug(self, tmp_path: Path) -> None:
        with patch("verdiclip.__main__.Path.home", return_value=tmp_path):
            _setup_logging()
        logger = logging.getLogger("verdiclip")
        assert logger.level == logging.DEBUG, (
            f"Expected logger.level to equal logging.DEBUG, got {logger.level}"
        )

    def test_logger_has_at_least_two_handlers(self, tmp_path: Path) -> None:
        with patch("verdiclip.__main__.Path.home", return_value=tmp_path):
            _setup_logging()
        logger = logging.getLogger("verdiclip")
        assert len(logger.handlers) >= 2, (
            f"Expected len(logger.handlers) >= 2, got {len(logger.handlers)}"
        )


class TestMain:
    """main() orchestrates logging setup and app launch."""

    def test_invokes_setup_logging_and_runs_app(self) -> None:
        mock_app = MagicMock()
        mock_app.run.return_value = 0

        with (
            patch("verdiclip.__main__._setup_logging") as mock_setup,
            patch("verdiclip.app.VerdiClipApp", return_value=mock_app) as mock_cls,
            patch("sys.exit") as mock_exit,
            patch("sys.argv", ["verdiclip"]),
        ):
            main()

        mock_setup.assert_called_once()
        mock_cls.assert_called_once()
        mock_app.run.assert_called_once()
        mock_exit.assert_called_once_with(0)
