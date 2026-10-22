"""`pipeline.runner.Runner`: the build flow a mod's build script hands its hooks, builders and catalogue to. Offline:
block selection, the command line, and the plain errors (the build itself runs in Blender)."""

import json

import pytest

from pipefix import SyntheticSpec
from se2modkit.pipeline import zfight
from se2modkit.pipeline.hooks import HooksError
from se2modkit.pipeline.runner import Runner, Target, script_argv


class Spec(SyntheticSpec):
    @property
    def cells(self):
        return tuple(max(1, round(s / 0.25)) for s in self.size)


CATALOGUE = [Spec("XX_Box_Small", "box"), Spec("XX_Box_Large", "box", (1.0, 1.0, 2.0)), Spec("XX_Panel", "panel"),
             Spec("XX_Sign", "sign")]


def _runner(hooks, **kw):
    builders = {"box": lambda spec, lod: [], "panel": lambda spec, lod: []}
    return Runner(hooks=hooks, builders=builders, catalogue=lambda: list(CATALOGUE), **kw)


def _names(specs):
    return [s.name for s in specs]


def test_runner_selects_by_name_and_all(hooks):
    r = _runner(hooks)
    assert _names(r.select(["all"])) == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]   # every block with a builder
    assert _names(r.select([])) == _names(r.select(["all"]))
    assert _names(r.select(["XX_Panel", "XX_Box_Small"])) == ["XX_Box_Small", "XX_Panel"]   # catalogue order
    assert _names(r.select(["XX_Box*"])) == ["XX_Box_Small", "XX_Box_Large"]            # a prefix
    assert _names(r.select(["XX_Box*", "XX_Box_Large"])) == ["XX_Box_Small", "XX_Box_Large"]   # no duplicates
    picked = _runner(hooks, pipeline_blocks=lambda: [CATALOGUE[2]])
    assert _names(picked.select(["all"])) == ["XX_Panel"]


def test_runner_unknown_block_lists_choices(hooks, capsys):
    r = _runner(hooks)
    with pytest.raises(SystemExit) as e:
        r.select(["XX_Nope", "XX_Box_Small", "YY_*"])
    msg = str(e.value)
    assert "XX_Nope" in msg and "YY_*" in msg and "XX_Box_Small, XX_Box_Large, XX_Panel, XX_Sign" in msg
    assert r.main(["XX_Nope"]) == 2
    err = capsys.readouterr().err
    assert "Unknown block" in err and "XX_Sign" in err and "Traceback" not in err


def test_runner_no_builder_is_a_plain_error(hooks, capsys):
    assert _runner(hooks).main(["XX_Sign"]) == 2
    assert "no builder for family sign" in capsys.readouterr().err


def test_runner_help_and_unknown_option(hooks, capsys):
    r = _runner(hooks)
    assert r.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--export" in out and "--no-render" in out and "--check-placement" in out
    assert r.main(["--frobnicate"]) == 2
    assert "--frobnicate" in capsys.readouterr().err


def test_runner_outside_blender_says_so(hooks, capsys):
    try:
        import bpy  # noqa: F401
        pytest.skip("inside Blender")
    except ImportError:
        pass
    assert _runner(hooks).main(["XX_Panel"]) == 2
    err = capsys.readouterr().err
    assert "Blender" in err and "se2mod blender run" in err


def test_runner_wants_mod_hooks(hooks):
    with pytest.raises(HooksError, match="ModHooks"):
        Runner(hooks={"prefix": "XX_"}, builders={}, catalogue=lambda: [])
    hooks.prefix = "bad"
    with pytest.raises(HooksError, match="prefix"):
        _runner(hooks)


def test_script_argv_takes_the_args_after_the_separator():
    assert script_argv(["blender", "-b", "--python", "build.py", "--", "XX_A", "--export"]) == ["XX_A", "--export"]
    assert script_argv(["build.py", "XX_A"]) == ["XX_A"]
    assert script_argv(["build.py"]) == []


def test_launch_outside_blender_is_main(hooks, capsys):
    try:
        import bpy  # noqa: F401
        pytest.skip("inside Blender")
    except ImportError:
        pass
    assert _runner(hooks).launch(["--help"]) == 0 and "--export" in capsys.readouterr().out


def _offline(r, monkeypatch):
    """Let `run` go past the Blender guard with no block built (Blender-only steps stubbed)."""
    monkeypatch.setattr(r, "require_blender", lambda: None)
    monkeypatch.setattr(r, "prepare", lambda: None)
    monkeypatch.setattr(r, "vrt_ops", lambda: "ops")
    return r


def test_audits_run_around_the_strip_and_only_lists_print(hooks, synthetic_spec, monkeypatch, capsys):
    """Pre-strip audits, then zfight.audit_built (which strips the decal tags), then post-strip audits; a non-list
    value (a dict of bounds) is recorded, not printed as fault rows."""
    order = []
    monkeypatch.setattr(zfight, "audit_built", lambda h, spec, built: order.append("audit_built") or
                        {k: [] for k in zfight.REPORT_KEYS})
    r = _runner(hooks, audits=[lambda spec, built: order.append("pre") or {"backing": [("low", 0.1)]}],
                post_audits=[lambda spec, built: order.append("post") or {"leaf_bounds": {"L": [0, 1]},
                                                                          "leaf_leaf": []}])
    zf = r.audit(synthetic_spec())
    assert order == ["pre", "audit_built", "post"]
    assert zf["backing"] == [("low", 0.1)] and zf["leaf_bounds"] == {"L": [0, 1]} and zf["leaf_leaf"] == []
    out = capsys.readouterr().out
    assert "conflicts [0, 0, 0, 0, 0, 0, 1, 0]" in out and "('low', 0.1)" in out


def test_check_placement_analyse_gets_each_spec(hooks, monkeypatch):
    from se2modkit.pipeline import placement_check as PC
    seen = []

    def analyse(spec, tris, reg, occ, mnt, frac=(), *, bounds, shift):
        return {"spec": spec.name, "dirs": DIRS[spec.name], "bounds": bounds}
    DIRS = {"XX_Box_Small": ("Forward",), "XX_Panel": ("Up", "Down")}

    def check(h, spec, bounds, *, analyse=None, models_dir=None):
        seen.append(analyse(None, [], [], [], bounds=bounds, shift="bounds"))
    monkeypatch.setattr(PC, "check", check)
    for name in ("XX_Box_Small", "XX_Panel"):
        (hooks.paths.models / name).mkdir(parents=True)
        (hooks.paths.models / name / "export.json").write_text("{}", encoding="utf-8")
    r = _runner(hooks, bounds=lambda spec: ((0, 0, 0), (0, 0, 0)), analyse=analyse)
    r.check_placement(r.select(["all"]))
    assert [(x["spec"], x["dirs"]) for x in seen] == [("XX_Box_Small", ("Forward",)), ("XX_Panel", ("Up", "Down"))]


def test_targets_build_report_keys_export_and_render(hooks, monkeypatch):
    calls = []
    items = Target("items", build=lambda r: calls.append("build") or {"XX_Item": {"tris": 10}},
                   export=lambda r, ops: calls.append(("export", ops)))
    r = _offline(_runner(hooks, targets={"items": items},
                         preview=lambda built, tag: calls.append(("preview", dict(built), tag))), monkeypatch)
    monkeypatch.setenv("XX_PREVIEW", "legacy")
    assert r.main(["items", "--export"]) == 0                      # the target alone: no block is built
    assert calls == ["build", ("export", "ops"), ("preview", {}, "legacy")]
    data = json.loads(hooks.paths.report.read_text(encoding="utf-8"))
    assert data["items"] == {"XX_Item": {"tris": 10}} and "se2modkit" in data
    assert set(data["blocks"]) == {"XX_Box_Small", "XX_Box_Large", "XX_Panel"}   # empty entries, none built
    built = []
    monkeypatch.setattr(r, "build_block", lambda spec, render: built.append(spec.name) or {"lods": []})
    monkeypatch.setattr(r, "audit", lambda spec: {})
    assert r.main(["--no-render"]) == 0 and calls.count("build") == 1   # no word: "all" blocks, no target
    assert built == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]


def test_env_aliases(hooks, monkeypatch):
    r = _runner(hooks)
    monkeypatch.delenv("SE2MODKIT_PREVIEW", raising=False)
    monkeypatch.delenv("XX_PREVIEW", raising=False)
    assert r.env("PREVIEW") is None
    monkeypatch.setenv("XX_PREVIEW", "old")
    assert r.env("PREVIEW") == "old"
    monkeypatch.setenv("SE2MODKIT_PREVIEW", "new")
    assert r.env("PREVIEW") == "new"
    monkeypatch.setenv("XX_NO_QUIT", "1")
    assert r.env("NO_QUIT") == "1"


def test_failed_build_exits_1_with_traceback(hooks, monkeypatch, capsys):
    def boom(spec, render):
        raise ValueError("the builder broke")
    r = _offline(_runner(hooks), monkeypatch)
    monkeypatch.setattr(r, "build_block", boom)
    assert r.main(["XX_Panel"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" in err and "the builder broke" in err
    assert r.launch(["XX_Panel"]) == 1 or _in_blender()


def _in_blender():
    try:
        import bpy  # noqa: F401
        return True
    except ImportError:
        return False


def test_check_placement_outside_blender_says_so(hooks, capsys):
    if _in_blender():
        pytest.skip("inside Blender")
    assert _runner(hooks, bounds=lambda s: ((0, 0, 0), (0, 0, 0))).main(["--check-placement"]) == 2
    assert "inside Blender" in capsys.readouterr().err
