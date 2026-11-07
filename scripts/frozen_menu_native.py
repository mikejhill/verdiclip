"""Bounded same-fixture menu qualification on disposable Windows CI only.

No image is saved: only owned marker hashes, geometry and native-input receipts.
Baseline observations never require menu loss; every observed result is retained.
"""

# ruff: noqa: TRY301 - observer records and re-raises first failure with cleanup evidence

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast, override

from PySide6 import __version__ as qt_version
from PySide6.QtCore import QCoreApplication, QEvent, QProcess
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication
from scripts.frozen_menu_fixture import MenuFixture, add_patch, physical_box
from scripts.frozen_menu_input import (
    HotkeyObservation,
    OwnedInput,
    has_contrast,
    is_marker,
    pixel_digest,
    wait_for,
)
from tests.capture.desktop import DesktopSession

from verdiclip.capture import grabber, overlay, service
from verdiclip.capture.grabber import FrozenScreen, MssScreenSource
from verdiclip.capture.models import Capture, ScreenGeometry
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect
from verdiclip.platform import hotkeys
from verdiclip.platform.associations import FileAssociations
from verdiclip.platform.hotkeys import Hotkey, HotkeyService
from verdiclip.platform.shortcuts import ShortcutRegistration
from verdiclip.platform.startup import StartupRegistration, WinRegRunKey
from verdiclip.platform.windows import Win32WindowApi, WindowInfo
from verdiclip.settings import Settings, SettingsStore
from verdiclip.shell import controller as controller_module
from verdiclip.shell.controller import AppController, ShellIntegration

SCENARIOS = (
    "owned-drag",
    "foreign-drag",
    "foreign-hmenu",
    "owned-escape",
    "foreign-escape",
    "tray-command",
    "owned-window",
    "owned-fullscreen",
    "owned-repeat",
    "owned-error",
)


def attest() -> dict[str, str]:
    """Verify exact imported product bytes without modifying either source tree."""
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Disposable Windows CI required for simulated native input")
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("GitHub-hosted disposable runner identity required")
    if not bool(os.environ.get("QUALIFICATION_INPUT_LEDGER")):
        raise RuntimeError("Timeout input retirement ledger required")
    if qt_version != os.environ["QT_VERSION"]:
        raise RuntimeError("Unexpected Qt runtime")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git unavailable")
    root = Path(os.environ["SOURCE_ROOT"]).resolve()
    expected = os.environ["SOURCE_COMMIT"]
    actual = subprocess.check_output(  # noqa: S603 - fixed read-only query
        [git, "rev-parse", "HEAD"], cwd=root, timeout=5, text=True
    ).strip()
    if actual != expected:
        raise RuntimeError("Selected source commit mismatch")
    digests: dict[str, str] = {"commit": actual, "qt": qt_version}
    for module in (grabber, overlay, service, hotkeys, controller_module):
        path = Path(module.__file__).resolve()
        relative = path.relative_to(root).as_posix()
        committed = subprocess.check_output(  # noqa: S603 - selected source query
            [git, "show", "HEAD:" + relative], cwd=root, timeout=5
        )
        if path.read_bytes() != committed:
            raise RuntimeError("Imported product bytes differ from selected commit")
        digests[relative] = hashlib.sha256(committed).hexdigest()
    return digests


class ObservedScreen(MssScreenSource):
    """Record the real freeze boundary without changing production ordering."""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0
        self.popup_at_freeze = False
        self.reject_next = False

    @override
    def freeze(self) -> FrozenScreen:
        self.count += 1
        self.popup_at_freeze = QApplication.activePopupWidget() is not None
        if self.reject_next:
            self.reject_next = False
            raise CaptureError("Owned qualification freeze fault")
        return super().freeze()


class OwnedWindows:
    """Resolve only fixture handles; do not enumerate or read unrelated apps."""

    def __init__(self, handle: int) -> None:
        self.handle = handle
        self.api = Win32WindowApi()

    def foreground(self) -> WindowInfo:
        """Freeze the owned background's real physical frame and title."""
        return WindowInfo(
            self.handle,
            "Owned freeze-first menu fixture",
            self.api.frame_bounds(self.handle),
        )

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[WindowInfo]:
        """Expose only this single fixture as a selection candidate."""
        return () if self.handle in exclude else (self.foreground(),)


class FixtureOwner:
    """Own either an in-process popup or one bounded disposable child process."""

    def __init__(self, path: Path, *, foreign: bool, native: bool = False) -> None:
        self.path = path
        self.local = None if foreign else MenuFixture()
        self.process: QProcess | None = None
        self.first: dict[str, object] | None = None
        self.last: dict[str, object] | None = None
        if foreign:
            self.process = QProcess()
            self.process.setProgram(sys.executable)
            self.process.setArguments(
                ["-m", "scripts.frozen_menu_fixture", str(path)]
                + (["--hmenu"] if native else [])
            )
            self.process.start()
            if not self.process.waitForStarted(5000):
                failure = RuntimeError(
                    f"Owned menu child failed to start: {self.process.errorString()}"
                )
                try:
                    self.close()
                except BaseException as cleanup_error:
                    raise failure from cleanup_error
                raise failure
        else:
            if self.local is None:
                raise RuntimeError("Missing local fixture")
            self.local.show()

    def state(self) -> dict[str, object]:
        """Read an atomic fixture receipt, retaining first and last setup state."""
        if self.local is not None:
            state = self.local.state()
        elif self.path.exists():
            decoded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(decoded, dict):
                raise RuntimeError("Invalid owned fixture receipt")
            state = cast("dict[str, object]", decoded)
        else:
            state = {"visible": False, "exposed": False}
        self.first = state if self.first is None else self.first
        self.last = state
        return state

    def close(self) -> None:
        """Terminate/reap only the child we started; keep timeout as failure."""
        if self.local is not None:
            self.local.close()
        if (
            self.process is not None
            and self.process.state() != QProcess.ProcessState.NotRunning
        ):
            self.process.terminate()
            if (
                self.process.waitForFinished(2000)
                or self.process.state() == QProcess.ProcessState.NotRunning
            ):
                return
            self.process.kill()
            if (
                not self.process.waitForFinished(2000)
                and self.process.state() != QProcess.ProcessState.NotRunning
            ):
                raise RuntimeError("Owned menu child cleanup unconfirmed")


def state_patch(state: dict[str, object]) -> Rect:
    """Reject malformed fixture coordinates before feeding any native input."""
    raw = state["patch"]
    if (
        not isinstance(raw, list)
        or len(raw) != 4  # noqa: PLR2004 - rectangle fields
        or not all(isinstance(v, int) for v in raw)
    ):
        raise RuntimeError("Invalid fixture patch bounds")
    return Rect(*raw)


def make_controller(
    directory: Path, source: ObservedScreen, windows: OwnedWindows, keys: HotkeyService
) -> AppController:
    """Construct real controller over isolated preferences, without calling start.

    Registration backends are never invoked: start/settings are not called.
    Explicit owned hotkeys are the only OS registrations made by this harness.
    """
    settings = Settings()
    settings = replace(
        settings, editor=replace(settings.editor, confirm_unsaved_close=False)
    )
    store = SettingsStore(directory / "settings.json")
    store.save(settings)
    shortcuts = ShortcutRegistration(directory / "shortcuts")
    integration = ShellIntegration(
        StartupRegistration(shortcuts, WinRegRunKey()), FileAssociations(), shortcuts
    )
    return AppController(store, source, windows, keys, integration)


def final_patch(capture: Capture, patch: Rect) -> QImage:
    """Read only the synthetic menu interior from the delivered image."""
    return FrozenScreen(capture.image, capture.area).crop(patch)


def ready_reference(
    patch: Rect, scenario: str, seconds: float, receipt: dict[str, object]
) -> QImage:
    """Wait only for pre-action compositor readiness, retaining initial state."""
    image = QImage()
    attempts = 0
    with MssScreenSourceContext() as probe:

        def ready() -> bool:
            nonlocal image, attempts
            image = probe.freeze().crop(patch)
            attempts += 1
            result = (
                has_contrast(image) if scenario == "foreign-hmenu" else is_marker(image)
            )
            receipt.setdefault("first_pixel_ready", result)
            receipt["last_pixel_ready"] = result
            receipt["pixel_readiness_attempts"] = attempts
            return result

        wait_for(ready, "pretrigger owned marker pixels", seconds)
    return image


def native_drag(input_owner: OwnedInput, patch: Rect) -> Rect:
    """Trace ordinary OS coordinates over a 60ms synthetic physical-pixel drag."""
    start = input_owner.move(int(patch.left) - 12, int(patch.top) - 12)
    try:
        input_owner.button()
        # Fixed movement, identical on all sources, not a production timing gate.
        for fraction in (0.25, 0.5, 0.75, 1.0):
            target = (
                round(start[0] + (patch.width + 24) * fraction),
                round(start[1] + (patch.height + 24) * fraction),
            )
            input_owner.move(*target)
            until = time.perf_counter() + 0.015
            while time.perf_counter() < until:
                QApplication.processEvents()
                time.sleep(0.001)
        end = input_owner.cursor()
        input_owner.button(up=True)
    finally:
        input_owner.release()
    return Rect(start[0], start[1], end[0] - start[0], end[1] - start[1])


def close_controller(controller: AppController) -> None:
    """Retire every document and overlay without writing output or clipboard."""
    controller.shutdown()
    for editor in controller.editors:
        editor.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def validate_outcome(
    scenario: str, frames: int, captures: Sequence[Capture], errors: Sequence[str]
) -> None:
    """An emitted error or missing delivery cannot masquerade as an observation."""
    expected_frames = 2 if scenario == "owned-repeat" else 1
    expected_captures = (
        0
        if scenario.endswith("escape") or scenario == "owned-error"
        else expected_frames
    )
    if frames != expected_frames or len(captures) != expected_captures:
        raise RuntimeError("Duplicate or missing frame/capture")
    expected_errors = (
        ["Owned qualification freeze fault"] if scenario == "owned-error" else []
    )
    if list(errors) != expected_errors:
        raise RuntimeError(f"Unexpected capture errors: {list(errors)}")
    if captures:
        expected_mode = (
            "window"
            if scenario in {"owned-window", "owned-repeat"}
            else "fullscreen"
            if scenario == "owned-fullscreen"
            else "region"
        )
        if captures[-1].mode.value != expected_mode:
            raise RuntimeError("Captured mode does not match requested action")


def observe(scenario: str) -> dict[str, object]:  # noqa: C901, PLR0915, PLR0912 - explicit bounded native phases
    """Make exactly one test observation; no retry or alternate dispatch path."""
    if scenario not in SCENARIOS:
        raise ValueError("Unknown menu qualification scenario")
    provenance = attest()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    if QGuiApplication.platformName() != "windows":
        raise RuntimeError("Native Windows Qt required")
    desktop = DesktopSession.current()
    if desktop is None or desktop.state != 0 or desktop.flags != 1:
        raise RuntimeError("Known active unlocked desktop required")
    receipt: dict[str, object] = {
        "scenario": scenario,
        "source": os.environ["CAPTURE_SOURCE"],
        "provenance": provenance,
        "status": "started",
        "cleanup_confirmed": False,
        "simulated_input": True,
        "physical_mouse_qualified": False,
    }
    owner: FixtureOwner | None = None
    controller: AppController | None = None
    source = ObservedScreen()
    keys = HotkeyService()
    observer = HotkeyObservation()
    input_owner = OwnedInput()
    # Last installed runs first, before production may consume WM_HOTKEY.
    input_owner.install_observer(observer)
    captures: list[Capture] = []
    signals: list[str] = []
    errors: list[str] = []
    keys.triggered.connect(signals.append)
    temporary = tempfile.TemporaryDirectory(prefix="verdiclip-menu-")
    failure: BaseException | None = None
    overlay_handles: set[int] = set()
    try:
        directory = Path(temporary.name)
        setup_started = time.perf_counter()
        owner = FixtureOwner(
            directory / "fixture.json",
            foreign=scenario.startswith("foreign"),
            native=scenario == "foreign-hmenu",
        )
        wait_for(
            lambda: (
                owner.state().get("exposed") is True
                and owner.state().get("visible") is True
            ),
            "owned menu setup",
        )
        state = owner.state()
        background, menu = int(str(state["background"])), int(str(state["menu"]))
        pid = int(str(state["pid"]))
        if input_owner.process(background) != pid or input_owner.process(menu) != pid:
            raise RuntimeError("Fixture HWND ownership mismatch")
        if scenario.startswith("foreign") == (pid == os.getpid()):
            raise RuntimeError("Fixture process separation mismatch")
        input_owner.allowed_pids.add(pid)
        wait_for(input_owner.foreground_owned, "owned foreground setup")
        windows = OwnedWindows(background)
        expected_window = windows.foreground()
        controller = make_controller(directory, source, windows, keys)
        controller.capture.captured.connect(captures.append)
        controller.capture.failed.connect(errors.append)
        for index, action in enumerate(("region", "window", "fullscreen", "repeat")):
            if not keys.register(action, Hotkey.parse(f"ctrl+shift+f{18 + index}")):
                raise RuntimeError("Owned qualification hotkey unavailable")
        if scenario == "owned-repeat":
            controller.capture.capture_active_window()
            wait_for(lambda: len(captures) == 1, "repeat history priming")
            if source.count != 1 or captures[0].title != expected_window.title:
                raise RuntimeError("Repeat history priming failed")
            receipt["priming_capture"] = {
                "mode": captures[0].mode.value,
                "title": captures[0].title,
                "freeze_count": source.count,
            }
            if owner.local is None:
                raise RuntimeError("Repeat fixture must be local")
            owner.local.show()
            wait_for(
                lambda: (
                    owner.state().get("visible") is True
                    and input_owner.foreground_owned()
                ),
                "repeat menu readiness",
            )
        patch = state_patch(state)
        if scenario == "tray-command":
            if owner.local is None:
                raise RuntimeError("Tray fixture must be local")
            owner.local.menu.close()
            tray_menu = controller.tray.menu
            marker = add_patch(tray_menu)
            tray_menu.popup(
                owner.local.background.mapToGlobal(
                    owner.local.background.rect().center()
                )
            )
            wait_for(
                lambda: tray_menu.isVisible() and marker.isVisible(),
                "tray menu setup",
            )
            patch = Rect(*physical_box(marker))
            tray_menu.setActiveAction(controller.tray.action("region"))
        reference = ready_reference(
            patch, scenario, 5.0 - (time.perf_counter() - setup_started), receipt
        )
        receipt["reference_sha256"] = pixel_digest(reference)
        receipt["patch"] = [patch.x, patch.y, patch.width, patch.height]
        receipt["setup_elapsed_ms"] = round(
            (time.perf_counter() - setup_started) * 1000, 3
        )
        if time.perf_counter() - setup_started >= 5:  # noqa: PLR2004 - bounded setup/action budget
            raise TimeoutError("Owned menu setup exceeded 5s")
        initial_captures = len(captures)
        action = "region"
        started = time.perf_counter()
        if scenario == "owned-error":
            source.reject_next = True
        if scenario == "tray-command":
            try:
                input_owner.key(0x0D)
            finally:
                input_owner.release()
        else:
            action = scenario.split("-", 1)[1]
            action = (
                action if action in {"window", "fullscreen", "repeat"} else "region"
            )
            input_owner.chord(keys.registered[action].virtual_key)
        wait_for(
            lambda: (
                bool(controller.capture.overlays)
                or len(captures) > initial_captures
                or bool(errors)
            ),
            "capture action",
        )
        if scenario == "owned-error":
            if (
                errors != ["Owned qualification freeze fault"]
                or captures
                or controller.capture.is_selecting
            ):
                raise RuntimeError("Capture failure cleanup contract failed")
            receipt["failure_cleaned_up"] = True
        elif controller.capture.overlays:
            overlay_handles.update(
                int(item.winId()) for item in controller.capture.overlays
            )
            wait_for(
                lambda: any(
                    input_owner.api.GetForegroundWindow() == int(item.winId())
                    for item in controller.capture.overlays
                ),
                "overlay native foreground",
            )
            if QApplication.activePopupWidget() is not None:
                raise RuntimeError("Popup still owns selection input")
            # Fixture-specific Qt menus close on activation. Snapshot fidelity
            # is the product contract; post-capture closure itself is not.
            wait_for(
                lambda: owner.state().get("visible") is False,
                "owned fixture popup closure",
            )
            if scenario.endswith("escape"):
                input_owner.escape()
                wait_for(
                    lambda: not controller.capture.is_selecting,
                    "native Escape cancellation",
                )
                if captures:
                    raise RuntimeError("Escape unexpectedly delivered a capture")
                receipt["cancelled_cleanly"] = True
            else:
                area = native_drag(input_owner, patch)
                wait_for(lambda: len(captures) == 1, "native drag delivery")
                if captures[0].area != area:
                    raise RuntimeError(
                        "Selection differs from actual OS pointer coordinates"
                    )
                receipt["os_pointer_area_matches"] = True
        if captures:
            capture = captures[-1]
            observed = final_patch(capture, patch)
            preserved = observed.size() == reference.size() and pixel_digest(
                observed
            ) == pixel_digest(reference)
            receipt.update(
                menu_pixels_preserved=preserved,
                captured_patch_sha256=pixel_digest(observed),
                capture_mode=capture.mode.value,
                capture_title=capture.title,
            )
            if scenario == "tray-command":
                if preserved or source.popup_at_freeze:
                    raise RuntimeError(
                        "Initiating tray command menu leaked into capture"
                    )
            elif os.environ["CAPTURE_SOURCE"] == "candidate" and not preserved:
                raise RuntimeError("Candidate lost requested menu pixels")
            elif scenario.startswith("foreign") and not preserved:
                raise RuntimeError("Foreign menu regression control lost pixels")
            if scenario in {"owned-window", "owned-repeat"} and (
                capture.area != expected_window.bounds
                or capture.title != expected_window.title
            ):
                raise RuntimeError(
                    "Active-window metadata no longer matches frozen target"
                )
            if scenario == "owned-fullscreen":
                monitor = ScreenGeometry.from_screen(QGuiApplication.primaryScreen())
                if capture.area != monitor.physical:
                    raise RuntimeError("Fullscreen target metadata mismatch")
            wait_for(lambda: bool(controller.editors), "editor delivery")
            target = controller.editors[-1]
            wait_for(
                lambda: (
                    input_owner.api.GetForegroundWindow() == int(target.winId())
                    and QApplication.activeWindow() is target
                ),
                "editor native and Qt focus",
            )
            receipt["editor_focused"] = True
            if scenario == "owned-repeat" and hasattr(target, "pages"):
                pages = target.pages
                if len(pages) != 2 or target.tab_bar.currentIndex() != 1:  # noqa: PLR2004 - priming plus repeat
                    raise RuntimeError("Repeat capture did not select its new tab")
                receipt["repeat_selected_new_tab"] = True
        validate_outcome(scenario, source.count, captures, errors)
        if (
            scenario.startswith("owned")
            and os.environ["CAPTURE_SOURCE"] == "candidate"
            and not source.popup_at_freeze
        ):
            raise RuntimeError("Candidate froze after closing its owned popup")
        expected_messages = 0 if scenario == "tray-command" else 1
        if (
            len(observer.events) != expected_messages
            or len(signals) != expected_messages
        ):
            raise RuntimeError("Native hotkey delivery missing or duplicated")
        if expected_messages and (
            signals != [action] or any(event["hwnd"] != 0 for event in observer.events)
        ):
            raise RuntimeError("Actual registered thread-hotkey route mismatch")
        if controller.capture.is_selecting:
            raise RuntimeError("Capture retained an input session after completion")
        if time.perf_counter() - started >= 5:  # noqa: PLR2004 - bounded setup/action budget
            raise TimeoutError("Capture action exceeded 5s")
        receipt.update(
            status="observed",
            freeze_count=source.count,
            popup_at_freeze=source.popup_at_freeze,
            hotkey_events=observer.events,
            hotkey_signals=signals,
            capture_count=len(captures),
            action_elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )
    except BaseException as error:
        failure = error
        receipt["status"] = "failed"
        receipt["failure"] = f"{type(error).__name__}: {error}"
        raise
    else:
        return receipt
    finally:
        callbacks = [input_owner.release]
        if controller is not None:
            callbacks.append(lambda: close_controller(controller))
        if owner is not None:
            receipt["first_fixture_state"] = owner.first
            receipt["last_fixture_state"] = owner.last
            callbacks.append(owner.close)
        callbacks.extend(
            (
                keys.close,
                lambda: app.removeNativeEventFilter(observer),
                source.close,
                temporary.cleanup,
            )
        )
        cleanup_errors: list[BaseException] = []
        for callback in callbacks:
            try:
                callback()
            except BaseException as error:  # noqa: BLE001 - re-raised after all retirement
                cleanup_errors.append(error)
        if controller is not None and (
            controller.capture.is_selecting
            or any(input_owner.api.IsWindow(handle) for handle in overlay_handles)
        ):
            cleanup_errors.append(
                RuntimeError("Owned native overlays were not retired")
            )
        receipt.update(
            cleanup_confirmed=not cleanup_errors
            and not input_owner.down
            and not input_owner.mouse_down,
            freeze_count=source.count,
            hotkey_events=observer.events,
            hotkey_signals=signals,
            capture_count=len(captures),
            cleanup_errors=[
                f"{type(error).__name__}: {error}" for error in cleanup_errors
            ],
        )
        if cleanup_errors:
            receipt["status"] = "failed"
        sys.stdout.write(
            "FROZEN_MENU_NATIVE=" + json.dumps(receipt, sort_keys=True) + "\n"
        )
        if cleanup_errors:
            group = BaseExceptionGroup(
                "Owned qualification cleanup failed", cleanup_errors
            )
            if failure is not None:
                raise failure from group
            raise group


class MssScreenSourceContext(MssScreenSource):
    """Release the independent pretrigger oracle even when capture fails."""

    def __enter__(self) -> MssScreenSourceContext:
        """Retain the independent screen-source owner."""
        return self

    def __exit__(self, *_error: object) -> None:
        """Close on success or failure."""
        self.close()


if __name__ == "__main__":
    observe(sys.argv[1])
