"""Test-only owned process receipts; completion markers never override exit status."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import IO

_PHASE = re.compile(
    r"^ACTIVATION_PHASE=(script-started|before-decision|after-decision|assertions-complete)"
    r" script_ms=(-?\d{1,9}) parent_wall_ms=(-?\d{1,12})$"
)


def optional[T](action: Callable[[], T], fallback: T) -> T:
    """Guard evidence only, never child execution or the original verdict."""
    try:
        return action()
    except Exception:  # noqa: BLE001 - ordinary diagnostic failures only
        return fallback


def wall_start_ms() -> int:
    """Zero explicitly means parent wall timing was unavailable."""
    return optional(lambda: time.time_ns() // 1_000_000, 0)


def phase_receipts(stderr: str | bytes | None) -> list[dict[str, object]]:
    """Keep at most four known markers, discarding arbitrary child output."""
    text = (
        stderr.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes)
        else stderr or ""
    )
    receipts: dict[str, dict[str, object]] = {}
    for line in text.splitlines():
        match = _PHASE.fullmatch(line)
        if match is not None and match[1] not in receipts:
            receipts[match[1]] = {
                "phase": match[1],
                "script_ms": int(match[2]),
                "parent_wall_ms": int(match[3]),
            }
    return list(receipts.values())


def _elapsed(started: float | None) -> int | None:
    if started is None:
        return None
    return optional(lambda: round((time.monotonic() - started) * 1000), None)


def _retire(
    process: subprocess.Popen[str], errors: list[str], *, exited: bool = False
) -> None:
    """Always attempt bounded reaping even when owned-process kill fails."""
    try:
        if not exited:
            try:
                process.kill()
            except Exception:  # noqa: BLE001 - retain ordinary cleanup failure
                errors.append("kill-failed")
    finally:
        try:
            process.wait(timeout=2)
        except Exception:  # noqa: BLE001 - retain ordinary cleanup failure
            errors.append("reap-unconfirmed")


def _close_pipes(process: subprocess.Popen[str], *, interrupted: bool) -> bool:
    """Never acquire Windows reader-thread stream locks after interrupted read.

    Windows communicate uses daemon reader threads. Even an exited direct child
    does not prove inherited pipes reached EOF. Conservatively retain those
    streams rather than close/join them; report cleanup unconfirmed. POSIX
    communicate uses selectors and has no competing reader-thread stream locks.
    """
    if interrupted and sys.platform == "win32":
        return False

    def close(stream: IO[str] | None) -> bool:
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - retain ordinary cleanup failure
                return False
        return True

    try:
        stdout_closed = close(process.stdout)
    finally:
        stderr_closed = close(process.stderr)
    return stdout_closed and stderr_closed


def run_activation(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Keep 10s execution and a separate 2s cleanup-only cap; no retries.

    Child parent-wall offsets include startup, but may reflect wall adjustments.
    Parent elapsed uses monotonic time. Neither alone proves a shutdown hang.
    """
    started = optional(time.monotonic, None)
    process = subprocess.Popen(  # noqa: S603 - closed repository test command
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    cleanup_state: dict[str, bool] = {"interrupted": False, "handled": False}
    try:
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired as error:
            cleanup_state["interrupted"] = True
            cleanup_state["handled"] = True
            at_timeout = None
            returncode: int | str | None = "unavailable"
            cleanup_errors: list[str] = []
            # A diagnostic control signal must propagate, but cannot skip cleanup.
            try:
                at_timeout = _elapsed(started)
                returncode = optional(process.poll, "unavailable")
            finally:
                try:
                    _retire(process, cleanup_errors, exited=isinstance(returncode, int))
                finally:
                    pipes_closed = _close_pipes(process, interrupted=True)
            optional(
                lambda error=error: error.add_note(
                    str(
                        {
                            "at_timeout_elapsed_ms": at_timeout,
                            "after_cleanup_elapsed_ms": _elapsed(started),
                            "returncode_at_timeout": returncode,
                            "process_state_at_timeout": (
                                "exited"
                                if isinstance(returncode, int)
                                else "running"
                                if returncode is None
                                else "unavailable"
                            ),
                            "retirement_confirmed": process.returncode is not None,
                            "pipe_cleanup_confirmed": pipes_closed,
                            "cleanup_confirmed": process.returncode is not None
                            and pipes_closed,
                            "cleanup_errors": cleanup_errors,
                            "phases": phase_receipts(error.stderr),
                            "wall_offsets_are_not_monotonic": True,
                        }
                    )
                ),
                None,
            )
            raise
        except BaseException:
            cleanup_state["interrupted"] = True
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        # No unconditional wait or reader-thread joins; controls retain context.
        if not cleanup_state.get("handled", False):
            try:
                if cleanup_state["interrupted"]:
                    _retire(process, [])
            finally:
                _close_pipes(process, interrupted=cleanup_state["interrupted"])
