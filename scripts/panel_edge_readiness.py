"""One read-only cold/delayed Edge discovery experiment, never capture success."""

from __future__ import annotations

import argparse
import ctypes as c
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from scripts.panel_uia import MAX_ANCESTORS, PanelUia
from scripts.panel_uia_probe import NativeGuard

DELAY_SECONDS = 1.0
GUARD_INTERVAL_SECONDS = 0.05


def normal_chain_end(error: OSError, count: int) -> bool:
    """Only ordinary null-parent exhaustion permits the delayed observation."""
    return 1 <= count <= MAX_ANCESTORS and str(error) == (
        f"No vertical ScrollPattern: ancestor chain ended after {count} nodes"
    )


class ReadinessProbe:
    """Retain one UIA client while observing a single immutable input epoch."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.output = Path(args.output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.log = (self.output / "calls.jsonl").open("x", encoding="utf-8")
        self.report: dict[str, object] = {
            "experimental": True,
            "read_only": True,
            "finished": False,
            "diagnostic_completed": False,
            "capture_checkpoint_passed": False,
            "producer_pid": os.getpid(),
            "parent_pid": os.getppid(),
            "executable": sys.executable,
            "first_failure": None,
        }
        self.emit({"event": "process", **self.report})
        self.uia: PanelUia | None = None
        self.guard: NativeGuard | None = None
        self.observations: list[dict[str, object]] = []
        self.report["observations"] = self.observations

    def emit(self, event: dict[str, object]) -> None:
        """Flush observations and call receipts before subsequent native calls."""
        self.log.write(json.dumps({"monotonic": time.monotonic(), **event}) + "\n")
        self.log.flush()

    def observe(self, name: str) -> bool:
        """Return true only for normal no-pattern exhaustion, never RPC errors."""
        if self.guard is None or self.uia is None:
            raise RuntimeError("Readiness probe not initialized")
        self.guard.check()
        # Only a completed, target-free observation can reach this point again.
        # Keep the same automation/walker interfaces and all owned references.
        if self.uia.target is not None:
            raise RuntimeError("Discovery already selected a target")
        self.uia.ancestors.clear()
        result: dict[str, object] = {"name": name, "monotonic": time.monotonic()}
        self.observations.append(result)
        self.emit({"event": "observation_start", **result})
        no_pattern = False
        try:
            result["target"] = asdict(self.uia.discover(self.args.x, self.args.y))
            result["outcome"] = "target_found"
        except OSError as error:
            result["error"] = repr(error)
            if not normal_chain_end(error, len(self.uia.ancestors)):
                result["outcome"] = "provider_error"
                raise
            result["outcome"] = "no_pattern"
            no_pattern = True
        finally:
            result["ancestors"] = [asdict(item) for item in self.uia.ancestors]
            self.emit({"event": "observation", **result})
        self.guard.check()
        return no_pattern

    def wait_once(self) -> None:
        """One fixed delay, with ownership checks rather than discovery polling."""
        if self.guard is None:
            raise RuntimeError("Readiness probe not initialized")
        deadline = time.monotonic() + DELAY_SECONDS
        while True:
            self.guard.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(GUARD_INTERVAL_SECONDS, remaining))

    def execute(self) -> None:
        """Validate the explicitly owned root before any UIA observation."""
        fixture = json.loads(
            Path(self.args.fixture_manifest).read_text(encoding="utf-8")
        )
        self.guard = NativeGuard(int(fixture["root_hwnd"]), self.args.x, self.args.y)
        user = self.guard.user
        user.GetWindowThreadProcessId.argtypes = [c.c_void_p, c.POINTER(c.c_uint32)]
        user.GetWindowThreadProcessId.restype = c.c_uint32
        pid = c.c_uint32()
        if not user.GetWindowThreadProcessId(fixture["root_hwnd"], c.byref(pid)):
            raise RuntimeError("Cannot identify fixture root process")
        if pid.value != self.args.expected_pid:
            raise RuntimeError("Root PID differs from expected owned fixture")
        self.uia = PanelUia(self.emit)
        if self.observe("cold"):
            self.wait_once()
            self.observe("delayed")
        self.report["finished"] = True

    def run(self) -> int:
        """Diagnostic completion is separate from a capture checkpoint."""
        try:
            self.execute()
        except Exception as error:  # noqa: BLE001 - preserve terminal diagnostic failure
            self.report["first_failure"] = repr(error)
            self.report["finished"] = False
            self.emit({"event": "first_failure", "error": repr(error)})
        finally:
            try:
                if self.uia is not None:
                    self.uia.close()
            except Exception as error:  # noqa: BLE001 - preserve cleanup failure
                self.report["cleanup_failure"] = repr(error)
                if self.report["first_failure"] is None:
                    self.report["first_failure"] = repr(error)
                self.report["finished"] = False
            finally:
                self.report["diagnostic_completed"] = (
                    self.report["finished"] is True
                    and self.report["first_failure"] is None
                )
                (self.output / "manifest.json").write_text(
                    json.dumps(self.report, indent=2), encoding="utf-8"
                )
                self.log.close()
        return int(self.report["first_failure"] is not None)


def main() -> int:
    """Accept the same owned-fixture inputs as the scrolling feasibility helper."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--expected-pid", type=int, required=True)
    parser.add_argument("--fixture-manifest", required=True)
    parser.add_argument("--output", required=True)
    return ReadinessProbe(parser.parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
