"""Retained release gates cannot silently lose timeout or aggregate protection."""

from pathlib import Path

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
    assert "continue-on-error" not in workflow
    assert 'python: ["3.13", "3.14"]' in workflow
    assert "timeout-minutes: 15" in workflow
    assert "timeout-minutes: 10" in workflow
    assert "tests/ux/test_owned_modal.py" in workflow
    assert "--fail-under 95" in workflow
    assert "hosted-scrollbar" not in workflow
    assert "uia_helper" not in workflow


def test_tab_gate_targets_exist_without_removed_features() -> None:
    """Removed scrolling paths cannot make the retained tabs gate uncollectable."""
    assert COVERAGE_FLOOR == 0.9
    for target in TESTS:
        assert (ROOT / target).is_file(), target
        assert "scroll" not in target
    for module in COVERAGE:
        assert (ROOT / "src" / (module.replace(".", "/") + ".py")).is_file()
        assert "scroll" not in module
