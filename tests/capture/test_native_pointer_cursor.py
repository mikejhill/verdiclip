"""Portable known-static cursor equivalence and independent cleanup controls."""

from asyncio import CancelledError
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from tests.capture.native_pointer_cursor import (
    Appearance,
    IconInfo,
    NativeReader,
    cleanup,
    copy_appearance,
)


def reader() -> Mock:
    """Use synthetic private pixels/handles, never a desktop cursor."""
    value = Mock()
    value.icon.return_value = IconInfo(0, 0, 0, 11, 0)
    value.bitmap.side_effect = lambda _handle, **_kw: (2, 4, bytes(4))
    return value


def test_equivalent_shape_does_not_require_handle_equality() -> None:
    """Different opaque cursor handles may describe the same static appearance."""
    first, second = reader(), reader()
    assert copy_appearance(21, first) == copy_appearance(22, second)
    assert first.delete.call_args_list == second.delete.call_args_list
    assert [call.args[0] for call in first.delete.call_args_list] == [11]


@pytest.mark.parametrize("field", ["width", "height", "hotspot", "mask", "color"])
def test_shape_or_hotspot_difference_is_not_restoration(field: str) -> None:
    """Only complete bounded pixel/hotspot equality satisfies the logical oracle."""
    initial = Appearance(2, 2, (0, 0), bytes(16), bytes(16))
    changes = {"width": 3, "height": 3, "hotspot": (1, 0), "mask": b"x", "color": b"x"}
    assert initial != replace(initial, **{field: changes[field]})


def test_bitmap_read_failure_still_deletes_both_copies() -> None:
    """Reading failure cannot skip independent copy retirement."""
    value = reader()
    value.bitmap.side_effect = RuntimeError("read-failed")
    with pytest.raises(RuntimeError, match="read-failed"):
        copy_appearance(21, value)
    assert [call.args[0] for call in value.delete.call_args_list] == [11]


@pytest.mark.parametrize("error", [RuntimeError("cleanup-failed"), KeyboardInterrupt()])
def test_color_cleanup_failure_still_attempts_mask(error: BaseException) -> None:
    """Control signals propagate, and peer bitmap cleanup remains independent."""
    value = reader()
    value.icon.return_value.color = 12
    value.delete.side_effect = [error, None]
    with pytest.raises(type(error)):
        cleanup((lambda: value.delete(12), lambda: value.delete(11)))
    assert [call.args[0] for call in value.delete.call_args_list] == [12, 11]


@pytest.mark.parametrize("size", [(129, 4), (2, 258), (0, 4)])
def test_oversized_result_is_not_accepted(size: tuple[int, int]) -> None:
    """Unknown or oversized appearances cannot become equality proof."""
    value = reader()
    value.bitmap.side_effect = lambda _handle, **_kw: (*size, b"")
    with pytest.raises(RuntimeError, match="dimensions-unsupported"):
        copy_appearance(21, value)
    assert value.delete.call_count == 1


def test_private_bytes_do_not_appear_in_repr() -> None:
    """Failure diagnostics must not accidentally emit cursor pixels."""
    value = Appearance(2, 2, (0, 0), b"private-pixels", None)
    assert "private-pixels" not in repr(value)


@pytest.mark.parametrize("control", [KeyboardInterrupt(), CancelledError()])
def test_primary_control_survives_two_cleanup_failures(control: BaseException) -> None:
    """An already pending control remains primary after independent cleanup."""
    calls: list[int] = []

    def failed(number: int) -> None:
        calls.append(number)
        raise OSError("cleanup-failed")

    def interrupted() -> None:
        try:
            raise control
        finally:
            cleanup((lambda: failed(1), lambda: failed(2)))

    with pytest.raises(type(control)) as caught:
        interrupted()
    assert caught.value is control
    assert calls == [1, 2]


def native_reader() -> tuple[NativeReader, Mock, Mock]:
    """Inject actual adapter call boundaries without loading Windows DLLs."""
    value = object.__new__(NativeReader)
    user, gdi = Mock(), Mock()
    value.user, value.gdi = user, gdi

    def metadata(_handle: int, size: int, pointer: object) -> int:
        item = cast(SimpleNamespace, pointer)._obj
        item.width, item.height, item.planes, item.bits = 16, 32, 1, 1
        return size

    gdi.GetObjectW.side_effect = metadata
    gdi.GetDIBits.return_value = 32
    user.GetDC.return_value = 17
    user.ReleaseDC.return_value = 1
    return value, user, gdi


def test_native_one_bit_copy_is_bounded_and_releases_dc() -> None:
    """Actual adapter requests bounded one-bit top-down data, not RGBA conversion."""
    value, user, gdi = native_reader()
    width, height, pixels = value.bitmap(11, mask=True)
    assert (width, height, len(pixels)) == (16, 32, 64)
    args = gdi.GetDIBits.call_args.args
    assert args[2:4] == (0, 32)
    assert len(args[4]) == 128
    info = cast(SimpleNamespace, args[5])._obj
    assert info.header.bits == 1
    assert info.header.height == -32
    user.ReleaseDC.assert_called_once_with(None, 17)


@pytest.mark.parametrize("fault", ["width", "height", "planes", "bits"])
def test_native_invalid_bitmap_never_allocates_dc(fault: str) -> None:
    """Validate actual native metadata before bounded pixel allocation."""
    value, user, gdi = native_reader()
    original = gdi.GetObjectW.side_effect

    def metadata(handle: int, size: int, pointer: object) -> int:
        result = original(handle, size, pointer)
        setattr(cast(SimpleNamespace, pointer)._obj, fault, 999)
        return int(result)

    gdi.GetObjectW.side_effect = metadata
    with pytest.raises(RuntimeError, match="format-unsupported"):
        value.bitmap(11, mask=True)
    user.GetDC.assert_not_called()
    gdi.GetDIBits.assert_not_called()


def test_native_partial_copy_releases_dc() -> None:
    """Partial GDI output is never treated as complete appearance."""
    value, user, gdi = native_reader()
    gdi.GetDIBits.return_value = 31
    with pytest.raises(RuntimeError, match="copy-incomplete"):
        value.bitmap(11, mask=True)
    user.ReleaseDC.assert_called_once_with(None, 17)


def test_native_control_survives_release_failure() -> None:
    """ReleaseDC failure cannot replace a pending interruption."""
    value, user, gdi = native_reader()
    gdi.GetDIBits.side_effect = KeyboardInterrupt
    user.ReleaseDC.return_value = 0
    with pytest.raises(KeyboardInterrupt):
        value.bitmap(11, mask=True)
    user.ReleaseDC.assert_called_once_with(None, 17)
