"""Physical-only observation and admission; never generate or repair input."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import override

import pytest
from scripts.physical_scrollbar_qualification import (
    SourceProvenance,
    require_product_imports,
    verify_provenance,
)
from tests.capture.production_matrix import (
    InputProfile,
    ObservedWin32Platform,
    ProductionBrokerDriver,
    native_action_errors,
)

from verdiclip.capture.input_native import HookCallback, MousePacket
from verdiclip.capture.input_raw import RawPointer, RawPointerCallback


def scrollbar_profile(
    *, physical: bool, drag: bool, hosted: bool, delivery: bool
) -> InputProfile:
    """The physical lane cannot inherit injected, delivery or drag-lane authority."""
    if physical and (drag or hosted or delivery):
        raise ValueError("Physical scrollbar qualification requires its own window")
    return InputProfile.PHYSICAL if physical else InputProfile.INJECTED


@dataclass
class PhysicalClickEvidence:
    """Bounded hook/raw-button observations, excluding device IDs and coordinates."""

    hook_down: int = 0
    hook_up: int = 0
    raw_down: int = 0
    raw_up: int = 0
    injected_packets: int = 0
    unexpected_buttons: int = 0
    out_of_order: bool = False
    observation_failed: bool = False
    hook_down_at: float | None = None
    hook_up_at: float | None = None
    raw_down_at: float | None = None
    raw_up_at: float | None = None

    def mouse(self, code: int, message: int, address: int) -> None:
        """Inspect packet provenance without changing the real callback or result."""
        if code != 0:
            return
        packet = MousePacket.from_address(address)
        if packet.flags & 1:
            self.injected_packets += 1
        elif message == 0x201:
            self.hook_down += 1
            self.hook_down_at = time.monotonic()
        elif message == 0x202:
            self.out_of_order |= self.hook_down != 1 or self.hook_up != 0
            self.hook_up += 1
            self.hook_up_at = time.monotonic()
        elif message != 0x200:
            self.unexpected_buttons += 1

    def raw(self, event: RawPointer) -> None:
        """Require actual Raw Input button semantics, not any unrelated raw move."""
        self.unexpected_buttons += int(event.wheel)
        for message, button in event.buttons:
            if message == 0x201 and button == 0:
                self.raw_down += 1
                self.raw_down_at = time.monotonic()
            elif message == 0x202 and button == 0:
                self.out_of_order |= self.raw_down != 1 or self.raw_up != 0
                self.raw_up += 1
                self.raw_up_at = time.monotonic()
            else:
                self.unexpected_buttons += 1

    def complete(self) -> bool:
        """Mixed injected/physical or incomplete button pairs cannot qualify."""
        return (
            self.hook_down == self.hook_up == self.raw_down == self.raw_up == 1
            and self.injected_packets == self.unexpected_buttons == 0
            and not self.out_of_order
            and not self.observation_failed
            and all(
                hook is not None and raw is not None and abs(hook - raw) < 0.25
                for hook, raw in (
                    (self.hook_down_at, self.raw_down_at),
                    (self.hook_up_at, self.raw_up_at),
                )
            )
        )


class PhysicalScrollbarPlatform(ObservedWin32Platform):
    """Add passive counters only to the explicitly selected physical route."""

    def __init__(self) -> None:
        super().__init__()
        self.physical_click = PhysicalClickEvidence()

    @override
    def install(
        self, mouse: HookCallback, keyboard: HookCallback, raw: RawPointerCallback
    ) -> None:
        def observed_mouse(code: int, message: int, address: int) -> int:
            try:
                self.physical_click.mouse(code, message, address)
            except (OSError, RuntimeError, ValueError, MemoryError):
                self.physical_click.observation_failed = True
            return mouse(code, message, address)

        def observed_raw(event: RawPointer) -> None:
            try:
                self.physical_click.raw(event)
            except (OSError, RuntimeError, ValueError, MemoryError):
                self.physical_click.observation_failed = True
            raw(event)

        super().install(observed_mouse, keyboard, observed_raw)


class PhysicalScrollbarDriver(ProductionBrokerDriver):
    """Unchanged product worker/options, with physical-only receipt observation."""

    def __init__(self) -> None:
        super().__init__(InputProfile.PHYSICAL, max_seconds=40)

    @override
    def _native_platform(self) -> ObservedWin32Platform:
        platform = PhysicalScrollbarPlatform()
        self.platforms.append(platform)
        return platform


def physical_input_errors(driver: ProductionBrokerDriver) -> list[str]:
    """A real click, unchanged native route and confirmed cleanup are all required."""
    if (
        driver.profile is not InputProfile.PHYSICAL
        or not driver.native
        or len(driver.platforms) != 1
        or len(driver.workers) != 1
    ):
        return ["Physical scrollbar requires one real native production worker"]
    platform = driver.platforms[0]
    worker = driver.workers[0]
    if (
        not isinstance(platform, PhysicalScrollbarPlatform)
        or not platform.physical_click.complete()
        or not platform.installed
        or not platform.closed
        or platform.desktop_unsafe
        or worker.options.injected_move_mode is not None
        or worker.options.heartbeat_seconds != 1
        or worker.RAW_TIMEOUT_SECONDS != 0.25
        or worker.failure
        or driver.cleanup_errors()
        or native_action_errors(driver, "scrollbar-click")
    ):
        return ["Physical scrollbar click or native cleanup was not proved"]
    return []


def physical_environment(environ: Mapping[str, str], *, selected: bool) -> None:
    """The dedicated launcher and pytest selector must agree before resources exist."""
    declared = environ.get("VERDICLIP_PHYSICAL_SCROLLBAR") == "1"
    if declared != selected:
        raise ValueError("Physical scrollbar flag and launcher provenance disagree")


def admit_physical_scrollbar(request: pytest.FixtureRequest) -> SourceProvenance | None:
    """Reject conflicting authority or source identity before desktop resources."""
    physical = request.config.getoption("--native-scrollbar-physical")
    physical_environment(os.environ, selected=physical)
    scrollbar_profile(
        physical=physical,
        drag=request.config.getoption("--native-broker-physical"),
        hosted=os.environ.get("VERDICLIP_HOSTED_SCROLLBAR") == "1",
        delivery=request.config.getoption("--native-delivery-handoff"),
    )
    if not physical:
        return None
    if request.node.name != "test_native_scrollbar_selection_preserves_popup":
        raise ValueError(
            "Physical scrollbar flag requires the single scrollbar selector"
        )
    if not request.config.getoption("--native-broker-handoff"):
        raise ValueError("Physical scrollbar requires explicit exclusive handoff")
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        raise ValueError(
            "Physical qualification requires isolated no-bytecode bootstrap"
        )
    provenance = verify_provenance(
        harness_root=Path(__file__).resolve().parents[2],
        harness_head=os.environ.get("VERDICLIP_HARNESS_HEAD", ""),
    )
    require_product_imports(Path(provenance.harness_root))
    if os.environ.get("VERDICLIP_QUALIFICATION_HEAD") != provenance.source_head:
        raise ValueError("Physical source receipt does not match checked production")
    if os.environ.get("VERDICLIP_QUALIFICATION_DIRTY") != "false":
        raise ValueError("Physical source receipt must declare the verified clean tree")
    return provenance


def scrollbar_input_receipt(
    driver: ProductionBrokerDriver, *, physical: bool, qualified: bool, generated: bool
) -> dict[str, object]:
    """Keep the existing injected receipt, adding auditable physical-only evidence."""
    receipt = dict(driver.receipt(), input_generated_by_harness=generated)
    if physical:
        receipt["physical_raw_input_qualified"] = qualified
        receipt["physical_click_evidence"] = [
            asdict(platform.physical_click)
            for platform in driver.platforms
            if isinstance(platform, PhysicalScrollbarPlatform)
        ]
    return receipt
