"""Pure signal/fixture/report ports; never import Qt, tests or application code."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import cast, override
from weakref import ref

import pluggy
import pytest
from scripts.retained_overlay_signal_observer import (
    MAX_EVENTS,
    TARGET,
    Observer,
    order_hash,
    storage,
)


class SessionExitError(Exception):
    """Fake the admitted pytest.exit.Exception without importing pytest in parent."""


class FakeSignal:
    """Track only exact Python receivers."""

    def __init__(
        self, *, connect_error: bool = False, disconnect_error: bool = False
    ) -> None:
        self.receivers: list[Callable[..., None]] = []
        self.connect_error = connect_error
        self.disconnect_error = disconnect_error
        self.disconnect_control: BaseException | None = None
        self.disconnect_false = False

    def connect(self, receiver: Callable[..., None]) -> None:
        """Connect."""
        self.receivers.append(receiver)
        if self.connect_error:
            raise RuntimeError("partial connect")

    def disconnect(self, receiver: Callable[..., None]) -> bool | None:
        """Disconnect."""
        self.receivers.remove(receiver)
        if self.disconnect_control is not None:
            raise self.disconnect_control
        if self.disconnect_error:
            raise RuntimeError("disconnect")
        return False if self.disconnect_false else None

    def emit(self, *args: object) -> None:
        """Emit."""
        for receiver in tuple(self.receivers):
            receiver(*args)


class Service:
    """Plain stored state only."""

    def __init__(self) -> None:
        self.cancelled = FakeSignal()
        self.captured = FakeSignal()
        self.failed = FakeSignal()
        self._starting = False
        self._cancelled_start = False
        self._deactivated = True
        self._overlays: list[object] = []
        self._pending = None
        self._frozen = None
        self._watching = None


class Request:
    """Model pytest's public LIFO finalizer ownership."""

    def __init__(self, nodeid: str = TARGET) -> None:
        self.node = SimpleNamespace(nodeid=nodeid)
        self.finalizers: list[Callable[[], None]] = []

    def addfinalizer(self, finalizer: Callable[[], None]) -> None:
        """Addfinalizer."""
        self.finalizers.append(finalizer)

    def finish(self) -> None:
        """Finish."""
        while self.finalizers:
            self.finalizers.pop()()


def plugin() -> tuple[Observer, list[dict[str, object]]]:
    """No files or real fixture objects are constructed."""
    output: list[dict[str, object]] = []
    return Observer(
        Path("/admitted/mutants"), (TARGET,), output.append, SessionExitError
    ), output


def setup(observer: Observer, request: Request, service: Service) -> None:
    """Resume the hook exactly as pluggy passes its original result."""
    wrapper = observer.pytest_fixture_setup(SimpleNamespace(argname="service"), request)
    next(wrapper)
    with pytest.raises(StopIteration) as stopped:
        wrapper.send(service)
    assert stopped.value.value is service


def test_signal_observation_is_memory_only_and_payload_blind() -> None:
    """Signal observation is memory only and payload blind."""
    observer, output = plugin()
    service, request = Service(), Request()
    setup(observer, request, service)

    class Payload:
        @override
        def __repr__(self) -> str:
            raise AssertionError("payload inspection")

    service.captured.emit(Payload())
    service.failed.emit(Payload())
    service.cancelled.emit()
    assert not output
    assert [event["signal"] for event in observer.events] == [
        "CAPTURED",
        "FAILED",
        "CANCELLED",
    ]
    assert observer.events[0]["state"] == {
        "starting": False,
        "cancelled_start": False,
        "deactivated": True,
        "overlay_count": 0,
        "pending_present": False,
        "frozen_present": False,
        "watching_present": False,
    }
    assert not observer.errors


def test_retirement_precedes_fixture_cancel_and_disconnects_exact_receivers() -> None:
    """Retirement precedes fixture cancel and disconnects exact receivers."""
    observer, _ = plugin()
    service, request = Service(), Request()
    request.addfinalizer(service.cancelled.emit)
    setup(observer, request, service)
    request.finish()
    assert observer.retired
    assert not observer.events
    assert not any(
        (
            service.cancelled.receivers,
            service.captured.receivers,
            service.failed.receivers,
        )
    )
    assert not observer.errors


def test_setup_error_and_nonmatching_fixture_are_preserved() -> None:
    """Setup error and nonmatching fixture are preserved."""
    observer, _ = plugin()
    request = Request()
    wrapper = observer.pytest_fixture_setup(SimpleNamespace(argname="service"), request)
    next(wrapper)
    error = GeneratorExit("setup control")
    with pytest.raises(GeneratorExit) as caught:
        wrapper.throw(error)
    assert caught.value is error
    assert not request.finalizers
    service = Service()
    setup(observer, Request("other"), service)
    assert not observer.attached
    assert not service.cancelled.receivers
    wrapper = observer.pytest_fixture_setup(SimpleNamespace(argname="other"), request)
    next(wrapper)
    with pytest.raises(StopIteration) as stopped:
        wrapper.send(service)
    assert stopped.value.value is service
    assert not request.finalizers


def test_partial_connection_and_cleanup_errors_are_visible() -> None:
    """Partial connect and cleanup errors are visible without leaking connections."""
    observer, _ = plugin()
    service, request = Service(), Request()
    service.captured.connect_error = True
    service.cancelled.disconnect_error = True
    setup(observer, request, service)
    assert len(observer.errors) == 1
    request.finish()
    assert len(observer.errors) == 2
    assert observer.retired
    assert not service.cancelled.receivers
    assert not service.captured.receivers
    assert not observer.diagnostic_ok


def test_bounded_records_and_failures_never_escape_callback() -> None:
    """Bounded records and failures never escape callback."""
    observer, _ = plugin()
    service, request = Service(), Request()
    setup(observer, request, service)
    for _ in range(MAX_EVENTS + 3):
        service.cancelled.emit()
    assert len(observer.events) == MAX_EVENTS
    assert observer.dropped == 3
    receiver = service.cancelled.receivers[0]
    request.finish()
    observer.attempted = 0
    receiver()
    assert len(observer.errors) == 1


def test_frames_are_whitelisted_metadata_without_locals_or_external_paths() -> None:
    """Frames are whitelisted metadata without locals or external paths."""
    observer, _ = plugin()
    service, request = Service(), Request()
    setup(observer, request, service)
    namespace: dict[str, object] = {"emit": service.cancelled.emit}
    exec(  # noqa: S102 - fixed pure fake source tests only metadata filtering
        compile(
            "def route():\n    secret = object()\n    emit()\nroute()\n",
            str(observer.root / "src/verdiclip/capture/service.py"),
            "exec",
        ),
        namespace,
    )
    assert observer.events[0]["frames"] == [
        {"file": "src/verdiclip/capture/service.py", "function": "route", "line": 3},
        {"file": "src/verdiclip/capture/service.py", "function": "<module>", "line": 4},
    ]
    assert "secret" not in str(observer.events)


def test_storage_does_not_call_hostile_truthiness_or_list_subclass_length() -> None:
    """Storage does not call hostile truthiness or list subclass length."""
    attempts = []

    class Hostile(list[object]):
        @override
        def __len__(self) -> int:
            attempts.append("len")
            return 0

        def __bool__(self) -> bool:
            attempts.append("bool")
            return False

    service = SimpleNamespace(
        _overlays=Hostile(), _starting=Hostile(), _pending=Hostile()
    )
    result = storage(service)
    assert result["overlay_count"] is None
    assert result["starting"] is None
    assert result["pending_present"] is True
    assert attempts == []


def test_recorder_error_is_deferred_and_report_result_is_never_replaced() -> None:
    """Recorder error is deferred and report result is never replaced."""
    observer, output = plugin()
    request = Request()
    wrapper = observer.pytest_fixture_setup(SimpleNamespace(argname="service"), request)
    next(wrapper)
    # Builtin object has no Python storage; callbacks catch the diagnostic error.
    service = Service()
    setup(observer, request, service)
    receiver = observer._receiver(object(), "CANCELLED")
    receiver()
    assert len(observer.errors) == 1
    report = SimpleNamespace(when="call", failed=True)
    hook = observer.pytest_runtest_makereport(SimpleNamespace(nodeid=TARGET))
    next(hook)
    with pytest.raises(StopIteration) as stopped:
        hook.send(report)
    assert stopped.value.value is report
    assert output[-1]["observer_errors"] == 1


def test_emitter_error_preserves_failed_report_and_original_report_error() -> None:
    """Emitter error preserves failed report and original report error."""
    observer, _ = plugin()

    def fail(_packet: dict[str, object]) -> None:
        """Fail."""
        raise RuntimeError("emitter")

    observer.emit = fail
    report = SimpleNamespace(when="call", failed=True)
    hook = observer.pytest_runtest_makereport(SimpleNamespace(nodeid=TARGET))
    next(hook)
    with pytest.raises(StopIteration) as stopped:
        hook.send(report)
    assert stopped.value.value is report
    assert len(observer.errors) == 1
    hook = observer.pytest_runtest_makereport(SimpleNamespace(nodeid=TARGET))
    next(hook)
    error = KeyboardInterrupt("original report")
    with pytest.raises(KeyboardInterrupt) as caught:
        hook.throw(error)
    assert caught.value is error


def test_collection_hash_match_and_mismatch_never_reorder_items() -> None:
    """Collection hash match and mismatch never reorder items."""
    observer, output = plugin()
    items = [SimpleNamespace(nodeid=TARGET)]
    observer.pytest_collection_modifyitems(items)
    assert output[0]["observer_order_sha256"] == order_hash((TARGET,))
    assert observer.order_matched
    assert items[0].nodeid == TARGET
    original = [SimpleNamespace(nodeid="other"), SimpleNamespace(nodeid=TARGET)]
    before = tuple(original)
    with pytest.raises(RuntimeError, match="differs"):
        observer.pytest_collection_modifyitems(original)
    assert tuple(original) == before
    assert not observer.order_matched


def test_trylast_collection_observer_sees_prior_sorter_via_pluggy() -> None:
    """Trylast collection observer sees prior sorter via pluggy."""
    spec = pluggy.HookspecMarker("pytest")
    impl = pluggy.HookimplMarker("pytest")

    class Specs:
        @spec
        def pytest_collection_modifyitems(self, items: list[object]) -> None:
            """Specify collection ordering."""

    class Sorter:
        @impl
        def pytest_collection_modifyitems(self, items: list[object]) -> None:
            """Pytest collection modifyitems."""
            items.reverse()

    observer = Observer(
        Path("/admitted/mutants"),
        (TARGET, "other"),
        lambda _packet: None,
        SessionExitError,
    )
    manager = pluggy.PluginManager("pytest")
    manager.add_hookspecs(Specs)
    manager.register(Sorter())
    manager.register(observer)
    items = [SimpleNamespace(nodeid="other"), SimpleNamespace(nodeid=TARGET)]
    manager.hook.pytest_collection_modifyitems(items=items)
    assert observer.order_matched
    assert observer in manager.get_plugins()


def test_summary_requires_order_attachment_retirement_and_no_errors() -> None:
    """Summary requires order attachment retirement and no errors."""
    observer, _ = plugin()
    assert not observer.diagnostic_ok
    observer.pytest_collection_modifyitems([SimpleNamespace(nodeid=TARGET)])
    service, request = Service(), Request()
    setup(observer, request, service)
    assert not observer.diagnostic_ok
    request.finish()
    observer.target_call_seen = True
    assert observer.summary()["observer_diagnostic_ok"] is True
    observer.errors.append("diagnostic")
    assert not observer.diagnostic_ok


def test_ordinary_callback_errors_do_not_retain_payload_tracebacks() -> None:
    """Ordinary callback errors do not retain payload tracebacks."""

    class Broken(Observer):
        @override
        def _frames(self) -> list[dict[str, object]]:
            raise ValueError("recorder failed")

    class Payload:
        pass

    observer = Broken(
        Path("/admitted/mutants"), (TARGET,), lambda _packet: None, SessionExitError
    )
    service, request = Service(), Request()
    setup(observer, request, service)
    payload = Payload()
    weak = ref(payload)
    service.cancelled.emit(payload)
    del payload
    assert weak() is None
    assert observer.errors == ["recorder"]
    assert not observer.controls
    assert observer.finish(1) == 1
    assert observer.finish(0) == 1


def test_callback_control_is_stripped_and_rethrown_after_pytest() -> None:
    """Callback control is stripped and rethrown after pytest."""
    control = KeyboardInterrupt("diagnostic control")

    class Broken(Observer):
        @override
        def _frames(self) -> list[dict[str, object]]:
            raise control

    observer = Broken(
        Path("/admitted/mutants"), (TARGET,), lambda _packet: None, SessionExitError
    )
    service, request = Service(), Request()
    setup(observer, request, service)
    service.cancelled.emit(object())
    assert observer.controls == [control]
    assert control.__traceback__ is None
    assert control.__context__ is None
    request.finish()
    with pytest.raises(KeyboardInterrupt) as caught:
        observer.finish(1)
    assert caught.value is control
    assert not observer.controls


def test_cleanup_controls_are_independent_and_grouped_outside_signal_boundary() -> None:
    """Cleanup controls are independent and grouped outside signal boundary."""
    observer, _ = plugin()
    service, request = Service(), Request()
    setup(observer, request, service)
    first, second = SystemExit(3), GeneratorExit("cleanup")
    service.cancelled.disconnect_control = first
    service.captured.disconnect_control = second
    request.finish()
    assert not service.failed.receivers
    with pytest.raises(BaseExceptionGroup) as caught:
        observer.finish(1)
    assert caught.value.exceptions == (second, first)


@pytest.mark.parametrize("order", [(), ("",), (TARGET, TARGET), [TARGET], (1,)])
def test_invalid_expected_orders_are_rejected(order: object) -> None:
    """Invalid expected orders are rejected."""
    with pytest.raises(ValueError, match="validated"):
        Observer(
            Path("/admitted/mutants"),
            cast("tuple[str, ...]", order),
            lambda _packet: None,
            SessionExitError,
        )


def test_explicit_pytest_style_session_exit_is_deferred_as_control() -> None:
    """Session exit inherits Exception but must not become an ordinary error count."""
    control = SessionExitError("stop session")

    class Broken(Observer):
        @override
        def _frames(self) -> list[dict[str, object]]:
            raise control

    observer = Broken(
        Path("/admitted/mutants"), (TARGET,), lambda _packet: None, SessionExitError
    )
    service, request = Service(), Request()
    setup(observer, request, service)
    service.cancelled.emit()
    request.finish()
    with pytest.raises(SessionExitError) as caught:
        observer.finish(1)
    assert caught.value is control


def test_grouped_session_exit_is_preserved_without_inspecting_group_members() -> None:
    """A grouped session control must not become an ordinary diagnostic count."""
    control = ExceptionGroup("grouped session", [SessionExitError("stop session")])

    class Broken(Observer):
        @override
        def _frames(self) -> list[dict[str, object]]:
            raise control

    observer = Broken(
        Path("/admitted/mutants"), (TARGET,), lambda _packet: None, SessionExitError
    )
    service, request = Service(), Request()
    setup(observer, request, service)
    service.cancelled.emit()
    request.finish()
    with pytest.raises(ExceptionGroup) as caught:
        observer.finish(1)
    assert caught.value is control
    assert caught.value.exceptions[0] is control.exceptions[0]


def test_builtin_chain_descriptors_bypass_hostile_control_overrides() -> None:
    """Arbitrary controls cannot throw from custom chain attribute setters."""

    class Hostile(BaseException):
        @property
        @override
        def __traceback__(self) -> TracebackType | None:
            raise AssertionError("custom traceback getter")

        @__traceback__.setter
        @override
        def __traceback__(self, _value: object) -> None:
            raise AssertionError("custom traceback setter")

        @property
        @override
        def __cause__(self) -> BaseException | None:
            raise AssertionError("custom cause getter")

        @__cause__.setter
        @override
        def __cause__(self, _value: object) -> None:
            raise AssertionError("custom cause setter")

        @property
        @override
        def __context__(self) -> BaseException | None:
            raise AssertionError("custom context getter")

        @__context__.setter
        @override
        def __context__(self, _value: object) -> None:
            raise AssertionError("custom context setter")

    control = Hostile("custom control")

    class Broken(Observer):
        @override
        def _frames(self) -> list[dict[str, object]]:
            raise control

    observer = Broken(
        Path("/admitted/mutants"), (TARGET,), lambda _packet: None, SessionExitError
    )
    service, request = Service(), Request()
    setup(observer, request, service)
    service.cancelled.emit(object())
    assert observer.controls == [control]
    for name in ("__traceback__", "__cause__", "__context__"):
        assert BaseException.__dict__[name].__get__(control) is None
    request.finish()
    with pytest.raises(Hostile) as caught:
        observer.finish(1)
    assert caught.value is control


def test_scope_always_retires_and_reports_original_runner_exception() -> None:
    """Unexpected runner failure cannot skip owned disconnect/report cleanup."""
    observer, output = plugin()
    service, request = Service(), Request()
    original = ValueError("original runner")

    def exercise() -> None:
        """Raise after actual fixture ownership is installed."""
        with observer.scope():
            setup(observer, request, service)
            raise original

    with pytest.raises(ValueError, match="original runner") as caught:
        exercise()
    assert caught.value is original
    assert observer.retired
    assert not service.cancelled.receivers
    assert output[-1]["observer_retired"] is True
    request.finish()  # The real fixture finalizer may run later; cleanup is idempotent.
    assert not observer.errors


def test_scope_keeps_body_and_deferred_control_objects_together() -> None:
    """A body failure and true deferred control are distinct, preserved failures."""
    observer, _ = plugin()
    body, deferred = ValueError("runner"), GeneratorExit("diagnostic")

    def exercise() -> None:
        """Combine two distinct error sources before mandatory scope exit."""
        with observer.scope():
            observer._error("recorder", deferred)
            raise body

    with pytest.raises(BaseExceptionGroup) as caught:
        exercise()
    assert caught.value.exceptions == (body, deferred)


def test_scope_preserves_original_session_exit_when_alone() -> None:
    """An original explicit session exit remains the exact original object."""
    observer, _ = plugin()
    original = SessionExitError("session")
    entered = []

    def invoke() -> None:
        with observer.scope():
            entered.append(True)
            raise original

    with pytest.raises(SessionExitError) as caught:
        invoke()
    assert caught.value is original
    assert entered == [True]


def test_scope_rejects_early_return_reentry_and_completion_after_exit() -> None:
    """Mandatory outer exit cannot silently admit a missing runner completion."""
    observer, output = plugin()
    call = observer.scope()
    with pytest.raises(RuntimeError, match="did not complete"), call:
        pass
    assert output
    with pytest.raises(RuntimeError, match="cannot be reused"), observer.scope():
        pass
    with pytest.raises(RuntimeError, match="Invalid original"):
        call.complete(0)


@pytest.mark.parametrize("status", [0, 1, 3])
def test_scope_retains_nonzero_exit_and_requires_complete_admission(
    status: int,
) -> None:
    """Invalid observer converts only a zero outcome, preserving existing failure."""
    observer, _ = plugin()
    with observer.scope() as call:
        call.complete(status)
    assert call.result == (status if status != 0 else 1)


def test_scope_passes_only_with_target_call_and_finalized_fixture() -> None:
    """All observer gates and original success permit a diagnostic pass."""
    observer, _ = plugin()
    service, request = Service(), Request()
    with observer.scope() as call:
        observer.pytest_collection_modifyitems([SimpleNamespace(nodeid=TARGET)])
        setup(observer, request, service)
        report = observer.pytest_runtest_makereport(SimpleNamespace(nodeid=TARGET))
        next(report)
        with pytest.raises(StopIteration):
            report.send(SimpleNamespace(when="call", failed=False))
        call.complete(0)
    assert call.result == 0
    assert observer.diagnostic_ok


def test_failed_report_control_preserves_report_then_propagates_outside_hook() -> None:
    """A diagnostic session exit cannot replace the original failed Report."""
    observer, output = plugin()
    control = SessionExitError("failed-report control")
    original = SimpleNamespace(when="call", failed=True)

    def emit(packet: dict[str, object]) -> None:
        if "overlay_signal_observer" in packet:
            raise control
        output.append(packet)

    observer.emit = emit

    def invoke() -> None:
        with observer.scope() as call:
            hook = observer.pytest_runtest_makereport(SimpleNamespace(nodeid=TARGET))
            next(hook)
            with pytest.raises(StopIteration) as stopped:
                hook.send(original)
            assert stopped.value.value is original
            call.complete(1)

    with pytest.raises(SessionExitError) as caught:
        invoke()
    assert caught.value is control
    assert observer.error_stages == ["emitter"]
    assert output[-1]["deferred_controls"] == 1


def test_summary_control_cannot_replace_original_runner_exception() -> None:
    """Mandatory finalization preserves both the runner and summary controls."""
    observer, _ = plugin()
    original = ValueError("original runner")
    control = SessionExitError("summary control")

    def emit(_packet: dict[str, object]) -> None:
        raise control

    observer.emit = emit

    def invoke() -> None:
        with observer.scope():
            raise original

    with pytest.raises(BaseExceptionGroup) as caught:
        invoke()
    assert caught.value.exceptions == (original, control)
    assert observer.error_stages == ["summary_emitter"]
    assert not observer.diagnostic_ok


def test_duplicate_setup_cannot_retire_original_owned_connections() -> None:
    """A duplicate wrapper must not add an empty finalizer that retires the owner."""
    observer, _ = plugin()
    original, duplicate, request = Service(), Service(), Request()
    setup(observer, request, original)
    original_finalizers = len(request.finalizers)
    setup(observer, request, duplicate)
    assert len(request.finalizers) == original_finalizers
    assert not observer.retired
    assert not duplicate.cancelled.receivers
    request.finish()
    assert not original.cancelled.receivers
    assert not original.captured.receivers
    assert not original.failed.receivers
    assert observer.error_stages == ["connection"]


def test_false_disconnect_is_visible_and_does_not_skip_other_owned_cleanup() -> None:
    """Literal false from the actual Qt bool API is an unconfirmed cleanup."""
    observer, _ = plugin()
    service, request = Service(), Request()
    service.captured.disconnect_false = True
    setup(observer, request, service)
    request.finish()
    assert observer.error_stages == ["cleanup"]
    assert not service.cancelled.receivers
    assert not service.failed.receivers
    assert not observer.diagnostic_ok
