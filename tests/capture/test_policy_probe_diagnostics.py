"""Synthetic process outcomes qualify diagnostics without launching PowerShell."""

import subprocess
from unittest.mock import Mock

import pytest
from tests.capture.test_scroll_metric_policy import run_policy_probe


@pytest.mark.parametrize(
    "partial",
    [None, "policy-phase=entry", b"policy-phase=parsed", "policy-phase=json-start"],
)
def test_timeout_keeps_partial_phase_evidence_and_never_retries(
    monkeypatch: pytest.MonkeyPatch,
    partial: str | bytes | None,
) -> None:
    """Missing, startup, parser and JSON-phase receipts remain explicit failures."""
    command = ["unused-mocked-host", "fixed-policy.ps1"]
    launch = Mock(side_effect=subprocess.TimeoutExpired(command, 15, output=partial))
    monkeypatch.setattr(subprocess, "run", launch)
    with pytest.raises(AssertionError, match="unchanged 15s deadline") as failure:
        run_policy_probe(command)
    expected = partial.decode() if isinstance(partial, bytes) else (partial or "")
    assert repr(expected) in str(failure.value)
    launch.assert_called_once_with(
        command,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        creationflags=0x08000000,
    )


def test_completion_marker_does_not_turn_a_timeout_into_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung process still fails even if all policy assertions had completed."""
    output = b"owned-metrics-policy-passed\npolicy-phase=completed\n"
    launch = Mock(side_effect=subprocess.TimeoutExpired(["unused"], 15, output=output))
    monkeypatch.setattr(subprocess, "run", launch)
    with pytest.raises(AssertionError, match="policy-phase=completed"):
        run_policy_probe(["unused"])
    launch.assert_called_once()


def test_nonzero_policy_result_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Diagnostics never override a failed assertion or rewrite its exit code."""
    outcome = subprocess.CompletedProcess(["unused"], 1, "phase", "policy refused")
    monkeypatch.setattr(subprocess, "run", Mock(return_value=outcome))
    assert run_policy_probe(["unused"]) is outcome


def test_timeout_diagnostics_decode_and_bound_both_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed oversized output keeps bounded tails without hiding timeout."""
    stdout = b"discarded-out-head" + b"x" * 9000 + b"\xffOUTTAIL"
    stderr = b"discarded-err-head" + b"y" * 9000 + b"\xfeERRTAIL"
    launch = Mock(
        side_effect=subprocess.TimeoutExpired(
            ["unused"],
            15,
            output=stdout,
            stderr=stderr,
        )
    )
    monkeypatch.setattr(subprocess, "run", launch)
    with pytest.raises(AssertionError, match="unchanged 15s deadline") as failure:
        run_policy_probe(["unused"])
    message = str(failure.value)
    assert "discarded-out-head" not in message
    assert "discarded-err-head" not in message
    assert repr(stdout.decode("utf-8", errors="replace")[-8192:]) in message
    assert repr(stderr.decode("utf-8", errors="replace")[-8192:]) in message
    assert len(message) < 17000
    launch.assert_called_once()
