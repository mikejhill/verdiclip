"""A single forkserver owns rotation; child messages cannot displace its history."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from tests import mutation_support as support


@pytest.fixture
def handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[RotatingFileHandler]:
    """Keep the real mutmut logger isolated from the test session's logging."""
    result = RotatingFileHandler(tmp_path / "timing.log", maxBytes=180, backupCount=3)
    logger = logging.getLogger("mutmut")
    monkeypatch.setattr(logger, "handlers", [result])
    yield result
    result.close()


def test_ordinary_pytest_does_not_bind_a_handler(
    handler: RotatingFileHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The support plugin remains a no-op outside mutmut."""
    monkeypatch.delitem(sys.modules, "mutmut", raising=False)
    support.ForkserverLogOwner.install()
    assert handler.filters == []


def test_warmup_binds_once_and_candidate_session_cannot_rebind(
    handler: RotatingFileHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All log levels are filtered at the handler using the original owner PID."""
    monkeypatch.setitem(sys.modules, "mutmut", SimpleNamespace())
    monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
    support.pytest_sessionstart()
    assert len(handler.filters) == 1
    owner = handler.filters[0]
    assert isinstance(owner, support.ForkserverLogOwner)
    original_pid = owner.pid
    monkeypatch.setattr(os, "getpid", lambda: original_pid + 1)
    support.pytest_sessionstart()
    assert handler.filters == [owner]
    assert owner.pid == original_pid
    record = logging.LogRecord("mutmut.forkserver", logging.ERROR, "", 0, "", (), None)
    assert owner.filter(record) is False
    record.process = original_pid
    assert owner.filter(record) is True


def test_only_mutmut_rotating_handlers_are_bound(
    handler: RotatingFileHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root logging, ordinary handlers and unrelated filters remain untouched."""
    monkeypatch.setitem(sys.modules, "mutmut", SimpleNamespace())
    sentinel = logging.Filter()
    handler.addFilter(sentinel)
    ordinary = logging.NullHandler()
    root_handlers = list(logging.getLogger().handlers)
    monkeypatch.setattr(logging.getLogger("mutmut"), "handlers", [handler, ordinary])
    support.ForkserverLogOwner.install()
    assert handler.filters[0] is sentinel
    assert isinstance(handler.filters[1], support.ForkserverLogOwner)
    assert ordinary.filters == []
    assert logging.getLogger().handlers == root_handlers


def test_restarted_server_binds_its_new_handler_to_its_own_pid(
    handler: RotatingFileHandler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new server is not tied to the retired server's filter ownership."""
    monkeypatch.setitem(sys.modules, "mutmut", SimpleNamespace())
    support.ForkserverLogOwner.install()
    old_owner = handler.filters[0]
    assert isinstance(old_owner, support.ForkserverLogOwner)
    new_pid = old_owner.pid + 1
    monkeypatch.setattr(os, "getpid", lambda: new_pid)
    new_handler = RotatingFileHandler(tmp_path / "restart.log")
    try:
        monkeypatch.setattr(logging.getLogger("mutmut"), "handlers", [new_handler])
        support.ForkserverLogOwner.install()
        new_owner = new_handler.filters[0]
        assert isinstance(new_owner, support.ForkserverLogOwner)
        assert new_owner.pid == new_pid
        assert new_owner is not old_owner
    finally:
        new_handler.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Real owned POSIX child processes")
def test_real_forked_workers_cannot_rotate_parent_history(tmp_path: Path) -> None:
    """Exercise rotation without forking the Qt-bearing pytest process."""
    probe = "; ".join(
        [
            "import sys",
            "from pathlib import Path",
            "from tests.tooling.test_mutation_log_owner import forked_rotation_probe",
            "forked_rotation_probe(Path(sys.argv[1]))",
        ]
    )
    subprocess.run(  # noqa: S603 - fixed interpreter, owned test helper and scratch path
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-c",
            probe,
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        timeout=10,
    )


def forked_rotation_probe(root: Path) -> None:
    """Run in a disposable interpreter with no Qt application or test threads."""
    sys.modules["mutmut"] = ModuleType("mutmut")
    handler = RotatingFileHandler(root / "timing.log", maxBytes=180, backupCount=3)
    logging.getLogger("mutmut").handlers = [handler]
    support.ForkserverLogOwner.install()
    children = []
    for _ in range(4):
        pid = getattr(os, "fork")()  # noqa: B009 - portable test collection
        if pid == 0:
            try:
                getattr(signal, "alarm")(5)  # noqa: B009 - bound owned POSIX child
                support.ForkserverLogOwner.install()
                for index in range(100):
                    handler.handle(
                        logging.LogRecord(
                            "mutmut.forkserver.worker",
                            logging.ERROR,
                            "",
                            0,
                            f"child-{index}",
                            (),
                            None,
                        )
                    )
            except Exception:  # noqa: BLE001 - report owned child failure via exit
                os._exit(1)
            os._exit(0)
        children.append(pid)
    for index in range(40):
        handler.handle(
            logging.LogRecord(
                "mutmut.forkserver",
                logging.DEBUG,
                "",
                0,
                f"parent-{index:02}",
                (),
                None,
            )
        )
    statuses = [getattr(os, "waitpid")(pid, 0)[1] for pid in children]  # noqa: B009
    assert statuses == [0] * len(children)
    handler.flush()
    root = Path(handler.baseFilename).parent
    assert (root / "timing.log.1").exists()
    lines = [
        line
        for path in root.glob("timing.log*")
        for line in path.read_text().splitlines()
    ]
    assert sorted(lines) == [f"parent-{index:02}" for index in range(40)]
    handler.close()
