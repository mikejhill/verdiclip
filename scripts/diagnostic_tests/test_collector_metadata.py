"""Pure metadata policy controls; no Windows inspection or debugger execution."""

# ruff: noqa: PT009, PT027
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from scripts.collector_metadata import (
    LIMIT,
    collect,
    decode,
    inspect,
    matching_runtime,
    retire,
)


def wire(records: list[dict[str, object]]) -> bytes:
    """Encode bounded synthetic helper records."""
    return b"\n".join(json.dumps(r).encode() for r in records) + b"\n"


def record(stage: str, status: str, value: object) -> dict[str, object]:
    """Construct one fixed-format synthetic record."""
    return {"stage": stage, "status": status, "value": value}


def good() -> list[dict[str, object]]:
    """Create a complete cached-valid metadata sequence."""
    outcomes = [
        ("exists", True),
        ("pe", "x64"),
        ("version", [4, 8, 1234, 0]),
        ("signature", "cached-valid"),
        ("publisher", True),
        ("trust-close", True),
        ("complete", True),
    ]
    return [
        r
        for stage, value in outcomes
        for r in (record(stage, "started", None), record(stage, "ok", value))
    ]


class MetadataTests(unittest.TestCase):
    """No unknown evidence becomes trusted capability or escaped raw content."""

    def test_complete_metadata(self) -> None:
        """Complete metadata."""
        result = decode(wire(good()))
        self.assertTrue(result["schema_valid"])
        self.assertEqual(result["last_stage"], "complete")

    def test_timeout_preserves_started_stage(self) -> None:
        """Timeout preserves started stage."""
        result = decode(wire(good()[:7]))
        self.assertEqual(result["last_stage"], "signature")
        self.assertTrue(result["schema_valid"])

    def test_unknown_signature_is_retained(self) -> None:
        """Unknown signature is retained."""
        rows = [*good()[:7], record("signature", "unknown", "cache-unavailable")]
        self.assertEqual(
            cast(list[dict[str, object]], decode(wire(rows))["records"])[-1], rows[-1]
        )

    def test_untrusted_payload_never_escapes(self) -> None:
        """Untrusted payload never escapes."""
        for value in ("secret/path", {"secret": 3}, [True, 0, 0, 0], [65536, 0, 0, 0]):
            with self.subTest(value=value):
                result = decode(
                    wire(
                        [
                            record("version", "started", None),
                            record("version", "ok", value),
                        ]
                    )
                )
                self.assertFalse(result["schema_valid"])
                self.assertNotIn(
                    value,
                    [
                        r["value"]
                        for r in cast(list[dict[str, object]], result["records"])
                    ],
                )

    def test_schema_order_duplicate_and_size(self) -> None:
        """Schema order duplicate and size."""
        samples = [
            b'{"stage":"pe","stage":"version","status":"started","value":null}',
            wire([record("pe", "ok", "x64")]),
            wire(good()[:2] + good()[:2]),
            wire([{"path": "secret"}]),
            b"\xff",
            b"x" * (LIMIT + 1),
        ]
        for sample in samples:
            self.assertFalse(decode(sample)["schema_valid"])

    def test_version_match_is_only_candidate(self) -> None:
        """Version match is only candidate."""
        item = {
            **decode(wire(good())),
            "execution": "completed",
            "exit_code": 0,
            "retired": True,
        }
        tools = dict.fromkeys(("clr", "sos", "mscordacwks"), item)
        self.assertEqual(matching_runtime(tools), "equal-file-version-candidate")
        for key, value in (
            ("exit_code", 4),
            ("execution", "timeout"),
            ("retired", False),
            ("schema_valid", False),
        ):
            self.assertEqual(
                matching_runtime({**tools, "clr": {**item, key: value}}), "unverified"
            )

    def test_no_signature_never_matches(self) -> None:
        """No signature never matches."""
        item = {
            **decode(wire(good()[:6])),
            "execution": "completed",
            "exit_code": 0,
            "retired": True,
        }
        self.assertEqual(
            matching_runtime(dict.fromkeys(("clr", "sos", "mscordacwks"), item)),
            "unverified",
        )

    def test_kill_failure_still_reaps(self) -> None:
        """Kill failure still reaps."""
        process = Mock()
        process.poll.return_value = None
        process.kill.side_effect = OSError()
        self.assertTrue(retire(process))
        process.wait.assert_called_once_with(timeout=2)

    def test_control_during_kill_still_reaps(self) -> None:
        """Control during kill still reaps."""
        process = Mock()
        process.poll.return_value = None
        process.kill.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            retire(process)
        process.wait.assert_called_once_with(timeout=2)

    def test_unreaped_is_not_confirmed(self) -> None:
        """Unreaped is not confirmed."""
        process = Mock()
        process.poll.return_value = 0
        process.wait.side_effect = subprocess.TimeoutExpired("owned", 2)
        self.assertFalse(retire(process))
        process.kill.assert_not_called()

    def test_timeout_is_not_late_success(self) -> None:
        """Timeout is not late success."""
        process = Mock()
        process.poll.return_value = None
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("scripts.collector_metadata.subprocess.Popen", return_value=process),
            patch("scripts.collector_metadata.time.monotonic", side_effect=[0, 11]),
        ):
            result = inspect(Path("helper.exe"), "clr", Path(directory))
        self.assertEqual(result["execution"], "timeout")
        self.assertIsNone(result["exit_code"])
        self.assertTrue(result["retired"])
        process.kill.assert_called_once()

    def test_arbitrary_target_refused_before_spawn(self) -> None:
        """Arbitrary target refused before spawn."""
        with (
            patch("scripts.collector_metadata.subprocess.Popen") as spawn,
            self.assertRaises(ValueError),
        ):
            inspect(Path("helper.exe"), "arbitrary", Path())
        spawn.assert_not_called()

    def test_unconfirmed_retirement_stops_following_targets(self) -> None:
        """A surviving helper never permits another probe."""
        with patch(
            "scripts.collector_metadata.inspect", return_value={"retired": False}
        ) as probe:
            result = collect(Path("helper.exe"))
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(result["failure_stage"], "retirement")
        self.assertFalse(result["cleanup_confirmed"])

    def test_private_cleanup_failure_is_sanitized(self) -> None:
        """Failed file deletion cannot become a clean receipt or leak a path."""
        temporary = Mock(name="temporary")
        temporary.name = "private-root"
        temporary.cleanup.side_effect = OSError("private-sensitive-path")
        with (
            patch(
                "scripts.collector_metadata.tempfile.TemporaryDirectory",
                return_value=temporary,
            ),
            patch(
                "scripts.collector_metadata.inspect", return_value={"retired": False}
            ),
        ):
            result = collect(Path("helper.exe"))
        self.assertFalse(result["private_files_removed"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(result["failure_stage"], "private-cleanup")
        self.assertNotIn("private-sensitive-path", json.dumps(result))

    def test_open_failure_is_sanitized_and_no_further_probe(self) -> None:
        """Ordinary filesystem errors preserve only fixed failure categories."""
        with patch(
            "scripts.collector_metadata.inspect", side_effect=OSError("private-path")
        ) as probe:
            result = collect(Path("helper.exe"))
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(result["failure_stage"], "inspect")
        self.assertNotIn("private-path", json.dumps(result))
        self.assertTrue(result["private_files_removed"])

    def test_control_is_not_swallowed(self) -> None:
        """Control signals escape after the temporary directory is retired."""
        temporary = Mock(name="temporary")
        temporary.name = "private-root"
        with (
            patch(
                "scripts.collector_metadata.tempfile.TemporaryDirectory",
                return_value=temporary,
            ),
            patch(
                "scripts.collector_metadata.inspect", side_effect=KeyboardInterrupt()
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            collect(Path("helper.exe"))
        temporary.cleanup.assert_called_once()

    def test_native_source_keeps_offline_and_fixed_scope(self) -> None:
        """Required trust flags and narrow target list remain explicit."""
        source = Path("scripts/collector_metadata.cpp").read_text()
        self.assertIn(
            "WTD_CACHE_ONLY_URL_RETRIEVAL | WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT",
            source,
        )
        self.assertIn("WTD_REVOKE_WHOLECHAIN", source)
        for forbidden in (
            "WTD_REVOKE_NONE",
            "LoadLibrary",
            "DebugActiveProcess",
            "MiniDump",
            "CreateProcess",
            "OpenProcess",
        ):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("std::strcmp(argv[1],"), 4)


if __name__ == "__main__":
    unittest.main()
