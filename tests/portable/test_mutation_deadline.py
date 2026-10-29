"""Static workflow contract for bounded baseline failure and retained evidence."""

from pathlib import Path


def _step(workflow: str, name: str) -> str:
    """Select an explicitly named YAML step without importing a YAML runtime."""
    marker = f"      - name: {name}"
    return workflow.split(marker, 1)[1].split("      - name:", 1)[0]


def test_baseline_deadline_reserves_failure_evidence() -> None:
    """A failed baseline stops mutation and leaves independent diagnostics enabled."""
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/mutation.yml").read_text(encoding="utf-8")
    assert "timeout-minutes: 150" in workflow
    baseline = _step(workflow, "Verify portable baseline before mutation")
    assert "timeout-minutes: 10" in baseline
    assert "continue-on-error" not in baseline
    assert "set -o pipefail" in baseline
    assert "tee mutation-baseline.txt" in baseline
    assert "-p no:randomly" in baseline
    for name in (
        "Retain identity diagnostics",
        "Retain verdict context qualification",
        "Emit structured completion evidence",
        "Upload results",
    ):
        step = _step(workflow, name)
        assert "if: always()" in step, name
    upload = workflow.split("      - name: Upload results", 1)[1]
    assert "mutation-baseline.txt" in upload
    assert "mutation-result.json" in upload
    assert "BASELINE_OUTCOME: ${{ steps.baseline.outcome }}" in workflow
