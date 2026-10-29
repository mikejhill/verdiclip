"""One hosted Windows synthetic Qt test, with owned-child timeout supervision."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from abc import abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import BinaryIO, Literal, Protocol, cast

TARGET = (
    "tests/ux/test_app_journeys.py::TestAfterCapture::"
    "test_hotkey_opens_focused_editor_with_select_tool"
)
SEED = "2725389588"
TIMEOUT = 60
RETIRE_WAIT = 5
OUTPUT_PER_STREAM = 512 * 1024
CHUNK = 8192
STREAM_COUNT = 2
type InstrumentationMode = Literal["phases", "metadata-only"]


def validated_mode(value: str) -> InstrumentationMode:
    """Reject unknown instrumentation before any child can be created."""
    if type(value) is not str or value not in {"phases", "metadata-only"}:
        raise ValueError("Unknown editor instrumentation mode")
    return value


def admit(platform: str, environment: Mapping[str, str]) -> InstrumentationMode:
    """Gate before creating a child; no local Qt execution is admitted."""
    if (
        platform != "win32"
        or environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("RUNNER_OS") != "Windows"
        or environment.get("QT_QPA_PLATFORM") not in {"windows", "offscreen"}
    ):
        raise RuntimeError("Requires standard hosted Windows and admitted QPA")
    return validated_mode(environment.get("EDITOR_PROFILE_MODE", "phases"))


def command(executable: str, mode: InstrumentationMode = "phases") -> list[str]:
    """Run the exact single unchanged target; no warmup or broader selection."""
    validated_mode(mode)
    coverage = (
        ["--cov", "--cov-fail-under=0", "--cov-report=xml", "--cov-report=term"]
        if mode == "phases"
        else ["--no-cov"]
    )
    return [
        executable,
        "-m",
        "pytest",
        TARGET,
        "-p",
        "retained_editor_latency_profile",
        f"--randomly-seed={SEED}",
        *coverage,
        "--junitxml=junit.xml",
        "--ux-snapshots=docs/ux/snapshots",
        "-vv",
        "--capture=tee-sys",
    ]


class Process(Protocol):
    """Only the exact Popen-owned child object is managed."""

    @property
    @abstractmethod
    def stdout(self) -> BinaryIO | None:
        """Return the owned stdout pipe."""

    @property
    @abstractmethod
    def stderr(self) -> BinaryIO | None:
        """Return the owned stderr pipe."""

    @abstractmethod
    def wait(self, timeout: int) -> int:
        """Wait a bounded number of seconds."""

    @abstractmethod
    def poll(self) -> int | None:
        """Read completion for this same owned child."""

    @abstractmethod
    def terminate(self) -> None:
        """Terminate only this owned child."""

    @abstractmethod
    def kill(self) -> None:
        """Escalate only this owned child."""


class ReaderPort(Protocol):
    """Bounded byte-drain result; no child output is executed."""

    data: bytearray
    truncated: bool
    failed: bool

    @abstractmethod
    def start(self) -> None:
        """Start only after the owner has retained the reader object."""

    @abstractmethod
    def finish(self) -> bool:
        """Return whether the owned reader has stopped."""


class Reader:
    """Drain pipes continuously but retain at most one fixed-cap byte prefix."""

    def __init__(self, stream: BinaryIO) -> None:
        """Only the real hosted entry invokes this thread adapter."""
        self.stream = stream
        self.data = bytearray()
        self.truncated = False
        self.failed = True
        self.worker = threading.Thread(target=self._drain, daemon=True)

    def start(self) -> None:
        """Start the already-owned reader thread."""
        self.worker.start()

    def _drain(self) -> None:
        try:
            while chunk := self.stream.read(CHUNK):
                available = max(0, OUTPUT_PER_STREAM - len(self.data))
                self.data.extend(chunk[:available])
                if len(chunk) > available:
                    self.truncated = True
            self.failed = False  # Only clean EOF can establish complete output.
        except Exception:  # noqa: BLE001 - bounded reader failure is explicit in outcome
            self.failed = True

    def finish(self) -> bool:
        """Retain uncertainty if EOF/reader retirement does not complete."""
        if self.worker.is_alive():
            self.worker.join(timeout=1)
        return not self.worker.is_alive()


@dataclass
class Outcome:
    """Original child status and explicit cleanup/evidence limits."""

    original_exit: int | None
    timed_out: bool
    owned_process_gone: bool
    readers_gone: bool
    output_complete: bool
    errors: list[str]
    stdout: bytes
    stderr: bytes

    @property
    def exit_code(self) -> int:
        """Timeout/unknown cleanup cannot become a successful measurement."""
        if not self.owned_process_gone or not self.readers_gone:
            return 125
        if self.timed_out:
            return 124
        if self.original_exit is not None and self.original_exit != 0:
            return self.original_exit
        return 1 if self.errors or not self.output_complete else 0


@dataclass
class Retirement:
    """Independent owned-resource cleanup and explicit secondary failures."""

    errors: list[str] = field(default_factory=list)
    controls: list[BaseException] = field(default_factory=list)

    def note(self, stage: str, error: BaseException) -> None:
        """Retain fixed stages and preserve opaque genuine controls."""
        self.errors.append(stage)
        if (
            not isinstance(error, Exception) or isinstance(error, BaseExceptionGroup)
        ) and not any(error is previous for previous in self.controls):
            self.controls.append(error)

    def attempt(self, stage: str, operation: Callable[[], object]) -> bool:
        """One cleanup failure must not prevent independent next actions."""
        try:
            operation()
        except BaseException as error:  # noqa: BLE001 - controls propagate after owned cleanup
            self.note(stage, error)
            return False
        return True

    def gone(self, process: Process) -> bool:
        """Require direct completion evidence for the same owned child."""
        try:
            return process.poll() is not None
        except BaseException as error:  # noqa: BLE001 - unknown completion never qualifies
            self.note("poll", error)
            return False

    def process(self, process: Process) -> bool:
        """Escalate only this Popen object, with separate bounded waits."""
        if self.gone(process):
            return True
        for name, request in (("terminate", process.terminate), ("kill", process.kill)):
            self.attempt(name, request)
            self.attempt(name + "_wait", lambda: process.wait(timeout=RETIRE_WAIT))
            if self.gone(process):
                return True
        return False

    def readers(self, process: Process, readers: list[ReaderPort]) -> bool:
        """Bound joins; never acquire an active buffered reader's close lock."""
        gone = True
        for reader in readers:
            try:
                gone = reader.finish() and gone
            except BaseException as error:  # noqa: BLE001 - independent reader cleanup continues
                gone = False
                self.note("reader_retirement", error)
        for stream in (process.stdout, process.stderr):
            if stream is None:
                continue
            if gone:
                self.attempt("pipe_close", stream.close)
            else:
                self.errors.append("pipe_close_skipped_active_reader")
        return gone

    def propagate(self) -> None:
        """Raise controls outside the resource lifetime, preserving originals."""
        if len(self.controls) == 1:
            raise self.controls[0]
        if self.controls:
            raise BaseExceptionGroup("Owned runner controls", self.controls)


def start_readers(
    process: Process,
    factory: Callable[[BinaryIO], ReaderPort],
    readers: list[ReaderPort],
) -> None:
    """Keep partially started reader ownership visible to final cleanup."""
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Owned child pipes missing")
    for stream in (process.stdout, process.stderr):
        reader = factory(stream)
        readers.append(reader)
        reader.start()


def supervise(
    process: Process, reader_factory: Callable[[BinaryIO], ReaderPort] = Reader
) -> Outcome:
    """Bound work wait, then independently retire exact owned resources."""
    readers: list[ReaderPort] = []
    retirement = Retirement()
    original: int | None = None
    timed_out = False
    try:
        start_readers(process, reader_factory, readers)
        original = process.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
    except BaseException as error:  # noqa: BLE001 - controls propagate after owned cleanup
        retirement.note("work_wait", error)
    finally:
        gone = retirement.process(process)
        readers_gone = retirement.readers(process, readers)
    retirement.propagate()
    complete = len(readers) == STREAM_COUNT and not any(
        r.failed or r.truncated for r in readers
    )
    return Outcome(
        original,
        timed_out,
        gone,
        readers_gone,
        complete,
        retirement.errors,
        bytes(readers[0].data) if readers else b"",
        bytes(readers[1].data) if len(readers) == STREAM_COUNT else b"",
    )


def main() -> int:
    """Real hosted-only process entry; never invoked by local pure tests."""
    mode = admit(sys.platform, os.environ)
    process = subprocess.Popen(  # noqa: S603 - fixed exact owned test command
        command(sys.executable, mode),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    outcome = supervise(cast("Process", process))
    sys.stdout.write(outcome.stdout.decode("utf-8", errors="replace"))
    sys.stderr.write(outcome.stderr.decode("utf-8", errors="replace"))
    print(  # noqa: T201 - requested bounded hosted receipt
        json.dumps(
            {
                "editor_backend_runner": {
                    "original_exit": outcome.original_exit,
                    "instrumentation_mode": mode,
                    "timed_out": outcome.timed_out,
                    "owned_process_gone": outcome.owned_process_gone,
                    "readers_gone": outcome.readers_gone,
                    "output_complete": outcome.output_complete,
                    "errors": outcome.errors,
                    "exit_code": outcome.exit_code,
                    "descendant_cleanup_proved": False,
                }
            }
        ),
        flush=True,
    )
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
