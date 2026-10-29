"""Pure tee tests runnable with unittest without importing Qt or conftest."""

from __future__ import annotations

import io
import unittest

from scripts.ipc_qt_diagnostic import QtHandler, tee_qt_messages


class FakeHandlerSlot:
    """The install/restore contract, with no Qt imports or native operation."""

    def __init__(self, initial: QtHandler | None = None) -> None:
        self.current = initial

    def install(self, handler: QtHandler | None) -> QtHandler | None:
        """Return the exact prior handler while replacing the slot."""
        previous = self.current
        self.current = handler
        return previous


class TestMessageTee(unittest.TestCase):
    """Preserve stderr ordering, handler identity, exceptions and restoration."""

    def test_flushes_before_forwarding_original_arguments(self) -> None:
        """A fatal message reaches stderr before the original capture handler."""
        order: list[str] = []
        arguments: list[tuple[object, object, str]] = []

        class Sink(io.StringIO):
            def flush(self) -> None:
                order.append("flush")
                super().flush()

        def previous(kind: object, context: object, message: str) -> None:
            order.append("capture")
            arguments.append((kind, context, message))

        slot = FakeHandlerSlot(previous)
        sink = Sink()
        context = object()
        with tee_qt_messages(slot.install, sink):
            assert slot.current is not None
            slot.current("fatal", context, "original message")
        assert order == ["flush", "capture"]
        assert arguments == [("fatal", context, "original message")]
        assert sink.getvalue() == "[IPC Qt fatal] original message\n"
        assert slot.current is previous

    def test_restores_on_test_body_failure(self) -> None:
        """A test exception propagates without leaving the tee installed."""
        slot = FakeHandlerSlot()
        with (
            self.assertRaisesRegex(ValueError, "test failure"),  # noqa: PT027 - direct unittest avoids Qt conftest
            tee_qt_messages(slot.install, io.StringIO()),
        ):
            raise ValueError("test failure")
        assert slot.current is None

    def test_preserves_previous_handler_exception(self) -> None:
        """Logging cannot swallow the original capture handler's exception."""

        def previous(_kind: object, _context: object, _message: str) -> None:
            raise ValueError("capture failure")

        slot = FakeHandlerSlot(previous)
        with (
            self.assertRaisesRegex(ValueError, "capture failure"),  # noqa: PT027 - direct unittest avoids Qt conftest
            tee_qt_messages(slot.install, io.StringIO()),
        ):
            assert slot.current is not None
            slot.current("warning", None, "message")
        assert slot.current is previous

    def test_forwards_even_when_output_fails(self) -> None:
        """A failed stderr flush still calls the prior handler and fails visibly."""
        forwarded: list[str] = []

        class BrokenSink(io.StringIO):
            def flush(self) -> None:
                raise OSError("output failure")

        def previous(_kind: object, _context: object, message: str) -> None:
            forwarded.append(message)

        slot = FakeHandlerSlot(previous)
        with (
            self.assertRaisesRegex(OSError, "output failure"),  # noqa: PT027 - direct unittest avoids Qt conftest
            tee_qt_messages(slot.install, BrokenSink()),
        ):
            assert slot.current is not None
            slot.current("fatal", None, "fatal text")
        assert forwarded == ["fatal text"]
        assert slot.current is previous

    def test_nested_drains_restore_exact_handler_chain(self) -> None:
        """Nested call/event-drain instrumentation does not recurse or lose capture."""
        forwarded: list[str] = []

        def previous(_kind: object, _context: object, message: str) -> None:
            forwarded.append(message)

        slot = FakeHandlerSlot(previous)
        sink = io.StringIO()
        with tee_qt_messages(slot.install, sink):
            outer = slot.current
            with tee_qt_messages(slot.install, sink):
                assert slot.current is not None
                slot.current("debug", None, "drain")
            assert slot.current is outer
        assert slot.current is previous
        assert forwarded == ["drain"]


if __name__ == "__main__":
    unittest.main()
