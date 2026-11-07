"""Bounded identity contracts, not native replacement qualification."""

from __future__ import annotations

import ctypes as c
from types import SimpleNamespace
from typing import cast, override

import pytest
from scripts import panel_uia_identity as identity
from scripts.panel_uia import Com


def put(
    pointer: object,
    kind: type[c.c_int32] | type[c.c_uint16] | type[c.c_void_p],
    value: int,
) -> None:
    """Write an actual ctypes out parameter just as the ABI does."""
    c.cast(cast("c.c_void_p", pointer), c.POINTER(kind))[0] = kind(value)


class Element(Com):
    """A COM return can contain an owned allocation even on failure."""

    def __init__(self, *, result: int = 0, pointer: int = 42) -> None:
        super().__init__(c.c_void_p(7), lambda _: None)
        self.result, self.output = result, pointer

    @override
    def call(self, slot: int, types: tuple[type[c._CData], ...], *args: object) -> int:
        assert slot == 4
        assert types == (c.POINTER(c.c_void_p),)
        put(args[0], c.c_void_p, self.output)
        return self.result


class Arrays:
    """Tiny OleAut test seam with exact copy/destroy accounting."""

    def __init__(self) -> None:
        self.dim, self.kind, self.size = 1, 3, 4
        self.lower, self.upper = 0, 2
        self.fail = ""
        self.destroyed = 0
        self.reads: list[int] = []

    def call(self, name: str, *args: object) -> int:
        """Return ABI-compatible values without unsafe pointer dereferences."""
        if name == "SafeArrayDestroy":
            self.destroyed += 1
        if name == self.fail:
            return -1
        if name == "SafeArrayGetDim":
            return self.dim
        if name == "SafeArrayGetElemsize":
            return self.size
        if name == "SafeArrayGetVartype":
            put(args[-1], c.c_uint16, self.kind)
        elif name in ("SafeArrayGetLBound", "SafeArrayGetUBound"):
            put(
                args[-1],
                c.c_int32,
                self.lower if name.endswith("LBound") else self.upper,
            )
        elif name == "SafeArrayGetElement":
            index = c.cast(cast("c.c_void_p", args[1]), c.POINTER(c.c_int32))[0]
            self.reads.append(index)
            put(args[-1], c.c_int32, -index)
        return 0

    def library(self) -> c.CDLL:
        """Expose the fixed function names as injectable ABI callables."""
        names = ("Dim", "Elemsize", "Vartype", "LBound", "UBound", "Element", "Destroy")
        functions = {}
        for suffix in names:
            name = "SafeArray" + ("Destroy" if suffix == "Destroy" else "Get" + suffix)
            functions[name] = lambda *args, name=name: self.call(name, *args)
        return cast("c.CDLL", SimpleNamespace(**functions))


def test_valid_runtime_id_destroyed_once() -> None:
    """Verify valid runtime id destroyed once."""
    api = Arrays()
    events: list[dict[str, object]] = []
    assert identity.RuntimeIds(api.library(), events.append).read(Element()) == (
        0,
        -1,
        -2,
    )
    assert api.destroyed == 1
    assert api.reads == [0, 1, 2]
    assert events[-1] == {"event": "call_end", "name": "SafeArrayDestroy"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dim", 0),
        ("dim", 2),
        ("kind", 8),
        ("size", 8),
        ("lower", -1),
        ("lower", 1),
        ("upper", -1),
        ("upper", 64),
        ("upper", 2**31 - 1),
    ],
)
def test_invalid_array_never_copied(field: str, value: int) -> None:
    """Verify invalid array never copied."""
    api = Arrays()
    setattr(api, field, value)
    with pytest.raises(OSError, match=r"Runtime ID|HRESULT"):
        identity.RuntimeIds(api.library(), lambda _: None).read(Element())
    assert api.destroyed == 1
    assert api.reads == []


@pytest.mark.parametrize(
    "failure",
    [
        "SafeArrayGetVartype",
        "SafeArrayGetLBound",
        "SafeArrayGetUBound",
        "SafeArrayGetElement",
        "SafeArrayDestroy",
    ],
)
def test_failed_operation_never_returns_partial_id(failure: str) -> None:
    """Verify failed operation never returns partial id."""
    api = Arrays()
    api.fail = failure
    with pytest.raises(OSError, match=r"Runtime ID|HRESULT"):
        identity.RuntimeIds(api.library(), lambda _: None).read(Element())
    assert api.destroyed == 1


@pytest.mark.parametrize("pointer", [0, 42])
def test_failed_hresult_retains_output_for_cleanup(pointer: int) -> None:
    """Verify failed hresult retains output for cleanup."""
    api = Arrays()
    with pytest.raises(OSError, match="GetRuntimeId"):
        identity.RuntimeIds(api.library(), lambda _: None).read(
            Element(result=-1, pointer=pointer)
        )
    assert api.destroyed == int(pointer != 0)
    assert not api.reads


def test_cleanup_after_logging_failure() -> None:
    """Verify cleanup after logging failure."""
    released = []

    def emit(_event: object) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        identity.cleanup(emit, "release", lambda: released.append(True))
    assert released == [True]


class Node(Com):
    """Distinct interface addresses may identify the same underlying element."""

    def __init__(self, key: int, pid: int = 1) -> None:
        super().__init__(c.c_void_p(id(self)), lambda _: None)
        self.key, self.pid, self.releases = key, pid, 0

    @override
    def release(self) -> None:
        if self.pointer.value is not None:
            self.releases += 1
            self.pointer = c.c_void_p()


def test_cross_pid_ancestry_and_replacement() -> None:
    """Verify cross pid ancestry and replacement."""
    baseline, target, root, parent = Node(1), Node(1, 2), Node(3, 3), Node(3, 3)
    client = cast(
        "identity.IdentityUia",
        SimpleNamespace(
            ids=SimpleNamespace(read=lambda node: (node.key,)),
            same=lambda a, b: a.key == b.key,
            navigate=lambda _node, _slot: parent,
        ),
    )
    assert identity.prove_association(client, target, root, baseline, (1,)) == [
        (1,),
        (3,),
    ]
    assert parent.releases == 1
    assert target.releases == 0
    with pytest.raises(OSError, match="replaced"):
        identity.prove_association(client, Node(2), root, baseline, (1,))


@pytest.mark.parametrize("case", ["foreign", "cycle", "bound"])
def test_ancestry_rejection(case: str) -> None:
    """Verify ancestry rejection."""
    target, root = Node(1), Node(100)
    acquired: list[Node] = []

    def navigate(node: Node, _slot: int) -> Node | None:
        if case == "foreign":
            return None
        result = Node(1 if case == "cycle" else node.key + 1)
        acquired.append(result)
        return result

    client = cast(
        "identity.IdentityUia",
        SimpleNamespace(
            ids=SimpleNamespace(read=lambda node: (node.key,)),
            same=lambda a, b: a.key == b.key,
            navigate=navigate,
        ),
    )
    with pytest.raises(OSError, match=r"absent|cycle|bound"):
        identity.prove_association(client, target, root, target, (1,))
    assert all(node.releases == 1 for node in acquired)
    assert target.releases == 0


def test_getruntime_failure_and_destroy_failure_preserve_first() -> None:
    """Cleanup failure is visible, but never replaces the original provider error."""
    api = Arrays()
    api.fail = "SafeArrayDestroy"
    events: list[dict[str, object]] = []
    with pytest.raises(OSError, match="GetRuntimeId"):
        identity.RuntimeIds(api.library(), events.append).read(Element(result=-2))
    assert api.destroyed == 1
    assert events[-1]["event"] == "cleanup_error"


@pytest.mark.parametrize("result", [0, -1])
def test_interface_failure_allocation_owned_before_admission(
    monkeypatch: pytest.MonkeyPatch, result: int
) -> None:
    """A failed interface acquisition still registers the returned pointer."""
    client = object.__new__(identity.IdentityUia)
    client.refs = []
    client.emit = lambda _: None
    acquired = []

    def own(pointer: c.c_void_p) -> Com:
        acquired.append(pointer.value)
        return Node(1)

    monkeypatch.setattr(client, "own", own)
    if result < 0:
        with pytest.raises(OSError, match="acquire"):
            client.acquire(Element(result=result), 4, (), ())
    else:
        assert client.acquire(Element(), 4, (), ()) is not None
    assert acquired == [42]


@pytest.mark.parametrize("answer", [0, 1])
def test_compare_elements_uses_bool_output(answer: int) -> None:
    """S_OK never implies equality; the separate BOOL answer does."""

    class Compare(Com):
        @override
        def call(
            self, slot: int, types: tuple[type[c._CData], ...], *args: object
        ) -> int:
            assert slot == 3
            put(args[-1], c.c_int32, answer)
            return 0

    client = object.__new__(identity.IdentityUia)
    client.uia = Compare(c.c_void_p(1), lambda _: None)
    assert client.same(Node(1), Node(2)) is bool(answer)


def test_supervisor_requires_all_matching_checkpoints() -> None:
    """The executable receipt cannot substitute flags for actual identity evidence."""
    from scripts.panel_identity_feasibility import admit  # noqa: PLC0415

    records = [
        {
            "event": "identity_checkpoint",
            "checkpoint": index,
            "target_id": [1],
            "root_id": [3],
            "structure": {},
            "state": {},
            "ancestry": [[1], [3]],
        }
        for index in range(3)
    ]
    report = {"read_only": True, "identity_checkpoints_completed": 3}
    admit(records, report, 0)
    records[-1]["target_id"] = [2]
    with pytest.raises(RuntimeError, match="disagree"):
        admit(records, report, 0)
    with pytest.raises(RuntimeError, match="incomplete"):
        admit(records[:-1], report, 0)


@pytest.mark.parametrize(
    "failure", ["none", "replacement", "state", "guard", "cleanup"]
)
def test_actual_checkpoint_releases_client_and_never_writes(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """The executable checkpoint retains native associations and fails closed."""
    from scripts import panel_identity_probe as child  # noqa: PLC0415
    from scripts.panel_uia import ScrollState, Structure  # noqa: PLC0415
    from scripts.panel_uia_probe import NativeGuard, Probe  # noqa: PLC0415

    target, root = Node(1, 2), Node(3, 1)
    structure = Structure(2, 0, 1, "Chrome", (0, 0, 10, 10), offscreen=False)
    state = ScrollState(
        0, 10, 100, 20, horizontal_scrollable=False, vertical_scrollable=True
    )
    closed = []
    records: list[dict[str, object]] = []

    def close() -> None:
        closed.append(True)
        if failure == "cleanup":
            raise OSError("cleanup")

    fresh = SimpleNamespace(
        target=target,
        discover=lambda *_: structure,
        read_state=lambda: None if failure == "state" else state,
        close=close,
    )
    baseline = cast(
        "identity.IdentityUia",
        SimpleNamespace(
            target=target,
            ids=SimpleNamespace(read=lambda _: (3,)),
        ),
    )
    evidence = child.Baseline(baseline, root, (3,), (1,), structure, state)
    probe = cast(
        "Probe",
        SimpleNamespace(
            args=SimpleNamespace(x=5, y=5),
            emit=records.append,
        ),
    )

    def associate(*_args: object) -> list[tuple[int, ...]]:
        if failure == "replacement":
            raise OSError("replacement")
        return [(1,), (3,)]

    def guard_check() -> None:
        if failure == "guard":
            raise OSError("guard")

    guard = cast("NativeGuard", SimpleNamespace(check=guard_check))
    monkeypatch.setattr(child, "IdentityUia", lambda _: fresh)
    monkeypatch.setattr(child, "prove_association", associate)
    if failure == "none":
        child.checkpoint(probe, evidence, guard, 0)
        assert records[0]["ancestry"] == [(1,), (3,)]
    else:
        with pytest.raises(
            (OSError, RuntimeError), match=r"replacement|changed|guard|cleanup"
        ):
            child.checkpoint(probe, evidence, guard, 0)
    assert closed == [True]
    if failure not in ("none", "cleanup"):
        assert not records


@pytest.mark.parametrize("elapsed", [29.9, 30.0, 30.1])
def test_identity_supervisor_final_deadline(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, elapsed: float
) -> None:
    """Late log parsing must not admit a successful process beyond total30s."""
    from pathlib import Path  # noqa: PLC0415

    from scripts import panel_identity_feasibility as supervisor  # noqa: PLC0415
    from scripts.panel_owned_job import OwnedJob  # noqa: PLC0415

    now = [0.0]
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(supervisor, "validate_log", lambda *_a, **_kw: None)

    def events(_path: Path) -> list[dict[str, object]]:
        now[0] = elapsed
        return []

    monkeypatch.setattr(supervisor, "events", events)
    job = cast("OwnedJob", SimpleNamespace(start=lambda *_: (1, 2), wait=lambda *_: 0))
    path = cast("Path", tmp_path)
    if elapsed < supervisor.HELPER_SECONDS:
        assert (
            supervisor.launch(job, path / "fixture", path / "probe")["exit_code"] == 0
        )
    else:
        with pytest.raises(RuntimeError, match="deadline"):
            supervisor.launch(job, path / "fixture", path / "probe")


def test_broken_cleanup_logger_does_not_stop_remaining_releases() -> None:
    """Every owned reference and apartment is released despite failed log writes."""
    released: list[str] = []

    class BadReference(Node):
        @override
        def release(self) -> None:
            released.append("bad")
            raise OSError("release failed")

    class GoodReference(Node):
        @override
        def release(self) -> None:
            released.append("good")

    def emit(_event: object) -> None:
        raise OSError("log failed")

    client = object.__new__(identity.IdentityUia)
    client.refs = [GoodReference(1), BadReference(2)]
    client.initialized = True
    client.emit = emit
    client.ole = cast(
        "c.WinDLL",
        SimpleNamespace(
            CoUninitialize=lambda: released.append("uninitialize"),
        ),
    )
    with pytest.raises(RuntimeError, match="cleanup failed"):
        client.close()
    assert released == ["bad", "good", "uninitialize"]
    client.close()
    assert released == ["bad", "good", "uninitialize"]


def test_primary_array_error_survives_destroy_and_log_failure() -> None:
    """No secondary cleanup path hides the provider's first HRESULT."""
    api = Arrays()
    api.fail = "SafeArrayDestroy"

    def emit(event: dict[str, object]) -> None:
        if event["event"] == "cleanup_error":
            raise OSError("log failed")

    with pytest.raises(OSError, match="GetRuntimeId"):
        identity.RuntimeIds(api.library(), emit).read(Element(result=-2))
    assert api.destroyed == 1


def test_bstr_hresult_survives_failed_free_and_logger() -> None:
    """The original BSTR provider failure wins while returned storage is freed."""
    freed = []

    class Bstr(Com):
        @override
        def call(
            self, slot: int, types: tuple[type[c._CData], ...], *args: object
        ) -> int:
            assert slot == 40
            put(args[0], c.c_void_p, 123)
            return -1

    def emit(_event: object) -> None:
        raise OSError("log failed")

    client = object.__new__(identity.IdentityUia)
    client.emit = emit
    client.automation = cast(
        "c.WinDLL",
        SimpleNamespace(
            SysFreeString=lambda pointer: freed.append(pointer.value),
        ),
    )
    with pytest.raises(OSError, match="FrameworkId"):
        client.structure(Bstr(c.c_void_p(1), emit))
    assert freed == [123]


@pytest.mark.parametrize("upper", [0, 63])
def test_runtime_id_valid_length_edges(upper: int) -> None:
    """Both inclusive length limits copy exactly the validated number of elements."""
    api = Arrays()
    api.upper = upper
    result = identity.RuntimeIds(api.library(), lambda _: None).read(Element())
    assert len(result) == upper + 1
    assert api.destroyed == 1


def test_successful_null_runtime_id_rejected() -> None:
    """S_OK without an ID never becomes an empty identity token."""
    api = Arrays()
    with pytest.raises(OSError, match="Null runtime ID"):
        identity.RuntimeIds(api.library(), lambda _: None).read(Element(pointer=0))
    assert api.destroyed == 0


def test_equal_runtime_id_still_requires_compare_elements() -> None:
    """Contradictory provider comparison fails rather than trusting copied ID alone."""
    target, root = Node(1), Node(3)
    client = cast(
        "identity.IdentityUia",
        SimpleNamespace(
            ids=SimpleNamespace(read=lambda _: (1,)),
            same=lambda *_: False,
        ),
    )
    with pytest.raises(OSError, match="replaced"):
        identity.prove_association(client, target, root, target, (1,))


def test_cycle_error_survives_release_and_logger_failure() -> None:
    """Ancestry rejection remains first even if releasing its temporary node fails."""
    released = []

    class Broken(Node):
        @override
        def release(self) -> None:
            released.append(True)
            raise OSError("release failed")

    def emit(_event: object) -> None:
        raise OSError("log failed")

    target, root, cycle = Node(1), Node(3), Broken(1)
    client = cast(
        "identity.IdentityUia",
        SimpleNamespace(
            ids=SimpleNamespace(read=lambda node: (node.key,)),
            same=lambda a, b: a.key == b.key,
            navigate=lambda *_: cycle,
            emit=emit,
        ),
    )
    with pytest.raises(OSError, match="cycle"):
        identity.prove_association(client, target, root, target, (1,))
    assert released == [True]
