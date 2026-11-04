"""Offline checks of the combined native case's evidence; no desktop operations."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from PySide6.QtGui import QImage
from tests.capture.native_scrollbar import ScrollbarCase, fixture_image, verify_pixels
from tests.capture.production_matrix import (
    InputProfile,
    ProductionBrokerDriver,
    native_action_errors,
)
from tests.capture.scroll_peer import ScrollPeer
from tests.capture.test_scroll_core import Documents


@pytest.mark.parametrize("change", ["crop", "pixel", "alpha", "repeat"])
def test_full_panel_oracle_rejects_incomplete_or_changed_pixels(change: str) -> None:
    """An end-of-scroll flag cannot compensate for missing or changed content."""
    expected = Documents.image(width=32, height=128)
    actual = expected.copy()
    if change == "crop":
        actual = actual.copy(0, 0, 32, 127)
    elif change == "repeat":
        for column in range(actual.width()):
            actual.setPixel(column, 127, actual.pixel(column, 126))
    elif change == "alpha":
        actual.setPixel(0, 127, actual.pixel(0, 127) & 0xFFFFFF)
    else:
        actual.setPixel(0, 127, actual.pixel(0, 127) ^ 1)
    with pytest.raises(AssertionError):
        verify_pixels(actual, expected)


def test_full_panel_oracle_accepts_equal_pixels_across_formats() -> None:
    """Pixel equality does not depend on the image's memory channel order."""
    expected = Documents.image(width=32, height=128)
    verify_pixels(expected.convertToFormat(QImage.Format.Format_RGBA8888), expected)


@pytest.mark.parametrize(
    ("down", "up", "moves", "valid"),
    [
        (1, 1, 1, True),
        (0, 1, 1, False),
        (1, 0, 1, False),
        (2, 2, 1, False),
        (1, 1, 0, False),
    ],
)
def test_scrollbar_requires_one_native_click(
    down: int, up: int, moves: int, *, valid: bool
) -> None:
    """Acknowledgements and extra input pairs cannot certify the one-click UX."""
    driver = cast(
        ProductionBrokerDriver,
        SimpleNamespace(
            platforms=[
                SimpleNamespace(
                    left_down=down,
                    left_up=up,
                    mouse_moves=moves,
                )
            ]
        ),
    )
    assert (not native_action_errors(driver, "scrollbar-click")) is valid


@pytest.mark.parametrize("seconds", [0, -1, 41, float("nan"), float("inf")])
def test_fixture_rejects_unbounded_lease(seconds: float) -> None:
    """An extended acquisition case cannot create an unbounded input lease."""
    with pytest.raises(ValueError, match="bounded"):
        ProductionBrokerDriver(InputProfile.INJECTED, max_seconds=seconds)


def test_cleanup_continues_after_journal_failure(tmp_path: Path) -> None:
    """A failed receipt write still cancels both owners and closes safe resources."""
    calls: list[str] = []

    def fail() -> None:
        raise OSError("journal denied")

    case = cast(
        ScrollbarCase,
        SimpleNamespace(
            journal=SimpleNamespace(retire=fail),
            app=SimpleNamespace(
                controller=SimpleNamespace(
                    capture=SimpleNamespace(cancel=lambda: calls.append("capture"))
                )
            ),
            adapter=SimpleNamespace(cancel=lambda: calls.append("adapter")),
            qtbot=SimpleNamespace(waitUntil=lambda predicate, **_: predicate()),
            retired=lambda: True,
            snapshot=SimpleNamespace(close=lambda: calls.append("snapshot")),
            close_owned_windows=lambda: calls.append("windows"),
            source=SimpleNamespace(close=lambda: calls.append("source")),
            directory=tmp_path,
            passed=True,
            errors=[],
            discoveries=[],
            driver=SimpleNamespace(receipt=dict),
        ),
    )
    with pytest.raises(AssertionError, match="journal denied"):
        ScrollbarCase.close(case)
    assert calls == ["capture", "adapter", "snapshot", "windows", "source"]
    receipt = json.loads((tmp_path / "scrollbar-native.json").read_text())
    assert receipt["passed"] is False
    assert receipt["cleanup_errors"] == ["journal denied"]


@pytest.mark.parametrize("change", ["nonce", "pid", "sequence", "visible", "closed"])
def test_provider_snapshot_rejects_changed_owner(tmp_path: Path, change: str) -> None:
    """A visible-looking stale/foreign fixture cannot authorize input."""
    peer = ScrollPeer(tmp_path)
    peer.process = cast(
        "subprocess.Popen[bytes]", SimpleNamespace(pid=123, poll=lambda: None)
    )
    value = {
        "nonce": peer.nonce,
        "pid": 123,
        "sequence": 1,
        "visible": True,
        "closed": False,
        "started_ticks": 456,
        "hwnd": 789,
    }
    value[change] = {
        "nonce": "other",
        "pid": 124,
        "sequence": 2,
        "visible": False,
        "closed": True,
    }[change]
    (tmp_path / "fixture-state-000001.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="ownership"):
        peer.poll()


def test_provider_snapshot_ignores_partial_and_propagates_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real read denial is never retried or replaced by cached state."""
    peer = ScrollPeer(tmp_path)
    peer.process = cast(
        "subprocess.Popen[bytes]", SimpleNamespace(pid=123, poll=lambda: None)
    )
    (tmp_path / "fixture-state.partial").write_text("unfinished")
    assert not peer.poll()
    (tmp_path / "fixture-state-000001.json").write_text("{}")

    def denied(*_args: object, **_kwargs: object) -> str:
        raise PermissionError("fixture denied")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError, match="fixture denied"):
        peer.poll()


def test_wpf_pixel_oracle_has_all_rows_and_known_corner_channels() -> None:
    """Match the managed fixture formula at both boundaries, without native UI."""
    image = fixture_image(383, 1120)
    for x, y in ((0, 0), (382, 1119)):
        color = image.pixelColor(x, y)
        assert color.getRgb() == (
            (y * 43 + x * 19 + y // 127) % 255,
            (y * 31 + x * 7 + y // 251) % 253,
            (y * 17 + x * 11) % 251,
            255,
        )


def test_provider_stop_write_failure_still_retires_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retirement does not depend on successfully writing the cooperative marker."""
    calls: list[str] = []
    peer = ScrollPeer(tmp_path)
    peer.process = cast(
        "subprocess.Popen[bytes]",
        SimpleNamespace(wait=lambda **_: calls.append("wait")),
    )

    def denied(*_args: object, **_kwargs: object) -> int:
        raise PermissionError("stop marker denied")

    monkeypatch.setattr(Path, "write_text", denied)
    with pytest.raises(PermissionError, match="stop marker denied"):
        peer.close()
    assert calls == ["wait"]


def test_idle_helper_is_not_retirement() -> None:
    """An idle but live UIA helper must keep cleanup unconfirmed."""
    case = cast(
        ScrollbarCase,
        SimpleNamespace(
            driver=SimpleNamespace(workers=[]),
            app=SimpleNamespace(
                controller=SimpleNamespace(capture=SimpleNamespace(is_selecting=False))
            ),
            adapter=SimpleNamespace(busy=False),
            process=SimpleNamespace(worker_pid=123),
        ),
    )
    assert not ScrollbarCase.retired(case)
