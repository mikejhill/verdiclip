"""Publication cannot race or replace a contradictory main verification result."""

import io
import json
from pathlib import Path
from urllib.request import Request

import pytest
from scripts import release_main_gate as gate
from scripts.release_main_gate import assess, collect

SHA = "a" * 40


def run(identity: int, path: str) -> dict[str, object]:
    """Run."""
    return {
        "id": identity,
        "path": f".github/workflows/{path}.yml",
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
    }


def good() -> list[dict[str, object]]:
    """Good."""
    return [run(1, "ci"), run(2, "codeql")]


def test_exact_main_checks_required() -> None:
    """Exact main checks required."""
    assert assess(good(), SHA) == (True, [1, 2])
    assert assess([], SHA) == (False, [])
    assert assess(good()[:1], SHA)[0] is False
    assert assess(good()[1:], SHA)[0] is False
    assert assess(good(), "b" * 40)[0] is False


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "neutral", None])
def test_passing_release_run_cannot_erase_failed_main(result: object) -> None:
    """Passing release run cannot erase failed main."""
    rows = good()
    rows[0]["conclusion"] = result
    rows.append(run(4, "release"))
    rows.append(run(5, "ci"))
    with pytest.raises(ValueError, match="did not succeed"):
        assess(rows, SHA)


def test_other_main_workflows_do_not_gate_publication() -> None:
    """Only CI and CodeQL gate; other workflows can be pending or red."""
    rows = [*good(), run(4, "instance-lifetime"), run(5, "anything-else")]
    rows[-2]["status"] = "in_progress"
    rows[-1]["conclusion"] = "failure"
    assert assess(rows, SHA) == (True, [1, 2])


def test_async_mutation_does_not_become_a_release_dependency() -> None:
    """Async mutation does not become a release dependency."""
    rows = [*good(), run(4, "mutation"), run(5, "mutation-alerts")]
    for item in rows[2:]:
        item["status"] = "queued"
    assert assess(rows, SHA) == (True, [1, 2])


@pytest.mark.parametrize(
    ("field", "value"), [("event", "pull_request"), ("head_branch", "other")]
)
def test_pr_or_other_branch_is_not_main_evidence(field: str, value: str) -> None:
    """Pr or other branch is not main evidence."""
    rows = good()
    rows[0][field] = value
    assert not assess(rows, SHA)[0]


def test_rerun_counts_by_its_latest_result() -> None:
    """A re-run flaky job is judged by its latest attempt."""
    rows = good()
    rows[0]["run_attempt"] = 2
    assert assess(rows, SHA) == (True, [1, 2])
    rows[0]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="did not succeed"):
        assess(rows, SHA)


@pytest.mark.parametrize("payload", [None, {}, {"workflow_runs": [], "total_count": 1}])
def test_incomplete_inventory_refuses(payload: object) -> None:
    """Incomplete inventory refuses."""
    with pytest.raises((ValueError, TypeError), match=r"."):
        collect(lambda _: payload, SHA)


def test_pagination_keeps_late_failure_and_rejects_duplicates() -> None:
    """Pagination keeps late failure and rejects duplicates."""
    first = [run(i + 1, "ci") for i in range(100)]
    last = run(101, "codeql")
    last["conclusion"] = "failure"
    pages = iter([first, [last]])
    rows = collect(lambda _: {"workflow_runs": next(pages), "total_count": 101}, SHA)
    with pytest.raises((ValueError, TypeError), match=r"."):
        assess(rows, SHA)
    with pytest.raises(ValueError, match="ambiguous"):
        collect(
            lambda _: {"workflow_runs": [first[0], first[0]], "total_count": 2}, SHA
        )


def test_both_publish_targets_depend_on_gated_build() -> None:
    """Both publish targets depend on gated build."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"
    ).read_text()
    release = workflow.split("  release-please:\n", 1)[1].split(
        "  main-verification:\n", 1
    )[0]
    assert "needs: main-verification" in release
    gate_job = workflow.split("  main-verification:\n", 1)[1].split("  build:\n", 1)[0]
    assert "needs: release-please" not in gate_job
    assert "github.event_name == 'push' || inputs.testpypi" in gate_job
    build = workflow.split("  build:\n", 1)[1].split("  publish-pypi:", 1)[0]
    assert "needs: [release-please, main-verification]" in build
    assert "  ci:\n" not in workflow, (
        "main-verification already proves CI on this commit"
    )
    assert "needs.main-verification.result == 'success'" in build
    superseded = "needs.main-verification.outputs.superseded != 'true'"
    assert superseded in release
    assert superseded in build
    assert "superseded: ${{ steps.gate.outputs.superseded }}" in gate_job
    assert "needs: [release-please, build]" in workflow.split("  publish-pypi:", 1)[1]
    assert "needs: build" in workflow.split("  publish-testpypi:", 1)[1]


def reply(url: str, rows: list[dict[str, object]], fault: str | None) -> object:
    """GitHub's answer to ``url`` for a gate run with ``fault``."""
    if "actions/runs?" in url:
        return {"workflow_runs": rows, "total_count": len(rows)}
    if "git/ref/heads/main" in url:
        if fault == "malformed":
            return {"object": None}
        moved = fault in {"moved", "moved-quietly"}
        return {"object": {"sha": "b" * 40 if moved else SHA}}
    if "compare/" in url:
        assert url.endswith(f"compare/{SHA}...{'b' * 40}")
        title = "chore: release 9.9.9" if fault == "moved" else "fix: other"
        return {
            "status": "ahead",
            "total_commits": 1,
            "commits": [{"commit": {"message": f"{title} (#1)\n\nbody"}}],
        }
    if "code-scanning/analyses?" in url:
        assert f"sha={SHA}" in url
        return [{"results_count": 0}, {"results_count": 0}]
    assert "code-scanning/alerts?" in url
    return [{}] if fault == "alerts" else []


@pytest.mark.parametrize(
    "fault",
    [None, "moved", "moved-quietly", "malformed", "alerts", "pending", "failed"],
)
def test_complete_gate_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fault: str | None,
) -> None:
    """Complete gate path."""
    output = tmp_path / "github_output"
    for key, value in {
        "GITHUB_SHA": SHA,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": gate.REPOSITORY,
        "GH_TOKEN": "test-token",
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    rows = good()
    if fault == "pending":
        rows[0]["status"] = "queued"
    if fault == "failed":
        rows[0]["conclusion"] = "failure"
    endpoints: list[str] = []

    def fetch(request: Request, *, timeout: int) -> io.StringIO:
        assert timeout == 10
        url = request.full_url
        assert url.startswith("https://api.github.com/repos/mikejhill/verdiclip/")
        endpoints.append(url)
        return io.StringIO(json.dumps(reply(url, rows, fault)))

    monkeypatch.setattr(gate, "urlopen", fetch)
    clock = iter([0, 0, 1801])
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(gate.time, "sleep", lambda _: None)
    if fault == "moved":
        # Stands down for the newer push's run: no scan check, no publish
        gate.main()
        receipt = json.loads(capsys.readouterr().out)
        assert receipt == {"main_sha": SHA, "superseded_by": "b" * 40}
        assert output.read_text(encoding="utf-8") == "superseded=true\n"
        assert not any("code-scanning" in url for url in endpoints)
    elif fault not in {None, "moved-quietly"}:
        with pytest.raises((ValueError, TimeoutError)):
            gate.main()
        assert not capsys.readouterr().out
        assert not output.exists()
    else:
        # Commits other than a release merge landing meanwhile don't stop it
        gate.main()
        receipt = json.loads(capsys.readouterr().out)
        assert receipt == {"main_sha": SHA, "verified_runs": [1, 2]}
        assert len(endpoints) == (4 if fault else 3)
        # A moved release is judged by its own commit's scan, not main's alerts
        assert any("code-scanning/analyses" in u for u in endpoints) == bool(fault)
        assert not output.exists()


@pytest.mark.parametrize("outcome", ["queued", "failure", "skipped"])
def test_codeql_is_a_required_publication_gate(outcome: str) -> None:
    """A release still needs its exact main CodeQL result."""
    rows = good()
    if outcome == "queued":
        rows[-1]["status"] = "queued"
        assert not assess(rows, SHA)[0]
    else:
        rows[-1]["conclusion"] = outcome
        with pytest.raises(ValueError, match="did not succeed"):
            assess(rows, SHA)


@pytest.mark.parametrize(
    ("compare", "held"),
    [
        (None, True),
        ({"status": "diverged", "total_commits": 0, "commits": []}, True),
        ({"status": "ahead", "total_commits": 300, "commits": []}, True),
        ({"status": "ahead", "total_commits": 1, "commits": ["x"]}, True),
        (
            {
                "status": "ahead",
                "total_commits": 2,
                "commits": [
                    {"commit": {"message": "fix: a"}},
                    {"commit": {"message": "chore: release 1.2.3 (#9)"}},
                ],
            },
            True,
        ),
        (
            {
                "status": "ahead",
                "total_commits": 1,
                "commits": [{"commit": {"message": "feat: b"}}],
            },
            False,
        ),
    ],
    ids=["unreadable", "diverged", "truncated", "malformed", "release", "quiet"],
)
def test_only_a_later_release_merge_supersedes(
    compare: object,
    held: bool,  # noqa: FBT001 - pytest parameter
) -> None:
    """A newer release merge, or any doubt, stands down; other commits don't."""
    assert gate.releases_between(compare) is held


@pytest.mark.parametrize(
    ("analyses", "clean"),
    [
        (None, False),
        ([], False),
        ([{"results_count": 0}, {"results_count": 2}], False),
        ([{"results_count": 0}, "x"], False),
        ([{"results_count": 0}], True),
    ],
    ids=["unreadable", "none", "findings", "malformed", "clean"],
)
def test_a_moved_release_needs_its_own_clean_scan(
    analyses: object,
    clean: bool,  # noqa: FBT001 - pytest parameter
) -> None:
    """Only analyses of the release commit itself, all with no results, pass."""
    assert gate.clean_analyses(analyses) is clean
