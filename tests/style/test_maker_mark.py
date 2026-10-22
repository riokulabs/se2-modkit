"""The Rioku Labs maker plate (`helpers.maker_mark`): size floor, LOD rules and the R4 decal from the Rioku atlas.
Drawn against fake `geom` / `decals` modules, so no Blender is needed."""

import sys
import types
from pathlib import Path

import pytest

import se2modkit.model as model_pkg
from se2modkit.kit import atlas
from se2modkit.style import helpers

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _vanilla():
    helpers.use("vanilla")
    yield
    helpers.use("vanilla")


class _Rec:
    def __init__(self, name):
        self.name, self.calls = name, []


@pytest.fixture
def fake(monkeypatch):
    """Fake `se2modkit.model.geom` and `.decals`: record what the helper draws."""
    log = []
    geom = types.ModuleType("se2modkit.model.geom")
    geom.prism = lambda p, F, pts, d0, d1, mat, **kw: log.append(("prism", p.name, mat, d1 - d0, pts))
    geom.cylinder = lambda p, a, b, r, n, mat: log.append(("bolt", p.name, mat, r))
    decals = types.ModuleType("se2modkit.model.decals")
    decals._quad = lambda p, F, corners, d, mat, uvs: log.append(("decal", p.name, mat, corners, uvs, d))
    for name, mod in (("geom", geom), ("decals", decals)):
        monkeypatch.setitem(sys.modules, f"se2modkit.model.{name}", mod)
        monkeypatch.setattr(model_pkg, name, mod, raising=False)
    return log


def F(a, b, d):
    return (a, b, d)


F.n = (0.0, 0.0, 1.0)


def _draw(fake, lod, h=None, **kw):
    fake.clear()
    box = helpers.maker_mark(_Rec("solid"), _Rec("decal"), F, 0.0, 0.0, h=h, lod=lod, **kw)
    return box, [c[0] for c in fake]


def test_maker_mark_min_height(fake):
    with pytest.raises(ValueError, match="maker.cap_min"):
        helpers.maker_mark(_Rec("solid"), _Rec("decal"), F, 0, 0, h=0.03)
    assert helpers.maker_mark(_Rec("solid"), _Rec("decal"), F, 0, 0, h=0.04) is not None


def test_maker_mark_lod_rules(fake):
    assert helpers.const("decal.max_lod") == 1
    box, kinds = _draw(fake, 0)
    assert kinds == ["prism", "bolt", "bolt", "decal"]
    plate = fake[0]
    assert plate[1] == "solid" and plate[2] == "PaintedMetalDarker" and plate[3] == pytest.approx(0.0018)
    assert len(plate[4]) == 5                              # one 45 degree clipped corner
    assert all(c[1] == "solid" for c in fake if c[0] == "bolt")
    assert _draw(fake, 1)[1] == ["prism", "decal"]         # bolts LOD0 only, decal to decal.max_lod
    assert _draw(fake, 2)[1] == ["prism"]                  # plate to LOD2
    assert _draw(fake, 3) == (None, [])                    # nothing at LOD3
    assert box == pytest.approx((-0.105, -0.04, 0.105, 0.04))


def test_maker_mark_draws_the_r4_decal(fake):
    A = atlas.load()
    for h, elem in ((0.04, "r4_mark"), (0.06, "r4_mark_2x")):
        _draw(fake, 0, h=h)
        _, name, mat, corners, uvs, d = fake[-1]
        assert name == "decal" and mat == A.material
        (a0, b0), (a1, _), (_, b1) = corners[0], corners[1], corners[2]
        size = a1 - a0
        assert size == pytest.approx(b1 - b0)               # square, the mark's aspect
        L = helpers.maker_mark_layout(h)
        assert size <= L["height"] - 2 * L["clip"] + 1e-9    # fits between the clipped corner and the bottom edge
        assert a0 >= -L["width"] / 2 + L["mark_left"] - 1e-9  # clear of the bolts
        assert d == pytest.approx(L["proud"])
        u0, v0, u1, v1 = A.uv(elem)
        assert uvs == ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
    assert _draw(fake, 0, mark=None)[1] == ["prism", "bolt", "bolt"]


def test_maker_mark_artwork_has_no_lettering():
    """The maker mark is drawn shapes only: no stencil text in the SVG, so it reads the same at any size and needs no
    font."""
    svg = (REPO / "src/se2modkit/kit/maker-mark/r4-mark-40mm.svg").read_text(encoding="utf-8")
    assert "<text" not in svg and "<tspan" not in svg
    assert "<circle" in svg or "<path" in svg
