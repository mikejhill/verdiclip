"""Optional hosted failure profiler; no pytest/Qt/application imports at module load."""

from __future__ import annotations

import hashlib
import inspect
import os
import sys
import time
from abc import abstractmethod
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field
from functools import wraps
from typing import Protocol

from pluggy import HookimplMarker

hookimpl = HookimplMarker("pytest")
TARGET = (
    "tests/ux/test_app_journeys.py::TestAfterCapture::"
    "test_hotkey_opens_focused_editor_with_select_tool"
)
MAX_PHASES = 64
MAX_ERRORS = 16


@dataclass(frozen=True)
class Patch:
    """Explicit class attribute and fixed phase label."""

    owner: type
    attribute: str
    phase: str


@dataclass(frozen=True)
class Bindings:
    """Lazy hosted imports supply descriptors without executing constructors."""

    patches: tuple[Patch, ...]
    exit_control: type[Exception]
    labels: Callable[[], dict[str, object]]


class Item(Protocol):
    """Only the admitted item identity and already-collected module are used."""

    nodeid: str
    module: CollectedModule


class CollectedModule(Protocol):
    """Known classes from the admitted already-collected test module."""

    App: type
    FakeDesktop: type


class Manager(Protocol):
    """Public plugin registration boundary."""

    @abstractmethod
    def register(self, plugin: object, name: str) -> object:
        """Register the standalone profiler instance."""


class Config(Protocol):
    """Only explicit option labels and registration are read."""

    pluginmanager: Manager

    @abstractmethod
    def getoption(self, name: str, default: object = None) -> object:
        """Read selected CLI options without environment inventory."""


class Session(Protocol):
    """Final selected items and original exit code."""

    items: Sequence[Item]
    exitstatus: int


@dataclass(eq=False)
class Trace:
    """Inclusive phase clocks with original-operation forwarding on clock failure."""

    wall: Callable[[], int] = time.perf_counter_ns
    process: Callable[[], int] = time.process_time_ns
    thread: Callable[[], int] = time.thread_time_ns
    exit_control: type[Exception] | None = None
    phases: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    controls: list[BaseException] = field(default_factory=list)
    attempted: int = 0
    stage: str = "setup"

    def error(self, stage: str, error: BaseException) -> None:
        """Retain fixed metadata; defer genuine controls without traceback locals."""
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(stage)
        if (
            not isinstance(error, Exception)
            or isinstance(error, BaseExceptionGroup)
            or (self.exit_control is not None and isinstance(error, self.exit_control))
        ):
            for name in ("__traceback__", "__cause__", "__context__"):
                BaseException.__dict__[name].__set__(error, None)
            self.controls.append(error)

    def clocks(self) -> tuple[int, int, int]:
        """Read only the three Python clocks, with no IO or inventory."""
        return self.wall(), self.process(), self.thread()

    def wrap(
        self, label: str, original: Callable[..., object]
    ) -> Callable[..., object]:
        """Preserve arguments, result and original exceptions through one phase."""

        @wraps(original)
        def timed(*args: object, **kwargs: object) -> object:
            if self.attempted >= MAX_PHASES:
                return original(*args, **kwargs)
            self.attempted += 1
            started: tuple[int, int, int] | None = None
            stage = self.stage
            try:
                started = self.clocks()
            except BaseException as error:  # noqa: BLE001 - diagnostics cannot throw into Qt
                self.error("start_clock", error)
            completed = False
            try:
                result = original(*args, **kwargs)
                completed = True
                return result
            finally:
                if started is not None:
                    try:
                        ended = self.clocks()
                        self.phases.append(
                            {
                                "phase": label,
                                "stage": stage,
                                "completed": completed,
                                "wall_ms": (ended[0] - started[0]) / 1_000_000,
                                "process_cpu_ms": (ended[1] - started[1]) / 1_000_000,
                                "thread_cpu_ms": (ended[2] - started[2]) / 1_000_000,
                            }
                        )
                    except BaseException as error:  # noqa: BLE001 - original outcome retained
                        self.error("end_clock", error)

        return timed


class PatchSet:
    """Own only explicit attributes, restored independently in reverse order."""

    def __init__(self, trace: Trace) -> None:
        """Do not import or construct any Qt class."""
        self.trace = trace
        self.entries: list[tuple[Patch, object, object, bool]] = []
        self.admitted: list[str] = []

    def install(self, patches: tuple[Patch, ...]) -> None:
        """Save exact static descriptors before mutation, including partial writes."""
        for patch in patches:
            original = inspect.getattr_static(patch.owner, patch.attribute)
            if not callable(original):
                raise TypeError("Profile descriptor is not callable")
            wrapped = self.trace.wrap(patch.phase, original)
            directly_owned = patch.attribute in vars(patch.owner)
            self.entries.append((patch, original, wrapped, directly_owned))
            setattr(patch.owner, patch.attribute, wrapped)
            self.admitted.append(patch.phase)

    def close(self) -> None:
        """Restore exact owned bytes without overwriting an intervening writer."""
        for patch, original, wrapped, directly_owned in reversed(self.entries):
            try:
                current = inspect.getattr_static(patch.owner, patch.attribute)
                if current is wrapped:
                    if directly_owned:
                        setattr(patch.owner, patch.attribute, original)
                    else:
                        delattr(patch.owner, patch.attribute)
                elif current is not original or (
                    not directly_owned and patch.attribute in vars(patch.owner)
                ):
                    self.trace.error(
                        "patch_ownership_lost", RuntimeError("Patch changed")
                    )
            except BaseException as error:  # noqa: BLE001 - all independent restorations attempted
                self.trace.error("patch_cleanup", error)
        self.entries.clear()


def hosted_bindings(item: Item) -> Bindings:
    """Hosted-only lazy imports; never call this loader in local pure tests."""
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("QT_QPA_PLATFORM") != "offscreen"
    ):
        raise RuntimeError("Profiler requires hosted offscreen comparison")
    import PySide6  # noqa: PLC0415 - Qt already used by collection; no constructors
    import pytest  # noqa: PLC0415 - child hook only
    from PySide6.QtCore import qVersion  # noqa: PLC0415 - called only after target
    from PySide6.QtGui import QGuiApplication  # noqa: PLC0415 - metadata after target
    from PySide6.QtWidgets import QSpinBox  # noqa: PLC0415 - descriptor only

    from verdiclip.editor.style_bar import StyleBar  # noqa: PLC0415
    from verdiclip.editor.window import EditorWindow  # noqa: PLC0415
    from verdiclip.shell.controller import AppController  # noqa: PLC0415

    return Bindings(
        (
            Patch(
                item.module.App,
                "press_hotkey",
                "hotkey_dispatch",
            ),
            Patch(
                item.module.FakeDesktop,
                "freeze",
                "fake_desktop_freeze",
            ),
            Patch(AppController, "_on_captured", "controller_on_captured"),
            Patch(EditorWindow, "__init__", "editor_init"),
            Patch(StyleBar, "__init__", "style_bar_init"),
            Patch(QSpinBox, "__init__", "spin_box_init"),
        ),
        pytest.exit.Exception,
        lambda: {
            "python": sys.version.split()[0],
            "pyside": PySide6.__version__,
            "qt": qVersion(),
            "qpa_requested": os.environ.get("QT_QPA_PLATFORM"),
            "qpa_actual": QGuiApplication.platformName(),
        },
    )


@hookimpl
def pytest_configure(config: Config) -> None:
    """Standalone -p module entry: register pure plugin, leave Qt imports lazy."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Optional profiler requires explicit hosted execution")
    import pytest  # noqa: PLC0415 - actual hosted pytest hook only, never module import

    getoption = config.getoption
    metadata = {
        "source_sha_expected": os.environ.get("EDITOR_PROFILE_SOURCE_SHA"),
        "comparison_role": os.environ.get("EDITOR_PROFILE_ROLE"),
        "seed": getoption("randomly_seed", default=None),
        "coverage_requested": bool(getoption("cov_source", default=[])),
        "snapshots_requested": bool(getoption("ux_snapshots", default=None)),
        "qpa_requested": os.environ.get("QT_QPA_PLATFORM"),
        "selection_and_threshold_unchanged": True,
        "profiling_overhead_present": True,
    }
    manager = config.pluginmanager
    manager.register(
        Plugin(metadata, emit_json, trace=Trace(exit_control=pytest.exit.Exception)),
        "retained-editor-latency-instance",
    )


def emit_json(packet: dict[str, object]) -> None:
    """Serialize outside timed operations; collection metadata precedes the target."""
    import json  # noqa: PLC0415 - avoid serialization setup before target timer

    print(json.dumps({"editor_latency_profile": packet}, sort_keys=True), flush=True)  # noqa: T201 - requested bounded hosted receipt


@dataclass(eq=False)
class Plugin:
    """Exact-target protocol owns admission, rollback and post-test reporting."""

    metadata: dict[str, object]
    emit: Callable[[dict[str, object]], None]
    loader: Callable[[Item], Bindings] = hosted_bindings
    trace: Trace = field(default_factory=Trace)
    seen: bool = False

    def _propagate_controls(self, failure: BaseException | None) -> None:
        """Keep original exception identity and opaque distinct controls together."""
        controls, self.trace.controls = self.trace.controls, []
        failures = [failure] if failure is not None else []
        for control in controls:
            if not any(control is recorded for recorded in failures):
                failures.append(control)
        if len(failures) > 1:
            raise BaseExceptionGroup("Protocol and profiling controls", failures)
        if controls and failure is None:
            raise controls[0]

    @hookimpl
    def pytest_collection_finish(self, session: Session) -> None:
        """Hash final selected order after deselection; never mutate items."""
        nodes = tuple(item.nodeid for item in session.items)
        receipt = {
            "selected_count": len(nodes),
            "selected_order_sha256": hashlib.sha256(
                "\n".join(nodes).encode()
            ).hexdigest(),
            "target_index": next(
                (index for index, node in enumerate(nodes) if node == TARGET), None
            ),
            "first_node_is_target": bool(nodes) and nodes[0] == TARGET,
        }
        self.metadata.update(receipt)
        self.emit({"editor_profile_selection": receipt})

    @hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: Item) -> Generator[None, object, object]:
        """Never reorder, prewarm, change assertions or replace original results."""
        if item.nodeid != TARGET:
            return (yield)
        self.seen = True
        patches = PatchSet(self.trace)
        bindings: Bindings | None = None
        failure: BaseException | None = None
        try:
            try:
                bindings = self.loader(item)
                self.trace.exit_control = bindings.exit_control
                patches.install(bindings.patches)
            except BaseException as error:  # noqa: BLE001 - run unchanged test even if diagnostics fail
                self.trace.error("profile_admission", error)
            return (yield)
        except BaseException as error:
            failure = error
            raise
        finally:
            patches.close()
            labels: dict[str, object] = {}
            try:
                if bindings is not None:
                    labels = bindings.labels()
            except BaseException as error:  # noqa: BLE001 - opaque controls rethrow after reporting
                self.trace.error("profile_labels", error)
            try:
                self.emit(
                    {
                        "metadata": self.metadata,
                        "runtime_labels": labels,
                        "patches_admitted": patches.admitted,
                        "phases": list(self.trace.phases),
                        "errors": list(self.trace.errors),
                        "inclusive_phases_overlap": True,
                        "cpu_wall_ratio_is_not_causal_proof": True,
                        "diagnostic_only_not_cold_or_flake_resolution": True,
                    }
                )
            except BaseException as error:  # noqa: BLE001 - preserve original protocol outcome
                self.trace.error("profile_report", error)
            self._propagate_controls(failure)

    @hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: Item) -> Generator[None, object, object]:
        """Distinguish timed call phases from fixture setup/teardown phases."""
        if item.nodeid != TARGET:
            return (yield)
        self.trace.stage = "call"
        try:
            return (yield)
        finally:
            self.trace.stage = "after_call"

    @hookimpl
    def pytest_sessionfinish(self, session: Session) -> None:
        """A broken optional profiler cannot manufacture a green diagnostic."""
        if (not self.seen or self.trace.errors) and session.exitstatus == 0:
            session.exitstatus = 1
