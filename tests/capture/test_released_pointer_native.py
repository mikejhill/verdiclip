"""Finite actual v0.12.1-source comparison, not shipped-binary qualification."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.native_cursor import CursorInfo
from tests.capture.native_pointer_cursor import NativeReader, copy_appearance
from tests.capture.native_pointer_driver import Driver, State
from tests.capture.native_pointer_followup import followup
from tests.capture.native_pointer_setup import prepare
from tests.capture.released_pointer_peer import BaselinePeer
from tests.capture.scroll_peer import ScrollPeer
from tests.capture.test_native_pointer_native import expected_cursor
from tests.capture.test_native_pointer_native import (
    owned_source as owned_source,  # noqa: PLC0414 - pytest fixture re-export
)

from verdiclip.capture.input_integrity import GuiThreadInfo
from verdiclip.geometry import Point


def observe(driver: Driver) -> State:
    """Read current state once without imposing the candidate's focus policy."""
    api = driver.api
    cursor, gui, pid = CursorInfo(), GuiThreadInfo(), w.DWORD()
    cursor.size, gui.size = ctypes.sizeof(cursor), ctypes.sizeof(gui)
    foreground = api.GetForegroundWindow()
    thread = api.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
    if (
        not thread
        or not api.GetGUIThreadInfo(thread, ctypes.byref(gui))
        or not api.GetCursorInfo(ctypes.byref(cursor))
        or api.GetForegroundWindow() != foreground
    ):
        raise RuntimeError("baseline-first-observation-unavailable")
    return State(
        int(cursor.flags),
        int(cursor.cursor or 0),
        Point(cursor.point.x, cursor.point.y),
        int(foreground),
        tuple(
            int(item or 0)
            for item in (
                gui.flags,
                gui.active,
                gui.focus,
                gui.capture,
                gui.menu,
                gui.move,
            )
        ),
    )


@pytest.mark.native_desktop
@pytest.mark.parametrize("mode", ["cancel", "crash"])
def test_released_source_first_terminal(
    qtbot: QtBot, owned_source: ScrollPeer, mode: str
) -> None:
    """No menu, synthetic generic overlay, post-start movement or recovery polling."""
    root = Path(os.environ["VERDICLIP_RELEASED_BASELINE_ROOT"])
    assert owned_source.process is not None
    driver = Driver(owned_source.handle, owned_source.process.pid)
    point = prepare(driver, owned_source, qtbot.wait)
    before = driver.read()
    try:
        appearance_matches = (
            copy_appearance(before.cursor, NativeReader()) == expected_cursor()
        )
    except RuntimeError:
        print(  # noqa: T201 - fixed setup facts only
            "RELEASED_POINTER_SETUP="
            + json.dumps(
                {
                    "before_baseline_child": True,
                    "visible": before.flags == 1,
                    "position_matches": before.point == point,
                    "appearance_readable": False,
                }
            )
        )
        raise RuntimeError("released-fixture-appearance-unavailable") from None
    baseline = before.flags == 1 and before.point == point and appearance_matches
    print(  # noqa: T201 - fixed setup facts only
        "RELEASED_POINTER_SETUP="
        + json.dumps(
            {
                "before_baseline_child": True,
                "visible": before.flags == 1,
                "position_matches": before.point == point,
                "appearance_readable": True,
                "appearance_matches": appearance_matches,
            }
        )
    )
    assert baseline, "Released comparison baseline unavailable"
    cross_reference = driver.cross_cursor()
    child = BaselinePeer(root, mode, (int(point.x), int(point.y)))
    try:
        child.start()
        child.act()
        retired = not any(driver.api.IsWindow(handle) for handle in child.handles)
        assert retired, "Released source HWND retirement unconfirmed"
        after = observe(driver)
        appearance = copy_appearance(after.cursor, NativeReader())
        fixture_appearance = expected_cursor()
        checks = {
            "visible": after.flags == 1,
            "position_preserved": after.point == before.point,
            "appearance_preserved": appearance == fixture_appearance,
            "foreign_gui_preserved": (after.foreground, after.gui)
            == (before.foreground, before.gui),
        }
        print(  # noqa: T201 - fixed comparison fields only
            "RELEASED_POINTER_TERMINAL="
            + json.dumps(
                {
                    "source": "0.12.1",
                    "mode": mode,
                    "old_foreground_transferred": child.foreground_owned,
                    "terminal_matches_cross_resource": after.cursor == cross_reference,
                    "terminal_dimensions_match": (appearance.width, appearance.height)
                    == (fixture_appearance.width, fixture_appearance.height),
                    "terminal_hotspot_matches": appearance.hotspot
                    == fixture_appearance.hotspot,
                    "terminal_mask_matches": appearance.mask == fixture_appearance.mask,
                    "physical_input_verified": False,
                    "rendered_pointer_verified": False,
                    **checks,
                },
                sort_keys=True,
            )
        )
        first_ok = all(
            checks[key]
            for key in ("visible", "position_preserved", "appearance_preserved")
        )
        second_ok = followup(
            driver, owned_source, qtbot.wait, checks, fixture_appearance
        )
        child.finish()
        # Old focus behavior is recorded, not coerced into the candidate policy.
        assert first_ok, "Released source restoration mismatch"
        assert second_ok, "Subsequent interaction unavailable or mismatched"
    finally:
        child.close()
