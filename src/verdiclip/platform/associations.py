"""Register VerdiClip as an image handler for Explorer's "Open with" menu.

Everything is written under ``HKEY_CURRENT_USER``, so no elevation is needed.
Windows does not let applications make themselves the default handler; the
registration only makes VerdiClip available, and ``DEFAULT_APPS_URI`` opens the
Settings page where the user confirms it as the default.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass
from typing import Final, Protocol

if sys.platform == "win32":
    import winreg

from verdiclip import APP_NAME
from verdiclip.exceptions import PlatformError
from verdiclip.platform.launcher import LaunchCommand, is_other_install

logger = logging.getLogger(__name__)

PROG_ID: Final = "VerdiClip.Image"
EXTENSIONS: Final = (
    ".png",
    ".jpg",
    ".jpeg",
    ".jfif",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".webp",
)
DEFAULT_APPS_URI: Final = f"ms-settings:defaultapps?registeredAppUser={APP_NAME}"

_CLASSES: Final = r"Software\Classes"
_PROG_ID_KEY: Final = rf"{_CLASSES}\{PROG_ID}"
_APP_KEY: Final = rf"Software\{APP_NAME}"
_CAPABILITIES_KEY: Final = rf"{_APP_KEY}\Capabilities"
_REGISTERED_APPS_KEY: Final = r"Software\RegisteredApplications"
_SHCNE_ASSOCCHANGED: Final = 0x08000000
_SHCNF_IDLIST: Final = 0x0000
_ASSOCSTR_PROGID: Final = 20
_S_FALSE: Final = 1


@dataclass(frozen=True, slots=True)
class RegistryValue:
    """One string value under ``HKEY_CURRENT_USER``; ``name`` "" is the default."""

    key: str
    name: str
    data: str


class AssociationBackend(Protocol):
    """Registry operations the registration needs."""

    def read_value(self, key: str, name: str) -> str | None:
        """Return a string value, or None when the key or value is absent."""

    def write_value(self, value: RegistryValue) -> None:
        """Create the key as needed and store the string value."""

    def delete_value(self, key: str, name: str) -> None:
        """Delete a value, accepting an absent key or value."""

    def delete_tree(self, key: str) -> None:
        """Delete a key and everything below it, accepting an absent key."""

    def notify_changed(self) -> None:
        """Tell Explorer that file associations changed."""

    def default_prog_id(self, extension: str) -> str | None:
        """Return the ProgId that opens ``extension`` now, or None if none does."""


class WinRegAssociations:
    """``AssociationBackend`` over the real current-user registry."""

    def __init__(self) -> None:
        """Import winreg only when the Windows backend is constructed."""
        if sys.platform != "win32":
            msg = "File associations require Windows"
            raise PlatformError(msg)
        self._registry = winreg
        self._root = winreg.HKEY_CURRENT_USER

    def read_value(self, key: str, name: str) -> str | None:
        """Return a string value without creating the key."""
        try:
            with self._registry.OpenKey(self._root, key) as handle:
                value, _kind = self._registry.QueryValueEx(handle, name)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def write_value(self, value: RegistryValue) -> None:
        """Create the key as needed and store the string value."""
        with self._registry.CreateKeyEx(
            self._root, value.key, 0, self._registry.KEY_SET_VALUE
        ) as handle:
            self._registry.SetValueEx(
                handle, value.name, 0, self._registry.REG_SZ, value.data
            )

    def delete_value(self, key: str, name: str) -> None:
        """Delete a value, accepting an absent key or value."""
        try:
            with self._registry.OpenKey(
                self._root, key, 0, self._registry.KEY_SET_VALUE
            ) as handle:
                self._registry.DeleteValue(handle, name)
        except FileNotFoundError:
            logger.debug("Registry value %s\\%s is already absent", key, name)

    def delete_tree(self, key: str) -> None:
        """Delete a key and everything below it, accepting an absent key."""
        try:
            self._registry.DeleteKeyEx(self._root, key)
        except FileNotFoundError:
            logger.debug("Registry key %s is already absent", key)
            return
        except OSError:
            # DeleteKeyEx refuses keys with subkeys; remove children first
            self._delete_children(key)
            self._registry.DeleteKeyEx(self._root, key)

    def notify_changed(self) -> None:
        """Broadcast SHCNE_ASSOCCHANGED so Explorer refreshes its menus."""
        ctypes.windll.shell32.SHChangeNotify(
            _SHCNE_ASSOCCHANGED, _SHCNF_IDLIST, None, None
        )

    def default_prog_id(self, extension: str) -> str | None:
        """Ask the shell which ProgId opens ``extension``, the user's choice first.

        The shell knows where each Windows version records that choice
        (UserChoice, or UserChoiceLatest on newer builds).
        """
        query = ctypes.windll.shlwapi.AssocQueryStringW
        size = ctypes.c_uint(0)
        result = query(0, _ASSOCSTR_PROGID, extension, None, None, ctypes.byref(size))
        if result not in (0, _S_FALSE) or size.value == 0:
            return None
        buffer = ctypes.create_unicode_buffer(size.value)
        result = query(0, _ASSOCSTR_PROGID, extension, None, buffer, ctypes.byref(size))
        return str(buffer.value) if result == 0 else None

    def _delete_children(self, key: str) -> None:
        """Delete every subkey of ``key``."""
        with self._registry.OpenKey(
            self._root, key, 0, self._registry.KEY_READ
        ) as handle:
            count = self._registry.QueryInfoKey(handle)[0]
            children = [self._registry.EnumKey(handle, i) for i in range(count)]
        for child in children:
            self.delete_tree(rf"{key}\{child}")


class FileAssociations:
    """Add or remove VerdiClip from the image "Open with" choices."""

    def __init__(self, backend: AssociationBackend | None = None) -> None:
        """Store the backend; the real registry is used by default."""
        self._backend = backend if backend is not None else WinRegAssociations()

    @staticmethod
    def entries(command: str, icon: str) -> tuple[RegistryValue, ...]:
        """Return every value a registration writes.

        Args:
            command: The open command, with ``"%1"`` where the file path goes.
            icon: Path to an ``.ico`` file for the app and its file types.
        """
        description = "Capture, annotate, and share screenshots"
        values = [
            RegistryValue(_PROG_ID_KEY, "", f"{APP_NAME} image"),
            RegistryValue(_PROG_ID_KEY, "FriendlyTypeName", f"{APP_NAME} image"),
            RegistryValue(rf"{_PROG_ID_KEY}\DefaultIcon", "", icon),
            RegistryValue(rf"{_PROG_ID_KEY}\Application", "ApplicationName", APP_NAME),
            RegistryValue(rf"{_PROG_ID_KEY}\Application", "ApplicationIcon", icon),
            RegistryValue(rf"{_PROG_ID_KEY}\shell\open", "FriendlyAppName", APP_NAME),
            RegistryValue(rf"{_PROG_ID_KEY}\shell\open\command", "", command),
            RegistryValue(_CAPABILITIES_KEY, "ApplicationName", APP_NAME),
            RegistryValue(_CAPABILITIES_KEY, "ApplicationDescription", description),
            RegistryValue(_CAPABILITIES_KEY, "ApplicationIcon", icon),
            RegistryValue(_REGISTERED_APPS_KEY, APP_NAME, _CAPABILITIES_KEY),
        ]
        for extension in EXTENSIONS:
            values.append(
                RegistryValue(
                    rf"{_CAPABILITIES_KEY}\FileAssociations", extension, PROG_ID
                )
            )
            values.append(
                RegistryValue(rf"{_CLASSES}\{extension}\OpenWithProgids", PROG_ID, "")
            )
        return tuple(values)

    def registered_command(self) -> str | None:
        """Return the stored open command, or None when not registered."""
        try:
            return self._backend.read_value(rf"{_PROG_ID_KEY}\shell\open\command", "")
        except OSError as err:
            msg = "Could not read the Open with registration"
            raise PlatformError(msg) from err

    def defaults(self) -> dict[str, bool]:
        """Return, for each extension, whether VerdiClip is now its default.

        Only reads the choice the user made in Windows; it never changes it.

        Raises:
            PlatformError: If Windows cannot be asked.
        """
        try:
            return {
                extension: self._backend.default_prog_id(extension) == PROG_ID
                for extension in EXTENSIONS
            }
        except OSError as err:
            msg = "Could not read the default image apps"
            raise PlatformError(msg) from err

    def register(self, command: str, icon: str) -> None:
        """Write the registration, replacing any earlier one.

        Raises:
            PlatformError: If the registry cannot be written.
        """
        try:
            for value in self.entries(command, icon):
                self._backend.write_value(value)
            self._backend.notify_changed()
        except OSError as err:
            msg = "Could not add VerdiClip to Open with"
            raise PlatformError(msg) from err
        logger.info("Registered %s for %s", PROG_ID, ", ".join(EXTENSIONS))

    def refresh(self, command: str, icon: str) -> bool:
        """Register unless another install that still exists is registered.

        Same rule as ``ShortcutRegistration.refresh``: a missing registration,
        one that runs this environment, or one whose environment is gone is
        (re)written; another copy of the app (a development
        checkout, say) does not take over the installed one. Returns whether
        the registration was written.

        Raises:
            PlatformError: If the registry cannot be read or written.
        """
        current = self.registered_command()
        if current is not None and is_other_install(
            LaunchCommand.parse(current), LaunchCommand.parse(command)
        ):
            logger.info("Leaving Open with: it runs %s", current)
            return False
        self.register(command, icon)
        return True

    def unregister(self) -> None:
        """Remove everything ``register`` wrote; absent entries are fine.

        Raises:
            PlatformError: If the registry cannot be changed.
        """
        try:
            for extension in EXTENSIONS:
                self._backend.delete_value(
                    rf"{_CLASSES}\{extension}\OpenWithProgids", PROG_ID
                )
            self._backend.delete_value(_REGISTERED_APPS_KEY, APP_NAME)
            self._backend.delete_tree(_APP_KEY)
            self._backend.delete_tree(_PROG_ID_KEY)
            self._backend.notify_changed()
        except OSError as err:
            msg = "Could not remove VerdiClip from Open with"
            raise PlatformError(msg) from err
        logger.info("Removed %s registration", PROG_ID)
