"""Portable qualification of the prepared native delivery scenarios."""

from pathlib import Path
from typing import cast

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot
from tests.shell.delivery_fixture import CASES, run_scenario
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.output.delivery import ImageDelivery


@pytest.fixture
def factory(request: pytest.FixtureRequest) -> AppFactory:
    """All registrations/screens/settings/output are explicit fixture fakes."""
    return cast(AppFactory, request.getfixturevalue(make_app.__name__))


@pytest.mark.parametrize("case", CASES)
def test_synthetic_real_delivery_contract(
    case: str,
    factory: AppFactory,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Portable observations are not native foreground/notification evidence."""
    receipt = run_scenario(
        case, factory, qtbot, monkeypatch, tmp_path, foreground=lambda: 42
    )
    assert receipt["input"] is False
    assert receipt["clipboard"] is False
    assert receipt["real_private_png"] is True


def test_owned_admission_precedes_real_file_output(
    factory: AppFactory,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A native observer can refuse setup before any controller output runs."""
    admitted: list[bool] = []
    save = ImageDelivery.save

    def admit(anchor: QWidget, surface: QWidget) -> None:
        assert anchor.isVisible()
        assert surface.isVisible()
        admitted.append(True)

    def saving(delivery: ImageDelivery, image: QImage, path: Path) -> Path:
        assert admitted == [True]
        return save(delivery, image, path)

    monkeypatch.setattr(ImageDelivery, "save", saving)
    receipt = run_scenario(
        "popup-save-present",
        factory,
        qtbot,
        monkeypatch,
        tmp_path,
        foreground=lambda: 42,
        admit=admit,
    )
    assert receipt["real_private_png"] is True
    assert admitted == [True]


def test_changed_foreground_after_admission_refuses_output(
    factory: AppFactory,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A later foreign baseline cannot borrow an earlier owned observation."""
    with pytest.raises(AssertionError):
        run_scenario(
            "popup-save-present",
            factory,
            qtbot,
            monkeypatch,
            tmp_path,
            foreground=lambda: 42,
            admit=lambda *_: (99,),
        )
    assert not list(tmp_path.rglob("*.png"))
