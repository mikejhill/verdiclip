"""Offline checks of the combined native case's evidence; no desktop operations."""

import ctypes
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, cast

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot
from tests.capture.native_scrollbar import (
    ScrollbarCase,
    discovery_metadata,
    fixture_image,
    guard_owned_backend,
    menu_physical_rect,
    verify_pixels,
)
from tests.capture.production_matrix import (
    InputProfile,
    ProductionBrokerDriver,
    native_action_errors,
)
from tests.capture.scroll_peer import ScrollPeer, provider_pixels
from tests.capture.test_scroll_core import Documents
from tests.capture.test_scroll_uia_protocol import Payload

from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.scroll.native_backend import UiaFrameBackend
from verdiclip.capture.scroll.uia_messages import UiaRequest
from verdiclip.capture.scroll.uia_protocol import UiaDiscovery
from verdiclip.geometry import Point, Rect


class FunctionPort:
    """Fake ctypes callable supporting signature attributes, no OS invocation."""

    def __init__(self, call: Callable[..., object]) -> None:
        self.call = call

    def __call__(self, *args: object) -> object:
        """Forward only to the supplied deterministic fake."""
        return self.call(*args)


def test_provider_metadata_is_bounded_and_omits_runtime_identifiers() -> None:
    """Owned shape evidence preserves references without exporting opaque IDs."""
    discovery = UiaDiscovery.decode(Payload.discovery(Payload.nodes()))
    metadata = discovery_metadata(discovery)
    assert [node["kind"] for node in metadata] == [
        "vertical_scrollbar",
        "content",
        "window",
    ]
    assert metadata[1]["vertically_scrollable"] is True
    assert metadata[1]["bounds"] == [0, 0, 96, 48]
    assert metadata[1]["reference"] == {"vertical_percent": 0, "view_size": 20}
    assert metadata[0]["reference"] is None
    assert "runtime" not in json.dumps(metadata)
    assert Payload.WINDOW not in json.dumps(metadata)


@pytest.mark.parametrize("outcome", ["complete", "failed", "foreign", "wrong-point"])
def test_provider_center_probe_cannot_satisfy_actual_click_discovery(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Actual probe method uses fake OS/adapter ports and never sends input."""
    request = object()
    discovery = UiaDiscovery.decode(Payload.discovery(Payload.nodes()))
    if outcome == "foreign":
        discovery = UiaDiscovery(
            "foreign", discovery.hit_identity, discovery.nodes, discovery.references
        )
    callbacks: list[object] = []
    inspected: list[tuple[int, Point]] = []

    class SignalPort:
        def connect(self, callback: object) -> None:
            callbacks.append(callback)

        def disconnect(self, callback: object) -> None:
            callbacks.remove(callback)

    def pid_query(_handle: int, pointer: object) -> int:
        ctypes.cast(
            cast("ctypes.c_void_p", pointer), ctypes.POINTER(ctypes.c_ulong)
        ).contents.value = 2
        return 1

    api = SimpleNamespace(
        WindowFromPoint=FunctionPort(
            lambda _point: 99 if outcome == "wrong-point" else 7
        ),
        GetWindowThreadProcessId=FunctionPort(pid_query),
        GetAncestor=FunctionPort(lambda _handle, _flag: 1),
    )
    monkeypatch.setattr(
        "tests.capture.native_scrollbar.ctypes.WinDLL",
        lambda *_args, **_kwargs: api,
        raising=False,
    )
    monkeypatch.setattr("tests.capture.native_scrollbar.time.monotonic", lambda: 10)

    def inspect(handle: int, point: Point) -> object:
        inspected.append((handle, point))
        return request

    case = SimpleNamespace(
        peer=SimpleNamespace(
            poll=lambda: True,
            handle=1,
            integer=lambda key: {
                "list_handle": 7,
                "pid": 2,
                "started_ticks": Payload.TICKS,
            }[key],
            target=lambda: Rect(90, 0, 6, 48),
        ),
        snapshot=SimpleNamespace(errors=list),
        adapter=SimpleNamespace(inspect=inspect, failed=SignalPort()),
        input_deadline=50,
        discoveries=[],
        _provider_probe_request=None,
        provider_preflight={},
    )

    def wait(predicate: object, **options: object) -> None:
        assert options["timeout"] == 11000
        if outcome == "failed":
            cast("Callable[..., object]", callbacks[0])(
                request, "private provider message"
            )
        else:
            ScrollbarCase.record_discovery(
                cast(ScrollbarCase, case), cast("UiaRequest", request), discovery
            )
        assert cast("Callable[[], bool]", predicate)()

    case.qtbot = SimpleNamespace(waitUntil=wait)
    if outcome == "complete":
        ScrollbarCase.diagnose_provider(cast(ScrollbarCase, case), Rect(0, 0, 96, 48))
        assert case.provider_preflight["state"] == "complete"
        assert inspected == [(1, Point(48, 24))]
    else:
        with pytest.raises(AssertionError, match=r"ListBox|inspection failed"):
            ScrollbarCase.diagnose_provider(
                cast(ScrollbarCase, case), Rect(0, 0, 96, 48)
            )
    assert case.discoveries == []
    assert callbacks == []
    assert "private provider message" not in json.dumps(case.provider_preflight)
    if outcome != "wrong-point":
        # Even late delivery of the center response stays diagnostic-only.
        ScrollbarCase.record_discovery(
            cast(ScrollbarCase, case), cast("UiaRequest", request), discovery
        )
        assert case.discoveries == []
        ScrollbarCase.record_discovery(
            cast(ScrollbarCase, case), cast("UiaRequest", object()), discovery
        )
        assert case.discoveries == [discovery]


@pytest.mark.parametrize(
    ("scale", "logical", "expected"),
    [
        (1, Rect(-1800, 50, 100, 20), Rect(-1800, 50, 100, 20)),
        (1.5, Rect(-1200, 50, 100, 20), Rect(-1800, 75, 150, 30)),
        (2, Rect(-900, 50, 100, 20), Rect(-1800, 100, 200, 40)),
    ],
)
def test_popup_overlap_uses_physical_coordinates(
    scale: float, logical: Rect, expected: Rect
) -> None:
    """Scaled Qt coordinates cannot falsely certify a clear physical viewport."""
    geometry = ScreenGeometry(
        name="synthetic-negative-origin",
        logical=Rect(-1920 / scale, 0, 1920 / scale, 1080 / scale),
        physical=Rect(-1920, 0, 1920, 1080),
        scale=scale,
    )
    assert menu_physical_rect(geometry, logical) == expected


def test_unknown_owned_provider_is_rejected_before_start(tmp_path: Path) -> None:
    """Profile selection cannot become an arbitrary fixture command or path."""
    directory = tmp_path / "not-created"
    with pytest.raises(ValueError, match="Unknown owned"):
        ScrollPeer(directory, provider="../foreign")
    assert not directory.exists()


def test_provider_start_retains_child_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold-start failures remain observable without launching a native child."""
    handles: list[BinaryIO] = []

    def launch(_arguments: list[str], **options: object) -> object:
        for key, message in (("stdout", b"phase=ready"), ("stderr", b"fixture error")):
            stream = cast(BinaryIO, options[key])
            handles.append(stream)
            stream.write(message)
        return SimpleNamespace(pid=123)

    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(subprocess, "Popen", launch)
    peer = ScrollPeer(tmp_path / "provider")
    peer.start()
    assert (peer.directory / "fixture-stdout.log").read_bytes() == b"phase=ready"
    assert (peer.directory / "fixture-stderr.log").read_bytes() == b"fixture error"
    assert all(stream.closed for stream in handles)


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
            journal=SimpleNamespace(retire=fail, owned_handles=()),
            app=SimpleNamespace(
                controller=SimpleNamespace(
                    capture=SimpleNamespace(cancel=lambda: calls.append("capture"))
                )
            ),
            adapter=SimpleNamespace(cancel=lambda: calls.append("adapter")),
            qtbot=SimpleNamespace(waitUntil=lambda predicate, **_: predicate()),
            retired=lambda: True,
            popup_trace=None,
            retirement_probe={},
            operation_trace=[],
            fixture_readiness={},
            provider_profile={},
            process=SimpleNamespace(
                diagnostics=None, diagnostics_for_workers={}, worker_pids=()
            ),
            snapshot=SimpleNamespace(
                close=lambda: calls.append("snapshot"), transitions=[]
            ),
            close_owned_windows=lambda: calls.append("windows"),
            source=SimpleNamespace(close=lambda: calls.append("source")),
            directory=tmp_path,
            passed=True,
            errors=[],
            discoveries=[],
            hosted_input=None,
            driver=SimpleNamespace(receipt=dict),
        ),
    )
    with pytest.raises(AssertionError, match="journal denied"):
        ScrollbarCase.close(case)
    assert calls == ["capture", "adapter", "snapshot", "windows", "source"]
    receipt = json.loads((tmp_path / "scrollbar-native.json").read_text())
    assert receipt["passed"] is False
    assert receipt["cleanup_errors"] == ["journal denied"]


@pytest.mark.parametrize(
    "change", ["nonce", "pid", "sequence", "visible", "closed", "activation_complete"]
)
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
        "activation_complete": True,
        "started_ticks": 456,
        "hwnd": 789,
    }
    value[change] = {
        "nonce": "other",
        "pid": 124,
        "sequence": 2,
        "visible": False,
        "closed": True,
        "activation_complete": False,
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


def test_prepared_provider_pixels_match_independent_full_image_oracle() -> None:
    """Removing runtime compilation must preserve every synthetic pixel."""
    pixels = provider_pixels()
    assert len(pixels) == 384 * 1120 * 4
    image = QImage(pixels, 384, 1120, QImage.Format.Format_RGB32)
    verify_pixels(image, fixture_image(384, 1120))


def test_forced_provider_cleanup_is_sticky_without_second_wait(tmp_path: Path) -> None:
    """Repeated finalizers retain the real failure instead of missing-receipt noise."""
    calls: list[str] = []

    def wait(*, timeout: int) -> None:
        calls.append(f"wait:{timeout}")
        if timeout == 5:
            raise subprocess.TimeoutExpired("owned fixture", timeout)

    peer = ScrollPeer(tmp_path)
    peer.process = cast(
        "subprocess.Popen[bytes]",
        SimpleNamespace(wait=wait, kill=lambda: calls.append("kill")),
    )
    for _ in range(2):
        with pytest.raises(AssertionError, match="forced termination"):
            peer.close()
    assert calls == ["wait:5", "kill", "wait:2"]


def test_provider_stop_timer_does_not_wait_for_render_event() -> None:
    """Partial startup has a cooperative stop path before readiness publication."""
    script = Path(__file__).with_name("provider_fixture.ps1").read_text()
    assert "Add-Type -TypeDefinition" not in script
    assert script.index("$timer.Start()") < script.index("$window.ShowDialog()")
    assert script.index("try {") < script.index("Add-Type -AssemblyName")
    assert "if ($null -ne $timer) { $timer.Stop() }" in script
    assert "if (-not $ready -or -not $window.IsLoaded -or $closed) { return }" in script
    rendered = script.split("$window.Add_ContentRendered({", 1)[1].split("})", 1)[0]
    assert rendered.index("if ($script:ready)") < rendered.index("$window.Activate()")
    assert rendered.index("$window.Activate()") < rendered.index(
        "$script:ready = $true"
    )
    assert rendered.index("$script:ready = $true") < rendered.index(
        "Write-FixtureState"
    )


@pytest.mark.parametrize("failure", ["kill", "wait"])
def test_uncertain_forced_exit_is_verified_on_later_cleanup(
    tmp_path: Path, failure: str
) -> None:
    """An unsuccessful termination attempt cannot discard owned-child tracking."""
    calls: list[str] = []
    short_waits = 0

    def wait(*, timeout: int) -> None:
        nonlocal short_waits
        calls.append(f"wait:{timeout}")
        if timeout == 5:
            raise subprocess.TimeoutExpired("owned fixture", timeout)
        short_waits += 1
        if failure == "wait" and short_waits == 1:
            raise subprocess.TimeoutExpired("owned fixture", timeout)

    def kill() -> None:
        calls.append("kill")
        if failure == "kill":
            raise PermissionError("termination denied")

    peer = ScrollPeer(tmp_path)
    peer.process = cast(
        "subprocess.Popen[bytes]", SimpleNamespace(wait=wait, kill=kill)
    )
    with pytest.raises((PermissionError, subprocess.TimeoutExpired)):
        peer.close()
    assert not peer._exit_confirmed
    with pytest.raises(AssertionError, match="forced termination"):
        peer.close()
    assert peer._exit_confirmed
    assert calls.count("kill") == 1
    assert calls[-1] == "wait:2"


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


def test_retirement_failure_precedes_any_provider_start(
    qapp: QApplication, qtbot: QtBot
) -> None:
    """A failed local surface check must not launch the unrelated WPF provider."""
    assert QApplication.instance() is qapp
    starts: list[bool] = []

    def failed_probe() -> None:
        raise AssertionError("retirement failed")

    case = SimpleNamespace(
        menu=QMenu(),
        qtbot=qtbot,
        journal=SimpleNamespace(owned_handles=()),
        peer=SimpleNamespace(start=lambda: starts.append(True)),
        check_passive_retirement=failed_probe,
    )
    try:
        with pytest.raises(AssertionError, match="retirement failed"):
            ScrollbarCase.prepare(cast(ScrollbarCase, case))
        assert not starts
    finally:
        if hasattr(case, "snapshot"):
            case.snapshot.close()
        if hasattr(case, "popup_trace"):
            case.menu.removeEventFilter(case.popup_trace)
        case.menu.close()
        case.menu.deleteLater()


def test_fixture_interference_cannot_recover_for_restore() -> None:
    """The actual production safety method retains a transient fixture failure."""
    backend = cast(
        UiaFrameBackend,
        SimpleNamespace(
            _reference=SimpleNamespace(window_handle=42),
            _options=SimpleNamespace(input_unchanged=lambda: True),
            _cancelled=False,
            _interfered=False,
        ),
    )
    for safe in (True, False, True):
        result = guard_owned_backend(
            backend,
            expected_handle=42,
            fixture_safe=safe,
            production_safe=UiaFrameBackend._safe,
        )
        assert result is (safe and not backend._interfered)
    assert backend._interfered
    with pytest.raises(AssertionError, match="not owned"):
        guard_owned_backend(
            backend,
            expected_handle=43,
            fixture_safe=True,
            production_safe=lambda _: pytest.fail("Foreign target reached product"),
        )
