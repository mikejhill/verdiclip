"""Three read-only identity checkpoints on an explicitly owned fixture."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scripts.panel_uia import Com, ScrollState, Structure
from scripts.panel_uia_identity import (
    IdentityUia,
    prove_association,
    record_cleanup_error,
)
from scripts.panel_uia_probe import NativeGuard, Probe

CHECKPOINTS = 3


@dataclass(frozen=True)
class Baseline:
    """Session-local evidence retained only by this helper's MTA thread."""

    client: IdentityUia
    root: Com
    root_id: tuple[int, ...]
    target_id: tuple[int, ...]
    structure: Structure
    state: ScrollState


def checkpoint(
    probe: Probe, evidence: Baseline, guard: NativeGuard, index: int
) -> None:
    """Release every freshly acquired reference even after replacement rejection."""
    baseline, root = evidence.client, evidence.root
    root_id, target_id = evidence.root_id, evidence.target_id
    initial, initial_state = evidence.structure, evidence.state
    if baseline.target is None:
        raise RuntimeError("Missing retained target")
    fresh = IdentityUia(probe.emit)
    try:
        current = fresh.discover(probe.args.x, probe.args.y)
        if current != initial or fresh.read_state() != initial_state:
            raise RuntimeError("Read-only selected structure/state changed")
        if fresh.target is None:
            raise RuntimeError("Missing fresh target")
        if baseline.ids.read(root) != root_id:
            raise RuntimeError("Selected root changed")
        chain = prove_association(fresh, fresh.target, root, baseline.target, target_id)
        guard.check()
        probe.emit(
            {
                "event": "identity_checkpoint",
                "checkpoint": index,
                "target_id": target_id,
                "root_id": root_id,
                "ancestry": chain,
                "structure": asdict(current),
                "state": asdict(initial_state),
            }
        )
    finally:
        primary = sys.exception()
        try:
            fresh.close()
        except BaseException:
            if primary is None:
                raise


def execute(probe: Probe, fixture: dict[str, Any]) -> None:
    """Never scroll or restore; observe only three bounded identity checkpoints."""
    guard = NativeGuard(int(str(fixture["root_hwnd"])), probe.args.x, probe.args.y)
    probe.guard = guard
    baseline = IdentityUia(probe.emit)
    probe.uia = baseline
    failures: list[BaseException] = []
    try:
        initial = probe.discover_target()
        probe.identity = initial
        probe.check_rect(fixture)
        if baseline.target is None:
            raise RuntimeError("Missing baseline target")  # noqa: TRY301 - cleanup
        root = baseline.from_handle(guard.root)
        root_id = baseline.ids.read(root)
        target_id = baseline.ids.read(baseline.target)
        initial_state = baseline.read_state()
        guard.check()
        for index in range(CHECKPOINTS):
            guard.check()
            checkpoint(
                probe,
                Baseline(baseline, root, root_id, target_id, initial, initial_state),
                guard,
                index,
            )
            time.sleep(0.1)
        guard.check()
        probe.report["identity_checkpoints_completed"] = CHECKPOINTS
        probe.report["read_only"] = True
        probe.report["native_replacement_qualified"] = False
    except BaseException as error:
        failures.append(error)
        raise
    finally:
        try:
            baseline.close()
        except BaseException as error:
            record_cleanup_error(probe.emit, error)
            if not failures:
                raise


def main() -> int:
    """Run only a fixed manifest-selected owned fixture, never an arbitrary target."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture_manifest.read_text(encoding="utf-8"))
    args.x, args.y = (round(value) for value in fixture["click"])
    args.settle_discovery = True
    probe = Probe(args)
    try:
        execute(probe, fixture)
    except Exception as error:  # noqa: BLE001 - retain first native failure
        probe.report["first_failure"] = repr(error)
        probe.emit({"event": "first_failure", "error": repr(error)})
    finally:
        # Unlike the capture probe, no restoration is attempted, even on failure.
        (probe.output / "manifest.json").write_text(
            json.dumps(probe.report, indent=2), encoding="utf-8"
        )
        probe.log.close()
    return int(probe.report["first_failure"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
