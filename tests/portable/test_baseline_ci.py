"""Retained release gates cannot silently lose timeout or aggregate protection."""

from pathlib import Path

import pytest
from scripts.test_tabs_ci import COVERAGE, COVERAGE_FLOOR, TESTS

ROOT = Path(__file__).resolve().parents[2]


def test_retained_jobs_are_all_required() -> None:
    """Every retained quality lane remains a hard all-success gate."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    aggregate = workflow.split("  ci-passed:\n", 1)[1]
    dependencies = aggregate.split("    needs: [", 1)[1].split("]", 1)[0].split(", ")
    assert set(dependencies) == {
        "portable-baseline",
        "lint",
        "typecheck",
        "test",
        "capture-contracts",
        "tabs-contracts",
        "ux-gallery",
        "build",
        "audit",
        "workflows",
    }
    assert "    if: always()\n" in aggregate
    assert "RESULTS: ${{ join(needs.*.result, ' ') }}" in aggregate
    assert 'if [ "$result" != "success" ]; then' in aggregate
    assert_only_uploads_may_fail(workflow)
    assert 'python: ["3.13", "3.14"]' in workflow
    assert "timeout-minutes: 15" in workflow
    assert "timeout-minutes: 10" in workflow
    assert "tests/ux/test_owned_modal.py" in workflow
    assert "--fail-under 95" in workflow
    assert "hosted-scrollbar" not in workflow
    assert "uia_helper" not in workflow


def assert_only_uploads_may_fail(workflow: str) -> None:
    """Allow ``continue-on-error`` only on artifact uploads, which are diagnostics."""
    lines = workflow.splitlines()
    tolerant = [n for n, line in enumerate(lines) if "continue-on-error" in line]
    for n in tolerant:
        assert lines[n].strip() == "continue-on-error: true"
        assert "uses: actions/upload-artifact@" in lines[n - 1], lines[n - 1]
        assert lines[n + 1].strip() == "timeout-minutes: 3"
    uploads = sum("uses: actions/upload-artifact@" in line for line in lines)
    assert len(tolerant) == uploads


def test_upload_tolerance_cannot_spread_to_quality_steps() -> None:
    """A tolerated step that is not an upload is rejected."""
    soft_test = "      - run: pytest\n        continue-on-error: true\n"
    with pytest.raises(AssertionError):
        assert_only_uploads_may_fail(soft_test)


def test_tab_gate_targets_exist_without_removed_features() -> None:
    """Removed scrolling paths cannot make the retained tabs gate uncollectable."""
    assert COVERAGE_FLOOR == 0.9
    for target in TESTS:
        assert (ROOT / target).is_file(), target
        assert "scroll" not in target
    for module in COVERAGE:
        assert (ROOT / "src" / (module.replace(".", "/") + ".py")).is_file()
        assert "scroll" not in module
