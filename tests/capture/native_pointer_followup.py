"""One separately labeled interaction after an immutable terminal observation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from tests.capture.native_pointer_cursor import (
    Appearance,
    NativeReader,
    copy_appearance,
)
from tests.capture.native_pointer_driver import Driver
from tests.capture.native_pointer_setup import prepare
from tests.capture.scroll_peer import ScrollPeer


def followup(
    driver: Driver,
    peer: ScrollPeer,
    pause: Callable[[int], None],
    first: Mapping[str, bool],
    expected: Appearance,
) -> bool:
    """Caller has confirmed retirement; never change its first result or verdict."""
    result: dict[str, bool | str] = {
        "phase": "subsequent-interaction",
        "first_appearance_preserved": first["appearance_preserved"],
        "qualified": False,
        "physical_input_verified": False,
        "rendered_pointer_verified": False,
    }
    if not all(
        first[k] for k in ("visible", "position_preserved", "foreign_gui_preserved")
    ):
        result["status"] = "prerequisite-unavailable"
        return _report(result)
    stage = "prerequisite"
    try:
        driver.allowed = {driver.fixture: driver.fixture_pid}
        before = driver.read()
        before_gui = before.gui
        driver.guard(before.point)
        stage = "readiness"
        point = prepare(driver, peer, pause, phase="post-retirement")
        stage = "observation"
        after = driver.read()
        checks = {
            "visible": after.flags == 1,
            "position_matches_target": after.point == point,
            "appearance_preserved": copy_appearance(after.cursor, NativeReader())
            == expected,
            "foreign_gui_preserved": after.gui == before_gui,
        }
        result.update(checks)
        result["qualified"] = all(checks.values())
        result["status"] = "observed"
    except (RuntimeError, OSError, ValueError, TypeError):
        result["status"] = stage + "-unavailable"
    return _report(result)


def _report(result: dict[str, bool | str]) -> bool:
    print("POINTER_SUBSEQUENT_INTERACTION=" + json.dumps(result, sort_keys=True))  # noqa: T201 - fixed fields only
    return result["qualified"] is True
