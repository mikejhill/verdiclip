"""Exact owned baseline child, with first observation before normal child exit."""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path
from typing import cast
from uuid import uuid4

from tests.capture.native_pointer_crash import Wire, receive, runtime, send
from tests.capture.released_pointer_child import child


def reply(pipe: object, deadline: float, nonce: str) -> dict[str, object]:
    """Surface only a strictly allowlisted child failure category."""
    value = receive(cast("Wire", pipe), deadline)
    if value.get("phase") == "failed":
        stages = {"source", "start", "admission", "action", "retirement", "finish"}
        reasons = {"timeout", "invalid-proof", "unavailable", "unknown"}
        if (
            set(value) != {"phase", "nonce", "stage", "reason"}
            or value["nonce"] != nonce
            or type(value["stage"]) is not str
            or value["stage"] not in stages
            or type(value["reason"]) is not str
            or value["reason"] not in reasons
        ):
            raise RuntimeError("baseline-failure-reply-invalid")
        raise RuntimeError(f"baseline-child-{value['stage']}-{value['reason']}")
    return value


class BaselinePeer:
    """One fixture child and one command, never a retry-until-restored loop."""

    def __init__(self, root: Path, mode: str, point: tuple[int, int]) -> None:
        if mode not in {"cancel", "crash"}:
            raise ValueError("baseline-mode-invalid")
        self.mode = mode
        self.nonce = uuid4().hex
        context = multiprocessing.get_context("spawn")
        self.pipe, self.child_pipe = context.Pipe(duplex=True)
        self.process = context.Process(
            target=child,
            args=(self.child_pipe, self.nonce, str(root), mode, point),
        )
        self.handles: tuple[int, ...] = ()
        self.foreground_owned = False
        self.started = False

    def start(self) -> None:
        """Reject an unexpected runtime/PID/source before any baseline window exists."""
        self.process.start()
        self.started = True
        self.child_pipe.close()
        value = reply(self.pipe, time.monotonic() + 5, self.nonce)
        expected = {
            "phase": "ready",
            "nonce": self.nonce,
            "pid": self.process.pid,
            "runtime": runtime(),
            "baseline_verified": True,
        }
        if value != expected:
            raise RuntimeError("baseline-attestation-failed")
        send(self.pipe, {"phase": "start", "nonce": self.nonce})

    def act(self) -> None:
        """Require real active pointer proof and the matching terminal boundary."""
        deadline = time.monotonic() + 5
        value = reply(self.pipe, deadline, self.nonce)
        handles = value.get("handles")
        if (
            set(value) != {"phase", "nonce", "handles", "foreground_owned"}
            or value["phase"] != "admitted"
            or value["nonce"] != self.nonce
            or type(value["foreground_owned"]) is not bool
            or not isinstance(handles, list)
            or not 1 <= len(handles) <= 16
            or any(type(item) is not int or item <= 0 for item in handles)
        ):
            raise RuntimeError("baseline-active-proof-unavailable")
        self.handles = tuple(cast("list[int]", handles))
        self.foreground_owned = value["foreground_owned"]
        send(self.pipe, {"phase": "action", "nonce": self.nonce})
        phase = "retired" if self.mode == "cancel" else "crash-entry"
        if reply(self.pipe, deadline, self.nonce) != {
            "phase": phase,
            "nonce": self.nonce,
        }:
            raise RuntimeError("baseline-terminal-proof-unavailable")
        if self.mode == "crash":
            self.process.join(max(0, deadline - time.monotonic()))
            if self.process.is_alive() or self.process.exitcode != 97:
                raise RuntimeError("baseline-crash-exit-unconfirmed")
        elif not self.process.is_alive():
            raise RuntimeError("baseline-normal-exited-before-observation")

    def finish(self) -> None:
        """Normal exit occurs only after the parent's immutable first observation."""
        if self.mode == "cancel":
            send(self.pipe, {"phase": "finish", "nonce": self.nonce})
            self.process.join(2)
            if self.process.is_alive() or self.process.exitcode != 0:
                raise RuntimeError("baseline-normal-exit-unconfirmed")

    def close(self) -> None:
        """Independently retire the retained process and both owned endpoints."""
        try:
            if self.started or self.process.pid is not None:
                try:
                    if self.process.is_alive():
                        self.process.terminate()
                finally:
                    self.process.join(2)
                if self.process.is_alive():
                    raise RuntimeError("baseline-cleanup-unconfirmed")
                self.process.close()
        finally:
            try:
                self.pipe.close()
            finally:
                self.child_pipe.close()
