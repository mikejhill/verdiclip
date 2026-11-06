"""Finite hosted logical-state acceptance; never rendered-pointer qualification."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession
from tests.capture.hosted_fixture_input import require_hosted
from tests.capture.menu_peer import MenuPeer
from tests.capture.owned_cursor_observer import Snapshot, observe
from tests.capture.owned_cursor_setup import (
    OneMove,
    baseline_summary,
    eligible_baseline,
)
from tests.capture.owned_cursor_supervisor import Child
from tests.capture.scroll_peer import ScrollPeer


def assert_restored(child: Child) -> None:
    """Assert the fixed first sample without printing raw snapshots on failure."""
    assert child.first is not None
    summary = child.first.summary()
    print("OWNED_CURSOR_LOGICAL=" + json.dumps(summary, sort_keys=True))  # noqa: T201 - fixed reduced receipt
    assert all(
        value is True
        for key, value in summary.items()
        if key != "rendered_pointer_verified"
    )


def ordinary(directory: Path, mode: str, baseline: Snapshot | None = None) -> None:
    """Run one stationary session; an unsupported entry ends qualification."""
    child = Child(directory)
    try:
        before = observe() if baseline is None else baseline
        print(  # noqa: T201 - fixed setup provenance
            "OWNED_CURSOR_BASELINE="
            + json.dumps(baseline_summary(before), sort_keys=True)
        )
        assert eligible_baseline(before), (
            "unsupported: baseline is not logically visible"
        )
        child.launch()
        child.begin(before)
        during = child.admitted()
        checks = {
            "logical_hidden": not during.visible,
            "stationary": during.position == before.position,
            "shape_unchanged": during.shape == before.shape,
            "foreground_preserved": during.foreground == before.foreground,
            "foreign_gui_preserved": during.gui == before.gui,
        }
        assert all(checks.values()), checks
        if mode == "crash":
            child.crash()
        else:
            result = child.finish(cancel=mode == "cancel")
            assert result["balance_confirmed"] is True
            assert result["hwnd_destroyed"] is True
        assert_restored(child)
    finally:
        child.close()


def menu_ready(peer: MenuPeer, qtbot: QtBot) -> None:
    """One outstanding correlated request within the fixed setup allocation."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        sequence = peer.poll()
        assert sequence is not None
        while not peer.acknowledged(sequence) and time.monotonic() < deadline:
            qtbot.wait(5)
        if peer.acknowledged(sequence) and not peer.preservation_errors():
            return
        assert not peer.error
    raise TimeoutError("owned-menu-setup-timeout")


def refused_menu(directory: Path, qtbot: QtBot) -> None:
    """Foreign popup is not closed or bypassed to obtain cursor ownership."""
    peer = MenuPeer()
    child = Child(directory)
    try:
        peer.start(handoff=True)
        menu_ready(peer, qtbot)
        child.launch()
        child.begin(observe())
        result = child.finish()
        assert result["admitted"] is False
        assert result["reason"] == "foreign-state-refused"
        assert result["balance_attempted"] is False
        assert_restored(child)
        sequence = peer.poll()
        assert sequence is not None
        qtbot.waitUntil(lambda: peer.acknowledged(sequence), timeout=2000)
        assert not peer.preservation_errors()
    finally:
        try:
            child.close()
        finally:
            peer.close()


def changed_menu(directory: Path, qtbot: QtBot, baseline: Snapshot) -> None:
    """Opening an owned foreign popup revokes, never causes corrective takeover."""
    child = Child(directory)
    peer = MenuPeer()
    try:
        assert eligible_baseline(baseline), "unsupported: setup baseline unavailable"
        child.launch()
        child.begin(baseline)
        during = child.admitted()
        hidden = not during.visible
        assert hidden
        child.require_active()
        peer.start(handoff=True)
        menu_ready(peer, qtbot)
        result = child.finish()
        # A legitimate menu transition changed the baseline. Do not label that
        # transition cursor restoration; only correlated popup preservation applies.
        if result["reason"] != "foreign-state-changed":
            raise RuntimeError("unsupported: no proven mid-session foreign transition")
        sequence = peer.poll()
        assert sequence is not None
        qtbot.waitUntil(lambda: peer.acknowledged(sequence), timeout=2000)
        assert not peer.preservation_errors()
        print(  # noqa: T201 - fixed reduced receipt
            'OWNED_CURSOR_INTERVENTION={"menu_preserved":true,"cursor_equality":null,"rendered_pointer_verified":false}'
        )
    finally:
        try:
            child.close()
        finally:
            peer.close()


@pytest.mark.native_desktop
def test_finite_owned_cursor_lifecycle(qtbot: QtBot, tmp_path: Path) -> None:
    """Stop at the first failure/unsupported state; no retry or repair follows."""
    if os.environ.get("VERDICLIP_HOSTED_OWNED_CURSOR") != "1":
        pytest.skip("Dedicated disposable hosted cursor job only")
    require_hosted(os.environ)
    session = DesktopSession.current()
    assert session is not None
    assert session.state == 0
    assert session.flags == 1
    for index in range(2):
        peer = ScrollPeer(tmp_path / f"foreign-{index}", provider="winforms")
        try:
            peer.start()
            qtbot.waitUntil(peer.poll, timeout=20000)
            baseline = OneMove(peer).prepare(qtbot.wait)
            print(  # noqa: T201 - fixed setup provenance
                "OWNED_CURSOR_SETUP="
                + json.dumps(baseline_summary(baseline), sort_keys=True)
            )
            assert peer.identity is not None
            owned_foreground = baseline.foreground == peer.identity[2]
            assert owned_foreground
            for repetition, mode in enumerate(("normal", "cancel", "normal")):
                ordinary(
                    tmp_path / f"case-{index}-{repetition}",
                    mode,
                    baseline if repetition == 0 else None,
                )
            if index == 1:
                refused_menu(tmp_path / "refused", qtbot)

        finally:
            peer.close()

    # Each post-menu positive scenario needs a fresh independently owned foreground.
    for mode in ("changed", "crash"):
        peer = ScrollPeer(tmp_path / ("foreign-" + mode), provider="winforms")
        try:
            peer.start()
            qtbot.waitUntil(peer.poll, timeout=20000)
            baseline = OneMove(peer).prepare(qtbot.wait)
            print(  # noqa: T201 - fixed setup provenance
                "OWNED_CURSOR_SETUP="
                + json.dumps(baseline_summary(baseline), sort_keys=True)
            )
            assert peer.identity is not None
            owned_foreground = baseline.foreground == peer.identity[2]
            assert owned_foreground
            if mode == "changed":
                changed_menu(tmp_path / mode, qtbot, baseline)
            else:
                ordinary(tmp_path / mode, mode, baseline)
        finally:
            peer.close()
