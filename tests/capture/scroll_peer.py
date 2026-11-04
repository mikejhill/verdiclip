"""Existing WPF provider fixture, reused by the native broker interaction test."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from verdiclip.geometry import Rect


class ScrollPeer:
    """Own one explicit child; never send pointer or keyboard input."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.nonce = uuid4().hex
        self.process: subprocess.Popen[bytes] | None = None
        self.state: dict[str, object] = {}
        self.identity: tuple[object, ...] | None = None

    def start(self) -> None:
        """Called only inside the existing native-handoff test gate."""
        self.directory.mkdir(parents=True)
        executable = (
            Path(os.environ["SYSTEMROOT"])
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        with (
            (self.directory / "fixture-stdout.log").open("wb") as stdout,
            (self.directory / "fixture-stderr.log").open("wb") as stderr,
        ):
            self.process = subprocess.Popen(  # noqa: S603 - fixed owned script, no shell
                [
                    str(executable),
                    "-NoProfile",
                    "-NonInteractive",
                    "-STA",
                    "-File",
                    str(Path(__file__).with_name("provider_fixture.ps1")),
                    "-Directory",
                    str(self.directory),
                    "-Nonce",
                    self.nonce,
                    "-Case",
                    "end",
                    "-ExecuteAfterHandoff",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=stdout,
                stderr=stderr,
            )

    def poll(self) -> bool:
        """Accept only a fresh immutable snapshot from the exact owned process."""
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Owned WPF provider exited")
        paths = sorted(self.directory.glob("fixture-state-*.json"))
        if not paths:
            return False
        path = paths[-1]
        match = re.fullmatch(r"fixture-state-([0-9]{6})\.json", path.name)
        if match is None or path.is_symlink() or not 1 <= int(match[1]) <= 10000:
            raise ValueError("Invalid fixture snapshot name")
        if (
            not 0 <= time.time() - path.stat().st_mtime <= 2
            or path.stat().st_size > 65536
        ):
            raise ValueError("Stale or oversized fixture snapshot")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if (
            value["nonce"] != self.nonce
            or value["pid"] != self.process.pid
            or value["sequence"] != int(match[1])
            or not value["visible"]
            or value["closed"]
        ):
            raise ValueError("Fixture snapshot ownership changed")
        identity = (value["pid"], value["started_ticks"], value["hwnd"])
        if self.identity is not None and self.identity != identity:
            raise ValueError("Fixture process/window generation changed")
        self.identity, self.state = identity, value
        return True

    @property
    def handle(self) -> int:
        """Validated native owner for the supported input driver."""
        return self.integer("hwnd")

    def target(self) -> Rect:
        """Only the real WPF scrollbar center is a permitted input target."""
        return Rect(
            self.integer("scrollbar_x") - 1, self.integer("scrollbar_y") - 1, 2, 2
        )

    def integer(self, name: str) -> int:
        """Reject coercions in owned identity and physical-coordinate fields."""
        value = self.state[name]
        if type(value) is not int:
            raise ValueError("Noninteger fixture field")
        return value

    def close(self) -> None:
        """Stop and verify only this child; forced exit never counts as clean."""
        if self.process is None:
            return
        failure: OSError | None = None
        try:
            (self.directory / "fixture-stop.json").write_text("{}", encoding="utf-8")
        except OSError as error:
            failure = error
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
            raise AssertionError(
                "Owned provider required forced termination"
            ) from failure
        if failure is not None:
            raise failure
        receipt = json.loads((self.directory / "fixture-retired.json").read_text())
        assert receipt == {"nonce": self.nonce, "pid": self.process.pid, "closed": True}
        assert self.process.returncode == 0
