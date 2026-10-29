"""Mocked process and byte-reader ports only; no child or Qt execution."""

from __future__ import annotations

import io
import subprocess
from collections.abc import Mapping
from typing import BinaryIO, cast, override

import pytest
from scripts.retained_editor_backend_runner import (
    OUTPUT_PER_STREAM,
    SEED,
    TARGET,
    InstrumentationMode,
    Outcome,
    Reader,
    admit,
    command,
    supervise,
)


class FakeProcess:
    """Only scripted waits determine the fake owned process lifetime."""

    def __init__(self, waits: list[int | BaseException]) -> None:
        """No Popen, OS query, thread or handle is constructed."""
        self.waits = waits
        self.code: int | None = None
        self.stdout = io.BytesIO(b"stdout")
        self.stderr = io.BytesIO(b"stderr")
        self.calls: list[str | int] = []

    def wait(self, timeout: int) -> int:
        """Follow the predetermined completion/error sequence."""
        self.calls.append(timeout)
        next_wait = self.waits.pop(0)
        if isinstance(next_wait, BaseException):
            raise next_wait
        self.code = next_wait
        return next_wait

    def poll(self) -> int | None:
        """Return only this fake child's known state."""
        return self.code

    def terminate(self) -> None:
        """Record exact-object retirement, without changing process state."""
        self.calls.append("terminate")

    def kill(self) -> None:
        """Record exact-object escalation only."""
        self.calls.append("kill")


class FakeReader:
    """Read inert BytesIO data synchronously; never create a real thread."""

    def __init__(self, stream: BinaryIO) -> None:
        """Emulate the same retained-prefix cap."""
        data = stream.read()
        self.data = bytearray(data[:OUTPUT_PER_STREAM])
        self.truncated = len(data) > OUTPUT_PER_STREAM
        self.failed = False

    def finish(self) -> bool:
        """Pure readers always finish unless a test explicitly overrides it."""
        return True

    def start(self) -> None:
        """No fake reader needs a real thread or OS operation."""


def timeout(seconds: int) -> subprocess.TimeoutExpired:
    """Create a pure diagnostic timeout object; no command is executed."""
    return subprocess.TimeoutExpired("fake-owned-child", seconds)


@pytest.mark.parametrize("status", [0, 1, 3])
def test_original_completed_exit_is_preserved(status: int) -> None:
    """Normal child success/failure remains unchanged with complete cleanup."""
    process = FakeProcess([status])
    result = supervise(process, FakeReader)
    assert result.exit_code == status
    assert result.original_exit == status
    assert result.owned_process_gone
    assert result.readers_gone
    assert result.stdout == b"stdout"
    assert result.stderr == b"stderr"
    assert process.calls == [60]


def test_timeout_terminates_only_owned_process_and_never_passes() -> None:
    """A child that exits after timeout remains a timed-out measurement."""
    process = FakeProcess([timeout(60), 0])
    result = supervise(process, FakeReader)
    assert process.calls == [60, "terminate", 5]
    assert result.timed_out
    assert result.owned_process_gone
    assert result.exit_code == 124


def test_timeout_escalates_to_exact_owned_kill_with_final_wait() -> None:
    """Terminate timeout consumes only its bounded reserve before owned kill."""
    process = FakeProcess([timeout(60), timeout(5), -9])
    result = supervise(process, FakeReader)
    assert process.calls == [60, "terminate", 5, "kill", 5]
    assert result.owned_process_gone
    assert result.exit_code == 124


def test_uncertain_final_wait_is_not_cleanup_confirmation() -> None:
    """No runner disposal or kill request substitutes for actual child exit."""
    process = FakeProcess([timeout(60), timeout(5), timeout(5)])
    result = supervise(process, FakeReader)
    assert not result.owned_process_gone
    assert result.exit_code == 125
    assert "kill_wait" in result.errors


def test_original_control_propagates_after_owned_child_cleanup() -> None:
    """Keyboard interruption cannot skip independent owned retirement."""
    original = KeyboardInterrupt("wait control")
    process = FakeProcess([original, -15])
    with pytest.raises(KeyboardInterrupt) as caught:
        supervise(process, FakeReader)
    assert caught.value is original
    assert process.code == -15
    assert process.calls == [60, "terminate", 5]


def test_output_truncation_is_explicit_and_invalidates_zero_exit() -> None:
    """Bounded output does not silently count missing evidence as a pass."""
    process = FakeProcess([0])
    process.stdout = io.BytesIO(b"x" * (OUTPUT_PER_STREAM + 1))
    result = supervise(process, FakeReader)
    assert len(result.stdout) == OUTPUT_PER_STREAM
    assert not result.output_complete
    assert result.exit_code == 1


def test_reader_start_failure_still_retires_owned_child() -> None:
    """Failure after process creation cannot orphan a waiting child."""
    process = FakeProcess([-15])

    def broken(_stream: BinaryIO) -> FakeReader:
        raise RuntimeError("reader setup")

    result = supervise(process, broken)
    assert process.calls == ["terminate", 5]
    assert result.owned_process_gone
    assert result.exit_code == 1
    assert "work_wait" in result.errors


def test_unretired_reader_does_not_trigger_a_blocking_pipe_close() -> None:
    """An active pipe reader is uncertain; never acquire its buffered IO lock."""

    class Active(FakeReader):
        @override
        def finish(self) -> bool:
            return False

    process = FakeProcess([0])
    result = supervise(process, Active)
    assert not result.readers_gone
    assert not process.stdout.closed
    assert result.exit_code == 125


def test_command_selects_only_exact_target_with_explicit_original_flags() -> None:
    """The process recipe cannot widen tests, warm up or alter the threshold."""
    args = command("reviewed-python.exe")
    assert args[:4] == ["reviewed-python.exe", "-m", "pytest", TARGET]
    assert args.count(TARGET) == 1
    assert f"--randomly-seed={SEED}" in args
    assert "--cov" in args
    assert "--cov-fail-under=0" in args
    assert "--ux-snapshots=docs/ux/snapshots" in args
    assert "retained_editor_latency_profile" in args


def test_default_phase_command_preserves_exact_previous_argument_order() -> None:
    """Default mode retains the already-qualified coverage/profiling recipe."""
    assert command("reviewed-python.exe") == [
        "reviewed-python.exe",
        "-m",
        "pytest",
        TARGET,
        "-p",
        "retained_editor_latency_profile",
        f"--randomly-seed={SEED}",
        "--cov",
        "--cov-fail-under=0",
        "--cov-report=xml",
        "--cov-report=term",
        "--junitxml=junit.xml",
        "--ux-snapshots=docs/ux/snapshots",
        "-vv",
        "--capture=tee-sys",
    ]


def test_metadata_only_command_disables_coverage_but_keeps_exact_target() -> None:
    """The control removes coverage overhead without altering the original case."""
    args = command("reviewed-python.exe", "metadata-only")
    assert args[:4] == ["reviewed-python.exe", "-m", "pytest", TARGET]
    assert "--no-cov" in args
    assert not any(argument.startswith("--cov") for argument in args)
    assert "retained_editor_latency_profile" in args
    assert f"--randomly-seed={SEED}" in args
    assert "--ux-snapshots=docs/ux/snapshots" in args


def test_mode_admission_defaults_and_rejects_unknown_before_child_recipe() -> None:
    """Only validated mode values reach the hosted command construction."""
    facts = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_OS": "Windows",
        "QT_QPA_PLATFORM": "offscreen",
    }
    assert admit("win32", facts) == "phases"
    assert (
        admit("win32", {**facts, "EDITOR_PROFILE_MODE": "metadata-only"})
        == "metadata-only"
    )
    with pytest.raises(ValueError, match="Unknown editor instrumentation"):
        admit("win32", {**facts, "EDITOR_PROFILE_MODE": "unknown"})
    with pytest.raises(ValueError, match="Unknown editor instrumentation"):
        command(
            "reviewed-python.exe",
            cast("InstrumentationMode", "".join(("un", "known"))),
        )


@pytest.mark.parametrize("qpa", ["windows", "offscreen"])
def test_pure_host_guard_admits_only_explicit_hosted_windows_qpa(qpa: str) -> None:
    """Fake environment values never trigger the actual process entry."""
    facts: Mapping[str, str] = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_OS": "Windows",
        "QT_QPA_PLATFORM": qpa,
    }
    admit("win32", facts)
    with pytest.raises(RuntimeError, match="hosted Windows"):
        admit("linux", facts)
    with pytest.raises(RuntimeError, match="hosted Windows"):
        admit("win32", {**facts, "QT_QPA_PLATFORM": "unsupported"})


def test_cleanup_uncertainty_takes_priority_over_original_success() -> None:
    """Receipt-derived status cannot conceal a still-owned process."""
    outcome = Outcome(
        original_exit=0,
        timed_out=False,
        owned_process_gone=False,
        readers_gone=True,
        output_complete=True,
        errors=[],
        stdout=b"",
        stderr=b"",
    )
    assert outcome.exit_code == 125


def test_real_drain_marks_unexpected_control_incomplete_without_starting_thread() -> (
    None
):
    """A reader exiting before EOF cannot manufacture complete success."""
    original = GeneratorExit("reader control")

    class BrokenStream:
        def read(self, _size: int) -> bytes:
            """Raise a genuine control from an inert fake stream."""
            raise original

    reader = Reader.__new__(Reader)  # No constructor, thread or process is invoked.
    reader.stream = cast("BinaryIO", BrokenStream())
    reader.data = bytearray()
    reader.truncated = False
    reader.failed = True
    with pytest.raises(GeneratorExit) as caught:
        reader._drain()
    assert caught.value is original
    assert reader.failed


def test_real_drain_requires_clean_eof_and_caps_prefix_without_thread() -> None:
    """The actual drain algorithm is exercised against inert BytesIO only."""
    reader = Reader.__new__(Reader)
    reader.stream = io.BytesIO(b"x" * (OUTPUT_PER_STREAM + 1))
    reader.data = bytearray()
    reader.truncated = False
    reader.failed = True
    reader._drain()
    assert not reader.failed
    assert reader.truncated
    assert len(reader.data) == OUTPUT_PER_STREAM
