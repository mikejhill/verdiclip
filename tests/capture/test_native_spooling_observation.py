"""Portable native-harness storage evidence; no provider or native input calls."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture.native_scrollbar import (
    OperationTiming,
    ScrollbarCase,
    observe_operation,
    observe_storage_operations,
    verify_deferred_storage,
)
from tests.capture.test_scroll_core import Documents

from verdiclip.capture.scroll.models import ScrollLimits
from verdiclip.capture.scroll.spooling import StorageJob
from verdiclip.capture.scroll.storage import AppendRequest, StagedAppend, StripStore


def forbid_trace(*_args: object) -> None:
    """Storage metadata must never consume or race the GUI event trace."""
    pytest.fail("Storage observation wrote to the GUI trace")


def test_deferred_storage_receipt_observes_actual_worker_and_gui_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real PNG staging is measured even though production never calls append."""
    monkeypatch.setattr("tests.capture.native_scrollbar.record_operation", forbid_trace)
    monkeypatch.setattr(StripStore, "append", forbid_trace)
    timings = observe_storage_operations(monkeypatch)
    store = StripStore(tmp_path, ScrollLimits())
    image = Documents.image(height=120)
    job = StorageJob(store.prepare(image))
    owned: list[StagedAppend] = []
    try:
        assert not list(tmp_path.iterdir())
        job.start()
        assert job.wait(5)
        staged = job.take().staged
        assert staged is not None
        owned.append(staged)
        assert store.height == 0
        store.admit(staged)
        assert store.materialize() == image
        verify_deferred_storage(timings)
        assert set(timings) == {"strips.prepare", "strips.stage", "strips.admit"}
        for name, timing in timings.items():
            receipt = timing.snapshot()
            assert receipt["calls"] == receipt["timed_calls"] == 1
            assert receipt["other_thread_calls"] == (name == "strips.stage")
            assert receipt["active_calls"] == receipt["raised"] == 0
            assert receipt["clock_failures"] == 0
    finally:
        assert job.wait(5)
        store.close()
        for staged in owned:
            staged.close()


def test_stage_observer_preserves_classmethod_and_instance_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrapped classmethod must still receive the actual calling subclass."""
    calls: list[tuple[type[StripStore], AppendRequest]] = []
    token = cast(StagedAppend, object())
    request = cast(AppendRequest, object())

    def stage(cls: type[StripStore], value: AppendRequest) -> StagedAppend:
        calls.append((cls, value))
        return token

    class SpecializedStore(StripStore):
        """Check inherited descriptor binding, not just the base class path."""

    monkeypatch.setattr(StripStore, "stage", classmethod(stage))
    timings = observe_storage_operations(monkeypatch)
    store = SpecializedStore(tmp_path, ScrollLimits())
    try:
        assert StripStore.stage(request) is token
        assert SpecializedStore.stage(request) is token
        assert store.stage(request) is token
        assert calls == [
            (StripStore, request),
            (SpecializedStore, request),
            (SpecializedStore, request),
        ]
        assert timings["strips.stage"].snapshot()["calls"] == 3
    finally:
        store.close()


@pytest.mark.parametrize("clock", ["monotonic", "thread_time"])
@pytest.mark.parametrize("failure", [OSError, RuntimeError, MemoryError])
@pytest.mark.parametrize("when", ["begin", "finish"])
@pytest.mark.parametrize("raises", [False, True])
def test_timing_only_clock_failures_preserve_exact_outcome(
    monkeypatch: pytest.MonkeyPatch,
    clock: str,
    failure: type[Exception],
    when: str,
    *,
    raises: bool,
) -> None:
    """Neither missing clock can suppress a call or replace its return/exception."""
    timing = OperationTiming()
    token, error = object(), RuntimeError("product failure")
    calls: list[object] = []

    def unavailable() -> float:
        raise failure("diagnostic clock")

    def operation(value: object) -> object:
        calls.append(value)
        if when == "finish":
            monkeypatch.setattr(
                f"tests.capture.native_scrollbar.time.{clock}", unavailable
            )
        if raises:
            raise error
        return value

    monkeypatch.setattr("tests.capture.native_scrollbar.record_operation", forbid_trace)
    if when == "begin":
        monkeypatch.setattr(f"tests.capture.native_scrollbar.time.{clock}", unavailable)
    observed = observe_operation(None, "strips.stage", operation, timing=timing)
    if raises:
        with pytest.raises(RuntimeError) as caught:
            observed(token)
        assert caught.value is error
    else:
        assert observed(token) is token
    assert calls == [token]
    receipt = timing.snapshot()
    assert receipt["calls"] == receipt["clock_failures"] == 1
    assert receipt["timed_calls"] == receipt["active_calls"] == 0
    assert receipt["raised"] == raises


def test_timing_only_retains_late_stall_without_trace_or_history_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-flight worker keeps fixed metadata beyond the 128-event budget."""
    clock = [0.0, 0.0]
    monkeypatch.setattr(
        "tests.capture.native_scrollbar.time.monotonic", lambda: clock[0]
    )
    monkeypatch.setattr(
        "tests.capture.native_scrollbar.time.thread_time", lambda: clock[1]
    )
    monkeypatch.setattr("tests.capture.native_scrollbar.record_operation", forbid_trace)
    timing = OperationTiming()
    fields = set(timing.snapshot())
    error = RuntimeError("late stage failure")
    calls = 0

    def operation() -> int:
        nonlocal calls
        calls += 1
        clock[0] += 1.25 if calls == 201 else 0.01
        clock[1] += 0.25 if calls == 201 else 0.001
        if calls == 201:
            raise error
        return calls

    observed = observe_operation(None, "strips.stage", operation, timing=timing)
    for expected in range(1, 201):
        assert observed() == expected
    with pytest.raises(RuntimeError) as caught:
        observed()
    assert caught.value is error
    receipt = timing.snapshot()
    assert set(receipt) == fields
    assert calls == receipt["calls"] == receipt["timed_calls"] == 201
    assert receipt["raised"] == 1
    assert receipt["active_calls"] == receipt["clock_failures"] == 0
    assert receipt["max_wall_seconds"] == pytest.approx(1.25)
    assert receipt["max_thread_cpu_seconds"] == pytest.approx(0.25)


def test_in_flight_worker_snapshot_does_not_wait_for_product_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Receipt reads never hold a lock across disk I/O or race partial counters."""
    monkeypatch.setattr("tests.capture.native_scrollbar.record_operation", forbid_trace)
    entered, release = threading.Event(), threading.Event()
    timing = OperationTiming()
    outcome: list[bool] = []

    def operation() -> None:
        entered.set()
        outcome.append(release.wait(5))

    worker = threading.Thread(
        target=observe_operation(None, "strips.stage", operation, timing=timing)
    )
    worker.start()
    try:
        assert entered.wait(5)
        receipt = timing.snapshot()
        assert receipt["calls"] == receipt["active_calls"] == 1
        assert receipt["other_thread_calls"] == 1
        assert receipt["timed_calls"] == receipt["raised"] == 0
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert outcome == [True]
    assert receipt["active_calls"] == 1  # The returned snapshot is detached.
    assert timing.snapshot()["active_calls"] == 0
    assert timing.snapshot()["timed_calls"] == 1


@pytest.mark.parametrize("method", ["prepare", "stage", "admit"])
def test_unsupported_refusal_rejects_each_real_untraced_storage_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Even a clean UIA trace cannot hide preparation, worker staging or admission."""
    store = StripStore(tmp_path, ScrollLimits())
    image = Documents.image(height=120)
    request = store.prepare(image)
    staged = StripStore.stage(request)
    additional = None
    timings = observe_storage_operations(monkeypatch)
    monkeypatch.setattr(
        "tests.capture.native_scrollbar.native_action_errors", lambda *_: []
    )
    case = SimpleNamespace(
        errors=["No supported scroll area was found at this point."],
        qtbot=SimpleNamespace(waitUntil=lambda predicate, **_: predicate()),
        retired=lambda: True,
        app=SimpleNamespace(
            controller=SimpleNamespace(workspace=SimpleNamespace(documents=[]))
        ),
        discoveries=[SimpleNamespace(references=())],
        snapshot=SimpleNamespace(errors=list),
        driver=object(),
        operation_trace=[],
        operation_timings=timings,
        peer=SimpleNamespace(poll=lambda: True, state={"offset": 0, "end_seen": False}),
        passed=False,
    )
    try:
        if method == "prepare":
            store.abandon()
            store.prepare(image)
        elif method == "stage":
            additional = StripStore.stage(request)
        else:
            store.admit(staged)
        assert timings[f"strips.{method}"].snapshot()["calls"] == 1
        assert sum(timing.snapshot()["calls"] for timing in timings.values()) == 1
        assert not case.operation_trace
        with pytest.raises(AssertionError, match="must not prepare, stage or admit"):
            ScrollbarCase.verify_unsupported(cast(ScrollbarCase, case))
        assert not case.passed
    finally:
        store.close()
        staged.close()
        if additional is not None:
            additional.close()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "unobserved_prepare",
        "unobserved_stage",
        "unobserved_admit",
        "synchronous_stage",
        "mixed_stage",
        "worker_prepare",
        "worker_admit",
        "active_prepare",
        "active_stage",
        "active_admit",
    ],
)
def test_completed_capture_storage_contract_ignores_clocks_but_rejects_wrong_threads(
    fault: str | None,
) -> None:
    """Optional timing loss cannot qualify a wrong thread or an active operation."""
    timings = {
        f"strips.{method}": OperationTiming(
            calls=1, clock_failures=1, other_thread_calls=int(method == "stage")
        )
        for method in ("prepare", "stage", "admit")
    }
    if fault is None:
        verify_deferred_storage(timings)
        return
    kind, method = fault.split("_", 1)
    timing = timings[f"strips.{method}"]
    if kind == "unobserved":
        timing.calls = timing.other_thread_calls = 0
    elif kind == "synchronous":
        timing.other_thread_calls = 0
    elif kind == "mixed":
        timing.calls = 2
    elif kind == "worker":
        timing.other_thread_calls = 1
    else:
        timing.active_calls = 1
    with pytest.raises(AssertionError):
        verify_deferred_storage(timings)


def test_completed_capture_rejects_real_synchronous_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pixel-perfect output through the old synchronous path is not async proof."""
    timings = observe_storage_operations(monkeypatch)
    store = StripStore(tmp_path, ScrollLimits())
    image = Documents.image(height=120)
    try:
        store.append(image)
        assert store.materialize() == image
        assert timings["strips.stage"].snapshot()["calls"] == 1
        with pytest.raises(AssertionError, match="staging ran on the GUI thread"):
            verify_deferred_storage(timings)
    finally:
        store.close()
