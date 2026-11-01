"""Private-CI data replay, requiring stable exclusively owned directories.

Never import restored code or mutmut. This is not a hostile-writer filesystem
API: path observations plus atomic replacement do not form compare-and-swap.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

SOURCE_SHA = "5fe14b25b651c02caea31786e44e9508595e4e19"
MAX_BYTES = 16 * 1024 * 1024
MAX_TESTS = 10000
MAX_NODE_LENGTH = 4096
DIGEST_LENGTH = 64
STATS_FIELDS = {
    "tests_by_mangled_function_name",
    "duration_by_test",
    "stats_time",
    "function_hashes",
    "function_dependencies",
    "config_fingerprint",
    "watched_file_hashes",
    "git_commit",
}
FILES = ("mutmut-stats.json", ".verdict-context.json", ".identity-provenance.json")
type Durations = dict[str, int | float]


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(_value: str) -> object:
    raise ValueError("Nonfinite JSON constant")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON numeric value")
    return number


def parse_object(raw: bytes) -> dict[str, object]:
    """Decode bounded UTF-8 JSON without duplicate keys or nonfinite numbers."""
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError("Oversize or non-byte JSON input")
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=_constant,
        parse_float=_float,
    )
    if type(value) is not dict:
        raise ValueError("JSON root must be an object")
    return cast("dict[str,object]", value)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sha(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == DIGEST_LENGTH
        and all(c in "0123456789abcdef" for c in value)
    )


def _relative_source(name: object, suffix: str) -> bool:
    if type(name) is not str or not name.endswith(suffix) or "\\" in name:
        return False
    path = PurePosixPath(name)
    return (
        str(path) == name
        and not path.is_absolute()
        and all(part not in {".", ".."} for part in path.parts)
    )


def validate_context(raw: bytes) -> None:
    """Require exact source/platform; this marker does not identify the run/map."""
    data = parse_object(raw)
    expected = {
        "schema": 1,
        "policy": "same-commit-os-arch-v1",
        "source_sha": SOURCE_SHA,
        "os": "Linux",
        "arch": "X64",
    }
    if type(data.get("schema")) is not int or data != expected:
        raise ValueError("Historical cache context mismatch")


def validate_identity(raw: bytes) -> None:
    """Validate only the real marker schema; do not read cached code or verdicts."""
    data = parse_object(raw)
    if set(data) != {"format", "tool", "identities", "metadata_sha256"}:
        raise ValueError("Identity marker schema mismatch")
    if (
        type(data["format"]) is not int
        or data["format"] != 1
        or data["tool"] != "3.8.0"
    ):
        raise ValueError("Identity marker format/tool mismatch")
    identities, metadata = data["identities"], data["metadata_sha256"]
    if (
        type(identities) is not dict
        or not identities
        or type(metadata) is not dict
        or not metadata
    ):
        raise ValueError("Identity marker maps missing")
    for source, vector in identities.items():
        if not _relative_source(source, ".py") or type(vector) is not dict:
            raise ValueError("Identity source/vector shape invalid")
        if any(
            type(name) is not str or not name or not _sha(digest)
            for name, digest in vector.items()
        ):
            raise ValueError("Identity vector digest invalid")
    for name, digest in metadata.items():
        if not _relative_source(name, ".py.meta") or not _sha(digest):
            raise ValueError("Identity metadata digest invalid")


def durations(value: object) -> Durations:
    """Accept finite nonnegative numeric values; bool is never a duration."""
    if type(value) is not dict or not value or len(value) > MAX_TESTS:
        raise ValueError("Duration map shape/count invalid")
    result: Durations = {}
    for node, number in value.items():
        if (
            type(node) is not str
            or not node
            or len(node) > MAX_NODE_LENGTH
            or any(c in node for c in "\x00\r\n")
        ):
            raise ValueError("Duration node id invalid")
        if type(number) is not int and type(number) is not float:
            raise ValueError("Duration value is not numeric data")
        numeric = number
        if numeric < 0:
            raise ValueError("Duration value is not nonnegative numeric data")
        try:
            finite = math.isfinite(float(numeric))
        except OverflowError as error:
            raise ValueError("Duration overflows the actual sorter") from error
        if not finite:
            raise ValueError("Duration is not finite")
        result[node] = numeric
    return result


def stats(raw: bytes) -> tuple[dict[str, object], Durations]:
    """Require pinned3.8 write schema/source without reusing function mappings."""
    data = parse_object(raw)
    if set(data) != STATS_FIELDS or data["git_commit"] != SOURCE_SHA:
        raise ValueError("Statistics schema/source mismatch")
    for field in STATS_FIELDS - {"duration_by_test", "stats_time", "git_commit"}:
        if type(data[field]) is not dict:
            raise ValueError("Statistics mapping shape invalid")
    return data, durations(data["duration_by_test"])


def _span(text: str) -> tuple[int, int]:
    """Find the unique top-level duration value with JSON token boundaries."""
    decoder = json.JSONDecoder()
    index = text.index("{") + 1
    while True:
        while text[index] in " \t\r\n":
            index += 1
        key, index = decoder.raw_decode(text, index)
        while text[index] in " \t\r\n":
            index += 1
        if text[index] != ":":
            raise ValueError("Ambiguous JSON key boundary")
        index += 1
        while text[index] in " \t\r\n":
            index += 1
        start = index
        _value, end = decoder.raw_decode(text, start)
        if key == "duration_by_test":
            return start, end
        index = end
        while text[index] in " \t\r\n":
            index += 1
        if text[index] != ",":
            raise ValueError("Duration key absent/ambiguous")
        index += 1


@dataclass(frozen=True)
class Replay:
    """Only new owned data and hashes; no historical executable objects."""

    updated: bytes
    receipt: dict[str, object]
    expected_order: tuple[str, ...]


def replay_bytes(
    fresh: bytes, historical: bytes, context: bytes, identity: bytes
) -> Replay:
    """Change only durations after complete fresh statistics, preserving all else.

    Equality is to the fresh COMPLETED stats-recorded node set, not a separate
    collection proof. Exact cache-key/context provenance is not a prior map hash.
    """
    validate_context(context)
    validate_identity(identity)
    fresh_data, fresh_map = stats(fresh)
    _historical_data, historical_map = stats(historical)
    if set(fresh_map) != set(historical_map):
        raise ValueError("Historical/fresh stats-recorded node sets differ")
    text = fresh.decode("utf-8")
    start, end = _span(text)
    replacement = json.dumps(historical_map, separators=(",", ":"), allow_nan=False)
    prefix, suffix = text[:start].encode(), text[end:].encode()
    updated = prefix + replacement.encode() + suffix
    after, after_map = stats(updated)
    fresh_data.pop("duration_by_test")
    after.pop("duration_by_test")
    if after != fresh_data or after_map != historical_map:
        raise ValueError("Non-duration data changed")
    new_start, new_end = _span(updated.decode())
    if (
        updated.decode()[:new_start].encode() != prefix
        or updated.decode()[new_end:].encode() != suffix
    ):
        raise ValueError("Non-duration bytes changed")
    order = tuple(sorted(fresh_map, key=lambda node: float(historical_map[node])))
    receipt = {
        "historical_duration_map_sha256": _digest(historical_map),
        "fresh_duration_map_sha256": _digest(fresh_map),
        "replayed_duration_map_sha256": _digest(after_map),
        "fresh_stats_recorded_node_set_sha256": _digest(sorted(fresh_map)),
        "expected_order_from_fresh_stats_node_order_sha256": _digest(order),
        "non_duration_parsed_sha256": _digest(after),
        "non_duration_bytes_sha256": hashlib.sha256(
            prefix + b"\0" + suffix
        ).hexdigest(),
        "fresh_file_sha256": hashlib.sha256(fresh).hexdigest(),
        "replayed_file_sha256": hashlib.sha256(updated).hexdigest(),
        "node_count": len(order),
        "all_other_data_and_bytes_unchanged": True,
        "independent_collection_set_proved": False,
        "prior_map_hash_available": False,
        "identity_marker_schema_validated_only": True,
        "stable_exclusive_directories_required": True,
        "hostile_concurrent_writer_safety_proved": False,
        "atomic_replace_is_compare_and_swap": False,
    }
    return Replay(updated, receipt, order)


def read_owned_json(root: Path, name: str) -> bytes:
    """Read fixed bounded JSON in stable, exclusively owned private CI paths.

    Observed symlinks/nonregular files are rejected. Directory-component races
    are outside this precondition; O_NOFOLLOW protects only the final open.
    """
    if name not in FILES:
        raise ValueError("Unapproved historical filename")
    if any(ancestor.is_symlink() for ancestor in (root, *root.parents)):
        raise ValueError("Historical path has a symlink component")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Historical cache root invalid")
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError("Historical JSON path redirected")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
        raise ValueError("Historical JSON is nonregular/oversize")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if type(nofollow) is not int or type(nonblock) is not int:
        raise RuntimeError("Data replay requires no-follow file support")
    descriptor = os.open(path, os.O_RDONLY | nofollow | nonblock)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise ValueError("Historical JSON is nonregular/oversize")
        if (metadata.st_dev, metadata.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Historical JSON identity changed while opening")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Historical JSON grew past limit")
    return raw


def install_replay(fresh_root: Path, historical_root: Path) -> Replay:
    """Update only fresh statistics under the private job's single-writer contract.

    Restore must be finished, no restored code may execute, the fresh stats
    child must have exited, and no SDK writer may run during this hook. The
    fresh-byte comparison detects an observed change; it is NOT an atomic CAS
    and does not prove safety against a hostile concurrent writer.
    """
    fresh_path, historical_path = fresh_root.resolve(), historical_root.resolve()
    if fresh_path.is_relative_to(historical_path) or historical_path.is_relative_to(
        fresh_path
    ):
        raise ValueError("Fresh and historical roots overlap")
    fresh = read_owned_json(fresh_root, "mutmut-stats.json")
    historical, context, identity = (
        read_owned_json(historical_root, name) for name in FILES
    )
    replay = replay_bytes(fresh, historical, context, identity)
    temporary = fresh_root / (".duration-replay-" + uuid.uuid4().hex + ".tmp")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if type(nofollow) is not int:
        raise RuntimeError("Data replay requires no-follow file support")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if stream.write(replay.updated) != len(replay.updated):
                raise OSError("Partial replay data write")
            stream.flush()
            os.fsync(stream.fileno())
        if read_owned_json(fresh_root, "mutmut-stats.json") != fresh:
            raise ValueError("Fresh statistics changed during replay")
        temporary.replace(fresh_root / "mutmut-stats.json")
        if read_owned_json(fresh_root, "mutmut-stats.json") != replay.updated:
            raise ValueError("Installed statistics differ from replay plan")
    finally:
        if temporary.exists():
            temporary.unlink()
    return replay
