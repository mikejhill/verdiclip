"""Standalone pure fake profiler checks: no root pytest/Qt/native imports."""
# ruff: noqa: PT009, PT027 - standalone unittest avoids root Qt conftest

from __future__ import annotations

import inspect
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gui_readiness_profile import (
    MAX_DISPATCH_ANCHORS,
    MAX_OFFSET_NS,
    MAX_RECORDS,
    TARGET,
    Patch,
    PatchSet,
    Profiler,
    Trace,
    require_hosted,
)


class Clock:
    """Deterministic clocks, with no timers or blocking waits."""

    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        """Advance one synthetic nanosecond."""
        self.value += 1
        return self.value


class PureProfilerTests(unittest.TestCase):
    """Exercise forwarding, bounded records, owned restoration and fail-closed use."""

    def trace(self) -> Trace:
        """Use deterministic clocks without native work."""
        clock = Clock()
        return Trace(wall=clock, process=clock, thread=clock)

    def number(self, record: dict[str, object], key: str) -> float:
        """Require a concrete numeric fake-clock field before comparison."""
        value = record[key]
        assert isinstance(value, (int, float))
        return float(value)

    def test_forward_result_arguments_and_exception_identity(self) -> None:
        """Timing must not change the original operation's behavior."""
        trace = self.trace()
        patch = Patch(object, "unused", "fixed")
        token = object()
        calls = []

        def original(*args: object, **kwargs: object) -> object:
            calls.append((args, kwargs))
            return token

        wrapped = trace.wrap(patch, original)
        self.assertIs(wrapped(1, secret_argument=2), token)
        self.assertEqual(calls, [((1,), {"secret_argument": 2})])
        self.assertNotIn("secret_argument", str(trace.records))
        failure = ValueError("private path must never enter trace")

        def broken() -> None:
            raise failure

        with self.assertRaises(ValueError) as caught:
            trace.wrap(patch, broken)()
        self.assertIs(caught.exception, failure)
        self.assertFalse(trace.records[-1]["completed"])
        self.assertNotIn("private path", str(trace.records))

    def test_original_control_exception_propagates(self) -> None:
        """A real product control is never caught by diagnostic forwarding."""
        failure = KeyboardInterrupt()

        def original() -> None:
            raise failure

        with self.assertRaises(KeyboardInterrupt) as caught:
            self.trace().wrap(Patch(object, "unused", "fixed"), original)()
        self.assertIs(caught.exception, failure)

    def test_nested_first_warm_and_after_dispatch_records(self) -> None:
        """Separate dispatch ordinals and delayed callbacks without GUI events."""
        trace = self.trace()
        nested = trace.wrap(Patch(object, "unused", "editor_show"), lambda: 1)
        outer = trace.wrap(
            Patch(object, "unused", "hotkey_dispatch", dispatch=True), nested
        )
        outer()
        nested()
        outer()
        self.assertEqual([r["capture_ordinal"] for r in trace.records], [1, 1, 1, 2, 2])
        self.assertEqual(
            [r["within_dispatch"] for r in trace.records],
            [True, True, False, True, True],
        )
        self.assertEqual(trace.dispatch_depth, 0)
        first_show, first_dispatch, after_dispatch, warm_show, warm_dispatch = (
            trace.records
        )
        self.assertEqual(first_dispatch["wall_start_offset_ms"], 0)
        self.assertEqual(warm_dispatch["wall_start_offset_ms"], 0)
        self.assertGreater(self.number(first_show, "wall_start_offset_ms"), 0)
        self.assertLess(
            self.number(first_show, "wall_end_offset_ms"),
            self.number(first_dispatch, "wall_end_offset_ms"),
        )
        self.assertGreater(
            self.number(after_dispatch, "wall_start_offset_ms"),
            self.number(first_dispatch, "wall_end_offset_ms"),
        )
        self.assertGreater(self.number(warm_show, "wall_start_offset_ms"), 0)
        self.assertLess(
            self.number(warm_show, "wall_end_offset_ms"),
            self.number(warm_dispatch, "wall_end_offset_ms"),
        )

    def test_show_event_offsets_split_before_callback_and_after_callback(self) -> None:
        """Nested timing identifies gaps without any new event or native probe."""
        clock = Clock()
        trace = Trace(wall=clock, process=clock, thread=clock)
        callback = trace.wrap(Patch(object, "unused", "editor_show_event"), lambda: 1)
        show = trace.wrap(Patch(object, "unused", "editor_show"), callback)
        dispatch = trace.wrap(
            Patch(object, "unused", "hotkey_dispatch", dispatch=True), show
        )
        output = StringIO()
        with redirect_stdout(output):
            dispatch()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(clock.value, 18)  # Three phases, start/end, three clocks.
        event_record, show_record, dispatch_record = trace.records
        before = self.number(event_record, "wall_start_offset_ms") - self.number(
            show_record, "wall_start_offset_ms"
        )
        after = self.number(show_record, "wall_end_offset_ms") - self.number(
            event_record, "wall_end_offset_ms"
        )
        self.assertGreater(before, 0)
        self.assertGreater(after, 0)
        self.assertAlmostEqual(
            before + self.number(event_record, "wall_ms") + after,
            self.number(show_record, "wall_ms"),
        )
        self.assertEqual(dispatch_record["wall_start_offset_ms"], 0)

    def test_offset_anchor_limit_and_range_are_bounded(self) -> None:
        """Missing/oversized anchors retain no absolute uptime and fail diagnostics."""
        trace = self.trace()
        self.assertEqual(trace.offsets(0, 100, 200), (None, None))
        dispatch = trace.wrap(
            Patch(object, "unused", "hotkey_dispatch", dispatch=True), lambda: 1
        )
        for _ in range(MAX_DISPATCH_ANCHORS + 1):
            self.assertEqual(dispatch(), 1)
        self.assertEqual(len(trace.dispatch_anchors), MAX_DISPATCH_ANCHORS)
        self.assertIn("dispatch_anchor_limit", trace.errors)
        anchor = trace.dispatch_anchors[1]
        self.assertEqual(
            trace.offsets(1, anchor, anchor + MAX_OFFSET_NS + 1), (None, None)
        )
        self.assertIn("dispatch_offset_out_of_bounds", trace.errors)

    def test_partial_install_and_inherited_descriptor_restore(self) -> None:
        """Rollback deletes only owned shadow attributes and preserves originals."""

        class Base:
            def method(self) -> int:
                return 3

        class Child(Base):
            pass

        original = inspect.getattr_static(Base, "method")
        patches = PatchSet(self.trace())
        with self.assertRaises(AttributeError):
            patches.install(
                (Patch(Child, "method", "method"), Patch(Child, "missing", "missing"))
            )
        self.assertEqual(Child().method(), 3)
        patches.close()
        self.assertNotIn("method", vars(Child))
        self.assertIs(inspect.getattr_static(Base, "method"), original)

    def test_dont_overwrite_an_intervening_patch(self) -> None:
        """Lost ownership fails diagnostics rather than replacing another writer."""

        class Owner:
            def method(self) -> int:
                return 1

        trace = self.trace()
        patches = PatchSet(trace)
        patches.install((Patch(Owner, "method", "method"),))

        def replacement(_self: object) -> int:
            return 2

        attribute = "method"
        setattr(Owner, attribute, replacement)
        patches.close()
        self.assertIs(Owner.method, replacement)
        self.assertIn("patch_ownership_lost", trace.errors)

    def test_record_cap_and_clock_failure_cannot_fake_valid_diagnostic(self) -> None:
        """Bounded recording and clock faults preserve calls but mark failure."""
        trace = self.trace()
        wrapped = trace.wrap(Patch(object, "unused", "fixed"), lambda: 1)
        for _ in range(MAX_RECORDS + 2):
            self.assertEqual(wrapped(), 1)
        self.assertEqual(len(trace.records), MAX_RECORDS)
        self.assertTrue(trace.truncated)

        def broken_clock() -> int:
            raise RuntimeError("unlogged private detail")

        trace = Trace(wall=broken_clock)
        self.assertEqual(trace.wrap(Patch(object, "unused", "fixed"), lambda: 2)(), 2)
        self.assertEqual(trace.errors, ["start_clock"])
        self.assertNotIn("private detail", str(trace.errors))

    def test_hook_restores_on_original_failure_and_emits_after_call(self) -> None:
        """The hook reports only after the unchanged test and preserves its error."""

        class Owner:
            def method(self) -> int:
                return 1

        original = Owner.method
        packets = []
        plugin = Profiler(
            self.trace(), lambda: (Patch(Owner, "method", "method"),), packets.append
        )
        hook = plugin.pytest_runtest_call(SimpleNamespace(nodeid=TARGET))
        next(hook)
        self.assertEqual(packets, [])
        self.assertEqual(Owner().method(), 1)
        failure = AssertionError("unchanged 300ms failure")
        with self.assertRaises(AssertionError) as caught:
            hook.throw(failure)
        self.assertIs(caught.exception, failure)
        self.assertIs(Owner.method, original)
        self.assertEqual(len(packets), 1)
        self.assertFalse(plugin.trace.active)

    def test_inactive_saved_signal_wrapper_only_forwards(self) -> None:
        """A previously connected callback performs no profiling after cleanup."""
        trace = self.trace()
        wrapped = trace.wrap(Patch(object, "unused", "fixed"), lambda: 3)
        trace.active = False
        self.assertEqual(wrapped(), 3)
        self.assertEqual(trace.records, [])

    def test_end_clock_failure_preserves_original_exception(self) -> None:
        """A diagnostic failure in finally cannot mask the product's exception."""
        calls = 0

        def clock() -> int:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError("diagnostic clock unavailable")
            return 0

        failure = ValueError("original product failure")

        def original() -> None:
            raise failure

        trace = Trace(wall=clock)
        with self.assertRaises(ValueError) as caught:
            trace.wrap(Patch(object, "unused", "fixed"), original)()
        self.assertIs(caught.exception, failure)
        self.assertEqual(trace.errors, ["end_clock_or_record"])

    def test_emission_failure_makes_diagnostic_red_after_normal_return(self) -> None:
        """A failed receipt never converts an otherwise green test into proof."""

        def broken_emit(_packet: dict[str, object]) -> None:
            raise OSError("private unavailable output")

        plugin = Profiler(self.trace(), lambda: (), broken_emit)
        hook = plugin.pytest_runtest_call(SimpleNamespace(nodeid=TARGET))
        next(hook)
        with self.assertRaises(StopIteration):
            hook.send(None)
        session = SimpleNamespace(exitstatus=0)
        plugin.pytest_sessionfinish(session)
        self.assertEqual(session.exitstatus, 1)
        self.assertEqual(plugin.trace.errors, ["profile_emit"])

    def test_selection_and_missing_profile_fail_closed(self) -> None:
        """A skipped/missing/broken profile cannot become green."""
        plugin = Profiler(self.trace())
        with self.assertRaises(RuntimeError):
            plugin.pytest_collection_finish(SimpleNamespace(items=[]))
        session = SimpleNamespace(exitstatus=0)
        plugin.pytest_sessionfinish(session)
        self.assertEqual(session.exitstatus, 1)
        session.exitstatus = 2
        plugin.pytest_sessionfinish(session)
        self.assertEqual(session.exitstatus, 2)

    def test_default_emitter_is_an_unbound_instance_callable(self) -> None:
        """The real default receives only its packet, never an implicit profiler."""
        output = StringIO()
        with redirect_stdout(output):
            Profiler().emitter({"fixture": "safe"})
        self.assertEqual(
            output.getvalue(), 'GUI_READINESS_PROFILE={"fixture": "safe"}\n'
        )

    def test_local_guard_refuses_before_any_qt_import(self) -> None:
        """Pure test environments cannot activate the optional hosted loader."""
        before = set(sys.modules)
        with self.assertRaises(RuntimeError):
            require_hosted()
        self.assertFalse(
            any(name.startswith("PySide6") for name in set(sys.modules) - before)
        )


if __name__ == "__main__":
    unittest.main()
