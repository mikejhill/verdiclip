"""Pure fake descriptors/hooks only; no Qt/application/plugin main execution."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from scripts.retained_editor_latency_profile import (
    TARGET,
    Bindings,
    Patch,
    PatchSet,
    Plugin,
    Trace,
)


class SessionExitError(Exception):
    """Stand in for explicit pytest exit without loading it in SDK parent."""


class Clock:
    """Deterministic increasing clock."""

    def __init__(self) -> None:
        """Start before any measurement."""
        self.value = 0

    def __call__(self) -> int:
        """Return one millisecond per read."""
        self.value += 1_000_000
        return self.value


def trace() -> Trace:
    """Each CPU clock is independent from wall time."""
    return Trace(Clock(), Clock(), Clock(), SessionExitError)


def test_wrapper_preserves_arguments_result_and_measured_clocks() -> None:
    """A phase remains transparent and all clocks are reported distinctly."""
    timing, seen, result = trace(), [], object()

    def operation(value: object, *, other: object) -> object:
        """Retain actual argument identities."""
        seen.extend((value, other))
        return result

    first, second = object(), object()
    assert timing.wrap("phase", operation)(first, other=second) is result
    assert seen == [first, second]
    assert timing.phases == [
        {
            "phase": "phase",
            "stage": "setup",
            "completed": True,
            "wall_ms": 1.0,
            "process_cpu_ms": 1.0,
            "thread_cpu_ms": 1.0,
        }
    ]


def test_operation_exception_is_preserved_and_recorded_incomplete() -> None:
    """Failures are never replaced by successful profiler results."""
    timing, original = trace(), GeneratorExit("original")

    def operation() -> None:
        raise original

    with pytest.raises(GeneratorExit) as caught:
        timing.wrap("phase", operation)()
    assert caught.value is original
    assert timing.phases[0]["completed"] is False


def test_clock_error_does_not_prevent_original_operation() -> None:
    """A diagnostic start-clock failure cannot skip the original Qt operation."""
    timing, called = trace(), []

    def broken() -> int:
        raise ValueError("clock")

    timing.wall = broken
    assert timing.wrap("phase", lambda: called.append(True))() is None
    assert called == [True]
    assert timing.errors == ["start_clock"]


def test_patch_restores_exact_descriptor_and_forwards_bound_self() -> None:
    """The class wrapper binds self normally and returns the original object."""

    class Owner:
        def operation(self, value: object) -> object:
            """Return unchanged input."""
            return value

    timing, patches = trace(), None
    patches = PatchSet(timing)
    original = Owner.__dict__["operation"]
    patches.install((Patch(Owner, "operation", "owner"),))
    value = object()
    assert Owner().operation(value) is value
    patches.close()
    assert Owner.__dict__["operation"] is original
    assert not timing.errors


def test_intervening_writer_is_not_clobbered_and_other_patch_restored() -> None:
    """Cleanup loss remains visible while unrelated owned cleanup proceeds."""

    class Owner:
        def first(self) -> None:
            """First owned method."""

        def second(self) -> None:
            """Second owned method."""

    timing, patches = trace(), None
    patches = PatchSet(timing)
    second = Owner.__dict__["second"]
    patches.install((Patch(Owner, "first", "first"), Patch(Owner, "second", "second")))

    def replacement(self: object) -> None:
        """Represent an intervening attribute writer."""

    Owner.first = replacement
    patches.close()
    assert Owner.__dict__["first"] is replacement
    assert Owner.__dict__["second"] is second
    assert timing.errors == ["patch_ownership_lost"]


def test_target_protocol_always_restores_and_reports_after_original_error() -> None:
    """The mandatory outer generator owns cleanup even on control exceptions."""

    class Owner:
        def operation(self) -> None:
            """A harmless fake operation."""

    output: list[dict[str, object]] = []
    labels_called: list[bool] = []
    original = Owner.__dict__["operation"]

    def labels() -> dict[str, object]:
        """Assert inventory happens only after patches have been retired."""
        assert Owner.__dict__["operation"] is original
        labels_called.append(True)
        return {"qt": "fake"}

    plugin = Plugin(
        {},
        output.append,
        lambda _item: Bindings(
            (Patch(Owner, "operation", "phase"),), SessionExitError, labels
        ),
        trace(),
    )
    wrapper = plugin.pytest_runtest_protocol(
        SimpleNamespace(nodeid=TARGET, module=object())
    )
    next(wrapper)
    assert not labels_called
    Owner().operation()
    error = KeyboardInterrupt("protocol")
    with pytest.raises(KeyboardInterrupt) as caught:
        wrapper.throw(error)
    assert caught.value is error
    assert Owner.__dict__["operation"] is original
    assert labels_called == [True]
    assert output[0]["phases"]


def test_other_node_does_not_load_or_patch_anything() -> None:
    """Normal tests remain outside optional profiler admission."""
    called: list[bool] = []

    def loader(_item: object) -> Bindings:
        called.append(True)
        raise AssertionError("must not run")

    plugin = Plugin({}, lambda _packet: None, loader)
    wrapper = plugin.pytest_runtest_protocol(
        SimpleNamespace(nodeid="other", module=object())
    )
    next(wrapper)
    value = object()
    with pytest.raises(StopIteration) as stopped:
        wrapper.send(value)
    assert stopped.value.value is value
    assert not called


def test_partial_admission_and_emitter_failure_remain_diagnostic_failures() -> None:
    """Profiler errors cannot replace the original protocol return or green CI."""

    def broken(_item: object) -> Bindings:
        raise TypeError("descriptor admission")

    def emit(_packet: dict[str, object]) -> None:
        raise ValueError("output")

    plugin = Plugin({}, emit, broken, trace())
    wrapper = plugin.pytest_runtest_protocol(
        SimpleNamespace(nodeid=TARGET, module=object())
    )
    next(wrapper)
    value = object()
    with pytest.raises(StopIteration) as stopped:
        wrapper.send(value)
    assert stopped.value.value is value
    assert plugin.trace.errors == ["profile_admission", "profile_report"]
    session = SimpleNamespace(exitstatus=0)
    plugin.pytest_sessionfinish(session)
    assert session.exitstatus == 1
    session.exitstatus = 3
    plugin.pytest_sessionfinish(session)
    assert session.exitstatus == 3


def test_deferred_clock_control_is_rethrown_after_cleanup() -> None:
    """No diagnostic control escapes through the operation's Qt boundary."""
    timing, control = trace(), SessionExitError("clock control")

    def broken() -> int:
        raise control

    timing.wall = broken
    plugin = Plugin(
        {},
        lambda _packet: None,
        lambda _item: Bindings((), SessionExitError, dict),
        timing,
    )
    wrapper = plugin.pytest_runtest_protocol(
        SimpleNamespace(nodeid=TARGET, module=object())
    )
    next(wrapper)
    assert timing.wrap("phase", lambda: "result")() == "result"
    assert control.__traceback__ is None
    with pytest.raises(SessionExitError) as caught:
        wrapper.send(None)
    assert caught.value is control


def test_bound_limits_phase_records() -> None:
    """Extra calls still execute without growing diagnostic records indefinitely."""
    timing = trace()
    operation = timing.wrap("phase", lambda: 3)
    for _ in range(100):
        assert operation() == 3
    assert len(timing.phases) == 64


def test_collection_receipt_observes_final_order_without_reordering() -> None:
    """Selected count/hash change with order while original items remain intact."""
    output: list[dict[str, object]] = []
    plugin = Plugin({}, output.append)
    items = [SimpleNamespace(nodeid="b"), SimpleNamespace(nodeid="a")]
    session = SimpleNamespace(items=items, exitstatus=0)
    before = tuple(items)
    plugin.pytest_collection_finish(session)
    assert tuple(items) == before
    assert plugin.metadata["selected_count"] == 2
    assert (
        plugin.metadata["selected_order_sha256"] == hashlib.sha256(b"b\na").hexdigest()
    )
    assert output[0]["editor_profile_selection"]
    assert plugin.metadata["target_index"] is None
    first = SimpleNamespace(items=[SimpleNamespace(nodeid=TARGET)], exitstatus=0)
    plugin.pytest_collection_finish(first)
    assert plugin.metadata["target_index"] == 0
    assert plugin.metadata["first_node_is_target"] is True


def test_partial_descriptor_admission_restores_previous_successful_patch() -> None:
    """A later unsupported descriptor cannot leave an earlier class patched."""

    class Owner:
        missing_operation = None

        def operation(self) -> None:
            """A valid first descriptor."""

    original = Owner.__dict__["operation"]
    plugin = Plugin(
        {},
        lambda _packet: None,
        lambda _item: Bindings(
            (
                Patch(Owner, "operation", "first"),
                Patch(Owner, "missing_operation", "invalid"),
            ),
            SessionExitError,
            dict,
        ),
        trace(),
    )
    wrapper = plugin.pytest_runtest_protocol(
        SimpleNamespace(nodeid=TARGET, module=object())
    )
    next(wrapper)
    assert Owner.__dict__["operation"] is not original
    value = object()
    with pytest.raises(StopIteration) as stopped:
        wrapper.send(value)
    assert stopped.value.value is value
    assert Owner.__dict__["operation"] is original
    assert plugin.trace.errors == ["profile_admission"]


def test_end_clock_failure_does_not_mask_operation_failure() -> None:
    """The original object still propagates when finishing diagnostics fails."""
    timing, original = trace(), ValueError("operation")
    reads = 0

    def wall() -> int:
        """Fail only after the original operation starts."""
        nonlocal reads
        reads += 1
        if reads == 2:
            raise RuntimeError("end clock")
        return 1

    def operation() -> None:
        raise original

    timing.wall = wall
    with pytest.raises(ValueError, match="operation") as caught:
        timing.wrap("phase", operation)()
    assert caught.value is original
    assert timing.errors == ["end_clock"]


def test_grouped_session_control_is_preserved_opaquely_outside_qt() -> None:
    """Exception-derived groups must not hide an explicit session exit control."""
    timing = trace()
    original = ExceptionGroup("opaque control", [SessionExitError("exit")])

    def broken() -> int:
        raise original

    timing.wall = broken
    plugin = Plugin(
        {},
        lambda _packet: None,
        lambda _item: Bindings((), SessionExitError, dict),
        timing,
    )
    wrapper = plugin.pytest_runtest_protocol(
        SimpleNamespace(nodeid=TARGET, module=object())
    )
    next(wrapper)
    assert timing.wrap("phase", lambda: "unchanged")() == "unchanged"
    with pytest.raises(ExceptionGroup) as caught:
        wrapper.send(None)
    assert caught.value is original


def test_label_failure_still_emits_phases_and_original_protocol_result() -> None:
    """Metadata failure cannot discard the useful phase/error evidence."""
    output: list[dict[str, object]] = []

    def labels() -> dict[str, object]:
        raise RuntimeError("version metadata")

    plugin = Plugin(
        {}, output.append, lambda _item: Bindings((), SessionExitError, labels), trace()
    )
    wrapper = plugin.pytest_runtest_protocol(
        SimpleNamespace(nodeid=TARGET, module=object())
    )
    next(wrapper)
    assert plugin.trace.wrap("phase", lambda: 7)() == 7
    value = object()
    with pytest.raises(StopIteration) as stopped:
        wrapper.send(value)
    assert stopped.value.value is value
    assert output[0]["phases"]
    assert output[0]["errors"] == ["profile_labels"]


def test_constructor_descriptor_returns_none_and_restores_exact_original() -> None:
    """Fake constructor binding proves wrapper self/return semantics only."""

    class Owner:
        def __init__(self, value: object) -> None:
            """Retain unchanged constructor argument identity."""
            self.value = value

    patches = PatchSet(trace())
    original, value = Owner.__dict__["__init__"], object()
    patches.install((Patch(Owner, "__init__", "fake_constructor"),))
    assert Owner(value).value is value
    patches.close()
    assert Owner.__dict__["__init__"] is original


def test_inherited_descriptor_restoration_removes_temporary_shadow() -> None:
    """Rollback preserves inheritance rather than adding a permanent class slot."""

    class Base:
        def operation(self, value: object) -> object:
            """Return the untouched original input."""
            return value

    class Derived(Base):
        """Inherit the operation without a direct class attribute."""

    original = Base.__dict__["operation"]
    assert "operation" not in Derived.__dict__
    patches = PatchSet(trace())
    patches.install((Patch(Derived, "operation", "inherited"),))
    value = object()
    assert Derived().operation(value) is value
    patches.close()
    assert "operation" not in Derived.__dict__
    assert Base.__dict__["operation"] is original
