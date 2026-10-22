from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

from hookfix import make_hooks
from se2modkit.preview import PreviewError, base

PKG = Path(base.__file__).parent
BLENDER_SIDE = ("base", "mats", "turntable", "icons", "states")
ALLOWED = {"__future__", "bpy", "bmesh", "mathutils", "bpy_extras", "numpy"}


def test_output_dir_under_source_model(tmp_path):
    hooks = make_hooks(tmp_path / "mod")
    assert base.output_dir(hooks, "review-1") == tmp_path / "mod/Source/Model/preview/review-1"
    for bad in ("", "../x", "a/b", ".hidden"):
        with pytest.raises(PreviewError):
            base.output_dir(hooks, bad)


def test_output_dir_refuses_symlinked_preview_folders(tmp_path):
    hooks = make_hooks(tmp_path / "mod")
    sm = hooks.paths.source_model
    sm.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (sm / "preview").symlink_to(Path("../../../elsewhere"))
    assert (sm / "preview").resolve() == elsewhere.resolve()
    with pytest.raises(PreviewError, match="symlink") as e:
        base.output_dir(hooks, "review")
    assert "\n" not in str(e.value)
    (sm / "preview").unlink()
    (sm / "preview").mkdir()
    (sm / "preview" / "review").symlink_to(elsewhere)
    with pytest.raises(PreviewError, match="symlink"):
        base.output_dir(hooks, "review")
    assert base.output_dir(hooks, "other") == sm / "preview" / "other"
    with pytest.raises(PreviewError, match="not a set folder"):
        base.check_output(sm, tmp_path / "elsewhere" / "x")


def test_mats_cache_honours_the_legacy_prefix(tmp_path, monkeypatch):
    from se2modkit.preview import mats
    monkeypatch.delenv("SE2MODKIT_TEXCACHE", raising=False)
    monkeypatch.setenv("XX_TEXCACHE", str(tmp_path / "old-cache"))
    monkeypatch.setitem(mats._PATHS, "texcache", None)
    monkeypatch.setitem(mats._PATHS, "legacy_prefix", None)
    mats.configure(legacy_prefix="XX_")
    assert mats._cache() == str(tmp_path / "old-cache")


def test_want_and_configure(tmp_path):
    old = base.settings()
    try:
        base.configure(tmp_path, fast=True, only=["XX_A_"])
        assert base.want("XX_A_front") and not base.want("XX_B_front") and base.settings()["fast"]
        base.configure(only=[])
        assert base.want("anything")
    finally:
        base.configure(old["out"], fast=old["fast"], only=old["only"])


@pytest.mark.parametrize("mod", BLENDER_SIDE)
def test_blender_side_imports_offline_and_only_allowed_modules(mod):
    importlib.import_module(f"se2modkit.preview.{mod}")       # no bpy here: must still import
    tree = ast.parse((PKG / f"{mod}.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # se2modkit itself
                assert node.module not in ("sheets",), f"{mod} imports the Pillow sheets"
                continue
            names = [node.module]
        else:
            continue
        for n in names:
            top = n.split(".")[0]
            assert top in ALLOWED or top in _stdlib(), f"{mod} imports {n}"


def _stdlib():
    import sys
    return set(sys.stdlib_module_names)


# ------------------------------------------------------------------------------------------------ Pillow sheets
Image = pytest.importorskip("PIL.Image")
from se2modkit.preview import sheets  # noqa: E402


def _png(p, colour, size=(64, 48)):
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(p)
    return p


def test_contact(tmp_path):
    ps = [_png(tmp_path / f"{n}.png", (40 * i, 0, 0)) for i, n in enumerate("abc")]
    w, h = sheets.contact(ps, tmp_path / "out" / "contact.png", cols=2, thumb=60)
    assert (tmp_path / "out/contact.png").is_file() and w > 2 * 60


def test_diff_pct():
    a, b = Image.new("RGB", (10, 10), (0, 0, 0)), Image.new("RGB", (10, 10), (0, 0, 0))
    assert sheets.diff_pct(a, b) == 0.0 and sheets.diff_pct(a, None) is None
    b.putpixel((0, 0), (200, 0, 0))
    assert sheets.diff_pct(a, b) == pytest.approx(1.0)
    assert sheets.diff_pct(a, Image.new("RGB", (5, 5))) is None


def _states_set(d, colour):
    for key in ("XX_A", "V_Vanilla"):
        for shot in ("done", "c00"):
            for view in ("front", "rear"):
                _png(d / key / f"{shot}_{view}.png", colour)
    (d / "XX_A" / "info.json").write_text(json.dumps({"fr_lod0_tris": 100, "role_tris": {"scaffold": 25}}))
    (d / "manifest.json").write_text(json.dumps({"XX_A": ["done", "c00"]}))
    (d / "analogues.json").write_text(json.dumps({"V_Vanilla": ["done", "c00"]}))


def test_states_sheet_with_before(tmp_path):
    _states_set(tmp_path / "after", (10, 10, 10))
    _states_set(tmp_path / "before", (200, 10, 10))
    out = sheets.states_sheet(tmp_path / "after", families={"XX_A": "box"}, analogues=lambda f: ["Vanilla"],
                              before_dir=tmp_path / "before")
    assert out == [tmp_path / "after/sheets/XX_A.png"] and out[0].is_file()
    fam = sheets.states_sheet(tmp_path / "after", families={"XX_A": "box"}, analogues=lambda f: [], by_family=True)
    assert fam[0].name == "family_box.png"
    with pytest.raises(PreviewError, match="manifest.json"):
        sheets.states_sheet(tmp_path / "nope", families={}, analogues=lambda f: [])


def test_icon_sheet(tmp_path):
    ours = [(_png(tmp_path / "o.png", (1, 2, 3), (200, 200)), "Ours")]
    van = [(_png(tmp_path / "v.png", (3, 2, 1), (200, 200)), "vanilla X")]
    out = sheets.icon_sheet(ours, van, tmp_path / "s/icons.png", menu=[("Kind", ours[0][0], ["size 1: Ours (2.5 m)"])])
    assert out.is_file()
    with pytest.raises(PreviewError, match="unreadable icon"):
        sheets.icon_sheet([(tmp_path / "missing.png", "x")], [], tmp_path / "s2.png")
