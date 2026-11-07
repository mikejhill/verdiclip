"""GUI-side source-bound hold and fixed raw reference handoff, without native I/O."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke


def test_death_hold_is_source_bound_and_has_no_grant_or_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only bounded metadata is published; no facade method is available here."""
    journal = smoke.Journal(tmp_path / "calls.jsonl", time.monotonic() + 15)
    monkeypatch.setattr(smoke.time, "monotonic_ns", lambda: 120)
    token = {
        "credit_schema": 1,
        "epoch": "ab" * 16,
        "frame_id": 1,
        "chunk_index": 0,
        "offset": 0,
        "count": 65536,
    }
    try:
        smoke.hold_parent_death(tmp_path, "cd" * 16, 1, 100, token, 110, journal)
        value = smoke.read(tmp_path / "death-held.json")
        assert value["hold"]["token"] == token
        assert value["hold"]["no_credit_issued"] is True
        assert value["hold"]["no_cancel_issued"] is True
        assert (
            value["hold"]["harness_sha256"]
            == hashlib.sha256(Path(smoke.__file__).read_bytes()).hexdigest()
        )
        with pytest.raises(RuntimeError, match="Duplicate"):
            smoke.hold_parent_death(tmp_path, "cd" * 16, 1, 100, token, 110, journal)
    finally:
        journal.stream.close()


@pytest.mark.parametrize("now", [2_000_000_100, 2_000_000_101])
def test_expired_hold_never_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, now: int
) -> None:
    """The conservative two-second edge rejects without a grace period."""
    journal = smoke.Journal(tmp_path / "calls.jsonl", time.monotonic() + 15)
    monkeypatch.setattr(smoke.time, "monotonic_ns", lambda: now)
    try:
        with pytest.raises(RuntimeError, match="Late"):
            smoke.hold_parent_death(tmp_path, "cd" * 16, 1, 100, {}, 110, journal)
        assert not (tmp_path / "death-held.json").exists()
    finally:
        journal.stream.close()


def test_reference_is_exact_bounded_and_fixed_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The survivor receives the original independent RGB, not a provider crop."""
    executor = tmp_path / "executor"
    executor.mkdir()
    (tmp_path / "death-witness").mkdir()
    journal = smoke.Journal(executor / "calls.jsonl", time.monotonic() + 15)
    initial = {"rect": [0, 0, 900, 640]}
    expected = {"pose": "frozen"}
    fixture = {"authored": "fixture"}
    monkeypatch.setattr(smoke, "latest", Mock(return_value=fixture))
    monkeypatch.setattr(smoke, "fixture_key", Mock(return_value=expected))
    raw = b"x" * smoke.DEATH_REFERENCE_BYTES
    try:
        smoke.freeze_death_reference(
            executor, tmp_path / "states", "cd" * 16, initial, expected, raw, journal
        )
        assert (tmp_path / "death-witness/before-root.rgb").read_bytes() == raw
        value = smoke.read(executor / "death-reference.json")
        assert value["before_rgb_sha256"] == hashlib.sha256(raw).hexdigest()
        assert value["reference_initial"] == initial
        assert value["fixture"] == fixture
        with pytest.raises(RuntimeError, match="Duplicate"):
            smoke.freeze_death_reference(
                executor,
                tmp_path / "states",
                "cd" * 16,
                initial,
                expected,
                raw,
                journal,
            )
    finally:
        journal.stream.close()


def test_bad_reference_size_never_writes(tmp_path: Path) -> None:
    """No malformed reference allocation or file can reach the witness."""
    journal = smoke.Journal(tmp_path / "calls.jsonl", time.monotonic() + 15)
    try:
        with pytest.raises(RuntimeError, match="size"):
            smoke.freeze_death_reference(
                tmp_path,
                tmp_path,
                "cd" * 16,
                {"rect": [0, 0, 900, 640]},
                {},
                b"bad",
                journal,
            )
        assert not (tmp_path / "death-reference.json").exists()
    finally:
        journal.stream.close()
