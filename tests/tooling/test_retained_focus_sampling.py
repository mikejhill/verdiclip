"""Pure report-boundary admission and identity ports; no Qt imports or events."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts.retained_overlay_signal_observer import (
    FOCUS_PREDECESSOR,
    TARGET,
    FocusProbe,
    Observer,
    Report,
)


def report(observer: Observer, node: str, phase: str = "call") -> object:
    """Resume the public hook with an unchanged fake report."""
    original = SimpleNamespace(when=phase, failed=False)
    hook = observer.pytest_runtest_makereport(SimpleNamespace(nodeid=node))
    next(hook)
    with pytest.raises(StopIteration) as caught:
        hook.send(cast(Report, original))
    return caught.value.value


def test_sampler_runs_only_at_exact_predecessor_post_call() -> None:
    """No sampling happens at construction, signal recording or other reports."""
    calls: list[str] = []
    token = object()

    def sample() -> tuple[object, ...]:
        calls.append("sample")
        return (token,)

    def register(tokens: tuple[object, ...]) -> None:
        assert tokens == (token,)
        calls.append("register")

    def forbidden_match(_focus: object) -> dict[str, object]:
        pytest.fail("Report sampling must not compare callback focus")

    probe = cast(FocusProbe, SimpleNamespace(register=register, match=forbidden_match))
    observer = Observer(
        Path("/admitted/mutants"),
        (FOCUS_PREDECESSOR, TARGET),
        lambda _p: None,
        Exception,
        probe,
        sample,
    )
    assert not calls
    for node, phase in (
        (TARGET, "setup"),
        (FOCUS_PREDECESSOR, "setup"),
        ("other", "call"),
    ):
        result = report(observer, node, phase)
        assert isinstance(result, SimpleNamespace)
    assert not calls
    report(observer, FOCUS_PREDECESSOR)
    assert calls == ["sample", "register"]
    assert observer.focus_sampled
    report(observer, FOCUS_PREDECESSOR, "teardown")
    assert calls == ["sample", "register"]
    report(observer, FOCUS_PREDECESSOR)
    assert calls == ["sample", "register"]
    assert observer.error_stages == ["focus_sample"]
    assert not observer.diagnostic_ok


def test_sample_failure_preserves_original_report_and_invalidates_diagnostic() -> None:
    """A sampler error cannot conceal the original report or count as evidence."""

    def sample() -> tuple[object, ...]:
        raise RuntimeError("sample unavailable")

    def forbidden_register(_tokens: tuple[object, ...]) -> None:
        pytest.fail("Failed sample must never register tokens")

    probe = cast(FocusProbe, SimpleNamespace(register=forbidden_register))
    observer = Observer(
        Path("/admitted/mutants"),
        (FOCUS_PREDECESSOR, TARGET),
        lambda _p: None,
        Exception,
        probe,
        sample,
    )
    assert isinstance(report(observer, FOCUS_PREDECESSOR), SimpleNamespace)
    assert not observer.focus_sampled
    assert observer.error_stages == ["focus_sample"]
    assert not observer.diagnostic_ok


@pytest.mark.parametrize("order", [(TARGET,), (TARGET, FOCUS_PREDECESSOR)])
def test_probe_cannot_admit_missing_or_late_predecessor(order: tuple[str, ...]) -> None:
    """Missing or reordered predecessor does not become a valid negative match."""
    with pytest.raises(ValueError, match="predecessor"):
        Observer(
            Path("/admitted/mutants"),
            order,
            lambda _p: None,
            Exception,
            cast(FocusProbe, object()),
            lambda: (),
        )


def test_probe_requires_sampler_pair() -> None:
    """An unpaired model cannot silently produce incomplete evidence."""
    with pytest.raises(ValueError, match="together"):
        Observer(
            Path("/admitted/mutants"),
            (FOCUS_PREDECESSOR, TARGET),
            lambda _p: None,
            Exception,
            cast(FocusProbe, object()),
        )
