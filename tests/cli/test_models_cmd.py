"""`se2mod build-models` and `se2mod check` against a toy mod and a fake Blender (a Python script that runs the build
script the way Blender would, without bpy): plan, parallel workers, merge, failures, and the check summary line."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from fake_env import make_env

from se2modkit import cli
from se2modkit import modelbuild as MB
from se2modkit.cli import models_cmd as MC
from se2modkit.env import procs

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake Blender is a POSIX script")

FAKE_BLENDER = """import builtins, importlib, json, os, runpy, sys
from pathlib import Path


def xxtoy_do(block):
    # the "import:MODULE" actions of XXTOY_DO (the others run in the builder, catalog.build); defined here, outside
    # the mod, so the mod's own sources stay free of dynamic imports
    for act in json.loads(os.environ.get("XXTOY_DO", "{}")).get(block, []):
        kind, _, arg = act.partition(":")
        if kind == "import":
            importlib.import_module(arg)


builtins.xxtoy_do = xxtoy_do
argv = sys.argv[1:]
exec(argv[argv.index("--python-expr") + 1])
script = argv[argv.index("--python") + 1]
sys.argv = [script] + argv
runpy.run_path(script, run_name="__main__")
"""

BUILD = '''import json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xxtoy import catalog  # noqa: E402
from se2modkit.pipeline import ModHooks  # noqa: E402
from se2modkit.pipeline import incremental as IN  # noqa: E402
from se2modkit.pipeline.runner import Runner, script_argv  # noqa: E402
from se2modkit.project import load_project  # noqa: E402


class ToyRunner(Runner):
    def require_blender(self):
        pass

    def prepare(self):
        pass

    def build_block(self, spec, render):
        if os.environ.get("XXTOY_SLEEP") == spec.name:
            Path(os.environ["XXTOY_PIDS"]).joinpath(str(os.getpid())).write_text("", encoding="utf-8")
            time.sleep(120)
        if os.environ.get("XXTOY_FAIL") == spec.name:
            t0 = time.monotonic()      # fail once the sleeper sleeps
            while os.environ.get("XXTOY_PIDS") and not any(Path(os.environ["XXTOY_PIDS"]).iterdir()) \\
                    and time.monotonic() - t0 < 20:
                time.sleep(0.05)
            raise RuntimeError("the toy builder broke on " + spec.name)
        if os.environ.get("XXTOY_DO"):
            xxtoy_do(spec.name)  # noqa: F821 (the fake Blender's)
        IN.call_builder(self.builders[spec.family], spec, 0)     # as Runner.build_block calls a builder
        with open(os.environ["XXTOY_LOG"], "a", encoding="utf-8") as f:
            f.write(spec.name + "\\n")
        return {"cells": list(spec.cells), "lods": [[spec.name, 12]], "placement": {"total": 2},
                "dummies": [], "provenance": [], "tmp": Path(os.environ.get("TMPDIR", "")).parent.name.endswith("tmp")
                and Path(os.environ["TMPDIR"]).name.startswith("se2modkit-run-")}

    def audit(self, spec):
        return {"elements": []}


def main():
    mod = load_project(Path(__file__).resolve().parent).mod(None)
    r = ToyRunner(hooks=ModHooks.for_mod(mod), builders=catalog.BUILDERS, catalogue=catalog.catalogue,
                  outputs=json.loads(os.environ.get("XXTOY_OUTPUTS", "[]")))
    return r.launch(script_argv())


if __name__ == "__main__":
    sys.exit(main())
'''

CATALOG = '''import functools
import json
import linecache
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from xxtoy import cat2, common
from xxtoy.common import generic

# a table read when the module is imported, outside any block's build (XXTOY_IMPORT_READ: its path)
TABLE = Path(os.environ["XXTOY_IMPORT_READ"]).read_text(encoding="utf-8") if os.environ.get("XXTOY_IMPORT_READ") \\
    else None
# a folder listed when the module is imported (XXTOY_IMPORT_LIST: its path)
PARTS = sorted(os.listdir(os.environ["XXTOY_IMPORT_LIST"])) if os.environ.get("XXTOY_IMPORT_LIST") else None
# each process that imports this module takes the next free number in XXTOY_MARKS (0, 1, 2 ...), so a block can wait
# until the other parallel worker has imported it ("wait:" below)
if os.environ.get("XXTOY_MARKS"):
    for i in range(100):
        try:
            os.close(os.open(os.path.join(os.environ["XXTOY_MARKS"], str(i)), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            continue


# XXTOY_FROZEN: a frozen spec that compares (and hashes) without its name, so two blocks' specs can be equal
@dataclass(frozen=bool(os.environ.get("XXTOY_FROZEN")))
class Spec:
    name: str = field(compare=not os.environ.get("XXTOY_FROZEN"))
    family: str = "box"
    size: tuple = (1.0, 1.0, 1.0)

    @property
    def cells(self):
        return tuple(max(1, round(s / 0.25)) for s in self.size)


# XXTOY_DECO: the builder's decorator ("wraps": common.py's functools.wraps, "plain": common.py's without wraps,
# "memo": common.py's functools.wraps keeping one result per family, "cache": functools.cache); unset: none
_DECO = {"wraps": common.builder, "plain": common.plain, "memo": common.memo,
         "cache": functools.cache}.get(os.environ.get("XXTOY_DECO", ""), lambda fn: fn)


@_DECO
def build(spec, lod):
    if spec.name.startswith("__read__:"):       # re-entered through a shared helper ("reenter:" below)
        return Path(spec.name[9:]).read_text(encoding="utf-8")
    # what the block's build reads and writes, here in the builder's own file (XXTOY_DO: {block: ["read:PATH",
    # "log:PATH" (append, read back), "copy:SRC>DST", "strip:PATH" (rewrite in place), "table:" (cat2's cached
    # table), "common:" (the cached table of common.py, the module that also holds the builder's decorator)]})
    for act in json.loads(os.environ.get("XXTOY_DO", "{}")).get(spec.name, []):
        kind, _, arg = act.partition(":")
        if kind == "wait":                      # until PATH exists (a stat: not a read), at most 15 s
            t0 = time.monotonic()
            while not os.path.exists(arg) and time.monotonic() - t0 < 15:
                time.sleep(0.02)
        elif kind == "read":
            Path(arg).read_text(encoding="utf-8")
        elif kind == "open":
            with open(arg, encoding="utf-8") as f:
                f.read()
        elif kind == "json":
            with open(arg, encoding="utf-8") as f:
                json.load(f)
        elif kind == "linecache":               # a stdlib helper that keeps what it read
            linecache.getlines(arg)
        elif kind == "gen":                     # a helper compiled from a string (xxtoy_gen, imported lazily)
            sys.modules["xxtoy_gen"].gen_table(arg)
        elif kind == "once":                    # a loader nested here, but run and kept by a shared helper
            def load():
                return Path(arg).read_text(encoding="utf-8")
            common.once(arg, load)
        elif kind == "reenter":                 # this builder re-entered from a lambda a shared helper runs and keeps
            common.once(arg, lambda: build(Spec("__read__:" + arg), 0))
        elif kind == "reenter_partial":         # the same through a partial
            common.once(arg, functools.partial(build, Spec("__read__:" + arg), 0))
        elif kind == "log":
            with open(arg, "a", encoding="utf-8") as f:
                f.write(spec.name + "\\n")
            Path(arg).read_text(encoding="utf-8")
        elif kind == "copy":
            src, dst = arg.split(">")
            Path(dst).write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")
        elif kind == "strip":
            Path(arg).write_text(Path(arg).read_text(encoding="utf-8").strip() + "\\n", encoding="utf-8")
        elif kind == "table":
            cat2.table()
        elif kind == "common":
            common.table()
    return []


# XXTOY_BUILDERS: the builder is shared code from common.py ("partial": a partial of its generic function made here,
# "generic": the function itself, "import": the function imported by name, "common_partial": a partial common.py
# declares, "common_builders": common.py's own builder table); unset: this module's build
BUILDERS = {
    "partial": lambda: {"box": functools.partial(common.generic, size=2)},
    "generic": lambda: {"box": common.generic},
    "import": lambda: {"box": generic},
    "common_partial": lambda: {"box": common.PARTIAL},
    "common_builders": lambda: common.BUILDERS,
    "factory": lambda: {"box": common.make_builder(common.table2)},
    "deco_factory": lambda: {"box": common.with_loader(common.table2)(build)},
    "factory_plain": lambda: {"box": common.make_builder(common.table3)},
    "deco_factory_plain": lambda: {"box": common.with_loader(common.table3)(build)},
}.get(os.environ.get("XXTOY_BUILDERS", ""), lambda: {"box": build})()
NAMES = ["XX_A", "XX_B", "XX_C", "XX_D", "XX_E"]


def catalogue():
    return [Spec(n) for n in NAMES]
'''

CAT2 = '''import functools
import json
from pathlib import Path


@functools.cache
def table():
    return json.loads((Path(__file__).parent / "table.json").read_text(encoding="utf-8"))
'''

COMMON = '''import functools
import json
import os
from pathlib import Path


def builder(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper


def plain(fn):
    # a decorator that does not use functools.wraps: the wrapper hides the builder
    def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper


@functools.cache
def table():
    return json.loads((Path(__file__).parent / "table.json").read_text(encoding="utf-8"))


def generic(spec, lod, size=1):
    table()
    return []


PARTIAL = functools.partial(generic, size=3)
BUILDERS = {"box": generic}


@functools.cache
def table2():
    return json.loads((Path(__file__).parent / "table.json").read_text(encoding="utf-8"))


_TABLE3 = {}


def table3():
    # a plain function that keeps what it read in a module-level memo
    if "t" not in _TABLE3:
        _TABLE3["t"] = json.loads((Path(__file__).parent / "table.json").read_text(encoding="utf-8"))
    return _TABLE3["t"]


def make_builder(loader):
    # a builder factory: the builder it makes closes over a shared loader
    def build(spec, lod):
        loader()
        return []
    return build


def with_loader(loader):
    # a decorator factory without functools.wraps whose wrapper calls a shared loader
    def deco(fn):
        def wrapper(*a, **k):
            loader()
            return fn(*a, **k)
        return wrapper
    return deco


_MEMO = {}


def memo(fn):
    # a functools.wraps decorator that keeps one result per family: a later block of the family gets the first one's
    # without running the builder (each such block is logged to XXTOY_MEMO_LOG as "BLOCK:FIRST")
    @functools.wraps(fn)
    def wrapper(spec, lod):
        key = (spec.family, lod)
        if key not in _MEMO:
            _MEMO[key] = (spec.name, fn(spec, lod))
        elif os.environ.get("XXTOY_MEMO_LOG"):
            with open(os.environ["XXTOY_MEMO_LOG"], "a", encoding="utf-8") as f:
                f.write(spec.name + ":" + _MEMO[key][0] + "\\n")
        return _MEMO[key][1]
    return wrapper


_ONCE = {}


def once(key, fn):
    if key not in _ONCE:
        _ONCE[key] = fn()
    return _ONCE[key]
'''


# a shared helper compiled from a string, so its code's file name is "<generated>"; it keeps what it read
GEN = '''_SRC = """
_memo = {}


def gen_table(path):
    if path not in _memo:
        with open(path, encoding="utf-8") as f:
            _memo[path] = f.read()
    return _memo[path]
"""
exec(compile(_SRC, "<generated>", "exec"), globals())
'''


@pytest.fixture
def toy(tmp_path, monkeypatch):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "Toy Mod"
    sm = root / "Source" / "Model"
    (sm / "xxtoy").mkdir(parents=True)
    (root / "ToyMod.vrgproj").write_text("{}", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "ToyMod"\nprefix = "XX_"\n', encoding="utf-8")
    (sm / "build_toymod.py").write_text(BUILD, encoding="utf-8")
    (sm / "xxtoy" / "__init__.py").write_text("", encoding="utf-8")
    (sm / "xxtoy" / "catalog.py").write_text(CATALOG, encoding="utf-8")
    (sm / "xxtoy" / "cat2.py").write_text(CAT2, encoding="utf-8")
    (sm / "xxtoy" / "common.py").write_text(COMMON, encoding="utf-8")
    (sm / "xxtoy_gen.py").write_text(GEN, encoding="utf-8")
    blender = tmp_path / "bin" / "blender"
    blender.parent.mkdir()
    (tmp_path / "bin" / "fake_blender.py").write_text(FAKE_BLENDER, encoding="utf-8")
    blender.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{blender.parent / "fake_blender.py"}" "$@"\n',
                       encoding="utf-8")
    blender.chmod(blender.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "built.log"
    monkeypatch.setenv("XXTOY_LOG", str(log))
    monkeypatch.setattr(procs, "listing_available", lambda **k: True)
    monkeypatch.setattr(procs, "game_running", lambda **k: False)
    monkeypatch.chdir(root)
    env = make_env(tmp_path, blender=blender)

    class Toy:
        def __init__(self):
            self.root, self.env, self.report = root, env, sm / "build_report.json"

        def built(self):
            names = log.read_text(encoding="utf-8").split() if log.exists() else []
            log.unlink(missing_ok=True)
            return sorted(names)

        out = ""      # what the last run() printed

        def direct(self, *blocks, force=False):
            """The build script run straight in (fake) Blender, as `se2mod blender run` does."""
            import subprocess
            from se2modkit.cli.blender_cmd import blender_argv, build_env
            argv = blender_argv(env, sm / "build_toymod.py", [*blocks, "--no-render", *(["--force"] if force else [])],
                                mod_source=sm, windowed=False)
            r = subprocess.run(argv, env=build_env(env), cwd=root, capture_output=True, text=True, timeout=60,
                               check=False)
            assert r.returncode == 0, r.stdout + r.stderr
            self.out = r.stdout + r.stderr
            return 0

        def run(self, path, *blocks, force=False):
            """One build in fresh processes: "direct", or build-models with that many jobs (1, 2); the blocks it
            built, sorted (what it printed in `out`)."""
            import contextlib
            import io
            if path == "direct":
                self.direct(*blocks, force=force)
            else:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = self.build(*blocks, jobs=path, force=force)
                self.out = buf.getvalue()
                assert rc == 0, self.out
            return self.built()

        def warnings(self, name):
            return [x for x in self.out.splitlines() if x.startswith("warning:") and name in x]

        def build(self, *blocks, jobs=3, force=False, export=False, capsys=None):
            ns = argparse.Namespace(mod="ToyMod", blocks=list(blocks), jobs=jobs, export=export, render=False,
                                    force=force, script=None, timeout=60, assume_game_closed=False)
            return MC.cmd_build_models(ns, env)
    return Toy()


def test_registered():
    assert {"build-models", "check"} <= set(cli.commands())


def test_split_balances_by_last_build_time():
    assert MB.split(["A", "B", "C", "D"], 2) == [["A", "C"], ["B", "D"]]
    assert MB.split(["A", "B", "C", "D"], 2, {"A": 10.0, "B": 1.0, "C": 1.0, "D": 1.0}) == [["A"], ["B", "C", "D"]]
    assert MB.split(["A"], 4) == [["A"]]
    assert MB.split(["A", "B", "C"], 8, {"A": None}) == [["A"], ["B"], ["C"]]


def test_parallel_build_matches_serial_bytes_and_skips_fresh_blocks(toy, capsys):
    assert toy.build(jobs=1) == 0
    assert toy.built() == ["XX_A", "XX_B", "XX_C", "XX_D", "XX_E"]
    serial = toy.report.read_bytes()
    assert "built 5 of 5" in capsys.readouterr().out
    assert toy.build(jobs=3) == 0                                    # nothing stale: one plan run, no worker
    assert toy.built() == [] and toy.report.read_bytes() == serial
    assert "up to date" in capsys.readouterr().out
    assert toy.build(jobs=3, force=True) == 0                         # three workers, merged
    assert toy.built() == ["XX_A", "XX_B", "XX_C", "XX_D", "XX_E"]
    assert toy.report.read_bytes() == serial
    out = capsys.readouterr().out
    assert "built 5 of 5" in out and "3 workers" in out
    data = json.loads(serial)
    assert data["blocks"]["XX_A"]["tmp"] is True     # each Blender ran in its own temp dir, the run in a private one
    assert not list((toy.env.cache_dir / "build-runs").iterdir())     # a good run leaves nothing behind


def test_a_source_edit_rebuilds_only_what_reads_it(toy, capsys):
    toy.build(jobs=2)
    toy.built()
    cat = toy.root / "Source" / "Model" / "xxtoy" / "catalog.py"
    cat.write_text(cat.read_text(encoding="utf-8").replace('"XX_E"]', '"XX_E", "XX_F"]'), encoding="utf-8")
    assert toy.build("XX_A", "XX_F", jobs=2) == 0           # the catalogue module changed: both selected rebuild
    assert toy.built() == ["XX_A", "XX_F"]
    assert toy.build(jobs=2) == 0
    assert toy.built() == ["XX_B", "XX_C", "XX_D", "XX_E"]


def test_a_failing_worker_names_the_block_and_stops_the_rest(toy, monkeypatch, capsys, tmp_path):
    pids = tmp_path / "pids"
    pids.mkdir()
    monkeypatch.setenv("XXTOY_PIDS", str(pids))
    monkeypatch.setenv("XXTOY_SLEEP", "XX_B")          # worker 2 (XX_B, XX_E)
    monkeypatch.setenv("XXTOY_FAIL", "XX_C")           # worker 3 (XX_C)
    t0 = time.monotonic()
    assert toy.build(jobs=3) == 1
    assert time.monotonic() - t0 < 40
    err = capsys.readouterr().err
    assert "XX_C failed in worker-3 of 3" in err and "the toy builder broke on XX_C" in err and "log:" in err
    assert not toy.report.exists()
    assert len(list(pids.iterdir())) == 1
    for p in pids.iterdir():                                       # the sleeping worker was stopped and reaped
        with pytest.raises(ProcessLookupError):
            os.kill(int(p.name), 0)
    runs = list((toy.env.cache_dir / "build-runs").iterdir())
    assert len(runs) == 1 and list(runs[0].glob("worker-*.log"))   # the failed run keeps its logs
    assert not [d for d in runs[0].rglob("*") if d.is_dir() and (d.name == "tmp" or d.name.endswith("-tmp"))]


def test_plain_errors(toy, monkeypatch, capsys):
    monkeypatch.setattr(procs, "game_running", lambda **k: True)
    assert toy.build() == 4 and "is running" in capsys.readouterr().err
    monkeypatch.setattr(procs, "game_running", lambda **k: False)
    assert toy.build("XX_Nope", jobs=1) == 1
    err = capsys.readouterr().err
    assert "Unknown block(s): XX_Nope" in err and "Traceback" not in err
    (toy.root / "Source" / "Model" / "build_other.py").write_text("", encoding="utf-8")
    assert toy.build() == 1 and "exactly one build_*.py" in capsys.readouterr().err
    assert toy.build(jobs=0) == 2


# ------------------------------------------------------------------------------------------------ check
def _check(toy, block="XX_B", **kw):
    ns = argparse.Namespace(mod="ToyMod", block=block, set_name=None, force=kw.get("force", False), script=None,
                            timeout=60, assume_game_closed=False)
    return MC.cmd_check(ns, toy.env)


def test_check_prints_one_summary_line(toy, monkeypatch, capsys):
    calls = []

    def build_models(env, **kw):
        calls.append(kw)
        return MB.BuildResult(["XX_B"], ["XX_B"] if len(calls) == 1 else [], 1.0)

    def cmd_test(ns, env):
        assert ns.block == ["XX_B"] and ns.mod == "ToyMod"
        print("static selection — `se2mod test --full` is the gate")
        print("4 passed, 1 skipped in 0.5s | ToyMod: 2 test files, serial")
        return 0

    def preview_shots(ns, env):
        assert ns.still and ns.block == ["XX_B"]
        print("/previews/review/XX_B_front.png")
        print("1 image(s) in /previews/review")
        return 0
    monkeypatch.setattr(MB, "build_models", build_models)
    monkeypatch.setattr("se2modkit.cli.testrun_cmd.cmd_test", cmd_test)
    monkeypatch.setattr("se2modkit.cli.preview_cmd.preview_shots", preview_shots)
    assert _check(toy) == 0
    out = capsys.readouterr().out
    assert out == "XX_B: rebuilt yes | tests 4 passed, 1 skipped in 0.5s | still /previews/review/XX_B_front.png\n"
    assert calls[0]["words"] == ["XX_B"] and calls[0]["export"] and calls[0]["jobs"] == 1
    assert _check(toy) == 0 and "rebuilt no" in capsys.readouterr().out


def test_check_reports_failures_in_one_line(toy, monkeypatch, capsys):
    monkeypatch.setattr(MB, "build_models", lambda env, **kw: MB.BuildResult(["XX_B"], [], 1.0))

    def cmd_test(ns, env):
        print("1 failed, 3 passed in 0.5s FAILED (pytest exit 1) | ToyMod: 2 test files, serial")
        print("___ test_x ___")
        return 1

    def preview_shots(ns, env):
        print("error: ToyMod has no previews.toml (in here); see the format.", file=sys.stderr)
        return 1
    monkeypatch.setattr("se2modkit.cli.testrun_cmd.cmd_test", cmd_test)
    monkeypatch.setattr("se2modkit.cli.preview_cmd.preview_shots", preview_shots)
    assert _check(toy) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == ["XX_B: rebuilt no | tests 1 failed, 3 passed in 0.5s FAILED (pytest exit 1) | still none "
                   "(ToyMod has no previews.toml (in here); see the format.)"]


def test_check_build_failure_is_one_sentence(toy, monkeypatch, capsys):
    def boom(env, **kw):
        raise MB.BuildError("The build run failed (RuntimeError: broke); log: /x.log")
    monkeypatch.setattr(MB, "build_models", boom)
    assert _check(toy) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert err == ["error: XX_B: build failed: The build run failed (RuntimeError: broke); log: /x.log"]


def test_check_end_to_end_with_the_fake_blender(toy, monkeypatch, capsys):
    """The real build path for one block (export is windowed: the fake Blender has no window, so the toy runner's
    launch runs main and the DONE marker is missing; the check says the build failed in one sentence)."""
    assert _check(toy) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("error: XX_B: build failed:")


def test_check_rejects_a_non_block_before_building(toy, monkeypatch, capsys):
    monkeypatch.setattr(MB, "build_models", lambda env, **kw: pytest.fail("must not build"))
    for word in ("all", "XX_*", "", "--x"):
        assert _check(toy, block=word) == 2
        assert "not a block name" in capsys.readouterr().err


def test_check_with_the_fake_blender_rejects_a_target_word_in_the_runner(toy, capsys):
    """Exact names only reach the build (--blocks-only): a word the catalogue does not know fails before any build,
    in one sentence."""
    assert _check(toy, block="XX_Nope") == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "Not a block name: XX_Nope" in err[0]
    assert toy.built() == []


def test_check_step_exiting_is_one_sentence(toy, monkeypatch, capsys):
    monkeypatch.setattr(MB, "build_models", lambda env, **kw: MB.BuildResult(["XX_B"], [], 1.0))

    def cmd_test(ns, env):
        print("2 passed in 0.1s | ToyMod: 1 test files, serial")
        return 0

    def preview_shots(ns, env):
        raise SystemExit("Blender is not installed")
    monkeypatch.setattr("se2modkit.cli.testrun_cmd.cmd_test", cmd_test)
    monkeypatch.setattr("se2modkit.cli.preview_cmd.preview_shots", preview_shots)
    assert _check(toy) == 1
    assert capsys.readouterr().out == "XX_B: rebuilt no | tests 2 passed in 0.1s | still none (Blender is not installed)\n"


def test_a_second_build_of_the_same_mod_is_refused(toy, capsys):
    from se2modkit.pipeline import incremental as IN
    lock = IN.lock_path(toy.report)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{os.getppid()}\n", encoding="utf-8")
    assert toy.build() == 1
    assert "Another build of ToyMod is running" in capsys.readouterr().err
    lock.unlink()
    assert toy.build(jobs=2) == 0 and not lock.exists()            # plan, workers and merge under one lock


def test_a_table_the_catalogue_loads_at_import_is_an_input(toy, capsys):
    """The build script imports the catalogue before anything from se2modkit; the Blender command line installs the
    read hook first, so the catalogue's module-level read still counts."""
    sm = toy.root / "Source" / "Model"
    (sm / "xxtoy" / "dims.json").write_text('{"w": 1}', encoding="utf-8")
    cat = sm / "xxtoy" / "catalog.py"
    cat.write_text("import json\nfrom pathlib import Path\n"
                   "TABLE = json.loads((Path(__file__).parent / 'dims.json').read_text())\n"
                   + cat.read_text(encoding="utf-8"), encoding="utf-8")
    assert toy.build(jobs=2) == 0
    toy.built()
    assert toy.build(jobs=2) == 0 and toy.built() == []
    (sm / "xxtoy" / "dims.json").write_text('{"w": 2}', encoding="utf-8")
    assert toy.build(jobs=2) == 0
    assert toy.built() == ["XX_A", "XX_B", "XX_C", "XX_D", "XX_E"]


# ------------------------------------------------------------------------------------------ per-block file reads
PATHS = ["direct", 1, 2]


def _do(monkeypatch, actions):
    monkeypatch.setenv("XXTOY_DO", json.dumps(actions))


ALL = ["XX_A", "XX_B", "XX_C", "XX_D", "XX_E"]


@pytest.mark.parametrize("path", PATHS)
def test_a_file_only_one_block_reads_rebuilds_only_that_block(toy, monkeypatch, path):
    """N1 on every launch path: a data file XX_A's builder reads in its own source file rebuilds XX_A alone, whatever
    ran after it in the same process or worker."""
    data = toy.root / "Source" / "Model" / "a.json"
    data.write_text("1", encoding="utf-8")
    _do(monkeypatch, {"XX_A": [f"read:{data}"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    data.write_text("2", encoding="utf-8")
    assert toy.run(path) == ["XX_A"]


@pytest.mark.parametrize("path", PATHS)
def test_a_shared_lazily_imported_helper_rebuilds_its_users(toy, monkeypatch, path):
    """A helper no source imports statically, first imported while XX_A builds (and reading a table at import), then
    used by XX_B: editing the helper or its table rebuilds both, on every path."""
    sm = toy.root / "Source" / "Model"
    (sm / "xxlazyhelp.py").write_text("from pathlib import Path\n"
                                      "T = (Path(__file__).parent / 'table.json').read_text()\n", encoding="utf-8")
    (sm / "table.json").write_text("1", encoding="utf-8")
    _do(monkeypatch, {"XX_A": ["import:xxlazyhelp"], "XX_B": ["import:xxlazyhelp"]})
    toy.run(path)
    assert toy.run(path) == []
    (sm / "table.json").write_text("2", encoding="utf-8")
    assert {"XX_A", "XX_B"} <= set(toy.run(path))
    assert toy.run(path) == []
    (sm / "xxlazyhelp.py").write_text("T = 3\n", encoding="utf-8")
    assert {"XX_A", "XX_B"} <= set(toy.run(path))


@pytest.mark.parametrize("path", PATHS)
def test_a_file_under_the_system_temp_folder_is_an_input_on_every_path(toy, monkeypatch, path):
    """N3: only the run's private temp folder is left out, so a file in the system temp folder XX_C reads is an input
    whether the build script runs directly or under build-models."""
    import tempfile
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    f = Path(name)
    try:
        f.write_text("1", encoding="utf-8")
        _do(monkeypatch, {"XX_C": [f"read:{f}"]})
        toy.run(path)
        assert toy.run(path) == []
        f.write_text("2", encoding="utf-8")
        assert toy.run(path) == ["XX_C"]
    finally:
        f.unlink(missing_ok=True)


# ------------------------------------------------------------------------- files the build writes and reads back
@pytest.mark.parametrize("path", PATHS)
def test_a_file_the_build_once_wrote_is_an_input_of_its_reader(toy, monkeypatch, path):
    """R1: XX_A copies src.txt to gen.txt; then the copy goes away and XX_C reads gen.txt. A hand edit of gen.txt
    rebuilds XX_C: a file the build once wrote is not remembered as an output."""
    sm = toy.root / "Source" / "Model"
    src, gen = sm / "src.txt", sm / "gen.txt"
    src.write_text("1", encoding="utf-8")
    _do(monkeypatch, {"XX_A": [f"copy:{src}>{gen}"]})
    assert toy.run(path) == ALL
    _do(monkeypatch, {"XX_C": [f"read:{gen}"]})
    assert toy.run(path, "XX_C", force=True) == ["XX_C"]
    assert toy.run(path) == []
    gen.write_text("hand edit", encoding="utf-8")
    assert toy.run(path) == ["XX_C"]
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_an_intermediate_file_propagates_to_its_reader(toy, monkeypatch, path):
    """R2: XX_A reads src.txt and writes mid.txt, XX_C reads mid.txt. Editing src.txt rebuilds XX_A, and XX_C at the
    latest on the run after (when mid.txt changed after it was read), then nothing."""
    sm = toy.root / "Source" / "Model"
    src, mid = sm / "src.txt", sm / "mid.txt"
    src.write_text("1", encoding="utf-8")
    mid.write_text("1", encoding="utf-8")
    _do(monkeypatch, {"XX_A": [f"copy:{src}>{mid}"], "XX_C": [f"read:{mid}"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    src.write_text("2", encoding="utf-8")
    first = toy.run(path)
    assert "XX_A" in first
    second = toy.run(path)
    assert "XX_C" in first + second and mid.read_text(encoding="utf-8") == "2"
    assert toy.run(path) == []
    src.write_text("3", encoding="utf-8")                     # both rebuild together: under --jobs XX_C may read
    assert toy.run(path, force=True) == ALL                   # mid.txt in its worker before XX_A rewrites it
    assert set(toy.run(path)) <= {"XX_C"}
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_file_its_block_rewrites_in_place_settles_and_rebuilds_on_edit(toy, monkeypatch, path):
    """R3: XX_A reads data.txt and rewrites it in place (stripped). It settles once the content is stable, and a user
    edit rebuilds it, also when the rewrite writes the very same bytes."""
    data = toy.root / "Source" / "Model" / "data.txt"
    data.write_text("  1  \n", encoding="utf-8")
    _do(monkeypatch, {"XX_A": [f"strip:{data}"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    data.write_text("  2  \n", encoding="utf-8")
    assert toy.run(path) == ["XX_A"]
    assert data.read_text(encoding="utf-8") == "2\n"
    assert toy.run(path) == []
    data.write_text("3\n", encoding="utf-8")                      # already stripped: the rewrite keeps the bytes
    assert toy.run(path) == ["XX_A"]
    assert toy.run(path) == []
    assert not toy.warnings("data.txt")


@pytest.mark.parametrize("path", PATHS)
def test_a_cached_table_in_a_shared_module_rebuilds_every_user(toy, monkeypatch, path):
    """R4: xxtoy/cat2.py (statically imported) loads table.json through a functools.cache; XX_A and XX_B use it.
    Editing the table rebuilds both: a read made from a module other than the block's own builder file counts for
    that block and every later block of the process."""
    (toy.root / "Source" / "Model" / "xxtoy" / "table.json").write_text('{"v": 1}', encoding="utf-8")
    _do(monkeypatch, {"XX_A": ["table:"], "XX_B": ["table:"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    (toy.root / "Source" / "Model" / "xxtoy" / "table.json").write_text('{"v": 2}', encoding="utf-8")
    assert {"XX_A", "XX_B"} <= set(toy.run(path))
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_shared_log_rebuilds_with_one_warning_until_declared_an_output(toy, monkeypatch, path):
    """XX_A and XX_B append to a log in the mod and read it back, XX_C only reads it. Undeclared, the log is an input
    the build itself changes: blocks rebuild every run and each run prints one warning naming it (never "inputs
    changed during the build"). Declared in outputs=, it settles completely."""
    log = toy.root / "Source" / "Model" / "shared.log"
    log.write_text("", encoding="utf-8")
    _do(monkeypatch, {"XX_A": [f"log:{log}"], "XX_B": [f"log:{log}"], "XX_C": [f"read:{log}"]})
    assert toy.run(path) == ALL
    for _ in range(3):
        assert toy.run(path) != []
        warned = toy.warnings("shared.log")
        assert len(warned) == 1 and "outputs=" in warned[0]
    monkeypatch.setenv("XXTOY_OUTPUTS", json.dumps(["Source/Model/*.log"]))
    toy.run(path)
    assert toy.run(path) == [] and toy.run(path) == []
    assert not toy.warnings("shared.log")


@pytest.mark.parametrize("path", PATHS)
def test_a_declared_output_is_never_an_input(toy, monkeypatch, path):
    """A file matching Runner(outputs=...) is never an input, whoever writes it: a hand edit rebuilds nothing."""
    monkeypatch.setenv("XXTOY_OUTPUTS", json.dumps(["Source/Model/cache/**"]))
    cache = toy.root / "Source" / "Model" / "cache"
    cache.mkdir()
    (cache / "x.bin").write_text("1", encoding="utf-8")
    _do(monkeypatch, {"XX_B": [f"read:{cache / 'x.bin'}", f"copy:{cache / 'x.bin'}>{cache / 'y.bin'}"]})
    assert toy.run(path) == ALL
    (cache / "x.bin").write_text("2", encoding="utf-8")
    (cache / "y.bin").write_text("3", encoding="utf-8")
    assert toy.run(path) == [] and not toy.warnings("cache")


@pytest.mark.parametrize("path", PATHS)
def test_a_file_read_at_import_and_rewritten_by_a_block_rebuilds_the_later_blocks(toy, monkeypatch, path):
    """xxtoy/catalog.py reads data.txt when it is imported, before any block builds, and XX_A strips data.txt in
    place. Every block of that process, XX_A included, was built from the table as it was imported, so they rebuild
    on the next run, with one warning naming data.txt; then everything settles."""
    data = toy.root / "Source" / "Model" / "data.txt"
    data.write_text("  1  \n", encoding="utf-8")
    monkeypatch.setenv("XXTOY_IMPORT_READ", str(data))
    _do(monkeypatch, {"XX_A": [f"strip:{data}"]})
    assert toy.run(path) == ALL
    assert len(toy.warnings("data.txt")) == 1
    # one process imports before XX_A strips the file and builds every block; with two workers the other worker may
    # import before or after the strip, but the first worker's blocks (XX_A, XX_C, XX_E) always rebuild
    again = toy.run(path)
    assert ({"XX_A", "XX_C", "XX_E"} if path == 2 else set(ALL)) <= set(again)
    assert toy.run(path) == []
    assert not toy.warnings("data.txt")
    data.write_text("  2  \n", encoding="utf-8")
    assert toy.run(path) == ALL
    toy.run(path)
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_decorator_module_is_shared_code_not_the_blocks_own_file(toy, monkeypatch, path):
    """The builder is wrapped by a functools.wraps decorator from xxtoy/common.py, which also holds a cached table
    XX_B and XX_D use. Only the file defining the unwrapped builder is the block's own: common.py is shared code, so
    editing the table rebuilds both users, not just the first one that read it."""
    monkeypatch.setenv("XXTOY_DECO", "wraps")
    table = toy.root / "Source" / "Model" / "xxtoy" / "table.json"
    table.write_text('{"v": 1}', encoding="utf-8")
    _do(monkeypatch, {"XX_B": ["common:"], "XX_D": ["common:"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    table.write_text('{"v": 2}', encoding="utf-8")
    assert {"XX_B", "XX_D"} <= set(toy.run(path))
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_folder_the_catalogue_lists_at_import_is_an_input(toy, monkeypatch, path):
    """xxtoy/catalog.py lists Source/Model/parts when it is imported. Adding a part rebuilds every block: a listing made
    by a module while it is being imported is the module's, not the import system's."""
    parts = toy.root / "Source" / "Model" / "parts"
    parts.mkdir()
    (parts / "a.json").write_text("1", encoding="utf-8")
    monkeypatch.setenv("XXTOY_IMPORT_LIST", str(parts))
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    (parts / "c.json").write_text("1", encoding="utf-8")
    assert toy.run(path) == ALL
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_decorator_without_wraps_from_a_shared_module_is_not_the_blocks_own_file(toy, monkeypatch, path):
    """The builder is wrapped by a decorator from xxtoy/common.py that does not use functools.wraps; common.py also
    holds a cached table XX_B and XX_D use. The wrapper's module is shared code, so editing the table rebuilds both."""
    monkeypatch.setenv("XXTOY_DECO", "plain")
    table = toy.root / "Source" / "Model" / "xxtoy" / "table.json"
    table.write_text('{"v": 1}', encoding="utf-8")
    _do(monkeypatch, {"XX_B": ["common:"], "XX_D": ["common:"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    table.write_text('{"v": 2}', encoding="utf-8")
    assert {"XX_B", "XX_D"} <= set(toy.run(path))
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("how", ["partial", "generic", "import", "common_partial", "common_builders", "factory",
                                 "deco_factory", "factory_plain", "deco_factory_plain"])
def test_a_builder_that_is_shared_code_rebuilds_every_user_of_a_shared_cache(toy, monkeypatch, path, how):
    """The builder the runner is given runs common.generic, whose helper reads table.json through a functools.cache:
    a partial of it, the function itself, the function imported by name, a partial common.py declares, common.py's
    own builder table, a builder a factory made around a cached loader, or a builder wrapped by a decorator factory
    whose wrapper calls that loader. Only reads made in the builder's own code count for one block; the helper's read
    counts for every later block too, so editing the table rebuilds every block that used it."""
    monkeypatch.setenv("XXTOY_BUILDERS", how)
    table = toy.root / "Source" / "Model" / "xxtoy" / "table.json"
    table.write_text('{"v": 1}', encoding="utf-8")
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    table.write_text('{"v": 2}', encoding="utf-8")
    assert toy.run(path) == ALL
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("how", ["gen", "linecache", "once"])
def test_a_helper_that_keeps_what_it_read_counts_for_later_blocks(toy, monkeypatch, path, how):
    """XX_B and XX_D read data.txt through a helper that keeps what it read: one compiled from a string (its code's
    file name is "<generated>"), a standard-library helper (linecache), or a shared helper that runs and keeps a
    loader nested in the builder. Any such frame between the builder and the read makes the read count for every
    later block, so an edit rebuilds both."""
    data = toy.root / "Source" / "Model" / "data.txt"
    data.write_text("1\n", encoding="utf-8")
    pre = ["import:xxtoy_gen"] if how == "gen" else []
    _do(monkeypatch, {"XX_B": pre + [f"{how}:{data}"], "XX_D": pre + [f"{how}:{data}"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    data.write_text("2\n", encoding="utf-8")
    assert {"XX_B", "XX_D"} <= set(toy.run(path))
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("how", ["read", "open", "json"])
def test_a_file_read_in_the_builder_body_through_io_plumbing_stays_per_block(toy, monkeypatch, path, how):
    """open, pathlib and json between the builder's own code and the read are plumbing: the read stays XX_A's."""
    data = toy.root / "Source" / "Model" / "a.json"
    data.write_text("1", encoding="utf-8")
    _do(monkeypatch, {"XX_A": [f"{how}:{data}"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    data.write_text("2", encoding="utf-8")
    assert toy.run(path) == ["XX_A"]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("how", ["reenter", "reenter_partial"])
def test_a_builder_re_entered_through_a_shared_helper_counts_for_later_blocks(toy, monkeypatch, path, how):
    """XX_B and XX_D hand a lambda (or a partial) that re-enters the builder to common.once, which keeps the result;
    the re-entered builder reads data.txt. The shared helper sits between the runner's call and the read, so the read
    counts for every later block and an edit rebuilds both, though the read itself is made in the builder's code."""
    data = toy.root / "Source" / "Model" / "data.txt"
    data.write_text("1\n", encoding="utf-8")
    _do(monkeypatch, {"XX_B": [f"{how}:{data}"], "XX_D": [f"{how}:{data}"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    data.write_text("2\n", encoding="utf-8")
    assert {"XX_B", "XX_D"} <= set(toy.run(path))
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_memoizing_decorator_rebuilds_every_block_given_the_kept_result(toy, monkeypatch, tmp_path, path):
    """The builder is wrapped by a functools.wraps decorator that keeps one result per family: XX_A's builder reads
    data.txt, and the later blocks of its process get XX_A's result without running the builder. The wrapper is not
    the builder's own code, so the read counts for every later block and an edit rebuilds each one that got it."""
    monkeypatch.setenv("XXTOY_DECO", "memo")
    memo_log = tmp_path / "memo.log"
    monkeypatch.setenv("XXTOY_MEMO_LOG", str(memo_log))
    data = toy.root / "Source" / "Model" / "data.txt"
    data.write_text("1\n", encoding="utf-8")
    _do(monkeypatch, {"XX_A": [f"read:{data}"]})
    assert toy.run(path) == ALL
    given = {b for b, _, first in (x.partition(":") for x in memo_log.read_text(encoding="utf-8").split())
             if first == "XX_A"}
    assert "XX_A" not in given and (path == 2 or given == set(ALL) - {"XX_A"})
    assert toy.run(path) == []
    data.write_text("2\n", encoding="utf-8")
    assert {"XX_A"} | given <= set(toy.run(path))
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_cached_builder_given_equal_specs_rebuilds_every_block(toy, monkeypatch, path):
    """The builder is wrapped in functools.cache and the specs compare without their names, so every block after the
    first in a process gets XX_A's cached result without running the builder. Every block reads data.txt in the
    builder body; a cached builder has no own code, so an edit rebuilds every block of every process."""
    monkeypatch.setenv("XXTOY_DECO", "cache")
    monkeypatch.setenv("XXTOY_FROZEN", "1")
    data = toy.root / "Source" / "Model" / "data.txt"
    data.write_text("1\n", encoding="utf-8")
    _do(monkeypatch, {n: [f"read:{data}"] for n in ALL})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    data.write_text("2\n", encoding="utf-8")
    assert toy.run(path) == ALL
    assert toy.run(path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_wraps_decorator_makes_the_builders_reads_count_for_later_blocks(toy, monkeypatch, path):
    """A functools.wraps decorator that keeps nothing still sits between the runner's call and the builder: its
    wrapper is not the builder's own code, so a read in the builder body counts for every later block too. That
    rebuilds more than needed, never less."""
    monkeypatch.setenv("XXTOY_DECO", "wraps")
    data = toy.root / "Source" / "Model" / "a.json"
    data.write_text("1", encoding="utf-8")
    _do(monkeypatch, {"XX_A": [f"read:{data}"]})
    assert toy.run(path) == ALL
    assert toy.run(path) == []
    data.write_text("2", encoding="utf-8")
    again = toy.run(path)
    assert "XX_A" in again and (path == 2 or again == ALL)


def _marks(tmp_path, monkeypatch):
    marks = tmp_path / "marks"
    marks.mkdir()
    monkeypatch.setenv("XXTOY_MARKS", str(marks))
    return marks


def _run_marked(toy, marks, path, **kw):
    for m in marks.iterdir():
        m.unlink()
    return toy.run(path, **kw)


@pytest.mark.parametrize("path", PATHS)
def test_a_file_outside_the_mod_read_at_import_and_rewritten_by_another_worker(toy, monkeypatch, tmp_path, path):
    """The catalogue reads ../outside/data.txt (outside the mod) when it is imported; XX_A strips it in place. Under
    --jobs 2, XX_A waits until the other worker has imported the catalogue, so that worker built its blocks from the
    old content: they must rebuild on the next run, never stay up to date."""
    marks = _marks(tmp_path, monkeypatch)
    data = tmp_path / "outside" / "data.txt"
    data.parent.mkdir()
    data.write_text("  1  \n", encoding="utf-8")
    monkeypatch.setenv("XXTOY_IMPORT_READ", str(data))
    _do(monkeypatch, {"XX_A": ([f"wait:{marks / '2'}"] if path == 2 else []) + [f"strip:{data}"]})
    assert _run_marked(toy, marks, path) == ALL
    assert _run_marked(toy, marks, path) == ALL
    assert _run_marked(toy, marks, path) == []
    data.write_text("  2  \n", encoding="utf-8")
    assert _run_marked(toy, marks, path) == ALL
    assert _run_marked(toy, marks, path) == ALL
    assert _run_marked(toy, marks, path) == []


@pytest.mark.parametrize("path", PATHS)
def test_a_folder_listed_at_import_and_written_into_by_another_worker(toy, monkeypatch, tmp_path, path):
    """The catalogue lists ../outside/parts when it is imported; XX_A writes a new part into it. Under --jobs 2 the
    other worker listed the folder before that write: its blocks rebuild on the next run, then everything settles."""
    marks = _marks(tmp_path, monkeypatch)
    parts = tmp_path / "outside" / "parts"
    parts.mkdir(parents=True)
    (parts / "a.json").write_text("1", encoding="utf-8")
    src = tmp_path / "outside" / "new.json"
    src.write_text("1", encoding="utf-8")
    monkeypatch.setenv("XXTOY_IMPORT_LIST", str(parts))
    _do(monkeypatch, {"XX_A": ([f"wait:{marks / '2'}"] if path == 2 else []) + [f"copy:{src}>{parts / 'b.json'}"]})
    assert _run_marked(toy, marks, path) == ALL
    again = _run_marked(toy, marks, path)
    assert {"XX_B", "XX_C", "XX_D", "XX_E"} <= set(again)
    assert _run_marked(toy, marks, path) == []


# ------------------------------------------------------------------------------------------------ release builds
def _git(root, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def _commit_all(root, msg="models"):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


@pytest.fixture
def released(toy, monkeypatch):
    """The toy mod in a git repo, with build_models faked: it writes every block's export and fragment the way a
    forced, exported build does, and records how it was called."""
    from se2modkit.pipeline import incremental as IN
    (toy.root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(toy.root, "init", "-q", "-b", "main")
    _git(toy.root, "config", "user.email", "t@example.com")
    _git(toy.root, "config", "user.name", "t")
    _commit_all(toy.root, "initial")
    calls = []

    def build_models(env, **kw):
        calls.append(kw)
        store = IN.Store.for_report(kw["report"], kw["mod_name"])
        for b in toy.names:
            d = toy.root / "Source" / "Model" / "Models" / b
            d.mkdir(parents=True, exist_ok=True)
            (d / "export.json").write_text(f'{{"block": "{b}"}}\n', encoding="utf-8")
            (d / f"{b}.fbx").write_text(released.fbx.get(b, "fbx " + b), encoding="utf-8")
            store.save(b, {"block": b, "key": "key-" + b})
            for name, body in toy.extra.get(b, {}).items():
                (d / name).write_text(body, encoding="utf-8")
        return MB.BuildResult(list(toy.names), list(toy.names), 1.0)
    monkeypatch.setattr(MB, "build_models", build_models)
    toy.calls = calls
    toy.fbx = {}
    toy.names = list(ALL)
    toy.extra = {}            # block -> {extra exported file: content}
    released = toy
    return toy


def _ship(toy, content=None):
    """Install the exported models into Assets/ as the mod's integration step does: byte copies, each at
    Assets/<mod>/<block>/Models/<path inside the block's export folder>."""
    for b in toy.names:
        src = toy.root / "Source" / "Model" / "Models" / b
        for f in sorted(src.rglob("*")):
            if f.is_file() and f.name not in ("export.json", "placement_exported.json"):
                dst = toy.root / "Assets" / "ToyMod" / b / "Models" / f.relative_to(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(f.read_bytes() if content is None else content)


def _first_release(toy):
    assert _release(toy) == 1
    _ship(toy)
    _commit_all(toy.root)
    assert _release(toy) == 0


def _release(toy, *blocks):
    ns = argparse.Namespace(mod="ToyMod", blocks=list(blocks), jobs=2, export=False, render=False, force=False,
                            script=None, timeout=60, assume_game_closed=False, release=True)
    return MC.cmd_build_models(ns, toy.env)


def test_a_release_build_forces_and_exports_every_block_from_a_clean_tree(released, capsys):
    from se2modkit import release as REL
    stamp = REL.stamp_path(released.report, "ToyMod")
    assert _release(released) == 1                    # the first export adds the models: commit them, run again
    err = capsys.readouterr().err
    assert "changed files in the mod" in err and "se2mod build-models ToyMod --release" in err
    assert not stamp.exists()
    kw = released.calls[-1]
    assert kw["force"] and kw["export"] and kw["words"] == []
    _ship(released)
    _commit_all(released.root)
    assert _release(released) == 0                    # same bytes: the tree stays clean, the build is stamped
    data = REL.read_stamp(stamp)
    assert data["head"] == _git(released.root, "rev-parse", "HEAD").strip()
    assert data["blocks"] == ALL and data["mod"] == "ToyMod" and data["modkit"]
    assert data["keys"] == REL.keys_digest({b: "key-" + b for b in ALL})
    assert set(data["files"]) == {f"Source/Model/Models/{b}/{n}" for b in ALL for n in ("export.json", f"{b}.fbx")}
    assert set(data["shipped"]) == {f"Assets/ToyMod/{b}/Models/{b}.fbx" for b in ALL}
    assert data["script"] == "Source/Model/build_toymod.py"
    assert "stamped" in capsys.readouterr().out
    assert REL.check(mod="ToyMod", root=released.root, report=released.report,
                     models=released.root / "Source" / "Model" / "Models") is None


def test_a_release_build_refuses_a_dirty_tree_and_block_names(released, capsys):
    (released.root / "Source" / "Model" / "xxtoy" / "catalog.py").write_text("# edited\n", encoding="utf-8")
    assert _release(released) == 1
    assert "uncommitted changes" in capsys.readouterr().err and not released.calls
    assert _release(released, "XX_A") == 2
    assert "drop the block names" in capsys.readouterr().err


def test_a_release_build_outside_git_is_one_sentence(toy, monkeypatch, capsys):
    monkeypatch.setattr(MB, "build_models", lambda env, **kw: pytest.fail("must not build"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(toy.root.parent))
    assert _release(toy) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "not in a git repository" in err[0]


def _gate(toy):
    from se2modkit import release as REL
    from se2modkit.project import load_project
    return REL.publish_check(toy.root)[0], load_project(toy.root)


def test_an_older_shipped_copy_is_refused_by_the_release_build_and_the_gate(released, capsys):
    """The export changed, but Assets/ still holds the older copy the mod ships: the release build refuses to stamp
    it, and a stamp made over such a tree does not pass the publish check."""
    _first_release(released)
    released.fbx = {"XX_C": "new geometry"}
    assert _release(released) == 1                       # the export changed: commit it
    _commit_all(released.root)                           # ... but not the shipped copy
    assert _release(released) == 1
    assert "Assets/ToyMod/XX_C/Models/XX_C.fbx" in capsys.readouterr().err
    from se2modkit import release as REL
    assert not REL.stamp_path(released.report, "ToyMod").exists()
    why, _ = _gate(released)
    assert why is not None and "--release" in why
    _ship(released)
    _commit_all(released.root)
    assert _release(released) == 0 and _gate(released)[0] is None
    (released.root / "Assets" / "ToyMod" / "XX_C" / "Models" / "XX_C.fbx").write_text("stale", encoding="utf-8")
    _commit_all(released.root, "older copy")
    why, _ = _gate(released)
    assert "not HEAD" in why or "Assets/ToyMod/XX_C" in why


def test_a_symlink_in_the_models_or_assets_is_refused(released, capsys):
    _first_release(released)
    models = released.root / "Source" / "Model" / "Models"
    (models / "XX_LINK").symlink_to(models / "XX_A", target_is_directory=True)
    _commit_all(released.root, "a linked block folder")
    assert _release(released) == 1
    assert "symlink" in capsys.readouterr().err
    why, _ = _gate(released)
    assert "symlink" in why


def test_a_project_mod_without_a_build_named_build_star_is_gated(released, capsys):
    """Built with --script: no Source/Model/build_*.py, but the mod has models; it is still gated, and the stamp
    records the script it was built with."""
    from se2modkit import release as REL
    sm = released.root / "Source" / "Model"
    (sm / "build_toymod.py").rename(sm / "make_models.py")
    _commit_all(released.root, "renamed")
    _ship(released, b"x")
    (sm / "Models" / "XX_A").mkdir(parents=True, exist_ok=True)
    (sm / "Models" / "XX_A" / "XX_A.fbx").write_text("x", encoding="utf-8")
    _commit_all(released.root, "models")
    why, _ = _gate(released)
    assert why is not None and "no release build" in why
    ns = argparse.Namespace(mod="ToyMod", blocks=[], jobs=2, export=False, render=False, force=False,
                            script=sm / "make_models.py", timeout=60, assume_game_closed=False, release=True)
    if MC.cmd_build_models(ns, released.env) == 1:
        _ship(released)
        _commit_all(released.root)
        assert MC.cmd_build_models(ns, released.env) == 0
    assert REL.read_stamp(REL.stamp_path(released.report, "ToyMod"))["script"] == "Source/Model/make_models.py"
    assert _gate(released)[0] is None
    (sm / "make_models.py").unlink()
    _commit_all(released.root, "script gone")
    stamp = REL.stamp_path(released.report, "ToyMod")
    data = REL.read_stamp(stamp)
    data["head"] = _git(released.root, "rev-parse", "HEAD").strip()
    stamp.write_text(json.dumps(data), encoding="utf-8")
    assert "build script" in _gate(released)[0]


def test_a_script_outside_the_mod_repo_is_refused(released, tmp_path, capsys):
    other = tmp_path / "elsewhere" / "build_other.py"
    other.parent.mkdir()
    other.write_text("# not in the repo\n", encoding="utf-8")
    ns = argparse.Namespace(mod="ToyMod", blocks=[], jobs=2, export=False, render=False, force=False,
                            script=other, timeout=60, assume_game_closed=False, release=True)
    assert MC.cmd_build_models(ns, released.env) == 1
    assert "outside the mod's git repository" in capsys.readouterr().err and not released.calls


def test_ignored_files_that_would_ship_make_the_tree_dirty(released, capsys):
    (released.root / ".gitignore").write_text("__pycache__/\n*.bak\n", encoding="utf-8")
    _commit_all(released.root, "ignore")
    (released.root / "Assets" / "ToyMod").mkdir(parents=True)
    (released.root / "Assets" / "ToyMod" / "old.bak").write_text("x", encoding="utf-8")
    assert _release(released) == 1
    assert "old.bak" in capsys.readouterr().err and not released.calls
    (released.root / "Assets" / "ToyMod" / "old.bak").unlink()
    (released.root / "notes.bak").write_text("x", encoding="utf-8")       # ignored, does not ship: fine
    _first_release(released)


def test_a_stamp_for_another_mod_is_refused(released):
    from se2modkit import release as REL
    _first_release(released)
    stamp = REL.stamp_path(released.report, "ToyMod")
    data = REL.read_stamp(stamp)
    data["mod"] = "OtherMod"
    stamp.write_text(json.dumps(data), encoding="utf-8")
    assert "OtherMod" in _gate(released)[0]


def test_a_renamed_blocks_old_shipped_models_are_refused(released, capsys):
    """XX_E is renamed XX_F: the export no longer has XX_E, but the old Assets/ToyMod/XX_E copy is still committed
    and would ship. The release build and the gate both refuse, naming the file."""
    import shutil
    _first_release(released)
    released.names = [n for n in ALL if n != "XX_E"] + ["XX_F"]
    shutil.rmtree(released.root / "Source" / "Model" / "Models" / "XX_E")
    _commit_all(released.root, "XX_E is now XX_F")
    assert _release(released) == 1                      # the XX_F export is new: commit it
    _ship(released)
    _commit_all(released.root, "renamed")
    capsys.readouterr()
    assert _release(released) == 1
    assert "Assets/ToyMod/XX_E/Models/XX_E.fbx" in capsys.readouterr().err
    from se2modkit import release as REL
    assert not REL.stamp_path(released.report, "ToyMod").exists()
    shutil.rmtree(released.root / "Assets" / "ToyMod" / "XX_E")
    _commit_all(released.root, "old copy gone")
    assert _release(released) == 0 and _gate(released)[0] is None
    old = released.root / "Assets" / "ToyMod" / "XX_E" / "Models" / "XX_E.fbx"
    old.parent.mkdir(parents=True)
    old.write_text("fbx XX_E", encoding="utf-8")
    _commit_all(released.root, "the old copy is back")
    data = REL.read_stamp(REL.stamp_path(released.report, "ToyMod"))
    data["head"] = _git(released.root, "rev-parse", "HEAD").strip()     # even a stamp of this very commit
    REL.stamp_path(released.report, "ToyMod").write_text(json.dumps(data), encoding="utf-8")
    assert "Assets/ToyMod/XX_E/Models/XX_E.fbx" in _gate(released)[0]


def test_shipped_copies_are_matched_by_their_place_not_their_name(released, capsys):
    """Two blocks export a file of the same name (LOD0.fbx) with different content. Copies swapped between the two
    blocks' folders would pass a check by name; matched by place inside the block, they are refused."""
    released.extra = {"XX_A": {"LOD0.fbx": "geometry A"}, "XX_B": {"LOD0.fbx": "geometry B"}}
    _first_release(released)
    assert _gate(released)[0] is None
    a = released.root / "Assets" / "ToyMod" / "XX_A" / "Models" / "LOD0.fbx"
    b = released.root / "Assets" / "ToyMod" / "XX_B" / "Models" / "LOD0.fbx"
    a.write_text("geometry B", encoding="utf-8")
    b.write_text("geometry A", encoding="utf-8")
    _commit_all(released.root, "swapped")
    assert _release(released) == 1
    assert "Assets/ToyMod/XX_A/Models/LOD0.fbx" in capsys.readouterr().err


def _stage_model(toy, block, body):
    """The mod's integration step stages a block's .model asset (git-ignored), then installs it into Assets/."""
    staged = toy.root / "Source" / "Model" / "integration" / "Assets" / "ToyMod" / block / "Models" / f"{block}.model"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(body, encoding="utf-8")
    return staged


def test_a_shipped_model_asset_must_be_the_staged_one(released, capsys):
    """A .model in Assets/ is compared byte for byte with the staged package at the same place, like the geometry:
    an older .model in an exported block's folder is refused by the release build and the gate."""
    (released.root / ".gitignore").write_text("__pycache__/\nSource/Model/integration/\n", encoding="utf-8")
    _commit_all(released.root, "ignore the staging")
    staged = _stage_model(released, "XX_A", "model v1")
    shipped = released.root / "Assets" / "ToyMod" / "XX_A" / "Models" / "XX_A.model"
    assert _release(released) == 1
    _ship(released)
    shipped.write_bytes(staged.read_bytes())
    _commit_all(released.root)
    assert _release(released) == 0 and _gate(released)[0] is None
    staged.write_text("model v2", encoding="utf-8")            # the staging changed, the shipped copy did not
    capsys.readouterr()
    assert _release(released) == 1
    assert "Assets/ToyMod/XX_A/Models/XX_A.model" in capsys.readouterr().err
    shipped.write_text("model v0", encoding="utf-8")            # an older .model committed over the right one
    _commit_all(released.root, "older model")
    from se2modkit import release as REL
    data = REL.read_stamp(REL.stamp_path(released.report, "ToyMod")) or {}
    assert _gate(released)[0] is not None
