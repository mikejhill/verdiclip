"""Pure capability reduction controls; no Windows tools or capture."""

# unittest keeps this inspection independent of package downloads.
# ruff: noqa: PT009, PT027
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.wpt_capabilities import (
    COMMANDS,
    Outcome,
    completed_help,
    decode_signature,
    inspect_tool,
    private_help,
    summarize,
)


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
            self.assertEqual(
                private_help(["owned-tool", "-help"], Path(folder), "test").status,
                "timeout",
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

    def test_helper_failures_stay_unknown_and_never_execute(self) -> None:
        """Unavailable or malformed helper output cannot authorize help."""
        for status in (
            "timeout",
            "output-limit",
            "nonzero-exit",
            "spawn-error",
            "decode-error",
            "exit-code-invalid",
        ):
            with self.subTest(status=status):
                schema, tools = decode_signature(Outcome(status, 1))
                self.assertEqual(schema, "helper-unavailable")
                with patch("scripts.wpt_capabilities.private_help") as execute:
                    result = inspect_tool(
                        "xperf", Path("unused"), Path("unused"), tools.get("xperf")
                    )
                self.assertIsNone(result["signed_installed_tool"])
                execute.assert_not_called()

    def test_strict_helper_schema(self) -> None:
        """Missing, duplicate, extra and inconsistent fields refuse all tools."""
        good = {
            "schema": 1,
            "status": "complete",
            "tools": {
                name: {
                    "state": "verified",
                    "stage": "complete",
                    "exists": True,
                    "signature": "Valid",
                    "version": "10.0.1.0",
                }
                for name in COMMANDS
            },
        }
        self.assertEqual(
            decode_signature(Outcome("completed", 0, json.dumps(good)))[0], "validated"
        )
        bad_values = [
            None,
            [],
            {},
            {**good, "schema": True},
            {**good, "extra": "private"},
            {**good, "tools": {}},
            {**good, "tools": {"xperf": None}},
        ]
        for field, value in (
            ("exists", "true"),
            ("state", "other"),
            ("signature", []),
            ("version", "private-path"),
            ("stage", []),
        ):
            bad = json.loads(json.dumps(good))
            bad["tools"]["xperf"][field] = value
            bad_values.append(bad)
        for bad in bad_values:
            with self.subTest(bad=bad):
                self.assertEqual(
                    decode_signature(Outcome("completed", 0, json.dumps(bad))),
                    ("schema-invalid", {}),
                )
        for bad in ("not-json", '{"schema":1,"schema":1}'):
            self.assertEqual(
                decode_signature(Outcome("completed", 0, bad)), ("json-invalid", {})
            )

    def test_missing_refused_and_probe_error_are_distinct(self) -> None:
        """All refused/unknown states block execution and preserve the fixed reason."""
        values = [
            {
                "state": "missing",
                "stage": "exists",
                "exists": False,
                "signature": None,
                "version": None,
            },
            {
                "state": "signature-refused",
                "stage": "signature",
                "exists": True,
                "signature": "NotSigned",
                "version": None,
            },
            {
                "state": "publisher-refused",
                "stage": "publisher",
                "exists": True,
                "signature": "Valid",
                "version": None,
            },
            {
                "state": "probe-error",
                "stage": "signature",
                "exists": True,
                "signature": None,
                "version": None,
            },
        ]
        for value in values:
            with (
                self.subTest(state=value["state"]),
                patch("scripts.wpt_capabilities.private_help") as execute,
            ):
                result = inspect_tool("xperf", Path("unused"), Path("unused"), value)
                self.assertEqual(result["state"], value["state"])
                execute.assert_not_called()
                self.assertIs(
                    result["signed_installed_tool"],
                    None if value["state"] == "probe-error" else False,
                )

    def test_bounded_exit_and_error_classifier_never_emit_stderr(self) -> None:
        """Only fixed categories and bounded numeric exit codes escape."""
        with tempfile.TemporaryDirectory() as folder:
            output, errors = Path(folder) / "out", Path(folder) / "err"
            output.write_bytes(b"private-output")
            errors.write_bytes(b"private secret ParserError path")
            outcome = completed_help(1, output, errors)
            self.assertEqual(
                outcome.public(),
                {
                    "status": "nonzero-exit",
                    "exit_code": 1,
                    "error_category": "ParserError",
                },
            )
            self.assertIsNone(outcome.text)
            self.assertEqual(
                completed_help(2**40, output, errors).status, "exit-code-invalid"
            )
            output.write_bytes(b"\xff")
            self.assertEqual(completed_help(0, output, errors).status, "decode-error")

    def test_path_refusal_prevents_help(self) -> None:
        """A valid signature cannot bypass changed/linked path ancestry."""
        info = {
            "state": "verified",
            "stage": "complete",
            "exists": True,
            "signature": "Valid",
            "version": "1.2.3.4",
        }
        for response in (False, OSError("private path")):
            with (
                patch(
                    "scripts.wpt_capabilities.no_links",
                    side_effect=response if isinstance(response, OSError) else None,
                    return_value=False,
                ),
                patch("scripts.wpt_capabilities.private_help") as execute,
            ):
                result = inspect_tool("xperf", Path("unused"), Path("unused"), info)
                self.assertEqual(result["help"], {"status": "not-run-path"})
                execute.assert_not_called()

    def test_actual_spawn_and_output_limit_paths(self) -> None:
        """Report execution failure stages rather than inventing tool absence."""
        with (
            tempfile.TemporaryDirectory() as folder,
            patch(
                "scripts.wpt_capabilities.subprocess.Popen",
                side_effect=OSError("private"),
            ),
        ):
            self.assertEqual(
                private_help(["owned"], Path(folder), "spawn").status, "spawn-error"
            )
        process = Mock()
        process.poll.return_value = 0
        process.returncode = 0
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("scripts.wpt_capabilities.subprocess.Popen", return_value=process),
            patch("scripts.wpt_capabilities.LIMIT", -1),
        ):
            self.assertEqual(
                private_help(["owned"], Path(folder), "limit").status, "output-limit"
            )
        process.wait.assert_called_once_with(timeout=2)

    def test_control_signal_is_preserved_after_cleanup(self) -> None:
        """Process controls still propagate while owned cleanup is attempted."""
        process = Mock()
        signal = KeyboardInterrupt()
        process.poll.side_effect = [signal, None]
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("scripts.wpt_capabilities.subprocess.Popen", return_value=process),
            self.assertRaises(KeyboardInterrupt) as caught,
        ):
            private_help(["owned"], Path(folder), "signal")
        self.assertIs(caught.exception, signal)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)

    def test_bootstrap_and_completed_help_outcomes(self) -> None:
        """Explicit helper bootstrap failure differs from a successful empty help."""
        self.assertEqual(
            decode_signature(
                Outcome(
                    "completed",
                    0,
                    '{"schema":1,"status":"bootstrap-error","tools":null}',
                )
            ),
            ("bootstrap-error", {}),
        )
        with tempfile.TemporaryDirectory() as folder:
            output, errors = Path(folder) / "out", Path(folder) / "err"
            output.write_text("-pid -range", encoding="utf-8")
            errors.write_text("private", encoding="utf-8")
            outcome = completed_help(0, output, errors)
            self.assertEqual(outcome.status, "completed")
            self.assertNotIn("-pid", str(outcome.public()))
            self.assertTrue(summarize(outcome.text, "xperf")["advertises_pid"])


if __name__ == "__main__":
    unittest.main()
