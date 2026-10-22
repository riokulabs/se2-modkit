"""The se2modkit pytest plugin: markers, fixtures and the shared gates, run on synthetic mods through pytester."""

import json
import textwrap

import pytest

pytest_plugins = ["pytester"]

GUID = "11111111-2222-4333-8444-555555555555"
SHIM = "from se2modkit.testing.gates import test_gate  # noqa: F401  (the shared gates)\n"
# A conftest whose environment has no game, no SDK, no Blender and no decompile (and an empty user cache).
NO_ENV = textwrap.dedent('''\
    import pytest
    from pathlib import Path
    from se2modkit.env.core import discover_env


    @pytest.fixture(scope="session")
    def se2_env(tmp_path_factory):
        home = tmp_path_factory.mktemp("home")
        return discover_env(environ={}, platform="linux", home=home, config={}, sysroot=home / "sys")
    ''')


def _def(guid, bundles=("VRage",), typ="VRage:Keen.VRage.Thing.ThingDefinitionObjectBuilder"):
    return {"$Bundles": {b: "1.0" for b in bundles}, "$Type": typ, "$Value": {"Guid": guid}}


def _mod_files(prefix, name, gates, guid=GUID, rel="", tests=True):
    pre = f"{rel}/" if rel else ""
    files = {f"{pre}{name}.vrgproj": "{}",
             f"{pre}Assets/{name}/Thing/Thing.def": json.dumps(_def(guid))}
    if tests:
        files[f"{pre}Tests/conftest.py"] = NO_ENV
        files[f"{pre}Tests/test_gates.py"] = SHIM
    return files


def _write(pytester, files):
    for rel, text in files.items():
        p = pytester.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _single(pytester, gates, prefix="MM_", extra=None):
    toml = textwrap.dedent(f'''\
        [tool.se2modkit]
        mod_name = "Mini"
        prefix = "{prefix}"
        gates = {json.dumps(gates)}
        ''')
    _write(pytester, {"pyproject.toml": toml, **_mod_files(prefix, "Mini", gates), **(extra or {})})


def test_gates_enabled_from_pyproject(pytester):
    _single(pytester, ["json", "bundles"])
    r = pytester.runpytest("Tests", "--collect-only", "-q")
    ids = sorted(line.split("::", 1)[1] for line in r.outlines if "::" in line)
    assert ids == ["test_gate[bundles]", "test_gate[json]"], r.outlines
    r = pytester.runpytest("Tests", "-q")
    r.assert_outcomes(passed=2)


def test_gate_fails_with_the_problem(pytester):
    _single(pytester, ["bundles"])
    (pytester.path / "Assets/Mini/Thing/Bad.def").write_text(json.dumps(_def(GUID[:-1] + "6", typ="Game2:X.Y")))
    r = pytester.runpytest("Tests", "-q")
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines(["*Thing/Bad.def*Game2*"])


def test_unknown_gate_name_is_a_usage_error(pytester):
    _single(pytester, ["json", "nope"])
    r = pytester.runpytest("Tests", "-q")
    assert r.ret != 0
    r.stdout.fnmatch_lines(["*Unknown gate 'nope'*"])


def test_gate_skips_without_sdk(pytester):
    _single(pytester, ["clientserver", "schema"])
    r = pytester.runpytest("Tests", "-q", "-rs")
    r.assert_outcomes(skipped=2)
    r.stdout.fnmatch_lines(["*SKIPPED*Mod SDK*"])


def test_decompiled_marker_skips_without_decompile(pytester):
    _single(pytester, [], extra={"Tests/test_x.py": textwrap.dedent('''\
        import pytest


        @pytest.mark.decompiled
        def test_needs_it():
            pass
        ''')})
    r = pytester.runpytest("Tests/test_x.py", "-q", "-rs")
    r.assert_outcomes(skipped=1)
    r.stdout.fnmatch_lines(["*SE2_DECOMPILED*"])


def test_sdkbuild_opt_in(pytester, monkeypatch):
    _single(pytester, [], prefix="MM_", extra={"Tests/test_build.py": textwrap.dedent('''\
        import pytest


        @pytest.mark.sdkbuild
        def test_real_build():
            pass
        ''')})
    for var in ("SE2MODKIT_SDK_BUILD", "MM_SDK_BUILD"):
        monkeypatch.delenv(var, raising=False)
    pytester.runpytest("Tests/test_build.py", "-q").assert_outcomes(skipped=1)
    monkeypatch.setenv("SE2MODKIT_SDK_BUILD", "1")
    pytester.runpytest("Tests/test_build.py", "-q").assert_outcomes(passed=1)
    monkeypatch.delenv("SE2MODKIT_SDK_BUILD")
    monkeypatch.setenv("MM_SDK_BUILD", "1")             # the mod's old prefix: an alias for one release
    pytester.runpytest("Tests/test_build.py", "-q").assert_outcomes(passed=1)


def test_cross_mod_guid_collision(pytester):
    toml = textwrap.dedent('''\
        [tool.se2modkit]
        gates = ["guids"]

        [tool.se2modkit.mods.Alpha]
        path = "mods/Alpha"
        prefix = "AL_"

        [tool.se2modkit.mods.Beta]
        path = "mods/Beta"
        prefix = "BE_"
        ''')
    files = {"pyproject.toml": toml, "Tests/conftest.py": NO_ENV, "Tests/test_gates.py": SHIM}
    files.update(_mod_files("AL_", "Alpha", ["guids"], rel="mods/Alpha", tests=False))
    files.update(_mod_files("BE_", "Beta", ["guids"], rel="mods/Beta", tests=False))
    _write(pytester, files)
    r = pytester.runpytest("Tests", "-q")
    r.assert_outcomes(passed=2, failed=1)              # each mod alone is fine; across the repo they collide
    r.stdout.fnmatch_lines([f"*{GUID}*Alpha*Beta*"])


def test_only_selected_gates_run(pytester):
    _single(pytester, ["json", "bundles"], extra={"Tests/test_other.py": "def test_other():\n    pass\n"})
    r = pytester.runpytest("Tests", "-q", "--se2-gate", "json")
    r.assert_outcomes(passed=1)
    assert any("1 deselected" in line or "2 deselected" in line for line in r.outlines), r.outlines


def test_fixtures_outside_a_mod_skip(pytester):
    pytester.makepyfile(test_plain="def test_needs_mod(mod_root):\n    pass\n")
    pytester.runpytest("-q").assert_outcomes(skipped=1)


def test_style_gate_surfaces_not_measured(pytester):
    report = {"blocks": {"MM_Thing": {"lods": [{"tris": 10, "materials": 1}]}}}
    _single(pytester, ["style"], extra={"Source/Model/build_report.json": json.dumps(report)})
    r = pytester.runpytest("Tests", "-q", "-W", "always")
    r.assert_outcomes(passed=1, warnings=1)
    r.stdout.fnmatch_lines(["*not measured*"])


@pytest.mark.parametrize("gate", ["fresh_clone", "determinism"])
def test_staging_gates_skip_without_a_stage_hook(pytester, gate):
    _single(pytester, [gate])
    r = pytester.runpytest("Tests", "-q", "-rs")
    r.assert_outcomes(skipped=1)
    r.stdout.fnmatch_lines(["*scratch_stage*"])


def test_bundles_fails_without_assets(pytester):
    _single(pytester, ["bundles"])
    import shutil
    shutil.rmtree(pytester.path / "Assets")
    r = pytester.runpytest("Tests", "-q")
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines(["*installed root*missing*"])


@pytest.mark.parametrize("want,outcome", [(1, "passed"), (5, "failed")])
def test_bundles_min_files_option(pytester, want, outcome):
    _single(pytester, ["bundles"], extra={})
    with open(pytester.path / "pyproject.toml", "a", encoding="utf-8") as fh:
        fh.write(f"\n[tool.se2modkit.gate_options.bundles]\nmin_files = {want}\n")
    r = pytester.runpytest("Tests", "-q")
    r.assert_outcomes(**{outcome: 1})
    if outcome == "failed":
        r.stdout.fnmatch_lines(["*holds 1 asset files, fewer than the 5*"])


BUILT_REPORT = {"blocks": {"MM_Thing": {"placement": {"total": 1, "occupied": 1, "faces": {}, "free_cells": []}}}}


@pytest.mark.parametrize("gate,message", [
    ("stages", "*MM_Thing: built but has no export.json*"),
    ("scaffold", "*MM_Thing: built but has no export.json*"),
    ("construction_roles", "*no golden states baseline*"),
    ("placement", "*MM_Thing: no exported NonFractured/MM_Thing.fbx*"),
])
def test_built_block_with_missing_inputs_fails(pytester, gate, message):
    extra = {"Source/Model/build_report.json": json.dumps(BUILT_REPORT)}
    if gate == "placement":
        extra["Source/Model/Models/MM_Thing/placement_exported.json"] = json.dumps(
            {"nonfractured": BUILT_REPORT["blocks"]["MM_Thing"]["placement"]})
    _single(pytester, [gate], extra=extra)
    r = pytester.runpytest("Tests", "-q")
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines([message])


def test_construction_roles_fails_for_a_block_missing_from_the_baseline(pytester):
    _single(pytester, ["construction_roles"], extra={
        "Source/Model/build_report.json": json.dumps(BUILT_REPORT),
        "Source/Model/Models/MM_Thing/export.json": json.dumps({"fractured": {"lods": []}}),
        "Tests/golden/states_baseline.json": json.dumps({"shared_def_guids": []})})
    r = pytester.runpytest("Tests", "-q")
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines(["*MM_Thing: built but not in the golden states baseline*"])


def test_stale_staging_warns(pytester):
    import os
    _single(pytester, [], extra={
        "Source/Model/build_report.json": json.dumps(BUILT_REPORT),
        "Source/Model/integration/Assets/Mini/x.def": "{}",
        "Tests/test_staged.py": "def test_a(staged_assets):\n    assert staged_assets is not None\n\n\n"
                                "def test_b(staged_assets):\n    assert staged_assets is not None\n"})
    old = pytester.path / "Source/Model/integration/Assets/Mini/x.def"
    os.utime(old, (1_000_000, 1_000_000))
    r = pytester.runpytest("Tests/test_staged.py", "-q", "-W", "always")
    r.assert_outcomes(passed=2, warnings=1)              # once per mod and session
    r.stdout.fnmatch_lines(["*older than build_report.json*"])
