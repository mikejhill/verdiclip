"""Native broker lifecycle over replay packets, with no operating-system input."""

from __future__ import annotations

from PySide6.QtCore import QObject
from tests.capture.input_fixture import ReplayInputPlatform

from verdiclip.capture.input_broker import NativeInputBroker
from verdiclip.capture.input_routing import MoveMode
from verdiclip.capture.input_worker import CaptureInputWorker, InputWorkerOptions
from verdiclip.geometry import Point, Rect


class ReplayWorkerTransport:
    """Exercise production broker/worker scheduling, never install a real hook."""

    def __init__(self) -> None:
        self.platforms: list[ReplayInputPlatform] = []
        self.workers: list[CaptureInputWorker] = []

    def broker(self, owner: QObject) -> NativeInputBroker:
        """Explicit injection also permits this harness on offscreen Qt."""
        return NativeInputBroker(owner, factory=self._worker, require_windows=False)

    def _worker(self, initial: Point, bounds: Rect) -> CaptureInputWorker:
        """Bind one worker explicitly to an in-memory platform and finite clock."""
        platform = ReplayInputPlatform()
        platform.anchor = initial
        worker = CaptureInputWorker(
            initial,
            bounds,
            options=InputWorkerOptions(
                max_seconds=15,
                injected_move_mode=MoveMode.ABSOLUTE,
            ),
            platform_factory=lambda: platform,
        )
        self.platforms.append(platform)
        self.workers.append(worker)
        return worker

    def send(self, outcome: str, area: Rect) -> None:
        """Queue native-shaped packets on the fake platform, without SendInput."""
        if not self.workers or not self.workers[-1].active:
            raise ValueError("Input transport is not ready")
        platform = self.platforms[-1]
        if outcome == "escape":
            platform.key(0x100, 0x1B)
            platform.key(0x101, 0x1B)
        elif outcome == "drag":
            platform.mouse(0x200, area.top_left)
            platform.mouse(0x201, area.top_left)
            platform.mouse(0x200, area.bottom_right)
            platform.mouse(0x202, area.bottom_right)
        else:
            raise ValueError("Unknown capture outcome")

    def cleanup_errors(self) -> list[str]:
        """Distinguish an observed cleanup receipt from merely hiding an overlay."""
        if not self.workers:
            return ["No broker worker was started"]
        errors = []
        if not all(
            worker.stopped.is_set() and worker.cleaned for worker in self.workers
        ):
            errors.append("Replay worker cleanup was not confirmed")
        if not all(platform.closed for platform in self.platforms):
            errors.append("Replay platform cleanup was not confirmed")
        if any(platform.forwarded for platform in self.platforms):
            errors.append("Selection packets were forwarded")
        return errors
