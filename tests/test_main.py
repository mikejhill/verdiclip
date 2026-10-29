"""Tests for the application entry point."""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication
from tests.ux.test_app_journeys import FakeHotkeys

from verdiclip import __main__ as entry
from verdiclip.__main__ import Application
from verdiclip.capture.grabber import FrozenScreen
from verdiclip.geometry import Rect
from verdiclip.platform.hotkeys import HotkeyService
from verdiclip.settings import Settings, SettingsStore
from verdiclip.shell.gui_readiness import GuiReadinessError
from verdiclip.shell.instance import SingleInstance


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
) -> list[tuple[str, Path]]:
    """Point settings at tmp_path and replace the OS boundaries with fakes."""
    del qapp
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    base = Settings()
    SettingsStore(SettingsStore.default_path()).save(
        replace(base, output=replace(base.output, directory=tmp_path / "auto"))
    )
    monkeypatch.setattr(entry, "MssScreenSource", FakeScreens)
    monkeypatch.setattr(entry, "WindowLocator", FakeLocator)
    # Application startup owns process-wide logging in production. In-process
    # command tests must not close pytest's handlers or retain its captured stderr
    # after that test ends. The real configuration is checked in a child below.
    logging_calls: list[tuple[str, Path]] = []

    def record_logging(level: str, directory: Path) -> None:
        logging_calls.append((level, directory))

    monkeypatch.setattr(Application, "_configure_logging", staticmethod(record_logging))
    return logging_calls


class TestApplication:
    """Tests for Application."""

    def test_capture_preserves_test_process_logging(
        self, tmp_path: Path, isolated: list[tuple[str, Path]]
    ) -> None:
        """Entry-point tests leave pytest's handlers and level intact."""
        root = logging.getLogger()
        handlers, level = tuple(root.handlers), root.level

        code = Application().run(
            [
                "--log-level",
                "DEBUG",
                "capture",
                "screen",
                "-o",
                str(tmp_path / "isolated.png"),
            ]
        )

        assert code == 0
        assert tuple(root.handlers) == handlers
        assert root.level == level
        assert isolated == [("DEBUG", SettingsStore.default_path().parent / "logs")]

    def test_real_logging_is_confined_to_child_process(self, tmp_path: Path) -> None:
        """The real entry-point configuration still writes stderr and its file."""
        root = logging.getLogger()
        handlers, level = tuple(root.handlers), root.level
        log_dir = tmp_path / "child-logs"
        script = "\n".join(
            (
                "import logging",
                "import sys",
                "from pathlib import Path",
                "from verdiclip.__main__ import Application",
                "Application._configure_logging('INFO', Path(sys.argv[1]))",
                "logging.getLogger('isolated-startup').debug('hidden-debug')",
                "logging.getLogger('isolated-startup').info('visible-info')",
                "logging.shutdown()",
            )
        )
        result = subprocess.run(  # noqa: S603 - this Python and fixed source
            [
                sys.executable,
                "-c",
                script,
                str(log_dir),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "INFO isolated-startup: visible-info" in result.stderr
        contents = (log_dir / "verdiclip.log").read_text(encoding="utf-8")
        assert "INFO isolated-startup: visible-info" in contents
        assert "hidden-debug" not in result.stderr + contents
        assert tuple(root.handlers) == handlers
        assert root.level == level

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
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No foreground window returns 1 and logs the reason."""
        monkeypatch.setattr(FakeLocator, "window", None)

        code = Application().run(["capture", "window", "--clipboard"])

        assert code == 1
        assert "no active window" in caplog.text

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


def owned_factory[T](
    create: Callable[[], T],
    owners: list[T],
    retire: Callable[[T], None],
    safety: ExitStack,
) -> Callable[[], T]:
    """Register safety ownership immediately after each successful acquisition."""

    def acquire() -> T:
        owned = create()
        owners.append(owned)
        safety.callback(retire, owned)
        return owned

    return acquire


def tracked_signal_install(safety: ExitStack) -> SimpleNamespace:
    """Restore only a handler actually installed by this hosted test invocation."""
    previous = signal.getsignal(signal.SIGINT)
    original = Application._install_sigint

    def restore(handler: object) -> None:
        if signal.getsignal(signal.SIGINT) is handler:
            signal.signal(signal.SIGINT, previous)

    def install(application: Application, app: QApplication) -> None:
        try:
            original(application, app)
        finally:
            handler = signal.getsignal(signal.SIGINT)
            if handler is not previous:
                safety.callback(restore, handler)

    return SimpleNamespace(previous=previous, install=install)


class OwnedFilterCalls:
    """Call through actual Qt methods and retire only captured test filters."""

    def __init__(self, app: QApplication, safety: ExitStack) -> None:
        self.install_actual = app.installNativeEventFilter
        self.remove_actual = app.removeNativeEventFilter
        self.safety = safety
        self.installed: list[QAbstractNativeEventFilter] = []
        self.removed: list[QAbstractNativeEventFilter] = []

    def install(self, native_filter: QAbstractNativeEventFilter) -> None:
        """Retain exact ownership after Qt accepts the native event filter."""
        self.install_actual(native_filter)
        self.installed.append(native_filter)
        self.safety.callback(self.remove_actual, native_filter)

    def remove(self, native_filter: QAbstractNativeEventFilter) -> None:
        """Record actual product removal before fallback safety retirement."""
        self.remove_actual(native_filter)
        self.removed.append(native_filter)


@pytest.fixture
def qt_entry_owners(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    """Own real Qt test resources even if production or retirement assertions fail."""
    failure = GuiReadinessError("fixture preparation failure")
    instances: list[SingleInstance] = []
    hotkeys: list[HotkeyService] = []
    timers: list[QTimer] = []
    shutdowns: list[bool] = []
    safety = ExitStack()
    filters = OwnedFilterCalls(qapp, safety)
    name = f"VerdiClip-readiness-fixture-{uuid.uuid4().hex}"

    instance = owned_factory(
        lambda: SingleInstance(name), instances, SingleInstance.close, safety
    )
    service = owned_factory(
        lambda: HotkeyService(FakeHotkeys()), hotkeys, HotkeyService.close, safety
    )
    timer = owned_factory(QTimer, timers, QTimer.stop, safety)
    sigint = tracked_signal_install(safety)
    monkeypatch.setattr(qapp, "installNativeEventFilter", filters.install)
    monkeypatch.setattr(qapp, "removeNativeEventFilter", filters.remove)

    class Controller:
        """The real controller's pre-admission failure at the entry boundary."""

        def __init__(self, *_args: object) -> None:
            pass

        def start(self) -> None:
            raise failure

        def shutdown(self) -> None:
            shutdowns.append(True)

        def handle_forwarded(self, _arguments: list[str]) -> None:
            raise AssertionError("Retired forwarding callback must not run")

    monkeypatch.setattr(entry, "SingleInstance", instance)
    monkeypatch.setattr(entry, "HotkeyService", service)
    monkeypatch.setattr(entry, "QTimer", timer)
    monkeypatch.setattr(entry, "AppController", Controller)
    monkeypatch.setattr(Application, "_install_sigint", sigint.install)
    monkeypatch.setattr(
        QApplication, "exec", lambda _app: pytest.fail("Startup must not enter exec")
    )
    monkeypatch.setattr(
        entry,
        "ShellLinkBackend",
        lambda: SimpleNamespace(known_folder=lambda _folder: tmp_path),
    )
    for boundary in (
        "ShortcutRegistration",
        "StartupRegistration",
        "WinRegRunKey",
        "FileAssociations",
        "LowLevelKeyboardHook",
        "Updater",
        "ReleaseFeed",
        "UpdateLauncher",
    ):
        monkeypatch.setattr(entry, boundary, lambda *_args: object())
    monkeypatch.setattr(entry.Installation, "current", staticmethod(object))
    application = Application()
    request = entry.CommandLine().parse([])
    store = SettingsStore(tmp_path / "failure-settings.json")
    with safety:
        yield SimpleNamespace(
            application=application,
            request=request,
            store=store,
            qapp=qapp,
            failure=failure,
            instances=instances,
            hotkeys=hotkeys,
            timers=timers,
            shutdowns=shutdowns,
            name=name,
            previous=sigint.previous,
            filters=filters,
        )


def test_gui_readiness_failure_retires_real_qt_owners_without_exec(
    qt_entry_owners: SimpleNamespace,
) -> None:
    """Observe production retirement before the fixture safety cleanup runs."""
    for _attempt in range(2):
        with pytest.raises(GuiReadinessError) as caught:
            qt_entry_owners.application._run_gui(
                qt_entry_owners.request, qt_entry_owners.store
            )
        assert caught.value is qt_entry_owners.failure
        assert QApplication.instance() is qt_entry_owners.qapp
        assert signal.getsignal(signal.SIGINT) is qt_entry_owners.previous
        assert qt_entry_owners.application._signal_timer is None
        assert qt_entry_owners.instances[-1]._server is None
        assert (
            qt_entry_owners.instances[-1]
            .metaObject()
            .indexOfSignal("message_received(QVariantList)")
            >= 0
        )
        assert (
            qt_entry_owners.instances[-1].receivers("2message_received(QVariantList)")
            == 0
        )
        assert qt_entry_owners.hotkeys[-1]._closed
        native_filter = qt_entry_owners.hotkeys[-1]._filter
        assert qt_entry_owners.filters.installed[-1] is native_filter
        assert (
            sum(removed is native_filter for removed in qt_entry_owners.filters.removed)
            == 1
        )
        assert not qt_entry_owners.hotkeys[-1].registered
        assert not qt_entry_owners.timers[-1].isActive()
        # Prove the exact owned address was released before the next attempt.
        probe = QLocalServer()
        try:
            listening = probe.listen(qt_entry_owners.name)
            assert listening, probe.errorString()
        finally:
            probe.close()
    assert len(qt_entry_owners.shutdowns) == 2
