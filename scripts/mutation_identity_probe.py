"""Run the locked, unmodified CLI on a tiny fixture before project mutation.

This is tooling qualification, not a product mutation score. The fixture is
owned by this invocation, uses no GUI/input, and retains every stage log.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def require(condition: object, message: str) -> None:
    """Keep contract checks enabled even with Python optimization flags."""
    if not condition:
        raise RuntimeError(message)


def stage(root: Path, name: str, command: list[str], deadline: float) -> None:
    """Run only owned processes, and make timeout an explicit failed probe."""
    if sys.platform != "linux":
        raise RuntimeError("Probe subprocess groups require Linux")
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "MUTANT_UNDER_TEST"):
        env.pop(key, None)
    # Only the tiny fixture disables unrelated plugin discovery. Project checks
    # and the real project mutation invocation keep their existing configuration.
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONUNBUFFERED="1")
    with (root / f"{name}.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(  # noqa: S603 - fixed interpreter/CLI and owned fixture paths
            command,
            cwd=root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            result = child.wait(
                timeout=max(0.001, min(180, deadline - time.monotonic()))
            )
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=3)
            raise RuntimeError(
                f"Probe stage {name} timed out; evidence retained"
            ) from None
    require(result == 0, f"Probe stage {name} failed ({result}); inspect its log")


def preserve_logs(root: Path, repo: Path) -> None:
    """Keep bounded fixture logs on both success and stage failure for CI upload."""
    receipt = repo / "mutation-identity-probe.json"
    if receipt.is_file():
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("status") == "running":
            value["status"] = "failed"
            receipt.write_text(json.dumps(value, indent=2), encoding="utf-8")
    for mode in ("cold", "unchanged", "shifted"):
        for phase in ("prepare", "cli", "verify"):
            log = root / f"{mode}-{phase}.log"
            if log.is_file():
                shutil.copyfile(
                    log, repo / f"mutation-identity-probe-{mode}-{phase}.log"
                )


def run(repo: Path) -> Path:
    """Cold run, trusted unchanged resume, then covered-line ordinal shift."""
    require(sys.platform == "linux", "The locked mutation runtime requires Linux")
    (repo / "mutation-identity-probe.json").write_text(
        json.dumps({"status": "running"}), encoding="utf-8"
    )
    parent = repo / ".test-tmp"
    parent.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="identity-probe-", dir=parent))
    atexit.register(preserve_logs, root, repo)
    source = root / "src/probe"
    source.mkdir(parents=True)
    (root / "tests").mkdir()
    (source / "__init__.py").write_text("", encoding="utf-8")
    original = "def branch(flag):\n    if flag:\n        return 10\n    return 20\n"
    (source / "chosen.py").write_text(original, encoding="utf-8")
    tests = root / "tests/test_branch.py"
    tests.write_text(
        "from probe.chosen import branch\ndef test_both():\n"
        " assert branch(True)==10\n assert branch(False)==20\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        """[tool.pytest.ini_options]
pythonpath=["src", "."]
[tool.mutmut]
source_paths=["src/probe/"]
only_mutate=["src/probe/chosen.py"]
pytest_add_cli_args_test_selection=["tests/"]
pytest_add_cli_args=["-p","no:randomly","-p","no:cacheprovider"]
mutate_only_covered_lines=true
process_isolation="forkserver"
debug=true
""",
        encoding="utf-8",
    )
    helper = root / "identity.py"
    helper.write_bytes((repo / "scripts/mutation_identity.py").read_bytes())
    state = root / ".test-tmp/mutation-identity"
    cache = root / "mutants"
    results: list[dict[str, object]] = []
    deadline = time.monotonic() + 600
    for mode, reused, workers in (
        ("cold", 0, 2),
        ("unchanged", 2, 0),
        ("shifted", 0, 1),
    ):
        if state.exists():
            shutil.move(str(state), str(state.with_name(f"before-{mode}")))
        if mode == "shifted":
            tests.write_text(
                "from probe.chosen import branch\ndef test_false():\n"
                " assert branch(False)==20\n",
                encoding="utf-8",
            )
        stage(
            root,
            f"{mode}-prepare",
            [sys.executable, str(helper), "prepare", "--max-children", "1"],
            deadline,
        )
        receipt = json.loads((state / "receipt.json").read_text(encoding="utf-8"))
        require(receipt["reused"] == reused, f"Unexpected {mode} reuse")
        require(
            not list(cache.rglob("*.py")), "Generated Python leaked into CLI coverage"
        )
        stage(
            root,
            f"{mode}-cli",
            [
                str(Path(sys.executable).with_name("mutmut")),
                "run",
                "--max-children",
                "1",
            ],
            deadline,
        )
        stage(root, f"{mode}-verify", [sys.executable, str(helper), "verify"], deadline)
        actual = (
            (root / f"{mode}-cli.log")
            .read_text(encoding="utf-8")
            .count("worker exit code")
        )
        require(actual == workers, f"Unexpected {mode} worker count {actual}")
        verdicts = json.loads(
            (cache / "src/probe/chosen.py.meta").read_text(encoding="utf-8")
        )["exit_code_by_key"]
        require(
            all(value == 1 for value in verdicts.values()),
            "Tiny fixture mutants were not killed",
        )
        require(
            (source / "chosen.py").read_text(encoding="utf-8") == original,
            "Original source changed",
        )
        results.append(
            {
                "stage": mode,
                "reused": reused,
                "actual_workers": actual,
                "verified": True,
            }
        )
        (root / "result.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
    (repo / "mutation-identity-probe.json").write_text(
        json.dumps({"status": "success", "results": results}, indent=2),
        encoding="utf-8",
    )
    return root


if __name__ == "__main__":
    sys.stdout.write(str(run(Path.cwd())) + "\n")
