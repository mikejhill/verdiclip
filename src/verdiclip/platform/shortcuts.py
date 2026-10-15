"""Create, read, and remove Windows shell shortcuts (``.lnk`` files).

Shortcuts are how Windows names and badges an app it did not install: the
Startup folder entry shows in Settings → Apps → Startup and Task Manager under
the shortcut's name and icon, and a Start menu entry makes the app searchable.
The native backend drives the Shell's ``IShellLinkW`` COM object through ctypes.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from ctypes import _CData

from verdiclip import APP_NAME
from verdiclip.exceptions import PlatformError
from verdiclip.platform.launcher import LaunchCommand, is_other_install

logger = logging.getLogger(__name__)

_CLSID_SHELL_LINK: Final = "00021401-0000-0000-c000-000000000046"
_IID_SHELL_LINK_W: Final = "000214f9-0000-0000-c000-000000000046"
_IID_PERSIST_FILE: Final = "0000010b-0000-0000-c000-000000000046"
_CLSCTX_INPROC_SERVER: Final = 0x1
_COINIT_APARTMENTTHREADED: Final = 0x2
_STGM_READ: Final = 0x0
_SLGP_RAWPATH: Final = 0x4
_MAX_TEXT: Final = 32768

# Vtable slots, counted from IUnknown's QueryInterface/AddRef/Release (0-2)
_QUERY_INTERFACE: Final = 0
_RELEASE: Final = 2
_LINK_GET_PATH: Final = 3
_LINK_GET_DESCRIPTION: Final = 6
_LINK_SET_DESCRIPTION: Final = 7
_LINK_GET_WORKING_DIRECTORY: Final = 8
_LINK_SET_WORKING_DIRECTORY: Final = 9
_LINK_GET_ARGUMENTS: Final = 10
_LINK_SET_ARGUMENTS: Final = 11
_LINK_GET_ICON_LOCATION: Final = 16
_LINK_SET_ICON_LOCATION: Final = 17
_LINK_SET_PATH: Final = 20
_FILE_LOAD: Final = 5
_FILE_SAVE: Final = 6  # Save(path, remember=1)


class KnownFolder(StrEnum):
    """Per-user shell folders, by their ``FOLDERID`` GUID."""

    STARTUP = "b97d20bb-f46a-4c97-ba10-5e3608430854"
    PROGRAMS = "a77f5d77-2e2b-44c3-a6a2-aba601054a51"


@dataclass(frozen=True, slots=True)
class Shortcut:
    """What a shortcut launches and how Windows presents it."""

    target: Path
    arguments: str
    working_directory: Path
    icon: Path
    description: str


class ShortcutBackend(Protocol):
    """File-level shortcut operations."""

    def read(self, path: Path) -> Shortcut | None:
        """Return the shortcut at ``path``, or None when there is none."""

    def write(self, path: Path, shortcut: Shortcut) -> None:
        """Create or replace the shortcut at ``path``."""

    def delete(self, path: Path) -> None:
        """Remove the shortcut at ``path`` if it exists."""


class _Guid(ctypes.Structure):
    """A COM GUID in its native memory layout."""

    _fields_ = (("data", ctypes.c_ubyte * 16),)

    @classmethod
    def parse(cls, text: str) -> _Guid:
        """Return the GUID for its canonical string form."""
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class _ComObject:
    """A raw COM interface pointer with typed vtable calls."""

    def __init__(self, pointer: ctypes.c_void_p) -> None:
        self._pointer = pointer

    def call(self, slot: int, argtypes: tuple[type[_CData], ...], *args: object) -> int:
        """Call vtable ``slot``; failing HRESULTs raise ``OSError``."""
        vtable = ctypes.cast(
            self._pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
        return int(prototype(vtable[slot])(self._pointer, *args))

    def query(self, iid: str) -> _ComObject:
        """Return another interface of the same object."""
        result = ctypes.c_void_p()
        self.call(
            _QUERY_INTERFACE,
            (ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)),
            ctypes.byref(_Guid.parse(iid)),
            ctypes.byref(result),
        )
        return _ComObject(result)

    def release(self) -> None:
        """Drop this reference."""
        vtable = ctypes.cast(
            self._pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[_RELEASE])(
            self._pointer
        )


class ShellLinkBackend:
    """Read and write ``.lnk`` files through the Shell's ``IShellLinkW``."""

    def __init__(self) -> None:
        """Load the COM libraries only when the Windows backend is constructed."""
        if sys.platform != "win32":
            msg = "Shell shortcuts require Windows"
            raise PlatformError(msg)
        self._ole32 = ctypes.WinDLL("ole32")
        self._ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        self._ole32.CoInitializeEx.restype = ctypes.c_long  # S_FALSE is not an error
        self._ole32.CoCreateInstance.argtypes = (
            ctypes.POINTER(_Guid),
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_Guid),
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._ole32.CoCreateInstance.restype = ctypes.HRESULT
        self._ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
        self._ole32.CoTaskMemFree.restype = None
        self._shell32 = ctypes.WinDLL("shell32")
        self._shell32.SHGetKnownFolderPath.argtypes = (
            ctypes.POINTER(_Guid),
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT

    def known_folder(self, folder: KnownFolder) -> Path:
        """Return the current user's path for ``folder`` (honors redirection)."""
        raw = ctypes.c_void_p()
        self._shell32.SHGetKnownFolderPath(
            ctypes.byref(_Guid.parse(folder.value)), 0, None, ctypes.byref(raw)
        )
        try:
            return Path(ctypes.wstring_at(raw))
        finally:
            self._ole32.CoTaskMemFree(raw)

    def read(self, path: Path) -> Shortcut | None:
        """Load the shortcut's fields, or None when the file does not exist."""
        if not path.is_file():
            return None
        with self._shell_link() as link:
            link.query(_IID_PERSIST_FILE).call(
                _FILE_LOAD, (ctypes.c_wchar_p, ctypes.c_ulong), str(path), _STGM_READ
            )
            text = (ctypes.c_wchar_p, ctypes.c_int)
            target = self._text(
                link,
                _LINK_GET_PATH,
                (*text, ctypes.c_void_p, ctypes.c_ulong),
                None,
                _SLGP_RAWPATH,
            )
            arguments = self._text(link, _LINK_GET_ARGUMENTS, text)
            directory = self._text(link, _LINK_GET_WORKING_DIRECTORY, text)
            description = self._text(link, _LINK_GET_DESCRIPTION, text)
            index = ctypes.c_int()
            icon = self._text(
                link,
                _LINK_GET_ICON_LOCATION,
                (*text, ctypes.POINTER(ctypes.c_int)),
                ctypes.byref(index),
            )
        return Shortcut(
            Path(target), arguments, Path(directory), Path(icon), description
        )

    def write(self, path: Path, shortcut: Shortcut) -> None:
        """Save ``shortcut`` to ``path``, creating the folder as needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (ctypes.c_wchar_p,)
        with self._shell_link() as link:
            link.call(_LINK_SET_PATH, text, str(shortcut.target))
            link.call(_LINK_SET_ARGUMENTS, text, shortcut.arguments)
            link.call(
                _LINK_SET_WORKING_DIRECTORY, text, str(shortcut.working_directory)
            )
            link.call(_LINK_SET_DESCRIPTION, text, shortcut.description)
            link.call(
                _LINK_SET_ICON_LOCATION,
                (ctypes.c_wchar_p, ctypes.c_int),
                str(shortcut.icon),
                0,
            )
            link.query(_IID_PERSIST_FILE).call(
                _FILE_SAVE, (ctypes.c_wchar_p, ctypes.wintypes.BOOL), str(path), 1
            )

    def delete(self, path: Path) -> None:
        """Remove the file, treating a missing one as already removed."""
        path.unlink(missing_ok=True)

    @contextmanager
    def _shell_link(self) -> Generator[_ComObject]:
        """Yield a new ``IShellLinkW`` with COM initialized on this thread."""
        initialized = self._ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED) >= 0
        try:
            pointer = ctypes.c_void_p()
            self._ole32.CoCreateInstance(
                ctypes.byref(_Guid.parse(_CLSID_SHELL_LINK)),
                None,
                _CLSCTX_INPROC_SERVER,
                ctypes.byref(_Guid.parse(_IID_SHELL_LINK_W)),
                ctypes.byref(pointer),
            )
            link = _ComObject(pointer)
            try:
                yield link
            finally:
                link.release()
        finally:
            # Qt already initialized COM on the GUI thread; only undo our own call
            if initialized:
                self._ole32.CoUninitialize()

    @staticmethod
    def _text(
        link: _ComObject, slot: int, argtypes: tuple[type[_CData], ...], *extra: object
    ) -> str:
        """Call a ``(buffer, size, ...)`` getter and return the buffer's text."""
        buffer = ctypes.create_unicode_buffer(_MAX_TEXT)
        link.call(slot, argtypes, buffer, _MAX_TEXT, *extra)
        return str(buffer.value)


class ShortcutRegistration:
    """Keep one named shortcut present or absent in a folder."""

    def __init__(
        self,
        folder: Path,
        backend: ShortcutBackend | None = None,
        name: str = APP_NAME,
    ) -> None:
        """Store the folder, backend, and shortcut name (without ``.lnk``)."""
        self._backend = backend if backend is not None else ShellLinkBackend()
        self._path = folder / f"{name}.lnk"

    @property
    def path(self) -> Path:
        """Return the shortcut file's path."""
        return self._path

    def is_enabled(self) -> bool:
        """Return whether the shortcut exists."""
        try:
            return self._backend.read(self._path) is not None
        except OSError as err:
            msg = f"Could not read shortcut {self._path}"
            raise PlatformError(msg) from err

    def enable(self, shortcut: Shortcut) -> None:
        """Create or refresh the shortcut."""
        try:
            self._backend.write(self._path, shortcut)
        except OSError as err:
            msg = f"Could not create shortcut {self._path}"
            raise PlatformError(msg) from err

    def refresh(self, shortcut: Shortcut) -> bool:
        """Write the shortcut unless it starts a different install that still exists.

        Launching from a second checkout (a worktree or dev copy) must not take
        over the installed shortcut. A missing shortcut, one that runs this same
        environment (any interpreter in its folder), or one whose environment is
        gone (a moved install) is rewritten. Returns whether it was written.
        """
        try:
            current = self._backend.read(self._path)
        except OSError as err:
            msg = f"Could not read shortcut {self._path}"
            raise PlatformError(msg) from err
        if current is not None and is_other_install(
            LaunchCommand(current.target, current.arguments),
            LaunchCommand(shortcut.target, shortcut.arguments),
        ):
            logger.info("Leaving %s: it starts %s", self._path, current.target)
            return False
        self.enable(shortcut)
        return True

    def disable(self) -> None:
        """Remove the shortcut, accepting an already absent file."""
        try:
            self._backend.delete(self._path)
        except OSError as err:
            msg = f"Could not remove shortcut {self._path}"
            raise PlatformError(msg) from err
