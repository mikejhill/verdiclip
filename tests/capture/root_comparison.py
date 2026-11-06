"""One hosted-only read-only root lookup; no product qualification or input."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import cast

from PySide6.QtCore import QPoint, qVersion
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession
from tests.capture.hosted_fixture_input import require_hosted
from tests.capture.production_matrix import (
    StrictFixtureSnapshot,
    fixture_foreground_errors,
)
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.capture.scroll.uia_diagnostics import WorkerDiagnostics
from verdiclip.capture.scroll.uia_messages import UiaAction, UiaRequest
from verdiclip.capture.scroll.uia_process import UiaProcess
from verdiclip.geometry import Rect

RESULT_KEYS = {
    "client",
    "apartment",
    "identity_verified",
    "foreground_unchanged",
    "native_only_verified",
    "release_confirmed",
    "dpi_restored",
    "durations_us",
    "runtime",
    "release_kind",
}
DURATION_KEYS = {"construct", "root", "validate", "release"}
SOURCE_FILES = {
    "helper": "tests/capture/uia_root_probe.ps1",
    "native_probe": "tests/capture/uia_root_probe_native.cs",
    "interop": "src/verdiclip/capture/scroll/uia_nofocus_interop.cs",
    "desktop": "src/verdiclip/capture/scroll/uia_desktop.cs",
    "winforms_fixture": "tests/capture/provider_winforms_fixture.ps1",
    "wpf_fixture": "tests/capture/provider_fixture.ps1",
    "harness": "tests/capture/root_comparison.py",
}


def require(condition: bool) -> None:  # noqa: FBT001 - validation boundary
    """Reject with constant text instead of exporting object values or raw IDs."""
    if not condition:
        raise ValueError("Root comparison precondition or result failed")


def decode_result(value: object, client: str) -> dict[str, object]:
    """Allow only fixed labels, booleans and bounded timings into public receipts."""
    require(isinstance(value, dict))
    result = cast(dict[str, object], value)
    require(set(result) == RESULT_KEYS)
    require(result["client"] == client and result["apartment"] == "MTA")
    for key in (
        "identity_verified",
        "foreground_unchanged",
        "release_confirmed",
        "dpi_restored",
    ):
        require(result[key] is True)
    require(result["native_only_verified"] is (client == "native"))
    require(
        result["release_kind"]
        == (
            "native_rcw_release_completed"
            if client == "native"
            else "managed_reference_dropped"
        )
    )
    durations = result["durations_us"]
    require(isinstance(durations, dict))
    duration_map = cast(dict[str, object], durations)
    require(set(duration_map) == DURATION_KEYS)
    for duration in duration_map.values():
        require(
            duration is None or (type(duration) is int and 0 <= duration <= 999999999)
        )
    runtime = result["runtime"]
    require(isinstance(runtime, dict))
    runtime_map = cast(dict[str, object], runtime)
    require(set(runtime_map) == {"powershell", "clr", "architecture"})
    require(runtime_map["architecture"] == "x64")
    for key in ("powershell", "clr"):
        version = runtime_map[key]
        require(isinstance(version, str))
        require(
            re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,8}){1,3}", cast(str, version))
            is not None
        )
    return result


def require_probe_environment() -> tuple[str, str]:
    """The new selector narrows, rather than replaces, existing hosted gates."""
    require_hosted(os.environ)
    require(os.environ.get("VERDICLIP_QUALIFICATION_DIRTY") == "false")
    require(os.environ.get("VERDICLIP_ROOT_PROBE") == "1")
    require(os.environ.get("GITHUB_HEAD_REF") == "diagnose/uia-root-comparison")
    client = os.environ.get("VERDICLIP_ROOT_CLIENT", "")
    provider = os.environ.get("VERDICLIP_NATIVE_PROVIDER", "")
    require(client in {"managed", "native"} and provider in {"winforms", "wpf"})
    require(qVersion() == "6.12.0" and os.environ.get("QT_SCALE_FACTOR") == "1")
    session = DesktopSession.current()
    require(session is not None and session.state == 0 and session.flags == 1)
    return client, provider


def source_manifest() -> dict[str, object]:
    """Export hashes and runtime versions, never repository or fixture paths."""
    executable = shutil.which("git")
    require(executable is not None)
    head = subprocess.check_output(  # noqa: S603 - fixed read-only command
        [cast(str, executable), "rev-parse", "HEAD"], text=True
    ).strip()
    tree = subprocess.check_output(  # noqa: S603 - fixed read-only command
        [cast(str, executable), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    windows_build = platform.version()
    require(re.fullmatch(r"[0-9.]{1,32}", windows_build) is not None)
    require(head == os.environ.get("VERDICLIP_HOSTED_SOURCE"))
    require(re.fullmatch(r"[a-f0-9]{40}", tree) is not None)
    return {
        "head": head,
        "tree": tree,
        "python": platform.python_version(),
        "windows_build": windows_build,
        "qt": qVersion(),
        "architecture": "x64",
        "qt_scale": 1,
        "source_hashes": {
            key: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for key, path in SOURCE_FILES.items()
        },
    }


class RootComparison:
    """A fresh fixture, popup and helper with exactly one read-only request."""

    def __init__(
        self, qtbot: QtBot, directory: Path, client: str, provider: str
    ) -> None:
        self.qtbot, self.directory, self.client = qtbot, directory, client
        self.peer = ScrollPeer(directory / "provider", provider=provider)
        self.menu = QMenu("Owned root comparison popup")
        self.snapshot: StrictFixtureSnapshot | None = None
        self.process: UiaProcess | None = None
        self.done = False
        self.cleanup_failures: list[str] = []
        self.result: dict[str, object] | None = None
        self.stage = "admission"
        self.receipt: dict[str, object] = {
            "schema": 1,
            "client": client,
            "provider": provider,
            "outcome": "failed",
            "failure_stage": "admission",
            "result": None,
            "diagnostics": None,
            "helper_retired": False,
            "fixture_retired": False,
            "popup_preserved": False,
            "fixture_ready_ms": None,
            "retirement_failed": False,
            "budgets_ms": {
                "fixture": 20000,
                "startup": 8000,
                "operation": 3000,
                "cleanup": 2000,
            },
        }
        self.deadline = time.monotonic() + 50

    def run(self) -> None:
        """Leave every failure terminal; cleanup and receipt writing happen outside."""
        self.receipt["source"] = source_manifest()
        self.stage = "fixture"
        started = time.monotonic()
        try:
            self.peer.start()
            remaining = int((20 - (time.monotonic() - started)) * 1000)
            require(remaining > 0)
            self.qtbot.waitUntil(self.peer.poll, timeout=remaining)
        finally:
            self.receipt["fixture_ready_ms"] = round(
                (time.monotonic() - started) * 1000
            )
        self.stage = "popup"
        self.prepare_popup()
        self.stage = "helper"
        self.inspect_once()
        require(self.result is not None)
        require(self.peer.poll())
        require(self.snapshot is not None and not self.snapshot.errors())
        self.receipt["popup_preserved"] = True
        self.receipt["result"] = self.result
        self.receipt["outcome"] = "passed"
        self.receipt["failure_stage"] = None

    def prepare_popup(self) -> None:
        """Match the owned popup placement without driver or cursor preparation."""
        raw = self.peer.state["physical_rectangle"]
        require(isinstance(raw, dict) and set(raw) == {"x", "y", "width", "height"})
        rectangle = cast(dict[str, int], raw)
        require(all(type(value) is int for value in rectangle.values()))
        area = Rect(**rectangle)
        self.menu.addAction("Inert owned item")
        self.menu.popup(QPoint(int(area.right + 80), int(area.y)))
        self.qtbot.waitUntil(self.menu.isVisible)
        require(self.menu.devicePixelRatioF() == 1)
        geometry = self.menu.frameGeometry()
        require(
            not area.intersects(
                Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())
            )
        )
        app = QApplication.instance()
        require(isinstance(app, QApplication))
        self.snapshot = StrictFixtureSnapshot(cast(QApplication, app), (self.menu,))

    def inspect_once(self) -> None:
        """Use the unchanged lease protocol and deadlines, never another attempt."""
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetForegroundWindow.restype = w.HWND
        foreground = int(api.GetForegroundWindow() or 0)
        self.receipt["foreground_owner"] = (
            "fixture" if foreground == self.peer.handle else "popup"
        )
        require(
            not fixture_foreground_errors(
                foreground, (self.peer.handle, int(self.menu.winId()))
            )
        )
        host = (
            Path(os.environ["SYSTEMROOT"])
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        self.process = UiaProcess(
            helper_command=(
                str(host),
                "-NoProfile",
                "-NonInteractive",
                "-Mta",
                "-File",
                str(Path(__file__).with_name("uia_root_probe.ps1")),
                "-Client",
                self.client,
            ),
            qualified=True,
            use_local_pipe=True,
        )
        self.process.completed.connect(self.completed)
        self.process.failed.connect(self.failed)
        remaining = int((self.deadline - time.monotonic()) * 1000)
        require(remaining >= UiaProcess.STARTUP_MS + UiaProcess.DEADLINE_MS)
        self.process.start(
            UiaAction.INSPECT,
            {
                "window_handle": self.peer.handle,
                "process_id": self.peer.integer("pid"),
                "started_ticks": self.peer.integer("started_ticks"),
                "foreground_handle": foreground,
            },
        )
        self.qtbot.waitUntil(lambda: self.done, timeout=min(remaining, 11000))
        require(int(api.GetForegroundWindow() or 0) == foreground)

    def completed(self, _request: UiaRequest, reply: object) -> None:
        """Reject unexpected fields without exposing the untrusted helper object."""
        try:
            require(isinstance(reply, dict))
            self.result = decode_result(
                cast(dict[str, object], reply).get("result"), self.client
            )
        except (ValueError, TypeError, KeyError):
            self.result = None
        finally:
            self.done = True

    def failed(self, _request: UiaRequest, _message: str) -> None:
        """Do not retain free-text errors; bounded worker stages explain failures."""
        self.done = True

    def close(self) -> None:
        """Retire every independently owned resource and retain only safe evidence."""
        if self.receipt["outcome"] != "passed":
            self.receipt["failure_stage"] = self.stage
        if self.process is not None:
            try:
                self.retire_helper()
            except Exception:  # noqa: BLE001 - independently owned fixture must retire
                self.cleanup_failures.append("helper_retirement")
                self.receipt["helper_retired"] = False
        else:
            self.receipt["helper_retired"] = True
        try:
            self.retire_popup()
        except Exception:  # noqa: BLE001 - independently owned fixture must retire
            self.cleanup_failures.append("popup_retirement")
            self.receipt["popup_preserved"] = False
        self.receipt["fixture_started"] = self.peer.process is not None
        try:
            self.peer.close()
            self.receipt["fixture_retired"] = True
        except Exception:  # noqa: BLE001 - no exception/provider text may be exported
            self.receipt["fixture_retired"] = False
            self.cleanup_failures.append("fixture_retirement")
        retired = bool(
            self.receipt["helper_retired"] and self.receipt["fixture_retired"]
        )
        preserved = self.snapshot is None or bool(self.receipt["popup_preserved"])
        if not retired or not preserved or self.cleanup_failures:
            if self.receipt["outcome"] == "passed":
                self.receipt["failure_stage"] = "retirement"
            self.receipt["outcome"] = "failed"
            self.receipt["retirement_failed"] = True
        self.receipt["cleanup_failures"] = self.cleanup_failures
        encoded = json.dumps(self.receipt, sort_keys=True)
        require(len(encoded) <= 16384)
        (self.directory / "root-comparison.json").write_text(encoded, encoding="utf-8")

    def retire_popup(self) -> None:
        """Observation, filter removal and both Qt cleanup calls are independent."""
        if self.snapshot is not None:
            try:
                self.receipt["popup_preserved"] = not self.snapshot.errors()
            except Exception:  # noqa: BLE001 - fixed labels only; continue cleanup
                self.receipt["popup_preserved"] = False
                self.cleanup_failures.append("popup_observation")
            try:
                self.snapshot.close()
            except Exception:  # noqa: BLE001 - filter failure cannot strand menu
                self.receipt["popup_preserved"] = False
                self.cleanup_failures.append("popup_filter")
        try:
            self.menu.close()
        except Exception:  # noqa: BLE001 - still attempt QObject disposal
            self.receipt["popup_preserved"] = False
            self.cleanup_failures.append("popup_close")
        try:
            self.menu.deleteLater()
        except Exception:  # noqa: BLE001 - still retire independently owned fixture
            self.receipt["popup_preserved"] = False
            self.cleanup_failures.append("popup_dispose")

    def retire_helper(self) -> None:
        """Diagnostics and Qt disposal faults cannot prevent fixture retirement."""
        process = cast(UiaProcess, self.process)
        try:
            process.shutdown(timeout_ms=2000)
            trace = process.diagnostics
            self.receipt["helper_retired"] = (
                process.worker_pid is None
                and trace is not None
                and trace.cleanup == "confirmed"
            )
        except Exception:  # noqa: BLE001 - fixed labels only; continue independent cleanup
            self.receipt["helper_retired"] = False
            self.cleanup_failures.append("helper_shutdown")
        try:
            trace = process.diagnostics
            self.receipt["diagnostics"] = (
                asdict(trace) if isinstance(trace, WorkerDiagnostics) else None
            )
        except Exception:  # noqa: BLE001 - diagnostics must never strand the fixture
            self.cleanup_failures.append("helper_diagnostics")
        try:
            process.deleteLater()
        except Exception:  # noqa: BLE001 - attempt all independently owned cleanup
            self.cleanup_failures.append("helper_dispose")
