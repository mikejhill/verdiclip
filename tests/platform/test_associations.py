"""Verify the Open with registration against a fake registry."""

from __future__ import annotations

import sys
from operator import methodcaller
from pathlib import Path

import pytest

from verdiclip.exceptions import PlatformError
from verdiclip.platform import associations
from verdiclip.platform.associations import (
    EXTENSIONS,
    PROG_ID,
    FileAssociations,
    RegistryValue,
    WinRegAssociations,
)
from verdiclip.platform.launcher import RELAY_CODE

COMMAND = '"C:\\VerdiClip\\pythonw.exe" -m verdiclip open "%1"'
ICON = "C:\\VerdiClip\\verdiclip.ico"


class FakeAssociationBackend:
    """In-memory keys of string values with an injectable failure."""

    def __init__(self) -> None:
        """Start empty."""
        self.keys: dict[str, dict[str, str]] = {}
        self.notified = 0
        self.error: OSError | None = None

    def read_value(self, key: str, name: str) -> str | None:
        """Return a value or None."""
        self._fail()
        return self.keys.get(key, {}).get(name)

    def write_value(self, value: RegistryValue) -> None:
        """Store a value, creating its key."""
        self._fail()
        self.keys.setdefault(value.key, {})[value.name] = value.data

    def delete_value(self, key: str, name: str) -> None:
        """Remove a value idempotently."""
        self._fail()
        self.keys.get(key, {}).pop(name, None)

    def delete_tree(self, key: str) -> None:
        """Remove a key and its subkeys."""
        self._fail()
        for existing in list(self.keys):
            if existing == key or existing.startswith(f"{key}\\"):
                del self.keys[existing]

    def notify_changed(self) -> None:
        """Count change broadcasts."""
        self.notified += 1

    def _fail(self) -> None:
        """Raise the injected failure, if any."""
        if self.error is not None:
            raise self.error


class TestFileAssociations:
    """Registration contents, round trip, and error conversion."""

    def test_register_offers_every_extension_under_one_prog_id(self) -> None:
        """Each image extension lists VerdiClip.Image, which opens with the command."""
        backend = FakeAssociationBackend()
        FileAssociations(backend).register(COMMAND, ICON)

        for extension in EXTENSIONS:
            progids = backend.keys[rf"Software\Classes\{extension}\OpenWithProgids"]
            assert PROG_ID in progids
            capabilities = r"Software\VerdiClip\Capabilities\FileAssociations"
            assert backend.keys[capabilities][extension] == PROG_ID
        prog_id = rf"Software\Classes\{PROG_ID}"
        assert backend.keys[rf"{prog_id}\shell\open\command"][""] == COMMAND
        assert backend.keys[rf"{prog_id}\shell\open"]["FriendlyAppName"] == "VerdiClip"
        assert backend.keys[rf"{prog_id}\DefaultIcon"][""] == ICON
        registered = backend.keys[r"Software\RegisteredApplications"]
        assert registered["VerdiClip"] == r"Software\VerdiClip\Capabilities"
        assert backend.notified == 1

    @pytest.mark.parametrize(
        ("previous", "written"),
        [
            (None, True),
            (("own", "python.exe"), True),
            (("gone", "pythonw.exe"), True),
            (("other", "pythonw.exe"), False),
        ],
        ids=["missing", "same_folder", "gone", "other_install"],
    )
    def test_refresh_keeps_only_another_existing_install(
        self, tmp_path: Path, previous: tuple[str, str] | None, *, written: bool
    ) -> None:
        """A dev copy doesn't take over Open with; missing or stale ones are redone."""
        for folder in ("own", "other"):
            (tmp_path / folder).mkdir()
            (tmp_path / folder / "pythonw.exe").touch()

        def command(folder: str, program: str) -> str:
            return f'"{tmp_path / folder / program}" -m verdiclip open "%1"'

        registrations = FileAssociations(FakeAssociationBackend())
        old = None if previous is None else command(*previous)
        if old is not None:
            registrations.register(old, ICON)
        new = command("own", "pythonw.exe")

        assert registrations.refresh(new, ICON) is written

        assert registrations.registered_command() == (new if written else old)

    @pytest.mark.parametrize(
        ("installed_environment", "written"),
        [("main", False), ("gone", True), ("worktree", True)],
    )
    def test_refresh_tells_relay_commands_apart_by_environment(
        self, tmp_path: Path, installed_environment: str, *, written: bool
    ) -> None:
        """Relays share the base pythonw.exe; the environment decides ownership."""
        for environment in ("main", "worktree"):
            (tmp_path / environment).mkdir()
            (tmp_path / environment / "python.exe").touch()

        def relay(environment: str) -> str:
            base = tmp_path / "base" / "pythonw.exe"
            interpreter = tmp_path / environment / "python.exe"
            return f'"{base}" -c "{RELAY_CODE}" "{interpreter}" -m verdiclip open "%1"'

        registrations = FileAssociations(FakeAssociationBackend())
        registrations.register(relay(installed_environment), ICON)

        assert registrations.refresh(relay("worktree"), ICON) is written

        expected = "worktree" if written else installed_environment
        assert registrations.registered_command() == relay(expected)

    def test_unregister_removes_only_verdiclip_entries(self) -> None:
        """Other apps' Open with entries and registrations survive."""
        backend = FakeAssociationBackend()
        backend.write_value(
            RegistryValue(r"Software\Classes\.png\OpenWithProgids", "GIMP.png", "")
        )
        backend.write_value(
            RegistryValue(r"Software\RegisteredApplications", "GIMP", "x")
        )
        registration = FileAssociations(backend)
        registration.register(COMMAND, ICON)
        assert registration.registered_command() == COMMAND

        registration.unregister()
        registration.unregister()

        assert registration.registered_command() is None
        assert backend.keys[r"Software\Classes\.png\OpenWithProgids"] == {
            "GIMP.png": ""
        }
        assert backend.keys[r"Software\RegisteredApplications"] == {"GIMP": "x"}
        assert not any(key.startswith(r"Software\VerdiClip") for key in backend.keys)

    @pytest.mark.parametrize(
        "action",
        [
            methodcaller("register", COMMAND, ICON),
            methodcaller("unregister"),
            methodcaller("registered_command"),
        ],
    )
    def test_registry_failures_become_platform_errors(
        self, action: methodcaller
    ) -> None:
        """OS errors are reported with context instead of escaping raw."""
        backend = FakeAssociationBackend()
        backend.error = PermissionError("denied")
        registration = FileAssociations(backend)

        with pytest.raises(PlatformError, match="Open with"):
            action(registration)

    def test_default_backend_is_the_real_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without an injected backend the winreg one is constructed."""
        monkeypatch.setattr(associations, "WinRegAssociations", FakeAssociationBackend)

        assert FileAssociations().registered_command() is None


@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows registry")
class TestWinRegAssociations:
    """The winreg backend against a throwaway key."""

    KEY = r"Software\VerdiClipTest\Associations"

    def test_round_trip_and_tree_delete(self) -> None:
        """Values persist, nested keys delete, and absent items are tolerated."""
        backend = WinRegAssociations()
        backend.write_value(RegistryValue(self.KEY, "name", "value"))
        backend.write_value(RegistryValue(rf"{self.KEY}\child\leaf", "", "deep"))
        try:
            assert backend.read_value(self.KEY, "name") == "value"
            assert backend.read_value(rf"{self.KEY}\child\leaf", "") == "deep"
            backend.delete_value(self.KEY, "name")
            backend.delete_value(self.KEY, "name")
            assert backend.read_value(self.KEY, "name") is None
        finally:
            backend.delete_tree(r"Software\VerdiClipTest")
        backend.delete_tree(r"Software\VerdiClipTest")
        backend.delete_value(r"Software\VerdiClipTest", "gone")
        assert backend.read_value(rf"{self.KEY}\child\leaf", "") is None

    def test_notify_changed_does_not_raise(self) -> None:
        """The Explorer broadcast is fire-and-forget."""
        WinRegAssociations().notify_changed()
