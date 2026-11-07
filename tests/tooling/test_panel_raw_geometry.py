# ruff: noqa: ANN401 - fake ABI receives ctypes byref objects
"""Fake ABI proof for one retained-target raw geometry read, no native execution."""

import ctypes as c
from typing import Any, cast
from unittest.mock import Mock

import pytest
from scripts import panel_matrix_raw_geometry as matrix
from scripts import panel_raw_geometry as raw


def setup() -> tuple[Mock, Mock]:
    """Populate actual ctypes output storage as OleAut32/UIA would."""
    api, target = Mock(), Mock()
    target.read.side_effect = [raw.Rect(10, 20, 110, 220), raw.Rect(10, 20, 110, 220)]

    def property_read(slot: int, types: object, prop: int, pointer: Any) -> int:
        assert slot == 10
        assert prop == 30001
        assert types == (c.c_int32, c.POINTER(raw.Variant))
        value = c.cast(pointer, c.POINTER(raw.Variant)).contents
        value.vt = raw.VT_ARRAY | raw.VT_R8
        value.value.array = 123
        return 0

    target.call.side_effect = property_read
    api.SafeArrayGetDim.return_value = 1
    api.SafeArrayGetElemsize.return_value = 8
    api.VariantClear.return_value = 0

    def out(pointer: Any, kind: Any, value: int) -> int:
        c.cast(pointer, c.POINTER(kind)).contents.value = value
        return 0

    api.SafeArrayGetVartype.side_effect = lambda _, pointer: out(
        pointer, c.c_uint16, raw.VT_R8
    )
    api.SafeArrayGetLBound.side_effect = lambda _, _dim, pointer: out(
        pointer, c.c_int32, 0
    )
    api.SafeArrayGetUBound.side_effect = lambda _, _dim, pointer: out(
        pointer, c.c_int32, 3
    )

    def element(_: object, position: Any, pointer: Any) -> int:
        index = c.cast(position, c.POINTER(c.c_int32)).contents.value
        c.cast(pointer, c.POINTER(c.c_double)).contents.value = (
            10.25,
            20.5,
            100.0,
            199.5,
        )[index]
        return 0

    api.SafeArrayGetElement.side_effect = element
    return api, target


def test_x64_layout_and_exact_fractional_record() -> None:
    """Four doubles remain evidence; typed RECT ABI and pixel bounds do not change."""
    assert c.sizeof(raw.Variant) == 24
    assert raw.Variant.value.offset == 8
    api, target = setup()
    result = raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    assert result["raw_xywh"] == [10.25, 20.5, 100.0, 199.5]
    assert result["raw_hex"] == [
        float(value).hex() for value in cast("list[float]", result["raw_xywh"])
    ]
    assert (
        result["typed_before_ltrb"] == result["typed_after_ltrb"] == [10, 20, 110, 220]
    )
    assert result["geometry_qualified"] is False
    assert target.read.call_args_list == [((43, raw.Rect),), ((43, raw.Rect),)]
    api.VariantClear.assert_called_once()
    assert api.SafeArrayGetElement.call_count == 4


@pytest.mark.parametrize(
    ("field", "value"), [("vt", 0), ("vt", 0x6005), ("reserved", 1), ("array", 0)]
)
def test_invalid_variant_cleared_without_array_reads(field: str, value: int) -> None:
    """Malformed/provider unsupported values never reach an array destination."""
    api, target = setup()
    original = target.call.side_effect

    def call(*args: Any) -> int:
        original(*args)
        variant = c.cast(args[-1], c.POINTER(raw.Variant)).contents
        if field == "reserved":
            variant.reserved[0] = value
        elif field == "array":
            variant.value.array = value
        else:
            variant.vt = value
        return 0

    target.call.side_effect = call
    with pytest.raises(ValueError, match="exact"):
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    api.SafeArrayGetDim.assert_not_called()
    api.VariantClear.assert_called_once()


@pytest.mark.parametrize(
    ("name", "value"),
    [("SafeArrayGetDim", 2), ("SafeArrayGetElemsize", 16), ("SafeArrayGetElemsize", 0)],
)
def test_malformed_descriptor_prevents_element_copy(name: str, value: int) -> None:
    """Storage size is checked before any native element copy into8bytes."""
    api, target = setup()
    getattr(api, name).return_value = value
    with pytest.raises(ValueError, match=r"dimension|element size"):
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    api.SafeArrayGetElement.assert_not_called()
    api.VariantClear.assert_called_once()


@pytest.mark.parametrize(
    "name",
    [
        "SafeArrayGetVartype",
        "SafeArrayGetLBound",
        "SafeArrayGetUBound",
        "SafeArrayGetElement",
    ],
)
def test_failed_array_hresult_preserves_clear(name: str) -> None:
    """Every failed native result still releases the returned VARIANT exactlyonce."""
    api, target = setup()
    getattr(api, name).side_effect = None
    getattr(api, name).return_value = -1
    with pytest.raises(OSError, match=name):
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    api.VariantClear.assert_called_once()


def test_failed_property_with_owned_output_and_failed_cleanup_keeps_first_error() -> (
    None
):
    """A provider may allocate output even when returning failedHRESULT."""
    api, target = setup()
    original = target.call.side_effect

    def fail(*args: Any) -> int:
        original(*args)
        return -1

    target.call.side_effect = fail
    api.VariantClear.return_value = -1
    with pytest.raises(OSError, match="GetCurrentPropertyValue") as error:
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    assert "Variant cleanup failure" in error.value.__notes__[0]
    api.VariantClear.assert_called_once()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1_000_001.0])
def test_nonfinite_or_excessive_double_rejects(value: float) -> None:
    """Never serialize nonfinite or unbounded geometry as usable evidence."""
    api, target = setup()

    def element(_: object, _position: Any, pointer: Any) -> int:
        c.cast(pointer, c.POINTER(c.c_double)).contents.value = value
        return 0

    api.SafeArrayGetElement.side_effect = element
    with pytest.raises(ValueError, match="nonfinite"):
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    api.VariantClear.assert_called_once()


def test_logging_failure_does_not_skip_clear() -> None:
    """Cleanup logging is not allowed to gate native destruction."""
    api, target = setup()

    def emit(event: dict[str, object]) -> None:
        if event.get("raw_geometry") == "VariantClear":
            raise RuntimeError("logger broke")

    with pytest.raises(RuntimeError, match="logger broke"):
        raw.compare(target, Mock(), emit, 30, api=api, clock=lambda: 0.0)
    api.VariantClear.assert_called_once()


def test_deadline_after_allocated_output_still_clears_without_more_reads() -> None:
    """Post-COM deadline checks cannot leak ownership or retry the provider."""
    api, target = setup()
    clock = [0.0]
    original = target.call.side_effect

    def call(*args: Any) -> int:
        original(*args)
        clock[0] = 30.0
        return 0

    target.call.side_effect = call
    with pytest.raises(RuntimeError, match="bound"):
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: clock[0])
    api.SafeArrayGetDim.assert_not_called()
    api.VariantClear.assert_called_once()


@pytest.mark.parametrize(
    ("name", "kind", "value"),
    [
        ("SafeArrayGetVartype", c.c_uint16, 3),
        ("SafeArrayGetLBound", c.c_int32, 1),
        ("SafeArrayGetUBound", c.c_int32, 4),
    ],
)
def test_wrong_array_type_or_bounds_before_copy(
    name: str, kind: Any, value: int
) -> None:
    """Reject storage metadata even when HRESULT succeeds."""
    api, target = setup()

    def out(*args: Any) -> int:
        c.cast(args[-1], c.POINTER(kind)).contents.value = value
        return 0

    getattr(api, name).side_effect = out
    with pytest.raises(ValueError, match="four zero-based"):
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    api.SafeArrayGetElement.assert_not_called()
    api.VariantClear.assert_called_once()


def test_changed_typed_rectangle_rejects() -> None:
    """Raw values cannot override a changed native selected rectangle."""
    api, target = setup()
    target.read.side_effect = [raw.Rect(10, 20, 110, 220), raw.Rect(10, 21, 110, 220)]
    with pytest.raises(ValueError, match="Typed rectangle changed"):
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    api.VariantClear.assert_called_once()


def test_failed_cleanup_without_prior_failure_rejects() -> None:
    """Successful geometry cannot hide failed VARIANT cleanup."""
    api, target = setup()
    api.VariantClear.return_value = -1
    with pytest.raises(OSError, match="VariantClear"):
        raw.compare(target, Mock(), Mock(), 30, api=api, clock=lambda: 0.0)
    api.VariantClear.assert_called_once()


def test_raw_mode_has_no_discovery_retry() -> None:
    """New mode is separate from historical sparse/browser qualification."""
    from scripts.panel_feasibility import probe_arguments  # noqa: PLC0415

    assert probe_arguments({"kind": "owned-edge"}, "raw-geometry") == [
        "--raw-geometry-diagnostic"
    ]


def test_guard_loss_after_property_allocation_stops_reads() -> None:
    """Lost ownership after COM output permits cleanup only."""
    api, target = setup()
    allocated = [False]
    original = target.call.side_effect

    def call(*args: Any) -> int:
        original(*args)
        allocated[0] = True
        return 0

    target.call.side_effect = call

    def guard() -> None:
        if allocated[0]:
            raise RuntimeError("ownership lost")

    with pytest.raises(RuntimeError, match="ownership lost"):
        raw.compare(target, guard, Mock(), 30, api=api, clock=lambda: 0.0)
    api.SafeArrayGetDim.assert_not_called()
    api.VariantClear.assert_called_once()


def test_cleanup_clock_error_still_attempts_clear() -> None:
    """Even a failing cleanup timestamp must not skip VariantClear."""
    api, target = setup()
    cleanup = [False]

    def emit(event: dict[str, object]) -> None:
        if event.get("raw_geometry") == "VariantClear":
            cleanup[0] = True

    def clock() -> float:
        if cleanup[0]:
            raise RuntimeError("clock unavailable")
        return 0.0

    with pytest.raises(RuntimeError, match="clock unavailable"):
        raw.compare(target, Mock(), emit, 30, api=api, clock=clock)
    api.VariantClear.assert_called_once()


def test_parser_rejects_non125_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """No accidental WPF/100% experiment enters the new mode."""
    from scripts import panel_feasibility as supervisor  # noqa: PLC0415

    monkeypatch.setattr(
        supervisor.sys,
        "argv",
        [
            "probe",
            "--case",
            "edge-100",
            "--output",
            "unused",
            "--raw-geometry-diagnostic",
        ],
    )
    with pytest.raises(SystemExit):
        supervisor.parse_arguments()


def test_failed_raw_mode_never_restores() -> None:
    """The raw flag prohibits writes even before initialization completes."""
    from argparse import Namespace  # noqa: PLC0415

    from scripts.panel_uia_probe import Probe  # noqa: PLC0415

    probe = object.__new__(Probe)
    probe.args = Namespace(raw_geometry_diagnostic=True, msaa_diagnostic=False)
    probe.seek = Mock(side_effect=AssertionError("write prohibited"))
    probe.restore_after_failure()
    probe.seek.assert_not_called()


def test_nested_com_events_preserve_balanced_raw_wrappers() -> None:
    """Existing slot events nest without erasing wrapper identity or duration."""
    api, target = setup()
    events = []
    original = target.call.side_effect

    def call(*args: Any) -> int:
        events.append({"event": "call_start", "slot": 10})
        result = original(*args)
        events.append({"event": "call_end", "slot": 10, "seconds": 0.0})
        return int(result)

    target.call.side_effect = call
    reads = iter([raw.Rect(10, 20, 110, 220), raw.Rect(10, 20, 110, 220)])

    def read(slot: int, _kind: Any) -> raw.Rect:
        events.append({"event": "call_start", "slot": slot})
        value = next(reads)
        events.append({"event": "call_end", "slot": slot, "seconds": 0.0})
        return value

    target.read.side_effect = read
    raw.compare(target, Mock(), events.append, 30, api=api, clock=lambda: 0.0)
    stack = []
    for event in events:
        identity = (event.get("slot"), event.get("raw_geometry"))
        if event["event"] == "call_start":
            stack.append(identity)
        elif event["event"] == "call_end":
            assert stack.pop() == identity
    assert not stack
    assert events[-1]["hresult"] == 0


@pytest.mark.parametrize(
    "pending",
    [
        {"slot": 10},
        {"raw_geometry": "GetCurrentPropertyValue"},
        {"raw_geometry": "VariantClear"},
    ],
)
def test_supervisor_bounds_pending_nested_or_wrapper_call(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    pending: dict[str, object],
) -> None:
    """The existing last-event watchdog covers both COM and outer/cleanup calls."""
    from scripts import panel_feasibility as supervisor  # noqa: PLC0415

    records = [
        {
            "event": "call_start",
            "raw_geometry": "GetCurrentPropertyValue",
            "monotonic": 0.0,
        },
        {"event": "call_start", "monotonic": 0.0, **pending},
    ]
    monkeypatch.setattr(supervisor, "events", lambda _: records)
    clock = iter([0.0, 0.0, 4.0])
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(clock))
    job = Mock()
    job.start.return_value = (1, 2)
    job.wait.return_value = None
    with pytest.raises(RuntimeError, match="Native provider call exceeded"):
        supervisor.run_probe(
            job,
            {"kind": "owned-edge", "click": [1, 1], "pid": 1},
            tmp_path,
            experiment="raw-geometry",
        )
    job.start.assert_called_once()


def test_cleanup_hresult_is_emitted_alongside_preserved_original() -> None:
    """Manifest's first exception need not be replaced to retain cleanup evidence."""
    api, target = setup()
    target.call.side_effect = None
    target.call.return_value = -1
    api.VariantClear.return_value = -2
    events = []
    with pytest.raises(OSError, match="GetCurrentPropertyValue"):
        raw.compare(target, Mock(), events.append, 30, api=api, clock=lambda: 0.0)
    assert any(event.get("hresult") == -2 for event in events)
    assert any(event["event"] == "cleanup_error" for event in events)


@pytest.mark.parametrize("slow", ["root_dpi", "dpi_context", "dpi_awareness"])
@pytest.mark.parametrize("elapsed", [3.0, 30.0])
def test_dpi_return_at_deadline_stops_next_native_call(
    monkeypatch: pytest.MonkeyPatch,
    slow: str,
    elapsed: float,
) -> None:
    """A completed slow DPI call cannot admit subsequent native evidence."""
    from types import SimpleNamespace  # noqa: PLC0415

    from scripts import panel_uia_probe as probe_module  # noqa: PLC0415

    clock = [0.0]
    calls = []

    def native(name: str, value: int) -> int:
        calls.append(name)
        if name == slow:
            clock[0] = elapsed
        return value

    user = Mock()
    user.GetDpiForWindow.side_effect = lambda _: native("root_dpi", 96)
    user.GetThreadDpiAwarenessContext.side_effect = lambda: native("dpi_context", 1)
    user.AreDpiAwarenessContextsEqual.side_effect = lambda *_: native(
        "dpi_awareness", 1
    )
    probe = SimpleNamespace(
        report={},
        uia=SimpleNamespace(target=Mock()),
        guard=SimpleNamespace(user=user),
        validate=Mock(),
        emit=Mock(),
    )

    def compare(
        _: object, guard: Any, _emit: Any, _deadline: float
    ) -> dict[str, object]:
        guard()
        raise AssertionError("provider reads must not start")

    monkeypatch.setattr(probe_module, "compare_raw_geometry", compare)
    monkeypatch.setattr(probe_module.time, "monotonic", lambda: clock[0])
    fixture = {
        "kind": "owned-matrix",
        "fixture_schema": matrix.SCHEMA,
        "matrix_provenance": matrix.provenance(),
        "observed_initial_scroll": dict(matrix.HISTORICAL),
        "dpr": 1.25,
        "browser_scale_flag": "1.25",
        "root_hwnd": 1,
    }
    with pytest.raises(RuntimeError, match=r"three seconds|deadline"):
        probe_module.Probe.read_raw_geometry(
            cast("Any", probe), fixture, cast("Any", None), cast("Any", None), 0
        )
    assert calls[-1] == slow
    assert "raw_geometry" not in probe.report
