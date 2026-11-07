"""Portable proof controls; no real desktop actions."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from scripts import panel_paint_feasibility as supervisor
from scripts import panel_paint_fixture as fixture
from scripts import panel_paint_oracle as oracle
from scripts import panel_paint_probe as probe
from scripts.panel_probe_oracle import Pixels
from scripts.panel_uia import ScrollState, Structure

ROOT = (-2, -3, 4, 3)
SELECTED = (-1, -2, 3, 2)


def image(
    points: list[tuple[int, int]] | tuple[()] = (), color: bytes = b"\x01\x00\x00"
) -> Pixels:
    """Image."""
    data = bytearray(6 * 6 * 3)
    for x, y in points:
        index = ((y - ROOT[1]) * 6 + x - ROOT[0]) * 3
        data[index : index + 3] = color
    return Pixels(6, 6, bytes(data))


def painted() -> Pixels:
    """Painted."""
    return image([(x, y) for x in range(-1, 3) for y in range(-2, 2)])


def test_exact_support_including_single_channel_blended_edge() -> None:
    """Exact support including single channel blended edge."""
    result = oracle.paint_support(painted(), image(), painted(), ROOT, SELECTED)
    assert result.changed_pixels == 16
    assert result.bounds == SELECTED
    assert result.edge_counts == (4, 4, 4, 4)
    assert result.corner_observed == (True,) * 4


@pytest.mark.parametrize(
    "rect", [(0, -2, 3, 2), (-1, -1, 3, 2), (-1, -2, 2, 2), (-1, -2, 3, 1)]
)
def test_every_one_pixel_inset_rejects_observed_paint(
    rect: tuple[int, int, int, int],
) -> None:
    """Every one pixel inset rejects observed paint."""
    with pytest.raises(ValueError, match="outside selected"):
        oracle.paint_support(painted(), image(), painted(), ROOT, rect)


@pytest.mark.parametrize("extra", [(-2, -2), (3, 0), (0, -3), (0, 2)])
def test_decoration_outside_native_rectangle_rejects(extra: tuple[int, int]) -> None:
    """Decoration outside native rectangle rejects."""
    changed = image([(x, y) for x in range(-1, 3) for y in range(-2, 2)] + [extra])
    with pytest.raises(ValueError, match="outside selected"):
        oracle.paint_support(changed, image(), changed, ROOT, SELECTED)


def test_no_changes_cannot_prove_invisible_content() -> None:
    """No changes cannot prove invisible content."""
    with pytest.raises(ValueError, match="Empty"):
        oracle.paint_support(image(), image(), image(), ROOT, SELECTED)


def test_unobservable_edge_and_corner_reject() -> None:
    """Unobservable edge and corner reject."""
    interior = image([(0, 0)])
    with pytest.raises(ValueError, match="edge is unobservable"):
        oracle.paint_support(interior, image(), interior, ROOT, SELECTED)
    cross = image([(-1, 0), (2, 0), (0, -2), (0, 1)])
    with pytest.raises(ValueError, match="corner"):
        oracle.paint_support(cross, image(), cross, ROOT, SELECTED)


def test_restore_and_geometry_must_be_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore and geometry must be exact."""
    with pytest.raises(ValueError, match="Restored"):
        oracle.paint_support(painted(), image(), image(), ROOT, SELECTED)
    with pytest.raises(ValueError, match="geometry"):
        oracle.paint_support(Pixels(1, 1, b"abc"), image(), painted(), ROOT, SELECTED)
    monkeypatch.setattr(oracle, "MAX_PIXELS", 35)
    with pytest.raises(ValueError, match="budget"):
        oracle.paint_support(painted(), image(), painted(), ROOT, SELECTED)


@pytest.mark.parametrize(
    "rect", [(True, 0, 2, 2), (0.5, 0, 2, 2), (2, 0, 2, 2), (0, 2, 2, 0)]
)
def test_rectangle_rejects_nonintegral_or_empty(
    rect: tuple[int, int, int, int],
) -> None:
    """Rectangle rejects nonintegral or empty."""
    with pytest.raises(ValueError, match="rectangle"):
        oracle.rectangle(rect)


def state(phase: int = 0, sequence: int = 1) -> dict[str, Any]:
    """State."""
    return {
        "sequence": sequence,
        "state": {"target": {"vertical": 119}},
        "viewport": {"inner": [760, 520]},
        "dpr": 1.25,
        "config": {"width": 520},
        "stream_tick": 0,
        "calibration": [],
        "paint": {
            "phase": phase,
            "failure": None,
            "descendants_same_opacity": True,
            "descendants": 4,
            "styles": [["static"]],
            "opacity": "0" if phase == 1 else "1",
        },
    }


def test_only_explicit_opacity_phase_can_differ() -> None:
    """Only explicit opacity phase can differ."""
    expected = probe.state_key(state())
    for phase in range(3):
        probe.phase_state(state(phase), phase, expected)
    for field, value in [
        ("state", {}),
        ("viewport", {}),
        ("dpr", 1),
        ("stream_tick", 1),
    ]:
        changed = state(1)
        changed[field] = value
        with pytest.raises(RuntimeError, match="invariant"):
            probe.phase_state(changed, 1, expected)
    changed = state(1)
    changed["paint"]["styles"] = [["changed"]]
    with pytest.raises(RuntimeError, match="invariant"):
        probe.phase_state(changed, 1, expected)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("failure", "oops"),
        ("descendants_same_opacity", False),
        ("descendants", 513),
        ("descendants", 0),
    ],
)
def test_control_errors_rejected(key: str, value: object) -> None:
    """Control errors rejected."""
    changed = state()
    changed["paint"][key] = value
    with pytest.raises(RuntimeError, match="control failed"):
        probe.state_key(changed)


def test_fixed_fixture_hook_and_phase_commands(tmp_path: Path) -> None:
    """Fixed fixture hook and phase commands."""
    source = fixture.page()
    assert source.count("paint:paintEvidence()") == 1
    assert "target.style.opacity = next===1 ? '0' : '1'" in source
    assert "if (paintTransition) return;" in source
    assert '"reference_offset": 119' in source
    command = tmp_path / "phase.json"
    for phase in range(3):
        fixture.request_phase(command, phase)
        assert json.loads(command.read_text()) == phase
    with pytest.raises(ValueError, match="skipped or repeated"):
        fixture.request_phase(command, 2)
    with pytest.raises(ValueError, match="Invalid"):
        fixture.request_phase(command, phase=True)


def capture_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[float], dict[str, Any], Any, Any]:
    """Capture setup."""
    clock = [0.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        probe.time, "sleep", lambda dt: clock.__setitem__(0, clock[0] + dt)
    )
    owner = {"root_hwnd": 5, "allowed_rect": [0, 0, 6, 6]}
    monkeypatch.setattr(
        probe, "NativeWindow", lambda: SimpleNamespace(foreground=lambda _: owner)
    )
    monkeypatch.setattr(probe, "retained_identity", Mock())
    monkeypatch.setattr(probe.mss.tools, "to_png", lambda *_a, **_kw: None)
    command = tmp_path / "phase.json"
    fixture.request_phase(command, 0)
    manifest = {
        **owner,
        "owned_process_ids": [123],
        "phase_command": str(command),
        "state_directory": str(tmp_path),
    }
    counter = [0]

    def latest(_: Path) -> dict[str, Any]:
        """Latest."""
        counter[0] += 1
        return state(sequence=counter[0])

    monkeypatch.setattr(probe, "latest_authored_state", latest)
    source = SimpleNamespace(
        grab=lambda _: SimpleNamespace(width=6, height=6, rgb=b"\x00" * 108)
    )
    runner = SimpleNamespace(emit=Mock(), output=tmp_path)
    return (clock, manifest, source, runner)


def test_capture_three_equal_complete_samples_and_quiet_span(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Capture three equal complete samples and quiet span."""
    clock, manifest, source, runner = capture_setup(monkeypatch, tmp_path)
    result = probe.capture_phase(
        runner, cast(Any, None), source, manifest, 0, probe.state_key(state())
    )
    assert result.rgb == b"\x00" * 108
    samples = list(tmp_path.glob("phase-0-sample-*.json"))
    assert len(samples) >= 3
    assert clock[0] >= 0.2
    assert cast(Any, probe.retained_identity).call_count >= 7


def test_capture_exact_deadline_rejected_and_sample_retained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Capture exact deadline rejected and sample retained."""
    clock, manifest, source, runner = capture_setup(monkeypatch, tmp_path)

    def grab(_: object) -> SimpleNamespace:
        """Grab."""
        clock[0] = 2.0
        return SimpleNamespace(width=6, height=6, rgb=b"\x00" * 108)

    source.grab = grab
    with pytest.raises(RuntimeError, match="two-second"):
        probe.capture_phase(
            runner, cast(Any, None), source, manifest, 0, probe.state_key(state())
        )
    assert list(tmp_path.glob("phase-0-sample-*.json"))


def test_phase_transition_never_accepts_other_layout_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase transition never accepts other layout change."""
    _, manifest, source, runner = capture_setup(monkeypatch, tmp_path)
    changed = state()
    changed["state"]["target"]["vertical"] = 120
    monkeypatch.setattr(probe, "latest_authored_state", lambda _: changed)
    with pytest.raises(RuntimeError, match="transition"):
        probe.capture_phase(
            runner, cast(Any, None), source, manifest, 1, probe.state_key(state())
        )


def test_opacity_rejects_offscreen_geometry_and_identity_changes() -> None:
    """Opacity never permits offscreen, geometry or identity changes."""
    structure = Structure(1, 2, 3, "Chrome", (0, 0, 10, 10), offscreen=False)
    current = [replace(structure, offscreen=True)]
    client = SimpleNamespace(
        target=object(),
        ids=SimpleNamespace(read=lambda _: (7,)),
        structure=lambda _: current[0],
        read_state=lambda: ScrollState(
            0, 0, 50, 50, horizontal_scrollable=True, vertical_scrollable=True
        ),
    )
    baseline: Any = SimpleNamespace(
        client=client,
        target_id=(7,),
        root_id=(7,),
        root=object(),
        structure=structure,
        state=ScrollState(
            0, 0, 50, 50, horizontal_scrollable=True, vertical_scrollable=True
        ),
    )
    runner: Any = SimpleNamespace(guard=SimpleNamespace(check=Mock()), emit=Mock())
    with pytest.raises(RuntimeError, match="geometry/state"):
        probe.retained_identity(runner, baseline, 1)
    with pytest.raises(RuntimeError, match="geometry/state"):
        probe.retained_identity(runner, baseline, 2)
    current[0] = replace(structure, rect=(0, 0, 10, 11))
    with pytest.raises(RuntimeError, match="geometry/state"):
        probe.retained_identity(runner, baseline, 1)
    client.ids.read = lambda _: (8,)
    with pytest.raises(RuntimeError, match="identity changed"):
        probe.retained_identity(runner, baseline, 1)


def test_phase_zero_cannot_reset_existing_control(tmp_path: Path) -> None:
    """A stale helper cannot rewind an already advanced authored phase."""
    path = tmp_path / "phase.json"
    fixture.request_phase(path, 0)
    fixture.request_phase(path, 1)
    with pytest.raises(ValueError, match="skipped or repeated"):
        fixture.request_phase(path, 0)


def test_fixture_http_phase_is_fixed_and_bounded(tmp_path: Path) -> None:
    """Exercise the actual server handlers and original telemetry endpoint."""
    source = tmp_path / "authored.html"
    source.write_text(fixture.page())
    command = tmp_path / "phase.json"
    fixture.request_phase(command, 0)
    server = fixture.PaintServer(source, tmp_path / "states", command)
    try:
        with urllib.request.urlopen(server.url + "paint-phase", timeout=2) as response:  # noqa: S310 - owned loopback
            assert response.read() == b"0"
        request = urllib.request.Request(  # noqa: S310 - owned loopback
            server.url + "state", data=b'{"test":1}', method="POST"
        )
        with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310 - owned loopback
            assert response.status == 204
        assert server.states == [{"test": 1}]
        command.write_bytes(b"22")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(server.url + "paint-phase", timeout=2)  # noqa: S310 - owned loopback
    finally:
        server.close()


def test_supervisor_late_log_read_never_admitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The original30s limit is checked after reading evidence, not before it."""
    clock = [0.0]
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(supervisor, "validate_log", Mock())

    def events(_: Path) -> list[dict[str, Any]]:
        """Simulate evidence arriving at the exact deadline."""
        clock[0] = 30.0
        return []

    monkeypatch.setattr(supervisor, "events", events)
    job: Any = SimpleNamespace(start=lambda *_: (1, 2), wait=lambda *_: 0)
    with pytest.raises(RuntimeError, match="deadline"):
        supervisor.launch(job, tmp_path / "manifest", tmp_path / "probe")
    assert not cast(Any, supervisor.validate_log).called


def test_supervisor_final_clock_after_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Even finished helpers cannot be admitted after validation consumes budget."""
    clock = [0.0]
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(supervisor, "events", lambda _: [])
    monkeypatch.setattr(
        supervisor, "validate_log", lambda *_a, **_kw: clock.__setitem__(0, 30.0)
    )
    job: Any = SimpleNamespace(start=lambda *_: (1, 2), wait=lambda *_: 0)
    with pytest.raises(RuntimeError, match="late completion"):
        supervisor.launch(job, tmp_path / "manifest", tmp_path / "probe")


def test_primary_failure_retained_when_source_and_identity_cleanup_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A phase failure still closes both resources and remains the first failure."""
    structure = Structure(1, 2, 3, "Chrome", (0, 0, 6, 6), offscreen=False)
    close = Mock(side_effect=RuntimeError("source close"))
    source = SimpleNamespace(
        monitors=[{}, {"left": 0, "top": 0, "width": 10, "height": 10}], close=close
    )
    client = SimpleNamespace(
        target=object(),
        from_handle=lambda _: object(),
        ids=SimpleNamespace(read=lambda _: (7,)),
        read_state=lambda: ScrollState(
            0, 0, 50, 50, horizontal_scrollable=True, vertical_scrollable=True
        ),
        close=Mock(side_effect=RuntimeError("identity close")),
    )
    monkeypatch.setattr(
        probe, "NativeGuard", lambda *_: SimpleNamespace(root=1, check=Mock())
    )
    monkeypatch.setattr(probe, "IdentityUia", lambda _: client)
    monkeypatch.setattr(probe, "checkpoint", Mock())
    monkeypatch.setattr(probe, "latest_authored_state", lambda _: state())
    monkeypatch.setattr(probe.mss, "MSS", lambda: source)
    failure = RuntimeError("phase failed")
    monkeypatch.setattr(probe, "capture_phase", Mock(side_effect=failure))
    runner: Any = SimpleNamespace(
        args=SimpleNamespace(x=1, y=1),
        emit=Mock(),
        discover_target=lambda: structure,
        check_rect=Mock(),
        report={},
    )
    with pytest.raises(RuntimeError, match="phase failed") as caught:
        probe.execute(
            runner,
            {
                "root_hwnd": 1,
                "state_directory": str(tmp_path),
                "allowed_rect": [0, 0, 6, 6],
            },
        )
    assert caught.value is failure
    close.assert_called_once()
    client.close.assert_called_once()


def admission() -> tuple[
    dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Independent complete supervisor evidence, including nonzero offsets."""
    states = [state(phase) for phase in range(3)]
    for s in states:
        s["config"]["reference_offset"] = 119
        s["state"]["outer"] = {
            "vertical": 0,
            "horizontal": 11,
            "extent_height": 700,
            "viewport_height": 402,
            "extent_width": 1100,
            "viewport_width": 602,
        }
        s["state"]["sibling"] = {
            "vertical": 93,
            "horizontal": 27,
            "extent_height": 900,
            "viewport_height": 402,
            "extent_width": 280,
            "viewport_width": 92,
        }
        s["state"]["target"] = {
            "vertical": 119,
            "horizontal": 147,
            "extent_height": 1000,
            "viewport_height": 300,
            "extent_width": 1000,
            "viewport_width": 500,
        }
    child = {
        "first_failure": None,
        "paint_control_completed": True,
        "selected_before": {"rect": [0, 0, 6, 6]},
        "selected_after": {"rect": [0, 0, 6, 6]},
        "complete_capture_qualified": False,
        "native_scroll_state": {
            "vertical": 119 / 700 * 100,
            "horizontal": 147 / 500 * 100,
        },
    }
    records = [
        {"event": "identity_checkpoint", "checkpoint": 0},
        {"event": "identity_checkpoint", "checkpoint": 1},
        {"event": "call_end", "name": "paint.source.close"},
    ]
    return {"helper": {"exit_code": 0}}, child, states, records


def test_supervisor_admits_only_one_pose_control() -> None:
    """Passing paint support is not full scrolling capture qualification."""
    supervisor.admit(*admission())


@pytest.mark.parametrize(
    "failure",
    ["cleanup", "replacement", "reordered", "nan", "wrongtarget", "missingclose"],
)
def test_supervisor_rejects_stale_or_unqualified_success(failure: str) -> None:
    """Independent guards still override an otherwise completed child receipt."""
    report, child, states, records = admission()
    if failure == "cleanup":
        records.append({"event": "cleanup_error"})
    elif failure == "replacement":
        child["selected_after"] = {"rect": [0, 0, 6, 5]}
    elif failure == "reordered":
        states.reverse()
    elif failure == "nan":
        child["native_scroll_state"]["vertical"] = float("nan")
    elif failure == "wrongtarget":
        child["native_scroll_state"]["vertical"] = 0
    else:
        records.pop()
    with pytest.raises(RuntimeError):
        supervisor.admit(report, child, states, records)


@pytest.mark.parametrize(
    "rect",
    [
        (-(2**31) - 1, 0, 1, 1),
        (0, 0, 2**31, 1),
        (0, -(2**31) - 1, 1, 1),
        (0, 0, 1, 2**31),
    ],
)
def test_signed32_coordinate_overflow_rejected(rect: tuple[int, int, int, int]) -> None:
    """Both physical axes reject out-of-contract coordinate integers."""
    with pytest.raises(ValueError, match="rectangle"):
        oracle.rectangle(rect)


def test_signed32_coordinate_endpoints_allowed() -> None:
    """Coordinates at the signed32 endpoints remain valid before pixel-budget checks."""
    oracle.rectangle((-(2**31), -(2**31), 2**31 - 1, 2**31 - 1))


@pytest.mark.parametrize("axis", ["vertical", "horizontal"])
def test_fixed_pose_rejects_zero_target_offsets(axis: str) -> None:
    """A matching zero native percentage cannot establish the nonzero fingerprint."""
    _, child, states, _ = admission()
    states[0]["state"]["target"][axis] = 0
    child["native_scroll_state"][axis] = 0
    with pytest.raises(RuntimeError, match="nonzero"):
        supervisor.target_association(states[0], child["native_scroll_state"])


@pytest.mark.parametrize("offset", [None, 0, 118, True, float("nan")])
def test_reference_offset_must_be_fixed119(offset: object) -> None:
    """The control cannot silently run a different authored reference pose."""
    _, child, states, _ = admission()
    states[0]["config"]["reference_offset"] = offset
    with pytest.raises(RuntimeError, match="fixed119"):
        supervisor.target_association(states[0], child["native_scroll_state"])


@pytest.mark.parametrize("pane", ["outer", "sibling"])
def test_other_pane_cannot_share_target_fingerprint(pane: str) -> None:
    """Reject even distinct offsets when normalized one-pixel bands overlap."""
    _, child, states, _ = admission()
    target = states[0]["state"]["target"]
    states[0]["state"][pane] = dict(target)
    states[0]["state"][pane]["vertical"] += 1
    with pytest.raises(RuntimeError, match="overlaps"):
        supervisor.target_association(states[0], child["native_scroll_state"])
