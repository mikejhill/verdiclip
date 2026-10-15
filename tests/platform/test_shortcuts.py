"""Verify shortcut registration with a fake backend and the real Shell COM object."""

from __future__ import annotations

import sys
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest

from verdiclip.exceptions import PlatformError
from verdiclip.platform import shortcuts
from verdiclip.platform.shortcuts import (
    KnownFolder,
    ShellLinkBackend,
    Shortcut,
    ShortcutRegistration,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Shell COM")


class FakeShortcutBackend:
    """In-memory ``.lnk`` files with a controllable OS failure."""

    def __init__(self) -> None:
        """Start with no files and no injected failure."""
        self.files: dict[Path, Shortcut] = {}
        self.error: OSError | None = None

    def read(self, path: Path) -> Shortcut | None:
        """Return a stored shortcut or raise the injected failure."""
        if self.error is not None:
            raise self.error
        return self.files.get(path)

    def write(self, path: Path, shortcut: Shortcut) -> None:
        """Store a shortcut or raise the injected failure."""
        if self.error is not None:
            raise self.error
        self.files[path] = shortcut

    def delete(self, path: Path) -> None:
        """Remove a shortcut idempotently or raise the injected failure."""
        if self.error is not None:
            raise self.error
        self.files.pop(path, None)


def sample(tmp_path: Path) -> Shortcut:
    """Return a shortcut with every field set, including spaces and non-ASCII."""
    return Shortcut(
        target=Path(sys.executable),
        arguments="-m verdiclip",
        working_directory=tmp_path,
        icon=tmp_path / "Ünïcode dir" / "verdiclip.ico",
        description="Fast, faithful screenshot capture",
    )


class TestShortcutRegistration:
    """Named shortcut lifecycle and contextual error conversion."""

    def test_lifecycle(self, tmp_path: Path) -> None:
        """The shortcut is named after the app and disable is idempotent."""
        backend = FakeShortcutBackend()
        registration = ShortcutRegistration(tmp_path, backend)

        assert not registration.is_enabled()
        registration.enable(sample(tmp_path))
        assert registration.is_enabled()
        assert registration.path == tmp_path / "VerdiClip.lnk"
        assert backend.files == {registration.path: sample(tmp_path)}
        registration.disable()
        registration.disable()
        assert not registration.is_enabled()

    def test_refresh_writes_a_missing_shortcut(self, tmp_path: Path) -> None:
        """With no shortcut yet, refresh creates it."""
        backend = FakeShortcutBackend()
        registration = ShortcutRegistration(tmp_path, backend)

        assert registration.refresh(sample(tmp_path))

        assert backend.files == {registration.path: sample(tmp_path)}

    def test_refresh_keeps_a_shortcut_for_another_existing_install(
        self, tmp_path: Path
    ) -> None:
        """A dev or worktree run doesn't take over the installed shortcut."""
        backend = FakeShortcutBackend()
        registration = ShortcutRegistration(tmp_path, backend)
        other = tmp_path / "main" / "pythonw.exe"
        other.parent.mkdir()
        other.touch()
        installed = replace(sample(tmp_path), target=other)
        backend.files[registration.path] = installed

        assert not registration.refresh(sample(tmp_path))

        assert backend.files[registration.path] == installed

    @pytest.mark.parametrize("previous", ["same", "same_folder", "gone"])
    def test_refresh_updates_own_or_stale_shortcut(
        self, tmp_path: Path, previous: str
    ) -> None:
        """Its own environment's shortcut, or one whose program is gone, is redone."""
        backend = FakeShortcutBackend()
        registration = ShortcutRegistration(tmp_path, backend)
        own = sample(tmp_path).target
        target = {
            "same": own,
            "same_folder": own.with_name("verdiclip-gui.exe"),
            "gone": tmp_path / "gone.exe",
        }[previous]
        backend.files[registration.path] = replace(
            sample(tmp_path), target=target, description="old"
        )

        assert registration.refresh(sample(tmp_path))

        assert backend.files[registration.path] == sample(tmp_path)

    @pytest.mark.parametrize("operation", ["read", "enable", "disable", "refresh"])
    def test_errors(self, tmp_path: Path, operation: str) -> None:
        """OS failures become PlatformErrors that name the shortcut."""
        backend = FakeShortcutBackend()
        backend.error = PermissionError("denied")
        registration = ShortcutRegistration(tmp_path, backend, "Custom")
        actions = {
            "read": registration.is_enabled,
            "enable": partial(registration.enable, sample(tmp_path)),
            "disable": registration.disable,
            "refresh": partial(registration.refresh, sample(tmp_path)),
        }

        with pytest.raises(PlatformError, match=r"Custom\.lnk") as caught:
            actions[operation]()

        assert caught.value.__cause__ is backend.error

    def test_default_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Omitted backends construct the Shell COM adapter."""
        monkeypatch.setattr(shortcuts, "ShellLinkBackend", FakeShortcutBackend)

        assert not ShortcutRegistration(tmp_path).is_enabled()


@windows_only
class TestShellLinkBackend:
    """Real ``.lnk`` files written and read back through ``IShellLinkW``."""

    def test_round_trip(self, tmp_path: Path) -> None:
        """Every field survives a write and read, and delete removes the file."""
        backend = ShellLinkBackend()
        path = tmp_path / "new folder" / "VerdiClip.lnk"

        backend.write(path, sample(tmp_path))
        stored = backend.read(path)
        backend.delete(path)
        backend.delete(path)

        assert stored == sample(tmp_path)
        assert not path.exists()

    def test_rewrite_replaces(self, tmp_path: Path) -> None:
        """Writing over an existing shortcut replaces its fields."""
        backend = ShellLinkBackend()
        path = tmp_path / "VerdiClip.lnk"
        backend.write(path, sample(tmp_path))
        moved = Shortcut(
            Path(sys.executable).with_name("moved.exe"),
            "-m verdiclip",
            tmp_path,
            tmp_path / "verdiclip.ico",
            "Moved",
        )

        backend.write(path, moved)

        assert backend.read(path) == moved

    def test_missing(self, tmp_path: Path) -> None:
        """A missing file reads as no shortcut."""
        assert ShellLinkBackend().read(tmp_path / "absent.lnk") is None

    def test_corrupt_file(self, tmp_path: Path) -> None:
        """A file that is not a shortcut raises OSError for the caller to wrap."""
        path = tmp_path / "VerdiClip.lnk"
        path.write_bytes(b"not a shortcut")

        with pytest.raises(OSError, match=r"."):
            ShellLinkBackend().read(path)

    @pytest.mark.parametrize("folder", list(KnownFolder))
    def test_known_folders(self, folder: KnownFolder) -> None:
        """Startup and Programs resolve under the user's Start menu."""
        path = ShellLinkBackend().known_folder(folder)

        assert path.is_absolute()
        assert "Start Menu" in path.parts or path.is_dir()


def test_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module imports safely and construction explains unsupported systems."""
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")

    with pytest.raises(PlatformError, match="Windows"):
        ShellLinkBackend()
