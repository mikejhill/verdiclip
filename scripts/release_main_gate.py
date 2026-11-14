"""Fail closed until the exact release commit's main-push checks have passed."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.request import Request, urlopen

PAGE_SIZE = 100
REPOSITORY = "mikejhill/verdiclip"
# Only these gate publication. Other main workflows (mutation, release itself,
# Dependabot graph updates) are asynchronous signals, not release dependencies.
REQUIRED = {
    ".github/workflows/ci.yml",
    ".github/workflows/codeql.yml",
}


def assess(runs: list[dict[str, object]], sha: str) -> tuple[bool, list[int]]:
    """Ready once every required main workflow on ``sha`` has passed.

    Any red required run fails the gate. A rerun reports its latest attempt,
    so a flaky job can be re-run instead of waiting for another merge.
    """
    seen: set[str] = set()
    ids: list[int] = []
    pending = False
    for run in runs:
        if run.get("head_sha") != sha or run.get("event") != "push":
            continue
        if run.get("head_branch") != "main":
            continue
        path = str(run.get("path", "")).split("@", 1)[0]
        if path not in REQUIRED:
            continue
        identity = run.get("id")
        if type(identity) is not int or identity <= 0:
            raise ValueError("Missing run identity")
        seen.add(path)
        ids.append(identity)
        if run.get("status") != "completed":
            pending = True
        elif run.get("conclusion") != "success":
            raise ValueError("A main verification did not succeed")
    return seen >= REQUIRED and not pending, sorted(ids)


def collect(fetch: Callable[[str], object], sha: str) -> list[dict[str, object]]:
    """Bound pagination and reject incomplete inventories instead of approving."""
    runs: list[dict[str, object]] = []
    for page in range(1, 11):
        response = fetch(
            f"actions/runs?head_sha={sha}&event=push&per_page=100&page={page}"
        )
        if not isinstance(response, dict):
            raise TypeError("Invalid run inventory")
        items = response.get("workflow_runs")
        if not isinstance(items, list) or not all(
            isinstance(item, dict) for item in items
        ):
            raise ValueError("Invalid run records")
        runs.extend(items)
        if len(items) < PAGE_SIZE:
            if response.get("total_count") != len(runs):
                raise ValueError("Run inventory changed or is incomplete")
            identities = [item.get("id") for item in runs]
            if any(type(identity) is not int for identity in identities) or len(
                set(identities)
            ) != len(identities):
                raise ValueError("Run inventory contains ambiguous identities")
            return runs
    raise ValueError("Run inventory exceeds bounded budget")


def superseded(sha: str, head: str) -> None:
    """Report that main moved on, so later release steps skip this commit."""
    output = os.environ.get("GITHUB_OUTPUT", "")
    if output != "":
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write("superseded=true\n")
    sys.stdout.write(json.dumps({"main_sha": sha, "superseded_by": head}) + "\n")


def main() -> None:
    """Read only GitHub metadata; do not dispatch, retry, approve or publish."""
    sha = os.environ["GITHUB_SHA"]
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or re.fullmatch(r"[a-f0-9]{40}", sha) is None
    ):
        raise ValueError("Release gate requires exact main source identity")
    token = os.environ["GH_TOKEN"]

    def fetch(endpoint: str) -> object:
        request = Request(
            f"https://api.github.com/repos/{REPOSITORY}/{endpoint}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed HTTPS GitHub API
            return json.load(response)

    # Back-to-back merges queue Windows CI; 10 minutes timed out on 2026-11-12.
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        ready, identities = assess(collect(fetch, sha), sha)
        if ready:
            branch = fetch("git/ref/heads/main")
            target = branch.get("object") if isinstance(branch, dict) else None
            head = target.get("sha") if isinstance(target, dict) else None
            if not isinstance(head, str) or re.fullmatch(r"[a-f0-9]{40}", head) is None:
                raise ValueError("Unreadable main branch head")
            if head != sha:
                # The newer push has its own release run, which verifies and
                # publishes; this one stands down without publishing.
                superseded(sha, head)
                return
            alerts = fetch(
                "code-scanning/alerts?ref=refs%2Fheads%2Fmain&state=open&per_page=1"
            )
            if alerts != []:
                raise ValueError("Main has open or unreadable code-scanning findings")
            sys.stdout.write(
                json.dumps({"main_sha": sha, "verified_runs": identities}) + "\n"
            )
            return
        time.sleep(10)
    raise TimeoutError("Exact main verification did not finish within the gate budget")


if __name__ == "__main__":
    main()
