"""Tests for the application entry point."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from verdiclip import __main__ as entry
from verdiclip.__main__ import Application
from verdiclip.capture.grabber import FrozenScreen
from verdiclip.geometry import Rect
from verdiclip.settings import Settings, SettingsStore


@dataclass(frozen=True, slots=True)
class FakeWindow:
    """A foreground window."""

    title: str
    bounds: Rect


class FakeScreens:
    """A 200x100 desktop: left half red, right half blue."""

    def freeze(self) -> FrozenScreen:
        """Return the fake desktop."""
        image = QImage(200, 100, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        for x in range(100, 200):
            for y in range(100):
                image.setPixelColor(x, y, QColor("blue"))
        return FrozenScreen(image=image, bounds=Rect(0, 0, 200, 100))


class FakeLocator:
    """Reports a configurable foreground window."""

    window: FakeWindow | None = FakeWindow("Notes", Rect(100, 0, 100, 100))

    def foreground(self) -> FakeWindow | None:
        """Return the foreground window."""
        return self.window


@pytest.fixture(autouse=True)
def isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qapp: QApplication
) -> None:
    """Point settings at tmp_path and replace the OS boundaries with fakes."""
    del qapp
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    base = Settings()
    SettingsStore(SettingsStore.default_path()).save(
        replace(base, output=replace(base.output, directory=tmp_path / "auto"))
    )
    monkeypatch.setattr(entry, "MssScreenSource", FakeScreens)
    monkeypatch.setattr(entry, "WindowLocator", FakeLocator)


class TestApplication:
    """Tests for Application."""

    def test_capture_screen_to_file_prints_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``capture screen -o`` writes the file and prints its path."""
        target = tmp_path / "shot.png"

        code = Application().run(["capture", "screen", "-o", str(target)])

        assert code == 0
        assert capsys.readouterr().out.strip() == str(target)
        assert QImage(str(target)).width() == 200

    def test_capture_region_crops(self, tmp_path: Path) -> None:
        """``capture region`` keeps only the requested rectangle."""
        target = tmp_path / "region.png"

        Application().run(
            ["capture", "region", "--region", "120,10,30,20", "-o", str(target)]
        )

        saved = QImage(str(target))
        assert (saved.width(), saved.height()) == (30, 20)
        assert saved.pixelColor(5, 5) == QColor("blue")

    def test_capture_window_to_clipboard(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``capture window --clipboard`` copies the foreground window."""
        code = Application().run(["capture", "window", "--clipboard"])

        assert code == 0
        assert "clipboard" in capsys.readouterr().out.lower()
        assert QGuiApplication.clipboard().image().width() == 100

    def test_capture_without_window_fails_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No foreground window → exit code 1 and the reason on stderr."""
        monkeypatch.setattr(FakeLocator, "window", None)

        code = Application().run(["capture", "window", "--clipboard"])

        assert code == 1
        assert "no active window" in capsys.readouterr().err

    def test_capture_uses_auto_name_without_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without ``-o`` the configured folder and pattern are used."""
        code = Application().run(["capture", "screen"])

        written = Path(capsys.readouterr().out.strip())
        assert code == 0
        assert written.parent == tmp_path / "auto"
        assert written.suffix == ".png"

    def test_main_exits_with_run_code(self, tmp_path: Path) -> None:
        """``main`` exits with the code from ``run``."""
        with pytest.raises(SystemExit) as excinfo:
            Application.main(["capture", "screen", "-o", str(tmp_path / "x.png")])

        assert excinfo.value.code == 0
