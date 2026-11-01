"""Pure replay data/guard tests; no cache download or mutmut/Qt execution."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import retained_mutmut_duration_data as replay


def context() -> bytes:
    """Represent only the actual known cache context schema."""
    return json.dumps(
        {
            "schema": 1,
            "policy": "same-commit-os-arch-v1",
            "source_sha": replay.SOURCE_SHA,
            "os": "Linux",
            "arch": "X64",
        }
    ).encode()


def identity() -> bytes:
    """Represent the actual marker fields without any code or verdict data."""
    return json.dumps(
        {
            "format": 1,
            "tool": "3.8.0",
            "identities": {"src/verdiclip/a.py": {"x_f__mutmut_1": "a" * 64}},
            "metadata_sha256": {"src/verdiclip/a.py.meta": "b" * 64},
        }
    ).encode()


def stats(durations: object, label: str = "fresh") -> bytes:
    """Include non-duration strings that would fool substring replacements."""
    data = {
        "git_commit": replay.SOURCE_SHA,
        "function_hashes": {
            "kept": label + ' é \\"duration_by_test\\" : {not a field}'
        },
        "duration_by_test": durations,
        "stats_time": 1.0,
        "tests_by_mangled_function_name": {"f": ["tests/a.py::a"]},
        "function_dependencies": {"f": [label]},
        "config_fingerprint": {"kept": label},
        "watched_file_hashes": {"kept": label},
    }
    return json.dumps(data, ensure_ascii=False, indent=2).replace("\n", "\r\n").encode()


def test_only_duration_changes_preserve_raw_bytes_and_fresh_mappings() -> None:
    """Historical function/source maps cannot replace independently fresh data."""
    fresh = stats({"tests/a.py::a": 1, "tests/b.py::b": 2, "tests/c.py::c": 3})
    historical = stats(
        {"tests/a.py::a": 9, "tests/b.py::b": 0, "tests/c.py::c": 9}, "history"
    )
    result = replay.replay_bytes(fresh, historical, context(), identity())
    before, after = replay.parse_object(fresh), replay.parse_object(result.updated)
    before.pop("duration_by_test")
    after.pop("duration_by_test")
    assert after == before
    assert result.expected_order == ("tests/b.py::b", "tests/a.py::a", "tests/c.py::c")
    assert result.receipt["all_other_data_and_bytes_unchanged"] is True
    assert result.receipt["independent_collection_set_proved"] is False
    assert result.receipt["prior_map_hash_available"] is False
    assert result.receipt["stable_exclusive_directories_required"] is True
    assert result.receipt["hostile_concurrent_writer_safety_proved"] is False
    assert result.receipt["atomic_replace_is_compare_and_swap"] is False
    assert b"\r\n" in result.updated
    assert "é".encode() in result.updated


def test_escaped_top_level_key_is_unambiguous_and_preserved() -> None:
    """JSON-token scanning handles escaped keys without text-search aliases."""
    fresh = stats({"tests/a.py::a": 1}).replace(
        b'"duration_by_test":', b'"\\u0064uration_by_test":'
    )
    result = replay.replay_bytes(
        fresh, stats({"tests/a.py::a": 2}), context(), identity()
    )
    assert b'"\\u0064uration_by_test":' in result.updated
    assert replay.parse_object(result.updated)["duration_by_test"] == {
        "tests/a.py::a": 2
    }


@pytest.mark.parametrize(
    "bad", [True, -1, float("nan"), float("inf"), "1", None, 10**1000]
)
def test_invalid_duration_values_rejected(bad: object) -> None:
    """A real sorter cannot consume bool, negative, nonfinite or oversized values."""
    with pytest.raises(ValueError, match=r"Duration|Nonfinite"):
        replay.replay_bytes(
            stats({"tests/a.py::a": 1}),
            stats({"tests/a.py::a": bad}),
            context(),
            identity(),
        )


@pytest.mark.parametrize(
    "mapping", [{"tests/b.py::b": 1}, {"tests/a.py::a": 1, "tests/b.py::b": 2}]
)
def test_node_set_mismatch_fails_without_partial_replay(
    mapping: dict[str, int],
) -> None:
    """No intersection/fallback may omit fresh stats-recorded tests."""
    with pytest.raises(ValueError, match="node sets differ"):
        replay.replay_bytes(
            stats({"tests/a.py::a": 1}), stats(mapping), context(), identity()
        )


def test_duplicate_keys_are_rejected_including_nested_objects() -> None:
    """Last-key-wins parsing cannot choose an ambiguous duration/context map."""
    for raw in [b'{"x":1,"x":2}', b'{"x":{"y":1,"y":2}}']:
        with pytest.raises(ValueError, match="Duplicate"):
            replay.parse_object(raw)
    duplicate = stats({"tests/a.py::a": 1}).replace(
        b'"stats_time": 1.0', b'"stats_time": 1.0,"duration_by_test":{}'
    )
    with pytest.raises(ValueError, match="Duplicate"):
        replay.replay_bytes(
            duplicate, stats({"tests/a.py::a": 1}), context(), identity()
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema", True), ("source_sha", "a" * 40), ("os", "Windows"), ("arch", "ARM64")],
)
def test_historical_context_requires_exact_source_os_arch(
    field: str, value: object
) -> None:
    """A cache key alone cannot override invalid marker identity."""
    data = json.loads(context())
    data[field] = value
    with pytest.raises(ValueError, match="context mismatch"):
        replay.validate_context(json.dumps(data).encode())


def test_identity_schema_and_path_digests_are_validated_without_meta_reads() -> None:
    """Only known marker data is accepted; code/meta hash verification is unclaimed."""
    replay.validate_identity(identity())
    for field, value in [
        ("format", True),
        ("tool", "3.9"),
        ("metadata_sha256", {}),
        ("identities", {"../outside.py": {}}),
    ]:
        data = json.loads(identity())
        data[field] = value
        with pytest.raises(ValueError, match="Identity"):
            replay.validate_identity(json.dumps(data).encode())


def test_stats_git_commit_and_schema_fail_closed() -> None:
    """A different/missing producer identity cannot be called historical replay."""
    data = json.loads(stats({"tests/a.py::a": 1}))
    data["git_commit"] = "a" * 40
    with pytest.raises(ValueError, match="schema/source"):
        replay.stats(json.dumps(data).encode())
    del data["git_commit"]
    with pytest.raises(ValueError, match="schema/source"):
        replay.stats(json.dumps(data).encode())


def test_oversize_and_invalid_utf8_are_rejected() -> None:
    """Bound decoding before a potentially large parser allocation."""
    with pytest.raises(ValueError, match="Oversize"):
        replay.parse_object(b" " * (replay.MAX_BYTES + 1))
    with pytest.raises(UnicodeDecodeError):
        replay.parse_object(b"\xff")


def test_unapproved_filename_never_opens_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restored .py and .meta files cannot be selected by the reader."""

    def forbidden(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("file must not be opened")

    monkeypatch.setattr(replay.os, "open", forbidden)
    with pytest.raises(ValueError, match="Unapproved"):
        replay.read_owned_json(Path.cwd(), "src/verdiclip/a.py")


@pytest.mark.parametrize(
    ("mode", "size"),
    [(stat.S_IFIFO, 1), (stat.S_IFDIR, 1), (stat.S_IFREG, replay.MAX_BYTES + 1)],
)
def test_nonregular_or_oversize_rejected_before_open(
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    size: int,
) -> None:
    """A FIFO cannot block the reader before fstat; no actual special file created."""
    root = Path.cwd() / "synthetic-cache-that-is-not-created"
    target = root / "mutmut-stats.json"
    original_dir, original_lstat = Path.is_dir, Path.lstat

    def is_dir(path: Path) -> bool:
        return True if path == root else original_dir(path)

    def lstat(path: Path) -> object:
        if path == target:
            return SimpleNamespace(st_mode=mode, st_size=size)
        return original_lstat(path)

    def forbidden(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("nonregular data must not be opened")

    monkeypatch.setattr(Path, "is_dir", is_dir)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(replay.os, "open", forbidden)
    with pytest.raises(ValueError, match="nonregular/oversize"):
        replay.read_owned_json(root, "mutmut-stats.json")


def test_symlink_component_rejected_without_creation_or_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock only the path observation; never create local native links."""
    root = Path.cwd() / "synthetic-cache"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == root or original(path))
    with pytest.raises(ValueError, match="symlink component"):
        replay.read_owned_json(root, "mutmut-stats.json")
