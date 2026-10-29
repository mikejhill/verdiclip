"""Direct pure startup lifetime contracts; no Qt, source copying or AST execution."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from verdiclip.startup_lifetime import StartupLifetime


class StartupError(Exception):
    """A stand-in for the application operation's primary failure."""


def raising(error: BaseException) -> Callable[[], None]:
    """Create an inert failing cleanup operation."""

    def fail() -> None:
        raise error

    return fail


def test_callbacks_retire_independently_in_reverse_order() -> None:
    """Normal successful ownership leaves no callbacks for a second close."""
    calls: list[str] = []
    with StartupLifetime() as lifetime:
        lifetime.callback(calls.append, "first")
        lifetime.callback(calls.append, "second")
    lifetime.close()
    assert calls == ["second", "first"]


@pytest.mark.parametrize(
    "primary",
    [
        StartupError("body"),
        KeyboardInterrupt("body"),
        SystemExit(130),
        GeneratorExit("body"),
    ],
)
def test_primary_identity_survives_ordinary_cleanup_failure(
    primary: BaseException,
) -> None:
    """Cleanup is independent and becomes the original operation's explicit cause."""
    cleanup = RuntimeError("cleanup")
    calls: list[str] = []

    def operation() -> None:
        with StartupLifetime() as lifetime:
            lifetime.callback(calls.append, "last")
            lifetime.callback(raising(cleanup))
            lifetime.callback(calls.append, "first")
            raise primary

    with pytest.raises(type(primary)) as caught:
        operation()
    assert caught.value is primary
    assert caught.value.__cause__ is cleanup
    assert calls == ["first", "last"]


def test_cleanup_error_after_success_is_not_suppressed() -> None:
    """A successful operation cannot conceal a failed retirement."""
    cleanup = RuntimeError("cleanup")

    def operation() -> None:
        with StartupLifetime() as lifetime:
            lifetime.callback(raising(cleanup))

    with pytest.raises(RuntimeError) as caught:
        operation()
    assert caught.value is cleanup


def test_cleanup_control_escapes_with_primary_in_its_chain() -> None:
    """A cleanup interrupt cannot become ordinary AppError handling."""
    primary, control = StartupError("body"), KeyboardInterrupt("cleanup")
    calls: list[str] = []

    def operation() -> None:
        with StartupLifetime() as lifetime:
            lifetime.callback(calls.append, "retired")
            lifetime.callback(raising(control))
            raise primary

    with pytest.raises(KeyboardInterrupt) as caught:
        operation()
    assert caught.value is control
    assert control.__context__ is primary
    assert calls == ["retired"]


def test_later_cleanup_error_cannot_bury_an_earlier_interrupt() -> None:
    """Mixed cleanup errors retain the actual control in an escaping opaque group."""
    primary, control, cleanup = (
        StartupError("body"),
        KeyboardInterrupt("cleanup"),
        RuntimeError("later"),
    )

    def operation() -> None:
        with StartupLifetime() as lifetime:
            lifetime.callback(raising(cleanup))
            lifetime.callback(raising(control))
            raise primary

    with pytest.raises(BaseExceptionGroup) as caught:
        operation()
    assert caught.value.exceptions == (primary, control, cleanup)


def test_grouped_cleanup_control_remains_opaque() -> None:
    """A control group propagates unchanged instead of being split or swallowed."""
    primary = StartupError("body")
    control = BaseExceptionGroup("cleanup", [KeyboardInterrupt("one"), SystemExit(130)])

    def operation() -> None:
        with StartupLifetime() as lifetime:
            lifetime.callback(raising(control))
            raise primary

    with pytest.raises(BaseExceptionGroup) as caught:
        operation()
    assert caught.value is control
    assert control.__context__ is primary
