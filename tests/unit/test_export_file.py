"""Tests for verdiclip.export.file_export.FileExporter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdiclip.export.file_export import FileExporter

if TYPE_CHECKING:
    from pathlib import Path


class TestGetNextCounterEmptyDir:
    def test_get_next_counter_empty_dir(self, tmp_path: Path) -> None:
        assert FileExporter._get_next_counter(tmp_path, "png") == 1


class TestGetNextCounterWithFiles:
    def test_get_next_counter_with_files(self, tmp_path: Path) -> None:
        (tmp_path / "shot_001.png").touch()
        (tmp_path / "shot_002.png").touch()
        (tmp_path / "shot_003.png").touch()
        assert FileExporter._get_next_counter(tmp_path, "png") == 4

    def test_ignores_other_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "image.jpg").touch()
        (tmp_path / "image.bmp").touch()
        (tmp_path / "image.png").touch()
        assert FileExporter._get_next_counter(tmp_path, "png") == 2
