"""Hosted retained-source exception diagnostics; import never loads Qt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import traceback
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import BinaryIO, Protocol, cast

MINIMUM_BYTES = 64
EXCEPTION_BYTES = 65536
SOURCE_SHA = "f3066d3cf458a4d8f72a4b679a22c72cc922d191"
LOCK_SHA = "14e1d58cd3d516b6eff172dba8b92beccb93ecd7741add578af7a99adb39da78"
BASE_SELECTION = ["-m", "not native_desktop", "-p", "no:randomly", "-q"]
DIAGNOSTIC_OPTIONS = ["-o", "faulthandler_timeout=10"]
type Hook = Callable[..., object]
type Recorder = Callable[..., None]


class HookModule(Protocol):
    """The exact pytest-qt4.5.0 adapter reviewed for this single-use entry."""

    _except_hook: Hook


class CollectedItem(Protocol):
    """Only read-only source provenance exposed by a collected pytest item."""

    nodeid: str
    path: Path


def emit(value: object) -> None:
    """Emit bounded metadata, never ambient environment or credential values."""
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


class BoundedUtf8:
    """Write and flush the admitted bytes without partial UTF-8 units."""

    def __init__(self, stream: BinaryIO, limit: int = EXCEPTION_BYTES) -> None:
        if limit < MINIMUM_BYTES:
            raise ValueError("Diagnostic budget is too small")
        self.stream = stream
        self.remaining = limit
        self.truncated = False

    def write(self, text: str) -> int:
        """Preserve the text-writer return contract after the byte budget ends."""
        encoded = text.encode("utf-8", errors="backslashreplace")
        accepted = (
            encoded[: self.remaining].decode("utf-8", errors="ignore").encode("utf-8")
        )
        written = self.stream.write(accepted)
        if written != len(accepted):
            raise OSError("Diagnostic stream accepted only a partial write")
        self.remaining -= len(accepted)
        if len(accepted) != len(encoded):
            self.truncated = True
        self.stream.flush()
        return len(text)

    def flush(self) -> None:
        """Expose the narrow text sink used by traceback.print_exception."""
        self.stream.flush()


def tee_exception_hook(original: Hook, record: Recorder) -> Hook:
    """Forward identical objects/results/errors, including recording failures.

    The original hook always runs. If both fail, its error wins with the
    recording failure retained by normal chaining. No control is discarded.
    """

    def hook(*args: object, **kwargs: object) -> object:
        try:
            record(*args, **kwargs)
        finally:
            result = original(*args, **kwargs)
        return result

    return hook


@contextmanager
def installed_exception_tee(module: HookModule, record: Recorder) -> Generator[None]:
    """Restore the precise prior hook even if the pytest entry raises."""
    original = module._except_hook
    module._except_hook = tee_exception_hook(original, record)
    try:
        yield
    finally:
        module._except_hook = original


def unexpected_python_sources(names: list[str]) -> list[str]:
    """Allow the locked venv, reject import-root additions and root shadows."""
    unexpected = []
    for name in names:
        path = PurePosixPath(name.replace("\\", "/"))
        if path.suffix == ".py" and (
            len(path.parts) == 1 or path.parts[0] in {"src", "tests", "scripts"}
        ):
            unexpected.append(name)
    return unexpected


def require_hosted_linux(platform: str, environment: dict[str, str]) -> None:
    """Reject accidental local use; these facts are not a security boundary."""
    admitted = (
        platform == "linux"
        and environment.get("GITHUB_ACTIONS") == "true"
        and environment.get("RUNNER_OS") == "Linux"
    )
    if not admitted:
        raise RuntimeError("Retained Qt diagnostic requires hosted Linux Actions")


def admit_source(options: argparse.Namespace) -> Path:
    """Refuse dirty/unexpected source before application or pytest imports."""
    root = Path(options.source_root).resolve(strict=True)
    if Path.cwd().resolve() != root:
        raise RuntimeError("Run from the exact retained source root")
    if options.source_sha != SOURCE_SHA or options.lock_sha256 != LOCK_SHA:
        raise RuntimeError("Unreviewed source identity requested")
    if not Path("/usr/bin/git").is_file():
        raise RuntimeError("Trusted runner Git unavailable")
    actual = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if actual != SOURCE_SHA:
        raise RuntimeError("Retained checkout identity mismatch")
    subprocess.run(["/usr/bin/git", "diff", "--quiet", "HEAD", "--"], check=True)
    extras = subprocess.run(
        [
            "/usr/bin/git",
            "ls-files",
            "--others",
            "--",
            "src",
            "tests",
            "scripts",
            ":(top,glob)*.py",
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    if unexpected_python_sources(extras):
        raise RuntimeError("Unexpected Python source in retained checkout")
    if hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest() != LOCK_SHA:
        raise RuntimeError("Retained lock identity mismatch")
    return root


def package_origin(package: str, expected: Path) -> str:
    """Resolve a package spec without importing its Python or native code."""
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        raise RuntimeError("Required package has no admitted source")
    if not Path(spec.origin).resolve().is_relative_to(expected):
        raise RuntimeError("Package import is outside its admitted root")
    return spec.origin


def admit_environment(root: Path) -> dict[str, object]:
    """Bind actual imports to the exact retained editable/locked environment."""
    environment = (root / ".venv").resolve(strict=True)
    if Path(sys.prefix).resolve() != environment:
        raise RuntimeError("Python is not the retained locked environment")
    if importlib.metadata.version("pytest-qt") != "4.5.0":
        raise RuntimeError("Re-review the private hook for a new pytest-qt")
    return {
        "application_origin": package_origin("verdiclip", root / "src"),
        "pytest_origin": package_origin("pytest", environment),
        "pytestqt_origin": package_origin("pytestqt", environment),
        "versions": {
            name: importlib.metadata.version(name)
            for name in (
                "pytest",
                "pytest-qt",
                "pyside6-essentials",
                "shiboken6",
            )
        },
    }


@dataclass(eq=False)
class Provenance:
    """Read-only phase labels; no processing, collection reorder or per-phase IO."""

    root: Path
    nodeid: str = ""
    phase: str = "collection"

    def pytest_collection_modifyitems(self, items: list[CollectedItem]) -> None:
        """Reject foreign tests/helpers and retain the ordered node-id digest."""
        for item in items:
            if not Path(item.path).resolve().is_relative_to(self.root / "tests"):
                raise RuntimeError("Collected test outside retained tests")
        for name, module in tuple(sys.modules.items()):
            location = getattr(module, "__file__", None)
            if (
                location
                and name.split(".")[0] in {"verdiclip", "tests", "scripts"}
                and not Path(location).resolve().is_relative_to(self.root)
            ):
                raise RuntimeError("Imported helper outside retained checkout")
        digest = hashlib.sha256("\n".join(item.nodeid for item in items).encode())
        emit({"collected": len(items), "ordered_nodeids_sha256": digest.hexdigest()})

    def pytest_runtest_protocol(
        self,
        item: CollectedItem,
        nextitem: CollectedItem | None,
    ) -> Generator[None, object, object]:
        """Retain test identity until the original protocol finishes or fails."""
        del nextitem
        self.nodeid = item.nodeid
        self.phase = "protocol"
        try:
            return (yield)
        finally:
            self.phase = "between-items"

    def pytest_runtest_setup(self) -> Generator[None, object, object]:
        """Track last-known setup without extra Qt processing or file writes."""
        self.phase = "setup"
        return (yield)

    def pytest_runtest_call(self) -> Generator[None, object, object]:
        """Track last-known call without altering its result or exception."""
        self.phase = "call"
        return (yield)

    def pytest_runtest_teardown(self) -> Generator[None, object, object]:
        """Track last-known teardown without extra event drains."""
        self.phase = "teardown"
        return (yield)


def run_pytest(root: Path, output: Path) -> int:
    """Enter the actual locked plugin only after all hosted/source gates."""
    import pytest  # noqa: PLC0415 - actual pytest/plugin entry is hosted only
    from pytestqt import exceptions  # noqa: PLC0415 - never imported in pure tests

    # Decorator metadata only; no wrapper executes during registration.
    for name in (
        "pytest_runtest_protocol",
        "pytest_runtest_setup",
        "pytest_runtest_call",
        "pytest_runtest_teardown",
    ):
        pytest.hookimpl(wrapper=True, tryfirst=True)(getattr(Provenance, name))
    pytest.hookimpl(trylast=True)(Provenance.pytest_collection_modifyitems)
    provenance = Provenance(root)
    with output.open("xb", buffering=0) as stream:
        writer = BoundedUtf8(stream)

        def record(
            kind: type[BaseException],
            value: BaseException,
            tback: TracebackType | None,
            *args: object,
            **kwargs: object,
        ) -> None:
            del args, kwargs
            writer.write(
                json.dumps({"nodeid": provenance.nodeid, "phase": provenance.phase})
                + "\n"
            )
            traceback.print_exception(kind, value, tback, file=writer)
            writer.flush()

        with installed_exception_tee(cast("HookModule", exceptions), record):
            result = int(
                pytest.main(
                    [*BASE_SELECTION, *DIAGNOSTIC_OPTIONS], plugins=[provenance]
                )
            )
        emit(
            {
                "diagnostic_truncated": writer.truncated,
                "baseline_exit": result,
                "classification": "diagnostic_only_not_a_mutation_score",
            }
        )
        return result


def main(arguments: list[str]) -> int:
    """Refuse accidental local entry before pytest/plugin/application imports."""
    require_hosted_linux(sys.platform, dict(os.environ))
    if not sys.flags.isolated:
        raise RuntimeError("Diagnostic child must use Python isolated mode")
    parser = argparse.ArgumentParser()
    for name in ("source-root", "source-sha", "lock-sha256", "exception-file"):
        parser.add_argument("--" + name, required=True)
    options = parser.parse_args(arguments)
    root = admit_source(options)
    environment = admit_environment(root)
    emit(
        {
            "source_sha": SOURCE_SHA,
            "lock_sha256": LOCK_SHA,
            "reporter": str(Path(__file__).resolve()),
            "python": sys.version,
            "environment": environment,
            "exception_budget_bytes": EXCEPTION_BYTES,
            "base_selection": BASE_SELECTION,
            "diagnostic_only_options": DIAGNOSTIC_OPTIONS,
            "isolated_import_admission": True,
        }
    )
    return run_pytest(root, Path(options.exception_file))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
