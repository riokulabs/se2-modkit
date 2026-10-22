"""Incremental builds (`pipeline.incremental`, `Runner`): per-block keys, staleness, fragments and the merged report.
Offline: the block build is faked, so these check which blocks rebuild and what the report holds, not Blender."""

import functools
from pathlib import Path
import importlib.util
import json
import os
import sys
from dataclasses import dataclass

import pytest

from pipefix import SyntheticSpec
from se2modkit.pipeline import incremental as IN
from se2modkit.pipeline.runner import Runner


class Spec(SyntheticSpec):
    @property
    def cells(self):
        return tuple(max(1, round(s / 0.25)) for s in self.size)


# ------------------------------------------------------------------------------------------------ canon
def test_canon_is_deterministic_and_order_free():
    assert IN.canon({"b": 1, "a": (1.5, None)}) == IN.canon({"a": (1.5, None), "b": 1})
    assert IN.canon({3, 1, 2}) == IN.canon({2, 3, 1})
    assert IN.canon((1, 2)) != IN.canon([1, 2])          # a type change is a change
    assert IN.canon(Spec("XX_A")) == IN.canon(Spec("XX_A")) != IN.canon(Spec("XX_A", size=(1, 1, 2)))


def test_canon_sees_closure_values_and_defaults():
    def make(n):
        return lambda spec, lod: n
    assert IN.canon(make(1)) == IN.canon(make(1)) != IN.canon(make(2))

    def f(x, k=1):
        return x
    assert IN.canon(functools.partial(f, k=2)) != IN.canon(functools.partial(f, k=3))
    files = set()
    IN.canon(f, files)
    assert any(p.name == "test_incremental.py" for p in files)


def test_canon_refuses_an_address_repr():
    class Opaque:
        __slots__ = ()
    with pytest.raises(IN.Unstable):
        IN.canon(Opaque())


# ------------------------------------------------------------------------------------------------ sources
def _src(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def test_module_closure_follows_mod_imports_only(tmp_path):
    src = _src(tmp_path / "Source" / "Model", {
        "build_xx.py": "import sys\nfrom xxmod import catalog\n",
        "xxmod/__init__.py": "",
        "xxmod/catalog.py": "from . import boxes\nimport json\n",
        "xxmod/boxes.py": "def build(spec, lod):\n    from .shared import helper\n    return helper()\n",
        "xxmod/shared.py": "def helper():\n    return []\n",
        "xxmod/unused.py": "x = 1\n",
    })
    files, dynamic = IN.module_closure([src / "build_xx.py"], [src])
    names = sorted(p.relative_to(src).as_posix() for p in files)
    assert names == ["build_xx.py", "xxmod/__init__.py", "xxmod/boxes.py", "xxmod/catalog.py", "xxmod/shared.py"]
    assert not dynamic
    (src / "xxmod" / "shared.py").write_text("import importlib\nm = importlib.import_module('x')\n",
                                             encoding="utf-8")
    assert IN.module_closure([src / "build_xx.py"], [src])[1]


# ------------------------------------------------------------------------------------------------ the runner
CATALOG = """from dataclasses import dataclass


@dataclass
class Spec:
    name: str
    family: str = "box"
    size: tuple = (1.0, 1.0, 1.0)
    variant: str = ""

    @property
    def cells(self):
        return tuple(max(1, round(s / 0.25)) for s in self.size)


CAT = [Spec("XX_Box_Small", "box"), Spec("XX_Box_Large", "box", (1.0, 1.0, 2.0)), Spec("XX_Panel", "panel")]


def catalogue():
    return list(CAT)
"""


# The builder: what a block's build reads and writes happens in its own body (a read made there counts for that block
# only). No dynamic import here: "import:" stays in the test's build_block.
BUILD = """def build(spec, lod, reads=()):
    from pathlib import Path
    if spec is None:                          # never: the static import is what a test edits
        import xxtoy_shared  # noqa: F401
    for f in reads:
        f = str(f)
        if f.startswith("glob:"):                 # every *.json of a folder, as builders do
            for g in sorted(Path(f[5:]).glob("*.json")):
                g.read_text(encoding="utf-8")
        elif f.startswith("r+:"):
            with open(f[3:], "r+", encoding="utf-8") as fh:
                fh.read()
        elif f.startswith("log:"):                # a shared log the build appends to, then reads back
            with open(f[4:], "a", encoding="utf-8") as fh:
                fh.write(spec.name + "\\n")
            Path(f[4:]).read_text(encoding="utf-8")
        elif f.startswith("maybe:"):              # an optional override file
            try:
                open(f[6:], encoding="utf-8").close()
            except FileNotFoundError:
                pass
        elif not f.startswith("import:"):
            with open(f, encoding="utf-8") as fh:
                fh.read()
    return []
"""


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def toy(hooks):
    """A toy mod whose catalogue and builders live in its own source files (editing a file is a source change): three
    blocks of two families. The block build is faked and records what it built."""
    # this process may have run other tests first: what their code read outside any block or while one built (a log,
    # a listing of the system temp folder) is not this mod's input
    IN._PROCESS.clear()
    IN._PROCESS_SEEN.clear()
    IN._LAZY_READS.clear()
    IN._LAZY_SEEN.clear()
    src = _src(hooks.paths.source_model, {
        "xxtoy_catalog.py": CATALOG,
        "xxtoy_boxes.py": BUILD,
        "xxtoy_panels.py": BUILD.replace("        import xxtoy_shared  # noqa: F401\n", "        pass\n"),
        "xxtoy_shared.py": "SIZE = 1\n",
    })
    tag = abs(hash(str(src)))
    mods = {n: _load(src / f"{n}.py", f"{n}_{tag}") for n in ("xxtoy_catalog", "xxtoy_boxes", "xxtoy_panels")}

    class Toy:
        cat = mods["xxtoy_catalog"]
        Spec = cat.Spec
        catalogue = cat.CAT
        built: list = []
        reads: dict = {}          # block -> files its (fake) build reads

        def runner(self, **kw):
            r = Runner(hooks=hooks, builders={"box": mods["xxtoy_boxes"].build, "panel": mods["xxtoy_panels"].build},
                       catalogue=self.cat.catalogue, **kw)
            r.require_blender = lambda: None
            r.prepare = lambda: None
            r.vrt_ops = lambda: "ops"

            def build_block(spec, render):
                self.built.append(spec.name)
                for f in self.reads.get(spec.name, ()):
                    if str(f).startswith("import:"):         # a module first imported while the block builds
                        __import__(str(f)[7:])
                reads = self.reads.get(spec.name, ())
                fn = r.builders[spec.family]
                if reads:                                    # the reads happen in the builder's own body
                    IN.call_builder(fn, spec, 0, reads)     # as Runner.build_block calls a builder
                return {"cells": tuple(spec.cells), "lods": [(spec.name, 12)], "placement": {"total": 3},
                        "dummies": [], "provenance": []}
            r.build_block = build_block
            r.audit = lambda spec: {"elements": [], "faults": ()}
            return r

        def run(self, *argv, **kw):
            self.built.clear()
            assert self.runner(**kw).main(list(argv)) == 0
            return list(self.built)

    path = list(sys.path)
    yield Toy()
    sys.path[:] = path
    for m in mods.values():
        sys.modules.pop(m.__name__, None)


def _report(hooks):
    return hooks.paths.report.read_bytes()


def test_second_run_builds_nothing_and_writes_the_same_bytes(toy, hooks):
    assert toy.run("--no-render") == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]
    full = _report(hooks)
    assert toy.run("--no-render") == []
    assert _report(hooks) == full
    hooks.paths.report.unlink()                    # the fragments alone rebuild the report, byte for byte
    assert toy.run("--no-render") == []
    assert _report(hooks) == full
    assert toy.run("--no-render", "--force") == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]
    assert _report(hooks) == full


def test_a_source_change_rebuilds_the_blocks_that_import_it(toy, hooks):
    toy.run("--no-render")
    shared = hooks.paths.source_model / "xxtoy_shared.py"
    shared.write_text("SIZE = 2\n", encoding="utf-8")      # imported (inside a function) by the box builder only
    assert toy.run("--no-render") == ["XX_Box_Small", "XX_Box_Large"]
    assert toy.run("--no-render") == []
    (hooks.paths.source_model / "xxtoy_panels.py").write_text("def build(spec, lod):\n    return [1]\n",
                                                             encoding="utf-8")
    assert toy.run("--no-render") == ["XX_Panel"]


def test_spec_hooks_modkit_and_env_changes(toy, hooks, monkeypatch, capsys):
    toy.run("--no-render")
    toy.catalogue[1] = toy.Spec("XX_Box_Large", "box", (1.0, 1.0, 3.0))           # one catalogue entry
    assert toy.run("--no-render") == ["XX_Box_Large"]
    assert "stale XX_Box_Large: spec" in capsys.readouterr().out
    hooks.dent["box"] = dict(hooks.dent["box"], amp=0.03)                       # a family table: that family
    assert toy.run("--no-render") == ["XX_Box_Small", "XX_Box_Large"]
    hooks.decal_api.add(("labels.py", "stencil"))                               # a mod-wide table: every block
    assert len(toy.run("--no-render")) == 3
    monkeypatch.setenv("SE2MODKIT_PREVIEW", "tag")                               # render-only: nothing
    monkeypatch.setenv("XX_NO_QUIT", "1")
    assert toy.run("--no-render") == []
    monkeypatch.setenv("SE2MODKIT_LOD_BIAS", "1")                               # any other modkit setting: all
    assert len(toy.run("--no-render")) == 3
    monkeypatch.setattr(IN, "modkit_digest", lambda: "another modkit")
    assert len(toy.run("--no-render")) == 3
    assert toy.run("XX_Panel", "--no-render") == []


def test_the_style_profile_is_an_input(toy, hooks):
    root = hooks.paths.root
    (root / "XX_Mod.vrgproj").write_text("<Project/>\n", encoding="utf-8")
    py = root / "pyproject.toml"
    py.write_text('[tool.se2modkit]\nmod_name = "XX_Mod"\nprefix = "XX_"\nprofile = "vanilla"\n', encoding="utf-8")
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    py.write_text('[tool.se2modkit]\nmod_name = "XX_Mod"\nprefix = "XX_"\nprofile = "vanilla"\n'
                  '[tool.se2modkit.style]\nwarn_only = true\n', encoding="utf-8")
    assert len(toy.run("--no-render")) == 3


def test_an_unhashable_input_rebuilds_every_time(toy):
    class Opaque:
        __slots__ = ()

    @dataclass
    class OddSpec(toy.Spec):
        extra: object = None
    toy.catalogue[2] = OddSpec("XX_Panel", "panel", extra=Opaque())
    assert toy.run("--no-render") == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]
    assert toy.run("--no-render") == ["XX_Panel"]


def test_export_rebuilds_blocks_never_exported(toy, hooks, monkeypatch):
    toy.run("--no-render")
    exported = []

    def export(self_ops=None):
        for name in r.built:
            d = hooks.paths.models / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "export.json").write_text("{}", encoding="utf-8")
            exported.append(name)
    built = []
    r = toy.runner()
    r.export = export
    orig = r.build_block
    r.build_block = lambda spec, render: built.append(spec.name) or r.built.setdefault(spec.name, {}) or \
        orig(spec, render)
    assert r.main(["XX_Panel", "--export", "--no-render"]) == 0
    assert built == ["XX_Panel"] and exported == ["XX_Panel"]               # built, but never exported before
    r2 = toy.runner()
    r2.export = lambda ops=None: exported.append("again")
    assert r2.main(["XX_Panel", "--export", "--no-render"]) == 0 and exported == ["XX_Panel"]
    import shutil
    shutil.rmtree(hooks.paths.models / "XX_Panel")                        # the models went missing: rebuild
    assert toy.run("XX_Panel", "--export", "--no-render") == ["XX_Panel"]


def test_plan_worker_adopt_gives_the_full_build_bytes(toy, hooks, tmp_path):
    toy.run("--no-render")
    full = _report(hooks)
    for f in toy.runner().store().root.glob("*.json"):   # start over: no fragment, no report
        f.unlink()
    hooks.paths.report.unlink()
    status = tmp_path / "plan.json"
    assert toy.run("--no-render", "--plan", f"--status={status}") == []          # plan: nothing built, no report
    plan = json.loads(status.read_text(encoding="utf-8"))
    assert plan["stale"] == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"] and not hooks.paths.report.exists()
    w1, w2 = tmp_path / "w1", tmp_path / "w2"
    assert toy.run("XX_Box_Small", "XX_Panel", f"--worker={w1}") == ["XX_Box_Small", "XX_Panel"]
    assert toy.run("XX_Box_Large", f"--worker={w2}") == ["XX_Box_Large"]
    assert not hooks.paths.report.exists()                                        # workers write fragments only
    assert toy.run("--no-render", "--plan", f"--adopt={w1}", f"--adopt={w2}", f"--status={status}") == []
    assert json.loads(status.read_text(encoding="utf-8"))["stale"] == []
    assert _report(hooks) == full


def test_worker_refuses_export(toy, hooks, tmp_path, capsys):
    assert toy.runner().main([f"--worker={tmp_path}", "--export"]) == 2
    assert "--worker" in capsys.readouterr().err


def test_bad_value_option_is_a_plain_error(toy, capsys):
    assert toy.runner().main(["--status"]) == 2
    assert "--status=PATH" in capsys.readouterr().err


def test_store_is_per_report_and_tolerates_junk(tmp_path, monkeypatch):
    monkeypatch.setenv(IN.STATE_ENV, str(tmp_path))
    a = IN.Store.for_report(tmp_path / "a" / "build_report.json", "XX Mod")
    b = IN.Store.for_report(tmp_path / "b" / "build_report.json", "XX Mod")
    assert a.root != b.root and a.root.parent == tmp_path
    a.save("XX/odd", {"block": "XX/odd"})
    assert a.load("XX/odd") == {"block": "XX/odd"} and a.blocks() == ["XX/odd"]
    a.path("XX_B").write_text("{not json", encoding="utf-8")
    assert a.load("XX_B") is None and a.blocks() == ["XX/odd"]


# ------------------------------------------------------------------------------ helpers reached through sys.path
def _load_into(toy, name, text, hooks):
    """Write a mod module and load it (as a mod's build would), returning the module."""
    p = hooks.paths.source_model / f"{name}.py"
    p.write_text(text, encoding="utf-8")
    return _load(p, f"{name}_{abs(hash(str(p)))}")


def test_a_helper_reached_through_sys_path_is_hashed(toy, hooks):
    """The reproduced case: a builder imports a helper from mod/tools/ that a module-level sys.path edit made
    importable. Editing the helper rebuilds the block."""
    tools = hooks.paths.root / "tools"
    tools.mkdir()
    (tools / "xxhelp.py").write_text("WIDTH = 1\n", encoding="utf-8")
    m = _load_into(toy, "xxtoy_boxes2", "import sys\nfrom pathlib import Path\n"
                   "sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))\n"
                   "import xxhelp\n\n\ndef build(spec, lod):\n    return [xxhelp.WIDTH]\n", hooks)
    try:
        def run():
            toy.built.clear()
            r = toy.runner()
            r.builders["box"] = m.build
            assert r.main(["--no-render"]) == 0
            return list(toy.built)
        run()
        assert run() == []
        (tools / "xxhelp.py").write_text("WIDTH = 2\n", encoding="utf-8")
        assert run() == ["XX_Box_Small", "XX_Box_Large"]
    finally:
        sys.modules.pop(m.__name__, None)
        sys.modules.pop("xxhelp", None)


def test_a_sys_path_edit_that_has_not_run_hashes_the_whole_mod(toy, hooks):
    m = _load_into(toy, "xxtoy_boxes3", "def build(spec, lod):\n    import sys\n    sys.path.append('/x')\n"
                   "    return []\n", hooks)
    other = hooks.paths.root / "tools" / "anything.py"
    other.parent.mkdir()
    other.write_text("A = 1\n", encoding="utf-8")
    try:
        def run():
            toy.built.clear()
            r = toy.runner()
            r.builders["box"] = m.build
            assert r.main(["--no-render"]) == 0
            return list(toy.built)
        run()
        assert run() == []
        other.write_text("A = 2\n", encoding="utf-8")         # unrelated to the build, but unsure: rebuild
        assert run() == ["XX_Box_Small", "XX_Box_Large"]
    finally:
        sys.modules.pop(m.__name__, None)


def test_a_loaded_module_no_import_explains_counts_for_every_block(toy, hooks):
    toy.run("--no-render")
    extra = _load_into(toy, "xxtoy_plugin", "X = 1\n", hooks)     # loaded by some means no import shows
    try:
        assert len(toy.run("--no-render")) == 3
        assert toy.run("--no-render") == []
        (hooks.paths.source_model / "xxtoy_plugin.py").write_text("X = 2\n", encoding="utf-8")
        assert len(toy.run("--no-render")) == 3
    finally:
        sys.modules.pop(extra.__name__, None)


def test_a_file_the_build_reads_is_an_input(toy, hooks, capsys):
    data = hooks.paths.root / "data" / "panel.json"
    data.parent.mkdir()
    data.write_text('{"w": 1}', encoding="utf-8")
    toy.reads["XX_Panel"] = [data]
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    data.write_text('{"w": 2}', encoding="utf-8")                  # never declared anywhere
    assert toy.run("--no-render") == ["XX_Panel"]
    assert "stale XX_Panel: files read: panel.json" in capsys.readouterr().out
    data.unlink()                                                   # gone counts as changed
    toy.reads.clear()
    assert toy.run("--no-render") == ["XX_Panel"]


def test_a_file_read_outside_any_block_counts_for_every_block(toy, hooks):
    table = hooks.paths.root / "table.csv"
    table.write_text("a,1\n", encoding="utf-8")
    table.read_text(encoding="utf-8")                              # e.g. the catalogue loading its data
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    table.write_text("a,2\n", encoding="utf-8")
    assert len(toy.run("--no-render")) == 3


def test_declared_data_folders_are_walked(toy, hooks, tmp_path):
    d = tmp_path / "data"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "a.json").write_text("1", encoding="utf-8")
    assert IN.files_digest([d]) == IN.files_digest([d])
    before = IN.files_digest([d])
    (d / "sub" / "a.json").write_text("2", encoding="utf-8")
    assert IN.files_digest([d]) != before

    def run(**kw):
        toy.built.clear()
        assert toy.runner(inputs=lambda spec: [d] if spec.family == "panel" else []).main(["--no-render"]) == 0
        return list(toy.built)
    run()
    assert run() == []
    (d / "sub" / "b.json").write_text("new", encoding="utf-8")
    assert run() == ["XX_Panel"]


def test_addon_game_and_sdk_versions_are_inputs(toy, hooks, monkeypatch):
    monkeypatch.delenv("SE2MODKIT_GAME_VERSION", raising=False)
    monkeypatch.delenv("SE2MODKIT_SDK_BUILDID", raising=False)
    monkeypatch.setattr(IN, "discovered_versions", lambda: ("4.0.0.1", "42"))
    toy.run("--no-render")
    monkeypatch.setattr(IN, "addon_digest", lambda: "VRAGE Tools 9.9")
    assert len(toy.run("--no-render")) == 3
    monkeypatch.setenv("SE2MODKIT_SDK_BUILDID", "42")       # se2mod passing what discovery finds: the same key
    monkeypatch.setenv("SE2MODKIT_GAME_VERSION", "4.0.0.1")
    assert toy.run("--no-render") == []
    monkeypatch.setenv("SE2MODKIT_SDK_BUILDID", "43")       # an SDK update
    assert len(toy.run("--no-render")) == 3
    monkeypatch.setenv("SE2MODKIT_GAME_VERSION", "4.0.0.2")  # a game update
    assert len(toy.run("--no-render")) == 3
    assert toy.run("--no-render") == []


def _exporting(toy, hooks):
    r = toy.runner()
    orig = r.build_block

    def build_block(spec, render):
        r.built[spec.name] = {}
        return orig(spec, render)

    def export(ops=None):
        for name in r.built:
            d = hooks.paths.models / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "export.json").write_text("{}", encoding="utf-8")
            (d / f"{name}.fbx").write_text("model", encoding="utf-8")
    r.build_block, r.export = build_block, export
    return r


def test_export_freshness_follows_the_key_and_the_files(toy, hooks, monkeypatch):
    toy.built.clear()
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0
    assert toy.built == ["XX_Panel"]
    toy.built.clear()
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0 and toy.built == []
    (hooks.paths.models / "XX_Panel" / "XX_Panel.fbx").write_text("an older model", encoding="utf-8")  # reverted
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0 and toy.built == ["XX_Panel"]
    toy.built.clear()
    monkeypatch.setattr(IN, "addon_digest", lambda: "another add-on")       # a new VRAGE Tools: export again
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0 and toy.built == ["XX_Panel"]
    toy.built.clear()
    assert toy.run("XX_Panel", "--no-render", "--force") == ["XX_Panel"]       # same key, models still valid
    toy.built.clear()
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0 and toy.built == []


def test_messages_for_fresh_blocks_and_no_preview(toy, capsys):
    toy.run("--no-render")
    capsys.readouterr()
    shown = []
    assert toy.runner(preview=lambda built, tag: shown.append(built)).main([]) == 0
    out = capsys.readouterr().out
    assert "up to date: 3 block(s) (use --force to rebuild)" in out and "no preview: nothing rebuilt" in out
    assert shown == []


def test_blocks_only_rejects_wildcards_all_and_targets(toy, capsys):
    from se2modkit.pipeline.runner import Target
    r = toy.runner(targets={"items": Target("items", build=lambda r: {})})
    for words in (["all"], ["XX_Box*"], ["items"], ["XX_Nope"], []):
        assert r.main([*words, "--blocks-only"]) == 2
        assert "Not a block name" in capsys.readouterr().err
    assert toy.built == []


def test_the_build_lock(toy, hooks, monkeypatch, capsys):
    import os
    lock = IN.lock_path(hooks.paths.report)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{os.getppid()}\n", encoding="utf-8")        # a live process holds it
    assert toy.runner().main(["--no-render"]) == 2
    assert "Another build of XX_Mod is running" in capsys.readouterr().err and toy.built == []
    monkeypatch.setenv(IN.LOCK_ENV, str(lock))                   # a child of the holder runs under it
    assert toy.run("--no-render") == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]
    monkeypatch.delenv(IN.LOCK_ENV)
    lock.write_text("999999999\n", encoding="utf-8")              # its holder is gone: taken over
    assert toy.run("--no-render", "--force") and not lock.exists()


def test_report_write_is_atomic(tmp_path):
    from se2modkit.pipeline import report as RP
    path = tmp_path / "build_report.json"
    RP.write(path, {"blocks": {}}, modkit_provenance={"version": "1", "commit": None})
    before = path.read_bytes()

    def broken(p, data):
        p.write_text("{half", encoding="utf-8")
        raise OSError("disk full")
    with pytest.raises(OSError):
        RP.write(path, {"blocks": {"X": {}}}, writer=broken)
    assert path.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == ["build_report.json"]


# ------------------------------------------------------------------------ module-level reads and dynamic imports
def test_module_level_data_reads_are_inputs(toy, hooks):
    """A builder module that loads a table at import time (TABLE = json.loads(...)): the read happens before any
    block builds, so it counts for every block of the mod."""
    (hooks.paths.source_model / "dims.json").write_text('{"w": 1}', encoding="utf-8")
    m = _load_into(toy, "xxtoy_dims", "import json\nfrom pathlib import Path\nHERE = Path(__file__).parent\n"
                   "TABLE = json.loads((HERE / 'dims.json').read_text())\n\n\n"
                   "def build(spec, lod):\n    return [TABLE['w']]\n", hooks)
    try:
        def run():
            toy.built.clear()
            r = toy.runner()
            r.builders["box"] = m.build
            assert r.main(["--no-render"]) == 0
            return list(toy.built)
        run()
        assert run() == []
        (hooks.paths.source_model / "dims.json").write_text('{"w": 2}', encoding="utf-8")
        assert "XX_Box_Small" in run()
    finally:
        sys.modules.pop(m.__name__, None)


@pytest.mark.parametrize("code", [
    "from importlib import import_module as im\n\ndef f(x):\n    return im('pkg.' + x)\n",
    "import importlib as il\n\ndef f(x):\n    return il.import_module(x)\n",
    "import importlib\n\ndef f(x):\n    return getattr(importlib, 'import_module')(x)\n",
    "def f(x):\n    return __import__(x)\n",
    "def f(x, g=getattr):\n    return g(__builtins__, '__import__')(x)\n",
])
def test_any_mention_of_dynamic_import_is_dynamic(tmp_path, code):
    f = tmp_path / "m.py"
    f.write_text(code, encoding="utf-8")
    assert IN.module_closure([f], [tmp_path])[1]


def test_an_aliased_dynamic_import_rebuilds_every_block_sharing_it(toy, hooks):
    (hooks.paths.source_model / "xxtoy_dyn.py").write_text("W = 1\n", encoding="utf-8")
    m = _load_into(toy, "xxtoy_boxes4", "from importlib import import_module as im\n\n\n"
                   "def build(spec, lod):\n    return [im('xxtoy_' + 'dyn').W]\n", hooks)
    try:
        def run():
            toy.built.clear()
            r = toy.runner()
            r.builders["box"] = m.build
            assert r.main(["--no-render"]) == 0
            return list(toy.built)
        run()
        assert run() == []
        (hooks.paths.source_model / "xxtoy_dyn.py").write_text("W = 2\n", encoding="utf-8")
        assert {"XX_Box_Small", "XX_Box_Large"} <= set(run())
    finally:
        sys.modules.pop(m.__name__, None)


def test_a_lazy_import_counts_for_later_blocks_and_pyc_loads(tmp_path, monkeypatch):
    import py_compile
    mod = tmp_path / "xxlazy_helper_r2.py"
    mod.write_text("V = 1\n", encoding="utf-8")
    py_compile.compile(str(mod))                    # the import below loads the .pyc: no .py is opened
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with IN.recording() as first:
            __import__("xxlazy_helper_r2")
        assert "module:xxlazy_helper_r2" in first
        with IN.recording() as second:             # a later block: the module is cached, nothing is opened
            pass
        assert "module:xxlazy_helper_r2" not in second
        lazy = IN.lazy_imports(mod_dir=tmp_path)
        assert mod.resolve().as_posix() in lazy     # still counted for it
    finally:
        sys.modules.pop("xxlazy_helper_r2", None)


def test_a_read_change_clears_the_export_stamp(toy, hooks):
    """The reproduced sequence: export; a file the build reads changes; a plain (no export) run rebuilds; the next
    --export run must export again (the models on disk are from the old data)."""
    data = hooks.paths.root / "panel.json"
    data.write_text("1", encoding="utf-8")
    toy.reads["XX_Panel"] = [data]
    toy.built.clear()
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0 and toy.built == ["XX_Panel"]
    data.write_text("2", encoding="utf-8")
    assert toy.run("XX_Panel", "--no-render") == ["XX_Panel"]
    toy.built.clear()
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0 and toy.built == ["XX_Panel"]


def test_check_placement_output_does_not_unexport(toy, hooks):
    toy.built.clear()
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0
    (hooks.paths.models / "XX_Panel" / "placement_exported.json").write_text("{}", encoding="utf-8")
    toy.built.clear()
    assert _exporting(toy, hooks).main(["XX_Panel", "--export", "--no-render"]) == 0 and toy.built == []


# ------------------------------------------------------------------------- folder listings and import-time reads
def test_a_file_added_to_a_globbed_folder_makes_the_block_stale(toy, hooks, capsys):
    parts = hooks.paths.root / "parts"
    parts.mkdir()
    (parts / "a.json").write_text("1", encoding="utf-8")
    toy.reads["XX_Panel"] = [f"glob:{parts}"]
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    (parts / "b.json").write_text("2", encoding="utf-8")             # a new part: the listing changed
    assert toy.run("--no-render") == ["XX_Panel"]
    assert "files read: parts/" in capsys.readouterr().out
    (parts / "a.json").unlink()
    assert toy.run("--no-render") == ["XX_Panel"]


def test_a_folder_listed_at_import_time_counts_for_every_block(toy, hooks):
    parts = hooks.paths.root / "parts"
    parts.mkdir()
    (parts / "a.json").write_text("1", encoding="utf-8")
    sorted(parts.glob("*.json"))                                      # e.g. a catalogue globbing its parts
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    (parts / "b.json").write_text("2", encoding="utf-8")
    assert len(toy.run("--no-render")) == 3


@pytest.fixture
def os_tmp(tmp_path, monkeypatch):
    """The OS temp folder moved away from the test's own folders (pytest's tmp_path lives in the real one, which
    the read tracking leaves out), so a "workspace outside the mod" can sit in tmp_path."""
    import tempfile
    d = tmp_path / "os-tmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


def test_an_import_time_read_outside_the_mod_is_an_input(toy, hooks, tmp_path, os_tmp):
    shared = tmp_path / "workspace" / "shared.json"
    shared.parent.mkdir()
    shared.write_text("1", encoding="utf-8")
    shared.read_text(encoding="utf-8")                              # read at import time, outside the mod
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    shared.write_text("2", encoding="utf-8")
    assert len(toy.run("--no-render")) == 3


def test_a_read_through_a_symlink_leaving_the_mod_is_an_input(toy, hooks, tmp_path, os_tmp):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "t.json").write_text("1", encoding="utf-8")
    link = hooks.paths.source_model / "linked"
    link.symlink_to(elsewhere, target_is_directory=True)
    (link / "t.json").read_text(encoding="utf-8")
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    (elsewhere / "t.json").write_text("2", encoding="utf-8")
    assert len(toy.run("--no-render")) == 3


def test_an_absent_file_that_appears_makes_the_block_stale(toy, hooks):
    override = hooks.paths.root / "override.json"
    toy.reads["XX_Panel"] = [f"maybe:{override}"]
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    override.write_text("{}", encoding="utf-8")
    assert toy.run("--no-render") == ["XX_Panel"]


def test_an_r_plus_open_is_a_read(toy, hooks):
    f = hooks.paths.root / "state.json"
    f.write_text("1", encoding="utf-8")
    toy.reads["XX_Panel"] = [f"r+:{f}"]
    toy.run("--no-render")
    assert toy.run("--no-render") == []
    f.write_text("2", encoding="utf-8")
    assert toy.run("--no-render") == ["XX_Panel"]


def test_inputs_read_records_what_the_hook_cannot_see(tmp_path):
    from se2modkit.pipeline import inputs
    f = tmp_path / "tex.png"
    f.write_bytes(b"png")
    with IN.recording() as seen:
        assert inputs.read(f) == str(f)
    assert str(f) in seen


# -------------------------------------------------------------------------------- reads that belong to one block
def test_a_file_one_block_reads_rebuilds_only_that_block(toy, hooks):
    """N1: a read made while XX_Box_Small builds is that block's alone; the blocks built after it in the same process
    do not inherit it."""
    data = hooks.paths.source_model / "small.json"
    data.write_text("1", encoding="utf-8")
    toy.reads["XX_Box_Small"] = [data]                       # built first: Large and Panel follow in this process
    assert toy.run("--no-render") == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]
    assert toy.run("--no-render") == []
    data.write_text("2", encoding="utf-8")
    assert toy.run("--no-render") == ["XX_Box_Small"]
    store = toy.runner().store()
    assert data.as_posix() in store.load("XX_Box_Small")["reads"]
    assert data.as_posix() not in store.load("XX_Box_Large")["reads"]


def test_import_time_reads_inside_a_block_are_lazy_plain_reads_are_not(tmp_path, monkeypatch):
    (tmp_path / "t.json").write_text("1", encoding="utf-8")
    (tmp_path / "plain.json").write_text("1", encoding="utf-8")
    (tmp_path / "xxlazy_r4_unit.py").write_text("from pathlib import Path\n"
                                                "T = (Path(__file__).parent / 't.json').read_text()\n",
                                                encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        def build():
            __import__("xxlazy_r4_unit")
            (tmp_path / "plain.json").read_text(encoding="utf-8")
        with IN.recording(own=IN.own_code(build)) as seen:
            IN.call_builder(build)
        assert {str(tmp_path / "t.json"), str(tmp_path / "plain.json")} <= seen
        lazy = IN.lazy_imports(mod_dir=tmp_path)
        assert (tmp_path / "t.json").as_posix() in lazy and (tmp_path / "xxlazy_r4_unit.py").as_posix() in lazy
        assert (tmp_path / "plain.json").as_posix() not in lazy
        assert (tmp_path / "plain.json").as_posix() not in IN.process_reads(mod_dir=tmp_path)
    finally:
        sys.modules.pop("xxlazy_r4_unit", None)


def test_a_read_outside_any_block_keeps_what_it_saw_when_the_build_rewrites_it(tmp_path):
    """A file read outside any block (a table loaded at import) and then rewritten by the build is keyed by what was
    read: the blocks built from the old content rebuild on the next run."""
    import hashlib
    f = tmp_path / "data.txt"
    f.write_bytes(b"  1  \n")
    with IN.run_scope():
        f.read_text(encoding="utf-8")
        f.write_text("1\n", encoding="utf-8")
        reads = IN.process_reads(mod_dir=tmp_path)
    assert reads[f.as_posix()] == hashlib.sha256(b"  1  \n").hexdigest()


def test_a_decorators_wrapper_is_not_the_builders_own_code(tmp_path, monkeypatch):
    """The block's own code is the builder function unwrapped through __wrapped__ (and code nested in it): never the
    decorator's wrapper. A partial has no own code."""
    (tmp_path / "xxdeco_unit.py").write_text("import functools\n\n\ndef builder(fn):\n    @functools.wraps(fn)\n"
                                             "    def wrapper(*a, **k):\n        return fn(*a, **k)\n"
                                             "    return wrapper\n", encoding="utf-8")
    (tmp_path / "xxcat_unit.py").write_text("import functools\nimport xxdeco_unit\n\n\n@xxdeco_unit.builder\n"
                                            "def build(spec, lod):\n    return list(x for x in (1, 2))\n\n\n"
                                            "BUILDERS = {'box': functools.partial(build, lod=0)}\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        cat = __import__("xxcat_unit")
        deco = sys.modules["xxdeco_unit"]
        own = IN.own_code(cat.build)
        assert own.root is cat.build.__wrapped__.__code__
        assert any(c.co_name == "<genexpr>" for c in own)                  # code nested in the builder is its own
        assert all(c.co_filename == str(tmp_path / "xxcat_unit.py") for c in own)
        assert not any(c.co_filename.endswith("xxdeco_unit.py") for c in own)
        assert deco.builder.__code__ not in own
        assert IN.own_code(cat.BUILDERS["box"]).root is None                # a partial: no own code, all shared
    finally:
        sys.modules.pop("xxcat_unit", None)
        sys.modules.pop("xxdeco_unit", None)


def test_a_shared_log_rebuilds_every_run_with_one_warning_until_declared(toy, hooks, capsys):
    """Two blocks append to a shared log in the mod and read it back. Undeclared, the log is an input the build itself
    changes after a block read it: a block rebuilds on every run and every run prints one warning naming the file.
    Declared in outputs=, it settles."""
    log = hooks.paths.source_model / "shared.log"
    toy.reads["XX_Box_Small"] = [f"log:{log}"]
    toy.reads["XX_Panel"] = [f"log:{log}"]
    assert len(toy.run("--no-render")) == 3
    for _ in range(3):
        capsys.readouterr()
        assert toy.run("--no-render") != []
        warned = [x for x in capsys.readouterr().out.splitlines() if x.startswith("warning:")]
        assert len(warned) == 1 and "Source/Model/shared.log" in warned[0] and "outputs=" in warned[0]
    toy.run("--no-render", outputs=["Source/Model/*.log"])
    assert toy.run("--no-render", outputs=["Source/Model/*.log"]) == []
    capsys.readouterr()
    assert toy.run("--no-render", outputs=["Source/Model/*.log"]) == []
    assert "warning:" not in capsys.readouterr().out


def test_a_written_file_is_never_remembered_as_an_output(toy, hooks):
    """XX_Box_Small reads a log XX_Panel writes. Once XX_Panel stops writing it, a hand edit rebuilds XX_Box_Small: a
    file the build wrote is not an output for later runs (no list of written paths is kept)."""
    log = hooks.paths.source_model / "shared.log"
    log.write_text("", encoding="utf-8")
    toy.reads["XX_Box_Small"] = [log]
    toy.reads["XX_Panel"] = [f"log:{log}"]
    toy.run("--no-render")
    toy.reads["XX_Panel"] = []
    toy.run("--no-render", "XX_Panel", "--force")
    toy.run("--no-render")                                           # XX_Box_Small read the log before XX_Panel wrote
    assert toy.run("--no-render") == []
    log.write_text("edited\n", encoding="utf-8")
    assert toy.run("--no-render") == ["XX_Box_Small"]
    assert not list(toy.runner().store().root.glob(".*.json"))


def test_reads_from_the_builder_file_are_per_block_others_count_for_later_blocks(tmp_path):
    """Frame attribution: a read whose calling code is the block's own builder file stays with that block; one made by
    any other module (a shared helper, a cached loader) counts for that block and every later block of the process."""
    (tmp_path / "own.json").write_text("1", encoding="utf-8")
    (tmp_path / "helped.json").write_text("1", encoding="utf-8")
    (tmp_path / "xxhelper_r5.py").write_text(
        "import functools\nfrom pathlib import Path\n\n\n@functools.cache\n"
        "def table():\n    return (Path(__file__).parent / 'helped.json').read_text()\n", encoding="utf-8")
    helper = _load(tmp_path / "xxhelper_r5.py", "xxhelper_r5")
    try:
        def build():
            (tmp_path / "own.json").read_text(encoding="utf-8")
            helper.table()
        with IN.recording(own=IN.own_code(build)) as seen:
            IN.call_builder(build)
        assert {str(tmp_path / "own.json"), str(tmp_path / "helped.json")} <= seen
        lazy = IN.lazy_imports(mod_dir=tmp_path)
        assert (tmp_path / "helped.json").as_posix() in lazy and (tmp_path / "own.json").as_posix() not in lazy
        with IN.recording(own=[]) as seen:                       # not the builder's code: shared
            (tmp_path / "own.json").read_text(encoding="utf-8")
        assert (tmp_path / "own.json").as_posix() in IN.lazy_imports(mod_dir=tmp_path)
    finally:
        sys.modules.pop("xxhelper_r5", None)


def test_a_read_is_keyed_by_what_the_block_saw(tmp_path):
    """A read is hashed when it is made: a later write by someone else leaves the old digest (the block rebuilds next
    run); a file the block wrote itself is keyed by what it left (a rewrite in place settles)."""
    seen_by, rewritten = tmp_path / "seen.json", tmp_path / "rewritten.json"
    seen_by.write_text("1", encoding="utf-8")
    rewritten.write_text(" 1 ", encoding="utf-8")
    with IN.recording(own=[sys._getframe().f_code]) as seen:
        seen_by.read_text(encoding="utf-8")
        rewritten.write_text(rewritten.read_text(encoding="utf-8").strip(), encoding="utf-8")
    seen_by.write_text("2", encoding="utf-8")                        # a later block or worker writes it
    reads = IN.block_reads(seen, mod_dir=tmp_path)
    assert IN.reads_changed(reads) == [seen_by.as_posix()]
    assert IN.reads_changed(reads, ignore=[str(seen_by)]) == []      # the merge run's check skips the build's writes


def test_declared_outputs_are_never_inputs(toy, hooks):
    cache = hooks.paths.root / "cache"
    cache.mkdir()
    (cache / "x.bin").write_text("1", encoding="utf-8")
    (hooks.paths.root / "kept.json").write_text("1", encoding="utf-8")
    toy.reads["XX_Panel"] = [cache / "x.bin", hooks.paths.root / "kept.json"]
    toy.run("--no-render")                                            # undeclared: x.bin is a read
    (cache / "x.bin").write_text("2", encoding="utf-8")              # written outside a run: only the glob says so
    assert toy.run("--no-render", outputs=["cache/*.bin"]) == []
    assert toy.run("--no-render", outputs=["./cache"]) == []          # a folder covers what is under it
    assert toy.run("--no-render") == ["XX_Panel"]                     # undeclared: an input
    (hooks.paths.root / "kept.json").write_text("2", encoding="utf-8")
    assert toy.run("--no-render", outputs=["cache/**"]) == ["XX_Panel"]
    for bad in ("../x", "/abs/x", ""):
        with pytest.raises(ValueError, match="relative to the mod folder"):
            toy.runner(outputs=[bad])


def test_outputs_globs_match_inside_the_mod_only(tmp_path):
    o = IN.Outputs(globs=["Source/Model/*.log", "cache/**/*.bin", "out"], root=tmp_path)
    assert o.covers(tmp_path / "Source" / "Model" / "a.log")
    assert not o.covers(tmp_path / "Source" / "Model" / "sub" / "a.log")
    assert o.covers(tmp_path / "cache" / "a.bin") and o.covers(tmp_path / "cache" / "x" / "y" / "a.bin")
    assert o.covers(tmp_path / "out" / "deep" / "file.txt")
    assert not o.covers(tmp_path.parent / "out")


def test_a_read_under_the_system_temp_folder_is_an_input(toy, hooks):
    """N3: only the run's private temp folder is left out, never the system one, so a file under /tmp that a block
    reads is an input, as it is when build-models starts the run."""
    import tempfile
    fd, name = tempfile.mkstemp(suffix=".json")
    os_file = Path(name)
    try:
        os.close(fd)
        os_file.write_text("1", encoding="utf-8")
        toy.reads["XX_Panel"] = [os_file]
        toy.run("--no-render")
        assert toy.run("--no-render") == []
        os_file.write_text("2", encoding="utf-8")
        assert toy.run("--no-render") == ["XX_Panel"]
    finally:
        os_file.unlink(missing_ok=True)


def test_the_run_uses_a_private_temp_folder(toy, hooks):
    import tempfile
    before, env_before = tempfile.gettempdir(), os.environ.get("TMPDIR")
    seen = {}
    r = toy.runner()
    inner = r.build_block

    def build_block(spec, render):
        d = Path(tempfile.gettempdir())
        seen[spec.name] = (d, os.environ.get("TMPDIR"))
        (d / "scratch.json").write_text("1", encoding="utf-8")
        (d / "scratch.json").read_text(encoding="utf-8")
        return inner(spec, render)
    r.build_block = build_block
    assert r.main(["--no-render"]) == 0
    d, env = seen["XX_Panel"]
    assert d.name.startswith("se2modkit-run-") and env == str(d) and not d.exists()
    assert tempfile.gettempdir() == before and os.environ.get("TMPDIR") == env_before
    assert not any("scratch.json" in k for k in r.store().load("XX_Panel")["reads"])


def test_inputs_read_walks_a_folder(tmp_path):
    from se2modkit.pipeline import inputs
    parts = tmp_path / "parts"
    (parts / "sub").mkdir(parents=True)
    (parts / "a.json").write_text("1", encoding="utf-8")
    (parts / "sub" / "b.json").write_text("2", encoding="utf-8")
    with IN.recording() as seen:
        inputs.read(parts)
    assert {str(parts / "a.json"), str(parts / "sub" / "b.json"), f"dir:{parts}", f"dir:{parts / 'sub'}"} <= seen
    before = IN.read_hashes(seen, mod_dir=tmp_path)
    (parts / "sub" / "c.json").write_text("3", encoding="utf-8")
    assert IN.reads_changed(before) == [f"dir:{(parts / 'sub').as_posix()}"]


def test_import_scene_uses_blender_4_importers(monkeypatch, tmp_path):
    import types
    from se2modkit.pipeline import inputs
    calls = []
    op = lambda name: lambda **k: calls.append((name, k["filepath"]))   # noqa: E731
    bpy = types.SimpleNamespace(
        ops=types.SimpleNamespace(wm=types.SimpleNamespace(obj_import=op("wm.obj_import")),
                                  import_scene=types.SimpleNamespace(fbx=op("import_scene.fbx"))),
        path=types.SimpleNamespace(abspath=lambda p: p))
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    inputs.import_scene("obj", tmp_path / "a.obj")
    inputs.import_scene("fbx", tmp_path / "a.fbx")
    assert calls == [("wm.obj_import", str(tmp_path / "a.obj")), ("import_scene.fbx", str(tmp_path / "a.fbx"))]


# ------------------------------------------------------------------------------------------------ import-time listings,
# wrappers, parallel workers and processes that run the build more than once
def test_a_folder_a_module_lists_while_it_is_imported_is_recorded(tmp_path, monkeypatch):
    """A module's top-level code that lists a folder runs with import frames below it; the listing is still the
    module's own read. The import system's listing of the folder it finds the module in is not."""
    code, parts = tmp_path / "code", tmp_path / "parts"
    code.mkdir()
    parts.mkdir()
    (parts / "a.json").write_text("1", encoding="utf-8")
    (code / "xxlist_unit.py").write_text(f"import os\nPARTS = sorted(os.listdir({str(parts)!r}))\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(code))
    IN.install_read_hook()
    try:
        __import__("xxlist_unit")
        assert "dir:" + str(parts) in IN._PROCESS
        assert "dir:" + str(code) not in IN._PROCESS
    finally:
        sys.modules.pop("xxlist_unit", None)


def test_no_closure_is_followed_to_find_the_builders_own_code(tmp_path, monkeypatch):
    """Without __wrapped__, the builder's own code is the outermost function itself: the wrapper of a decorator that
    does not use functools.wraps, or what a factory made. A function it closes over (the decorated builder, a loader)
    is other code. A partial or a callable object has no own code."""
    (tmp_path / "xxplain_unit.py").write_text(
        "import functools\n\n\ndef builder(fn):\n    def wrapper(*a, **k):\n        return fn(*a, **k)\n"
        "    return wrapper\n\n\ndef table():\n    return 1\n\n\n"
        "def make(loader):\n    def build(spec, lod):\n        return [loader()]\n    return build\n\n\n"
        "def generic(spec, lod, size=1):\n    return [table(), size]\n\n\n"
        "class Builder:\n    def __call__(self, spec, lod):\n        return []\n", encoding="utf-8")
    (tmp_path / "xxpcat_unit.py").write_text(
        "import functools\nimport xxplain_unit\n\n\n@xxplain_unit.builder\ndef build(spec, lod):\n    return []\n\n\n"
        "BUILDERS = {'box': build, 'generic': functools.partial(xxplain_unit.generic, size=2),\n"
        "            'made': xxplain_unit.make(xxplain_unit.table), 'object': xxplain_unit.Builder()}\n",
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        cat = __import__("xxpcat_unit")
        shared = sys.modules["xxplain_unit"]
        wrapped = cat.build.__closure__[0].cell_contents
        own = IN.own_code(cat.build)
        assert own.root is cat.build.__code__ and wrapped.__code__ not in own
        made = IN.own_code(cat.BUILDERS["made"])
        assert made.root is cat.BUILDERS["made"].__code__ and shared.table.__code__ not in made
        assert IN.own_code(cat.BUILDERS["generic"]).root is None
        assert IN.own_code(cat.BUILDERS["object"]).root is None
    finally:
        sys.modules.pop("xxpcat_unit", None)
        sys.modules.pop("xxplain_unit", None)


def test_own_code_is_compared_by_identity(tmp_path):
    """Two code objects can be equal (same source and name) while coming from different files: only the builder's
    own object counts."""
    src = "def f(p):\n    with open(p, encoding='utf-8') as fh:\n        return fh.read()\n"
    ns_a, ns_b = {}, {}
    exec(compile(src, str(tmp_path / "a.py"), "exec"), ns_a)
    exec(compile(src, str(tmp_path / "b.py"), "exec"), ns_b)
    assert ns_a["f"].__code__ == ns_b["f"].__code__                        # equal, not the same
    data = tmp_path / "d.json"
    data.write_text("1", encoding="utf-8")
    own = IN.own_code(ns_a["f"])
    assert ns_a["f"].__code__ in own and ns_b["f"].__code__ not in own
    IN._LAZY_READS.discard(str(data))
    with IN.recording(own=own):
        IN.call_builder(ns_b["f"], str(data))
    assert str(data) in IN._LAZY_READS
    IN._LAZY_READS.discard(str(data))
    with IN.recording(own=own):
        IN.call_builder(ns_a["f"], str(data))
    assert str(data) not in IN._LAZY_READS


def test_the_path_from_the_builder_to_the_read_decides(tmp_path):
    """Only the builder's own code and I/O plumbing (open, pathlib, json ...) may sit between the builder and the read;
    a standard-library helper that keeps what it read (linecache), code compiled from a string, or a helper of the
    builder's own file make it count for later blocks too."""
    import json as _json
    import linecache
    data = tmp_path / "d.json"
    data.write_text("[1]", encoding="utf-8")
    ns = {}
    exec(compile("def gen(p):\n    return open(p).read()\n", "<generated>", "exec"), ns)

    def helper():
        return data.read_text(encoding="utf-8")

    def build(how):
        if how == "json":
            with open(data, encoding="utf-8") as f:
                _json.load(f)
        elif how == "pathlib":
            data.read_text(encoding="utf-8")
        elif how == "linecache":
            linecache.checkcache(str(data))
            linecache.getlines(str(data))
        elif how == "generated":
            ns["gen"](str(data))
        elif how == "helper":
            helper()
    own = IN.own_code(build)
    for how, shared in (("json", False), ("pathlib", False), ("linecache", True), ("generated", True),
                        ("helper", True)):
        IN._LAZY_READS.discard(str(data))
        with IN.recording(own=own):
            IN.call_builder(build, how)
        assert (str(data) in IN._LAZY_READS) is shared, how
    IN._LAZY_READS.discard(str(data))


def _shared(data, own, fn, *args) -> bool:
    """Whether a read of `data` made by calling `fn` the way the runner calls a builder counts for later blocks."""
    IN._LAZY_READS.discard(str(data))
    with IN.recording(own=own):
        IN.call_builder(fn, *args)
    out = str(data) in IN._LAZY_READS
    IN._LAZY_READS.discard(str(data))
    return out


def test_every_frame_up_to_the_runners_call_must_be_the_builders_own(tmp_path):
    """The whole path from the read to the runner's call into the builder decides, not just the frames up to the
    first frame of the builder's code: the builder re-entered from a lambda or a partial a shared helper runs and
    keeps, or a wrapper (memoizing or not) between the runner's call and the builder, makes the read count for later
    blocks, and so does a cache wrapper anywhere on the builder. Only a read under the runner's call, made along the
    builder's own code and plumbing, stays per-block."""
    data = tmp_path / "d.json"
    data.write_text("1", encoding="utf-8")
    kept = {}

    def once(key, fn):                       # a shared helper: not nested in the builder
        if key not in kept:
            kept[key] = fn()
        return kept[key]

    def build(how):
        if how == "read":
            data.read_text(encoding="utf-8")
        elif how == "lambda":
            once(how, lambda: build("read"))
        elif how == "partial":
            once(how, functools.partial(build, "read"))
        elif how == "nested":                # re-entered from the builder's own code only: still its own path
            (lambda: build("read"))()
    own = IN.own_code(build)
    assert not _shared(data, own, build, "read")
    assert not _shared(data, own, build, "nested")
    assert _shared(data, own, build, "lambda")
    assert _shared(data, own, build, "partial")

    memo = {}

    def keep(fn):                            # functools.wraps, one result per key
        @functools.wraps(fn)
        def wrapper(how):
            if "k" not in memo:
                memo["k"] = fn(how)
            return memo["k"]
        return wrapper

    def thin(fn):                            # functools.wraps, keeps nothing: still not the builder's own code
        @functools.wraps(fn)
        def wrapper(how):
            return fn(how)
        return wrapper
    for deco in (keep, thin):
        memo.clear()
        wrapped = deco(build)
        assert IN.own_code(wrapped).root is build.__code__
        assert _shared(data, IN.own_code(wrapped), wrapped, "read"), deco.__name__
    # a cache wrapper can hand one block's result to another (equal arguments) without running the builder: no own
    # code, at any layer of the __wrapped__ chain
    for cached in (functools.cache(build), functools.lru_cache(maxsize=4)(build), thin(functools.cache(build)),
                   functools.cache(thin(build))):
        assert IN.own_code(cached).root is None
        assert _shared(data, IN.own_code(cached), cached, "read")


def test_a_read_with_no_runner_call_below_it_counts_for_later_blocks(tmp_path):
    """Without the runner's call into the builder on the stack (the builder called directly, or `call_builder` given
    another function that then calls the builder), the read counts for later blocks even in the builder's code."""
    data = tmp_path / "d.json"
    data.write_text("1", encoding="utf-8")

    def build():
        data.read_text(encoding="utf-8")

    def other():
        build()
    own = IN.own_code(build)
    IN._LAZY_READS.discard(str(data))
    with IN.recording(own=own):
        build()
    assert str(data) in IN._LAZY_READS
    assert _shared(data, own, other)
    assert not _shared(data, own, build)


def test_a_read_the_mods_code_made_at_import_keeps_what_it_saw(tmp_path, monkeypatch):
    """A table the mod's code read at import is keyed by what was read even when another process (a parallel worker)
    rewrote the file before the block finished; in a later run of the same process, where the module still holds that
    table and nothing reads it again, it is unknown: never fresh."""
    import hashlib
    (tmp_path / "data.txt").write_bytes(b"  1  \n")
    (tmp_path / "xxtab_unit.py").write_text("from pathlib import Path\n"
                                            "T = (Path(__file__).parent / 'data.txt').read_text()\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    IN.install_read_hook()
    key = (tmp_path / "data.txt").as_posix()
    try:
        with IN.run_scope():
            __import__("xxtab_unit")
            (tmp_path / "data.txt").write_text("1\n", encoding="utf-8")      # as another worker would, unseen here
            pinned = set()
            assert IN.process_reads(mod_dir=tmp_path, pinned=pinned)[key] == hashlib.sha256(b"  1  \n").hexdigest()
            assert key in pinned
        with IN.run_scope():
            assert IN.process_reads(mod_dir=tmp_path)[key].startswith("unknown:")
            assert not IN.process_reads(mod_dir=tmp_path / "elsewhere").get(key, "").startswith("unknown:")
    finally:
        sys.modules.pop("xxtab_unit", None)


def test_a_cached_read_from_an_earlier_run_is_unknown(tmp_path, monkeypatch):
    """A shared helper's functools.cache read the table in an earlier run of this process; a block built in a later
    run uses the cached value without reading the file, so what it used cannot be told: unknown, never fresh."""
    (tmp_path / "t.json").write_text("1", encoding="utf-8")
    (tmp_path / "xxcache_unit.py").write_text("import functools\nfrom pathlib import Path\n\n\n@functools.cache\n"
                                              "def table():\n    return (Path(__file__).parent / 't.json').read_text()\n",
                                              encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    key = (tmp_path / "t.json").as_posix()
    try:
        m = __import__("xxcache_unit")
        with IN.run_scope():
            with IN.recording(own=[sys._getframe().f_code], mod_dir=tmp_path):
                m.table()
            assert not IN.lazy_imports(mod_dir=tmp_path)[key].startswith("unknown:")
        with IN.run_scope():
            with IN.recording(own=[sys._getframe().f_code], mod_dir=tmp_path):
                m.table()                                  # cached: no read
            assert IN.lazy_imports(mod_dir=tmp_path)[key].startswith("unknown:")
    finally:
        sys.modules.pop("xxcache_unit", None)


def test_a_module_edited_since_it_was_loaded_is_never_fresh(tmp_path, monkeypatch):
    """A process that loaded a mod module and then sees its file edited still runs the old code: its sources input is
    unstable, so the block rebuilds (and does so again in a fresh process)."""
    src = tmp_path / "xxedit_unit.py"
    src.write_text("X = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    IN.install_read_hook()
    try:
        __import__("xxedit_unit")
        assert not IN.edited_since_loaded(src)
        sources = IN.Sources(tmp_path, tmp_path, [src])
        assert ":edited:" not in sources.digest({src})
        src.write_text("X = 2\n", encoding="utf-8")
        assert IN.edited_since_loaded(src)
        assert ":edited:" in sources.digest({src})
    finally:
        sys.modules.pop("xxedit_unit", None)


def test_a_table_loaded_at_import_never_settles_in_a_process_that_keeps_the_old_one(toy, hooks):
    """Several runs in one process: a mod module loaded a table at import. After an edit the blocks rebuild, but the
    module still holds the old table, so they must not be recorded as fresh: they rebuild on every run of this
    process (a fresh build process then settles them)."""
    (hooks.paths.source_model / "dims.json").write_text("1", encoding="utf-8")
    m = _load_into(toy, "xxtoy_dims_lp", "from pathlib import Path\n"
                   "DIMS = (Path(__file__).parent / 'dims.json').read_text()\n\n\n"
                   "def build(spec, lod):\n    return [DIMS]\n", hooks)
    try:
        def run():
            toy.built.clear()
            r = toy.runner()
            r.builders["box"] = m.build
            assert r.main(["--no-render"]) == 0
            return list(toy.built)
        run()
        assert run() == []
        (hooks.paths.source_model / "dims.json").write_text("2", encoding="utf-8")
        assert run() == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]
        assert run() == ["XX_Box_Small", "XX_Box_Large", "XX_Panel"]
    finally:
        sys.modules.pop(m.__name__, None)
