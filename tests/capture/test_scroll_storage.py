"""Lossless page goldens and fault injection for private capture spooling."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import override

import pytest
from PySide6.QtGui import QImage
from tests.capture.test_scroll_core import Documents

from verdiclip.capture.scroll.models import ScrollLimitError, ScrollLimits
from verdiclip.capture.scroll.storage import PngStripWriter, StripStore
from verdiclip.exceptions import CaptureError


class InterruptedWriter(PngStripWriter):
    """Fail a later tile after an earlier tile in that transaction was written."""

    def __init__(self, fail_at: int) -> None:
        self._writes = 0
        self._fail_at = fail_at

    @override
    def write(self, image: QImage, path: Path) -> None:
        """Write real PNGs until the injected disk/encoder failure."""
        self._writes += 1
        if self._writes == self._fail_at:
            raise CaptureError("Injected encoder interruption")
        super().write(image, path)


def test_page_crossing_tile_boundaries_matches_original(tmp_path: Path) -> None:
    """Tiled review has the same pixels as a contiguous source crop."""
    source = Documents.image(height=1300)
    store = StripStore(tmp_path, ScrollLimits())
    try:
        store.append(source)
        assert store.materialize(430, 700) == source.copy(0, 430, 96, 700)
    finally:
        store.close()


def test_partial_transaction_rolls_back_without_losing_prefix(tmp_path: Path) -> None:
    """A failure on tile two must not leave tile one appended or orphaned."""
    source = Documents.image(height=1000)
    store = StripStore(tmp_path, ScrollLimits(), writer=InterruptedWriter(3))
    try:
        store.append(source.copy(0, 0, 96, 120))
        with pytest.raises(CaptureError, match="interruption"):
            store.append(source.copy(0, 120, 96, 700))
        assert store.height == 120
        assert store.materialize() == source.copy(0, 0, 96, 120)
        directory = store.strips[0].path.parent
        assert sorted(directory.iterdir()) == [store.strips[0].path]
    finally:
        store.close()


def test_corrupt_tile_is_never_delivered(tmp_path: Path) -> None:
    """Integrity failure surfaces before pixels can enter the editor."""
    store = StripStore(tmp_path, ScrollLimits())
    try:
        store.append(Documents.image(height=120))
        store.strips[0].path.write_bytes(b"corrupt")
        with pytest.raises(CaptureError, match="integrity"):
            store.materialize()
    finally:
        store.close()


def test_storage_limit_is_checked_before_first_write(tmp_path: Path) -> None:
    """The encoder is not called when pessimistic bytes exceed the quota."""
    store = StripStore(
        tmp_path, ScrollLimits(max_disk_bytes=100), writer=InterruptedWriter(1)
    )
    try:
        with pytest.raises(ScrollLimitError, match="resource limit"):
            store.append(Documents.image(height=120))
        assert store.strips == ()
        assert list(tmp_path.iterdir()) == []
    finally:
        store.close()


def test_cleanup_deletes_only_its_own_private_folder(tmp_path: Path) -> None:
    """Closing twice cannot remove a sibling file or another session."""
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("Unrelated data", encoding="utf-8")
    other = StripStore(tmp_path, ScrollLimits())
    store = StripStore(tmp_path, ScrollLimits())
    try:
        other.append(Documents.image(height=120))
        store.append(Documents.image(height=120))
        store.close()
        store.close()
        assert sentinel.read_text(encoding="utf-8") == "Unrelated data"
        assert not other.materialize().isNull()
    finally:
        other.close()


@pytest.mark.parametrize(("top", "height"), [(-1, 20), (0, 0), (110, 20)])
def test_invalid_page_cannot_read_outside_committed_extent(
    tmp_path: Path, top: int, height: int
) -> None:
    """Coordinates are validated before a page allocation or file read."""
    store = StripStore(tmp_path, ScrollLimits())
    try:
        store.append(Documents.image(height=120))
        with pytest.raises(CaptureError, match="outside"):
            store.materialize(top, height)
    finally:
        store.close()


def test_materialization_accounts_for_full_image_copies(tmp_path: Path) -> None:
    """Raw bitmap bytes alone cannot authorize an unsafe editor allocation."""
    store = StripStore(tmp_path, ScrollLimits(max_materialize_bytes=96 * 120 * 4))
    try:
        store.append(Documents.image(height=120))
        with pytest.raises(CaptureError, match="page slices"):
            store.materialize()
        assert store.materialize(0, 15).height() == 15
    finally:
        store.close()


def test_encoder_error_is_visible_for_unwritable_destination(tmp_path: Path) -> None:
    """A missing output directory must not be mistaken for a saved PNG."""
    with pytest.raises(CaptureError, match="store capture pixels"):
        PngStripWriter().write(
            Documents.image(height=120), tmp_path / "missing" / "tile.png"
        )


def test_missing_tile_cannot_be_delivered(tmp_path: Path) -> None:
    """Concurrent removal of temporary data is an explicit delivery error."""
    store = StripStore(tmp_path, ScrollLimits())
    try:
        store.append(Documents.image(height=120))
        store.strips[0].path.unlink()
        with pytest.raises(CaptureError, match="unavailable"):
            store.materialize()
    finally:
        store.close()


def test_free_disk_reserve_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exhausted real disk cannot be bypassed by a generous session quota."""
    actual = shutil.disk_usage(tmp_path)
    no_free_space = type(actual)(actual.total, actual.total, 0)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: no_free_space)
    store = StripStore(tmp_path, ScrollLimits())
    try:
        with pytest.raises(ScrollLimitError, match="free disk"):
            store.append(Documents.image(height=120))
        assert store.strips == ()
    finally:
        store.close()


def test_width_change_cannot_mutate_manifest(tmp_path: Path) -> None:
    """A resize is rejected before any new tile is staged."""
    store = StripStore(tmp_path, ScrollLimits())
    try:
        store.append(Documents.image(height=120))
        with pytest.raises(CaptureError, match="width changed"):
            store.append(Documents.image(width=95, height=120))
        assert store.width == 96
        assert store.height == 120
    finally:
        store.close()


def test_closed_store_cannot_be_reused(tmp_path: Path) -> None:
    """A late producer cannot recreate storage after capture cancellation."""
    store = StripStore(tmp_path, ScrollLimits())
    store.close()
    with pytest.raises(CaptureError, match="closed"):
        store.append(Documents.image(height=120))
    assert not list(tmp_path.iterdir())


def test_cleanup_failure_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transient disk errors preserve the private folder identity for retry."""
    store = StripStore(tmp_path, ScrollLimits())
    store.append(Documents.image(height=120))

    def fail_cleanup(_temporary: tempfile.TemporaryDirectory[str]) -> None:
        """Simulate antivirus or another process temporarily holding a file."""
        raise PermissionError("Injected temporary lock")

    with monkeypatch.context() as context:
        context.setattr(tempfile.TemporaryDirectory, "cleanup", fail_cleanup)
        with pytest.raises(CaptureError, match="temporary capture storage"):
            store.close()
    store.close()
    assert not list(tmp_path.iterdir())
