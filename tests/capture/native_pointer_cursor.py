"""Bounded private appearance comparison for known static test-fixture cursors."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys
from asyncio import CancelledError
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Protocol


class IconInfo(ctypes.Structure):
    """Documented GetIconInfo layout; returned bitmap copies are owned."""

    _fields_ = (
        ("icon", w.BOOL),
        ("hotspot_x", w.DWORD),
        ("hotspot_y", w.DWORD),
        ("mask", w.HBITMAP),
        ("color", w.HBITMAP),
    )


class Bitmap(ctypes.Structure):
    """Documented BITMAP layout, inspected without reading its pointer."""

    _fields_ = (
        ("kind", w.LONG),
        ("width", w.LONG),
        ("height", w.LONG),
        ("stride", w.LONG),
        ("planes", w.WORD),
        ("bits", w.WORD),
        ("data", ctypes.c_void_p),
    )


class BitmapHeader(ctypes.Structure):
    """BITMAPINFOHEADER for a bounded top-down one-bit BI_RGB copy."""

    _fields_ = (
        ("size", w.DWORD),
        ("width", w.LONG),
        ("height", w.LONG),
        ("planes", w.WORD),
        ("bits", w.WORD),
        ("compression", w.DWORD),
        ("image_size", w.DWORD),
        ("xppm", w.LONG),
        ("yppm", w.LONG),
        ("used", w.DWORD),
        ("important", w.DWORD),
    )


@dataclass(frozen=True, repr=False)
class Appearance:
    """Transient private bytes/hotspot; never serialized or included in repr."""

    width: int
    height: int
    hotspot: tuple[int, int]
    mask: bytes
    color: bytes | None


class Reader(Protocol):
    """Private copy ownership interface for portable cleanup controls."""

    def icon(self, cursor: int) -> IconInfo:
        """Copy the two bitmap objects from the known static fixture cursor."""

    def bitmap(self, handle: int, *, mask: bool) -> tuple[int, int, bytes]:
        """Read only a rigorously bounded supported bitmap format."""

    def delete(self, handle: int) -> None:
        """Delete the caller-owned bitmap copy, never the system cursor."""


def cleanup(actions: tuple[Callable[[], None], ...]) -> None:
    """Attempt every owned cleanup without replacing a pending control signal."""
    primary = sys.exception()
    errors: list[Exception | KeyboardInterrupt | SystemExit | CancelledError] = []
    for action in actions:
        try:
            action()
        except (Exception, KeyboardInterrupt, SystemExit, CancelledError) as error:  # noqa: BLE001 - retain failures after independent cleanup
            errors.append(error)
    if isinstance(primary, (KeyboardInterrupt, SystemExit, CancelledError)):
        return
    for error in errors:
        if isinstance(error, (KeyboardInterrupt, SystemExit, CancelledError)):
            raise error
    if errors and primary is None:
        raise errors[0]


def copy_appearance(cursor: int, reader: Reader) -> Appearance:
    """Copy only a caller-proven owned static monochrome fixture cursor."""
    if not cursor:
        raise RuntimeError("cursor-handle-unavailable")
    info = reader.icon(cursor)
    mask_handle, color_handle = int(info.mask or 0), int(info.color or 0)
    try:
        if info.icon or not mask_handle or color_handle:
            raise RuntimeError("cursor-format-unsupported")
        width, mask_height, mask = reader.bitmap(mask_handle, mask=True)
        if mask_height % 2:
            raise RuntimeError("cursor-monochrome-dimensions")
        height = mask_height // 2
        if (
            not 1 <= width <= 128
            or not 1 <= height <= 128
            or not 0 <= info.hotspot_x < width
            or not 0 <= info.hotspot_y < height
        ):
            raise RuntimeError("cursor-dimensions-unsupported")
        return Appearance(width, height, (info.hotspot_x, info.hotspot_y), mask, None)
    finally:
        handles = tuple(dict.fromkeys(h for h in (color_handle, mask_handle) if h))
        cleanup(tuple(partial(reader.delete, h) for h in handles))


class BitmapInfo(ctypes.Structure):
    """One-bit BI_RGB requires exactly two RGBQUAD color entries."""

    _fields_ = (("header", BitmapHeader), ("colors", w.DWORD * 2))


class NativeReader:
    """Read-only Win32 metadata/copies; no cursor writes, image files or network."""

    def __init__(self) -> None:
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user.GetIconInfo.argtypes = [w.HICON, ctypes.POINTER(IconInfo)]
        self.user.GetIconInfo.restype = w.BOOL
        self.user.GetDC.argtypes = [w.HWND]
        self.user.GetDC.restype = w.HDC
        self.user.ReleaseDC.argtypes = [w.HWND, w.HDC]
        self.user.ReleaseDC.restype = ctypes.c_int
        self.gdi.GetObjectW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p]
        self.gdi.GetObjectW.restype = ctypes.c_int
        self.gdi.GetDIBits.argtypes = [
            w.HDC,
            w.HBITMAP,
            w.UINT,
            w.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(BitmapInfo),
            w.UINT,
        ]
        self.gdi.GetDIBits.restype = ctypes.c_int
        self.gdi.DeleteObject.argtypes = [w.HANDLE]
        self.gdi.DeleteObject.restype = w.BOOL

    def icon(self, cursor: int) -> IconInfo:
        """GetIconInfo creates only copies that the caller must retire."""
        info = IconInfo()
        if not self.user.GetIconInfo(cursor, ctypes.byref(info)):
            raise RuntimeError("cursor-copy-unavailable")
        return info

    def bitmap(self, handle: int, *, mask: bool) -> tuple[int, int, bytes]:
        """Reject oversized/unsupported formats before any pixel allocation."""
        source = Bitmap()
        if self.gdi.GetObjectW(
            handle, ctypes.sizeof(source), ctypes.byref(source)
        ) != ctypes.sizeof(source):
            raise RuntimeError("cursor-bitmap-unavailable")
        if (
            source.planes != 1
            or not mask
            or source.bits != 1
            or not 1 <= source.width <= 128
            or not 1 <= source.height <= 256
        ):
            raise RuntimeError("cursor-bitmap-format-unsupported")
        info = BitmapInfo()
        header = info.header
        info.colors[0], info.colors[1] = 0, 0xFFFFFF
        header.size = ctypes.sizeof(header)
        header.width, header.height = source.width, -source.height
        header.planes, header.bits = 1, 1
        stride = ((int(source.width) + 31) // 32) * 4
        length = stride * int(source.height)
        buffer = ctypes.create_string_buffer(length)
        dc = self.user.GetDC(None)
        if not dc:
            raise RuntimeError("cursor-copy-dc-unavailable")
        try:
            copied = self.gdi.GetDIBits(
                dc, handle, 0, source.height, buffer, ctypes.byref(info), 0
            )
            if copied != source.height:
                raise RuntimeError("cursor-bitmap-copy-incomplete")
            if tuple(info.colors) != (0, 0xFFFFFF):
                raise RuntimeError("cursor-mask-palette-unsupported")
            # Retain only defined width bits; DIB row padding is not cursor content.
            width, height = int(source.width), int(source.height)
            row_bytes = (width + 7) // 8
            rows = []
            for row in range(height):
                data = bytearray(buffer.raw[row * stride : row * stride + row_bytes])
                if width % 8:
                    data[-1] &= 0xFF << (8 - width % 8)
                rows.append(bytes(data))
            return width, height, b"".join(rows)
        finally:
            cleanup((partial(self.release, dc),))

    def release(self, dc: int) -> None:
        """Release only this reader-owned screen DC."""
        if self.user.ReleaseDC(None, dc) != 1:
            raise RuntimeError("cursor-copy-dc-cleanup-unconfirmed")

    def delete(self, handle: int) -> None:
        """Release only GetIconInfo-owned bitmap copies."""
        if not self.gdi.DeleteObject(handle):
            raise RuntimeError("cursor-copy-cleanup-unconfirmed")
