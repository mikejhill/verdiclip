"""Fake-port shutdown checks; standalone mode never imports Qt or helpers."""

# Standalone unittest execution deliberately avoids the repository's Qt conftest.
# ruff: noqa: PT009, PT027

from __future__ import annotations

import ast
import importlib
import runpy
import unittest
from collections.abc import Callable
from enum import Enum
from math import ceil
from pathlib import Path
from types import FunctionType, SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


class CaptureError(Exception):
    """Pure exception port."""


class State(Enum):
    """Only the QProcess terminal-state distinction is required."""

    NotRunning = 0
    Running = 1


def method(path: str, owner: str, name: str, **ports: object) -> Callable[..., None]:
    """Use imported methods in pytest; standalone mode avoids importing Qt."""
    namespace = {"CaptureError": CaptureError, "CLEANUP_MS": 2000, **ports}
    if __name__ != "__main__":
        module = importlib.import_module(
            "verdiclip." + path.removesuffix(".py").replace("/", ".")
        )
        actual = cast("FunctionType", getattr(getattr(module, owner), name))

        def invoke(*args: object, **kwargs: object) -> None:
            with patch.dict(actual.__globals__, namespace):
                actual(*args, **kwargs)

        return invoke
    tree = ast.parse((ROOT / "src/verdiclip" / path).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner)
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    unit = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias("annotations")], level=0
            ),
            fn,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(unit), path, "exec"), namespace)  # noqa: S102 - repository method with explicit fake ports
    return cast("Callable[..., None]", namespace[name])


def worker_shutdown() -> Callable[..., None]:
    """Bind only state/liveness ports, never the Qt module."""
    return method(
        "capture/scroll/uia_worker.py",
        "UiaWorkerLease",
        "shutdown",
        QProcess=SimpleNamespace(ProcessState=State),
        isValid=lambda _value: True,
        monotonic=lambda: 0,
        ceil=ceil,
    )


class ShutdownTests(unittest.TestCase):
    """Lifecycle behavior with controlled completion, errors and clocks."""

    def test_idle_warm_worker_waits_and_cleans_after_terminal_state(self) -> None:
        """Private files retire strictly after owned child completion."""
        calls = []
        process = SimpleNamespace()
        process.state = Mock(side_effect=[State.Running, State.NotRunning])
        process.waitForFinished = Mock(
            side_effect=lambda ms: calls.append(("wait", ms))
        )
        worker = SimpleNamespace(
            _process=process,
            _scope_open=True,
            _ownership_lost=False,
            _cleanup_state="waiting",
            cancel=Mock(side_effect=lambda: calls.append("cancel")),
        )

        def dispose(_process: object) -> None:
            calls.append("cleanup")
            worker._process = None
            worker._scope_open = False
            worker._cleanup_state = "confirmed"

        worker._dispose = dispose
        worker_shutdown()(worker, timeout_ms=73)
        self.assertEqual(calls, ["cancel", ("wait", 73), "cleanup"])

    def test_timeout_keeps_owned_process_and_cleanup_reserved(self) -> None:
        """Exhaustion cannot free the child slot or run file cleanup."""
        process = SimpleNamespace(
            state=lambda: State.Running, waitForFinished=Mock(return_value=False)
        )
        worker = SimpleNamespace(
            _process=process,
            _scope_open=True,
            _ownership_lost=False,
            _cleanup_state="waiting",
            cancel=Mock(),
            _dispose=Mock(),
        )
        with self.assertRaises(CaptureError):
            worker_shutdown()(worker, timeout_ms=0)
        process.waitForFinished.assert_called_once_with(0)
        worker._dispose.assert_not_called()
        self.assertIs(worker._process, process)
        self.assertTrue(worker._closing)

    def test_unknown_or_failed_file_cleanup_cannot_report_success(self) -> None:
        """Missing PID evidence cannot override failed ownership or cleanup."""
        for lost, state, scope in [
            (True, "confirmed", False),
            (False, "callback_failed", False),
            (False, "confirmed", True),
        ]:
            with self.subTest(lost=lost, state=state, scope=scope):
                worker = SimpleNamespace(
                    _process=None,
                    _scope_open=scope,
                    _ownership_lost=lost,
                    _cleanup_state=state,
                    cancel=Mock(),
                )
                with self.assertRaises(CaptureError):
                    worker_shutdown()(worker, timeout_ms=1)

    def test_pre_child_partial_startup_and_repeated_retirement(self) -> None:
        """No admitted child needs no wait and repeated cleanup is harmless."""
        worker = SimpleNamespace(
            _process=None,
            _scope_open=False,
            _ownership_lost=False,
            _cleanup_state="closed",
            cancel=Mock(),
        )
        worker_shutdown()(worker)
        worker_shutdown()(worker)
        self.assertEqual(worker.cancel.call_count, 2)

    def test_shared_deadline_and_independent_second_lane_after_failure(self) -> None:
        """The second lane receives only the remainder even after first failure."""
        first = SimpleNamespace(shutdown=Mock(side_effect=CaptureError("timeout")))
        second = SimpleNamespace(shutdown=Mock())
        scope = SimpleNamespace(
            _leases={False: first, True: second},
            _retired=set(),
            cancel=Mock(),
            busy=False,
            worker_pids=(),
        )
        clock = Mock(side_effect=[10.0, 10.25, 12.5])
        shutdown = method(
            "capture/scroll/uia_process.py",
            "_IsolatedScope",
            "shutdown",
            monotonic=clock,
            isValid=lambda _value: True,
        )
        with self.assertRaises(CaptureError):
            shutdown(scope, timeout_ms=2000)
        first.shutdown.assert_called_once_with(timeout_ms=1750)
        second.shutdown.assert_called_once_with(timeout_ms=0)
        self.assertTrue(scope._released)
        self.assertTrue(scope._broken)

    def test_idle_workflow_still_retires_adapter(self) -> None:
        """IDLE does not imply an absence of warm helpers."""
        phases = SimpleNamespace(CLOSING="closing")
        workflow = SimpleNamespace(
            cancel=Mock(),
            _adapter=SimpleNamespace(shutdown=Mock()),
            _state="idle",
            has_work=False,
        )
        shutdown = method(
            "capture/scroll/workflow.py",
            "ScrollWorkflow",
            "shutdown",
            WorkflowState=phases,
            StartupLifetime=runpy.run_path(
                str(ROOT / "src/verdiclip/startup_lifetime.py")
            )["StartupLifetime"],
        )
        shutdown(workflow, timeout_ms=99)
        workflow._adapter.shutdown.assert_called_once_with(timeout_ms=99)
        workflow.cancel.side_effect = CaptureError("cancel failure")
        with self.assertRaises(CaptureError):
            shutdown(workflow, timeout_ms=99)
        self.assertEqual(workflow._adapter.shutdown.call_count, 2)
        control = KeyboardInterrupt()
        workflow.cancel.side_effect = control
        workflow._adapter.shutdown.side_effect = CaptureError("cleanup")
        with self.assertRaises(KeyboardInterrupt) as caught:
            shutdown(workflow, timeout_ms=99)
        self.assertIs(caught.exception, control)
        self.assertIsInstance(control.__cause__, CaptureError)

    def test_controller_independent_cleanup_after_provider_failure(self) -> None:
        """Provider uncertainty does not abandon tray/hotkey retirement."""
        lifetime = runpy.run_path(str(ROOT / "src/verdiclip/startup_lifetime.py"))[
            "StartupLifetime"
        ]
        controller = SimpleNamespace(
            _check_timer=SimpleNamespace(stop=Mock()),
            _idle_timer=SimpleNamespace(stop=Mock()),
            _update_status=None,
            _capture=SimpleNamespace(cancel=Mock()),
            _scroll_workflow=SimpleNamespace(
                shutdown=Mock(side_effect=CaptureError("unconfirmed"))
            ),
            _workspace=SimpleNamespace(cancel_drag=Mock()),
            _hotkeys=SimpleNamespace(close=Mock()),
            _tray=SimpleNamespace(hide=Mock()),
        )
        shutdown = method(
            "shell/controller.py", "AppController", "shutdown", StartupLifetime=lifetime
        )
        with self.assertRaises(CaptureError):
            shutdown(controller)
        controller._workspace.cancel_drag.assert_called_once()
        controller._hotkeys.close.assert_called_once()
        controller._tray.hide.assert_called_once()

    def test_shutdown_facade_never_creates_a_worker_and_stops_admission(self) -> None:
        """A partial startup with no lease closes without constructing one."""
        facade = SimpleNamespace(_isolated=True, _scope=None, _lease=None, busy=False)
        shutdown = method(
            "capture/scroll/uia_process.py",
            "UiaProcess",
            "shutdown",
            UiaWorkerLease=SimpleNamespace(CLEANUP_MS=2000),
        )
        shutdown(facade)
        self.assertTrue(facade._shutdown)
        start = method("capture/scroll/uia_process.py", "UiaProcess", "start")
        with self.assertRaisesRegex(CaptureError, "shut down"):
            start(facade, object(), {})

    def test_cancellation_exception_still_attempts_each_owned_lane(self) -> None:
        """An earlier cancellation error cannot abandon either child."""
        leases = [SimpleNamespace(shutdown=Mock()), SimpleNamespace(shutdown=Mock())]
        scope = SimpleNamespace(
            _leases=dict(enumerate(leases)),
            _retired=set(),
            cancel=Mock(side_effect=CaptureError("cancel")),
            busy=False,
            worker_pids=(),
        )
        shutdown = method(
            "capture/scroll/uia_process.py",
            "_IsolatedScope",
            "shutdown",
            monotonic=lambda: 0,
            isValid=lambda _value: True,
        )
        with self.assertRaises(CaptureError) as caught:
            shutdown(scope, timeout_ms=9)
        self.assertIsInstance(caught.exception.__cause__, ExceptionGroup)
        for lease in leases:
            lease.shutdown.assert_called_once_with(timeout_ms=9)

    def test_worker_cancel_error_still_retires_terminal_owned_process(self) -> None:
        """Wait/cleanup are attempted despite a preceding cancellation error."""
        process = SimpleNamespace(state=lambda: State.NotRunning)
        worker = SimpleNamespace(
            _process=process,
            _scope_open=True,
            _ownership_lost=False,
            _cleanup_state="waiting",
            cancel=Mock(side_effect=CaptureError("cancel")),
            _dispose=Mock(),
        )
        with self.assertRaises(CaptureError):
            worker_shutdown()(worker, timeout_ms=1)
        worker._dispose.assert_called_once_with(process)

    def test_invalid_unretired_worker_is_not_touched_or_accepted(self) -> None:
        """Destroyed wrappers cannot be consulted to infer child retirement."""
        lease = object()
        scope = SimpleNamespace(
            _leases={False: lease},
            _retired=set(),
            cancel=Mock(),
            busy=False,
            worker_pids=(),
        )
        shutdown = method(
            "capture/scroll/uia_process.py",
            "_IsolatedScope",
            "shutdown",
            monotonic=lambda: 0,
            isValid=lambda _value: False,
        )
        with self.assertRaises(CaptureError):
            shutdown(scope, timeout_ms=1)

    def test_first_owner_control_preserves_identity_after_second_retirement(
        self,
    ) -> None:
        """Process control exceptions propagate only after independent cleanup."""
        for control in (KeyboardInterrupt(), SystemExit(17)):
            first = SimpleNamespace(shutdown=Mock(side_effect=control))
            second = SimpleNamespace(shutdown=Mock())
            scope = SimpleNamespace(
                _leases={False: first, True: second},
                _retired=set(),
                cancel=Mock(),
                busy=False,
                worker_pids=(),
            )
            shutdown = method(
                "capture/scroll/uia_process.py",
                "_IsolatedScope",
                "shutdown",
                monotonic=lambda: 0,
                isValid=lambda _value: True,
            )
            with self.assertRaises(type(control)) as caught:
                shutdown(scope, timeout_ms=10)
            self.assertIs(caught.exception, control)
            second.shutdown.assert_called_once_with(timeout_ms=10)
            self.assertTrue(scope._broken)

    def test_worker_cancel_control_still_kills_waits_and_cleans(self) -> None:
        """The exact child is retired before the original control reappears."""
        control = KeyboardInterrupt()
        process = SimpleNamespace(
            state=Mock(side_effect=[State.Running, State.NotRunning]),
            kill=Mock(),
            waitForFinished=Mock(return_value=True),
        )
        worker = SimpleNamespace(
            _process=process,
            _scope_open=True,
            _ownership_lost=False,
            _cleanup_state="waiting",
            cancel=Mock(side_effect=control),
            _dispose=Mock(),
        )
        with self.assertRaises(KeyboardInterrupt) as caught:
            worker_shutdown()(worker, timeout_ms=10)
        self.assertIs(caught.exception, control)
        process.kill.assert_called_once()
        process.waitForFinished.assert_called_once_with(10)
        worker._dispose.assert_called_once_with(process)

    def test_scope_cleanup_controls_do_not_skip_second_private_owner(self) -> None:
        """Both callback loops retain failures and propagate original controls."""
        for aggregate in (False, True):
            control = KeyboardInterrupt()
            callback = Mock(side_effect=control)
            second = Mock()
            owner = SimpleNamespace(
                _scope_open=True,
                _ownership_lost=False,
                _cleanup_state="waiting",
                _cleanup=[callback, second],
                _run_cleanup=lambda fn: fn(),
                _trace=SimpleNamespace(mark=Mock()),
                cleanup_failed=SimpleNamespace(emit=Mock()),
                scope_closed=SimpleNamespace(emit=Mock()),
                _finishing=False,
                _cancelling=False,
                _pending=None,
                _open=True,
                _leases={},
                _retired=set(),
                _failed_cleanup=[],
                _broken=False,
                busy=True,
                busy_changed=SimpleNamespace(emit=Mock()),
                _released=False,
            )
            close = method(
                "capture/scroll/uia_process.py"
                if aggregate
                else "capture/scroll/uia_worker.py",
                "_IsolatedScope" if aggregate else "UiaWorkerLease",
                "_finish_scope" if aggregate else "_close_scope",
                UiaWorkerLease=SimpleNamespace(_run_cleanup=lambda fn: fn()),
                logger=SimpleNamespace(warning=Mock()),
            )
            with self.assertRaises(KeyboardInterrupt) as caught:
                close(owner)
            self.assertIs(caught.exception, control)
            second.assert_called_once()
            owner.scope_closed.emit.assert_not_called()
            self.assertEqual(
                owner._failed_cleanup if aggregate else owner._cleanup, [callback]
            )


if __name__ == "__main__":
    unittest.main()
