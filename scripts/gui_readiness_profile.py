"""Optional hosted diagnostic for the unchanged readiness benchmark selector.

Module import is pure: no Qt, application, window or hardware operation. Phase
clocks overlap, add overhead and cannot qualify latency or identify an OS cause.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
import time
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field
from functools import wraps
from typing import Protocol

from pluggy import HookimplMarker

hookimpl = HookimplMarker("pytest")
TARGET = "tests/ux/test_gui_readiness.py::test_first_controller_start_and_capture_costs"
MAX_RECORDS = 128
MAX_ERRORS = 8


@dataclass(frozen=True)
class Patch:
    """One explicit callable descriptor and a fixed anonymous phase label."""

    owner: type
    attribute: str
    label: str
    dispatch: bool = False


@dataclass
class Trace:
    """Bounded in-memory wall/process/thread clocks, with no timed-path I/O."""

    wall: Callable[[], int] = time.perf_counter_ns
    process: Callable[[], int] = time.process_time_ns
    thread: Callable[[], int] = time.thread_time_ns
    records: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ordinal: int = 0
    dispatch_depth: int = 0
    active: bool = True
    truncated: bool = False

    def error(self, label: str) -> None:
        """Keep fixed labels only, never exception messages/arguments/paths."""
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(label)

    def clocks(self) -> tuple[int, int, int]:
        """Read Python clocks without GUI/event processing or inventories."""
        return self.wall(), self.process(), self.thread()

    def wrap(
        self, patch: Patch, original: Callable[..., object]
    ) -> Callable[..., object]:
        """Forward exact arguments/results/exceptions; retain bounded metadata."""

        @wraps(original)
        def measured(*args: object, **kwargs: object) -> object:
            if not self.active:
                return original(*args, **kwargs)
            if patch.dispatch:
                self.ordinal += 1
                self.dispatch_depth += 1
            ordinal = self.ordinal
            within_dispatch = self.dispatch_depth > 0
            start = None
            completed = False
            try:
                try:
                    start = self.clocks()
                except Exception:  # noqa: BLE001 - diagnostic clocks cannot change product behavior
                    self.error("start_clock")
                result = original(*args, **kwargs)
                completed = True
                return result
            finally:
                if patch.dispatch:
                    self.dispatch_depth -= 1
                if start is not None:
                    self.record(
                        patch.label,
                        start,
                        ordinal,
                        within_dispatch=within_dispatch,
                        completed=completed,
                    )

        return measured

    def record(
        self,
        label: str,
        start: tuple[int, int, int],
        ordinal: int,
        *,
        within_dispatch: bool,
        completed: bool,
    ) -> None:
        """Append after an operation; diagnostic errors never become a pass."""
        try:
            end = self.clocks()
            if any(after < before for before, after in zip(start, end, strict=True)):
                self.error("clock_regressed")
                return
            if len(self.records) >= MAX_RECORDS:
                self.truncated = True
                return
            self.records.append(
                {
                    "phase": label,
                    "capture_ordinal": ordinal,
                    "within_dispatch": within_dispatch,
                    "completed": completed,
                    "wall_ms": (end[0] - start[0]) / 1_000_000,
                    "process_cpu_ms": (end[1] - start[1]) / 1_000_000,
                    "thread_cpu_ms": (end[2] - start[2]) / 1_000_000,
                }
            )
        except Exception:  # noqa: BLE001 - preserve the original method outcome
            self.error("end_clock_or_record")


class PatchSet:
    """Own exact descriptors; restore partial installs and inherited attributes."""

    def __init__(self, trace: Trace) -> None:
        self.trace = trace
        self.entries: list[tuple[Patch, object, object, bool]] = []
        self.admitted: list[str] = []

    def install(self, patches: tuple[Patch, ...]) -> None:
        """Save ownership before mutation so partial installation can roll back."""
        for patch in patches:
            original = inspect.getattr_static(patch.owner, patch.attribute)
            if not callable(original):
                raise TypeError("Profile descriptor must be callable")
            wrapped = self.trace.wrap(patch, original)
            self.entries.append(
                (patch, original, wrapped, patch.attribute in vars(patch.owner))
            )
            setattr(patch.owner, patch.attribute, wrapped)
            self.admitted.append(patch.label)

    def close(self) -> None:
        """Disable connected wrappers and restore only descriptors still owned."""
        self.trace.active = False
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
                    self.trace.error("patch_ownership_lost")
            except Exception:  # noqa: BLE001 - attempt each independent owned restoration
                self.trace.error("patch_restore")
        self.entries.clear()


def hosted_patches() -> tuple[Patch, ...]:
    """Resolve already-collected classes after hosted guards, never construct Qt."""
    require_hosted()
    from PySide6.QtWidgets import QFontComboBox, QSpinBox  # noqa: PLC0415
    from tests.ux.test_app_journeys import App, FakeDesktop  # noqa: PLC0415

    from verdiclip.editor.page import DocumentPage  # noqa: PLC0415
    from verdiclip.editor.style_bar import StyleBar  # noqa: PLC0415
    from verdiclip.editor.window import EditorWindow  # noqa: PLC0415
    from verdiclip.editor.workspace import Workspace  # noqa: PLC0415
    from verdiclip.shell.controller import AppController  # noqa: PLC0415

    return (
        Patch(App, "press_hotkey", "hotkey_dispatch", dispatch=True),
        Patch(FakeDesktop, "freeze", "fake_desktop_freeze"),
        Patch(AppController, "_on_captured", "controller_on_captured"),
        Patch(AppController, "_open_document", "controller_open_document"),
        Patch(Workspace, "open_document", "workspace_open_document"),
        Patch(Workspace, "_present_host", "workspace_present_host"),
        Patch(EditorWindow, "__init__", "editor_init"),
        Patch(StyleBar, "__init__", "style_bar_init"),
        Patch(QSpinBox, "__init__", "spin_box_init"),
        Patch(QFontComboBox, "__init__", "font_combo_init"),
        Patch(DocumentPage, "__init__", "document_page_init"),
        Patch(DocumentPage, "present", "document_page_present"),
        Patch(EditorWindow, "show", "editor_show"),
        Patch(EditorWindow, "showEvent", "editor_show_event"),
        Patch(EditorWindow, "raise_", "editor_raise"),
        Patch(EditorWindow, "activateWindow", "editor_activate"),
    )


def require_hosted() -> None:
    """Refuse local use before resolving any Qt/application descriptor."""
    if (
        sys.platform != "win32"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Windows"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("QT_QPA_PLATFORM") != "windows"
        or os.environ.get("VERDICLIP_GUI_PROFILE") != "1"
        or os.environ.get("VERDICLIP_GUI_PROFILE_TRIAL")
        not in {"1", "2", "3", "4", "5"}
        or os.environ.get("VERDICLIP_EXPECTED_QT") not in {"6.11.2", "6.12.0"}
        or re.fullmatch(
            r"[0-9a-f]{40}", os.environ.get("VERDICLIP_BENCHMARK_SOURCE_SHA", "")
        )
        is None
    ):
        raise RuntimeError("Explicit standard hosted Windows diagnostic required")


class Item(Protocol):
    """Read only the exact pytest selector."""

    nodeid: str


class Session(Protocol):
    """Selected items and final original pytest outcome."""

    items: Sequence[Item]
    exitstatus: int


class PluginManager(Protocol):
    """Only explicit plugin registration is needed from pytest's config."""

    def register(self, plugin: object, name: str) -> object:
        """Register the diagnostic instance without inventory or application work."""
        ...


class Config(Protocol):
    """The real pytest config supplies its public registration boundary."""

    pluginmanager: PluginManager


def emit(packet: dict[str, object]) -> None:
    """Only post-call bounded diagnostic metadata is serialized to stdout."""
    sys.stdout.write(
        "GUI_READINESS_PROFILE=" + json.dumps(packet, sort_keys=True) + "\n"
    )
    sys.stdout.flush()


@dataclass(eq=False)
class Profiler:
    """One unchanged benchmark case; no selection, readiness or threshold changes."""

    trace: Trace = field(default_factory=Trace)
    loader: Callable[[], tuple[Patch, ...]] = hosted_patches
    emitter: Callable[[dict[str, object]], None] = emit
    seen: bool = False

    @hookimpl
    def pytest_collection_finish(self, session: Session) -> None:
        """Fail closed on any selection other than the one reviewed benchmark."""
        if tuple(item.nodeid for item in session.items) != (TARGET,):
            raise RuntimeError(
                "Diagnostic requires exactly the unchanged benchmark selector"
            )

    @hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item: Item) -> Generator[None, object, object]:
        """Wrap actual test execution after collection/fixtures; never prewarm."""
        if item.nodeid != TARGET:
            return (yield)
        self.seen = True
        patches = PatchSet(self.trace)
        try:
            try:
                patches.install(self.loader())
            except Exception:  # noqa: BLE001 - original test still runs, diagnostic remains failed
                self.trace.error("profile_admission")
            return (yield)
        finally:
            patches.close()
            try:
                self.emitter(
                    {
                        "source_sha": os.environ.get("VERDICLIP_BENCHMARK_SOURCE_SHA"),
                        "qt_expected": os.environ.get("VERDICLIP_EXPECTED_QT"),
                        "trial": os.environ.get("VERDICLIP_GUI_PROFILE_TRIAL"),
                        "selector": TARGET,
                        "instrumentation_is_diagnostic": True,
                        "inclusive_phases_overlap": True,
                        "instrumentation_overhead_present": True,
                        "no_cold_OS_or_causal_or_native_acceptance_claim": True,
                        "patches_admitted": patches.admitted,
                        "records": self.trace.records,
                        "errors": self.trace.errors,
                        "truncated": self.trace.truncated,
                        "capture_ordinals": self.trace.ordinal,
                    }
                )
            except Exception:  # noqa: BLE001 - retain original assertion/exception, never green diagnostic
                self.trace.error("profile_emit")

    @hookimpl
    def pytest_sessionfinish(self, session: Session) -> None:
        """Missing/broken profiling cannot manufacture a successful diagnostic."""
        if (
            not self.seen or self.trace.errors or self.trace.truncated
        ) and session.exitstatus == 0:
            session.exitstatus = 1


@hookimpl
def pytest_configure(config: Config) -> None:
    """Activate explicitly only in hosted pytest, before Qt descriptor imports."""
    require_hosted()
    # Keep the module independent of pytest at import; actual config owns manager.
    config.pluginmanager.register(Profiler(), "gui-readiness-profile-instance")
