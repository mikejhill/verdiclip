"""Malformed provider observations cannot authorize scrolling or document end."""

from __future__ import annotations

import pytest

from verdiclip.capture.scroll.uia_protocol import (
    UiaDiscovery,
    UiaObservation,
    UiaReference,
)


class Payload:
    """A real-shaped provider response with process-generation precision."""

    TICKS = 638_000_000_000_000_001
    WINDOW = f"1:2:{TICKS}"

    @staticmethod
    def reference() -> dict[str, object]:
        """Use only helper-defined fields and finite physical metrics."""
        return {
            "window_handle": 1,
            "process_id": 2,
            "started_ticks": Payload.TICKS,
            "runtime_id": "42.7",
            "bounds": [0, 0, 96, 48],
            "vertical_percent": 0,
            "view_size": 20,
        }

    @staticmethod
    def nodes() -> list[dict[str, object]]:
        """A scrollbar hit, scrollable content parent, and verified window root."""
        return [
            {
                "identity": Payload.WINDOW + "/bar",
                "window_identity": Payload.WINDOW,
                "parent_identity": Payload.WINDOW + "/42.7",
                "kind": "vertical_scrollbar",
                "bounds": [90, 0, 6, 48],
                "vertically_scrollable": False,
                "reference": None,
            },
            {
                "identity": Payload.WINDOW + "/42.7",
                "window_identity": Payload.WINDOW,
                "parent_identity": Payload.WINDOW + "/root",
                "kind": "content",
                "bounds": [0, 0, 96, 48],
                "vertically_scrollable": True,
                "reference": Payload.reference(),
            },
            {
                "identity": Payload.WINDOW + "/root",
                "window_identity": Payload.WINDOW,
                "parent_identity": None,
                "kind": "window",
                "bounds": [0, 0, 120, 80],
                "vertically_scrollable": False,
                "reference": None,
            },
        ]

    @staticmethod
    def discovery(nodes: list[dict[str, object]]) -> dict[str, object]:
        """Wrap the bounded semantic chain exactly as the helper does."""
        return {
            "result": {
                "window_identity": Payload.WINDOW,
                "hit_identity": Payload.WINDOW + "/bar",
                "nodes": nodes,
            }
        }


def test_discovery_retains_runtime_and_process_generation_without_text() -> None:
    """Provider references remain immutable snapshots, with integer timestamps."""
    discovery = UiaDiscovery.decode(Payload.discovery(Payload.nodes()))
    assert len(discovery.nodes) == 3
    assert len(discovery.references) == 1
    reference = discovery.references[0]
    assert reference.started_ticks == Payload.TICKS
    assert reference.identity == Payload.WINDOW + "/42.7"
    assert UiaReference.decode(reference.encode()) == reference


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("started_ticks", float(Payload.TICKS)),
        ("process_id", True),
        ("vertical_percent", float("nan")),
        ("vertical_percent", 10**400),
        ("vertical_percent", 101),
        ("view_size", 100),
        ("view_size", 0),
        ("bounds", [0, 0, 96.5, 48]),
        ("bounds", [0, 0, 0, 48]),
        ("runtime_id", ""),
    ],
)
def test_invalid_reference_cannot_be_reused(field: str, value: object) -> None:
    """Unknown flags, fractional geometry and rounded process timestamps fail closed."""
    reference = Payload.reference()
    reference[field] = value
    with pytest.raises((ValueError, TypeError)):
        UiaReference.decode(reference)


@pytest.mark.parametrize(
    "defect",
    ["cross_window", "cycle", "duplicate", "missing_root", "reference", "boolean"],
)
def test_inconsistent_ancestry_is_refused(defect: str) -> None:
    """A provider cannot smuggle another target through a valid-looking hit."""
    nodes = Payload.nodes()
    if defect == "cross_window":
        nodes[1]["window_identity"] = "another-window"
    elif defect == "cycle":
        nodes[1]["parent_identity"] = nodes[0]["identity"]
    elif defect == "duplicate":
        nodes[1]["identity"] = nodes[0]["identity"]
    elif defect == "missing_root":
        nodes[-1]["kind"] = "other"
    elif defect == "reference":
        nodes[1]["bounds"] = [0, 0, 80, 48]
    else:
        nodes[1]["vertically_scrollable"] = "true"
    with pytest.raises((ValueError, TypeError)):
        UiaDiscovery.decode(Payload.discovery(nodes))


@pytest.mark.parametrize(
    "change", ["process_id", "started_ticks", "runtime_id", "bounds"]
)
def test_observation_from_changed_target_is_not_admitted(change: str) -> None:
    """Handle reuse, virtualized replacement and resize stop the original session."""
    expected = UiaReference.decode(Payload.reference())
    changed = Payload.reference()
    changed[change] = {
        "process_id": 3,
        "started_ticks": Payload.TICKS + 1,
        "runtime_id": "different",
        "bounds": [0, 0, 95, 48],
    }[change]
    with pytest.raises(ValueError, match="changed"):
        UiaObservation.decode(
            {"result": {"reference": changed, "at_end": False}}, expected
        )


def test_provider_end_flag_must_agree_with_metrics() -> None:
    """A true flag alone cannot complete even the selected scroll range."""
    expected = UiaReference.decode(Payload.reference())
    with pytest.raises(ValueError, match="end evidence"):
        UiaObservation.decode(
            {"result": {"reference": Payload.reference(), "at_end": True}}, expected
        )
    changed = Payload.reference()
    changed["vertical_percent"] = 100
    assert UiaObservation.decode(
        {"result": {"reference": changed, "at_end": True}}, expected
    ).at_end
