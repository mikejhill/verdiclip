"""Hosted Windows qualification of the actual scrolling tray/controller lifecycle.

Only unrelated OS registrations are substituted. The chooser, hotkey service,
owned helper, delivery, editor tabs and ordinary Repeat are production objects.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import QEventLoop, QPoint, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
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
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.geometry import Point, Rect
from verdiclip.platform.associations import FileAssociations
from verdiclip.platform.hotkeys import HotkeyService
from verdiclip.platform.shortcuts import ShortcutRegistration
from verdiclip.platform.startup import StartupRegistration
from verdiclip.platform.windows import WindowLocator
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
            "Target focus did not naturally return",
        )
        self.report.update(
            selected_point=asdict(point),
            selected_window=window.handle,
            release_input_tick=tick,
            handoff_foreground=self.desktop.foreground(),
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
                bytes(helper._process.readAllStandardError().data()).decode(
                    "utf-8", errors="replace"
                ),
                encoding="utf-8",
            )
        self.report.update(
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
            target["window"] == self.window.handle
            and target["input_tick"] == self.report["release_input_tick"],
            "Child changed target/input baseline",
        )
        area = Rect(**target["area"])
        after = self.source.freeze().crop(area)
        _require(
            after.save(str(self.output / "after.png")), "Could not save final view"
        )
        if self.kind == "edge-app-escape":
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
            _require(
                after == QImage(str(self.output / "frame-0.png")),
                "Restored pixels differ",
            )
            if result.image is not None:
                _require(
                    result.image.save(str(self.output / "result.png")),
                    "Could not save complete image",
                )
                _verify_edge_rows(result, self.output, self.report)
        _require(
            self.desktop.foreground() == self.window.handle,
            "Editor activated before terminal delivery",
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
            self.selected = True
            active = next(
                o for o in overlays if o.screen_geometry.physical.contains(self.point)
            )
            self.desktop.click(active, self.point, enter=self.kind == "edge-app-hotkey")
        if (
            self.kind == "edge-app-escape"
            and not self.interrupted
            and (self.output / "frame-0.png").exists()
        ):
            self._keys((0x1B,))
            self.interrupted = True

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
        self.desktop.click(menu, physical)

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
            timeout.start(35_000)
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
            _require(self.report.get("success"), "Integrated capture timed out")
        finally:
            timeout.stop()
            self.timer.stop()
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
