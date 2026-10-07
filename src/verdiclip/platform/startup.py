"""Manage the current user's Windows run-at-login registration."""

from __future__ import annotations

import logging
import sys
from typing import Protocol

if sys.platform == "win32":
    import winreg

from verdiclip.exceptions import PlatformError

logger = logging.getLogger(__name__)


class RegistryBackend(Protocol):
    """Minimal string-value operations on a registry key."""

    def read_value(self, name: str) -> str | None:
        """Read a named string, returning None when absent."""

    def write_value(self, name: str, value: str) -> None:
        """Write a named string value."""

    def delete_value(self, name: str) -> None:
        """Delete a named value if it exists."""


class WinRegRunKey:
    """String-value backend for the current user's Run key."""

    def __init__(self) -> None:
        """Import winreg only when the Windows backend is constructed."""
        if sys.platform != "win32":
            msg = "Run-at-login registration requires Windows"
            raise PlatformError(msg)
        self._registry = winreg
        self._path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def read_value(self, name: str) -> str | None:
        """Read a string registration without creating the key."""
        try:
            with self._registry.OpenKey(
                self._registry.HKEY_CURRENT_USER, self._path
            ) as key:
                value, kind = self._registry.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        if kind not in (
            self._registry.REG_SZ,
            self._registry.REG_EXPAND_SZ,
        ) or not isinstance(value, str):
            msg = f"Run value {name!r} is not a string"
            raise OSError(msg)
        return value

    def write_value(self, name: str, value: str) -> None:
        """Create the Run key as needed and store the command verbatim."""
        with self._registry.CreateKeyEx(
            self._registry.HKEY_CURRENT_USER,
            self._path,
            0,
            self._registry.KEY_SET_VALUE,
        ) as key:
            self._registry.SetValueEx(key, name, 0, self._registry.REG_SZ, value)

    def delete_value(self, name: str) -> None:
        """Treat missing keys and values as already disabled."""
        try:
            with self._registry.OpenKey(
                self._registry.HKEY_CURRENT_USER,
                self._path,
                0,
                self._registry.KEY_SET_VALUE,
            ) as key:
                self._registry.DeleteValue(key, name)
        except FileNotFoundError:
            logger.debug("Startup registration %s is already absent", name)


class StartupRegistration:
    """Enable or disable a named login command through an injected backend."""

    def __init__(
        self, backend: RegistryBackend | None = None, value_name: str = "VerdiClip"
    ) -> None:
        """Store the backend and application-specific registry value name."""
        self._backend = backend if backend is not None else WinRegRunKey()
        self._value_name = value_name

    def is_enabled(self) -> bool:
        """Return whether a nonempty command is registered."""
        try:
            return bool(self._backend.read_value(self._value_name))
        except OSError as err:
            msg = f"Could not read startup registration {self._value_name!r}"
            raise PlatformError(msg) from err

    def enable(self, command: str) -> None:
        """Store the caller's complete, quoted startup command."""
        try:
            self._backend.write_value(self._value_name, command)
        except OSError as err:
            msg = f"Could not enable startup registration {self._value_name!r}"
            raise PlatformError(msg) from err

    def disable(self) -> None:
        """Remove the registration, accepting an already absent value."""
        try:
            self._backend.delete_value(self._value_name)
        except FileNotFoundError:
            logger.debug("Startup registration %s is already absent", self._value_name)
        except OSError as err:
            msg = f"Could not disable startup registration {self._value_name!r}"
            raise PlatformError(msg) from err
