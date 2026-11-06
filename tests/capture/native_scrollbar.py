"""One native product case using the existing driver journal and preservation checks."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast, override

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, qVersion
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot
from shiboken6 import isValid
from tests.capture.hosted_fixture_input import CursorPreparation, HostedFixtureClick
from tests.capture.production_matrix import (
    DriverJournal,
    InputProfile,
    MatrixCase,
    ProductionBrokerDriver,
    StrictFixtureSnapshot,
    cleanup_confirmed,
    fixture_foreground_errors,
    native_action_errors,
)
from tests.capture.scroll_failure_evidence import observe_settling, safe_receipt
from tests.capture.scroll_peer import ScrollPeer
from tests.capture.test_capture_native import RealDesktop
from tests.ux.test_app_journeys import App, AppFactory, make_app

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.overlay import SelectionOverlay
from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind
from verdiclip.capture.scroll.acquisition import ScrollAcquisition
from verdiclip.capture.scroll.binding import ScrollCaptureBinding
from verdiclip.capture.scroll.native_backend import UiaFrameBackend
from verdiclip.capture.scroll.pixels import FrameFiles
from verdiclip.capture.scroll.storage import StripStore
from verdiclip.capture.scroll.targets import SemanticTargets
from verdiclip.capture.scroll.uia_messages import UiaAction, UiaRequest
from verdiclip.capture.scroll.uia_protocol import UiaDiscovery
from verdiclip.capture.scroll.workflow import ScrollProgressPanel
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowLocator
from verdiclip.scrolling import compose_scrolling
from verdiclip.settings import CaptureSettings, Settings


def attest_fixture_point_owners(
    expected: dict[str, int],
    actual: dict[str, int],
    *,
    fixture_identity: tuple[int, int],
    process_id: Callable[[int], int],
    root_handle: Callable[[int], int],
) -> None:
    """Require exact declared controls, each owned by this fixture process/root."""
    fixture_handle, fixture_pid = fixture_identity
    assert set(expected) == set(actual) == {"center", "scrollbar"}
    for handle in set(expected.values()):
        assert handle > 0, "Owned fixture control handle unavailable"
        assert process_id(handle) == fixture_pid, "Owned fixture control PID changed"
        assert root_handle(handle) == fixture_handle, (
            "Owned fixture control root changed"
        )
    assert actual == expected, "Fixture point does not hit its declared control"


def guard_owned_backend(
    backend: UiaFrameBackend,
    *,
    expected_handle: int,
    fixture_safe: bool,
    production_safe: Callable[[UiaFrameBackend], bool],
) -> bool:
    """Additional fixture restrictions retain the product's permanent stop latch."""
    assert backend._reference.window_handle == expected_handle, (
        "UIA target is not owned"
    )
    if not fixture_safe:
        backend._interfered = True
    return production_safe(backend)


def record_operation(events: list[dict[str, object]], name: str, phase: str) -> None:
    """A diagnostic clock/buffer failure must never change the product call."""
    try:
        if len(events) < 128:
            events.append(
                {
                    "operation": name,
                    "phase": phase,
                    "time": time.monotonic(),
                    "thread_cpu": time.thread_time(),
                }
            )
    except (OSError, RuntimeError, MemoryError):
        # Optional trace loss must not prevent or replace the product operation.
        return


@dataclass
class OperationTiming:
    """Constant-space whole-run spans, including calls after trace saturation."""

    calls: int = 0
    timed_calls: int = 0
    raised: int = 0
    clock_failures: int = 0
    max_wall_seconds: float = 0.0
    max_thread_cpu_seconds: float = 0.0
    active_calls: int = 0
    other_thread_calls: int = 0
    _owner_thread: int = field(default_factory=threading.get_ident, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def begin(self) -> tuple[float, float] | None:
        """Count each real invocation even when optional clocks are unavailable."""
        with self._lock:
            self.calls += 1
            self.active_calls += 1
            self.other_thread_calls += threading.get_ident() != self._owner_thread
        try:
            return time.monotonic(), time.thread_time()
        except (OSError, RuntimeError, MemoryError):
            with self._lock:
                self.clock_failures += 1
            return None

    def finish(self, start: tuple[float, float] | None) -> None:
        """Publish a bounded duration without replacing the operation's outcome."""
        if start is not None:
            try:
                wall = time.monotonic() - start[0]
                cpu = time.thread_time() - start[1]
                with self._lock:
                    self.max_wall_seconds = max(self.max_wall_seconds, wall)
                    self.max_thread_cpu_seconds = max(self.max_thread_cpu_seconds, cpu)
                    self.timed_calls += 1
            except (OSError, RuntimeError, MemoryError):
                with self._lock:
                    self.clock_failures += 1
        with self._lock:
            self.active_calls -= 1

    def snapshot(self) -> dict[str, int | float]:
        """Read coherent counters even if failed cleanup leaves a worker active."""
        with self._lock:
            return {
                "calls": self.calls,
                "timed_calls": self.timed_calls,
                "raised": self.raised,
                "clock_failures": self.clock_failures,
                "max_wall_seconds": self.max_wall_seconds,
                "max_thread_cpu_seconds": self.max_thread_cpu_seconds,
                "active_calls": self.active_calls,
                "other_thread_calls": self.other_thread_calls,
            }


def probe_worker_cursor_visibility(
    result: dict[str, object], workers: list[threading.Thread]
) -> None:
    """Measure one balanced thread-local cursor change on the hosted fixture only."""
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("VERDICLIP_HOSTED_SCROLLBAR") != "1"
    ):
        raise RuntimeError("Cursor probe requires the disposable hosted fixture")
    result["cleanup_confirmed"] = False
    result["visibility_verdict"] = "incomplete"
    result["presentation_verified"] = False

    class CursorInfo(ctypes.Structure):
        _fields_ = (
            ("size", w.DWORD),
            ("flags", w.DWORD),
            ("cursor", w.HANDLE),
            ("point", w.POINT),
        )

    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
    api.GetCursorInfo.restype = w.BOOL
    api.ShowCursor.argtypes = [w.BOOL]
    api.ShowCursor.restype = ctypes.c_int

    def visible() -> bool:
        info = CursorInfo()
        info.size = ctypes.sizeof(info)
        if not api.GetCursorInfo(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(info.flags & 1)

    ready, release = threading.Event(), threading.Event()
    result["before"] = visible()

    def worker() -> None:
        hidden_count = api.ShowCursor(0)
        try:
            result["hidden_count"] = hidden_count
            ready.set()
            result["released_in_time"] = release.wait(2)
        finally:
            result["restored_count"] = api.ShowCursor(1)

    thread = threading.Thread(target=worker, daemon=True)
    workers.append(thread)
    thread.start()
    try:
        assert ready.wait(1), "Cursor probe did not become ready"
        result["during"] = visible()
    finally:
        release.set()
        thread.join(1)
    assert not thread.is_alive(), "Cursor probe cleanup is unconfirmed"
    assert result["released_in_time"] is True
    assert result["restored_count"] == cast(int, result["hidden_count"]) + 1
    result["after"] = visible()
    assert result["after"] == result["before"], "Cursor visibility was not restored"
    result["cleanup_confirmed"] = True
    # Balanced cleanup is not proof of suppression: ShowCursor changes only
    # its calling input queue, while passive overlays leave another queue owning
    # the mouse. An already-hidden baseline cannot exercise that distinction.
    result["visibility_verdict"] = (
        "inconclusive-hidden-baseline"
        if not result["before"]
        else "logical-visible-hidden-restored"
        if not result["during"]
        else "logical-visibility-unchanged"
    )


def observe_operation[**P, R](
    events: list[dict[str, object]] | None,
    name: str,
    operation: Callable[P, R],
    *,
    timing: OperationTiming | None = None,
) -> Callable[P, R]:
    """Observe existing calls only; preserve values/exceptions and bound metadata."""

    def mark(phase: str) -> None:
        if events is not None:
            record_operation(events, name, phase)

    callable_operation = cast(Callable[..., object], operation)

    def observed(*args: object, **kwargs: object) -> object:
        start = timing.begin() if timing is not None else None
        mark("entered")
        try:
            result = callable_operation(*args, **kwargs)
        except BaseException:
            if timing is not None:
                with timing._lock:
                    timing.raised += 1
            mark("raised")
            raise
        finally:
            if timing is not None:
                timing.finish(start)
        mark("returned")
        return result

    return cast("Callable[P, R]", observed)


def observe_storage_operations(
    patch: pytest.MonkeyPatch,
) -> dict[str, OperationTiming]:
    """Time actual storage calls without writing worker events to the GUI trace."""
    timings: dict[str, OperationTiming] = {}
    for method in ("prepare", "admit"):
        label = f"strips.{method}"
        timing = timings[label] = OperationTiming()
        patch.setattr(
            StripStore,
            method,
            observe_operation(None, label, getattr(StripStore, method), timing=timing),
        )
    label = "strips.stage"
    timing = timings[label] = OperationTiming()
    descriptor = vars(StripStore)["stage"]
    assert isinstance(descriptor, classmethod)
    patch.setattr(
        StripStore,
        "stage",
        classmethod(observe_operation(None, label, descriptor.__func__, timing=timing)),
    )
    return timings


def verify_deferred_storage(timings: dict[str, OperationTiming]) -> None:
    """A completed native capture must prove staging stayed off the GUI thread."""
    snapshots = {
        method: timings[f"strips.{method}"].snapshot()
        for method in ("prepare", "stage", "admit")
    }
    assert all(item["calls"] > 0 for item in snapshots.values()), (
        "Completed capture did not observe every storage phase"
    )
    assert snapshots["stage"]["other_thread_calls"] == snapshots["stage"]["calls"], (
        "Pixel staging ran on the GUI thread"
    )
    assert all(
        snapshots[method]["other_thread_calls"] == 0 for method in ("prepare", "admit")
    ), "Storage metadata left the GUI thread"
    assert all(item["active_calls"] == 0 for item in snapshots.values()), (
        "Capture cleanup left a storage call active"
    )


def discovery_metadata(discovery: UiaDiscovery) -> list[dict[str, object]]:
    """Bounded owned-provider shape only, without names or runtime identifiers."""
    references = {reference.identity: reference for reference in discovery.references}
    return [
        {
            "kind": node.kind.value,
            "bounds": [
                node.bounds.x,
                node.bounds.y,
                node.bounds.width,
                node.bounds.height,
            ],
            "vertically_scrollable": node.vertically_scrollable,
            "reference": (
                {
                    "vertical_percent": references[node.identity].vertical_percent,
                    "view_size": references[node.identity].view_size,
                }
                if node.identity in references
                else None
            ),
        }
        for node in discovery.nodes[:32]
    ]


def menu_physical_rect(geometry: ScreenGeometry, logical: Rect) -> Rect:
    """Compare an owned Qt popup with the provider's physical viewport."""
    start = geometry.to_physical(
        Point(logical.x - geometry.logical.x, logical.y - geometry.logical.y)
    )
    return Rect(
        start.x,
        start.y,
        logical.width * geometry.scale,
        logical.height * geometry.scale,
    )


class PopupFailureTrace(QObject):
    """Retain only the first owned popup loss, never user content or stack locals."""

    def __init__(self, menu: QMenu, phase: Callable[[], str]) -> None:
        super().__init__()
        self.menu, self.phase = menu, phase
        self.first: dict[str, object] = {}
        menu.installEventFilter(self)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.menu
            and not self.first
            and event.type() in {QEvent.Type.Close, QEvent.Type.Hide}
        ):
            self.first = {
                "event": event.type().name,
                "phase": self.phase(),
                "time": time.monotonic(),
                "stack": [
                    {
                        "file": Path(frame.filename).name,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in traceback.extract_stack(limit=16)
                ],
            }
        return False


def native_fixture_profile() -> tuple[str, str]:
    """Select declared fixture behavior, never infer it from observed output."""
    if os.environ.get("VERDICLIP_HOSTED_SCROLLBAR") != "1":
        return "wpf", "scrolling"
    provider = os.environ.get("VERDICLIP_NATIVE_PROVIDER", "wpf")
    fixture = os.environ.get("VERDICLIP_NATIVE_FIXTURE", "scrolling")
    if (
        provider not in {"wpf", "winforms"}
        or fixture not in {"scrolling", "unsupported-scrollbar"}
        or (fixture == "unsupported-scrollbar" and provider != "winforms")
    ):
        raise ValueError("Unsupported owned fixture/provider combination")
    return provider, fixture


class ScrollbarCase:
    """External driver locally; explicit owned-input driver on disposable CI only."""

    def __init__(
        self,
        qtbot: QtBot,
        request: pytest.FixtureRequest,
        patch: pytest.MonkeyPatch,
        directory: Path,
    ) -> None:
        self.provider, self.fixture = native_fixture_profile()
        self.qtbot, self.directory = qtbot, directory
        self.fixture_readiness: dict[str, object] = {}
        self.input_deadline = time.monotonic() + 50
        self.driver = ProductionBrokerDriver(InputProfile.INJECTED, max_seconds=40)
        patch.setattr("verdiclip.capture.service.make_input_broker", self.driver.broker)
        self.source = RealDesktop()
        request.addfinalizer(self.source.close)
        factory = cast(AppFactory, request.getfixturevalue(make_app.__name__))
        self.app: App = factory(
            Settings(capture=CaptureSettings(open_editor=True)),
            screens=self.source,
            windows=WindowLocator(),
        )
        self.provider_profile: dict[str, object] = {
            "provider": self.provider,
            "fixture": self.fixture,
            "qt": qVersion(),
            "qt_process_scale": None,
            "physical_mixed_dpi_proved": False,
        }
        self.peer = ScrollPeer(
            directory / "provider", provider=self.provider, fixture=self.fixture
        )
        self.menu = QMenu("Owned scrollbar test popup")
        request.addfinalizer(self.close_owned_windows)
        self.operation_trace: list[dict[str, object]] = []
        self.operation_timings, self.settling_evidence = (
            observe_storage_operations(patch),
            observe_settling(patch),
        )
        workflow = compose_scrolling(self.app.controller, data_root=directory)
        assert workflow is not None, "Production scrolling is unavailable"
        self.workflow = workflow
        self.adapter = self.workflow._adapter
        self.process = self.adapter._process
        production_safe = UiaFrameBackend._safe

        def owned_safe(backend: UiaFrameBackend) -> bool:
            return guard_owned_backend(
                backend,
                expected_handle=self.peer.handle,
                fixture_safe=self.safe(),
                production_safe=production_safe,
            )

        patch.setattr(UiaFrameBackend, "_safe", owned_safe)
        original_start = self.process.start

        def start(action: UiaAction, payload: dict[str, object]) -> UiaRequest:
            return observe_operation(
                self.operation_trace, f"uia.{action.value}", original_start
            )(action, payload)

        def completed(request: UiaRequest, _result: object) -> None:
            record_operation(
                self.operation_trace, f"uia.{request.action.value}", "completed"
            )

        patch.setattr(self.process, "start", start)
        self.process.completed.connect(completed)
        # Observe existing calls only. No event pumping, warm-up, heartbeat
        # changes or additional native calls are introduced. Storage uses only
        # synchronized counters, never cross-thread/per-strip trace entries.
        for owner, method, label in (
            (FrameFiles, "consume", "pixels.consume"),
            (ScrollAcquisition, "complete_sample", "acquisition.complete_sample"),
        ):
            timing = self.operation_timings[label] = OperationTiming()
            patch.setattr(
                owner,
                method,
                observe_operation(
                    self.operation_trace, label, getattr(owner, method), timing=timing
                ),
            )
        patch.setattr(
            ScrollProgressPanel,
            "show",
            observe_operation(
                self.operation_trace, "progress.show", ScrollProgressPanel.show
            ),
        )
        patch.setattr(
            ScrollCaptureBinding,
            "start_confirmed",
            observe_operation(
                self.operation_trace,
                "binding.start_confirmed",
                ScrollCaptureBinding.start_confirmed,
            ),
        )
        self.discoveries: list[UiaDiscovery] = []
        self.provider_preflight: dict[str, object] = {}
        self._provider_probe_request: UiaRequest | None = None
        self.adapter.discovered.connect(self.record_discovery)
        self.passed, self.repeated_window_captures = False, 0
        self.positive_checks: dict[str, bool] = {}
        self.snapshot: StrictFixtureSnapshot | None = None
        self.journal = DriverJournal(
            directory,
            MatrixCase("qt-scrollbar-popup", "scrollbar-click", InputProfile.INJECTED),
            input_generated_by_harness=os.environ.get("VERDICLIP_HOSTED_SCROLLBAR")
            == "1",
        )
        self.hosted_input: HostedFixtureClick | None = None
        if os.environ.get("VERDICLIP_HOSTED_SCROLLBAR") == "1":
            self.hosted_input = HostedFixtureClick(self.peer, self.journal)
        self.retirement_probe: dict[str, object] = {}
        self.popup_trace: PopupFailureTrace | None = None
        self.image = QImage()
        self.errors: list[str] = []
        self.app.controller.capture.failed.connect(self.errors.append)

    def safe(self) -> bool:
        """Native movement never outlives its lease or unchanged fixture owners."""
        return (
            self.app.controller.capture.input_active
            and self.snapshot is not None
            and not self.snapshot.errors()
        )

    def prepare(self) -> None:
        """Expose owned content and popup before asking the existing driver to bind."""
        # Retirement itself needs only an owned Qt popup, not WPF or UIA startup.
        self.menu.addAction("Inert owned item")
        self.menu.popup(QPoint(64, 64))
        self.qtbot.waitUntil(self.menu.isVisible)
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        self.snapshot = StrictFixtureSnapshot(app, (self.menu,))
        self.popup_trace = PopupFailureTrace(self.menu, lambda: "retirement_probe")
        self.journal.owned_handles = (int(self.menu.winId()),)
        self.check_passive_retirement()
        self.snapshot.close()
        self.menu.removeEventFilter(self.popup_trace)
        self.menu.close()
        self.snapshot = None
        self.popup_trace = None
        started = time.monotonic()
        try:
            self.peer.start()
            remaining_ms = int((20 - (time.monotonic() - started)) * 1000)
            assert remaining_ms > 0, "Fixture startup exhausted preparation budget"
            # Fixture-only allocation inside unchanged outer/input deadlines.
            # Slow cold WPF construction is not a passing product latency result.
            self.qtbot.waitUntil(self.peer.poll, timeout=remaining_ms)
        finally:
            elapsed = time.monotonic() - started
            self.fixture_readiness = {
                "elapsed_ms": round(elapsed * 1000),
                "exceeded_previous_10s": elapsed > 10,
                "fixture_budget_ms": 20000,
            }
        rectangle = self.peer.state["physical_rectangle"]
        assert isinstance(rectangle, dict)
        width, height = int(rectangle["width"]), self.peer.integer("content_rows")
        assert int(rectangle["height"]) == self.peer.state["viewport_height"], (
            "100% scale required"
        )
        assert 0 < width <= 384
        assert height == 1120
        self.image = fixture_image(width, height)
        geometry = ScreenGeometry.from_screen(self.menu.screen())
        anchor = geometry.to_local(
            Point(int(rectangle["x"]) + width + 80, int(rectangle["y"]))
        )
        self.menu.popup(
            QPoint(
                round(geometry.logical.x + anchor.x),
                round(geometry.logical.y + anchor.y),
            )
        )
        self.qtbot.waitUntil(self.menu.isVisible)
        self.check_provider_profile()
        menu_rect = self.menu.frameGeometry()
        viewport = Rect(**rectangle)
        assert not viewport.intersects(
            menu_physical_rect(
                geometry,
                Rect(
                    menu_rect.x(), menu_rect.y(), menu_rect.width(), menu_rect.height()
                ),
            )
        )
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        self.snapshot = StrictFixtureSnapshot(app, (self.menu,))
        self.popup_trace = PopupFailureTrace(
            self.menu, lambda: str(self.workflow.state)
        )
        self.journal.owned_handles = (self.peer.handle, int(self.menu.winId()))
        self.journal.binding_handle = self.peer.handle
        self.finish_provider_preparation(viewport)

    def finish_provider_preparation(self, viewport: Rect) -> None:
        """Keep optional cursor input after passive and provider preflight checks."""
        self.check_passive_retirement()
        if self.peer.provider == "winforms":
            self.diagnose_provider(viewport)
        self.prepare_driver_binding()

    def record_discovery(self, request: UiaRequest, discovery: UiaDiscovery) -> None:
        """Pre-click inspection can never satisfy the actual scrollbar oracle."""
        if not getattr(self, "discovery_startup_diagnostics", None):
            # Preserve the first bounded lifetime snapshot before warm-frame
            # traffic replaces startup stamps in the rolling diagnostic buffer.
            self.discovery_startup_diagnostics = {
                mode: asdict(trace)
                for mode, trace in self.process.diagnostics_for_workers.items()
            }
        if request is self._provider_probe_request:
            expected = ":".join(
                str(value)
                for value in (
                    self.peer.handle,
                    self.peer.integer("pid"),
                    self.peer.integer("started_ticks"),
                )
            )
            self.provider_preflight.update(
                state="complete"
                if discovery.window_identity == expected
                else "foreign",
                window_matches_fixture=discovery.window_identity == expected,
                nodes=discovery_metadata(discovery),
            )
            return
        self.discoveries.append(discovery)

    def diagnose_provider(self, viewport: Rect) -> None:
        """One read-only center query, inside original case/input deadlines."""
        assert self.peer.poll(), "Owned provider state unavailable"
        assert self.snapshot is not None
        assert not self.snapshot.errors()
        point = viewport.center
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.WindowFromPoint.argtypes = [w.POINT]
        api.WindowFromPoint.restype = w.HWND
        api.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        api.GetWindowThreadProcessId.restype = w.DWORD
        api.GetAncestor.argtypes = [w.HWND, w.UINT]
        api.GetAncestor.restype = w.HWND
        list_handle = self.peer.integer("list_handle")
        expected_owners = {
            "center": list_handle,
            "scrollbar": self.peer.integer("scrollbar_handle")
            if self.fixture == "unsupported-scrollbar"
            else list_handle,
        }

        def process_id(handle: int) -> int:
            pid = w.DWORD()
            api.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            return pid.value

        owners = {
            name: int(
                api.WindowFromPoint(w.POINT(round(location.x), round(location.y))) or 0
            )
            for name, location in (
                ("center", point),
                ("scrollbar", self.peer.target().center),
            )
        }
        self.provider_preflight = {
            "state": "admitting",
            "point": [point.x, point.y],
            "list_handle": list_handle,
            "point_owners": owners,
        }
        attest_fixture_point_owners(
            expected_owners,
            owners,
            fixture_identity=(self.peer.handle, self.peer.integer("pid")),
            process_id=process_id,
            root_handle=lambda handle: int(api.GetAncestor(handle, 2) or 0),
        )
        remaining_ms = int((self.input_deadline - time.monotonic()) * 1000)
        assert remaining_ms > 0, (
            "Provider diagnosis exhausted original case input budget"
        )

        def failed(request: UiaRequest, _message: str) -> None:
            if request is self._provider_probe_request:
                self.provider_preflight["state"] = "failed"

        self.adapter.failed.connect(failed)
        try:
            self.provider_preflight["state"] = "waiting"
            self._provider_probe_request = self.adapter.inspect(self.peer.handle, point)
            self.qtbot.waitUntil(
                lambda: self.provider_preflight["state"] != "waiting",
                timeout=min(11000, remaining_ms),
            )
            assert self.provider_preflight["state"] == "complete", (
                "Owned provider center inspection failed"
            )
            assert not self.snapshot.errors(), "Provider inspection changed the popup"
        finally:
            self.adapter.failed.disconnect(failed)
            # Retain this one identity so even a late diagnostic response can
            # never enter the actual-click discovery list.

    def check_provider_profile(self) -> None:
        """Require the selected Qt runtime and process scale without changing OS DPI."""
        expected_scale = float(os.environ.get("VERDICLIP_EXPECTED_QT_SCALE", "1"))
        assert expected_scale in {1, 1.5, 2}, "Unreviewed Qt process scale"
        assert self.menu.devicePixelRatioF() == expected_scale, (
            "Qt process scale mismatch"
        )
        self.provider_profile["qt_process_scale"] = self.menu.devicePixelRatioF()
        expected_qt = os.environ.get("VERDICLIP_EXPECTED_QT")
        if expected_qt is not None:
            assert qVersion() == expected_qt, "Qt runtime mismatch"

    def check_passive_retirement(self) -> None:
        """Isolate ordinary owned tooltip retirement before hooks, input or UIA."""
        assert self.snapshot is not None
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetForegroundWindow.restype = w.HWND
        before = int(api.GetForegroundWindow() or 0)
        previous = self.retirement_probe
        self.retirement_probe = {
            "stage": "before_show",
            "foreground_before": before,
            "previous_environment": previous,
        }
        assert not fixture_foreground_errors(before, self.journal.owned_handles)
        screen = self.menu.screen()
        geometry = ScreenGeometry.from_screen(screen)
        width, height = int(geometry.physical.width), int(geometry.physical.height)
        assert 0 < width * height <= 16_000_000, "Synthetic overlay pixel budget"
        pixels = QImage(width, height, QImage.Format.Format_RGB32)
        pixels.fill(0xFF404040)
        panel = SelectionOverlay(
            geometry,
            screen,
            FrozenScreen(pixels, geometry.physical),
            (),
            show_magnifier=False,
            external_input=True,
        )

        def retired() -> bool:
            return not isValid(panel)

        try:
            panel.show()
            self.qtbot.waitUntil(lambda: panel.windowHandle().isExposed(), timeout=2000)
            shown = int(api.GetForegroundWindow() or 0)
            self.retirement_probe.update(stage="shown", foreground_shown=shown)
            assert shown == before, "Passive show changed native foreground"
            assert not self.snapshot.errors(), "Passive show changed the popup"
            self.retirement_probe["stage"] = "closing"
            panel.dismiss()
            self.qtbot.waitUntil(retired, timeout=2000)
            QApplication.processEvents()
            errors = self.snapshot.errors()
            after = int(api.GetForegroundWindow() or 0)
            self.retirement_probe.update(
                stage="retired",
                retired=not isValid(panel),
                errors=errors,
                foreground_after=after,
            )
            assert after == before, "Passive retirement changed native foreground"
            assert not errors, "Passive retirement changed the popup"
        finally:
            if isValid(panel):
                panel.dismiss()
                self.qtbot.waitUntil(retired, timeout=2000)

    def prepare_driver_binding(self) -> None:
        """One hosted preparation MOVE precedes binding and input interception."""
        self.cursor_preparation: dict[str, object] = {}
        if self.hosted_input is None:
            self.journal.prepare_binding(self.target())
            return
        snapshot = self.snapshot
        assert snapshot is not None
        self.hosted_input.prepare_cursor(
            self.target(),
            scope=CursorPreparation(
                owned_capture=int(self.menu.winId()),
                preserved=lambda: not snapshot.errors(),
                broker_idle=lambda: (
                    not self.app.controller.capture.input_active
                    and not self.driver.workers
                ),
                expires_at=self.input_deadline,
            ),
            process_events=QApplication.processEvents,
            result=self.cursor_preparation,
        )
        self.journal.prepare_binding(self.target())

    def probe_cursor(self) -> None:
        """Keep this diagnostic out of local desktop handoffs."""
        self.cursor_probe: dict[str, object] = {}
        self.cursor_workers: list[threading.Thread] = []
        if self.hosted_input is not None:
            assert self.snapshot is not None
            assert self.input_deadline - time.monotonic() >= 3
            assert not self.snapshot.errors()
            probe_worker_cursor_visibility(self.cursor_probe, self.cursor_workers)
            assert not self.snapshot.errors(), "Cursor probe changed the popup"

    def target(self) -> Rect:
        """Restrict the actual click to the owned native scrollbar center."""
        self.peer.poll()
        return self.peer.target()

    def run(self) -> None:
        """No replay events or direct Start/Use calls may satisfy this case."""
        self.probe_cursor()
        target = self.target()
        if self.hosted_input is not None:
            self.hosted_input.bind(target)
        self.qtbot.waitUntil(lambda: self.journal.binding_ready(target), timeout=20000)
        assert self.snapshot is not None
        assert not self.snapshot.errors()
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetForegroundWindow.restype = w.HWND
        assert not fixture_foreground_errors(
            int(api.GetForegroundWindow() or 0), self.journal.owned_handles
        )
        self.app.controller.capture_scrolling()
        self.qtbot.waitUntil(
            lambda: self.app.controller.capture.input_active or bool(self.errors),
            timeout=5000,
        )
        assert not self.errors
        self.journal.request("scrollbar-click", target)
        if self.hosted_input is not None:
            assert self.safe()
            assert len(self.driver.workers) == 1
            worker = self.driver.workers[0]
            self.hosted_input.click(
                target,
                expires_at=self.input_deadline,
                lease_valid=lambda: (
                    self.safe() and self.driver.workers == [worker] and worker.active
                ),
            )
        self.qtbot.waitUntil(
            lambda: bool(self.app.controller.workspace.documents) or bool(self.errors),
            timeout=30000,
        )
        if self.fixture == "unsupported-scrollbar":
            self.verify_unsupported()
            return
        self.verify_supported(target)

    def verify_supported(self, target: Rect) -> None:
        """Both scrolling providers must satisfy the complete positive oracle."""
        assert self.snapshot is not None
        assert not self.errors
        self.qtbot.waitUntil(self.retired, timeout=5000)
        records = self.app.controller.workspace.documents
        assert len(records) == 1
        record = records[0]
        assert record.provenance is not None
        assert record.provenance.source_kind is CaptureKind.SCROLLING
        assert record.provenance.completeness is CaptureCompleteness.COMPLETE
        self.positive_checks["complete_provenance"] = True
        assert self.discoveries, "No native UIA discovery received"
        discovery = self.discoveries[0]
        assert (
            SemanticTargets(discovery.nodes).scrollbar_at(
                discovery.hit_identity,
                target.center,
                discovery.window_identity,
                discovery.unresolved_hit,
            )
            is not None
        ), "Native hit is not a verified scrollbar or its child"
        assert self.discoveries[0].references, "Provider has no verified ScrollPattern"
        verify_pixels(record.session.document.image, self.image)
        self.positive_checks["pixel_exact"] = True
        verify_deferred_storage(self.operation_timings)
        self.positive_checks["deferred_storage"] = True
        assert not native_action_errors(self.driver, "scrollbar-click")
        assert not self.snapshot.errors()
        assert cleanup_confirmed(
            self.driver, selecting=self.app.controller.capture.is_selecting
        )
        self.positive_checks["popup_input_cleanup"] = True
        assert self.input_deadline > time.monotonic(), "Snapshot input budget exhausted"
        self.peer.request_snapshot()
        remaining_ms = int((self.input_deadline - time.monotonic()) * 1000)
        assert remaining_ms > 0, "Snapshot observation exhausted existing input budget"
        self.qtbot.waitUntil(self.peer.poll_snapshot, timeout=remaining_ms)
        self.peer.assert_snapshot_restored()
        self.positive_checks["fixture_end_seen"] = True
        self.positive_checks["fixture_restored"] = True
        self.verify_repeated_window_capture()
        assert self.repeated_window_captures == 2
        self.positive_checks["two_subsequent_captures"] = True
        self.passed = True

    def verify_repeated_window_capture(self) -> None:
        """Real pixels and window discovery must admit two subsequent selected tabs."""
        workspace = self.app.controller.workspace
        assert self.snapshot is not None
        original = workspace.documents
        for count in (2, 3):
            assert self.input_deadline - time.monotonic() >= 5, (
                "Repeated window capture exhausted the existing case budget"
            )
            assert not self.snapshot.errors()
            assert not self.app.controller.capture.is_selecting
            self.app.press_hotkey("window")
            self.qtbot.waitUntil(
                lambda expected=count: (
                    len(workspace.documents) == expected or bool(self.errors)
                ),
                timeout=5000,
            )
            assert time.monotonic() < self.input_deadline, (
                "Repeated window capture exceeded the existing case budget"
            )
            assert not self.errors
            records = workspace.documents
            assert len(records) == count
            newest = records[-1]
            assert newest.provenance is not None
            assert newest.provenance.source_kind is CaptureKind.SCREENSHOT
            assert not newest.session.document.image.isNull()
            host = workspace.host_of(newest.identifier)
            assert host is not None
            assert host.session is newest.session, "New capture is not the selected tab"
            assert not self.snapshot.errors(), "Repeated capture changed the popup"
            assert not self.app.controller.capture.is_selecting
            self.repeated_window_captures += 1
        assert workspace.documents[0] is original[0]
        identities = {
            record.provenance.capture_id
            for record in workspace.documents
            if record.provenance is not None
        }
        assert len(identities) == 3, "Repeated capture reused an earlier document"

    def verify_unsupported(self) -> None:
        """Standalone scrollbar without a scroll container must refuse safely."""
        assert self.errors == ["No supported scroll area was found at this point."]
        self.qtbot.waitUntil(self.retired, timeout=5000)
        assert not self.app.controller.workspace.documents, "Refusal delivered output"
        assert self.discoveries, "Refusal must follow actual provider discovery"
        assert all(not item.references for item in self.discoveries)
        assert self.snapshot is not None
        assert not self.snapshot.errors(), "Refusal dismissed or changed the popup"
        assert not native_action_errors(self.driver, "scrollbar-click")
        assert len(self.operation_trace) < 128, "Refusal trace is incomplete"
        assert not any(
            item["operation"] in {"uia.step", "uia.restore", "uia.sample"}
            for item in self.operation_trace
        ), "Unsupported provider must not mutate or capture"
        assert all(
            self.operation_timings[label].snapshot()["calls"] == 0
            for label in ("strips.prepare", "strips.stage", "strips.admit")
        ), "Unsupported provider must not prepare, stage or admit pixels"
        assert self.peer.poll()
        assert self.peer.state["offset"] == 0
        assert self.peer.state["end_seen"] is False
        self.passed = True

    def close(self) -> None:
        """Retire input before touching only declared owned test windows."""
        failures: list[str] = []
        popup_loss: dict[str, object] = {}
        transitions: list[dict[str, str]] = []
        try:
            popup_loss = dict(self.popup_trace.first) if self.popup_trace else {}
            transitions = list(self.snapshot.transitions) if self.snapshot else []
        except RuntimeError as error:
            failures.append(f"Popup observation unavailable: {error}")
        try:
            if self.popup_trace is not None:
                self.menu.removeEventFilter(self.popup_trace)
        except RuntimeError as error:
            failures.append(f"Popup observer detach failed: {error}")
        visible_owned = []
        try:
            visible_owned = [
                {"handle": int(widget.effectiveWinId()), "type": type(widget).__name__}
                for widget in QApplication.topLevelWidgets()
                if widget.isVisible()
            ]
        except RuntimeError as error:
            failures.append(str(error))
        for operation in (
            self.journal.retire,
            self.app.controller.capture.cancel,
            self.adapter.cancel,
        ):
            try:
                operation()
            except (OSError, RuntimeError, AssertionError) as error:
                failures.append(str(error))
        try:
            self.qtbot.waitUntil(
                self.retired,
                timeout=10000,
            )
        except AssertionError as error:
            failures.append(str(error))
        for operation in (
            self.snapshot.close if self.snapshot is not None else lambda: None,
            self.close_owned_windows,
            self.source.close,
        ):
            try:
                operation()
            except (OSError, RuntimeError, AssertionError) as error:
                failures.append(str(error))
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "scrollbar-native.json").write_text(
            json.dumps(
                {
                    "passed": self.passed and not failures and self.retired(),
                    "errors": self.errors,
                    "cleanup_errors": failures,
                    "visible_test_windows_before_cleanup": visible_owned,
                    "first_popup_loss": popup_loss,
                    "passive_retirement_probe": self.retirement_probe,
                    "settling_evidence": safe_receipt(self.settling_evidence.snapshot),
                    "operation_trace": self.operation_trace,
                    "operation_trace_at_capacity": len(self.operation_trace) >= 128,
                    "operation_timings": {
                        label: timing.snapshot()
                        for label, timing in self.operation_timings.items()
                    },
                    "fixture_readiness": self.fixture_readiness,
                    "provider_diagnostics": (
                        asdict(self.process.diagnostics)
                        if self.process.diagnostics is not None
                        else None
                    ),
                    "provider_profile": self.provider_profile,
                    "cursor_preparation": getattr(self, "cursor_preparation", {}),
                    "worker_cursor_probe": getattr(self, "cursor_probe", {}),
                    "subsequent_active_window_captures": getattr(
                        self, "repeated_window_captures", 0
                    ),
                    "subsequent_capture_ingress": (
                        "simulated hotkey callback; real pixels/window lookup"
                    ),
                    "expected_result": (
                        "safe refusal of standalone scrollbar without scroll container"
                        if self.provider_profile.get("fixture")
                        == "unsupported-scrollbar"
                        else "complete pixel-exact capture"
                    ),
                    "positive_checks": getattr(self, "positive_checks", {}),
                    "restoration_snapshot": {
                        "requested": self.peer.snapshot_request is not None,
                        "received": self.peer.snapshot_ack is not None,
                        "offset": self.peer.snapshot_ack["offset"]
                        if self.peer.snapshot_ack is not None
                        else None,
                        "end_seen": self.peer.snapshot_ack["end_seen"]
                        if self.peer.snapshot_ack is not None
                        else None,
                    },
                    "provider_preflight": getattr(self, "provider_preflight", {}),
                    "scrollbar_discoveries": [
                        discovery_metadata(item) for item in self.discoveries[:4]
                    ],
                    "discovery_startup_diagnostics": getattr(
                        self, "discovery_startup_diagnostics", {}
                    ),
                    "provider_worker_diagnostics": {
                        mode: asdict(trace)
                        for mode, trace in self.process.diagnostics_for_workers.items()
                    },
                    "provider_workers_after_cleanup": self.process.worker_pids,
                    "popup_transitions_before_cleanup": transitions,
                    "declared_owned_handles": self.journal.owned_handles,
                    "retired": self.retired(),
                    "input": dict(
                        self.driver.receipt(),
                        input_generated_by_harness=self.hosted_input is not None,
                    ),
                    "entry": "AppController.capture_scrolling; no global hotkey claim",
                    "semantic_hits": [
                        [node.kind.value for node in item.nodes]
                        for item in self.discoveries
                    ],
                    "scope": "native scrollbar interaction; no physical RawInput claim",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert not failures, failures
        assert self.retired(), "Native resources did not retire"

    def retired(self) -> bool:
        """Never hide a fixture while capture or provider resources remain active."""
        if any(worker.is_alive() for worker in getattr(self, "cursor_workers", ())):
            return False
        probe = getattr(self, "cursor_probe", {})
        if probe and probe.get("cleanup_confirmed") is not True:
            return False
        return cleanup_confirmed(
            self.driver, selecting=self.app.controller.capture.is_selecting
        ) and (
            not hasattr(self, "adapter")
            or (not self.adapter.busy and self.process.worker_pid is None)
        )

    def close_owned_windows(self) -> None:
        """Also cover partial construction; unsafe retirement remains a failure."""
        if self.retired():
            try:
                self.menu.close()
            finally:
                self.peer.close()


def verify_pixels(actual: QImage, expected: QImage) -> None:
    """Reject cropping, repeated rows, or any changed channel, including alpha."""
    assert actual.size() == expected.size(), "Captured full-panel dimensions differ"
    assert actual.convertToFormat(
        QImage.Format.Format_RGBA8888
    ) == expected.convertToFormat(QImage.Format.Format_RGBA8888), (
        "Captured full-panel pixels differ"
    )


def fixture_image(width: int, height: int) -> QImage:
    """Match the existing WPF synthetic bitmap, including first and last rows."""
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            pixels[offset : offset + 4] = bytes(
                (
                    (y * 43 + x * 19 + y // 127) % 255,
                    (y * 31 + x * 7 + y // 251) % 253,
                    (y * 17 + x * 11) % 251,
                    255,
                )
            )
    return QImage(pixels, width, height, QImage.Format.Format_RGBA8888).copy()
