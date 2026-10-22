"""The plain-Python half of `style.helpers`: sizes come from the active profile; outlines need no Blender."""

import pytest

from se2modkit.style import helpers, profile


@pytest.fixture(autouse=True)
def _vanilla():
    helpers.use("vanilla")
    yield
    helpers.use("vanilla")


def test_constants_read_the_profile():
    assert helpers.const("hw.bolt_r") == 0.009
    assert helpers.role("dark") == "PaintedMetalDarker"
    helpers.use(profile.load("vanilla", overrides={"roles": {"copper": "Copper"}}))
    assert helpers.role("copper") == "Copper"


def test_maker_layout_scales_and_refuses_under_minimum():
    big = helpers.maker_mark_layout()
    small = helpers.maker_mark_layout(0.04)
    assert big["cap"] == 0.06 and big["proud"] == 0.0018
    assert small["width"] == pytest.approx(big["width"] * 0.04 / 0.06)
    with pytest.raises(ValueError, match="maker.cap_min"):
        helpers.maker_mark_layout(0.03)


def test_outlines():
    assert len(helpers.oct_pts(0, 0, 1, 1, 0.1)) == 8
    assert helpers.oct_pts(0, 0, 1, 1, 0) == helpers.rect(0, 0, 1, 1)
    pts = helpers.inset(helpers.rect(0, 0, 1, 1), 0.1)
    assert pts[0] == pytest.approx((0.1, 0.1))
    assert pts[2] == pytest.approx((0.9, 0.9))

