"""Verify login registration using fakes and mocked native registry calls."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tests.platform.test_shortcuts import FakeShortcutBackend, sample

from verdiclip.exceptions import PlatformError
from verdiclip.platform import startup
from verdiclip.platform.shortcuts import ShortcutRegistration
from verdiclip.platform.startup import StartupRegistration, WinRegRunKey


class FakeRegistryBackend:
    """In-memory registry values with controllable OS failures."""

    def __init__(self) -> None:
        """Initialize values and an optional injected exception."""
        self.values: dict[str, str] = {}
        self.error: OSError | None = None

    def read_value(self, name: str) -> str | None:
        """Return a stored string or raise the injected failure."""
        if self.error is not None:
            raise self.error
        return self.values.get(name)

    def write_value(self, name: str, value: str) -> None:
        """Store the exact command or raise the injected failure."""
        if self.error is not None:
            raise self.error
        self.values[name] = value

    def delete_value(self, name: str) -> None:
        """Remove a value idempotently or raise the injected failure."""
        if self.error is not None:
            raise self.error
        self.values.pop(name, None)


class TestStartupRegistration:
    """Startup-folder shortcut plus cleanup of the legacy Run-key value."""

    @pytest.fixture
    def parts(
        self, tmp_path: Path
    ) -> tuple[StartupRegistration, FakeShortcutBackend, FakeRegistryBackend]:
        """Return a registration over fakes, with a pre-0.5 Run value present."""
        links = FakeShortcutBackend()
        legacy = FakeRegistryBackend()
        legacy.values["VerdiClip"] = '"pythonw.exe" -m verdiclip'
        registration = StartupRegistration(
            ShortcutRegistration(tmp_path, links), legacy
        )
        return registration, links, legacy

    def test_lifecycle(
        self,
        parts: tuple[StartupRegistration, FakeShortcutBackend, FakeRegistryBackend],
        tmp_path: Path,
    ) -> None:
        """Enable writes the shortcut and retires the Run value; disable removes."""
        registration, links, legacy = parts

        registration.enable(sample(tmp_path))
        enabled = registration.is_enabled()
        retired = dict(legacy.values)
        registration.disable()
        registration.disable()

        assert enabled
        assert retired == {}
        assert links.files == {}
        assert not registration.is_enabled()

    def test_disable_removes_legacy(
        self,
        parts: tuple[StartupRegistration, FakeShortcutBackend, FakeRegistryBackend],
    ) -> None:
        """Turning startup off also clears a Run value an older version wrote."""
        registration, _, legacy = parts

        registration.disable()

        assert legacy.values == {}

    def test_legacy_missing(self, tmp_path: Path) -> None:
        """A missing Run key does not make enable fail."""
        legacy = FakeRegistryBackend()
        legacy.error = FileNotFoundError("absent")
        registration = StartupRegistration(
            ShortcutRegistration(tmp_path, FakeShortcutBackend()), legacy
        )

        registration.enable(sample(tmp_path))

        assert registration.is_enabled()

    def test_legacy_error(self, tmp_path: Path) -> None:
        """Run-key failures become contextual chained PlatformErrors."""
        legacy = FakeRegistryBackend()
        legacy.error = PermissionError("denied")
        registration = StartupRegistration(
            ShortcutRegistration(tmp_path, FakeShortcutBackend()), legacy
        )

        with pytest.raises(PlatformError, match="old startup registration") as caught:
            registration.disable()

        assert caught.value.__cause__ is legacy.error


@pytest.fixture
def registry_library(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace winreg with a context-managed in-memory process boundary."""
    registry = MagicMock()
    registry.REG_SZ = 1
    registry.REG_EXPAND_SZ = 2
    registry.KEY_SET_VALUE = 2
    registry.HKEY_CURRENT_USER = 123
    registry.QueryValueEx.return_value = ("command", 1)
    monkeypatch.setattr(startup.sys, "platform", "win32")
    monkeypatch.setattr(startup, "winreg", registry, raising=False)
    return registry


class TestWinRegRunKey:
    """Native registry calls without writing the real Run key."""

    def test_read_write_delete(self, registry_library: MagicMock) -> None:
        """The backend scopes every operation to HKCU Run and preserves strings."""
        backend = WinRegRunKey()
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"

        assert backend.read_value("VerdiClip") == "command"
        backend.write_value("VerdiClip", '"app path"')
        backend.delete_value("VerdiClip")

        registry_library.CreateKeyEx.assert_called_once_with(123, path, 0, 2)
        key = registry_library.CreateKeyEx.return_value.__enter__.return_value
        registry_library.SetValueEx.assert_called_once_with(
            key, "VerdiClip", 0, 1, '"app path"'
        )
        delete_key = registry_library.OpenKey.return_value.__enter__.return_value
        registry_library.DeleteValue.assert_called_once_with(delete_key, "VerdiClip")
        assert registry_library.OpenKey.call_args_list[0].args == (123, path)

    @pytest.mark.parametrize(("value", "kind"), [(123, 1), ("command", 3)])
    def test_invalid_values(
        self, registry_library: MagicMock, value: str | int, kind: int
    ) -> None:
        """Non-string registry values are rejected instead of enabling startup."""
        registry_library.QueryValueEx.return_value = (value, kind)

        with pytest.raises(OSError, match="not a string"):
            WinRegRunKey().read_value("VerdiClip")

    @pytest.mark.parametrize("location", ["key", "value"])
    def test_absent(self, registry_library: MagicMock, location: str) -> None:
        """Missing Run keys and named values are safe for read and delete."""
        if location == "key":
            registry_library.OpenKey.side_effect = FileNotFoundError("missing")
        else:
            registry_library.QueryValueEx.side_effect = FileNotFoundError("missing")
            registry_library.DeleteValue.side_effect = FileNotFoundError("missing")
        backend = WinRegRunKey()

        assert backend.read_value("VerdiClip") is None
        backend.delete_value("VerdiClip")

    def test_expanded_string(self, registry_library: MagicMock) -> None:
        """Expandable registry strings are recognized as commands."""
        registry_library.QueryValueEx.return_value = ("%APPDATA%\\app.exe", 2)

        assert WinRegRunKey().read_value("VerdiClip") == "%APPDATA%\\app.exe"

    def test_os_error_propagates(self, registry_library: MagicMock) -> None:
        """Permission failures remain available for the service to wrap."""
        registry_library.OpenKey.side_effect = PermissionError("denied")

        with pytest.raises(PermissionError, match="denied"):
            WinRegRunKey().read_value("VerdiClip")

    def test_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The module imports safely and construction explains unsupported systems."""
        monkeypatch.setattr(startup.sys, "platform", "linux")

        with pytest.raises(PlatformError, match="Windows"):
            WinRegRunKey()
