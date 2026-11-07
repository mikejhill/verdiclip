"""First native UIA checkpoint on disposable Windows CI, never a release claim."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import mss
from PySide6.QtGui import QImage
from scripts.panel_checkpoint import checkpoint_passed
from scripts.panel_fixture_server import FixtureServer
from scripts.panel_owned_job import OwnedJob
from scripts.panel_probe_oracle import (
    Pixels,
    color_bounds,
    inspect_rows,
    overlap_displacements,
)

ROOT = Path(__file__).resolve().parent.parent
Json = dict[str, Any]
MAX_EVENT_BYTES = 4_000_000
HELPER_DEADLINE_SECONDS = 30
NATIVE_CALL_DEADLINE_SECONDS = 3
OFFSET_PERCENT_TOLERANCE = 0.2


def write(path: Path, value: object) -> None:
    """Write one immutable, human-readable synthetic receipt."""
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)


def snapshots(path: Path) -> list[Json]:
    """Read only closed and published immutable fixture snapshots."""
    return [
        json.loads(item.read_text(encoding="utf-8"))
        for item in sorted(path.glob("*.json"))
    ]


def wait_snapshot(path: Path, job: OwnedJob, process: int) -> Json:
    """Bound fixture startup independently of UIA/provider calls."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        states = snapshots(path)
        if states:
            return states[-1]
        if job.wait(process, 0) is not None:
            raise RuntimeError("Owned fixture exited before its first snapshot")
        time.sleep(0.05)
    raise RuntimeError("Owned fixture startup exceeded ten seconds")


def attest() -> Json:
    """Require disposable CI and exact source bytes before touching the desktop."""
    if not (
        sys.platform == "win32"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    ):
        raise RuntimeError("Only disposable GitHub-hosted Windows CI is allowed")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git unavailable")
    head = subprocess.check_output(  # noqa: S603 - fixed read-only source query
        [git, "rev-parse", "HEAD"], cwd=ROOT, timeout=5, text=True
    ).strip()
    if head != os.environ["QUALIFICATION_HEAD"]:
        raise RuntimeError("Candidate commit mismatch")
    files = [
        *sorted((ROOT / "scripts").glob("panel_*.py")),
        *sorted((ROOT / "scripts/panel_fixture").glob("*")),
        ROOT / ".github/workflows/automatic-panel-feasibility.yml",
    ]
    digests = {}
    for path in files:
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        committed = subprocess.check_output(  # noqa: S603 - fixed source query
            [git, "show", "HEAD:" + relative], cwd=ROOT, timeout=5
        )
        if committed != path.read_bytes():
            raise RuntimeError(f"Source differs from exact head: {relative}")
        digests[relative] = hashlib.sha256(committed).hexdigest()
    return {"head": head, "sha256": digests, "python": sys.version}


class NativeWindow:
    """Read the foreground window only after proving its owned PID."""

    def __init__(self) -> None:
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.user.SetProcessDpiAwarenessContext.restype = ctypes.c_int32
        self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self.user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        self.user.AreDpiAwarenessContextsEqual.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.user.AreDpiAwarenessContextsEqual.restype = ctypes.c_int32
        if not self.user.AreDpiAwarenessContextsEqual(
            self.user.GetThreadDpiAwarenessContext(), ctypes.c_void_p(-4)
        ):
            raise RuntimeError("Supervisor DPI awareness not PMv2")
        self.user.GetForegroundWindow.restype = ctypes.c_void_p
        self.user.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.user.GetWindowThreadProcessId.restype = ctypes.c_uint32
        self.user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.user.GetWindowRect.restype = ctypes.c_int32

    def foreground(self, allowed: list[int]) -> Json:
        """Return physical bounds for an owned foreground window, not its title."""
        hwnd = self.user.GetForegroundWindow()
        pid = ctypes.c_uint32()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            raise RuntimeError("Cannot identify foreground owner")
        if pid.value not in allowed:
            raise RuntimeError("Foreground does not belong to the fixture job")
        rect = (ctypes.c_int32 * 4)()
        if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("Cannot read owned window rectangle")
        return {"root_hwnd": hwnd, "pid": pid.value, "allowed_rect": list(rect)}


def edge_fixture(
    job: OwnedJob, server: FixtureServer, output: Path, scale: str
) -> Json:
    """Load an authored page in a fresh installed-Edge process tree."""
    candidates = [
        Path(os.environ.get(variable, "C:/missing"))
        / "Microsoft/Edge/Application/msedge.exe"
        for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES")
    ]
    edge = next((path for path in candidates if path.is_file()), None)
    if edge is None:
        raise RuntimeError(
            "Installed Microsoft Edge not found; browser proof unavailable"
        )
    _, handle = job.start(
        [
            str(edge),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            f"--user-data-dir={output / 'edge-profile'}",
            "--window-size=900,640",
            "--window-position=40,40",
            f"--force-device-scale-factor={scale}",
            "--app=" + server.url,
        ],
        ROOT,
    )
    state = wait_snapshot(server.output, job, handle)
    native = NativeWindow()
    owner = native.foreground(job.pids())
    left, top, right, bottom = owner["allowed_rect"]
    with mss.MSS() as source:
        shot = source.grab(
            {"left": left, "top": top, "width": right - left, "height": bottom - top}
        )
        # A cyan border exists only around this authored page's inner target.
        x1, y1, x2, y2 = color_bounds(
            Pixels(shot.width, shot.height, shot.rgb), bytes((23, 197, 233))
        )
    border = [left + x1, top + y1, left + x2, top + y2]
    dpr = float(state["dpr"])
    target = state["state"]["target"]
    content_left = border[0] + target["border"][0] * dpr
    content_top = border[1] + target["border"][1] * dpr
    content = [
        content_left,
        content_top,
        content_left + target["viewport_width"] * dpr,
        content_top + target["viewport_height"] * dpr,
    ]
    return {
        **owner,
        "kind": "owned-edge",
        "owned_process_ids": job.pids(),
        "click": [(content[0] + content[2]) / 2, (content[1] + content[3]) / 2],
        "content_rect": content,
        "pixel_border_rect": border,
        "dpr": dpr,
        "state": state["state"],
        "edge_executable": str(edge),
        "browser_user_agent": state["user_agent"],
        "browser_scale_flag": scale,
        "real_monitor_dpi_variant": False,
    }


def events(path: Path) -> list[Json]:
    """Read only complete JSONL records from a bounded helper log."""
    if not path.exists():
        return []
    data = path.read_bytes()
    if len(data) > MAX_EVENT_BYTES:
        raise RuntimeError("Helper event log exceeded four megabytes")
    return [
        json.loads(line)
        for line in data.splitlines(keepends=True)
        if line.endswith(b"\n")
    ]


def run_probe(job: OwnedJob, fixture: Json, output: Path) -> Json:
    """Bound startup, every logged native call, and total helper lifetime."""
    manifest = output / "fixture.json"
    write(manifest, fixture)
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    if not base.is_file():
        raise RuntimeError("Verified direct base interpreter unavailable")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(Path(sys.prefix) / "Lib/site-packages"), str(ROOT / "src")]
    )
    probe_dir = output / "probe"
    pid, handle = job.start(
        [
            str(base),
            "-m",
            "scripts.panel_uia_probe",
            "--x",
            str(round(fixture["click"][0])),
            "--y",
            str(round(fixture["click"][1])),
            "--expected-pid",
            str(fixture["pid"]),
            "--fixture-manifest",
            str(manifest),
            "--output",
            str(probe_dir),
        ],
        ROOT,
    )
    started = time.monotonic()
    while time.monotonic() - started < HELPER_DEADLINE_SECONDS:
        code = job.wait(handle, 0)
        if code is not None:
            return {
                "launch_pid": pid,
                "direct_base": str(base),
                "exit_code": code,
                "seconds": time.monotonic() - started,
                "timed_out": False,
            }
        records = events(probe_dir / "calls.jsonl")
        now = time.monotonic()
        if not records and now - started > NATIVE_CALL_DEADLINE_SECONDS:
            raise RuntimeError("Helper startup exceeded three seconds")
        if (
            records
            and records[-1]["event"] == "call_start"
            and now - records[-1]["monotonic"] > NATIVE_CALL_DEADLINE_SECONDS
        ):
            raise RuntimeError("Native provider call exceeded three seconds")
        if (
            records
            and records[-1]["event"] == "process"
            and now - records[-1]["monotonic"] > NATIVE_CALL_DEADLINE_SECONDS
        ):
            raise RuntimeError("First native provider setup exceeded three seconds")
        time.sleep(0.05)
    raise RuntimeError("Helper total deadline exceeded thirty seconds")


def load_pixels(path: Path) -> Pixels:
    """Decode a synthetic PNG without creating any GUI or capture windows."""
    image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB888)
    if image.isNull():
        raise RuntimeError("Invalid helper PNG")
    raw = bytes(image.constBits())
    rgb = b"".join(
        raw[row * image.bytesPerLine() : row * image.bytesPerLine() + image.width() * 3]
        for row in range(image.height())
    )
    return Pixels(image.width(), image.height(), rgb)


def oracle(fixture: Json, states: list[Json], output: Path) -> Json:
    """Compare UIA against independent fixture state and native pixel content."""
    path = output / "probe/manifest.json"
    if not path.is_file():
        return {"complete": False, "reason": "Probe produced no terminal manifest"}
    report = json.loads(path.read_text(encoding="utf-8"))
    if "target" not in report:
        return {"complete": False, "reason": "No discovered target", "probe": report}
    rect = report["target"]["rect"]
    content = fixture["content_rect"]
    relative = [
        round(content[0] - rect[0]),
        round(content[1] - rect[1]),
        round(content[2] - rect[0]),
        round(content[3] - rect[1]),
    ]
    frames: dict[str, Pixels] = {}
    checks = {}
    scale = fixture.get("dpr", fixture.get("dpi_y", 96) / 96)
    extent = fixture["state"]["target"]["extent_height"]
    viewport = fixture["state"]["target"]["viewport_height"]
    for event in events(output / "probe/calls.jsonl"):
        if event["event"] != "stable_frame":
            continue
        name = event["name"]
        frame = load_pixels(output / f"probe/{name}.png").crop(relative)
        frames[name] = frame
        percent = event["state"]["vertical"]
        # Percent is not an offset oracle: pick the fixture's independently
        # emitted actual offset that matches this provider acknowledgement.
        candidates = [
            s["state"]["target"]["vertical"]
            for s in states
            if abs(
                s["state"]["target"]["vertical"] / (extent - viewport) * 100 - percent
            )
            < OFFSET_PERCENT_TOLERANCE
        ]
        if not candidates:
            checks[name] = {
                "matches": False,
                "reason": "No independent offset snapshot",
            }
        else:
            checks[name] = inspect_rows(frame, candidates[0], scale)
    unchanged = all(
        snapshot["state"][pane] == fixture["state"][pane]
        for snapshot in states
        for pane in ("sibling", "outer")
        if fixture["state"].get(pane) is not None
    )
    horizontal = all(
        s["state"]["target"]["horizontal"] == fixture["state"]["target"]["horizontal"]
        for s in states
    )
    restored_state = bool(states) and states[-1]["state"] == fixture["state"]
    return {
        "complete": report.get("first_failure") is None,
        "probe_first_failure": report.get("first_failure"),
        "uia_rect": rect,
        "independent_content_rect": content,
        "uia_rect_is_content_rect": rect == content,
        "content_oracle_crops_are_test_only": True,
        "frame_checks": checks,
        "top_mid_displacements": overlap_displacements(frames["top"], frames["mid"])
        if "top" in frames and "mid" in frames
        else [],
        "sibling_outer_unchanged": unchanged,
        "horizontal_unchanged": horizontal,
        "original_state_restored": restored_state,
        "original_pixels_restored": "before" in frames
        and "restore" in frames
        and frames["before"] == frames["restore"],
        "product_viewport_qualified": False,
    }


def _wpf_executable(path: Path | None) -> Path:
    if path is None:
        raise RuntimeError("Precompiled WPF fixture executable required")
    return path.resolve()


def _require_probe_success(exit_code: int) -> None:
    if exit_code != 0:
        raise RuntimeError("Probe returned failure; first provider error retained")


def main() -> int:
    """Run one first-attempt-only case, preserving operation and cleanup errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=(
            "wpf-borderless",
            "wpf-bordered",
            "wpf-nested",
            "edge-100",
            "edge-125",
        ),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wpf-executable", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result: Json = {
        "case": args.case,
        "first_failure": None,
        "cleanup": {},
        "checkpoint_passed": False,
    }
    fixture_job = None
    probe_job = None
    server = None
    try:
        result["source"] = attest()
        NativeWindow()
        fixture_job, probe_job = OwnedJob(), OwnedJob()
        state_dir = args.output / "states"
        if args.case.startswith("wpf-"):
            executable = _wpf_executable(args.wpf_executable)
            _, handle = fixture_job.start(
                [
                    str(executable),
                    str(state_dir.resolve()),
                    args.case.removeprefix("wpf-"),
                ],
                ROOT,
            )
            fixture = wait_snapshot(state_dir, fixture_job, handle)
        else:
            server = FixtureServer(ROOT / "scripts/panel_fixture/edge.html", state_dir)
            fixture = edge_fixture(
                fixture_job,
                server,
                args.output.resolve(),
                "1.25" if args.case == "edge-125" else "1",
            )
        result["fixture"] = fixture
        result["helper"] = run_probe(probe_job, fixture, args.output)
        if any(
            record["event"] == "call_end"
            and record["seconds"] > NATIVE_CALL_DEADLINE_SECONDS
            for record in events(args.output / "probe/calls.jsonl")
        ):
            raise RuntimeError(  # noqa: TRY301 - terminal diagnostic boundary
                "Completed native call exceeded three seconds"
            )
        time.sleep(0.15)
        result["oracle"] = oracle(fixture, snapshots(state_dir), args.output)
        result["checkpoint_passed"] = checkpoint_passed(result["oracle"])
        _require_probe_success(result["helper"]["exit_code"])
        if not result["checkpoint_passed"]:
            raise RuntimeError(  # noqa: TRY301 - terminal diagnostic boundary
                "Independent panel checkpoint failed; inspect retained oracle"
            )
    except Exception as error:  # noqa: BLE001 - preserve first diagnostic failure
        result["first_failure"] = repr(error)
    finally:
        for name, job in (("probe", probe_job), ("fixture", fixture_job)):
            if job is not None:
                try:
                    result["cleanup"][name] = job.close()
                except Exception as error:  # noqa: BLE001 - independent cleanup receipt
                    result["cleanup"][name] = {"confirmed": False, "error": repr(error)}
        if server is not None:
            try:
                server.close()
            except Exception as error:  # noqa: BLE001 - preserve original failure
                result["cleanup"]["server"] = {"confirmed": False, "error": repr(error)}
        result["checkpoint_passed"] = (
            result["checkpoint_passed"]
            and result["first_failure"] is None
            and all(v["confirmed"] for v in result["cleanup"].values())
        )
        write(args.output / "receipt.json", result)
    return int(
        result["first_failure"] is not None
        or any(not v["confirmed"] for v in result["cleanup"].values())
    )


if __name__ == "__main__":
    raise SystemExit(main())
