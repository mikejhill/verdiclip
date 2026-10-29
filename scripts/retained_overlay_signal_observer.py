"""Clean-only diagnostic plugin: no Qt imports, event processing or test edits.

The caller supplies the admitted generated source root, validated replay order,
and a bounded JSON emitter. Events retain bounded Python metadata; genuine
controls are deferred separately for mandatory propagation outside Qt.
"""

from __future__ import annotations

import hashlib
import sys
from abc import abstractmethod
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType, TracebackType
from typing import Protocol, cast

from pluggy import HookimplMarker

hookimpl = HookimplMarker("pytest")
TARGET = (
    "tests/ux/test_capture_journeys.py::TestRegionCapture::"
    "test_click_finishes_a_selection_started_with_enter"
)
MAX_EVENTS = 16
MAX_FRAMES = 24
MAX_WALK = 96
MAX_ORDER_ITEMS = 10000
MAX_NODE_LENGTH = 4096
MAX_ERRORS = 8

SERVICE_FILE = "src/verdiclip/capture/service.py"
DECISION_FUNCTIONS = frozenset(
    {"_check_input_owner", "xǁCaptureServiceǁ_check_input_owner__mutmut_orig"}
)
MISSING = object()


def input_owner_decision(
    frame: object, admitted_service_filename: str
) -> dict[str, object] | None:
    """Copy only precomputed primitives; do not evaluate the original decision.

    The caller supplies the exact admitted generated service filename. CPython
    3.13 optimized frames expose a live f_locals proxy; no exact-dict assumption,
    iteration, object formatting, Qt properties or other locals are needed.
    """
    if (
        type(frame) is not FrameType
        or frame.f_code.co_filename != admitted_service_filename
        or frame.f_code.co_name not in DECISION_FUNCTIONS
    ):
        return None
    values = frame.f_locals
    result: dict[str, object] = {}
    for key in ("owns_focus", "lost_focus", "owns_grabs"):
        value = values.get(key, MISSING)
        result[key] = value if type(value) is bool else None
    for key in ("focus", "active"):
        value = values.get(key, MISSING)
        result[key + "_present"] = None if value is MISSING else value is not None
    return result


class Item(Protocol):
    """Only item identity is inspected."""

    nodeid: str


class Request(Protocol):
    """Public fixture ownership port."""

    @property
    @abstractmethod
    def node(self) -> Item:
        """Return the current fixture item."""

    @abstractmethod
    def addfinalizer(self, finalizer: Callable[[], None]) -> None:
        """Register exact owned cleanup."""


class Fixture(Protocol):
    """Only fixture name is inspected."""

    argname: str


class Signal(Protocol):
    """Keep exact receiver identities; never inspect emitted payloads."""

    @abstractmethod
    def connect(self, callback: Callable[..., None]) -> object:
        """Attach the owned callback."""

    @abstractmethod
    def disconnect(self, callback: Callable[..., None]) -> object:
        """Remove only the owned callback."""


class Report(Protocol):
    """Read only the original call result."""

    when: str
    failed: bool


class ExceptionSlot(Protocol):
    """The builtin BaseException getset descriptor, never a subclass override."""

    @abstractmethod
    def __set__(self, instance: BaseException, value: None) -> None:
        """Clear a builtin chain field directly."""


CHAIN_SLOTS = tuple(
    cast("ExceptionSlot", BaseException.__dict__[name])
    for name in ("__traceback__", "__cause__", "__context__")
)


def order_hash(nodes: Sequence[str]) -> str:
    """Use the replay's newline-delimited UTF-8 order convention."""
    return hashlib.sha256("\n".join(nodes).encode("utf-8")).hexdigest()


def storage(service: object) -> dict[str, object]:
    """Read exact plain Python storage, never Qt properties or widget truthiness."""
    values = vars(service)
    result: dict[str, object] = {}
    for key in ("_starting", "_cancelled_start", "_deactivated"):
        value = values.get(key)
        result[key[1:]] = value if type(value) is bool else None
    overlays = values.get("_overlays")
    result["overlay_count"] = len(overlays) if type(overlays) is list else None
    for key in ("_pending", "_frozen", "_watching"):
        result[key[1:] + "_present"] = key in values and values[key] is not None
    return result


@dataclass(eq=False)
class Observer:
    """One clean invocation; caller must check diagnostic_ok after pytest returns."""

    root: Path
    expected_order: tuple[str, ...]
    emit: Callable[[dict[str, object]], None]
    exit_control: type[Exception]
    events: list[dict[str, object]] = field(default_factory=list, init=False)
    errors: list[str] = field(default_factory=list, init=False)
    controls: list[BaseException] = field(default_factory=list, init=False)
    error_stages: list[str] = field(default_factory=list, init=False)
    dropped: int = field(default=0, init=False)
    attempted: int = field(default=0, init=False)
    attached: bool = field(default=False, init=False)
    retired: bool = field(default=False, init=False)
    order_matched: bool = field(default=False, init=False)
    observed_order_hash: str = field(default="", init=False)
    target_call_seen: bool = field(default=False, init=False)
    _allowed: dict[str, str] = field(default_factory=dict, init=False)
    _service_filename: str = field(default="", init=False)
    _retire_callback: Callable[[], None] | None = field(default=None, init=False)
    _scope_started: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Validate bounded order and precompute admitted frame filenames."""
        if type(self.exit_control) is not type or not issubclass(
            self.exit_control, Exception
        ):
            raise ValueError("Require admitted pytest session-exit exception class")
        if (
            type(self.expected_order) is not tuple
            or not 0 < len(self.expected_order) <= MAX_ORDER_ITEMS
            or any(
                type(n) is not str or not n or len(n) > MAX_NODE_LENGTH
                for n in self.expected_order
            )
            or len(set(self.expected_order)) != len(self.expected_order)
        ):
            raise ValueError("Expected order must be a validated unique bounded tuple")
        self._service_filename = str(self.root / SERVICE_FILE)
        for relative in (
            SERVICE_FILE,
            "src/verdiclip/capture/overlay.py",
            "tests/ux/test_capture_journeys.py",
        ):
            self._allowed[str(self.root / relative)] = relative

    @property
    def diagnostic_ok(self) -> bool:
        """A green pytest result alone cannot admit a broken observer."""
        return (
            self.order_matched
            and self.attached
            and self.retired
            and self.target_call_seen
            and not self.errors
        )

    def _error(self, stage: str, error: BaseException) -> None:
        # Ordinary errors retain only fixed metadata, never traceback/payload locals.
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(stage)
            self.error_stages.append(stage)
        if not isinstance(error, Exception) or isinstance(
            error, (self.exit_control, BaseExceptionGroup)
        ):
            # Preserve control object identity for mandatory out-of-Qt propagation.
            # Groups remain opaque: a session control may be nested inside an
            # ExceptionGroup, so preserve the whole group without inspecting it.
            # Strip only this outer object's chains. Opaque group members may
            # retain their own payloads/chains under the control-retention
            # exception; they are never traversed or serialized as metadata.
            for slot in CHAIN_SLOTS:
                slot.__set__(error, None)
            self.controls.append(error)

    def _frames(self) -> list[dict[str, object]]:
        found: list[dict[str, object]] = []
        frame = sys._getframe(1)
        try:
            for _ in range(MAX_WALK):
                if frame is None:
                    break
                relative = self._allowed.get(frame.f_code.co_filename)
                if relative is not None:
                    entry: dict[str, object] = {
                        "file": relative,
                        "function": frame.f_code.co_name,
                        "line": frame.f_lineno,
                    }
                    decision = input_owner_decision(frame, self._service_filename)
                    if decision is not None:
                        entry["precomputed_input_owner_decision"] = decision
                    found.append(entry)
                    if len(found) == MAX_FRAMES:
                        break
                frame = frame.f_back
        finally:
            del frame
        return found

    def _receiver(self, service: object, code: str) -> Callable[..., None]:
        def record(*_payload: object) -> None:
            # Never throw through a Qt signal boundary or inspect its arguments.
            try:
                if self.attempted >= MAX_EVENTS:
                    self.dropped += 1
                    return
                self.attempted += 1
                if self.retired:
                    self._error("recorder", RuntimeError("Callback after retirement"))
                    return
                self.events.append(
                    {
                        "signal": code,
                        "frames": self._frames(),
                        "state": storage(service),
                    }
                )
            except BaseException as error:  # noqa: BLE001 - deferred diagnostic; never throw into Qt
                self._error("recorder", error)

        return record

    @hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items: Sequence[Item]) -> None:
        """Observe the actual final order; never mutate items or their identities."""
        actual = tuple(item.nodeid for item in items)
        self.order_matched = actual == self.expected_order
        self.observed_order_hash = order_hash(actual)
        self.emit(
            {
                "observer_collection_count": len(actual),
                "observer_order_sha256": self.observed_order_hash,
                "observer_order_matches_replay": self.order_matched,
            }
        )
        if not self.order_matched:
            raise RuntimeError("Clean collection order differs from admitted replay")

    @hookimpl(wrapper=True)
    def pytest_fixture_setup(
        self, fixturedef: Fixture, request: Request
    ) -> Generator[None, object, object]:
        """Return the original fixture result; exceptions from setup pass unchanged."""
        result = yield
        if request.node.nodeid != TARGET or fixturedef.argname != "service":
            return result
        if self.attached:
            self._error("connection", RuntimeError("Duplicate target service setup"))
            return result
        connections: list[tuple[Signal, Callable[..., None]]] = []

        def disconnect() -> None:
            if self.retired:
                return
            self.retired = True
            for signal, receiver in reversed(connections):
                try:
                    if signal.disconnect(receiver) is False:
                        self._error(
                            "cleanup", RuntimeError("Disconnect returned false")
                        )
                except BaseException as error:  # noqa: BLE001 - independent disconnects; reported after pytest
                    self._error("cleanup", error)

        try:
            # Yield-fixture cleanup is already registered: ours runs first (LIFO).
            request.addfinalizer(disconnect)
            self.attached = True
            self._retire_callback = disconnect
            for name in ("cancelled", "captured", "failed"):
                signal = cast("Signal", getattr(result, name))
                receiver = self._receiver(result, name.upper())
                # Track before connect: a partial successful connection must retire.
                connections.append((signal, receiver))
                signal.connect(receiver)
        except BaseException as error:  # noqa: BLE001 - original fixture result must survive diagnostic failure
            self._error("connection", error)
        return result

    @hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item: Item) -> Generator[None, Report, Report]:
        """Emit only after the original failed call; never mask its report."""
        report = yield
        if item.nodeid == TARGET and report.when == "call":
            self.target_call_seen = True
        if item.nodeid == TARGET and report.when == "call" and report.failed:
            try:
                self.emit(
                    {
                        "overlay_signal_observer": list(self.events),
                        "dropped_events": self.dropped,
                        "observer_errors": len(self.errors),
                        "observer_error_stages": list(self.error_stages),
                        "post_signal_storage_only": True,
                        "decision_locals_are_precomputed": True,
                        "synchronous_emitter_stack_not_guaranteed": True,
                    }
                )
            except BaseException as error:  # noqa: BLE001 - preserve the original failed report
                self._error("emitter", error)
        return report

    def summary(self) -> dict[str, object]:
        """Return post-finalizer facts without replacing the original outcome."""
        return {
            "diagnostic_ok": self.diagnostic_ok,
            "observer_diagnostic_ok": self.diagnostic_ok,
            "observer_order_matched": self.order_matched,
            "observer_order_sha256": self.observed_order_hash,
            "observer_attached": self.attached,
            "observer_retired": self.retired,
            "target_fixture_seen": self.attached,
            "target_call_seen": self.target_call_seen,
            "signal_events": list(self.events),
            "observer_errors": len(self.errors),
            "observer_error_stages": list(self.error_stages),
            "dropped_events": self.dropped,
            "deferred_controls": len(self.controls),
        }

    def retire(self) -> None:
        """Release exact observer connections if pytest did not run its finalizer."""
        if self._retire_callback is not None:
            self._retire_callback()

    def _finalize(self, original_exit: int) -> tuple[int, list[BaseException]]:
        self.retire()
        try:
            self.emit(self.summary())
        except BaseException as error:  # noqa: BLE001 - propagate controls below; retain clean failure
            self._error("summary_emitter", error)
        controls, self.controls = self.controls, []
        result = (
            original_exit if original_exit != 0 else (0 if self.diagnostic_ok else 1)
        )
        return result, controls

    def finish(self, original_exit: int) -> int:
        """Finalize directly in pure tests; real integration must use scope()."""
        result, controls = self._finalize(original_exit)
        if len(controls) == 1:
            raise controls[0]
        if controls:
            raise BaseExceptionGroup("Deferred diagnostic controls", controls)
        return result

    def scope(self) -> ObservedCall:
        """Own cleanup and final reporting even if the original runner raises."""
        return ObservedCall(self)


@dataclass(eq=False)
class ObservedCall:
    """Mandatory outer lifetime for exactly one original clean runner call."""

    observer: Observer
    result: int | None = field(default=None, init=False)
    _active: bool = field(default=False, init=False)
    _completed: bool = field(default=False, init=False)

    def __enter__(self) -> ObservedCall:
        """Reject reentry or reuse before the original runner executes."""
        if self.observer._scope_started:
            raise RuntimeError("Observer scope cannot be reused")
        self.observer._scope_started = True
        self._active = True
        return self

    def complete(self, original_exit: int) -> None:
        """Record the unchanged runner outcome exactly once while scope is active."""
        if not self._active or self._completed or type(original_exit) is not int:
            raise RuntimeError("Invalid original clean completion")
        self.result = original_exit
        self._completed = True

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        """Unconditionally retire/report and propagate every genuine control."""
        self._active = False
        failures: list[BaseException] = []
        if value is not None:
            failures.append(value)
        elif not self._completed:
            failures.append(RuntimeError("Original clean runner did not complete"))
        original = self.result if self.result is not None and value is None else 1
        self.result, controls = self.observer._finalize(original)
        for control in controls:
            if not any(control is failure for failure in failures):
                failures.append(control)
        if len(failures) == 1:
            if failures[0] is value:
                return False
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("Clean runner and diagnostic controls", failures)
        return False
