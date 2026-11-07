"""Harness admission tests; simulated witnesses do not establish native OS proof."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import owned_helper_smoke as smoke

from verdiclip.platform.owned_helper import HelperCommand, HelperStatus


class FakeHelper:
    """Hold a stable synthetic ownership receipt across watchdog release."""

    def __init__(self, *, confirmed: bool, promote: bool) -> None:
        self.confirmed = confirmed
        self.promote = promote
        self.status = HelperStatus(
            pid=101,
            producer_verified=True,
            exit_code=None,
            remaining_pids=(101, 102),
            termination_requested=False,
            cleanup_confirmed=False,
            closed=False,
            failure=None,
            cleanup_errors=(),
        )

    def start(self, _command: HelperCommand) -> HelperStatus:
        """Return already created fixture identities."""
        return self.status

    def poll(self) -> HelperStatus:
        """Optionally model an incorrect promotion after watchdog release."""
        if self.promote and self.status.closed:
            return replace(self.status, cleanup_confirmed=True)
        return self.status

    def release_unconfirmed(self, reason: str) -> HelperStatus:
        """Model the production latch, or an explicitly requested faulty receipt."""
        if not self.status.closed:
            self.status = replace(
                self.status,
                remaining_pids=None,
                termination_requested=True,
                cleanup_confirmed=self.confirmed,
                closed=True,
                failure=reason,
                cleanup_errors=(f"Cleanup unconfirmed: {reason}",),
            )
        return self.status

    def close(self) -> HelperStatus:
        """Leave the watchdog receipt unchanged."""
        return self.status


class FakeWitness:
    """Simulate witnessed death separately from fallback process cleanup."""

    def __init__(self, *, survive: bool) -> None:
        self.survive = survive
        self.checks = 0
        self.fallback_cleanup = False

    def all_exited(self) -> bool:
        """A failed witness can appear dead only after fallback cleanup."""
        self.checks += 1
        return self.fallback_cleanup or (not self.survive and self.checks > 1)

    def close(self) -> None:
        """Simulate emergency cleanup that must never count as native proof."""
        self.fallback_cleanup = True


def prepare(
    monkeypatch: pytest.MonkeyPatch,
    *,
    confirmed: bool = False,
    promote: bool = False,
    survive: bool = False,
) -> tuple[FakeHelper, FakeWitness]:
    """Replace OS boundaries and advance a bounded deterministic fixture clock."""
    helper = FakeHelper(confirmed=confirmed, promote=promote)
    witness = FakeWitness(survive=survive)
    clock = [0.0]

    def now() -> float:
        value = clock[0]
        clock[0] += 1
        return value

    def retain(pids: Sequence[int]) -> FakeWitness:
        assert list(pids) == [101, 102]
        return witness

    monkeypatch.setattr(smoke, "OwnedHelper", lambda: helper)
    monkeypatch.setattr(smoke, "ProcessWitness", retain)
    monkeypatch.setattr(smoke, "command", lambda *_args: None)
    monkeypatch.setattr(
        smoke, "ready", lambda *_args: {"pid": 101, "descendant_pid": 102}
    )
    monkeypatch.setattr(smoke.time, "monotonic", now)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    return helper, witness


def test_watchdog_smoke_keeps_unconfirmed_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate positive process-exit evidence from the unconfirmed API receipt."""
    helper, witness = prepare(monkeypatch)
    receipt = smoke.prove_unconfirmed_release(Path("fixture"))
    assert receipt["passed"] is True
    assert receipt["processes_exited"] is True
    assert receipt["witnessed_pids"] == [101, 102]
    assert not helper.status.cleanup_confirmed
    assert helper.status.failure == "watchdog proof"
    assert witness.fallback_cleanup


def test_fallback_kill_cannot_turn_watchdog_smoke_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Survival at the deadline fails even though finally cleans the fixture up."""
    _, witness = prepare(monkeypatch, survive=True)
    with pytest.raises(RuntimeError, match="survived watchdog"):
        smoke.prove_unconfirmed_release(Path("fixture"))
    assert witness.fallback_cleanup
    assert witness.all_exited()


@pytest.mark.parametrize(("confirmed", "promote"), [(True, False), (False, True)])
def test_smoke_rejects_false_confirmation(
    monkeypatch: pytest.MonkeyPatch, *, confirmed: bool, promote: bool
) -> None:
    """Both immediate and late confirmation claims invalidate the watchdog proof."""
    prepare(monkeypatch, confirmed=confirmed, promote=promote)
    with pytest.raises(RuntimeError, match=r"incorrectly claimed|remain latched"):
        smoke.prove_unconfirmed_release(Path("fixture"))
