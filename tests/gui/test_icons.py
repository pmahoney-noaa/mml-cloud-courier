"""The mark: a pure loader over committed package data — no theme
coupling, no painter code (the hex-color test enforces the latter)."""

from importlib.resources import files

from mml_cloud_courier.gui.icons import app_icon

_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def test_app_icon_carries_every_rendered_size(qapp):
    icon = app_icon()
    assert not icon.isNull()
    widths = {size.width() for size in icon.availableSizes()}
    assert set(_SIZES) <= widths


def test_assets_ship_as_package_data():
    assets = files("mml_cloud_courier.gui") / "assets"
    for size in _SIZES:
        assert (assets / f"mark-{size}.png").is_file(), size
    assert (assets / "mmlcc.ico").is_file()
