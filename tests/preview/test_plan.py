from __future__ import annotations

import pytest

from se2modkit.preview import plan as PL

PLAN = """
[defaults]
resolution = [800, 600]
lighting = "flat"

[sets.review]
blocks = ["XX_A", "XX_B"]

[[sets.review.shots]]
name = "front"
camera = "3q_front"

[[sets.review.shots]]
name = "built-30"
state = "construction"
stage = 0.3
blocks = ["XX_A"]

[[sets.review.shots]]
name = "hinge"
camera = { position = [1, 2, 0.5], target = [0, 0, 0.2], lens = 85 }
state = "zones-dead"
zones = { Fracture_01 = "dead" }
together = true

[sets.lods]
blocks = ["XX_A"]
[[sets.lods.shots]]
name = "lod2"
lod = 2
"""


def test_parse_plan():
    plan = PL.parse_plan(__import__("tomllib").loads(PLAN))
    assert list(plan) == ["review", "lods"]
    front, built, hinge = plan["review"].shots
    assert front.blocks == ("XX_A", "XX_B") and front.resolution == (800, 600) and front.lighting == "flat"
    assert built.state == "construction" and built.stage == 0.3 and built.blocks == ("XX_A",)
    assert hinge.camera == {"position": [1.0, 2.0, 0.5], "target": [0.0, 0.0, 0.2], "lens": 85.0}
    assert hinge.zones == {"Fracture_01": "dead"} and hinge.together


def test_select_still_block_and_set(tmp_path):
    p = tmp_path / "previews.toml"
    p.write_text(PLAN, encoding="utf-8")
    plan = PL.load_plan(p)
    with pytest.raises(PL.PlanError, match="Which preview set"):
        PL.select(plan)
    ps, shots = PL.select(plan, set_name="review", blocks=["XX_B"])
    assert [s.name for s in shots] == ["front", "hinge"] and all(s.blocks == ("XX_B",) for s in shots)
    _, still = PL.select(plan, set_name="review", still=True)
    assert len(still) == 1 and still[0].blocks == ("XX_A",) and not still[0].together
    _, one = PL.select(plan, set_name="review", shot="hinge")
    assert [s.name for s in one] == ["hinge"]
    with pytest.raises(PL.PlanError, match="No shot 'nope'"):
        PL.select(plan, set_name="review", shot="nope")
    with pytest.raises(PL.PlanError, match="shows XX_Z"):
        PL.select(plan, set_name="lods", blocks=["XX_Z"])


@pytest.mark.parametrize("bad, msg", [
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\ncamera="fisheye"\n', "unknown camera"),
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\nstate="construction"\n', "needs stage"),
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\nstage=0.5\n', "only goes with"),
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\nzones={F=\"dead\"}\n', "damage state"),
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\nstate="damaged"\nzones={F="gone"}\n', "intact"),
    ('[sets.s]\n[[sets.s.shots]]\nname="x"\n', "no blocks"),
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="../x"\n', "name"),
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\n[[sets.s.shots]]\nname="x"\n', "repeat"),
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\nwobble=1\n', "unknown key"),
    ('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\nresolution=[10, 600]\n', "out of range"),
    ('[sets.s]\nblocks=["A"]\n', "no shots"),
    ('[other]\n', "unknown top-level"),
    ('x = [', "not valid TOML"),
])
def test_plan_errors_are_one_sentence(tmp_path, bad, msg):
    p = tmp_path / "previews.toml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(PL.PlanError, match=msg) as e:
        PL.load_plan(p)
    assert "\n" not in str(e.value) and str(p) in str(e.value)


def test_find_plan_and_job(tmp_path):
    root, sm = tmp_path / "Mod", tmp_path / "Mod/Source/Model"
    sm.mkdir(parents=True)
    assert PL.find_plan(root, sm) is None
    (sm / "previews.toml").write_text("", encoding="utf-8")
    assert PL.find_plan(root, sm) == sm / "previews.toml"
    (root / "previews.toml").write_text("", encoding="utf-8")
    assert PL.find_plan(root, sm) == root / "previews.toml"
    shot = PL.Shot(name="a", blocks=("XX_A",))
    j = PL.job(mod_name="XX_Mod", prefix="XX_", paths={"models": sm / "Models", "asset_guids": None}, out=tmp_path,
               shots=[shot], fast=True)
    assert j["mod"]["paths"] == {"models": str(sm / "Models"), "asset_guids": None}
    assert j["shots"][0]["blocks"] == ["XX_A"] or j["shots"][0]["blocks"] == ("XX_A",)


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\\\b", "..", "x..y"])
def test_block_names_cannot_leave_their_folders(tmp_path, name):
    for body in (f'[sets.s]\nblocks=["{name}"]\n[[sets.s.shots]]\nname="x"\n',
                 f'[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\nblocks=["{name}"]\n'):
        p = tmp_path / "previews.toml"
        p.write_text(body, encoding="utf-8")
        with pytest.raises(PL.PlanError, match="block name"):
            PL.load_plan(p)


@pytest.mark.parametrize("name", ["..", "a..b"])
def test_set_and_shot_names_reject_dotdot(tmp_path, name):
    p = tmp_path / "previews.toml"
    p.write_text(f'[sets."{name}"]\nblocks=["A"]\n[[sets."{name}".shots]]\nname="x"\n', encoding="utf-8")
    with pytest.raises(PL.PlanError, match="set name"):
        PL.load_plan(p)
    p.write_text(f'[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="{name}"\n', encoding="utf-8")
    with pytest.raises(PL.PlanError, match="name"):
        PL.load_plan(p)


def test_contained(tmp_path):
    out = tmp_path / "preview" / "set"
    out.mkdir(parents=True)
    assert PL.contained(out, out / "a.png", "the image") == (out / "a.png").resolve()
    with pytest.raises(PL.PlanError, match="leaves"):
        PL.contained(out, out / ".." / ".." / "x.png", "the image")
    (out / "link").symlink_to(tmp_path)
    with pytest.raises(PL.PlanError, match="leaves"):
        PL.contained(out, out / "link" / "x.png", "the image")


@pytest.mark.parametrize("name", ["CON", "nul", "com1", "LPT9.txt", "aux.png", "x."])
def test_windows_reserved_names_refused(tmp_path, name):
    p = tmp_path / "previews.toml"
    p.write_text(f'[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="{name}"\n', encoding="utf-8")
    with pytest.raises(PL.PlanError, match="name"):
        PL.load_plan(p)
    p.write_text(f'[sets.s]\nblocks=["{name}"]\n[[sets.s.shots]]\nname="x"\n', encoding="utf-8")
    with pytest.raises(PL.PlanError, match="block name"):
        PL.load_plan(p)


def test_paint_checked_at_plan_time(tmp_path):
    p = tmp_path / "previews.toml"
    p.write_text('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\npaint="plaid"\n', encoding="utf-8")
    with pytest.raises(PL.PlanError, match="unknown paint 'plaid'"):
        PL.load_plan(p)
    p.write_text('[sets.s]\nblocks=["A"]\n[[sets.s.shots]]\nname="x"\npaint="blue"\n', encoding="utf-8")
    assert PL.load_plan(p)["s"].shots[0].paint == "blue"
