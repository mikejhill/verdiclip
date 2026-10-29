"""Bounded observer of a test-owned external native menu; no input injection."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import TextIO
from uuid import uuid4

from verdiclip.geometry import Rect


@dataclass(frozen=True, slots=True)
class MenuState:
    """Only ownership/state/geometry metadata crosses the fixture pipe."""

    owner: int
    menu_owner: int
    flags: int
    visible: bool
    foreground: int
    bounds: Rect
    observed_at: float
    sequence: int = 0

    def errors(self, expected_owner: int, now: float) -> list[str]:
        """A live menu handle alone does not establish an open popup loop."""
        errors: list[str] = []
        if self.owner != expected_owner or self.menu_owner != expected_owner:
            errors.append("External menu owner changed")
        if self.flags & 0x14 != 0x14 or not self.visible:
            errors.append("External native popup is no longer open")
        if self.foreground != expected_owner:
            errors.append("External fixture foreground changed")
        if now - self.observed_at > 1 or now < self.observed_at:
            errors.append("External menu observation is stale")
        if self.bounds.is_empty:
            errors.append("External fixture geometry is empty")
        return errors


class MenuPeer:
    """Start no child until the native test's explicit handoff flag is checked."""

    def __init__(self) -> None:
        self.nonce = uuid4().hex
        self.process: subprocess.Popen[str] | None = None
        self.state: MenuState | None = None
        self.owner = 0
        self.error = ""
        self._reader: threading.Thread | None = None
        self._sequence = 0

    def start(self, *, handoff: bool) -> None:
        """The sole child creates its own owner/menu and has a 45s watchdog."""
        if not handoff or sys.platform != "win32" or self.process is not None:
            raise ValueError("External menu needs a Windows handoff and a fresh scope")
        self.process = subprocess.Popen(  # noqa: S603 - fixed module/interpreter, generated nonce
            [
                sys.executable,
                "-m",
                "tests.capture.native_menu_peer",
                "--execute-after-handoff",
                "--nonce",
                self.nonce,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert self.process.stdout is not None
        self._reader = threading.Thread(
            target=self._read, args=(self.process.stdout,), daemon=True
        )
        self._reader.start()

    def _read(self, stream: TextIO) -> None:
        try:
            while line := stream.readline(4097):
                state = decode_menu_state(line, self.nonce, time.monotonic())
                if not self.owner:
                    self.owner = state.owner
                self.state = state
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.error = str(error)

    def poll(self) -> int | None:
        """Request a bounded metadata reply; never change the menu's state."""
        child = self.process
        if child is None or child.poll() is not None or child.stdin is None:
            self.error = "External fixture is not available for a new observation"
            return None
        try:
            sequence = self._sequence + 1
            child.stdin.write(
                json.dumps({"op": "snapshot", "sequence": sequence}) + "\n"
            )
            child.stdin.flush()
        except OSError as error:
            self.error = str(error)
            return None
        else:
            self._sequence = sequence
            return sequence

    def preservation_errors(self) -> list[str]:
        """Both a current open-popup observation and the same owner are required."""
        if self.error:
            return [self.error]
        if self.process is not None and self.process.poll() is not None:
            return ["External fixture exited before preservation was confirmed"]
        if self.state is None:
            return ["No external menu observation received"]
        return self.state.errors(self.owner, time.monotonic())

    def acknowledged(self, sequence: int) -> bool:
        """Only a reply to a post-terminal request proves final preservation."""
        return (
            self.process is not None
            and self.process.poll() is None
            and self.state is not None
            and self._sequence >= self.state.sequence >= sequence > 0
        )

    def close(self) -> None:
        """After assertions, close/terminate only the Popen child owned here."""
        child = self.process
        if child is None:
            return
        forced = False
        close_error: OSError | None = None
        if child.poll() is None:
            close_error = _request_owned_close(child)
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                forced = True
                _terminate_owned(child)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
        if self._reader is not None:
            self._reader.join(timeout=1)
        if close_error is not None:
            raise OSError("External fixture cleanup request failed") from close_error
        if forced or child.returncode != 0:
            raise OSError("External fixture required termination or exited abnormally")


def _request_owned_close(child: subprocess.Popen[str]) -> OSError | None:
    """Retain control-pipe failures while still awaiting/retiring the owned child."""
    if child.stdin is None:
        return OSError("External fixture has no cleanup control pipe")
    try:
        child.stdin.write("close-owned\n")
        child.stdin.flush()
    except OSError as error:
        return error
    return None


def _terminate_owned(child: subprocess.Popen[str]) -> None:
    """Bound escalation to the exact Popen child, retaining failure status."""
    child.terminate()
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=2)


def decode_menu_state(line: str, nonce: str, now: float) -> MenuState:
    """Fail closed on malformed, stale-scope or nonphysical fixture metadata."""
    if len(line) > 4096 or not line.endswith("\n"):
        raise ValueError("External menu reply exceeds its frame bound")
    value = json.loads(line)
    if (
        not isinstance(value, dict)
        or value.get("nonce") != nonce
        or value.get("valid") is not True
    ):
        raise ValueError("External menu reply is invalid or unrelated")
    rect = value.get("rect")
    if (
        not isinstance(rect, list)
        or len(rect) != 4
        or not all(type(v) is int and -(2**31) <= v < 2**31 for v in rect)
    ):
        raise ValueError("External menu geometry is invalid")
    if not all(
        type(value.get(k)) is int and 0 <= value[k] < 2**64
        for k in ("owner", "menu_owner", "flags", "foreground", "sequence")
    ):
        raise ValueError("External menu identity is invalid")
    if (
        value["owner"] < 1
        or type(value.get("visible")) is not bool
        or rect[2] <= rect[0]
        or rect[3] <= rect[1]
    ):
        raise ValueError("External menu state is invalid")
    return MenuState(
        value["owner"],
        value["menu_owner"],
        value["flags"],
        value["visible"],
        value["foreground"],
        Rect(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]),
        now,
        value["sequence"],
    )
