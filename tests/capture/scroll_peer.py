"""Existing WPF provider fixture, reused by the native broker interaction test."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from verdiclip.geometry import Rect


def unique_snapshot_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Duplicate acknowledgment fields cannot establish ownership or observation."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate snapshot field")
        result[key] = value
    return result


class ScrollPeer:
    """Own one explicit child; never send pointer or keyboard input."""

    def __init__(
        self,
        directory: Path,
        *,
        provider: str = "wpf",
        fixture: str = "scrolling",
        native_pointer_cursor: bool = False,
    ) -> None:
        if provider not in {"wpf", "winforms"}:
            raise ValueError("Unknown owned scrolling provider")
        if fixture not in {"scrolling", "unsupported-scrollbar"} or (
            fixture == "unsupported-scrollbar" and provider != "winforms"
        ):
            raise ValueError("Unsupported owned fixture/provider combination")
        if native_pointer_cursor and provider != "winforms":
            raise ValueError("Static cursor requires the owned WinForms fixture")
        self.native_pointer_cursor = native_pointer_cursor
        self.fixture = fixture
        self.provider = provider
        self.directory = directory
        self.nonce = uuid4().hex
        self.process: subprocess.Popen[bytes] | None = None
        self.state: dict[str, object] = {}
        self.identity: tuple[object, ...] | None = None
        self._forced = False
        self._exit_confirmed = False
        self._closed = False
        self.snapshot_request: str | None = None
        self.snapshot_ack: dict[str, object] | None = None

    def start(self) -> None:
        """Called only inside the existing native-handoff test gate."""
        self.directory.mkdir(parents=True)
        # Prepare deterministic data before the native startup clock. The WPF
        # child must not compile C# merely to populate a synthetic bitmap.
        (self.directory / "fixture-pixels.bgr32").write_bytes(provider_pixels())
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
                    str(
                        Path(__file__).with_name(
                            "provider_fixture.ps1"
                            if self.provider == "wpf"
                            else "provider_winforms_fixture.ps1"
                        )
                    ),
                    "-Directory",
                    str(self.directory),
                    "-Nonce",
                    self.nonce,
                    "-Case",
                    "end",
                    "-ExecuteAfterHandoff",
                    *(["-NativePointerCursor"] if self.native_pointer_cursor else []),
                    *(
                        ["-UnsupportedScrollbar"]
                        if self.fixture == "unsupported-scrollbar"
                        else []
                    ),
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
            or value.get("activation_complete") is not True
            or value.get("fixture_kind", "scrolling") != self.fixture
            or value.get("native_pointer_cursor", False)
            is not self.native_pointer_cursor
        ):
            raise ValueError("Fixture snapshot ownership changed")
        identity = (value["pid"], value["started_ticks"], value["hwnd"])
        if self.identity is not None and self.identity != identity:
            raise ValueError("Fixture process/window generation changed")
        self.identity, self.state = identity, value
        return True

    def request_snapshot(self) -> None:
        """Request one post-terminal observation, without a mutation command."""
        if (
            self.snapshot_request is not None
            or self.identity is None
            or self.process is None
            or self.process.poll() is not None
        ):
            raise RuntimeError("Snapshot requires one live admitted fixture")
        paths = [
            self.directory / name
            for name in (
                "fixture-snapshot-request.partial",
                "fixture-snapshot-request.json",
                "fixture-snapshot-ack.json",
            )
        ]
        if any(path.exists() or path.is_symlink() for path in paths):
            raise ValueError("Preexisting snapshot protocol file")
        self.snapshot_request = uuid4().hex
        payload = {
            "nonce": self.nonce,
            "operation": "snapshot",
            "request_id": self.snapshot_request,
        }
        with paths[0].open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")))
        paths[0].rename(paths[1])

    def poll_snapshot(self) -> bool:
        """Wait for acknowledgment arrival only; never wait for a favorable offset."""
        if (
            self.snapshot_request is None
            or self.process is None
            or self.process.poll() is not None
        ):
            raise RuntimeError("Snapshot owner unavailable")
        if self.snapshot_ack is not None:
            return True
        path = self.directory / "fixture-snapshot-ack.json"
        if path.is_symlink():
            raise ValueError("Linked snapshot acknowledgment")
        if not path.exists():
            return False
        metadata = path.stat()
        if metadata.st_size > 512 or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("Oversized or linked snapshot acknowledgment")
        value = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=unique_snapshot_fields,
        )
        expected = {
            "nonce",
            "request_id",
            "pid",
            "started_ticks",
            "hwnd",
            "offset",
            "end_seen",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Invalid snapshot acknowledgment schema")
        if (
            value["nonce"] != self.nonce
            or value["request_id"] != self.snapshot_request
            or any(
                type(value[key]) is not int for key in ("pid", "started_ticks", "hwnd")
            )
            or (value["pid"], value["started_ticks"], value["hwnd"]) != self.identity
            or value["pid"] != self.process.pid
            or type(value["end_seen"]) is not bool
            or type(value["offset"]) not in (int, float)
            or not math.isfinite(value["offset"])
            or value["offset"] < 0
        ):
            raise ValueError("Snapshot correlation, ownership or observation changed")
        self.snapshot_ack = value
        return True

    def assert_snapshot_restored(self) -> None:
        """A fresh nonzero first observation is a failure, never a retry condition."""
        assert self.snapshot_ack is not None, "Correlated snapshot missing"
        assert self.snapshot_ack["end_seen"] is True
        assert self.snapshot_ack["offset"] == 0, "Fresh fixture offset was not restored"

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
        if self._forced:
            if not self._exit_confirmed:
                self.process.wait(timeout=2)
                self._exit_confirmed = True
            raise AssertionError("Owned provider required forced termination")
        if self._closed:
            return
        failure: OSError | None = None
        try:
            (self.directory / "fixture-stop.json").write_text("{}", encoding="utf-8")
        except OSError as error:
            failure = error
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._forced = True
            self.process.kill()
            self.process.wait(timeout=2)
            self._exit_confirmed = True
            raise AssertionError(
                "Owned provider required forced termination"
            ) from failure
        if failure is not None:
            raise failure
        self._exit_confirmed = True
        receipt = json.loads((self.directory / "fixture-retired.json").read_text())
        expected: dict[str, object] = {
            "nonce": self.nonce,
            "pid": self.process.pid,
            "closed": True,
        }
        if self.native_pointer_cursor:
            expected["cursor_lifetime"] = "process-exit"
        assert isinstance(receipt, dict)
        assert receipt == expected
        assert type(receipt["pid"]) is int
        assert receipt["closed"] is True
        assert self.process.returncode == 0
        self._closed = True


def provider_pixels() -> bytes:
    """Generate fixed WPF Bgr32 input independently of the RGBA output oracle."""
    pixels = bytearray()
    for y in range(1120):
        for x in range(384):
            pixels.extend(
                (
                    (y * 17 + x * 11) % 251,
                    (y * 31 + x * 7 + y // 251) % 253,
                    (y * 43 + x * 19 + y // 127) % 255,
                    255,
                )
            )
    return bytes(pixels)
