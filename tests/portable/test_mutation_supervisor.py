"""Prove writer retirement precedes cache use without touching native desktop APIs."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts import mutation_supervisor as subject


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Use isolated execution identity and receipt storage."""
    monkeypatch.chdir(tmp_path)
    for name, value in [
        ("GITHUB_SHA", "a" * 40),
        ("GITHUB_RUN_ID", "42"),
        ("GITHUB_RUN_ATTEMPT", "2"),
    ]:
        monkeypatch.setenv(name, value)
    return tmp_path


def test_no_proof_or_stale_attempt_never_admits_cache(
    hosted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither missing evidence nor another attempt may authorize a snapshot."""
    assert Path.cwd() == hosted
    with pytest.raises(FileNotFoundError):
        subject.verify()
    subject.RECEIPT.write_text(
        json.dumps({"identity": subject.identity(), "quiescent": False})
    )
    with pytest.raises(ValueError, match="unverified"):
        subject.verify()
    subject.RECEIPT.write_text(
        json.dumps({"identity": subject.identity(), "quiescent": True})
    )
    subject.verify()
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    with pytest.raises(ValueError, match="unverified"):
        subject.verify()


def test_permission_denial_is_not_retirement(monkeypatch: pytest.MonkeyPatch) -> None:
    """An access error remains a failure instead of becoming process absence."""

    def denied(_group: int, _signal: int) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "killpg", denied, raising=False)
    with pytest.raises(PermissionError):
        subject.group_exists(100)


@pytest.mark.skipif(
    os.name != "posix", reason="Actual owned POSIX process group on CI only"
)
def test_late_writer_is_retired_before_snapshot(hosted: Path) -> None:
    """A real writer ignoring initial cancellation cannot mutate after admission."""
    source = hosted / "writer.py"
    source.write_text("""import signal, time
from pathlib import Path
from types import SimpleNamespace
from typing import cast
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    Path('metadata').write_text(str(time.monotonic()))
    time.sleep(.01)
""")
    # The synthetic command is owned test code, never cache contents.
    child = subprocess.Popen([sys.executable, str(source)], start_new_session=True)  # noqa: S603
    try:
        deadline = time.monotonic() + 5
        while not (hosted / "metadata").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (hosted / "metadata").exists()
        assert subject.retire(child, grace=0.2)
        before = (hosted / "metadata").read_bytes()
        time.sleep(0.1)
        assert (hosted / "metadata").read_bytes() == before
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.skipif(
    os.name != "posix", reason="Actual owned POSIX subprocess on CI only"
)
@pytest.mark.parametrize("exit_code", [0, 7])
def test_full_supervision_keeps_exit_status_and_attempt_proof(
    hosted: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    """Failure is not converted to success merely because retirement succeeds."""
    monkeypatch.setenv("MUTATION_JOB_DEADLINE", str(time.time() + 30))
    assert (
        subject.supervise([sys.executable, "-c", f"raise SystemExit({exit_code})"], 5)
        == exit_code
    )
    subject.verify()
    proof = json.loads((hosted / "mutation-quiescence.json").read_text())
    assert proof["exit_code"] == exit_code


@pytest.mark.skipif(
    os.name != "posix", reason="Actual owned POSIX subprocess on CI only"
)
def test_expired_execution_is_incomplete_but_retired(
    hosted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bounded timeout retains cleanup evidence and cannot become a pass."""
    monkeypatch.setenv("MUTATION_JOB_DEADLINE", str(time.time() + 30))
    assert (
        subject.supervise([sys.executable, "-c", "import time; time.sleep(10)"], 0.2)
        == 124
    )
    subject.verify()
    assert (
        json.loads((hosted / "mutation-quiescence.json").read_text())["exit_code"]
        == 124
    )


def test_workflow_never_seals_before_verified_retirement() -> None:
    """Cancellation cleanup proof gates identity, results and dependent sealing."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/mutation.yml"
    ).read_text()
    assert "timeout-minutes: 150" in workflow
    assert "$(date +%s) + 8400" in workflow
    identity_step = workflow.split("      - name: Verify candidate identities", 1)[
        1
    ].split("      - name:", 1)[0]
    assert "steps.quiescence.outcome == 'success'" in identity_step
    results_step = workflow.split("      - name: Preserve available verdicts", 1)[
        1
    ].split("      - name:", 1)[0]
    assert "steps.quiescence.outcome == 'success'" in results_step
    assert "mutation-quiescence.json" in workflow


@pytest.mark.skipif(os.name != "posix", reason="Owned process cancellation on CI only")
def test_external_cancel_never_claims_success(
    hosted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation is preserved while a disposable owned child is retired."""
    monkeypatch.setenv("MUTATION_JOB_DEADLINE", str(time.time() + 30))
    module = Path(subject.__file__).resolve()
    child = subprocess.Popen(  # noqa: S603 - repository supervisor and synthetic child
        [
            sys.executable,
            str(module),
            "run",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(10)",
        ]
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if subject.RECEIPT.exists() and "process_group" in json.loads(
                subject.RECEIPT.read_text()
            ):
                break
            time.sleep(0.01)
        assert "process_group" in json.loads(subject.RECEIPT.read_text())
        child.send_signal(signal.SIGINT)
        assert child.wait(timeout=5) == 130
        subject.verify()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
    assert hosted == Path.cwd()


@pytest.mark.skipif(os.name != "posix", reason="Actual isolated subprocess on CI only")
def test_uncertain_retirement_blocks_cache(
    hosted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a successful worker exit cannot override failed ownership cleanup."""
    monkeypatch.setenv("MUTATION_JOB_DEADLINE", str(time.time() + 30))
    monkeypatch.setattr(subject, "retire", lambda _child: False)
    assert subject.supervise([sys.executable, "-c", "pass"], 5) == 1
    with pytest.raises(ValueError, match="unverified"):
        subject.verify()
    assert hosted == Path.cwd()


def test_failed_receipt_replacement_keeps_false_proof(
    hosted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem failure never publishes an unverified positive receipt."""
    proof = {"identity": subject.identity(), "quiescent": False}
    subject.write_proof(proof)

    def denied(_self: Path, _target: Path) -> None:
        raise PermissionError("receipt denied")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(PermissionError):
        subject.write_proof({**proof, "quiescent": True})
    with pytest.raises(ValueError, match="unverified"):
        subject.verify()
    assert hosted == Path.cwd()


def test_exited_leader_does_not_hide_remaining_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leader exit alone is insufficient; only its exact group is retired."""
    remaining = iter([True, False])
    signals = []
    monkeypatch.setattr(
        subject, "group_exists", lambda group: next(remaining) if group == 123 else True
    )
    monkeypatch.setattr(
        os,
        "killpg",
        lambda group, signum: signals.append((group, signum)),
        raising=False,
    )
    child = SimpleNamespace(pid=123, poll=lambda: 0)
    assert subject.retire(cast(subprocess.Popen[bytes], child))
    assert signals == [(123, signal.SIGINT)]


@pytest.mark.skipif(os.name != "posix", reason="POSIX supervisor admission on CI only")
def test_cancel_during_retirement_remains_failure(
    hosted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Late cancellation cannot turn a clean worker exit into a successful run."""
    monkeypatch.setenv("MUTATION_JOB_DEADLINE", str(time.time() + 30))

    def retire_with_cancel(_child: subprocess.Popen[bytes]) -> bool:
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        return True

    monkeypatch.setattr(subject, "retire", retire_with_cancel)
    assert subject.supervise([sys.executable, "-c", "pass"], 5) == 130
    proof = json.loads((hosted / "mutation-quiescence.json").read_text())
    assert proof["exit_code"] == 130
    subject.verify()
