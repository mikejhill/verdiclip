"""Hosted-only input policy and packet handling, with no native API execution."""

import ctypes
import ctypes.wintypes as w
import os
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from tests.capture.hosted_fixture_input import (
    CursorPreparation,
    HostedFixtureClick,
    absolute_pixel_center,
    absolute_point,
    require_hosted,
)
from tests.capture.native_cursor import CursorInfo
from tests.capture.production_matrix import DriverJournal, InputProfile, MatrixCase
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.geometry import Point, Rect


def environment() -> dict[str, str]:
    """Explicit fake hosted identity; never used to launch a native operation."""
    return {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Windows",
        "RUNNER_ARCH": "X64",
        "GITHUB_REPOSITORY": "mikejhill/verdiclip",
        "VERDICLIP_HOSTED_SCROLLBAR": "1",
        "VERDICLIP_QUALIFICATION_HEAD": "a" * 40,
        "VERDICLIP_HOSTED_SOURCE": "a" * 40,
    }


@pytest.mark.parametrize("key", list(environment()))
def test_missing_hosted_requirement_refuses_input(key: str) -> None:
    """Local/self-hosted/foreign or unpinned contexts are not admitted."""
    values = environment()
    values.pop(key)
    with pytest.raises(RuntimeError):
        require_hosted(values)


def test_exact_hosted_policy_accepts_explicit_scope() -> None:
    """Pure policy success creates no windows or native DLLs."""
    require_hosted(environment())


@pytest.mark.parametrize(
    ("point", "expected"), [(Point(-100, -50), (0, 0)), (Point(99, 49), (65535, 65535))]
)
def test_absolute_coordinates_respect_virtual_origin(
    point: Point, expected: tuple[int, int]
) -> None:
    """Negative virtual origins do not redirect the click outside its target."""
    assert absolute_point(point, Rect(-100, -50, 200, 100)) == expected


def test_outside_desktop_rejected() -> None:
    """The exclusive right edge is not a valid physical pixel."""
    with pytest.raises(ValueError, match="outside"):
        absolute_point(Point(100, 0), Rect(-100, -50, 200, 100))


@pytest.fixture
def driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HostedFixtureClick:
    """Only native declarations/calls are replaced; ownership checks execute."""
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    api = Mock()
    api.EnumWindows.side_effect = lambda callback, _parameter: callback(42, 0)

    def process_id(_handle: int, pointer: object) -> int:
        ctypes.cast(
            cast(ctypes.c_void_p, pointer), ctypes.POINTER(w.DWORD)
        ).contents.value = 123
        return 1

    api.GetWindowThreadProcessId.side_effect = process_id
    api.GetForegroundWindow.return_value = 42
    api.IsWindowVisible.return_value = 1
    api.WindowFromPoint.return_value = 42

    def title(_handle: int, buffer: object, _length: int) -> int:
        cast(
            "ctypes.Array[ctypes.c_wchar]", buffer
        ).value = "VerdiClip synthetic scroll fixture nonce"
        return 1

    api.GetWindowTextW.side_effect = title
    api.GetSystemMetrics.side_effect = {76: 0, 77: 0, 78: 1920, 79: 1080}.__getitem__
    api.SendInput.return_value = 3
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: api, raising=False)
    monkeypatch.setattr(
        ctypes, "WINFUNCTYPE", lambda *_args: lambda function: function, raising=False
    )
    monkeypatch.setattr(ctypes, "sizeof", lambda _type: 40)
    target = Rect(2, 3, 2, 2)
    peer = cast(
        ScrollPeer,
        SimpleNamespace(
            poll=lambda: True,
            target=lambda: target,
            handle=42,
            process=SimpleNamespace(pid=123),
            nonce="nonce",
        ),
    )
    journal = DriverJournal(
        tmp_path, MatrixCase("hosted", "scrollbar-click", InputProfile.INJECTED)
    )
    journal.owned_handles = (42,)
    journal.binding_handle = 42
    journal.prepare_binding(target)
    return HostedFixtureClick(peer, journal)


def test_discovery_and_one_click_are_required(driver: HostedFixtureClick) -> None:
    """The actual acknowledgement contract and one-shot check stay enforced."""
    target = driver.peer.target()
    driver.bind(target)
    driver.journal.binding_ready(target)
    driver.journal.request("scrollbar-click", target)
    driver.click(target, lease_valid=lambda: True, expires_at=time.monotonic() + 40)
    with pytest.raises(RuntimeError, match="fresh armed"):
        driver.click(target, lease_valid=lambda: True, expires_at=time.monotonic() + 40)
    assert cast(Mock, driver.api).SendInput.call_count == 1


def test_foreign_foreground_blocks_before_send(driver: HostedFixtureClick) -> None:
    """Native window discovery never permits foreign foreground input."""
    cast(Mock, driver.api).GetForegroundWindow.return_value = 999
    with pytest.raises(RuntimeError, match="Foreground"):
        driver.bind(driver.peer.target())
    cast(Mock, driver.api).SendInput.assert_not_called()


def test_absent_native_discovery_blocks(driver: HostedFixtureClick) -> None:
    """A fixture-supplied HWND cannot substitute for actual native discovery."""
    cast(Mock, driver.api).EnumWindows.side_effect = None
    cast(Mock, driver.api).EnumWindows.return_value = 1
    with pytest.raises(RuntimeError, match="not discovered"):
        driver.bind(driver.peer.target())
    cast(Mock, driver.api).SendInput.assert_not_called()


@pytest.mark.parametrize("inserted", [0, 1, 2])
def test_partial_input_fails_without_retry(
    driver: HostedFixtureClick, inserted: int
) -> None:
    """Only a known partial button-down receives its matching release."""
    target = driver.peer.target()
    driver.bind(target)
    driver.journal.binding_ready(target)
    driver.journal.request("scrollbar-click", target)
    cast(Mock, driver.api).SendInput.side_effect = [inserted, 1]
    with pytest.raises(RuntimeError, match="insertion failed"):
        driver.click(target, lease_valid=lambda: True, expires_at=time.monotonic() + 40)
    assert cast(Mock, driver.api).SendInput.call_count == (2 if inserted == 2 else 1)


@pytest.mark.parametrize("failure", ["hidden", "title", "point", "retired", "source"])
def test_failed_scene_or_source_prevents_input(
    driver: HostedFixtureClick, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A successful initial environment check cannot authorize changed state."""
    api = cast(Mock, driver.api)
    if failure == "hidden":
        api.IsWindowVisible.return_value = 0
    elif failure == "title":
        api.GetWindowTextW.side_effect = None
    elif failure == "point":
        api.WindowFromPoint.return_value = 999

        def foreign(handle: int, pointer: object) -> int:
            ctypes.cast(
                cast(ctypes.c_void_p, pointer), ctypes.POINTER(w.DWORD)
            ).contents.value = 123 if handle == 42 else 999
            return 1

        api.GetWindowThreadProcessId.side_effect = foreign
    elif failure == "retired":
        driver.journal.retired = True
    else:
        monkeypatch.setenv("VERDICLIP_HOSTED_SOURCE", "b" * 40)
    with pytest.raises(RuntimeError):
        driver.bind(driver.peer.target())
    api.SendInput.assert_not_called()


def test_partial_release_failure_is_explicit(driver: HostedFixtureClick) -> None:
    """Cleanup uncertainty cannot count as a successful mouse transaction."""
    target = driver.peer.target()
    driver.bind(target)
    driver.journal.binding_ready(target)
    driver.journal.request("scrollbar-click", target)
    cast(Mock, driver.api).SendInput.side_effect = [2, 0]
    with pytest.raises(RuntimeError, match="release unconfirmed"):
        driver.click(target, lease_valid=lambda: True, expires_at=time.monotonic() + 40)
    assert driver.sent


def test_click_uses_only_move_down_up_packet_order(driver: HostedFixtureClick) -> None:
    """The single batch contains no keyboard, activation or extra pointer event."""
    target = driver.peer.target()
    driver.bind(target)
    driver.journal.binding_ready(target)
    driver.journal.request("scrollbar-click", target)
    driver.click(target, lease_valid=lambda: True, expires_at=time.monotonic() + 40)
    count, packets, size = cast(Mock, driver.api).SendInput.call_args.args
    assert count == 3
    assert size == 40
    assert [packet.type for packet in packets] == [0, 0, 0]
    assert [packet.payload.mouse.dwFlags for packet in packets] == [0xC001, 2, 4]


@pytest.mark.parametrize("case", ["expired", "lease_lost"])
def test_deadline_or_lease_loss_during_verification_blocks_send(
    driver: HostedFixtureClick, case: str
) -> None:
    """Retained input-now instructions cannot survive loss of the actual lease."""
    target = driver.peer.target()
    driver.bind(target)
    driver.journal.binding_ready(target)
    driver.journal.request("scrollbar-click", target)
    with pytest.raises(RuntimeError, match="expired"):
        driver.click(
            target,
            lease_valid=lambda: case != "lease_lost",
            expires_at=time.monotonic() + (-1 if case == "expired" else 40),
        )
    cast(Mock, driver.api).SendInput.assert_not_called()


@pytest.fixture
def preparation(driver: HostedFixtureClick) -> HostedFixtureClick:
    """A popup owns capture before any binding or broker input contract."""
    driver.journal.binding = None
    driver.journal.owned_handles = (42, 43)
    api = cast(Mock, driver.api)
    api.GetCapture.return_value = 43
    api.IsChild.return_value = False
    api.SendInput.return_value = 1

    def process_id(handle: int, pointer: object) -> int:
        ctypes.cast(
            cast(ctypes.c_void_p, pointer), ctypes.POINTER(w.DWORD)
        ).contents.value = os.getpid() if handle == 43 else 123
        return 1

    def cursor(pointer: object) -> int:
        info = ctypes.cast(
            cast(ctypes.c_void_p, pointer), ctypes.POINTER(CursorInfo)
        ).contents
        info.flags = 2
        info.point = w.POINT(3, 4)
        return 1

    api.GetWindowThreadProcessId.side_effect = process_id
    api.GetCursorInfo.side_effect = cursor
    return driver


def prepare(driver: HostedFixtureClick, **overrides: object) -> dict[str, object]:
    """Invoke the production preparation method with no native functions."""
    result: dict[str, object] = {}
    driver.prepare_cursor(
        driver.peer.target(),
        scope=CursorPreparation(
            owned_capture=43,
            preserved=cast(
                Callable[[], bool], overrides.get("preserved", lambda: True)
            ),
            broker_idle=cast(
                Callable[[], bool], overrides.get("broker_idle", lambda: True)
            ),
            expires_at=cast(float, overrides.get("expires_at", time.monotonic() + 40)),
        ),
        process_events=cast(
            Callable[[], None], overrides.get("process_events", Mock())
        ),
        result=result,
    )
    return result


def test_preparation_is_exactly_one_move_without_visibility_claim(
    preparation: HostedFixtureClick,
) -> None:
    """Suppression may remain after insertion; there are no clicks or retries."""
    pump = Mock()
    result = prepare(preparation, process_events=pump)
    count, packets, size = cast(Mock, preparation.api).SendInput.call_args.args
    assert (count, size) == (1, 40)
    assert packets[0].type == 0
    mouse = packets[0].payload.mouse
    assert (mouse.dx, mouse.dy) == absolute_pixel_center(
        Point(3, 4), Rect(0, 0, 1920, 1080)
    )
    assert (mouse.dwFlags, mouse.mouseData) == (0xC001, 0)
    assert result == {
        "outcome": "inserted-preserved",
        "preserved": True,
        "before_flags": 2,
        "after_flags": 2,
        "inserted": 1,
    }
    pump.assert_called_once_with()
    with pytest.raises(RuntimeError, match="fresh"):
        prepare(preparation)
    assert cast(Mock, preparation.api).SendInput.call_count == 1


@pytest.mark.parametrize(
    "case",
    [
        "capture",
        "same_process_unowned_tree",
        "snapshot",
        "broker",
        "binding",
        "request",
        "expired",
        "nonfinite",
        "too_long",
        "foreground",
        "retired",
        "source",
    ],
)
def test_preparation_guard_prevents_send(
    preparation: HostedFixtureClick,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Every preparation gate is checked before any native input is inserted."""
    api = cast(Mock, preparation.api)
    overrides: dict[str, object] = {}
    if case == "capture":
        api.GetCapture.return_value = 999
    elif case == "same_process_unowned_tree":
        api.WindowFromPoint.return_value = 999
    elif case == "snapshot":
        overrides["preserved"] = lambda: False
    elif case == "broker":
        overrides["broker_idle"] = lambda: False
    elif case == "binding":
        preparation.journal.prepare_binding(preparation.peer.target())
    elif case == "request":
        preparation.journal.requests.append({})
    elif case in {"expired", "nonfinite", "too_long"}:
        overrides["expires_at"] = {
            "expired": time.monotonic() - 1,
            "nonfinite": float("nan"),
            "too_long": time.monotonic() + 60,
        }[case]
    elif case == "foreground":
        api.GetForegroundWindow.return_value = 999
    elif case == "retired":
        preparation.journal.retired = True
    else:
        monkeypatch.setenv("VERDICLIP_HOSTED_SOURCE", "b" * 40)
    with pytest.raises(RuntimeError):
        prepare(preparation, **overrides)
    api.SendInput.assert_not_called()


@pytest.mark.parametrize("inserted", [0, 2])
def test_preparation_refusal_is_not_retried(
    preparation: HostedFixtureClick,
    inserted: int,
) -> None:
    """Even impossible partial/oversized results cannot produce a second MOVE."""
    api = cast(Mock, preparation.api)
    api.SendInput.return_value = inserted
    with pytest.raises(RuntimeError, match="refused"):
        prepare(preparation)
    with pytest.raises(RuntimeError, match="fresh"):
        prepare(preparation)
    assert api.SendInput.call_count == 1


@pytest.mark.parametrize("change", ["capture", "foreground", "position", "snapshot"])
def test_preparation_rechecks_after_event_processing(
    preparation: HostedFixtureClick,
    change: str,
) -> None:
    """A delivered MOVE is not accepted after ownership or preservation loss."""
    api = cast(Mock, preparation.api)
    valid = [True]

    def pump() -> None:
        if change == "capture":
            api.GetCapture.return_value = 999
        elif change == "foreground":
            api.GetForegroundWindow.return_value = 999
        elif change == "position":
            api.GetCursorInfo.side_effect = None
            api.GetCursorInfo.return_value = 1
        else:
            valid[0] = False

    with pytest.raises(RuntimeError):
        prepare(preparation, process_events=pump, preserved=lambda: valid[0])
    assert api.SendInput.call_count == 1
    with pytest.raises(RuntimeError, match="fresh"):
        prepare(preparation)


@pytest.mark.parametrize("point", [Point(-100, -50), Point(3, 4), Point(1819, 1029)])
def test_preparation_coordinates_select_pixel_interior(point: Point) -> None:
    """Normalized coordinates round-trip to the intended physical pixel."""
    desktop = Rect(-100, -50, 1920, 1080)
    x, y = absolute_pixel_center(point, desktop)
    assert (
        int(x * desktop.width / 65536) + desktop.x,
        int(y * desktop.height / 65536) + desktop.y,
    ) == (point.x, point.y)


def test_preparation_checks_deadline_after_event_processing(
    preparation: HostedFixtureClick,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An event-processing pass may not silently extend the original deadline."""
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    def pump() -> None:
        now[0] = 141.0

    with pytest.raises(RuntimeError, match="deadline"):
        prepare(preparation, process_events=pump, expires_at=140.0)
    assert cast(Mock, preparation.api).SendInput.call_count == 1


def test_preparation_accepts_verified_owned_child_destination(
    preparation: HostedFixtureClick,
) -> None:
    """A provider child is admitted only by native ancestry, not PID alone."""
    api = cast(Mock, preparation.api)
    api.WindowFromPoint.return_value = 44
    api.IsChild.side_effect = lambda root, child: (root, child) == (42, 44)
    result = prepare(preparation)
    assert result["outcome"] == "inserted-preserved"
    assert api.SendInput.call_count == 1
    api.IsChild.assert_any_call(42, 44)
