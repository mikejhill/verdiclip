"""Pure reporter contracts; never run hosted entry or import pytest-qt/Qt."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
from collections.abc import Generator
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, ClassVar, cast, override

import pytest
from pluggy import PluginManager
from scripts.retained_baseline_qt_exception import (
    MODAL_FAILURE_NODE,
    PYTEST_LOGGING_SHA,
    BoundedUtf8,
    CollectedItem,
    HookModule,
    Provenance,
    TraceView,
    capture_service_snapshot,
    capture_warning_snapshot,
    installed_exception_tee,
    require_hosted_linux,
    tee_exception_hook,
    unexpected_python_sources,
    warning_counts,
)


def test_exact_forwarding_and_result() -> None:
    """Every original object and return value survives the extra observer."""
    calls = []
    args = (object(), object(), object())
    kwargs = {"exceptions": []}
    result = object()

    def original(*a: object, **k: object) -> object:
        calls.append(("original", a, k))
        return result

    def record(*a: object, **k: object) -> None:
        calls.append(("record", a, k))

    assert tee_exception_hook(original, record)(*args, **kwargs) is result
    assert [call[0] for call in calls] == ["record", "original"]
    for _, actual, keywords in calls:
        assert actual == args
        assert keywords["exceptions"] is kwargs["exceptions"]


def test_writer_failure_still_calls_original() -> None:
    """A broken diagnostic never suppresses the existing exception collector."""
    calls = []
    failure = ValueError("writer")

    def record(*_a: object, **_k: object) -> None:
        raise failure

    def original(*_a: object, **_k: object) -> None:
        calls.append(True)

    with pytest.raises(ValueError, match="writer") as caught:
        tee_exception_hook(original, record)(1, 2, 3)
    assert caught.value is failure
    assert calls == [True]


def test_original_control_not_suppressed() -> None:
    """Original control and its identity must propagate unchanged."""
    failure = KeyboardInterrupt("original")

    def original(*_a: object, **_k: object) -> None:
        raise failure

    with pytest.raises(KeyboardInterrupt) as caught:
        tee_exception_hook(original, lambda *_a, **_k: None)(1, 2, 3)
    assert caught.value is failure


def test_dual_failure_retains_writer_context() -> None:
    """Original-hook error wins while the observation failure stays inspectable."""
    writer = ValueError("writer")
    prior = RuntimeError("original")

    def record(*_a: object, **_k: object) -> None:
        raise writer

    def original(*_a: object, **_k: object) -> None:
        raise prior

    with pytest.raises(RuntimeError, match="original") as caught:
        tee_exception_hook(original, record)(1, 2, 3)
    assert caught.value is prior
    assert prior.__context__ is writer


def test_utf8_budget_and_flush() -> None:
    """Truncation never exceeds its byte cap or cuts a UTF-8 sequence."""
    output = io.BytesIO()
    writer = BoundedUtf8(output, 64)
    writer.write("é" * 40)
    writer.write("tail")
    assert len(output.getvalue()) <= 64
    output.getvalue().decode("utf-8")
    assert writer.truncated


def test_flush_failure_propagates() -> None:
    """Failed flushing cannot be silently called successful recording."""

    def fail_flush() -> None:
        raise OSError("flush")

    stream = SimpleNamespace(write=len, flush=fail_flush)
    with pytest.raises(OSError, match="flush"):
        BoundedUtf8(cast(BinaryIO, stream)).write("record")


def test_partial_write_failure_propagates() -> None:
    """Partial binary writes are observation failures, never valid UTF-8 proof."""
    stream = SimpleNamespace(write=lambda value: len(value) - 1, flush=lambda: None)
    with pytest.raises(OSError, match="partial write"):
        BoundedUtf8(cast(BinaryIO, stream)).write("record")


def test_install_restores_exact_hook_on_body_error() -> None:
    """Leaving the scope restores the original callback even on body failure."""

    def original(*_a: object, **_k: object) -> None:
        return

    module = SimpleNamespace(_except_hook=original)

    def fail_body() -> None:
        raise ValueError("body")

    def invoke() -> None:
        with installed_exception_tee(cast(HookModule, module), lambda *_a, **_k: None):
            assert module._except_hook is not original
            fail_body()

    with pytest.raises(ValueError, match="body"):
        invoke()
    assert module._except_hook is original


def test_collection_preserves_order_and_digest() -> None:
    """Read-only provenance never sorts or mutates the selected tests."""
    root = Path(__file__).resolve().parents[2]
    protocol = Provenance(root)
    items = [
        cast(CollectedItem, SimpleNamespace(path=root / "tests/b.py", nodeid="b")),
        cast(CollectedItem, SimpleNamespace(path=root / "tests/a.py", nodeid="a")),
    ]
    output = io.StringIO()
    with redirect_stdout(output):
        protocol.pytest_collection_modifyitems(items)
    assert [item.nodeid for item in items] == ["b", "a"]
    assert (
        json.loads(output.getvalue())["ordered_nodeids_sha256"]
        == hashlib.sha256(b"b\na").hexdigest()
    )


def test_collection_rejects_external_test() -> None:
    """Foreign source is rejected before executing any test body."""
    root = Path(__file__).resolve().parents[2]
    item = cast(
        CollectedItem, SimpleNamespace(path=root.parent / "external.py", nodeid="x")
    )
    with pytest.raises(RuntimeError, match="outside retained tests"):
        Provenance(root).pytest_collection_modifyitems([item])


def test_phase_wrapper_preserves_result() -> None:
    """Phase metadata leaves the yielded hook result untouched."""
    protocol = Provenance(Path(__file__).resolve().parents[2])
    gen = protocol.pytest_runtest_call()
    assert next(gen) is None
    assert protocol.phase == "call"
    result = object()
    with pytest.raises(StopIteration) as caught:
        gen.send(result)
    assert caught.value.value is result


def test_protocol_preserves_failure_and_finally_phase() -> None:
    """Failure forwarding still retires last-known phase state."""
    protocol = Provenance(Path(__file__).resolve().parents[2])
    item = cast(CollectedItem, SimpleNamespace(nodeid="old::item"))
    gen = protocol.pytest_runtest_protocol(item, None)
    next(gen)
    assert protocol.current_item is item
    failure = ValueError("test error")
    with pytest.raises(ValueError, match="test error") as caught:
        gen.throw(failure)
    assert caught.value is failure
    assert protocol.nodeid == "old::item"
    assert protocol.phase == "between-items"
    assert protocol.current_item is None


def test_locked_venv_is_not_source_additions() -> None:
    """Dependency source is outside the extra application/import-root policy."""
    assert (
        unexpected_python_sources(
            [
                ".venv/lib/python3.13/site-packages/pytest/__init__.py",
                ".venv/Scripts/activate_this.py",
                "src/verdiclip/__pycache__/x.pyc",
            ]
        )
        == []
    )


def test_extra_roots_and_python_shadows_are_rejected() -> None:
    """Ignored source and root-level import shadows cannot join the old suite."""
    names = [
        "src/verdiclip/extra.py",
        "tests/extra.py",
        "scripts/extra.py",
        "pytest.py",
        "sitecustomize.py",
        "conftest.py",
    ]
    assert unexpected_python_sources(names) == names


def test_host_admission_with_fake_facts() -> None:
    """All pure mismatched facts fail before any real environment is read."""
    for platform, environment in [
        ("win32", {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux"}),
        ("linux", {}),
        ("linux", {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows"}),
    ]:
        with pytest.raises(RuntimeError):
            require_hosted_linux(platform, environment)
    require_hosted_linux("linux", {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux"})


def test_main_guard_precedes_entry_and_imports() -> None:
    """AST proves real admission happens before source/environment/pytest entry."""
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/retained_baseline_qt_exception.py"
    )
    tree = ast.parse(path.read_text())
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    first = main.body[1]
    assert isinstance(first, ast.Expr)
    assert isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "require_hosted_linux"
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(main)
    )


def test_provenance_plugin_preserves_identity_in_plugin_inventory() -> None:
    """Pluggy can inventory the observer without unhashable dataclass failures."""
    root = Path(__file__).resolve().parents[2]
    plugin = Provenance(root)
    same_facts = Provenance(root)
    manager = PluginManager("pytest")
    manager.register(plugin)
    assert plugin in manager.get_plugins()
    assert plugin is not same_facts
    assert plugin != same_facts


class NoInspection:
    """Record forbidden protocol calls even when the reader catches errors."""

    attempts: ClassVar[list[str]] = []

    @override
    def __repr__(self) -> str:
        """Record representation without raising from a special method."""
        self.attempts.append("repr")
        return "<uninspected>"

    def __bool__(self) -> bool:
        """Record truthiness conversion of any unapproved storage field."""
        self.attempts.append("bool")
        return False

    def __len__(self) -> int:
        """Record length conversion outside an exact builtin list."""
        self.attempts.append("len")
        return 0


@pytest.fixture(autouse=True)
def no_object_protocol_calls() -> Generator[None]:
    """Fail every reader test if any hostile object was inspected."""
    NoInspection.attempts.clear()
    yield
    assert NoInspection.attempts == []


def test_object_protocol_observer_records_all_attempts() -> None:
    """The observer reports calls without depending on uncaught exceptions."""
    value = NoInspection()
    try:
        assert repr(value) == "<uninspected>"
        assert not value
        assert len(value) == 0
        assert NoInspection.attempts == ["repr", "bool", "len"]
    finally:
        NoInspection.attempts.clear()


def overlay_trace(
    service: object,
    *,
    line: int = 137,
    name: str = "__init__",
    qualname: str = "Overlay.__init__",
    file: str = "tests/ux/test_capture_journeys.py",
) -> TraceView:
    """Build only fake traceback ports; no application/Qt frames execute."""
    root = Path(__file__).resolve().parents[2]
    frame = SimpleNamespace(
        f_code=SimpleNamespace(
            co_filename=str(root / file), co_name=name, co_qualname=qualname
        ),
        f_locals={"service": service, "unrelated_secret": NoInspection()},
    )
    return cast(
        "TraceView", SimpleNamespace(tb_frame=frame, tb_lineno=line, tb_next=None)
    )


def test_snapshot_pending_after_processing_is_not_disappearance_proof() -> None:
    """Current pending/empty state is exported without inventing a history."""
    service = SimpleNamespace(
        _pending=NoInspection(),
        _starting=False,
        _overlays=[],
        _cancelled_start=False,
        _deactivated=False,
        secret=NoInspection(),
    )
    root = Path(__file__).resolve().parents[2]
    snapshot = capture_service_snapshot(root, IndexError, overlay_trace(service))
    assert snapshot == {
        "pending_present": True,
        "starting": False,
        "overlay_count": 0,
        "cancelled_start": False,
        "deactivated": False,
    }
    assert json.loads(json.dumps(snapshot)) == snapshot


def test_snapshot_cancel_flag_is_recorded_without_history_inference() -> None:
    """A cancellation flag is a primitive fact, not proof overlays existed."""
    service = SimpleNamespace(
        _pending=None,
        _starting=False,
        _overlays=[],
        _cancelled_start=True,
        _deactivated=True,
    )
    snapshot = capture_service_snapshot(
        Path(__file__).resolve().parents[2],
        IndexError,
        overlay_trace(service),
    )
    assert snapshot == {
        "pending_present": False,
        "starting": False,
        "overlay_count": 0,
        "cancelled_start": True,
        "deactivated": True,
    }


def test_snapshot_rejects_nonprimitive_fields_without_converting_them() -> None:
    """Unknown field shapes stay null; no repr/bool/len fallback is allowed."""
    service = SimpleNamespace(
        _starting=1,
        _overlays=NoInspection(),
        _cancelled_start="true",
        _deactivated=NoInspection(),
    )
    snapshot = capture_service_snapshot(
        Path(__file__).resolve().parents[2],
        IndexError,
        overlay_trace(service),
    )
    assert snapshot == dict.fromkeys(
        [
            "pending_present",
            "starting",
            "overlay_count",
            "cancelled_start",
            "deactivated",
        ],
    )


def test_snapshot_counts_plain_list_without_touching_overlays() -> None:
    """Only Python list length is read, never widget properties or elements."""
    service = SimpleNamespace(_overlays=[NoInspection(), NoInspection()])
    snapshot = capture_service_snapshot(
        Path(__file__).resolve().parents[2],
        IndexError,
        overlay_trace(service),
    )
    assert snapshot is not None
    assert snapshot["overlay_count"] == 2


@pytest.mark.parametrize("mismatch", ["kind", "line", "name", "qualname", "file"])
def test_snapshot_filters_before_reading_any_frame_locals(mismatch: str) -> None:
    """Every exact retained-frame guard must match before touching service."""
    trace = overlay_trace(object())
    kind: type[BaseException] = IndexError
    if mismatch == "kind":
        kind = ValueError
    elif mismatch == "line":
        trace = overlay_trace(object(), line=138)
    elif mismatch == "name":
        trace = overlay_trace(object(), name="other")
    elif mismatch == "qualname":
        trace = overlay_trace(object(), qualname="Other.__init__")
    else:
        trace = overlay_trace(object(), file="foreign/test_capture_journeys.py")
    assert (
        capture_service_snapshot(Path(__file__).resolve().parents[2], kind, trace)
        is None
    )


def test_snapshot_walks_only_traceback_identifiers_outside_target() -> None:
    """Unmatched outer-frame locals are never inspected or serialized."""
    target = overlay_trace(SimpleNamespace(_pending=None))
    outer = SimpleNamespace(
        tb_frame=SimpleNamespace(
            f_code=SimpleNamespace(
                co_filename="foreign.py",
                co_name="outer",
                co_qualname="outer",
            )
        ),
        tb_lineno=1,
        tb_next=target,
    )
    result = capture_service_snapshot(
        Path(__file__).resolve().parents[2],
        IndexError,
        cast("TraceView", outer),
    )
    assert result is not None
    assert result["pending_present"] is False
    assert capture_service_snapshot(Path.cwd(), IndexError, None) is None


def test_snapshot_observation_keeps_exact_original_hook_forwarding() -> None:
    """Adding the narrow state read does not replace the original hook call."""
    trace = overlay_trace(SimpleNamespace(_pending=NoInspection(), _overlays=[]))
    value = IndexError("tuple index out of range")
    calls = []

    def original(*args: object, **kwargs: object) -> str:
        calls.append((args, kwargs))
        return "original-result"

    def record(*_args: object, **_kwargs: object) -> None:
        snapshot = capture_service_snapshot(
            Path(__file__).resolve().parents[2], IndexError, trace
        )
        assert snapshot is not None

    assert (
        tee_exception_hook(original, record)(IndexError, value, trace)
        == "original-result"
    )
    assert calls == [((IndexError, value, trace), {})]


def test_snapshot_storage_failure_still_forwards_original_hook() -> None:
    """A failed diagnostic cannot prevent the existing exception collector."""
    trace = overlay_trace(object())
    calls = []

    def record(*_args: object, **_kwargs: object) -> None:
        capture_service_snapshot(Path(__file__).resolve().parents[2], IndexError, trace)

    def original(*_args: object, **_kwargs: object) -> None:
        calls.append(True)

    with pytest.raises(TypeError):
        tee_exception_hook(original, record)(IndexError, IndexError("bad"), trace)
    assert calls == [True]


class CaptureError(Exception):
    """Pure retained exception stand-in; formatting it would change semantics."""

    __module__ = "verdiclip.exceptions"

    @override
    def __str__(self) -> str:
        """Prove the warning reader never invokes exception formatting."""
        raise AssertionError("exception str is forbidden")


class FakeStash:
    """Read-only identity-checked stash port; no pytest logging plugin imports."""

    def __init__(self, key: object, value: object) -> None:
        self.key = key
        self.value = value
        self.reads = 0

    def get(self, key: object, default: object) -> object:
        """Permit only the admitted opaque key and the no-value default."""
        assert key is self.key
        assert default is None
        self.reads += 1
        return self.value


def capture_warning(message: object) -> logging.LogRecord:
    """Construct only an in-memory record; no handler, filter or logger call."""
    return logging.LogRecord(
        "verdiclip.capture.service",
        logging.WARNING,
        "synthetic.py",
        1,
        "Capture failed: %s",
        (CaptureError(message),),
        None,
    )


def warning_provenance(stored: object) -> tuple[Provenance, object, FakeStash]:
    """Set a synthetic current CALL item without entering pytest or Qt."""
    root = Path(__file__).resolve().parents[2]
    key = object()
    stash = FakeStash(key, stored)
    item = cast(CollectedItem, SimpleNamespace(stash=stash))
    provenance = Provenance(
        root, nodeid=MODAL_FAILURE_NODE, phase="call", current_item=item
    )
    return provenance, key, stash


def test_existing_call_warning_codes_without_formatting_or_export() -> None:
    """Only two fixed reason codes and an unknown count may leave the reader."""
    records = [
        capture_warning("Another window is holding input; try capturing again"),
        capture_warning("Close the open popup before capturing"),
        capture_warning("synthetic-secret-not-to-export"),
        capture_warning(NoInspection()),
    ]
    stored = {"call": records}
    provenance, key, stash = warning_provenance(stored)
    summary = capture_warning_snapshot(
        provenance, key, IndexError, overlay_trace(object())
    )
    assert summary == {
        "call_records_available": True,
        "INPUT_OWNER_REFUSED": 1,
        "POPUP_REFUSED": 1,
        "unknown_warning_count": 2,
        "records_shape_valid": True,
        "records_truncated": False,
    }
    assert "synthetic-secret" not in json.dumps(summary)
    assert stash.value is stored
    assert stored["call"] is records
    assert stash.reads == 1


@pytest.mark.parametrize("stored", [None, {}, {"call": None}, {"call": ()}])
def test_missing_call_records_are_unavailable_not_absence_proof(stored: object) -> None:
    """Missing capture storage cannot be interpreted as no warning/failure."""
    provenance, key, _stash = warning_provenance(stored)
    assert capture_warning_snapshot(
        provenance, key, IndexError, overlay_trace(object())
    ) == {
        "call_records_available": False,
    }


@pytest.mark.parametrize("guard", ["node", "phase", "kind", "frame"])
def test_warning_guard_rejects_before_any_stash_access(guard: str) -> None:
    """Only the exact reproduced CALL node/error/frame may inspect logs."""
    provenance, key, stash = warning_provenance({"call": []})
    kind: type[BaseException] = IndexError
    trace = overlay_trace(object())
    if guard == "node":
        provenance.nodeid = "other::test"
    elif guard == "phase":
        provenance.phase = "teardown"
    elif guard == "kind":
        kind = ValueError
    else:
        trace = overlay_trace(object(), line=138)
    assert capture_warning_snapshot(provenance, key, kind, trace) is None
    assert stash.reads == 0


def test_retired_current_item_makes_call_records_unavailable() -> None:
    """The reader does not recover a stale item after protocol retirement."""
    provenance, key, stash = warning_provenance({"call": []})
    provenance.current_item = None
    assert capture_warning_snapshot(
        provenance, key, IndexError, overlay_trace(object())
    ) == {
        "call_records_available": False,
    }
    assert stash.reads == 0


def test_unknown_records_and_loggers_do_not_export_raw_data() -> None:
    """Unqualified records are labelled; other loggers and levels are ignored."""
    other = capture_warning("Close the open popup before capturing")
    other.name = "other.logger"
    info = capture_warning("Close the open popup before capturing")
    info.levelno = logging.INFO
    raw = logging.LogRecord(
        "verdiclip.capture.service",
        logging.WARNING,
        "synthetic.py",
        1,
        NoInspection(),
        (),
        None,
    )
    summary = warning_counts([other, info, raw, NoInspection()])
    assert summary["INPUT_OWNER_REFUSED"] == 0
    assert summary["POPUP_REFUSED"] == 0
    assert summary["unknown_warning_count"] == 1
    assert summary["records_shape_valid"] is False


def test_warning_limit_labels_incomplete_record_scan() -> None:
    """A capped scan cannot silently imply no later warning existed."""
    record = capture_warning("unknown")
    summary = warning_counts([record] * 1001)
    assert summary["records_truncated"] is True
    assert summary["unknown_warning_count"] == 1000


def test_stash_error_still_forwards_original_exception_hook() -> None:
    """Observer lookup failure cannot prevent the existing exception collector."""
    provenance, key, _stash = warning_provenance({"call": []})
    original_item = provenance.current_item
    assert original_item is not None
    failure = ValueError("stash read")

    def fail_get(_key: object, _default: object) -> object:
        raise failure

    provenance.current_item = cast(
        CollectedItem, SimpleNamespace(stash=SimpleNamespace(get=fail_get))
    )
    calls = []

    def record(*_args: object, **_kwargs: object) -> None:
        capture_warning_snapshot(provenance, key, IndexError, overlay_trace(object()))

    def original(*_args: object, **_kwargs: object) -> None:
        calls.append(True)

    with pytest.raises(ValueError, match="stash read") as caught:
        tee_exception_hook(original, record)(IndexError, IndexError("bad"), None)
    assert caught.value is failure
    assert calls == [True]


def test_private_pytest_call_log_stash_matches_reviewed_locked_source() -> None:
    """Read installed source only; never load pytest's logging plugin here."""
    source = Path(pytest.__file__).parent.parent / "_pytest/logging.py"
    text = source.read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode()).hexdigest() == PYTEST_LOGGING_SHA
    tree = ast.parse(text)
    targets = [
        ast.unparse(node.targets[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and len(node.targets) == 1
    ]
    assert "item.stash[caplog_records_key]" in targets
    assert "item.stash[caplog_records_key][when]" in targets
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and ast.unparse(node.targets[0]) == "item.stash[caplog_records_key][when]"
    ]
    assert len(assignments) == 1
    assert ast.unparse(assignments[0].value) == "caplog_handler.records"
    deleted = [
        ast.unparse(target)
        for node in ast.walk(tree)
        if isinstance(node, ast.Delete)
        for target in node.targets
    ]
    assert "item.stash[caplog_records_key]" in deleted


def test_nonstring_exception_module_does_not_run_comparison() -> None:
    """Unknown class metadata is not allowed to execute equality protocols."""

    class HostileModule:
        @override
        def __hash__(self) -> int:
            """Retain identity hashing; comparison must never be invoked."""
            return object.__hash__(self)

        @override
        def __eq__(self, _other: object) -> bool:
            raise AssertionError("module equality is forbidden")

        @override
        def __ne__(self, _other: object) -> bool:
            raise AssertionError("module inequality is forbidden")

    error_type = type("CaptureError", (Exception,), {"__module__": HostileModule()})
    record = logging.LogRecord(
        "verdiclip.capture.service",
        logging.WARNING,
        "synthetic.py",
        1,
        "Capture failed: %s",
        (error_type("Close the open popup before capturing"),),
        None,
    )
    summary = warning_counts([record])
    assert summary["unknown_warning_count"] == 1
    assert summary["INPUT_OWNER_REFUSED"] == 0
    assert summary["POPUP_REFUSED"] == 0
