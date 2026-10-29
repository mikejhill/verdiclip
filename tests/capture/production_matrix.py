"""Prepared real-broker matrix: external input only, never a SendInput helper."""

from __future__ import annotations

import ctypes
import json
import math
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import override
from uuid import uuid4

from PySide6.QtCore import QObject, qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QWidget
from tests.capture.preservation_harness import PreservationSnapshot

from verdiclip.capture.input_broker import NativeInputBroker
from verdiclip.capture.input_native import (
    HookCallback,
    InputPlatform,
    KeyboardPacket,
    Win32InputPlatform,
)
from verdiclip.capture.input_raw import RawPointer, RawPointerCallback
from verdiclip.capture.input_routing import MoveMode
from verdiclip.capture.input_worker import CaptureInputWorker, InputWorkerOptions
from verdiclip.capture.reviewed import ReviewedCapture
from verdiclip.geometry import Point, Rect


class InputProfile(StrEnum):
    """Injected pointer interpretation is a fixture lane, not raw-device proof."""

    DEFAULT = "production-default"
    INJECTED = "fixture-injected-absolute"
    PHYSICAL = "physical-raw-pointer"


@dataclass(frozen=True, slots=True)
class MatrixCase:
    """One case owns only the listed test hosts and a finite input window."""

    scene: str
    outcome: str
    profile: InputProfile
    repeat: int = 1


CASES = (
    MatrixCase("qt-menu", "escape", InputProfile.DEFAULT),
    MatrixCase("owned-modal", "escape", InputProfile.DEFAULT),
    MatrixCase("qt-menu", "drag", InputProfile.INJECTED),
    MatrixCase("win32-menu", "escape", InputProfile.DEFAULT),
    MatrixCase("win32-menu", "drag", InputProfile.INJECTED),
    MatrixCase("qt-menu", "escape", InputProfile.DEFAULT, repeat=2),
)
PHYSICAL_CASES = tuple(
    MatrixCase(scene, "drag", InputProfile.PHYSICAL)
    for scene in ("qt-menu", "win32-menu")
)


def qualification_plan() -> dict[str, object]:
    """Plan-only callers neither create QApplication nor load a native DLL."""
    return {
        "execute": False,
        "exclusive_minutes": 8,
        "physical_observation_minutes": 2,
        "runtimes": ["Qt 6.11.2", "Qt 6.12.0"],
        "cases_per_runtime": [asdict(case) for case in CASES],
        "physical_cases": [asdict(case) for case in PHYSICAL_CASES],
        "max_case_seconds": 40,
        "max_fixture_hosts": 3,
        "input_source": "human or separately authorized guarded driver",
        "input_generated_by_harness": False,
        "writes_clipboard": False,
        "persists_full_desktop_pixels": False,
        "selected_fixture_pixels": "RAM only; no file, clipboard or editor delivery",
        "production_delivery_qualified": False,
        "capture_surfaces": "one passive overlay per attached display",
        "cleanup": (
            "cancel owned capture, confirm worker stopped+cleaned, "
            "then close only declared fixture hosts"
        ),
        "qualification": "no manifest readiness flag is changed by a test result",
    }


class DriverJournal:
    """Publish instructions; a file acknowledgement can never produce a pass."""

    def __init__(self, directory: Path, case: MatrixCase) -> None:
        self.directory, self.case = directory, case
        self.requests: list[dict[str, object]] = []
        self.owned_handles: tuple[int, ...] = ()
        self.binding_handle = 0
        self.binding: dict[str, object] | None = None
        self.bound = False
        self.retired = False

    def prepare_binding(self, area: Rect) -> None:
        """Offer owned identities before capture, without authorizing any input."""
        if (
            self.binding is not None
            or self.retired
            or not self.owned_handles
            or any(
                type(handle) is not int or not 0 < handle < 2**64
                for handle in self.owned_handles
            )
            or area.is_empty
            or not all(
                math.isfinite(v) for v in (area.x, area.y, area.width, area.height)
            )
        ):
            raise ValueError("Driver binding must be scoped and offered only once")
        if (
            type(self.binding_handle) is not int
            or self.binding_handle not in self.owned_handles
        ):
            raise ValueError("Driver binding anchor must belong to the owned handles")
        self.binding = {
            "nonce": uuid4().hex,
            "phase": "bind-only-no-input",
            "binding_hwnd": self.binding_handle,
            "owned_hwnds": list(self.owned_handles),
            "physical_rectangle": asdict(area.rounded()),
            "input_generated_by_harness": False,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "driver-bind-request.json").write_text(
            json.dumps(self.binding, indent=2), encoding="utf-8"
        )
        sys.stdout.write(
            "VERDICLIP_NATIVE_BIND_REQUEST=" + json.dumps(self.binding) + "\n"
        )
        sys.stdout.flush()

    def binding_ready(self, area: Rect) -> bool:
        """A nonce-bound acknowledgement permits arming, never test completion."""
        if self.retired:
            return False
        if self.binding is None:
            return False
        if (
            list(self.owned_handles) != self.binding["owned_hwnds"]
            or any(type(handle) is not int for handle in self.owned_handles)
            or type(self.binding_handle) is not int
            or self.binding_handle != self.binding["binding_hwnd"]
            or asdict(area.rounded()) != self.binding["physical_rectangle"]
        ):
            raise ValueError("Driver binding scope changed before arming")
        path = self.directory / "driver-bound.json"
        if not path.exists():
            return False
        with path.open(encoding="utf-8") as stream:
            text = stream.read(4097)
        if len(text) > 4096:
            raise ValueError("Driver binding acknowledgement exceeds its frame bound")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return (
                False  # A partial write is not readiness; the deadline still applies.
            )
        if (
            not isinstance(value, dict)
            or value.get("nonce") != self.binding["nonce"]
            or value.get("phase") != "bound-no-input"
            or type(value.get("binding_hwnd")) is not int
            or value.get("binding_hwnd") != self.binding_handle
            or value.get("owned_hwnds") != list(self.owned_handles)
            or not isinstance(value.get("owned_hwnds"), list)
            or not all(type(handle) is int for handle in value["owned_hwnds"])
        ):
            raise ValueError("Driver binding acknowledgement is stale or unrelated")
        rectangle = value.get("physical_rectangle")
        if (
            not isinstance(rectangle, dict)
            or set(rectangle) != {"x", "y", "width", "height"}
            or not all(type(coordinate) is int for coordinate in rectangle.values())
            or rectangle != self.binding["physical_rectangle"]
        ):
            raise ValueError("Driver binding acknowledgement geometry is unrelated")
        self.bound = True
        return True

    def retire(self) -> None:
        """Invalidate delayed input instructions before the fixture is removed."""
        if self.retired:
            return
        self.retired = True
        if self.binding is not None:
            (self.directory / "driver-retired.json").write_text(
                json.dumps(
                    {"nonce": self.binding["nonce"], "phase": "retired-no-input"}
                ),
                encoding="utf-8",
            )

    def request(self, outcome: str, area: Rect) -> None:
        """Only a live capture terminal signal can complete this instruction."""
        if outcome != self.case.outcome or outcome not in {"drag", "escape"}:
            raise ValueError("Driver request does not match its declared case")
        if (
            self.requests
            or area.is_empty
            or not all(
                math.isfinite(v) for v in (area.x, area.y, area.width, area.height)
            )
            or not self.owned_handles
            or any(h < 1 for h in self.owned_handles)
        ):
            raise ValueError("Driver request must be nonempty and issued only once")
        if self.retired or not self.bound or not self.binding_ready(area):
            raise ValueError(
                "Driver must bind the declared fixture before input arming"
            )
        request: dict[str, object] = {
            "nonce": self.binding["nonce"] if self.binding is not None else "",
            "phase": "input-now",
            "scene": self.case.scene,
            "outcome": outcome,
            "profile": self.case.profile.value,
            "physical_rectangle": asdict(area),
            "required": "Escape press/release"
            if outcome == "escape"
            else "left drag top-left to bottom-right",
            "input_generated_by_harness": False,
            "owned_hwnds": self.owned_handles,
            "target_guard": "listed test-owned handles only; stop on driver refusal",
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "driver-request.json").write_text(
            json.dumps(request, indent=2), encoding="utf-8"
        )
        self.requests.append(request)
        sys.stdout.write(
            "VERDICLIP_NATIVE_DRIVER_REQUEST=" + json.dumps(request) + "\n"
        )
        sys.stdout.flush()


class ObservedWin32Platform(Win32InputPlatform):
    """Count real callback receipts without altering or generating their packets."""

    def __init__(self) -> None:
        super().__init__()
        self.raw_packets = 0
        self.key_packets = 0
        self.escape_down = self.escape_up = 0
        self.left_down = self.left_up = self.mouse_moves = 0
        self.installed = self.closed = False

    @override
    def install(
        self, mouse: HookCallback, keyboard: HookCallback, raw: RawPointerCallback
    ) -> None:
        def observed_key(code: int, message: int, address: int) -> int:
            if code == 0:
                self.key_packets += 1
                packet = ctypes.cast(address, ctypes.POINTER(KeyboardPacket)).contents
                if packet.code == 0x1B:
                    self.escape_down += message in (0x100, 0x104)
                    self.escape_up += message in (0x101, 0x105)
            return keyboard(code, message, address)

        def observed_mouse(code: int, message: int, address: int) -> int:
            if code == 0:
                self.left_down += message == 0x201
                self.left_up += message == 0x202
                self.mouse_moves += message == 0x200
            return mouse(code, message, address)

        def observed_raw(event: RawPointer) -> None:
            self.raw_packets += 1
            raw(event)

        super().install(observed_mouse, observed_key, observed_raw)
        self.installed = True

    @override
    def close(self) -> bool:
        """Only successful real teardown confirms native resource retirement."""
        result = super().close()
        if result:
            self.closed = True
        return result


class ProductionBrokerDriver:
    """Use the real broker/worker and Win32 boundary unless explicitly injected.

    Portable tests inject an InputPlatform and are labelled non-native. Native
    execution refuses such a substitution. This object never sends OS input.
    """

    def __init__(
        self,
        profile: InputProfile,
        *,
        platform_factory: Callable[[], InputPlatform] | None = None,
    ) -> None:
        self.profile, self.platform_factory = profile, platform_factory
        self.workers: list[CaptureInputWorker] = []
        self.platforms: list[ObservedWin32Platform] = []

    def _native_platform(self) -> ObservedWin32Platform:
        platform = ObservedWin32Platform()
        self.platforms.append(platform)
        return platform

    @property
    def native(self) -> bool:
        """An injected platform never counts as OS-hook evidence."""
        return self.platform_factory is None

    def broker(self, owner: QObject) -> NativeInputBroker:
        """Keep the real production broker and its native-QPA guard."""
        return NativeInputBroker(
            owner, factory=self.worker, require_windows=self.native
        )

    def worker(self, initial: Point, bounds: Rect) -> CaptureInputWorker:
        """A real native thread starts only when its broker explicitly requests it."""
        worker = CaptureInputWorker(
            initial,
            bounds,
            options=InputWorkerOptions(
                max_seconds=12,
                injected_move_mode=(
                    MoveMode.ABSOLUTE if self.profile is InputProfile.INJECTED else None
                ),
            ),
            platform_factory=self.platform_factory or self._native_platform,
        )
        self.workers.append(worker)
        return worker

    def cleanup_errors(self) -> list[str]:
        """Require every worker to report stopped and cleaned."""
        if not self.workers:
            return ["No production broker worker started"]
        return (
            []
            if all(w.stopped.is_set() and w.cleaned for w in self.workers)
            else ["Production broker cleanup is unconfirmed"]
        )

    def receipt(self) -> dict[str, object]:
        """Keep native and injected/physical proof labels distinct."""
        return {
            "provenance": {
                "source_head": os.environ.get("VERDICLIP_QUALIFICATION_HEAD"),
                "source_dirty": os.environ.get("VERDICLIP_QUALIFICATION_DIRTY"),
                "python": sys.version,
                "qt": qVersion(),
                "qpa": QGuiApplication.platformName()
                if QGuiApplication.instance()
                else None,
            },
            "entry": ("posted WM_HOTKEY; fake registration; global chord unqualified"),
            "input_transport": "Win32 hooks/raw-input"
            if self.native
            else "portable injected platform",
            "native_path_selected": self.native,
            "native_hooks": self.native
            and bool(self.platforms)
            and all(p.installed for p in self.platforms),
            "native_platform_cleanup": self.native
            and bool(self.platforms)
            and all(p.closed for p in self.platforms),
            "profile": self.profile.value,
            "fixture_injected_pointer": self.profile is InputProfile.INJECTED,
            "physical_raw_input_qualified": False,
            "raw_pointer_packets_observed": sum(p.raw_packets for p in self.platforms),
            "keyboard_packets_observed": sum(p.key_packets for p in self.platforms),
            "workers": len(self.workers),
            "cleanup_errors": self.cleanup_errors(),
            "input_generated_by_harness": False,
        }


class MemoryCaptureSink:
    """Observe canonical delivery metadata without retaining or persisting pixels.

    This native-input lane deliberately replaces application output policy. It
    cannot qualify file/clipboard/editor delivery, even when capture is correct.
    """

    def __init__(self) -> None:
        self.count = 0

    def accept(
        self, capture: ReviewedCapture, *, activate_editor: bool = False
    ) -> None:
        """No output sink can be reached even for an unexpected full frame."""
        del capture, activate_editor
        self.count += 1


def cleanup_confirmed(driver: ProductionBrokerDriver, *, selecting: bool) -> bool:
    """A facade's idle flag cannot substitute for independent worker retirement."""
    return not selecting and all(
        w.stopped.is_set() and w.cleaned for w in driver.workers
    )


def native_action_errors(
    driver: ProductionBrokerDriver, outcome: str, *, start: int = 0
) -> list[str]:
    """Installation alone does not establish that the requested input occurred."""
    platforms = driver.platforms[start:]
    if outcome == "escape":
        observed = any(p.escape_down > 0 and p.escape_up > 0 for p in platforms)
    else:
        observed = any(
            p.left_down > 0 and p.left_up > 0 and p.mouse_moves > 0 for p in platforms
        )
    return [] if observed else ["Declared native input pair was not observed"]


class StrictFixtureSnapshot(PreservationSnapshot):
    """Require focus/action identities at readiness and terminal observations.

    Event transitions remain in the shared trace. These identity checks do not
    claim absence of every transient focus event between observations.
    """

    def __init__(self, app: QApplication, windows: tuple[QWidget, ...]) -> None:
        super().__init__(app, windows)
        self.active = QApplication.activeWindow()
        self.focus = QApplication.focusWidget()
        self.actions = tuple(
            (w, w.activeAction()) for w in windows if isinstance(w, QMenu)
        )

    @override
    def errors(self) -> list[str]:
        errors = super().errors()
        if QApplication.activeWindow() is not self.active:
            errors.append("Fixture active window changed")
        if QApplication.focusWidget() is not self.focus:
            errors.append("Fixture focus widget changed")
        if any(menu.activeAction() is not action for menu, action in self.actions):
            errors.append("Fixture menu action changed")
        return errors


def fixture_foreground_errors(foreground: int, allowed: tuple[int, ...]) -> list[str]:
    """Unknown/foreign foreground never authorizes requesting real OS input."""
    return (
        []
        if foreground > 0 and foreground in allowed
        else ["Foreground is not a declared fixture owner"]
    )
