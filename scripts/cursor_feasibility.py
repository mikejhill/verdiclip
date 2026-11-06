"""Bounded hosted cursor experiment; evidence never qualifies PR94 for release."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, Protocol, cast

from scripts.cursor_actor import require_host

MAX_COMMANDS = 48
MAX_RECEIPT_BYTES = 16384


class Actor:
    """One owned process, bounded commands, file-backed output (no reader threads)."""

    def __init__(self, command: list[str], output: Path, deadline: float) -> None:
        self.output = output
        self.sequence = 0
        self.deadline = deadline
        self.sink: IO[str] = output.open("w", encoding="utf-8")
        self.process = subprocess.Popen(  # noqa: S603 - fixed owned commands only
            command,
            stdin=subprocess.PIPE,
            stdout=self.sink,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def request(self, command: str) -> dict[str, object]:
        """Bound tiny command writes below pipe capacity, even if the peer stalls."""
        if self.sequence >= MAX_COMMANDS or command not in {
            "observe",
            "arm",
            "acquire",
            "hide",
            "show",
            "close",
            "noop",
            "quit",
        }:
            raise RuntimeError("Owned protocol limit")
        if self.process.stdin is None:
            raise RuntimeError("Owned command pipe unavailable")
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        return self.receive()

    def receive(self) -> dict[str, object]:
        """Receive one bounded file-backed receipt, including the oracle ready line."""
        self.sequence += 1
        deadline = min(self.deadline, time.monotonic() + 6)
        while time.monotonic() < deadline:
            if self.output.stat().st_size > MAX_RECEIPT_BYTES:
                raise RuntimeError("Owned receipt exceeded bound")
            lines = self.output.read_text(encoding="utf-8").splitlines()
            if len(lines) >= self.sequence:
                result = json.loads(lines[self.sequence - 1])
                if not isinstance(result, dict):
                    raise RuntimeError("Invalid owned receipt")
                return cast("dict[str, object]", result)
            if self.process.poll() is not None:
                raise RuntimeError("Owned actor exited without receipt")
            time.sleep(0.02)
        raise TimeoutError("Owned actor receipt deadline")

    def terminate(self) -> None:
        """Terminate only the exact retained child, then observe before any rescue."""
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=2)

    def retire(self) -> None:
        """No pipe reads/joins; bounded owned termination runs even if close fails."""
        try:
            self.terminate()
        finally:
            try:
                if self.process.stdin is not None:
                    self.process.stdin.close()
            finally:
                self.sink.close()


def require_ok(receipt: dict[str, object]) -> None:
    """API receipts are operation evidence, not presentation evidence."""
    if receipt.get("ok") is not True:
        raise RuntimeError("Owned API operation refused")


def observe(oracle: Port, *, transition: bool = False) -> str:
    """Accept only independent pointer-plane plus owned-surface verdicts."""
    receipt = oracle.request("observe")
    if receipt.get("supported") is not True or (
        transition and receipt.get("pointer_update_after_boundary") is not True
    ):
        return "unsupported"
    status = receipt.get("status")
    return (
        status
        if isinstance(status, str) and status in {"visible", "hidden"}
        else "unsupported"
    )


class Port(Protocol):
    """Only bounded operations used by the experiment coordinator."""

    def request(self, command: str) -> dict[str, object]:
        """Perform one bounded owned command."""
        ...

    def terminate(self) -> None:
        """Force termination of exactly the owned child."""
        ...

    def retire(self) -> None:
        """Confirm bounded retirement and close local handles."""
        ...


def retire_all(actors: Sequence[Port]) -> None:
    """Attempt every exact owned retirement even if an earlier one raises."""
    if actors:
        try:
            actors[0].retire()
        finally:
            retire_all(actors[1:])


def hide_competitor(competitor: Port | None, oracle: Port) -> str:
    """Confirm the second owned client's actual presentation before proceeding."""
    if competitor is None:
        raise RuntimeError("Owned competitor missing")
    require_ok(oracle.request("arm"))
    require_ok(competitor.request("hide"))
    return observe(oracle, transition=True)


def scenario(
    name: str, oracle: Port, create: Callable[[str], Port], owned: list[Port]
) -> dict[str, object]:
    """Record product observations before any supervisor restoration action."""
    lease = create("lease")
    owned.append(lease)
    competitor: Port | None = None
    receipt: dict[str, object] = {"name": name, "product_pass": False}
    if name in {"preexisting-hide", "concurrent-hide"}:
        competitor = create("client")
        owned.append(competitor)
    if name == "preexisting-hide":
        receipt["other_client_hidden_before"] = hide_competitor(competitor, oracle)
        if receipt["other_client_hidden_before"] != "hidden":
            receipt["case_precondition"] = "unsupported"
            return receipt
    require_ok(oracle.request("arm"))
    acquired = lease.request("acquire")
    require_ok(acquired)
    receipt["production_suppression_attempted"] = acquired.get("attempted") is True
    receipt["during"] = observe(oracle, transition=True)
    if receipt["during"] == "unsupported":
        return receipt
    if name == "concurrent-hide":
        receipt["other_client_hidden_during"] = hide_competitor(competitor, oracle)
        if receipt["other_client_hidden_during"] != "hidden":
            receipt["case_precondition"] = "unsupported"
            return receipt
    if name == "forced-termination":
        if (
            receipt["during"] != "hidden"
            or not receipt["production_suppression_attempted"]
        ):
            receipt["case_precondition"] = "unsupported"
            return receipt
        require_ok(oracle.request("arm"))
        lease.terminate()
        receipt["after_termination_before_rescue"] = observe(oracle, transition=True)
        receipt["crash_observed_before_rescue"] = True
        receipt["automatic_restoration_observed"] = (
            None
            if receipt["after_termination_before_rescue"] == "unsupported"
            else receipt["after_termination_before_rescue"] == "visible"
        )
    else:
        require_ok(oracle.request("arm"))
        require_ok(lease.request("close"))
        receipt["after_product_close_before_rescue"] = observe(oracle, transition=True)
        if name == "ordinary-lease":
            receipt["product_pass"] = (
                receipt["production_suppression_attempted"] is True
                and receipt["during"] == "hidden"
                and receipt["after_product_close_before_rescue"] == "visible"
            )
        else:
            receipt["other_client_hide_preserved"] = (
                None
                if receipt["after_product_close_before_rescue"] == "unsupported"
                else receipt["after_product_close_before_rescue"] == "hidden"
            )
    return receipt


def rescue_baseline(oracle: Port, rescue: Port) -> str:
    """Optional evidence arming must never prevent the owned restoration attempt."""
    try:
        try:
            require_ok(oracle.request("arm"))
            armed = True
        except Exception:  # noqa: BLE001 - fixed unconfirmed evidence, rescue still runs
            armed = False
    finally:
        require_ok(rescue.request("show"))
    return observe(oracle, transition=True) if armed else "unsupported"


def run_cases(
    oracle: Port, create: Callable[[str], Port], result: dict[str, object] | None = None
) -> dict[str, object]:
    """Reject unsupported presentation before touching global Magnification state."""
    if result is None:
        result = {}
    result.update(
        {
            "scope": "production-CursorVisibilityLease-only-no-hooks",
            "release_qualified": False,
            "global_uncounted_safety_incompatibility": True,
            "presentation_feasible": False,
            "status": "unsupported",
        }
    )
    baseline = observe(oracle)
    result["baseline"] = baseline
    if baseline != "visible":
        return result
    # Negative control: a deliberate no-op must NOT be accepted as hidden.
    negative = create("lease")
    try:
        require_ok(negative.request("noop"))
        result["negative_noop"] = observe(oracle)
    finally:
        negative.retire()
    if result["negative_noop"] != "visible":
        result["status"] = "unsupported-negative-control"
        return result
    result["presentation_feasible"] = True
    cases: list[dict[str, object]] = []
    result["cases"] = cases
    for name in (
        "ordinary-lease",
        "preexisting-hide",
        "concurrent-hide",
        "forced-termination",
    ):
        owned: list[Port] = []
        receipt: dict[str, object] = {"name": name, "status": "incomplete"}
        cases.append(receipt)
        try:
            receipt.update(scenario(name, oracle, create, owned))
            receipt["status"] = (
                "unsupported-phase" if "unsupported" in receipt.values() else "observed"
            )
        finally:
            # Crash observation has happened above before even creating rescue.
            # Rescue is cleanup of known fixture baseline, never product success.
            try:
                retire_all(owned)
            finally:
                rescue = create("client")
                try:
                    receipt["supervisor_rescue_visible"] = rescue_baseline(
                        oracle, rescue
                    )
                    require_ok(rescue.request("close"))
                finally:
                    rescue.retire()
        if receipt.get("supervisor_rescue_visible") != "visible":
            result["status"] = "cleanup-unconfirmed"
            return result
        if receipt["status"] == "unsupported-phase":
            result["status"] = "unsupported-phase"
            return result
    result["status"] = "diagnostic-complete-not-qualified"
    return result


def main() -> int:
    """Run only the isolated hosted workflow, preserving a metadata-only receipt."""
    require_host()
    root = Path.cwd() / ".test-tmp" / "cursor-feasibility"
    root.mkdir(parents=True, exist_ok=True)
    executable = root / "cursor-oracle.exe"
    if not executable.is_file():
        raise RuntimeError("Reviewed native oracle executable is unavailable")
    result: dict[str, object] = {"status": "incomplete", "release_qualified": False}
    with tempfile.TemporaryDirectory(prefix="owned-", dir=root) as folder:
        directory = Path(folder)
        deadline = time.monotonic() + 100
        oracle = Actor([str(executable)], directory / "oracle.jsonl", deadline)
        actors: list[Actor] = []

        def create(role: str) -> Actor:
            actor = Actor(
                [sys.executable, "-m", "scripts.cursor_actor", role],
                directory / f"actor-{len(actors)}.jsonl",
                deadline,
            )
            actors.append(actor)
            return actor

        try:
            ready = oracle.receive()
            if ready.get("reason") != "ready_for_baseline":
                result["status"] = "unsupported-oracle-initialization"
            else:
                run_cases(oracle, create, result)
        except Exception:  # noqa: BLE001 - retain fixed failure outcome, never pass
            result["status"] = "failed-or-unconfirmed"
        finally:
            try:
                try:
                    stopped = oracle.request("quit")
                    result["oracle_cleanup_confirmed"] = (
                        stopped.get("cleanup_confirmed") is True
                    )
                finally:
                    oracle.retire()
            finally:
                try:
                    retire_all(actors)
                finally:
                    (root / "receipt.json").write_text(
                        json.dumps(result, indent=2), encoding="utf-8"
                    )
    sys.stdout.write(json.dumps(result) + "\n")
    return (
        0
        if result.get("status") == "diagnostic-complete-not-qualified"
        and result.get("oracle_cleanup_confirmed")
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
