"""Hosted Windows qualification of the actual scrolling tray/controller lifecycle.

Only unrelated OS registrations are substituted. The chooser, hotkey service,
owned helper, delivery, editor tabs and ordinary Repeat are production objects.
"""

from __future__ import annotations

import ctypes.wintypes as w
import json
import math
import os
from collections.abc import Callable
from dataclasses import asdict, replace
from fractions import Fraction
from pathlib import Path
from typing import cast
from unittest.mock import Mock

from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from scripts.simple_scroll_handoff import Desktop
from scripts.simple_scroll_native import (
    _prepare,
    _require,
    _traced_helper,
    _verify_edge_rows,
    _window,
)
from scripts.simple_scroll_native import main as native_main

from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import END_PERCENT, MAX_SECONDS, ScrollResult
from verdiclip.geometry import Point, Rect
from verdiclip.platform.associations import FileAssociations
from verdiclip.platform.hotkeys import HotkeyService
from verdiclip.platform.scrolling import ScrollSelectionState
from verdiclip.platform.shortcuts import ShortcutRegistration
from verdiclip.platform.startup import StartupRegistration
from verdiclip.platform.windows import Win32WindowApi, WindowLocator
from verdiclip.settings import IntegrationSettings, Settings, SettingsStore
from verdiclip.shell.controller import AppController, ShellIntegration


class AppCapture:
    """One existing editor, one actual scrolling request and one bounded result."""

    def __init__(self, kind: str, output: Path, report: dict[str, object]) -> None:
        self.kind, self.output, self.report = kind, output, report
        self.desktop = Desktop()
        self.source = MssScreenSource()
        self.hotkeys = HotkeyService()
        self.actions: list[str] = []
        self.hotkeys.triggered.connect(self.actions.append)
        store = SettingsStore(output / "app-settings.json")
        base = Settings()
        store.save(
            replace(
                base,
                hotkeys=replace(base.hotkeys, scrolling="ctrl+alt+f9"),
                integration=IntegrationSettings(open_with=False, start_menu=False),
                startup=replace(base.startup, run_at_login=False),
            )
        )
        self.controller = AppController(
            store,
            self.source,
            WindowLocator(),
            self.hotkeys,
            ShellIntegration(
                Mock(spec=StartupRegistration),
                Mock(spec=FileAssociations),
                Mock(spec=ShortcutRegistration),
            ),
        )
        self.loop = QEventLoop()
        self.timer = QTimer()
        self.timer.setInterval(20)
        self.timer.timeout.connect(lambda: self._guarded(self._observe))
        self.failures: list[Exception] = []
        self.helper: ScrollHelper | None = None
        self.result: ScrollResult | None = None
        self._background: QWidget | None = None
        self._background_bounds: Rect | None = None
        self._activation_writes: list[int] = []
        self._restore_activation: Callable[[], None] | None = None
        self.selected = self.interrupted = self.repeating = False
        self.window = _prepare(_window())
        self.point = self.window.bounds.center
        self.report["window"] = asdict(self.window)
        self.controller._scrolling._helper_factory = self._helper
        self.controller._scrolling.finished.connect(
            lambda: QTimer.singleShot(0, lambda: self._guarded(self._completed))
        )
        self.controller._scrolling.failed.connect(
            lambda message: self.report.update(message=message)
        )

    def _guarded(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as error:  # noqa: BLE001 - re-raised after the Qt loop
            self.failures.append(error)
            self.loop.quit()

    def _helper(self) -> ScrollHelper:
        service = self.controller._scrolling
        choice = service._choice
        _require(choice is not None, "No retained selection")
        if choice is None:
            raise RuntimeError("Missing selected target")
        point, window, tick = choice
        # Preserve the actual choice even if an exact handoff assertion fails.
        self.report.update(
            selected_point=asdict(point),
            selected_window=window.handle,
            release_input_tick=tick,
        )
        _require(
            point == Point(round(self.point.x), round(self.point.y)),
            "Wrong selected point",
        )
        _require(window.handle == self.window.handle, "Wrong selected window")
        _require(
            not service.overlays and QApplication.activePopupWidget() is None,
            "Selector or menu survived handoff",
        )
        _require(
            self.desktop.foreground() == self.window.handle,
            "The exact selected target did not become foreground",
        )
        if self.kind in ("edge-background-click", "edge-background-enter"):
            _require(
                service._activation_requested,
                "Background selection did not request production activation",
            )
            _require(
                self._activation_writes == [window.handle],
                "Background selection did not make exactly one native activation "
                "request for the selected window",
            )
        self.report.update(
            saved_prechooser_foreground=service._foreground,
            handoff_foreground=self.desktop.foreground(),
            handoff_activation_requested=service._activation_requested,
        )
        helper = _traced_helper(self.output, self.kind)
        # Before the production queued publisher activates the editor, record
        # the restored application's pixels and original foreground.
        helper.finished.connect(
            lambda result: self._guarded(lambda: self._terminal(result))
        )
        self.helper = helper
        return helper

    def _terminal(self, result: ScrollResult) -> None:
        self.result = result
        helper = self.helper
        _require(helper is not None, "Terminal without owned helper")
        if helper is not None:
            (self.output / "helper.log").write_text(
                helper.diagnostics,
                encoding="utf-8",
            )
        self.report.update(
            message=result.message,
            restoration=result.restoration,
            frames=result.frames,
            terminal_foreground=self.desktop.foreground(),
        )
        target = json.loads((self.output / "target.json").read_text(encoding="utf-8"))
        movement = json.loads(
            (self.output / "movement.json").read_text(encoding="utf-8")
        )
        self.report.update(child_target=target, movement=movement)
        _require(
            bool(target),
            result.message or "The helper did not identify a scroll target",
        )
        _require(
            target["window"] == self.window.handle
            and target["input_tick"] == self.report["release_input_tick"],
            "Child changed target/input baseline",
        )
        area = Rect(**target["area"])
        if self._background_bounds is not None:
            _require(
                not self._background_bounds.intersects(area),
                "Owned foreground widget overlapped the captured panel",
            )
            self.report["background_outside_capture"] = True
        after = self.source.freeze().crop(area)
        _require(
            after.save(str(self.output / "after.png")), "Could not save final view"
        )
        if self.kind == "edge-sidebar-enter":
            self._verify_unsupported_fixed_sidebar(result, after)
        elif self.kind == "edge-app-escape":
            _require(
                self.interrupted and result.image is None and result.frames == 1,
                "Escape did not withhold the accepted frame",
            )
            _require(
                movement == {"writes": 1, "last_vertical": 0.0},
                "Escape caused a write or restoration after input",
            )
            _require(
                after == QImage(str(self.output / "frame-0.png")),
                "Escape changed pixels after the accepted top frame",
            )
        else:
            _require(result.image is not None, result.message)
            _require(
                result.restoration == "restored", "Complete capture did not restore"
            )
            if result.image is not None:
                _require(
                    result.image.save(str(self.output / "result.png")),
                    "Could not save complete image",
                )
            self._verify_restored(after)
            if result.image is not None:
                _verify_edge_rows(result, self.output, self.report)
        _require(
            self.desktop.foreground() == self.window.handle,
            "Editor activated before terminal delivery",
        )

    def _verify_unsupported_fixed_sidebar(
        self, result: ScrollResult, after: QImage
    ) -> None:
        """Qualify only the owned sidebar's exact no-image provider rejection."""
        _require(
            self.kind == "edge-sidebar-enter"
            and result.message
            == "No bounded stable post-transition prefix was observed"
            and result.image is None
            and result.restoration == "restored",
            result.message or "Unexpected fixed-sidebar result",
        )
        target = json.loads((self.output / "target.json").read_text(encoding="utf-8"))
        frames = json.loads((self.output / "frames.json").read_text(encoding="utf-8"))
        initial = target["initial_position"]
        _require(
            target["mode"] == "edge-sidebar-enter"
            and set(initial) == {"horizontal", "vertical", "view_size"}
            and initial["vertical"] == 0
            and initial == target["final_observed_position"],
            "Fixed-sidebar provider state was not restored",
        )
        _require(
            result.frames == 4 and len(frames) == 4 and frames[0] == initial,  # noqa: PLR2004
            "Fixed-sidebar frame evidence differs",
        )
        step = 50.0 * initial["view_size"] / (END_PERCENT - initial["view_size"])
        _require(
            all(
                set(frame) == set(initial)
                and frame["horizontal"] == initial["horizontal"]
                and frame["view_size"] == initial["view_size"]
                and math.isclose(
                    frame["vertical"], index * step, rel_tol=0.0, abs_tol=1e-9
                )
                and 0 <= frame["vertical"] < END_PERCENT
                for index, frame in enumerate(frames)
            ),
            "Fixed-sidebar probe trajectory differs",
        )
        _require(
            self.report["movement"] == {"writes": 4, "last_vertical": 0.0},
            "Fixed-sidebar write evidence differs",
        )
        original = QImage(str(self.output / "frame-0.png"))
        probes = [
            QImage(str(self.output / f"frame-{index}.png")) for index in (1, 2, 3)
        ]
        _require(
            all(
                not probe.isNull() and probe.size() == original.size()
                for probe in probes
            )
            and not (
                probes[0].copy(0, 0, original.width(), 1)
                == probes[1].copy(0, 0, original.width(), 1)
                == probes[2].copy(0, 0, original.width(), 1)
            ),
            "Fixed-sidebar probe pixels do not prove a zero-length stable prefix",
        )
        _require(
            not original.isNull()
            and not after.isNull()
            and original == after == QImage(str(self.output / "after.png")),
            "Fixed-sidebar restored pixels differ or are missing",
        )
        self.report.update(product_capture_success=False, capture_failed=True)

    def _verify_restored(self, after: QImage) -> None:
        _require(
            after == QImage(str(self.output / "frame-0.png")),
            "Restored pixels differ",
        )

    def _completed(self) -> None:
        if self.failures:
            return
        result = self.result
        _require(
            result is not None, str(self.report.get("message", "No terminal result"))
        )
        _require(
            "scroll_cancel" not in self.hotkeys.registered,
            "Temporary Escape survived terminal",
        )
        _require(
            self.controller.capture._last_area == self.repeat_area,
            "Scrolling changed Repeat history",
        )
        documents = self.controller.workspace.documents
        self.report["native_hotkey_actions"] = self.actions
        if self.kind == "edge-sidebar-enter":
            _require(
                self.report.get("capture_failed") is True
                and self.report.get("product_capture_success") is False,
                "Fixed-sidebar provider rejection was not verified",
            )
            _require(
                len(documents) == 1 and documents[0] is self.original,
                "Fixed-sidebar rejection changed the existing document",
            )
            _require(
                self.controller.editors == (self.editor,),
                "Fixed-sidebar rejection changed the existing editor",
            )
            self.report.update(
                tabs_after_scroll=len(documents),
                unsupported_fixed_sidebar_criterion_passed=True,
            )
            self.loop.quit()
            return
        if self.kind == "edge-app-hotkey":
            _require(
                "scrolling" in self.actions, "Configured shortcut did not dispatch"
            )
        if self.kind == "edge-app-escape":
            _require(
                "scroll_cancel" in self.actions, "Temporary Escape did not dispatch"
            )
            _require(len(documents) == 1, "Escape delivered another document")
        else:
            _require(len(documents) == 2, "Complete capture did not add one tab")  # noqa: PLR2004
            _require(
                self.controller.editors == (self.editor,),
                "Capture replaced the existing editor",
            )
            _require(
                result is not None and result.image is not None, "No complete pixels"
            )
            if result is not None and result.image is not None:
                _require(
                    self.editor.session.document.image
                    == result.image.convertToFormat(
                        QImage.Format.Format_ARGB32_Premultiplied
                    ),
                    "Tab pixels differ from complete result",
                )
            _require(
                self.desktop.foreground() == int(self.editor.winId()),
                "Delivered editor did not receive focus",
            )
            _require(
                self.editor.grab().save(str(self.output / "editor.png")),
                "Could not save editor evidence",
            )
            self.report["delivery_foreground"] = self.desktop.foreground()
        self.report["tabs_after_scroll"] = len(documents)
        _require(documents[0] is self.original, "Existing document was replaced")
        # Ordinary Repeat intentionally captures the retained physical rectangle,
        # never the tall composite. The editor may now occupy that rectangle.
        self.repeating = True
        self.controller.tray.action("repeat").trigger()

    def _verify_repeat(self) -> None:
        expected = 2 if self.kind == "edge-app-escape" else 3
        if len(self.controller.workspace.documents) < expected:
            return  # Bounded by the existing outer timer, no fixed completion sleep.
        _require(
            len(self.controller.workspace.documents) == expected,
            "Ordinary Repeat did not finish",
        )
        _require(
            self.editor.session.document.image.size()
            == self.original.session.document.image.size(),
            "Repeat used tall composite dimensions",
        )
        _require(
            self.controller.capture._last_area == self.repeat_area,
            "Ordinary Repeat area changed",
        )
        self.report.update(
            repeat_area=asdict(self.repeat_area)
            if self.repeat_area is not None
            else None,
            tabs_after_repeat=expected,
            success=True,
        )
        self.loop.quit()

    def _observe(self) -> None:
        if self.repeating:
            self._verify_repeat()
            return
        overlays = self.controller._scrolling.overlays
        if overlays and not self.selected:
            if self.kind in ("edge-background-click", "edge-background-enter"):
                self._trace_background_activation()
            self.selected = True
            active = next(
                o for o in overlays if o.screen_geometry.physical.contains(self.point)
            )
            self.point = self._gesture_point(active.screen_geometry)
            self.desktop.click(
                active,
                self.point,
                enter=self.kind == "edge-app-hotkey" or self.kind.endswith("-enter"),
                hold_ms=120,
            )
        if (
            self.kind == "edge-app-escape"
            and not self.interrupted
            and (self.output / "frame-0.png").exists()
        ):
            self._keys((0x1B,))
            self.interrupted = True

    def _gesture_point(self, geometry: ScreenGeometry) -> Point:
        """Choose an interior point shared by native and integer Qt cursor grids."""
        local = geometry.to_local(self.point)
        # For 125%, every fourth logical pixel is also an integer native pixel.
        # Enter samples integer QCursor.pos(), unlike fractional mouse positions.
        step = Fraction(str(geometry.scale)).denominator
        logical = Point(round(local.x / step) * step, round(local.y / step) * step)
        derived = geometry.to_physical(logical)
        native = Point(round(derived.x), round(derived.y))
        self.report.update(
            requested_center=asdict(self.point),
            gesture_screen=asdict(geometry),
            gesture_local_point=asdict(logical),
            logical_grid_point=asdict(derived),
            native_requested_point=asdict(native),
        )
        _require(native == derived, "Gesture point is not on the native pixel grid")
        area = self.window.bounds.intersected(geometry.physical)
        _require(
            area.left < native.x < area.right and area.top < native.y < area.bottom,
            "Gesture point is not inside the selected target and overlay",
        )
        _require(
            self._background_bounds is None
            or not self._background_bounds.contains(native),
            "Gesture point is covered by the owned background widget",
        )
        return native

    def _trace_background_activation(self) -> None:
        """Count the selected adapter's native calls without replacing behavior."""
        if self.kind not in ("edge-background-click", "edge-background-enter"):
            return
        _require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Activation tracing requires a disposable hosted runner",
        )
        _require(self._restore_activation is None, "Activation tracing already started")
        state = self.controller._scrolling._state
        if not isinstance(state, ScrollSelectionState):
            raise TypeError("The chooser does not own a native selection adapter")
        user = state._user
        original = cast("Callable[[int], int]", user.SetForegroundWindow)
        self.report["background_activation_writes"] = self._activation_writes
        self.report["background_activation_write_count"] = 0

        def request(window: int) -> int:
            self._activation_writes.append(window)
            self.report["background_activation_write_count"] = len(
                self._activation_writes
            )
            return original(window)

        setattr(user, "SetForegroundWindow", request)  # noqa: B010 - dynamic ctypes export
        self._restore_activation = lambda: setattr(
            user, "SetForegroundWindow", original
        )

    def _keys(self, keys: tuple[int, ...]) -> None:
        _require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Native keys require disposable hosted desktop",
        )
        _require(
            self.desktop.foreground() == self.window.handle,
            "Target lost foreground before test hotkey",
        )
        for key in keys:
            self.desktop.user.keybd_event(key, 0, 0, 0)
        for key in reversed(keys):
            self.desktop.user.keybd_event(key, 0, 2, 0)

    def _choose_menu(self) -> None:
        menu = self.controller.tray.menu
        _require(
            QApplication.activePopupWidget() is menu, "Actual tray popup is not active"
        )
        self.report.update(
            menu_foreground=self.desktop.foreground(), menu_hwnd=int(menu.winId())
        )
        action = self.controller.tray.action("scrolling")
        point = menu.mapToGlobal(menu.actionGeometry(action).center())
        geometry = ScreenGeometry.from_screen(menu.screen())
        physical = geometry.to_physical(
            Point(point.x() - geometry.logical.x, point.y() - geometry.logical.y)
        )
        self.desktop.click(menu, physical, hold_ms=120)

    def _seed_editor(self) -> None:
        """Choose a window through ordinary selection to seed fixed-area Repeat."""
        self.controller.tray.action("region").trigger()
        for _ in range(100):
            if self.controller.capture.overlays:
                break
            QTest.qWait(10)
        overlays = self.controller.capture.overlays
        _require(bool(overlays), "Initial ordinary chooser did not appear")
        active = next(
            overlay
            for overlay in overlays
            if overlay.screen_geometry.physical.contains(self.point)
        )
        # Overlay creation precedes Windows' asynchronous focus acknowledgement.
        # Observe that setup readiness before sending the one native gesture;
        # never force activation or retry an input/capture after failure.
        for _ in range(100):
            QApplication.processEvents()
            if self.desktop.foreground() == int(active.winId()):
                break
            QTest.qWait(10)
        _require(
            self.desktop.foreground() == int(active.winId()),
            "Initial ordinary chooser did not receive foreground",
        )
        self.desktop.click(active, self.point)
        for _ in range(100):
            if self.controller.workspace.documents:
                break
            QTest.qWait(10)
        _require(
            len(self.controller.workspace.documents) == 1, "Initial capture failed"
        )
        self.editor = self.controller.editors[0]
        self.original = self.controller.workspace.documents[0]
        self.repeat_area = self.controller.capture._last_area
        _require(
            self.repeat_area == self.window.bounds,
            "Initial selection did not retain the actual application rectangle",
        )
        self.report["initial_repeat_area"] = (
            asdict(self.repeat_area) if self.repeat_area is not None else None
        )

    def _settle_target_foreground(self) -> None:
        """Observe Qt acknowledge foreign focus before opening the test menu."""
        for _ in range(100):
            QApplication.processEvents()
            foreground = self.desktop.foreground()
            state = QApplication.applicationState()
            self.report.update(setup_foreground=foreground, setup_qt_state=state.name)
            if (
                foreground == self.window.handle
                and state == Qt.ApplicationState.ApplicationInactive
            ):
                self.report["setup_qt_inactive"] = True
                return
            QTest.qWait(10)
        raise RuntimeError("Target foreground did not settle before capture entry")

    def _prepare_background_selection(self) -> None:
        """Foreground owned chrome-only UI while leaving the Edge point exposed."""
        if self.kind not in ("edge-background-click", "edge-background-enter"):
            return
        screen = next(
            screen
            for screen in QApplication.screens()
            if ScreenGeometry.from_screen(screen).physical.contains(self.point)
        )
        geometry = ScreenGeometry.from_screen(screen)
        origin = geometry.to_local(self.window.bounds.top_left)
        widget = QWidget(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self._background = widget
        widget.setWindowTitle("Owned background-selection foreground")
        widget.setGeometry(
            round(geometry.logical.x + origin.x + 16),
            round(geometry.logical.y + origin.y + 8),
            140,
            16,
        )
        widget.show()
        widget.raise_()
        widget.activateWindow()
        handle = int(widget.winId())
        for _ in range(100):
            QApplication.processEvents()
            if self.desktop.foreground() == handle:
                break
            QTest.qWait(10)
        _require(
            self.desktop.foreground() == handle,
            "Owned background-selection window did not become foreground",
        )
        hit = self.desktop.user.WindowFromPoint(
            w.POINT(round(self.point.x), round(self.point.y))
        )
        _require(
            self.desktop.user.GetAncestor(hit, 2) == self.window.handle,
            "The original Edge selection point is not exposed",
        )
        self._background_bounds = Win32WindowApi().frame_bounds(handle)
        _require(
            self._background_bounds.bottom
            <= self.window.bounds.top + 40 * geometry.scale
            and not self._background_bounds.contains(self.point),
            "Owned foreground widget is not confined to browser chrome",
        )
        self.report.update(
            background_hwnd=handle,
            background_foreground=self.desktop.foreground(),
            background_bounds=asdict(self._background_bounds),
            background_selected_window=self.window.handle,
        )
        _require(
            self.source.freeze().image.save(str(self.output / "background-entry.png")),
            "Could not save background-selection setup",
        )

    def run(self) -> None:
        """Exercise app wiring and always close owned UI, helper and hotkeys."""
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(self.loop.quit)
        try:
            self.controller.start()
            _require(
                "scrolling" in self.hotkeys.registered,
                "Configured shortcut unavailable",
            )
            self._seed_editor()
            self.window = _prepare(self.window)  # Setup only, before entry/selection.
            self._settle_target_foreground()
            self._prepare_background_selection()
            self.report["window"] = asdict(self.window)
            self.report["initial_foreground"] = self.desktop.foreground()
            if self.kind == "edge-app-escape":
                before = self.source.freeze().crop(self.window.bounds)
                self._keys((0x22,))  # Ordinary Page Down, setup before selection.
                QTest.qWait(500)
                middle = self.source.freeze().crop(self.window.bounds)
                _require(middle != before, "Page Down did not prepare a non-top view")
                _require(
                    middle.save(str(self.output / "before-escape.png")),
                    "Could not save non-top setup",
                )
            self.timer.start()
            timeout.start(round((MAX_SECONDS + 10.0) * 1000))
            if self.kind == "edge-app-hotkey":
                self._keys((0x11, 0x12, 0x78))  # Actual configured Ctrl+Alt+F9.
            else:
                menu = self.controller.tray.menu
                menu.popup(QPoint(round(self.point.x), round(self.point.y)))
                menu.raise_()
                menu.activateWindow()
                QTimer.singleShot(150, lambda: self._guarded(self._choose_menu))
            self.loop.exec()
            if self.failures:
                raise self.failures[0]
            _require(
                self.report.get("success")
                or (
                    self.kind == "edge-sidebar-enter"
                    and self.report.get("unsupported_fixed_sidebar_criterion_passed")
                ),
                "Integrated capture timed out",
            )
        finally:
            timeout.stop()
            self.timer.stop()
            if self._restore_activation is not None:
                self._restore_activation()
                self._restore_activation = None
            if self._background is not None:
                self._background.close()
                self._background = None
            self.controller.shutdown()
            for record in self.controller.workspace.documents:
                record.session.history.mark_delivered()
            for editor in self.controller.editors:
                editor.close()
            self.source.close()


def _case(kind: str, output: Path, report: dict[str, object]) -> None:
    AppCapture(kind, output, report).run()


if __name__ == "__main__":
    native_main(gui=True, case=_case)
