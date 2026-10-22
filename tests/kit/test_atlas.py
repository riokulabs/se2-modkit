"""The Rioku decal atlas: element table, SVG rasteriser, `se2mod kit build | fetch` (text sources in git, binaries
in the user cache only)."""

import hashlib
import io
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

import se2modkit
from se2modkit import kit
from se2modkit.kit import atlas, build, fetch, svg

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

PKG = Path(kit.__file__).parent


def _tree(root: Path) -> dict[str, float]:
    return {str(p.relative_to(root)): p.stat().st_mtime for p in root.rglob("*") if "__pycache__" not in p.parts}


# ------------------------------------------------------------------------------------------------ element table
def test_atlas_table_rects_inside_texture():
    A = atlas.load()
    w, h = A.size
    assert A.elements, "the atlas has no elements"
    for el in A.elements.values():
        x0, y0, x1, y1 = el.rect
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, el.name
    rects = [el.rect for el in A.elements.values()]
    for i, r in enumerate(rects):
        for q in rects[i + 1:]:
            assert r[2] <= q[0] or q[2] <= r[0] or r[3] <= q[1] or q[3] <= r[1], f"{r} overlaps {q}"


def test_atlas_elements_keep_the_texel_density():
    A = atlas.load()
    assert A.px_per_m == 1600  # 64 px per 40 mm
    for el in A.elements.values():
        x0, y0, x1, y1 = el.rect
        assert (x1 - x0) == pytest.approx(el.size[0] * A.px_per_m * el.scale)
        assert (y1 - y0) == pytest.approx(el.size[1] * A.px_per_m * el.scale)
        assert (PKG / el.source).is_file(), el.source
    assert A.elements["r4_mark"].rect[2] - A.elements["r4_mark"].rect[0] == 64
    assert A.elements["r4_mark"].size == (0.04, 0.04)


def test_atlas_uv_and_pick():
    A = atlas.load()
    u0, v0, u1, v1 = A.uv("r4_mark")
    x0, y0, x1, y1 = A.elements["r4_mark"].rect
    assert (u0, u1) == pytest.approx((x0 / A.size[0], x1 / A.size[0]))
    assert (v0, v1) == pytest.approx((1 - y1 / A.size[1], 1 - y0 / A.size[1]))
    assert A.pick("r4_mark", 0.035).name == "r4_mark"
    assert A.pick("r4_mark", 0.052).name == "r4_mark_2x"  # the mark on a 60 mm-cap plate
    assert A.pick("r4_mark", 0.5).name == "r4_mark_2x"   # larger than any cell: the largest
    with pytest.raises(KeyError):
        A.pick("nope", 0.04)


def test_atlas_rejects_bad_tables(tmp_path):
    bad = tmp_path / "t.toml"
    bad.write_text('[atlas]\nname = "X"\nmaterial = "X"\nsize = [64, 64]\npx_per_m = 1600\n'
                   '[[element]]\nname = "a"\nfamily = "a"\nsource = "maker-mark/r4-mark-40mm.svg"\n'
                   'rect = [32, 0, 96, 64]\nsize = [0.04, 0.04]\n', encoding="utf-8")
    with pytest.raises(atlas.AtlasError, match="outside"):
        atlas.load(bad)


# ------------------------------------------------------------------------------------------------ rasteriser
def test_svg_evenodd_mask_and_errors():
    ring = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path fill="#fff" fill-rule="evenodd" '
            'd="M0 0 L10 0 L10 10 L0 10 Z M3 3 L7 3 L7 7 L3 7 Z"/></svg>')
    cov = svg.coverage(ring, 10, supersample=1)
    assert cov.getpixel((1, 1)) == 255 and cov.getpixel((5, 5)) == 0
    masked = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><defs><mask id="m">'
              '<rect x="0" y="0" width="10" height="10" fill="#fff"/><rect x="0" y="0" width="5" height="10" '
              'fill="#000"/></mask></defs><g mask="url(#m)"><rect x="0" y="0" width="10" height="10" fill="#fff"/>'
              '</g></svg>')
    cov = svg.coverage(masked, 10, supersample=1)
    assert cov.getpixel((2, 5)) == 0 and cov.getpixel((8, 5)) == 255
    with pytest.raises(svg.SvgError, match="straight segments"):
        svg.coverage('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0 0 C1 1 1 1 0 1Z"/></svg>',
                     4)


def test_r4_mark_fills_its_cell_with_a_margin():
    src = (PKG / "maker-mark" / "r4-mark-40mm.svg").read_text(encoding="utf-8")
    cov = svg.coverage(src, 64)
    x0, y0, x1, y1 = cov.point(lambda v: 255 if v > 8 else 0).getbbox()
    assert x0 >= 2 and y0 >= 2 and x1 <= 62 and y1 <= 62   # mip-safe margin
    assert x1 - x0 >= 54 and y1 - y0 >= 54                 # but it uses the cell
    filled = sum(cov.histogram()[128:]) / (64 * 64)
    assert 0.25 < filled < 0.6                             # a mark, not a blob or a hairline


# ------------------------------------------------------------------------------------------------ kit build / fetch
def test_kit_build_writes_cache_only(tmp_path):
    before = _tree(PKG)
    cache = tmp_path / "cache"
    res = build.build(cache, texconv=None)
    out = cache / "kit" / se2modkit.__version__
    assert res.png == out / "RiokuAtlas01.png" and res.png.is_file() and res.dds is None
    assert any("texconv" in w for w in res.warnings)
    assert {p.parent for p in res.files} == {out}
    assert _tree(PKG) == before, "kit build wrote into the package"
    im = Image.open(res.png)
    assert im.mode == "RGBA" and im.size == atlas.load().size
    x0, y0, x1, y1 = atlas.load().elements["r4_mark"].rect
    cell = im.crop((x0, y0, x1, y1))
    assert cell.getchannel("A").getextrema()[1] == 255
    assert cell.convert("RGB").getextrema() == ((255, 255), (255, 255), (255, 255))  # white ink, alpha carries it
    assert kit.asset_path("RiokuAtlas01.png", cache) == res.png


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell fake")
def test_kit_build_dds_with_texconv(tmp_path):
    fake = tmp_path / "texconv"
    fake.write_text('#!/bin/sh\nout=""\nwhile [ $# -gt 1 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                    'n=$(basename "$1" .png)\nprintf DDS > "$out/$n.dds"\n', encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    res = build.build(tmp_path / "cache", texconv=fake)
    assert res.dds is not None and res.dds.read_bytes() == b"DDS"
    assert not res.warnings


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell fake")
def test_kit_build_kills_a_hung_texconv(tmp_path):
    fake = tmp_path / "texconv"
    fake.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    res = build.build(tmp_path / "cache", texconv=fake, timeout=0.5)
    assert res.dds is None and any("timed out" in w for w in res.warnings)


def test_find_texconv_skips_windows_exe_off_windows(tmp_path):
    exe = tmp_path / "texconv.exe"
    exe.write_text("", encoding="utf-8")
    assert build.find_texconv(environ={"PATH": str(tmp_path)}, platform="linux") is None


def test_asset_path_missing_says_how(tmp_path):
    with pytest.raises(kit.KitError, match="se2mod kit build"):
        kit.asset_path("RiokuAtlas01.png", tmp_path)


def test_kit_fetch_without_release_says_so(tmp_path):
    with pytest.raises(kit.KitError, match="no release"):
        fetch.fetch(tmp_path, releases={})


def _zip(tmp_path) -> tuple[Path, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("RiokuAtlas01.png", b"png")
    z = tmp_path / "kit.zip"
    z.write_bytes(buf.getvalue())
    return z, hashlib.sha256(buf.getvalue()).hexdigest()


def test_kit_fetch_checks_sha256(tmp_path):
    z, sha = _zip(tmp_path)
    rel = {se2modkit.__version__: {"url": z.as_uri(), "sha256": sha}}
    out = fetch.fetch(tmp_path / "cache", releases=rel)
    assert (out / "RiokuAtlas01.png").read_bytes() == b"png"
    rel[se2modkit.__version__]["sha256"] = "0" * 64
    with pytest.raises(kit.KitError, match="sha256"):
        fetch.fetch(tmp_path / "cache2", releases=rel)
    assert not (tmp_path / "cache2" / "kit" / se2modkit.__version__ / "RiokuAtlas01.png").exists()


def test_kit_fetch_refuses_unsafe_members(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../escape.png", b"x")
    p = tmp_path / "bad.zip"
    p.write_bytes(buf.getvalue())
    rel = {se2modkit.__version__: {"url": p.as_uri(), "sha256": hashlib.sha256(buf.getvalue()).hexdigest()}}
    with pytest.raises(kit.KitError, match="unsafe"):
        fetch.fetch(tmp_path / "cache", releases=rel)
    assert not (tmp_path / "cache" / "kit" / "escape.png").exists()


def test_assets_toml_records_no_release_yet():
    assert fetch.load_releases() == {}


# ------------------------------------------------------------------------------------------------ CLI
def test_cli_kit_build_and_fetch(run, tmp_path):
    code, out, err = run("kit", "build", "--no-dds")
    assert code == 0, err
    png = tmp_path / "home" / ".cache" / "se2modkit" / "kit" / se2modkit.__version__ / "RiokuAtlas01.png"
    assert png.is_file() and str(png) in out
    code, out, err = run("kit", "fetch")
    assert code == 1 and "no release" in err and "se2mod kit build" in err
    assert "Traceback" not in err


def test_cli_kit_path(run, tmp_path):
    code, _, err = run("kit", "path", "RiokuAtlas01.png")
    assert code == 1 and "se2mod kit build" in err
    run("kit", "build", "--no-dds")
    code, out, _ = run("kit", "path", "RiokuAtlas01.png")
    assert code == 0 and out.strip().endswith(os.path.join("kit", se2modkit.__version__, "RiokuAtlas01.png"))


# ------------------------------------------------------------------------------------------------ review fixes
_DONUT = "M0 0 L10 0 L10 10 L0 10 Z M3 3 L3 7 L7 7 L7 3 Z"  # opposite-winding hole


def _svg(body: str, extra: str = "") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"{extra}>{body}</svg>'


def test_svg_nonzero_multi_subpath_is_refused_not_filled():
    with pytest.raises(svg.SvgError, match="nonzero"):
        svg.coverage(_svg(f'<path fill="#fff" d="{_DONUT}"/>'), 10, supersample=1)
    cov = svg.coverage(_svg(f'<path fill="#fff" fill-rule="evenodd" d="{_DONUT}"/>'), 10, supersample=1)
    assert cov.getpixel((5, 5)) == 0 and cov.getpixel((1, 1)) == 255


@pytest.mark.parametrize("body, match", [
    ('<rect x="0" y="0" width="10" height="10" fill="#fff" opacity="0.5"/>', "opacity"),
    ('<rect x="0" y="0" width="10" height="10" style="fill:#fff;fill-opacity:0.5"/>', "fill-opacity"),
    ('<g clip-path="url(#c)"><rect x="0" y="0" width="10" height="10" fill="#fff"/></g>', "clip-path"),
    ('<rect x="0" y="0" width="10" height="10" fill="url(#grad)"/>', "fill"),
    ('<rect x="0" y="0" width="10" height="10" fill="currentColor"/>', "fill"),
    ('<defs><mask id="m"><rect x="0" y="0" width="10" height="10" fill="#fff"/></mask></defs>'
     '<rect x="0" y="0" width="10" height="10" fill="#fff" mask="url(#m)"/>', "mask"),
    ('<rect x="0" y="0" width="10mm" height="10" fill="#fff"/>', "number"),
    ('<polygon fill="#fff" points="0,0 10,x 0,10"/>', "number"),
    ('<rect x="0" y="0" width="10" height="10" fill="#fff">', "malformed"),
])
def test_svg_unsupported_input_raises(body, match):
    with pytest.raises(svg.SvgError, match=match):
        svg.coverage(_svg(body), 10, supersample=1)


def test_svg_path_after_close_starts_at_the_current_point():
    # after Z the current point is (0, 0): "L10 0 L10 10 Z" must draw the triangle (0,0)-(10,0)-(10,10)
    cov = svg.coverage(_svg('<path fill="#fff" d="M0 0 L1 0 L0 1 Z L10 0 L10 10 Z" fill-rule="evenodd"/>'), 10,
                       supersample=1)
    assert cov.getpixel((8, 3)) == 255 and cov.getpixel((2, 8)) == 0


def test_atlas_missing_key_is_an_atlas_error(tmp_path):
    bad = tmp_path / "t.toml"
    bad.write_text('[atlas]\nname = "X"\nmaterial = "X"\nsize = [64, 64]\npx_per_m = 1600\n'
                   '[[element]]\nname = "a"\nrect = [0, 0, 64, 64]\nsize = [0.04, 0.04]\n', encoding="utf-8")
    with pytest.raises(atlas.AtlasError, match="source"):
        atlas.load(bad)
    with pytest.raises(kit.KitError, match="source"):
        build.build(tmp_path / "cache", texconv=None, atlas_path=bad)


def test_png_only_build_removes_a_stale_dds(tmp_path):
    out = kit.cache_dir(tmp_path / "cache")
    out.mkdir(parents=True)
    (out / "RiokuAtlas01.dds").write_bytes(b"old")
    res = build.build(tmp_path / "cache", texconv=None)
    assert res.dds is None and not (out / "RiokuAtlas01.dds").exists()


def _release(tmp_path, data: bytes) -> dict:
    p = tmp_path / "k.zip"
    p.write_bytes(data)
    return {se2modkit.__version__: {"url": p.as_uri(), "sha256": hashlib.sha256(data).hexdigest()}}


def test_kit_fetch_wraps_a_bad_zip(tmp_path):
    with pytest.raises(kit.KitError, match="not a readable zip"):
        fetch.fetch(tmp_path / "cache", releases=_release(tmp_path, b"not a zip"))


def test_kit_fetch_caps_the_unpacked_size(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.png", b"\0" * 4096)
    monkeypatch.setattr(fetch, "MAX_UNPACKED", 1024)
    with pytest.raises(kit.KitError, match="unpacks"):
        fetch.fetch(tmp_path / "cache", releases=_release(tmp_path, buf.getvalue()))
    assert not kit.cache_dir(tmp_path / "cache").exists()


def test_kit_fetch_leaves_no_part_files(tmp_path):
    z, sha = _zip(tmp_path)
    out = fetch.fetch(tmp_path / "cache", releases={se2modkit.__version__: {"url": z.as_uri(), "sha256": sha}})
    assert sorted(p.name for p in out.iterdir()) == ["RiokuAtlas01.png"]


def test_default_atlas_is_cached():
    assert atlas.default() is atlas.default()
