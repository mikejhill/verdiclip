"""Exact-owned child and immutable first-observation acceptance, without repair."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import cast
from uuid import uuid4

from tests.capture.owned_cursor_child import PHASES, validate_result
from tests.capture.owned_cursor_observer import FirstRetirement, Snapshot, observe
from tests.capture.owned_cursor_runtime import build_launch_plan, verify_runtime


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate keys before accepting private protocol metadata."""
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate-child-metadata")
        value[key] = item
    return value


class Child:
    """One disposable Popen owner; private files avoid buffered pipe cleanup locks."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.nonce = uuid4().hex
        self.process: subprocess.Popen[bytes] | None = None
        self.first: FirstRetirement | None = None
        self.runtime_attested = False

    def read(self, name: str) -> dict[str, object] | None:
        """Accept bounded nonce-correlated metadata from this exact private scope."""
        path = self.directory / name
        if not path.exists():
            return None
        if path.is_symlink():
            raise ValueError("invalid-child-metadata")
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("invalid-child-metadata")
        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict) or value.get("nonce") != self.nonce:
            raise ValueError("unrelated-child-metadata")
        if name == "ready.json":
            valid = (
                set(value) == {"nonce", "ready", "pid", "runtime"}
                and value["ready"] is True
            )
        elif name == "result.json":
            valid = set(value) == {"nonce", "result"}
            validate_result(value.get("result"))
        else:
            phase = name.removesuffix(".json")
            valid = (
                phase in PHASES
                and set(value) == {"nonce", "phase"}
                and value["phase"] == phase
            )
        if not valid:
            raise ValueError("invalid-child-schema")
        return cast("dict[str, object]", value)

    def launch(self) -> None:
        """Create only the owned child; it creates no surface before start."""
        plan = build_launch_plan()
        self.directory.mkdir()
        with (
            (self.directory / "stdout").open("xb") as stdout,
            (self.directory / "stderr").open("xb") as stderr,
        ):
            self.process = subprocess.Popen(  # noqa: S603 - fixed module and generated private scope
                [
                    plan.executable,
                    "-m",
                    "tests.capture.owned_cursor_child",
                    str(self.directory),
                    self.nonce,
                ],
                env=plan.environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        ready = self.await_file("ready.json", 5)
        verify_runtime(ready, plan.expected, self.process.pid)
        self.runtime_attested = True

    def await_file(self, name: str, seconds: float) -> dict[str, object]:
        """Wait for a protocol boundary, never for a favorable cursor observation."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = self.read(name)
            if value is not None:
                return value
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("component-child-exited-before-boundary")
            time.sleep(0.005)
        raise TimeoutError("component-boundary-timeout")

    def begin(self, baseline: Snapshot) -> None:
        """Record the supervisor baseline before allowing component surface creation."""
        if not self.runtime_attested:
            raise RuntimeError("child-runtime-not-attested")
        self.first = FirstRetirement(baseline)
        (self.directory / "start").touch(exist_ok=False)

    def admitted(self) -> Snapshot:
        """One logical sample after admission; unsupported is not retried or passed."""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.read("admitted.json") is not None:
                return observe()
            message = self.read("result.json")
            if message is not None:
                result = validate_result(message.get("result"))
                if (
                    result["cleanup_confirmed"] is True
                    and result["thread_retired"] is True
                    and result["hwnd_destroyed"] is True
                    and self.first is not None
                ):
                    self.first.record(observe)
                    if self.process is not None:
                        self.process.wait(timeout=2)
                raise RuntimeError("component-not-admitted:" + str(result["reason"]))
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("component-exited-before-admission")
            time.sleep(0.005)
        raise TimeoutError("component-admission-boundary-timeout")

    def finish(self, *, cancel: bool = False) -> dict[str, bool | str]:
        """First sample follows joined owner result, before any later wait/recovery."""
        if cancel:
            (self.directory / "cancel").touch(exist_ok=False)
        message = self.await_file("result.json", 5)
        result = validate_result(message.get("result"))
        if self.first is None:
            raise RuntimeError("missing-supervisor-baseline")
        assert result["hwnd_destroyed"] is True
        assert result["thread_retired"] is True
        assert result["cleanup_confirmed"] is True
        self.first.record(observe)
        assert self.process is not None
        exit_code = self.process.wait(timeout=2)
        assert exit_code == 0
        return result

    def require_active(self) -> None:
        """Reject stale admission after retirement; no retry can rehabilitate it."""
        if (
            self.process is None
            or self.process.poll() is not None
            or self.read("retired.json") is not None
            or self.read("result.json") is not None
        ):
            raise RuntimeError("unsupported: component already retired")

    def crash(self) -> None:
        """Request same-owner abrupt death; only exact unbalanced entry qualifies."""
        if self.process is None or self.first is None:
            raise RuntimeError("owned-crash-target-unavailable")
        self.require_active()
        (self.directory / "crash").touch(exist_ok=False)
        exit_code = self.process.wait(timeout=2)
        if exit_code != 97 or self.read("crash-entry.json") is None:
            raise RuntimeError("unsupported: no proven unbalanced abrupt exit")
        self.first.record(observe)

    def close(self) -> None:
        """Retire only this child on every failure; forced cleanup stays a failure."""
        child = self.process
        if child is None or child.poll() is not None:
            return
        try:
            child.kill()
        finally:
            child.wait(timeout=2)
        raise RuntimeError("component-required-forced-cleanup")
