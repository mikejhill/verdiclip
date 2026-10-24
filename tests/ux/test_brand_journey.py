"""Verify the brand remains visible in real editor window icons."""

from __future__ import annotations

import pytest
from tests.ux.conftest import User

pytestmark = pytest.mark.ux


@pytest.mark.parametrize("size", [16, 20, 24, 32, 48, 64, 128, 256])
def test_editor_brand_keeps_capture_corners_and_v(user: User, size: int) -> None:
    """UX-G-11: the window icon renders the emerald capture mark at every size."""
    image = user.window.windowIcon().pixmap(size, size).toImage()

    assert not image.isNull(), f"Expected a rendered {size}px window icon"
    assert image.pixelColor(0, 0).alpha() == 0, (
        f"Expected transparent padding at {size}px"
    )
    tile = image.pixelColor(round(size * 0.5), round(size * 0.15))
    assert tile.green() > tile.red() * 3, (
        f"Expected an emerald tile at {size}px, got {tile.name()}"
    )
    corner = image.pixelColor(int(size * 0.22), int(size * 0.28))
    assert corner.green() > 170, (
        f"Expected a mint capture corner at {size}px, got {corner.name()}"
    )
    assert corner.red() > 80, (
        f"Expected a mint capture corner at {size}px, got {corner.name()}"
    )
    v_tip = image.pixelColor(int(size * 0.48), int(size * 0.63))
    assert min(v_tip.red(), v_tip.green(), v_tip.blue()) > 200, (
        f"Expected the white V at {size}px, got {v_tip.name()}"
    )
