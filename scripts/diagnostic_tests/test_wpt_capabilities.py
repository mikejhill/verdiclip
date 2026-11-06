"""Pure capability reduction controls; no Windows tools or capture."""

# unittest keeps this inspection independent of package downloads.
# ruff: noqa: PT009, PT027
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.wpt_capabilities import COMMANDS, private_help, summarize


class CapabilityTests(unittest.TestCase):
    """Untrusted help is reduced to fixed booleans, never raw output."""

    def test_fixed_read_only_commands(self) -> None:
        """Only help verbs are admitted."""
        self.assertEqual(
            COMMANDS,
            {
                "xperf": ("-help", "stack"),
                "wpaexporter": ("-h",),
                "wpr": ("-help", "profiles"),
            },
        )

    def test_unknown_help_never_escapes(self) -> None:
        """Private strings never become receipt values."""
        result = summarize(
            "private-content secret-path -pid -tid -range -event", "xperf"
        )
        self.assertTrue(result["advertises_pid"])
        self.assertTrue(all(type(value) is bool for value in result.values()))
        self.assertNotIn("private-content", str(result))

    def test_missing_help_is_not_export_proof(self) -> None:
        """Absent output cannot imply capability."""
        self.assertFalse(any(summarize(None, "xperf").values()))

    def test_profile_switch_is_advertisement_only(self) -> None:
        """A help switch is not a verified export schema."""
        result = summarize("-profile -symbols", "wpaexporter")
        self.assertTrue(result["advertises_export"])
        self.assertTrue(result["advertises_symbols"])
        self.assertNotIn("export_schema_verified", result)

    def test_deadline_retires_exact_owned_process(self) -> None:
        """A stalled help invocation is killed and reaped without pipe threads."""
        process = Mock()
        process.poll.return_value = None
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("scripts.wpt_capabilities.subprocess.Popen", return_value=process),
            patch("scripts.wpt_capabilities.time.monotonic", side_effect=[0, 11]),
        ):
            self.assertIsNone(
                private_help(["owned-tool", "-help"], Path(folder), "test")
            )
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)

    def test_failed_kill_still_attempts_reap(self) -> None:
        """Independent owned cleanup proceeds even after kill reports an error."""
        process = Mock()
        process.poll.return_value = None
        process.kill.side_effect = OSError("owned failure")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("scripts.wpt_capabilities.subprocess.Popen", return_value=process),
            patch("scripts.wpt_capabilities.time.monotonic", side_effect=[0, 11]),
            self.assertRaises(OSError),
        ):
            private_help(["owned-tool", "-help"], Path(folder), "test")
        process.wait.assert_called_once_with(timeout=2)


if __name__ == "__main__":
    unittest.main()
