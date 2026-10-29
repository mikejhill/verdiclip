"""Pure manifest/provenance contracts; never construct GUI or native sources."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from verdiclip import verification
from verdiclip.verification import PreviewProfile

COMMIT = "a" * 40


@pytest.fixture(autouse=True)
def forbid_live_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any accidental launch must fail before reaching Qt or desktop adapters."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Pure manifest contract attempted live composition")

    for name in (
        "QApplication",
        "MssScreenSource",
        "WindowLocator",
        "PreviewController",
    ):
        monkeypatch.setattr(verification, name, forbidden)


@pytest.mark.parametrize(
    ("native_ui", "native_scrolling", "expected"),
    [
        (False, False, (False, False)),
        (True, False, (True, False)),
        (True, True, (True, True)),
        (False, True, None),
        (1, False, (False, False)),
        ("true", False, (False, False)),
        (True, 1, (True, False)),
        (True, "true", (True, False)),
        (True, None, (True, False)),
        (None, True, None),
    ],
)
def test_manifest_qualification_is_exact_and_preserves_flags(
    tmp_path: Path,
    native_ui: object,
    native_scrolling: object,
    expected: tuple[bool, bool] | None,
) -> None:
    """Neither disable qualified scrolling nor enable it from a false/truthy value."""
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "commit": COMMIT,
                "native_ui_ready": native_ui,
                "native_scrolling_ready": native_scrolling,
            }
        ),
        encoding="utf-8",
    )
    if expected is None:
        with pytest.raises(ValueError, match="native UI qualification"):
            PreviewProfile.load(path)
    else:
        profile = PreviewProfile.load(path)
        assert profile.native_ui_ready is expected[0]
        assert profile.native_scrolling_ready is expected[1]
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "flags",
    [
        {},
        {"native_ui_ready": True},
        {"NATIVE_UI_READY": True, "NATIVE_SCROLLING_READY": True},
    ],
)
def test_absent_or_unrecognized_scrolling_qualification_stays_disabled(
    tmp_path: Path, flags: dict[str, bool]
) -> None:
    """Older/unknown manifest fields cannot opt into a new native capability."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"commit": COMMIT, **flags}), encoding="utf-8")
    assert PreviewProfile.load(path).native_scrolling_ready is False


def test_utf8_bom_manifest_loads_without_starting_services(tmp_path: Path) -> None:
    """Windows-generated UTF-8 BOM metadata has the same readiness semantics."""
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "commit": COMMIT,
                "native_ui_ready": True,
                "native_scrolling_ready": False,
                "notes": "synthetic \u2713",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8-sig",
    )
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert PreviewProfile.load(path) == PreviewProfile(
        tmp_path, COMMIT, native_ui_ready=True, native_scrolling_ready=False
    )
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("ready", [False, True])
def test_self_check_provenance_schema_matches_private_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, ready: bool
) -> None:
    """The published self-check must identify actual build, IPC and state locations."""
    versions = {"pyside6-essentials": "pyside-fixture", "mss": "mss-fixture"}
    monkeypatch.setattr(verification, "version", versions.__getitem__)
    monkeypatch.setattr(verification, "qVersion", lambda: "qt-fixture")
    profile = PreviewProfile(
        tmp_path, COMMIT, native_ui_ready=ready, native_scrolling_ready=ready
    )
    assert profile.describe() == {
        "commit": COMMIT,
        "native_ui_ready": ready,
        "native_scrolling_ready": ready,
        "label": f"VerdiClip Preview {COMMIT[:8]}",
        "instance_name": profile.instance_name,
        "settings": str(tmp_path / "state" / "settings.json"),
        "styles": str(tmp_path / "state" / "styles.json"),
        "output": str(tmp_path / "output"),
        "logs": str(tmp_path / "logs"),
        "python": sys.version.split()[0],
        "pyside6": "pyside-fixture",
        "qt": "qt-fixture",
        "mss": "mss-fixture",
    }
    assert not (tmp_path / "state").exists()
