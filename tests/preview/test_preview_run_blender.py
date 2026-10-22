"""`se2mod preview`'s Blender side (`preview.run`) on a synthetic exported block: an intact LOD shot, a construction
stage and a damage scenario with one zone dead. Skipped outside Blender."""

from __future__ import annotations

import json

import pytest

bpy = pytest.importorskip("bpy")

from hookfix import make_hooks, write  # noqa: E402
from se2modkit.preview import plan as PL  # noqa: E402
from se2modkit.preview import run  # noqa: E402

pytestmark = pytest.mark.blender


def _cube(name, loc, mat, group=None):
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    ob.data.materials.append(bpy.data.materials.get(mat) or bpy.data.materials.new(mat))
    if group:
        ob["Group"] = group
    return ob


def _export(objs, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, use_custom_props=True)
    for o in objs:
        bpy.data.objects.remove(o)


def _block(hooks, name="XX_A"):
    d = hooks.paths.models / name
    _export([_cube("body", (0, 0, 0.25), "PaintedMetalColorable")], d / "NonFractured" / f"{name}.fbx")
    _export([_cube("body", (0, 0, 0.25), "PaintedMetalColorable")], d / "NonFractured" / f"{name}_LOD1.fbx")
    _export([_cube("left", (-0.3, 0, 0.25), "PaintedMetalColorable", "Fracture_01"),
             _cube("right", (0.3, 0, 0.25), "BrushedMetal", "Fracture_02")], d / "Fractured" / f"{name}_Fractured.fbx")
    write(d / "export.json", {"shift": [0, 0, 0], "fractured": {
        "zones": [{"id": "Fracture_01", "volume": 2.0}, {"id": "Fracture_02", "volume": 1.0}],
        "critical": ["Fracture_01"]}})
    write(hooks.paths.assets / hooks.name / name / f"{name}_BlockModelDefinition.partialdef",
          {"$Value": {"Manipulator": {"BuildProgressModels": [], "Dummies": []}}})


def test_run_job_renders_states(tmp_path):
    hooks = make_hooks(tmp_path / "mod")
    _block(hooks)
    shots = [PL.Shot(name="lod1", blocks=("XX_A",), lod=1, resolution=(160, 120)),
             PL.Shot(name="half", blocks=("XX_A",), state="construction", stage=0.5, resolution=(160, 120),
                     camera="front"),
             PL.Shot(name="cut", blocks=("XX_A",), state="zones-dead", zones={"Fracture_02": "dead"},
                     resolution=(160, 120), camera={"position": [0, 3, 1], "target": [0, 0, 0], "lens": 40})]
    p = hooks.paths
    job = PL.job(mod_name=hooks.name, prefix=hooks.prefix, out=hooks.paths.source_model / "preview" / "set", shots=shots, fast=True,
                 paths={"models": p.models, "report": p.report, "staged": p.staged, "golden_states": p.golden_states,
                        "source_model": p.source_model, "asset_guids": p.asset_guids, "root": p.root})
    jf = tmp_path / "job.json"
    jf.write_text(json.dumps(job), encoding="utf-8")
    assert run.main([str(jf)]) == 0
    for n in ("lod1", "half", "cut"):
        assert (p.source_model / "preview/set" / f"{n}.png").is_file(), n
    assert json.loads((p.source_model / "preview/set/shots.json").read_text()) == ["lod1.png", "half.png", "cut.png"]


def test_run_job_missing_export_is_one_error(tmp_path, capsys):
    hooks = make_hooks(tmp_path / "mod")
    p = hooks.paths
    job = PL.job(mod_name=hooks.name, prefix=hooks.prefix, out=hooks.paths.source_model / "preview" / "set", fast=True,
                 shots=[PL.Shot(name="x", blocks=("XX_Gone",), resolution=(64, 64))],
                 paths={"models": p.models, "report": p.report, "staged": p.staged, "golden_states": p.golden_states,
                        "source_model": p.source_model, "asset_guids": p.asset_guids, "root": p.root})
    jf = tmp_path / "job.json"
    jf.write_text(json.dumps(job), encoding="utf-8")
    assert run.main([str(jf)]) == 2
    assert "XX_Gone has no exported model" in capsys.readouterr().err


def test_run_job_refuses_paths_outside(tmp_path, capsys):
    hooks = make_hooks(tmp_path / "mod")
    p = hooks.paths
    for shot in (PL.Shot(name="x", blocks=("../../escape",), resolution=(64, 64)),
                 PL.Shot(name="../../escape", blocks=("XX_A",), resolution=(64, 64))):
        job = PL.job(mod_name=hooks.name, prefix=hooks.prefix, out=hooks.paths.source_model / "preview" / "set", fast=True, shots=[shot],
                     paths={"models": p.models, "report": p.report, "staged": p.staged,
                            "golden_states": p.golden_states, "source_model": p.source_model,
                            "asset_guids": p.asset_guids, "root": p.root})
        jf = tmp_path / "job.json"
        jf.write_text(json.dumps(job), encoding="utf-8")
        assert run.main([str(jf)]) == 2
        assert "leaves" in capsys.readouterr().err
    assert not (tmp_path / "escape.png").exists()


def test_run_job_refuses_a_symlinked_preview_folder(tmp_path, capsys):
    hooks = make_hooks(tmp_path / "mod")
    _block(hooks)
    p = hooks.paths
    (tmp_path / "elsewhere").mkdir()
    p.source_model.mkdir(parents=True, exist_ok=True)
    (p.source_model / "preview").symlink_to(tmp_path / "elsewhere")
    job = PL.job(mod_name=hooks.name, prefix=hooks.prefix, out=p.source_model / "preview" / "set", fast=True,
                 shots=[PL.Shot(name="x", blocks=("XX_A",), resolution=(64, 64))],
                 paths={"models": p.models, "report": p.report, "staged": p.staged, "golden_states": p.golden_states,
                        "source_model": p.source_model, "asset_guids": p.asset_guids, "root": p.root})
    jf = tmp_path / "job.json"
    jf.write_text(json.dumps(job), encoding="utf-8")
    assert run.main([str(jf)]) == 2
    assert "symlink" in capsys.readouterr().err
    assert not list((tmp_path / "elsewhere").iterdir())
