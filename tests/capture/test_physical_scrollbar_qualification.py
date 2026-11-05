"""Deterministic physical-harness contracts; no desktop, hooks or input generation."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from scripts import physical_scrollbar_qualification as provenance
from tests.capture.input_fixture import ReplayInputPlatform
from tests.capture.native_scrollbar import ScrollbarCase
from tests.capture.physical_scrollbar import (
    PhysicalClickEvidence,
    PhysicalScrollbarDriver,
    PhysicalScrollbarPlatform,
    admit_physical_scrollbar,
    physical_environment,
    physical_input_errors,
    scrollbar_input_receipt,
    scrollbar_profile,
)
from tests.capture.production_matrix import InputProfile, ObservedWin32Platform
from tests.capture.test_input_worker import ReplayRun

from verdiclip.capture.input_native import HookCallback, MousePacket
from verdiclip.capture.input_policy import InputKind
from verdiclip.capture.input_raw import RawPointer, RawPointerCallback
from verdiclip.capture.input_routing import MoveMode
from verdiclip.geometry import Point, Rect


@pytest.mark.parametrize("physical", [False, True])
@pytest.mark.parametrize("drag", [False, True])
@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize("delivery", [False, True])
def test_profile_selection_is_exclusive(
    *, physical: bool, drag: bool, hosted: bool, delivery: bool
) -> None:
    """Default injection stays intact; physical authority cannot leak across lanes."""
    if physical and (drag or hosted or delivery):
        with pytest.raises(ValueError, match="own window"):
            scrollbar_profile(
                physical=physical, drag=drag, hosted=hosted, delivery=delivery
            )
    else:
        assert scrollbar_profile(
            physical=physical, drag=drag, hosted=hosted, delivery=delivery
        ) is (InputProfile.PHYSICAL if physical else InputProfile.INJECTED)


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("declared", [False, True])
def test_flag_and_launcher_must_agree(*, selected: bool, declared: bool) -> None:
    """Neither a leaked environment value nor a bare flag admits physical work."""
    environment = {"VERDICLIP_PHYSICAL_SCROLLBAR": "1"} if declared else {}
    if selected == declared:
        physical_environment(environment, selected=selected)
    else:
        with pytest.raises(ValueError, match="disagree"):
            physical_environment(environment, selected=selected)


@pytest.mark.parametrize(
    "fault",
    ["injected", "raw-move-only", "raw-up-first", "late", "extra", "wheel", None],
)
def test_physical_evidence_needs_one_matching_non_injected_pair(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    """Mixed raw movement with injected buttons cannot qualify as physical input."""
    evidence = PhysicalClickEvidence()
    clock = Mock(return_value=1.0)
    monkeypatch.setattr("tests.capture.physical_scrollbar.time.monotonic", clock)
    packet = MousePacket()
    packet.flags = int(fault == "injected")
    address = ctypes.addressof(packet)
    evidence.mouse(-1, 0x201, address)
    evidence.mouse(0, 0x200, address)
    for message in (0x201, 0x202):
        evidence.mouse(0, message, address)
    messages = (0x202, 0x201) if fault == "raw-up-first" else (0x201, 0x202)
    if fault == "late":
        clock.return_value = 1.25
    for message in messages:
        evidence.raw(
            RawPointer(
                Point(0, 0),
                MoveMode.RELATIVE_DELTA,
                () if fault == "raw-move-only" else ((message, 0),),
                wheel=fault == "wheel",
            )
        )
    if fault == "extra":
        evidence.mouse(0, 0x201, address)
    assert evidence.complete() is (fault is None)


def test_physical_observer_forwards_original_callbacks_and_return_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only fake callback ports are installed; observation changes no semantics."""
    callbacks: list[object] = []

    def install(
        _self: object,
        mouse: HookCallback,
        keyboard: HookCallback,
        raw: RawPointerCallback,
    ) -> None:
        callbacks.extend((mouse, keyboard, raw))

    monkeypatch.setattr(ObservedWin32Platform, "install", install)
    platform = PhysicalScrollbarPlatform.__new__(PhysicalScrollbarPlatform)
    platform.physical_click = PhysicalClickEvidence()
    mouse, keyboard, raw = Mock(return_value=73), Mock(), Mock()
    platform.install(mouse, keyboard, raw)
    assert len(callbacks) == 3, "Native callbacks were registered more than once"
    packet = MousePacket()
    pointer = RawPointer(
        Point(0, 0), MoveMode.RELATIVE_DELTA, ((0x201, 0),), wheel=False
    )
    assert cast(HookCallback, callbacks[0])(0, 0x201, ctypes.addressof(packet)) == 73
    cast(RawPointerCallback, callbacks[2])(pointer)
    assert callbacks[1] is keyboard
    mouse.assert_called_once_with(0, 0x201, ctypes.addressof(packet))
    raw.assert_called_once_with(pointer)
    assert raw.call_args.args[0] is pointer
    assert platform.physical_click.hook_down == platform.physical_click.raw_down == 1


@pytest.mark.parametrize("fault", ["missing-raw", "unsupported", "injected"])
def test_production_physical_route_refuses_without_fallback(fault: str) -> None:
    """Actual product worker uses replay OS ports; native API creation is impossible."""
    driver = PhysicalScrollbarDriver()
    platform = ReplayInputPlatform()
    driver.platform_factory = lambda: platform
    worker = driver.worker(platform.anchor, Rect(0, 0, 1920, 1080))
    worker._clock = platform.clock
    worker._beat = platform.clock()
    assert worker.options.injected_move_mode is None
    assert worker.options.heartbeat_seconds == 1
    assert worker.RAW_TIMEOUT_SECONDS == 0.25
    if fault == "unsupported":
        platform.recipient_supported = False
    else:
        platform.mouse(0x200, platform.anchor, injected=fault == "injected")
    ReplayRun.complete(worker)
    assert worker.failure
    assert not any(event.kind is InputKind.RELEASE for event in worker.drain())
    assert platform.closed
    assert worker.cleaned
    assert worker.stopped.is_set()
    assert physical_input_errors(driver)


def test_physical_run_does_not_dispatch_hosted_input_or_cursor_probe() -> None:
    """Exercise the actual branch, stopping before any native call is reachable."""
    case = SimpleNamespace(
        profile=InputProfile.PHYSICAL,
        hosted_input=None,
        probe_cursor=Mock(side_effect=AssertionError("cursor probe dispatched")),
        target=Mock(side_effect=ValueError("safe stop before native boundary")),
    )
    with pytest.raises(ValueError, match="safe stop"):
        ScrollbarCase.run(cast(ScrollbarCase, case))
    case.probe_cursor.assert_not_called()
    case.hosted_input = Mock()
    with pytest.raises(AssertionError, match="hosted input"):
        ScrollbarCase.run(cast(ScrollbarCase, case))
    case.hosted_input.assert_not_called()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "dirty",
        "moved",
        "wrong-root",
        "unreviewed",
        "ignored",
        "unrelated",
        "empty",
        "missing-package",
        "hidden-index",
    ],
)
def test_provenance_checks_actual_git_identity_before_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    """A forged env label cannot hide source/dependency edits or ignored code."""
    package = tmp_path / "src" / "verdiclip" / "__init__.py"
    package.parent.mkdir(parents=True)
    if fault != "missing-package":
        package.write_text("", encoding="utf-8")
    head = "a" * 40
    replies = {
        ("ls-files", "-v"): "h src/verdiclip/__init__.py"
        if fault == "hidden-index"
        else "H src/verdiclip/__init__.py",
        ("rev-parse", "--show-toplevel"): str(
            tmp_path / "other" if fault == "wrong-root" else tmp_path
        ),
        ("rev-parse", "HEAD"): "b" * 40 if fault == "moved" else head,
        ("status", "--porcelain", "--untracked-files=all"): " M src/code.py"
        if fault == "dirty"
        else "",
        (
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
        ): "src/verdiclip/code.pyd" if fault == "ignored" else "",
        ("ls-files", "--", "src", "scripts", "tests"): "src/verdiclip/__init__.py",
        ("merge-base", provenance.PRODUCTION_BASE, head): "b" * 40
        if fault == "unrelated"
        else provenance.PRODUCTION_BASE,
        ("diff", "--name-only", provenance.PRODUCTION_BASE, "HEAD"): "uv.lock"
        if fault == "unreviewed"
        else ""
        if fault == "empty"
        else "tests/capture/physical_scrollbar.py",
    }
    monkeypatch.setattr(provenance, "git", lambda _root, *args: replies[args])
    if fault is not None:
        with pytest.raises(ValueError, match=r"Qualification|Harness|Pinned"):
            provenance.verify_provenance(harness_root=tmp_path, harness_head=head)
    else:
        result = provenance.verify_provenance(harness_root=tmp_path, harness_head=head)
        assert result.source_head == provenance.PRODUCTION_BASE
        assert result.harness_head == head
        assert result.package_path == str(package)
        assert result.receipt()["source_dirty"] is False
        assert "not-installed-wheel" in result.execution_kind


@pytest.mark.parametrize("head", ["", "abc", "A" * 40, provenance.PRODUCTION_BASE])
def test_harness_pin_cannot_be_missing_short_or_production(
    head: str, tmp_path: Path
) -> None:
    """Pin rejection precedes any Git/native resource query."""
    with pytest.raises(ValueError, match=r"reviewed harness|distinct"):
        provenance.verify_provenance(harness_root=tmp_path, harness_head=head)


def test_product_submodule_path_cannot_escape_verified_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checking the root package alone would miss an injected submodule path."""
    root = Path(__file__).resolve().parents[2]
    provenance.require_product_imports(root)
    foreign = ModuleType("verdiclip.unreviewed")
    foreign.__file__ = "/foreign/unreviewed.py"
    monkeypatch.setitem(sys.modules, foreign.__name__, foreign)
    with pytest.raises(ValueError, match="pinned production source"):
        provenance.require_product_imports(root)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing-worker",
        "injected-profile",
        "no-native",
        "missing-raw",
        "unclean",
        "worker-failure",
        "watchdog",
    ],
)
def test_qualification_requires_physical_evidence_and_cleanup(
    fault: str | None,
) -> None:
    """Native-looking receipts cannot override failed input or resource retirement."""
    driver = PhysicalScrollbarDriver()
    platform = PhysicalScrollbarPlatform.__new__(PhysicalScrollbarPlatform)
    platform.physical_click = PhysicalClickEvidence(
        hook_down=1,
        hook_up=1,
        raw_down=1,
        raw_up=1,
        hook_down_at=1,
        raw_down_at=1,
        hook_up_at=2,
        raw_up_at=2,
    )
    platform.installed = platform.closed = True
    platform.desktop_unsafe = False
    platform.left_down = platform.left_up = platform.mouse_moves = 1
    driver.platforms.append(platform)
    worker = driver.worker(Point(0, 0), Rect(0, 0, 1920, 1080))
    worker.stopped.set()
    worker.cleaned = True
    if fault == "missing-worker":
        driver.workers.clear()
    elif fault == "injected-profile":
        driver.profile = InputProfile.INJECTED
    elif fault == "no-native":
        driver.platform_factory = ReplayInputPlatform
    elif fault == "missing-raw":
        platform.physical_click.raw_up = 0
    elif fault == "unclean":
        worker.cleaned = False
    elif fault == "worker-failure":
        worker.failure = "pointer source missing"
    elif fault == "watchdog":
        worker.RAW_TIMEOUT_SECONDS = 0.5
    assert bool(physical_input_errors(driver)) is (fault is not None)


def test_physical_receipt_exposes_counters_without_contradictory_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nested receipt agrees with the final qualified/not-qualified decision."""
    driver = PhysicalScrollbarDriver()
    platform = PhysicalScrollbarPlatform.__new__(PhysicalScrollbarPlatform)
    platform.physical_click = PhysicalClickEvidence(hook_down=1)
    driver.platforms.append(platform)
    monkeypatch.setattr(
        driver, "receipt", lambda: {"physical_raw_input_qualified": False}
    )
    for qualified in (False, True):
        result = scrollbar_input_receipt(
            driver, physical=True, qualified=qualified, generated=False
        )
        assert result["physical_raw_input_qualified"] is qualified
        assert result["physical_click_evidence"] == [vars(platform.physical_click)]
        assert result["input_generated_by_harness"] is False


@pytest.mark.parametrize("fault", ["selector", "handoff", "hosted"])
def test_physical_admission_refuses_before_provenance_or_desktop(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Unsupported flag combinations cannot construct an app or a native fixture."""
    monkeypatch.setenv("VERDICLIP_PHYSICAL_SCROLLBAR", "1")
    monkeypatch.delenv("VERDICLIP_HOSTED_SCROLLBAR", raising=False)
    if fault == "hosted":
        monkeypatch.setenv("VERDICLIP_HOSTED_SCROLLBAR", "1")
    options = {
        "--native-scrollbar-physical": True,
        "--native-broker-physical": False,
        "--native-delivery-handoff": False,
        "--native-broker-handoff": fault != "handoff",
    }
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=lambda option: options[option]),
        node=SimpleNamespace(
            name="other"
            if fault == "selector"
            else "test_native_scrollbar_selection_preserves_popup"
        ),
    )
    query = Mock(side_effect=AssertionError("provenance queried before flag admission"))
    monkeypatch.setattr("tests.capture.physical_scrollbar.verify_provenance", query)
    with pytest.raises(ValueError, match="Physical scrollbar"):
        admit_physical_scrollbar(cast(pytest.FixtureRequest, request))
    query.assert_not_called()


@pytest.mark.parametrize("source", ["mouse", "raw"])
def test_observation_clock_failure_does_not_replace_product_callback(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """A failed diagnostic remains inconclusive without changing suppression."""
    callbacks: list[object] = []

    def install(
        _self: object,
        mouse: HookCallback,
        keyboard: HookCallback,
        raw: RawPointerCallback,
    ) -> None:
        callbacks.extend((mouse, keyboard, raw))

    monkeypatch.setattr(ObservedWin32Platform, "install", install)
    platform = PhysicalScrollbarPlatform.__new__(PhysicalScrollbarPlatform)
    platform.physical_click = PhysicalClickEvidence()
    mouse, keyboard, raw = Mock(return_value=71), Mock(), Mock()
    platform.install(mouse, keyboard, raw)
    monkeypatch.setattr(
        "tests.capture.physical_scrollbar.time.monotonic",
        Mock(side_effect=OSError("clock unavailable")),
    )
    packet = MousePacket()
    if source == "mouse":
        assert (
            cast(HookCallback, callbacks[0])(0, 0x201, ctypes.addressof(packet)) == 71
        )
        mouse.assert_called_once()
    else:
        cast(RawPointerCallback, callbacks[2])(
            RawPointer(Point(0, 0), MoveMode.RELATIVE_DELTA, ((0x201, 0),), wheel=False)
        )
        raw.assert_called_once()
    assert platform.physical_click.observation_failed
    assert not platform.physical_click.complete()


def test_physical_refusal_retires_journal_and_reports_no_qualification(
    tmp_path: Path,
) -> None:
    """Physical refusal and case cleanup cannot produce a green receipt."""
    import json  # noqa: PLC0415 -- local to this receipt test

    from tests.capture.production_matrix import (  # noqa: PLC0415
        DriverJournal,
        MatrixCase,
    )

    driver = PhysicalScrollbarDriver()
    platform = ReplayInputPlatform()
    driver.platform_factory = lambda: platform
    worker = driver.worker(platform.anchor, Rect(0, 0, 1920, 1080))
    worker._clock = platform.clock
    worker._beat = platform.clock()
    platform.mouse(0x200, platform.anchor, injected=False)
    ReplayRun.complete(worker)
    assert "pointer source" in worker.failure
    journal = DriverJournal(
        tmp_path,
        MatrixCase("qt-scrollbar-popup", "scrollbar-click", InputProfile.PHYSICAL),
    )
    journal.owned_handles = (42,)
    journal.binding_handle = 42
    journal.prepare_binding(Rect(10, 10, 10, 10))
    calls: list[str] = []
    case = SimpleNamespace(
        profile=InputProfile.PHYSICAL,
        journal=journal,
        driver=driver,
        app=SimpleNamespace(
            controller=SimpleNamespace(
                capture=SimpleNamespace(cancel=lambda: calls.append("capture"))
            )
        ),
        adapter=SimpleNamespace(cancel=lambda: calls.append("adapter")),
        qtbot=SimpleNamespace(waitUntil=lambda predicate, **_: predicate()),
        retired=lambda: worker.stopped.is_set() and worker.cleaned,
        popup_trace=None,
        retirement_probe={},
        operation_trace=[],
        operation_timings={},
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
        passed=False,
        errors=[worker.failure],
        discoveries=[],
        hosted_input=None,
        probe_cursor=Mock(side_effect=AssertionError("cursor probe dispatched")),
        target=Mock(side_effect=ValueError(worker.failure)),
        source_provenance=provenance.SourceProvenance(
            provenance.PRODUCTION_BASE, "a" * 40, str(tmp_path), "source/__init__.py"
        ),
    )
    try:
        with pytest.raises(ValueError, match="pointer source"):
            ScrollbarCase.run(cast(ScrollbarCase, case))
    finally:
        ScrollbarCase.close(cast(ScrollbarCase, case))
    case.probe_cursor.assert_not_called()
    assert calls == ["capture", "adapter", "snapshot", "windows", "source"]
    receipt = json.loads((tmp_path / "scrollbar-native.json").read_text())
    assert receipt["passed"] is False
    assert receipt["physical_raw_input_qualified"] is False
    assert receipt["input"]["physical_raw_input_qualified"] is False
    assert receipt["input"]["input_generated_by_harness"] is False
    assert receipt["retired"] is True
    assert receipt["cursor_presentation_verified"] is False
    assert receipt["physical_provenance"]["harness_head"] == "a" * 40
    assert (
        json.loads((tmp_path / "driver-retired.json").read_text())["phase"]
        == "retired-no-input"
    )


def test_bootstrap_refuses_before_importing_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standalone admission failure cannot create any fixture resources."""
    monkeypatch.setattr(sys, "argv", ["qualification", "a" * 40])
    monkeypatch.setattr(
        provenance,
        "sys",
        SimpleNamespace(flags=SimpleNamespace(isolated=1, dont_write_bytecode=1)),
    )
    monkeypatch.setattr(
        provenance, "verify_provenance", Mock(side_effect=ValueError("source rejected"))
    )
    execute = Mock(
        side_effect=AssertionError("pytest dispatched before source admission")
    )
    monkeypatch.setattr(pytest, "main", execute)
    with pytest.raises(ValueError, match="source rejected"):
        provenance.main()
    execute.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
        "pytest.pyc",
        "tests.pyd",
        "some_package/code.so",
        "src/verdiclip/code.pyd",
        "tests/__pycache__/code.pyc",
        "scripts/unused.dat",
        "pytest.PYD",
        "pytest.PYC",
        "some_package/code.SO",
        "some_package/code.SO.1",
        "SCRIPTS/unused.dat",
        "SRC/ignored.data",
    ],
)
def test_ignored_import_shadow_is_refused(name: str) -> None:
    """Root-level and nested ignored code cannot precede the pinned package."""
    assert provenance.unreviewed_import(name)


@pytest.mark.parametrize(
    "name",
    [
        ".test-tmp/case/result.json",
        ".venv/lib/pytest/__init__.py",
        ".pytest_cache/value",
        "docs/ux/gallery/image.png",
    ],
)
def test_non_importable_evidence_and_explicit_runtime_are_not_source(name: str) -> None:
    """Temporary evidence and the separately trusted interpreter remain usable."""
    assert not provenance.unreviewed_import(name)
