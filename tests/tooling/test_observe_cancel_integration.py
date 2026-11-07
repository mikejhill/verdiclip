"""Public cancellation call ordering and unchanged success retirement gates."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke


def test_cancel_is_called_once_without_grant(tmp_path: Path) -> None:
    """The public method is immediate, and a duplicate cannot send another call."""
    journal = smoke.Journal(tmp_path / "calls.jsonl", time.monotonic() + 15)
    facade = Mock()
    hold: smoke.Json = {"cancel_called_ns": None, "cancel_returned_ns": None}
    try:
        smoke.cancel_credit(journal, facade, hold)
        facade.cancel.assert_called_once_with()
        facade.grant_frame_credit.assert_not_called()
        called, returned = hold.get("cancel_called_ns"), hold.get("cancel_returned_ns")
        assert isinstance(called, int)
        assert isinstance(returned, int)
        assert 0 < called <= returned
        with pytest.raises(RuntimeError, match="already called"):
            smoke.cancel_credit(journal, facade, hold)
        facade.cancel.assert_called_once_with()
    finally:
        journal.stream.close()


def test_expired_case_cannot_initiate_cancel(tmp_path: Path) -> None:
    """No new public operation starts after the unchanged case deadline."""
    journal = smoke.Journal(tmp_path / "calls.jsonl", time.monotonic() - 1)
    facade = Mock()
    hold: smoke.Json = {"cancel_called_ns": None, "cancel_returned_ns": None}
    try:
        with pytest.raises(RuntimeError):
            smoke.cancel_credit(journal, facade, hold)
        facade.cancel.assert_not_called()
        assert hold["cancel_called_ns"] is None
    finally:
        journal.stream.close()


def test_cancel_exception_retains_attempt_without_retry(tmp_path: Path) -> None:
    """An uncertain public failure is not counted as completed or retried."""
    journal = smoke.Journal(tmp_path / "calls.jsonl", time.monotonic() + 15)
    facade = Mock()
    facade.cancel.side_effect = RuntimeError("cancel failed")
    hold: smoke.Json = {"cancel_called_ns": None, "cancel_returned_ns": None}
    try:
        with pytest.raises(RuntimeError, match="cancel failed"):
            smoke.cancel_credit(journal, facade, hold)
        assert hold["cancel_called_ns"] is not None
        assert hold["cancel_returned_ns"] is None
        with pytest.raises(RuntimeError, match="already called"):
            smoke.cancel_credit(journal, facade, hold)
        facade.cancel.assert_called_once_with()
    finally:
        journal.stream.close()


@pytest.mark.parametrize(
    ("case", "index"), [("success", None), ("cancel-first", 0), ("cancel-interior", 1)]
)
def test_only_fixed_cases(case: str, index: int | None) -> None:
    """No arbitrary chunk or timing selector enters the native experiment."""
    assert smoke.case_index(case) == index


def test_unknown_case_rejected() -> None:
    """An unsupported case cannot become a successful default."""
    with pytest.raises(ValueError, match="Unsupported"):
        smoke.case_index("cancel-last")
