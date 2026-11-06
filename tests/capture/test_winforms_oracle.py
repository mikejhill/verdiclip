"""Positive capture and real unsupported conditions have distinct fixed oracles."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture import native_scrollbar
from tests.capture.native_scrollbar import (
    ScrollbarCase,
    fixture_image,
    native_fixture_profile,
)
from tests.capture.scroll_peer import ScrollPeer
from tests.capture.test_scroll_uia_protocol import Payload

from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind
from verdiclip.capture.scroll.uia_protocol import UiaDiscovery
from verdiclip.geometry import Rect


@pytest.mark.parametrize("provider", ["wpf", "winforms"])
def test_default_fixture_is_positive(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both providers qualify capture; support is never inferred from results."""
    monkeypatch.setenv("VERDICLIP_HOSTED_SCROLLBAR", "1")
    monkeypatch.setenv("VERDICLIP_NATIVE_PROVIDER", provider)
    monkeypatch.delenv("VERDICLIP_NATIVE_FIXTURE", raising=False)
    assert native_fixture_profile() == (provider, "scrolling")


@pytest.mark.parametrize(
    ("provider", "fixture"),
    [
        ("wpf", "unsupported-scrollbar"),
        ("foreign", "scrolling"),
        ("winforms", "unknown"),
    ],
)
def test_invalid_fixture_profile_is_rejected_early(
    provider: str, fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No process or desktop work may start from an unreviewed profile."""
    monkeypatch.setenv("VERDICLIP_HOSTED_SCROLLBAR", "1")
    monkeypatch.setenv("VERDICLIP_NATIVE_PROVIDER", provider)
    monkeypatch.setenv("VERDICLIP_NATIVE_FIXTURE", fixture)
    with pytest.raises(ValueError, match="Unsupported owned"):
        native_fixture_profile()
    with pytest.raises(ValueError, match=r"Unknown owned|Unsupported owned"):
        ScrollPeer(tmp_path, provider=provider, fixture=fixture)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "errors",
        "output",
        "kind",
        "partial",
        "dimensions",
        "pixels",
        "references",
        "hit",
        "storage",
        "input",
        "popup",
        "cleanup",
        "end",
        "offset",
        "repeated",
    ],
)
def test_positive_oracle_rejects_each_incomplete_gate(  # noqa: C901 - one fault matrix for the actual oracle
    fault: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delivered document alone never sets pixel/full/restore qualification."""
    expected = fixture_image(4, 8)
    actual = expected.copy()
    provenance = SimpleNamespace(
        source_kind=CaptureKind.SCROLLING, completeness=CaptureCompleteness.COMPLETE
    )
    if fault == "kind":
        provenance.source_kind = CaptureKind.SCREENSHOT
    if fault == "partial":
        provenance.completeness = CaptureCompleteness.PARTIAL
    if fault == "dimensions":
        actual = actual.copy(0, 0, 4, 7)
    if fault == "pixels":
        actual.setPixel(0, 0, 0)
    discovery = UiaDiscovery.decode(Payload.discovery(Payload.nodes()))
    if fault == "references":
        discovery = UiaDiscovery(
            discovery.window_identity, discovery.hit_identity, discovery.nodes, ()
        )
    case = SimpleNamespace(
        errors=["failure"] if fault == "errors" else [],
        snapshot=SimpleNamespace(errors=lambda: ["popup"] if fault == "popup" else []),
        qtbot=SimpleNamespace(waitUntil=lambda predicate, **_: predicate()),
        retired=lambda: True,
        app=SimpleNamespace(
            controller=SimpleNamespace(
                workspace=SimpleNamespace(
                    documents=[]
                    if fault == "output"
                    else [
                        SimpleNamespace(
                            provenance=provenance,
                            session=SimpleNamespace(
                                document=SimpleNamespace(image=actual)
                            ),
                        )
                    ]
                ),
                capture=SimpleNamespace(is_selecting=False),
            )
        ),
        positive_checks={},
        discoveries=[discovery],
        image=expected,
        operation_timings={},
        driver=object(),
        peer=SimpleNamespace(
            request_snapshot=lambda: None,
            poll_snapshot=lambda: True,
            snapshot_ack={
                "end_seen": fault != "end",
                "offset": 8 if fault == "offset" else 0,
            },
        ),
        input_deadline=time.monotonic() + 50,
        repeated_window_captures=0,
        passed=False,
    )

    case.peer.assert_snapshot_restored = lambda: ScrollPeer.assert_snapshot_restored(
        cast(ScrollPeer, case.peer)
    )

    def repeat() -> None:
        case.repeated_window_captures = 1 if fault == "repeated" else 2

    def storage(_value: object) -> None:
        assert fault != "storage", "storage failed"

    case.verify_repeated_window_capture = repeat
    monkeypatch.setattr(native_scrollbar, "verify_deferred_storage", storage)
    monkeypatch.setattr(
        native_scrollbar,
        "native_action_errors",
        lambda *_: ["input"] if fault == "input" else [],
    )
    monkeypatch.setattr(
        native_scrollbar, "cleanup_confirmed", lambda *_, **__: fault != "cleanup"
    )
    target = Rect(500, 500, 2, 2) if fault == "hit" else Rect(91, 10, 2, 2)
    if fault is None:
        ScrollbarCase.verify_supported(cast(ScrollbarCase, case), target)
        assert case.passed is True
        assert len(case.positive_checks) == 7
        assert all(case.positive_checks.values())
    else:
        with pytest.raises(AssertionError):
            ScrollbarCase.verify_supported(cast(ScrollbarCase, case), target)
        assert case.passed is False
        if fault in {"dimensions", "pixels"}:
            assert "pixel_exact" not in case.positive_checks
        if fault == "end":
            assert "fixture_end_seen" not in case.positive_checks
        if fault == "offset":
            assert "fixture_restored" not in case.positive_checks
        if fault == "repeated":
            assert "two_subsequent_captures" not in case.positive_checks


def test_negative_lane_is_an_actual_distinct_fixture() -> None:
    """The negative lane uses a real standalone scrollbar, not missing runtime IDs."""
    workflow = Path(".github/workflows/ci.yml").read_text()
    source = Path("tests/capture/provider_winforms_fixture.ps1").read_text()
    assert "fixture: unsupported-scrollbar" in workflow
    assert "VERDICLIP_NATIVE_FIXTURE:" in workflow
    assert "[Windows.Forms.VScrollBar]::new()" in source
    assert "[Windows.Forms.PictureBox]::new()" in source
    assert "$offset=$standalone.Value*8" in source
    assert "Unsupported fixture must not receive scroll commands" in source
    assert "fixture_kind=$(if($UnsupportedScrollbar)" in source
    harness = Path("tests/capture/native_scrollbar.py").read_text()
    assert 'if self.fixture == "unsupported-scrollbar":' in harness
    assert 'if self.provider == "winforms":' not in harness
    assert "discovery.unresolved_hit," in harness


@pytest.mark.parametrize("declared", [None, "scrolling", "unsupported-scrollbar"])
def test_negative_peer_requires_matching_fixture_attestation(
    tmp_path: Path, declared: str | None
) -> None:
    """A positive/missing-mode snapshot cannot stand in for the negative fixture."""
    peer = ScrollPeer(tmp_path, provider="winforms", fixture="unsupported-scrollbar")
    peer.process = cast(
        subprocess.Popen[bytes], SimpleNamespace(pid=123, poll=lambda: None)
    )
    value: dict[str, object] = {
        "nonce": peer.nonce,
        "pid": 123,
        "sequence": 1,
        "visible": True,
        "closed": False,
        "activation_complete": True,
        "started_ticks": 456,
        "hwnd": 789,
    }
    if declared is not None:
        value["fixture_kind"] = declared
    (tmp_path / "fixture-state-000001.json").write_text(json.dumps(value))
    if declared == "unsupported-scrollbar":
        assert peer.poll()
    else:
        with pytest.raises(ValueError, match="ownership changed"):
            peer.poll()


@pytest.mark.parametrize("separate", [False, True])
@pytest.mark.parametrize(
    "fault", [None, "missing", "center", "scrollbar", "pid", "root"]
)
def test_point_owners_require_each_exact_owned_control(
    *, separate: bool, fault: str | None
) -> None:
    """Different negative controls retain the same exact HWND/PID/root guards."""
    expected = {"center": 10, "scrollbar": 20 if separate else 10}
    actual = expected.copy()
    if fault == "missing":
        expected["scrollbar"] = 0
    elif fault in {"center", "scrollbar"}:
        actual[fault] = 30
    checked: list[int] = []

    def pid(handle: int) -> int:
        checked.append(handle)
        return 999 if fault == "pid" and handle == expected["scrollbar"] else 100

    def run() -> None:
        native_scrollbar.attest_fixture_point_owners(
            expected,
            actual,
            fixture_identity=(1, 100),
            process_id=pid,
            root_handle=lambda handle: (
                999 if fault == "root" and handle == expected["scrollbar"] else 1
            ),
        )

    if fault is None:
        run()
        assert set(checked) == set(expected.values())
    else:
        with pytest.raises(AssertionError):
            run()
