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
    assert assess(good(), "b" * 40)[0] is False


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "neutral", None])
def test_passing_release_run_cannot_erase_failed_main(result: object) -> None:
    """Passing release run cannot erase failed main."""
    rows = good()
    rows[0]["conclusion"] = result
    rows.append(run(3, "release"))
    rows.append(run(4, "ci"))
    with pytest.raises(ValueError, match="did not succeed"):
        assess(rows, SHA)


def test_pending_and_optional_main_verification_block() -> None:
    """Pending and optional main verification block."""
    rows = [*good(), run(3, "gui-resource-readiness")]
    rows[-1]["status"] = "in_progress"
    assert not assess(rows, SHA)[0]
    rows[-1]["status"] = "completed"
    rows[-1]["conclusion"] = "failure"
    with pytest.raises((ValueError, TypeError), match=r"."):
        assess(rows, SHA)


def test_async_mutation_does_not_become_a_release_dependency() -> None:
    """Async mutation does not become a release dependency."""
    rows = [*good(), run(3, "mutation"), run(4, "mutation-alerts")]
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


def test_successful_retry_is_not_an_implicit_waiver() -> None:
    """Successful retry is not an implicit waiver."""
    rows = good()
    rows[0]["run_attempt"] = 2
    with pytest.raises(ValueError, match="disposition"):
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
    release = workflow.split("  release-please:\n", 1)[1].split("  ci:\n", 1)[0]
    assert "needs: main-verification" in release
    gate_job = workflow.split("  main-verification:\n", 1)[1].split("  build:\n", 1)[0]
    assert "needs: release-please" not in gate_job
    assert "github.event_name == 'push' || inputs.testpypi" in gate_job
    build = workflow.split("  build:\n", 1)[1].split("  publish-pypi:", 1)[0]
    assert "needs: [release-please, ci, main-verification]" in build
    assert "needs.main-verification.result == 'success'" in build
    assert "needs: [release-please, build]" in workflow.split("  publish-pypi:", 1)[1]
    assert "needs: build" in workflow.split("  publish-testpypi:", 1)[1]


@pytest.mark.parametrize(
    "fault", [None, "moved", "malformed", "alerts", "pending", "failed"]
)
def test_complete_gate_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str | None,
) -> None:
    """Complete gate path."""
    for key, value in {
        "GITHUB_SHA": SHA,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": gate.REPOSITORY,
        "GH_TOKEN": "test-token",
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
        if "actions/runs?" in url:
            payload = {"workflow_runs": rows, "total_count": len(rows)}
        elif "git/ref/heads/main" in url:
            payload = {
                "object": None
                if fault == "malformed"
                else {"sha": "b" * 40 if fault == "moved" else SHA}
            }
        else:
            assert "code-scanning/alerts?" in url
            payload = [{}] if fault == "alerts" else []
        return io.StringIO(json.dumps(payload))

    monkeypatch.setattr(gate, "urlopen", fetch)
    clock = iter([0, 0, 601])
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(gate.time, "sleep", lambda _: None)
    if fault is not None:
        with pytest.raises((ValueError, TimeoutError)):
            gate.main()
        assert not capsys.readouterr().out
    else:
        gate.main()
        receipt = json.loads(capsys.readouterr().out)
        assert receipt == {"main_sha": SHA, "verified_runs": [1, 2]}
        assert len(endpoints) == 3
