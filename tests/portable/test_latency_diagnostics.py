"""Portable diagnostics tests; importing widgets is deliberately unnecessary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.latency_trace import LatencyTrace
from scripts.qt_wait_audit import discarded_waits


@pytest.mark.parametrize(
    "call", ["waitExposed", "wait_exposed", "waitActive", "wait_active"]
)
def test_discarded_context_wait_is_rejected(call: str) -> None:
    """Calling a context factory alone never invokes its exit-time wait."""
    assert discarded_waits(f"qtbot.{call}(window)\n") == [1]
    assert discarded_waits(f"with qtbot.{call}(window):\n    window.show()\n") == []


def test_comments_and_unrelated_methods_are_not_context_waits() -> None:
    """Plain waitUntil remains valid and text mentioning waits is harmless."""
    assert (
        discarded_waits('qtbot.waitUntil(ready)\ntext = "qtbot.waitExposed(x)"\n') == []
    )


def test_repository_has_no_discarded_qt_wait_contexts() -> None:
    """Keep actual readiness checks from silently becoming no-ops again."""
    root = Path(__file__).resolve().parents[2]
    failures = {
        str(p.relative_to(root)): lines
        for p in (root / "tests").rglob("*.py")
        if (lines := discarded_waits(p.read_text(encoding="utf-8")))
    }
    assert failures == {}


def test_phase_clocks_report_wall_and_cpu_without_causal_claim() -> None:
    """A large wall-only delay is retained, not relabelled as runner scheduling."""
    wall = iter([0, 2_295_000_000])
    process = iter([0, 5_000_000])
    thread = iter([0, 4_000_000])
    trace = LatencyTrace(
        metadata={"scenario": "cold"},
        wall=lambda: next(wall),
        process=lambda: next(process),
        thread=lambda: next(thread),
    )
    with trace.phase("editor"):
        pass
    report = json.loads(trace.describe())
    assert report["phases"] == [
        {
            "name": "editor",
            "completed": True,
            "wall_ms": 2295,
            "process_cpu_ms": 5,
            "thread_cpu_ms": 4,
        }
    ]
    assert report["metadata"] == {"scenario": "cold"}
    assert "does not distinguish" in report["interpretation"]


def test_exception_phase_is_retained_without_suppression() -> None:
    """Failure diagnostics must not swallow the actual product failure."""
    trace = LatencyTrace()

    def fail_delivery() -> None:
        with trace.phase("delivery"):
            raise ValueError("original failure")

    with pytest.raises(ValueError, match="original failure"):
        fail_delivery()
    assert trace.phases[0]["completed"] is False


def test_nested_timings_are_inclusive_not_summed() -> None:
    """Nested traces explicitly disclose overlap rather than inflated totals."""
    clock = iter([0, 1_000_000, 3_000_000, 5_000_000])
    trace = LatencyTrace(wall=lambda: next(clock))
    with trace.phase("outer"), trace.phase("inner"):
        pass
    assert trace.phases[0]["wall_ms"] == 2
    assert trace.phases[1]["wall_ms"] == 5
    assert "overlap" in trace.describe()


def test_failure_notes_are_lazy_and_preserve_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful journeys emit nothing; failures retain their original identity."""
    calls = []
    original = LatencyTrace.describe

    def describe(trace: LatencyTrace) -> str:
        calls.append("serialized")
        return original(trace)

    monkeypatch.setattr(LatencyTrace, "describe", describe)
    trace = LatencyTrace()
    with trace.on_failure():
        pass
    assert calls == []
    error = ValueError("original failure")

    def fail_delivery() -> None:
        with trace.on_failure(), trace.phase("delivery"):
            raise error

    with pytest.raises(ValueError, match="original failure") as caught:
        fail_delivery()
    assert caught.value is error
    assert calls == ["serialized"]
    note = json.loads(error.__notes__[0])
    assert note["phases"][0]["completed"] is False


def test_unserializable_diagnostics_cannot_replace_product_failure() -> None:
    """Malformed metadata loses diagnostics, never the failure being diagnosed."""
    trace = LatencyTrace(metadata={"invalid": object()})
    original = AssertionError("budget exceeded")

    def fail_budget() -> None:
        with trace.on_failure():
            raise original

    with pytest.raises(AssertionError, match="budget exceeded") as caught:
        fail_budget()
    assert caught.value is original
    assert "diagnostics unavailable: TypeError" in original.__notes__[0]


def test_failed_exit_clock_cannot_replace_body_exception() -> None:
    """Clock injection failure preserves the original object and marks evidence lost."""
    clock = iter([0])
    trace = LatencyTrace(wall=lambda: next(clock))
    original = ValueError("delivery failed")

    def fail_delivery() -> None:
        with trace.on_failure(), trace.phase("delivery"):
            raise original

    with pytest.raises(ValueError, match="delivery failed") as caught:
        fail_delivery()
    assert caught.value is original
    assert "clocks unavailable: StopIteration" in original.__notes__[0]
    assert trace.phases == []


def test_focused_cold_profile_runs_before_other_editor_tests() -> None:
    """The outlier path is qualified in its own process rather than shuffled warm."""
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    profile = workflow.split("  readiness-cold:", 1)[1].split("  ux-gallery:", 1)[0]
    assert 'qt: ["6.11.2", "6.12.0"]' in profile
    assert "QT_QPA_PLATFORM: offscreen" in profile
    assert "uv run --no-sync pytest" in profile
    assert (
        "tests/ux/test_app_journeys.py::TestAfterCapture::test_hotkey_opens_focused_editor_with_select_tool"
        in profile
    )
    assert "--randomly-seed" not in profile
    assert profile.count("uv run --no-sync pytest") == 4
    assert "!cancelled()" in profile
    assert profile.index(
        "test_hotkey_opens_focused_editor_with_select_tool"
    ) < profile.index("tests/ux/test_owned_modal.py")
    assert profile.index("tests/ux/test_owned_modal.py") < profile.index(
        "test_first_font_activation_reports_separate_cost"
    )
    assert profile.index(
        "test_first_font_activation_reports_separate_cost"
    ) < profile.index("tests/editor/test_style_bar.py")
    assert 'VERDICLIP_FIRST_FONT_PROCESS: "1"' in profile
    assert "first-font.xml" in profile
    assert "font-controls.xml" in profile
    assert "readiness-cold" in workflow.split("  ci-passed:", 1)[1]
