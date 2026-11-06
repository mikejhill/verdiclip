"""Private bounded pipe for disposable cursor test processes; no raw log artifacts."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from typing import IO, cast
from uuid import uuid4

from PySide6.QtTest import QTest


class Wire:
    """Only the owned stdin/stdout pair carries correlated transient identities."""

    def __init__(self, stream: IO[str], *, stop_on_close: bool = False) -> None:
        self.lines: queue.Queue[str] = queue.Queue(maxsize=64)
        self.stop_on_close = stop_on_close
        self.reader = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.reader.start()

    def _read(self, stream: IO[str]) -> None:
        try:
            for _ in range(64):
                line = stream.readline(8193)
                if not line or len(line) > 8192:
                    self.lines.put_nowait("")
                    return
                self.lines.put_nowait(line)
                if self.stop_on_close:
                    value = json.loads(line)
                    if isinstance(value, dict) and value.get("op") == "close":
                        return
        except (OSError, ValueError, queue.Full):
            # EOF/timeout at the consumer is failure, never a fabricated reply.
            return

    def receive(self, *, timeout: float = 1) -> dict[str, object]:
        """Receive one bounded message while pumping this thread's event queue."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                QTest.qWait(5)
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError("Invalid private receipt")
            return cast("dict[str, object]", value)
        raise TimeoutError("Private receipt deadline exceeded")


def send(stream: IO[str], value: dict[str, object]) -> None:
    """Send one small private message, never a public artifact."""
    stream.write(json.dumps(value) + "\n")
    stream.flush()


_UNCONFIRMED: list[Peer] = []


class Peer:
    """Own exactly one child, with bounded termination on exceptional cleanup."""

    def __init__(self, role: str, mode: str) -> None:
        self.nonce = uuid4().hex
        self.sequence = 0
        self.crashed = False
        self.child = subprocess.Popen(  # noqa: S603 - fixed local test module
            [
                sys.executable,
                "-m",
                "tests.capture.cursor_state_child",
                role,
                mode,
                self.nonce,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        assert self.child.stdout is not None
        self.wire = Wire(self.child.stdout)

    def command(self, op: str) -> int:
        """Issue a monotonically correlated command to this child alone."""
        if self.child.stdin is None or self.child.poll() is not None:
            raise RuntimeError("Owned peer unavailable")
        self.sequence += 1
        send(
            self.child.stdin, {"nonce": self.nonce, "sequence": self.sequence, "op": op}
        )
        return self.sequence

    def receive(self, phase: str, *, timeout: float = 1) -> dict[str, object]:
        """Admit only the expected phase from the correct child scope."""
        value = self.wire.receive(timeout=timeout)
        if value.get("nonce") != self.nonce or value.get("phase") != phase:
            raise ValueError("Private receipt correlation failed")
        return value

    def snapshot(self) -> dict[str, object]:
        """Use the first newly correlated observation, never wait for good state."""
        sequence = self.command("snapshot")
        value = self.receive("snapshot")
        if type(value.get("sequence")) is not int or value["sequence"] != sequence:
            raise ValueError("Private snapshot sequence mismatch")
        return value

    def crash(self) -> None:
        """Terminate only the owned experiment process, bypassing its finally."""
        self.child.kill()
        self.child.wait(timeout=2)
        self.crashed = True

    def close(self) -> None:
        """Never wait on a pipe lock after uncertain exact-child termination."""
        child = self.child
        send_failed = False
        terminated = False
        try:
            terminated = child.poll() is not None
            if not terminated:
                try:
                    self.command("close")
                except (OSError, RuntimeError):
                    send_failed = True
                try:
                    child.wait(timeout=2)
                    terminated = True
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
                    terminated = True
                    raise RuntimeError("Owned peer required forced cleanup") from None
            if send_failed or (child.returncode != 0 and not self.crashed):
                raise RuntimeError("Owned peer cleanup or exit failed")
        finally:
            self._retire_streams(terminated=terminated)

    def _retire_streams(self, *, terminated: bool) -> None:
        if not terminated:
            _UNCONFIRMED.append(self)
            return
        self.wire.reader.join(timeout=1)
        if self.wire.reader.is_alive():
            _UNCONFIRMED.append(self)
            # Preserve any original process/control failure instead of masking it.
            if sys.exception() is None:
                raise RuntimeError("Owned reader retirement unconfirmed")
            return
        for stream in (self.child.stdin, self.child.stdout):
            if stream is not None:
                stream.close()
