"""The Blender half of the runner, the z-fight audit and the connectivity check (`se2mod blender run
tests/run_blender_tests.py`): a toy block built end to end into build_report.json, decal elements tagged and audited,
loose islands found. Skipped outside Blender."""

import json

import pytest

bpy = pytest.importorskip("bpy")

from pipefix import SyntheticSpec  # noqa: E402
from se2modkit.model import decals as DE  # noqa: E402
from se2modkit.model import geom as G  # noqa: E402
from se2modkit.pipeline import connect as K  # noqa: E402
from se2modkit.pipeline import zfight as Z  # noqa: E402
from se2modkit.pipeline.runner import Runner  # noqa: E402

pytestmark = pytest.mark.blender


class Spec(SyntheticSpec):
    @property
    def cells(self):
        return tuple(max(1, round(s / 0.25)) for s in self.size)


def _bounds(spec):
    lo = tuple(-(n // 2) for n in spec.cells)
    return lo, tuple(a + n - 1 for a, n in zip(lo, spec.cells))


def _build(spec, lod):
    """A 1 m box carrying a pipe across its top and, on its front, two stencil lines, the second laid over the first
    (a decal fight). LOD1 is the plain box (Keen's checker asks for two LODs at this size)."""
    body = G.Part("body")
    G.box(body, (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5), "PaintedMetalColorable")
    if lod:
        return [body]
    pipe = G.Part("pipe")
    G.cylinder(pipe, (-0.45, 0.0, 0.55), (0.45, 0.0, 0.55), 0.05, 40, "BrushedMetal")
    dec = G.Part("label")
    dec.decal = True
    F = G.Face((0.0, 0.5, 0.0), (1, 0, 0), (0, 0, 1))
    DE.text(dec, F, -0.2, 0.1, 0.05, "AB")
    DE.text(dec, F, -0.18, 0.1, 0.05, "CD")
    return [body, pipe, dec]


def _boxes(spec):
    return {"regular": [("body", (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5))], "occupancy": [], "mount": []}


@pytest.fixture
def clean():
    yield
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def test_runner_builds_a_toy_block(hooks, clean, capsys):
    spec = Spec("XX_Box", "box")
    r = Runner(hooks=hooks, builders={"box": _build}, catalogue=lambda: [spec], collision={"box": _boxes},
               bounds=_bounds)
    assert r.main(["XX_Box", "--no-render"]) == 0
    data = json.loads(hooks.paths.report.read_text(encoding="utf-8"))
    entry = data["blocks"]["XX_Box"]
    assert set(data["se2modkit"]) == {"version", "commit"}
    assert entry["lods"] and entry["placement"]["total"] == 64 and entry["cells"] == [4, 4, 4]
    zf = entry["zfight"]
    assert set(zf) == set(Z.REPORT_KEYS)
    assert len([e for e in zf["elements"] if e["model"] == "block_LOD0"]) == 2
    assert Z.decal_conflicts(zf["elements"])                        # the two lines overlap at one depth
    assert "XX_Box" in r.built and "built XX_Box" in capsys.readouterr().out
    for objs in (objs for _, objs in r.built["XX_Box"]["lods"]):
        assert all(o.data.attributes.get(Z.TAG) is None for o in objs if o.type == "MESH")   # tags stripped


def test_connect_finds_a_loose_island(hooks):
    hooks.connect_roots = {"base"}

    def parts():
        base, top, loose = G.Part("base"), G.Part("top"), G.Part("loose")
        with K.comp(base, "base.shell"):
            G.box(base, (0, 0, 0), (1, 1, 0.1), "PaintedMetalColorable")
        with K.comp(top, "top.plate"):
            G.box(top, (0.2, 0.2, 0.1), (0.8, 0.8, 0.2), "PaintedMetalColorable")
        G.box(loose, (0.2, 0.2, 0.5), (0.4, 0.4, 0.6), "PaintedMetalColorable")
        K.joint("top.plate", "base.shell")
        return [base, top, loose]
    res = K.check_build(hooks, parts)
    assert res["joints_checked"] == 1 and res["joint_failures"] == []
    assert [i["part"] for i in res["loose_islands"]] == ["loose"] and not res["ok"]
    assert res["realism"]["manual"] == {} and K.REG["comps"] == {}


def test_export_writes_the_three_models(hooks, clean, monkeypatch, tmp_path):
    """export.export_block on the toy block (the toy two-zone plan of family "box"), the VRAGE writers faked (they
    need a windowed Blender): NonFractured / Fractured / Damaged files, and export.json with the cut zones, the
    hooks' dent and the Damaged slot table."""
    from se2modkit.defs.material_guids import damaged_table
    from se2modkit.model import export as EX
    from se2modkit.pipeline import export as XP

    def fbx(objs, path, ops):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(o.name for o in objs)))

    def collision(objs, path, ops):
        fbx(objs, path, ops)
        return {"extensionsUsed": [], "colliders": len(objs), "nodes": [o.name for o in objs]}
    monkeypatch.setattr(EX, "export_fbx", fbx)
    monkeypatch.setattr(EX, "export_collision", collision)
    spec = Spec("XX_Box", "box")
    r = Runner(hooks=hooks, builders={"box": _build}, catalogue=lambda: [spec], collision={"box": _boxes},
               bounds=_bounds)
    assert r.main(["XX_Box", "--no-render"]) == 0
    b = r.built["XX_Box"]
    with pytest.raises(RuntimeError, match="VRAGE Tools"):
        XP.export_block(hooks, spec, b["lods"], b["collision"], b["boxes"], None)
    out = tmp_path / "models"
    man = XP.export_block(hooks, spec, b["lods"], b["collision"], b["boxes"], object(), out_dir=out)
    d = out / "XX_Box"
    assert (d / "NonFractured" / "XX_Box.fbx").is_file() and (d / "Fractured" / "XX_Box_Fractured.fbx").is_file()
    assert (d / "Damaged" / "XX_Box_Damaged.fbx").is_file()
    assert json.loads((d / "export.json").read_text(encoding="utf-8")) == json.loads(json.dumps(man))
    fr = man["fractured"]
    assert fr["kind"] == "cut" and [z["id"] for z in fr["zones"]] == ["Fracture_01", "Fracture_02"]
    assert man["damaged"]["slots"] == damaged_table(hooks) and man["damaged"]["dent"]
    assert sum(z["health_share"] for z in fr["zones"]) == pytest.approx(1.0, abs=1e-3)


def test_connect_roots_argument_overrides_hooks(hooks):
    hooks.connect_roots = {"nothing"}

    def parts():
        base = G.Part("base")
        G.box(base, (0, 0, 0), (1, 1, 0.1), "PaintedMetalColorable")
        return [base]
    assert K.check_build(hooks, parts)["loose_islands"]                      # hooks' roots: base is not one
    assert K.check_build(hooks, parts, roots={"base"})["loose_islands"] == []


def test_incremental_rebuild_is_skipped_and_byte_identical(hooks, clean, capsys):
    """A real build, then a run with nothing stale (no block built, same bytes), then --force (a full rebuild writes
    the same bytes: the report entries are deterministic)."""
    spec = Spec("XX_Box", "box")

    def runner():
        return Runner(hooks=hooks, builders={"box": _build}, catalogue=lambda: [spec], collision={"box": _boxes},
                      bounds=_bounds)
    assert runner().main(["XX_Box", "--no-render"]) == 0
    full = hooks.paths.report.read_bytes()
    capsys.readouterr()
    r = runner()
    assert r.main(["XX_Box", "--no-render"]) == 0
    assert r.built == {} and "up to date: 1 block(s)" in capsys.readouterr().out
    assert hooks.paths.report.read_bytes() == full
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    r = runner()
    assert r.main(["XX_Box", "--no-render", "--force"]) == 0 and "XX_Box" in r.built
    assert hooks.paths.report.read_bytes() == full


def test_blender_loaders_are_recorded_as_reads(tmp_path):
    """Blender's C loaders are invisible to the audit hook: libraries.load is wrapped, images go through
    pipeline.inputs."""
    from se2modkit.pipeline import incremental as IN
    from se2modkit.pipeline import inputs
    IN.install_read_hook()
    blend = tmp_path / "kit.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True)
    img = bpy.data.images.new("xx_probe", 4, 4)
    img.filepath_raw = str(tmp_path / "probe.png")
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    with IN.recording() as seen:
        with bpy.data.libraries.load(str(blend)) as (src, dst):
            pass
        loaded = inputs.load_image(str(tmp_path / "probe.png"))
    assert str(blend) in seen and str(tmp_path / "probe.png") in seen
    bpy.data.images.remove(loaded)
    hashed = IN.read_hashes(seen, mod_dir=tmp_path)
    assert str(blend) in hashed and str(tmp_path / "probe.png") in hashed
