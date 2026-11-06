"""Identity-free initial leaves cannot become saved or selectable targets."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

import pytest
from tests.capture.test_scroll_uia_protocol import Payload

from verdiclip.capture.scroll.targets import (
    NodeKind,
    SemanticNode,
    SemanticTargets,
    TargetIntent,
    UnresolvedHit,
)
from verdiclip.capture.scroll.uia_protocol import UiaDiscovery, UiaReference
from verdiclip.geometry import Point, Rect


def unresolved_payload() -> dict[str, object]:
    """An unresolved list item points to an identified content/window suffix."""
    return {
        "window_identity": Payload.WINDOW,
        "hit_identity": None,
        "nodes": Payload.nodes()[1:],
        "unresolved_hit": {
            "kind": "other",
            "window_identity": Payload.WINDOW,
            "parent_identity": Payload.WINDOW + "/42.7",
            "bounds": [0, 0, 96, 48],
            "vertically_scrollable": False,
        },
    }


def test_no_unresolved_identity_or_reference_is_created() -> None:
    """The actual hit stays identity-free; only the selected suffix has a ref."""
    result = unresolved_payload()
    discovery = UiaDiscovery.decode({"result": result})
    assert discovery.hit_identity is None
    assert discovery.unresolved_hit is not None
    assert len(discovery.nodes) == 2
    assert len(discovery.references) == 1
    assert discovery.references[0].identity == discovery.nodes[0].identity
    with pytest.raises(ValueError, match="missing"):
        UiaReference.decode(result["unresolved_hit"])


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("kind", "content"),
        ("kind", "vertical_scrollbar"),
        ("parent_identity", "foreign"),
        ("window_identity", "foreign"),
        ("vertically_scrollable", 0),
        ("vertically_scrollable", True),
        ("bounds", [0, 0, 0, 1]),
        ("bounds", [0, 0, 1.5, 2]),
        ("identity", "fake"),
        ("reference", Payload.reference()),
    ],
)
def test_unresolved_wire_has_no_candidate_or_identity_fields(
    key: str, value: object
) -> None:
    """Reject altered parent/ownership, candidate claims and hidden references."""
    result = unresolved_payload()
    cast(dict[str, object], result["unresolved_hit"])[key] = value
    with pytest.raises(ValueError, match="UI Automation"):
        UiaDiscovery.decode({"result": result})


@pytest.mark.parametrize("value", [None, {}, [], False])
def test_null_hit_requires_complete_unresolved_proof(value: object) -> None:
    """A null actual hit cannot use the legacy identified-hit path."""
    result = unresolved_payload()
    result["unresolved_hit"] = value
    with pytest.raises(ValueError, match="unresolved hit"):
        UiaDiscovery.decode({"result": result})


def test_hit_representations_are_exclusive() -> None:
    """Never relabel the first ancestor as the actual pointer hit."""
    result = unresolved_payload()
    result["hit_identity"] = Payload.WINDOW + "/42.7"
    with pytest.raises(ValueError, match="conflicting hit"):
        UiaDiscovery.decode({"result": result})


@pytest.mark.parametrize("count", [31, 32])
def test_leaf_consumes_ancestry_budget(count: int) -> None:
    """Only31 identified suffix nodes may accompany one unresolved leaf."""
    result = unresolved_payload()
    nodes = []
    for index in range(count):
        nodes.append(  # noqa: PERF401 - construct each linked node explicitly
            {
                "identity": f"{Payload.WINDOW}/{index}",
                "window_identity": Payload.WINDOW,
                "parent_identity": f"{Payload.WINDOW}/{index + 1}"
                if index + 1 < count
                else None,
                "kind": "other" if index + 1 < count else "window",
                "bounds": [0, 0, 96, 48],
                "vertically_scrollable": False,
                "reference": None,
            }
        )
    result["nodes"] = nodes
    cast(dict[str, object], result["unresolved_hit"])["parent_identity"] = nodes[0][
        "identity"
    ]
    if count == 32:
        with pytest.raises(ValueError, match="unresolved hit"):
            UiaDiscovery.decode({"result": result})
    else:
        assert len(UiaDiscovery.decode({"result": result}).nodes) == 31


@pytest.mark.parametrize(
    "parent", [NodeKind.CONTENT, NodeKind.VERTICAL_SCROLLBAR, NodeKind.OTHER]
)
def test_unresolved_other_preserves_identified_other_semantics(
    parent: NodeKind,
) -> None:
    """Normal content hits stay WINDOW; explicit and scrollbar ancestry agree."""
    bounds = Rect(0, 0, 100, 100)
    suffix = [
        SemanticNode(
            "parent", "w", parent, bounds, "content", parent is NodeKind.CONTENT
        ),
        SemanticNode(
            "content", "w", NodeKind.CONTENT, bounds, "root", vertically_scrollable=True
        ),
        SemanticNode("root", "w", NodeKind.WINDOW, bounds),
    ]
    resolved = SemanticTargets(
        [SemanticNode("leaf", "w", NodeKind.OTHER, bounds, "parent"), *suffix]
    )
    unresolved = SemanticTargets(suffix)
    proof = UnresolvedHit("w", bounds, "parent")
    point = Point(50, 50)
    assert resolved.normal_click("leaf", point, "w") == unresolved.normal_click(
        None, point, "w", proof
    )
    assert resolved.explicit_mode("leaf", point, "w") == unresolved.explicit_mode(
        None, point, "w", proof
    )
    if parent is NodeKind.CONTENT:
        assert (
            unresolved.normal_click(None, point, "w", proof).intent
            is TargetIntent.WINDOW
        )
    assert all(
        candidate.identity != "leaf"
        for candidate in unresolved.explicit_mode(None, point, "w", proof).candidates
    )


@pytest.mark.parametrize("fault", ["point", "window", "parent", "both", "parent-point"])
def test_unresolved_hit_semantics_fail_closed(fault: str) -> None:
    """Point/owner validation applies to both original leaf and followed parent."""
    discovery = UiaDiscovery.decode({"result": deepcopy(unresolved_payload())})
    proof = discovery.unresolved_hit
    assert proof is not None
    point = Point(5, 5)
    nodes = list(discovery.nodes)
    if fault == "point":
        proof = UnresolvedHit(
            proof.window_identity, Rect(100, 100, 1, 1), proof.parent_identity
        )
    elif fault == "window":
        proof = UnresolvedHit("foreign", proof.bounds, proof.parent_identity)
    elif fault == "parent":
        proof = UnresolvedHit(proof.window_identity, proof.bounds, "missing")
    elif fault == "parent-point":
        node = nodes[0]
        nodes[0] = SemanticNode(
            node.identity,
            node.window_identity,
            node.kind,
            Rect(100, 100, 1, 1),
            node.parent_identity,
            vertically_scrollable=True,
        )
    resolver = SemanticTargets(nodes)
    hit = nodes[0].identity if fault == "both" else None
    assert (
        resolver.normal_click(hit, point, discovery.window_identity, proof).intent
        is TargetIntent.WINDOW
    )
    assert (
        resolver.explicit_mode(hit, point, discovery.window_identity, proof).intent
        is TargetIntent.MANUAL_AREA
    )
