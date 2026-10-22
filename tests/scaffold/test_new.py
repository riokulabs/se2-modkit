"""`se2mod new`: full single-mod repos, mods added into multi-mod repos, library mods, org sync, and the MOD argument
every mod command takes (a name or a path)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import se2modkit
from se2modkit import cli
from se2modkit.cli import modarg
from se2modkit.project import load_project

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

MODKIT_ROOT = Path(__file__).resolve().parents[2]
PIN = "se2-modkit[publish] @ git+https://github.com/riokulabs/se2-modkit@v{v}"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def test_new_single_repo_layout(tmp_path, run):
    code, out, err = run("new", "My Mod", "--prefix", "MM_")
    assert code == 0, err
    d = tmp_path / "My Mod"
    assert _files(d) == {
        "My Mod.vrgproj", "Assets.meta", "Content.meta", "ModProject.modhub-metadata", "Assets/.gitkeep",
        ".gitignore", ".gitattributes", "README.md", "CHANGELOG.md", "LICENSE", "pyproject.toml",
        "Source/Model/mm/__init__.py", "Source/Model/mm/hooks.py", "Source/Model/mm/catalog.py",
        "Source/Model/build_my_mod.py", "Tests/conftest.py", "Tests/test_smoke.py", "Tests/test_gates.py",
    }
    assert (d / ".git").is_dir() and (d / "Content").is_dir()
    assert not subprocess.run(["git", "-C", str(d), "rev-parse", "--verify", "-q", "HEAD"],
                              capture_output=True).returncode == 0  # git init, no commit
    assert (d / "LICENSE").read_bytes() == (MODKIT_ROOT / "LICENSE").read_bytes()
    py = tomllib.loads((d / "pyproject.toml").read_text(encoding="utf-8"))
    assert py["project"] == {"name": "se2-my-mod-tooling", "version": "0", "requires-python": ">=3.11",
                             "dependencies": [PIN.format(v=se2modkit.__version__)]}  # the tooling manifest
    assert py["tool"]["se2modkit"] == {"mod_name": "My Mod", "profile": "vanilla", "prefix": "MM_",
                                       "gates": ["json", "guids"]}
    ignore = (d / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("preview/", "Content/", ".venv/", "__pycache__/", ".pytest_cache/", ".hashescache.json",
                    ".Scripts.hidden-for-build/", "Content.pruned/", "Content.tmp/", "bin/", "obj/", "build/", "dist/"):
        assert pattern in ignore
    mod = load_project(d).mod(None)
    assert (mod.name, mod.prefix, mod.profile, mod.root) == ("My Mod", "MM_", "vanilla", d)
    assert "--org-sync" in out  # the one line on how to get the org-managed files


def test_pin_is_the_release_tag_form(tmp_path, run, monkeypatch):
    from se2modkit import scaffold
    from se2modkit.cli import mods_cmd
    code, _, err = run("new", "Dev", "--prefix", "DV_")  # this modkit is a dev build: no tag yet, one warning
    assert code == 0 and err.count("warning: se2-modkit") == 1 and "release tag" in err
    monkeypatch.setattr(scaffold, "__version__", "0.4.0")
    monkeypatch.setattr(mods_cmd, "__version__", "0.4.0")
    code, _, err = run("new", "Rel", "--prefix", "RL_")
    assert code == 0 and "warning: se2-modkit" not in err
    deps = tomllib.loads((tmp_path / "Rel/pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert deps == [PIN.format(v="0.4.0")]
    assert PIN.format(v="0.4.0") in (tmp_path / "Rel/README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["Trailing.", "Trailing ", " Leading"])
def test_new_rejects_names_windows_cannot_hold(tmp_path, run, bad):
    code, _, err = run("new", bad)
    assert code == 1 and "mod name" in err and "Traceback" not in err
    assert not any(p.name.strip(" .") == bad.strip(" .") for p in tmp_path.iterdir())


def test_new_default_prefix_and_bad_prefix(tmp_path, run):
    code, _, err = run("new", "Space Tug")
    assert code == 0, err
    assert load_project(tmp_path / "Space Tug").mod(None).prefix == "ST_"
    code, _, err = run("new", "Other", "--prefix", "9x")
    assert code == 1 and "prefix" in err and "Traceback" not in err
    assert not (tmp_path / "Other").exists()


def test_new_repo_tests_pass(tmp_path, run):
    code, _, err = run("new", "My Mod", "--prefix", "MM_")
    assert code == 0, err
    d = tmp_path / "My Mod"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "SE2"))}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=d, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 passed" in r.stdout          # the smoke test and the json and guids gates
    # The build script is a working skeleton: with an empty catalogue it says so plainly.
    b = subprocess.run([sys.executable, str(d / "Source/Model/build_my_mod.py")], cwd=d, env=env,
                       capture_output=True, text=True, timeout=120)
    assert "Traceback" not in b.stderr
    assert b.returncode == 2 and "catalogue is empty" in b.stderr
    h = subprocess.run([sys.executable, str(d / "Source/Model/build_my_mod.py"), "--help"], cwd=d, env=env,
                       capture_output=True, text=True, timeout=120)
    assert h.returncode == 0 and "--export" in h.stdout


def test_new_into_multi_repo(tmp_path, run):
    assert run("new", "Core", "--prefix", "CO_")[0] == 0
    repo = tmp_path / "Core"
    code, _, err = run("new", "Drives", "--prefix", "DR_", "--into", str(repo), "--convert")
    assert code == 0, err
    code, _, err = run("new", "Deco", "--prefix", "DE_", "--into", str(repo))
    assert code == 0, err
    project = load_project(repo)
    assert sorted(project.mods) == ["Core", "Deco", "Drives"]
    assert project.mods["Core"].root == repo and project.mods["Core"].prefix == "CO_"
    assert project.mods["Drives"].root == repo / "mods/Drives"
    assert project.mods["Deco"].prefix == "DE_"
    for name, pkg in (("Drives", "dr"), ("Deco", "de")):
        d = repo / "mods" / name
        assert (d / f"{name}.vrgproj").is_file() and (d / "CHANGELOG.md").is_file()
        assert (d / "Source/Model" / pkg / "catalog.py").is_file() and (d / "Tests/test_smoke.py").is_file()
        assert not (d / ".git").exists()
    # Run from inside a mod, the mod is that one (the repo-root mod does not swallow nested ones).
    assert project.mod(None, cwd=repo / "mods/Deco/Assets").name == "Deco"
    # Each added mod's own tests pass.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "SE2"))}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "mods/Drives/Tests"],
                       cwd=repo, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "4 passed" in r.stdout, r.stdout + r.stderr   # smoke, 2 gates, guids across mods
    assert load_project(repo).mods["Drives"].gates == ("json", "guids")


def test_new_into_single_requires_convert(tmp_path, run):
    assert run("new", "Core", "--prefix", "CO_")[0] == 0
    repo = tmp_path / "Core"
    before = (repo / "pyproject.toml").read_text(encoding="utf-8")
    code, _, err = run("new", "Drives", "--into", str(repo))
    assert code == 1 and "--convert" in err and "Traceback" not in err
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == before
    assert not (repo / "mods").exists()


def test_new_into_needs_a_modkit_repo(tmp_path, run):
    (tmp_path / "plain").mkdir()
    code, _, err = run("new", "X", "--into", str(tmp_path / "plain"))
    assert code == 1 and "[tool.se2modkit]" in err


def test_new_rejects_duplicate_prefix_in_repo(tmp_path, run):
    assert run("new", "Core", "--prefix", "CO_")[0] == 0
    repo = tmp_path / "Core"
    assert run("new", "Drives", "--prefix", "DR_", "--into", str(repo), "--convert")[0] == 0
    before = (repo / "pyproject.toml").read_text(encoding="utf-8")
    code, _, err = run("new", "Dupe", "--prefix", "DR_", "--into", str(repo))
    assert code == 1 and "DR_" in err and "Drives" in err
    code, _, err = run("new", "Drives", "--prefix", "XY_", "--into", str(repo))
    assert code == 1 and "already" in err
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == before
    assert not (repo / "mods/Dupe").exists()


def test_new_library_mod(tmp_path, run):
    code, _, err = run("new", "Shared Core", "--library", "--prefix", "SC_")
    assert code == 0, err
    d = tmp_path / "Shared Core"
    assert (d / "Scripts").is_dir() and (d / "Assets").is_dir()
    assert not (d / "Source").exists()  # data-only placeholder: no model skeleton
    readme = (d / "README.md").read_text(encoding="utf-8")
    assert "library mod" in readme.lower() and "core-library spike" in readme
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "SE2"))}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=d, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "1 passed" in r.stdout, r.stdout + r.stderr


FAKE_SYNC = '''\
import json, sys, tomllib
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
name = sys.argv[1]
apply = "--apply" in sys.argv
with open(ROOT / "calls.log", "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
target = (ROOT / tomllib.loads((ROOT / "repos" / f"{name}.toml").read_text())["target"]).resolve()
if apply:
    (target / ".github").mkdir(exist_ok=True)
    (target / ".github" / "managed.yml").write_text("# managed by the org\\n")
print(("applied" if apply else "dry run, nothing written") + ": 1 file(s) differ, 0 problem(s)")
'''


def _fake_org(tmp_path: Path, name: str, target: str) -> Path:
    org = tmp_path / "org"
    (org / "tools").mkdir(parents=True)
    (org / "repos").mkdir()
    (org / "tools" / "sync-github-structure.py").write_text(FAKE_SYNC, encoding="utf-8")
    (org / "repos" / f"{name}.toml").write_text(f'target = "{target}"\nkind = "mod"\n', encoding="utf-8")
    return org


def test_new_org_sync_dry_run_then_apply(tmp_path, run):
    org = _fake_org(tmp_path, "my-mod", "../My Mod")
    code, out, err = run("new", "My Mod", "--org-sync", str(org))
    assert code == 0, err
    calls = (org / "calls.log").read_text(encoding="utf-8").splitlines()
    assert calls == ['["my-mod"]', '["my-mod", "--apply"]']
    assert (tmp_path / "My Mod/.github/managed.yml").is_file()
    assert "dry run" in out and "applied" in out


def test_new_org_sync_checks_before_creating(tmp_path, run):
    org = _fake_org(tmp_path, "my-mod", "../elsewhere")
    code, _, err = run("new", "My Mod", "--org-sync", str(org))
    assert code == 1 and "target" in err and "Traceback" not in err
    assert not (tmp_path / "My Mod").exists()
    code, _, err = run("new", "Other", "--org-sync", str(org))
    assert code == 1 and "repos/other.toml" in err
    assert not (tmp_path / "Other").exists() and not (org / "calls.log").exists()


_BANNED = ("/home/", "\\Users\\", "C:\\", "Z:\\", "S:\\", "~/", "GE-Proton", "debian-installation", "se2mod.py")


def test_new_org_sync_leaves_gitattributes_to_the_org(tmp_path, run):
    org = _fake_org(tmp_path, "my-mod", "../My Mod")
    (org / "templates" / "common").mkdir(parents=True)
    (org / "templates" / "common" / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    code, _, err = run("new", "My Mod", "--org-sync", str(org))
    assert code == 0, err
    assert not (tmp_path / "My Mod/.gitattributes").exists()


def test_new_org_sync_keep_conflict_fails_early(tmp_path, run):
    org = _fake_org(tmp_path, "my-mod", "../My Mod")
    (org / "templates" / "mod").mkdir(parents=True)
    (org / "templates" / "mod" / "README.md").write_text("x\n", encoding="utf-8")
    code, _, err = run("new", "My Mod", "--org-sync", str(org))
    assert code == 1 and "README.md" in err and "keep" in err and err.count("\n") == 1
    assert not (tmp_path / "My Mod").exists() and not (org / "calls.log").exists()
    with open(org / "repos" / "my-mod.toml", "a", encoding="utf-8") as f:
        f.write('keep = ["README.md", ".gitattributes"]\n')
    code, _, err = run("new", "My Mod", "--org-sync", str(org))
    assert code == 0, err
    assert (tmp_path / "My Mod/.gitattributes").is_file()  # kept by the repo: the scaffold writes it


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "x..y"])
def test_new_rejects_bad_org_name(tmp_path, run, bad):
    org = _fake_org(tmp_path, "my-mod", "../My Mod")
    code, _, err = run("new", "My Mod", "--org-sync", str(org), "--org-name", bad)
    assert code == 1 and "--org-name" in err
    assert not (tmp_path / "My Mod").exists()


def test_add_into_rolls_back_on_interrupt(tmp_path, run, monkeypatch):
    from se2modkit import scaffold
    assert run("new", "Core", "--prefix", "CO_")[0] == 0
    repo = tmp_path / "Core"
    before = (repo / "pyproject.toml").read_text(encoding="utf-8")

    def boom(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(scaffold, "load_project", boom)
    with pytest.raises(KeyboardInterrupt):
        run("new", "Drives", "--prefix", "DR_", "--into", str(repo), "--convert")
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == before
    assert not (repo / "mods/Drives").exists()


def test_generated_files_have_no_absolute_paths(tmp_path, run):
    assert run("new", "Core", "--prefix", "CO_")[0] == 0
    assert run("new", "Drives", "--prefix", "DR_", "--into", str(tmp_path / "Core"), "--convert")[0] == 0
    assert run("new", "Lib", "--library", "--prefix", "LB_")[0] == 0
    for repo in (tmp_path / "Core", tmp_path / "Lib"):
        for rel in _files(repo):
            if rel == "LICENSE":
                continue
            text = (repo / rel).read_text(encoding="utf-8")
            assert str(tmp_path) not in text and str(Path.home()) not in text, rel
            for word in _BANNED:
                assert word not in text, f"{rel}: {word}"


# --- every mod command takes MOD as a name or a path ---------------------------------------------------------------

_MOD_DESTS = ("mod", "mods")
_KNOWN = {"blender", "build", "clone-block", "copy", "override", "play", "report", "snapshot", "style", "test"}


def _mod_commands() -> list[tuple[str, list[str], object]]:
    """(command, sub-command path, parser holding the mod argument) for every registered command with a MOD arg."""
    out = []
    root = cli._parser()
    top = next(a for a in root._actions if a.dest == "command")

    def walk(name, path, parser):
        for a in parser._actions:
            if a.dest in _MOD_DESTS:
                out.append((name, path, parser))
                return True
        for a in parser._actions:
            if a.__class__.__name__ == "_SubParsersAction":
                for sub_name, sub in a.choices.items():
                    if walk(name, [*path, sub_name], sub):
                        return True
        return False

    for name, sp in top.choices.items():
        walk(name, [], sp)
    return out


def _argv(name: str, path: list[str], parser, mod: str) -> list[str]:
    argv = [name, *path]
    tail: list[str] = []
    for a in parser._actions:
        if not a.option_strings:
            if a.dest in _MOD_DESTS:
                argv.append(mod)
            elif a.nargs not in ("*", "?"):
                argv.append("x")
        elif a.dest in _MOD_DESTS:
            tail += [a.option_strings[-1], mod]
        elif a.required:
            tail += [a.option_strings[-1], "x"]
    for g in parser._mutually_exclusive_groups:
        if g.required:
            tail += [g._group_actions[0].option_strings[-1], "x"]
    return argv + tail


class _Stop(Exception):
    pass


def test_mod_commands_are_discovered():
    assert {name for name, _, _ in _mod_commands()} >= _KNOWN


@pytest.mark.parametrize("name,path,parser", _mod_commands(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_mod_command_accepts_mod_path(tmp_path, run, monkeypatch, name, path, parser):
    assert run("new", "Core", "--prefix", "CO_")[0] == 0
    repo = tmp_path / "Core"
    assert run("new", "Drives", "--prefix", "DR_", "--into", str(repo), "--convert")[0] == 0
    seen = []
    real = modarg.resolve

    def spy(*a, **kw):
        seen.append(real(*a, **kw))
        raise _Stop

    monkeypatch.setattr(modarg, "resolve", spy)
    monkeypatch.chdir(repo)
    with pytest.raises(_Stop):
        run(*_argv(name, path, parser, "Drives"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(_Stop):
        run(*_argv(name, path, parser, str(repo / "mods" / "Drives")))
    assert [(r.name, r.root.resolve()) for r in seen] == [("Drives", (repo / "mods/Drives").resolve())] * 2
    assert all(r.config is not None and r.config.prefix == "DR_" for r in seen)


def _monorepo(tmp_path: Path, run) -> Path:
    """A multi-mod repo whose root is not itself a mod: a bare [tool.se2modkit] table, then mods added into it."""
    repo = tmp_path / "Series"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[tool.se2modkit]\nprofile = "vanilla"\n', encoding="utf-8")
    for name, prefix in (("Core", "CO_"), ("Drives", "DR_"), ("Deco", "DE_")):
        code, _, err = run("new", name, "--prefix", prefix, "--into", str(repo))
        assert code == 0, err
    return repo


def test_new_into_bare_table_builds_a_monorepo(tmp_path, run):
    repo = _monorepo(tmp_path, run)
    project = load_project(repo)
    assert sorted(project.mods) == ["Core", "Deco", "Drives"]
    assert project.mods["Core"].root == repo / "mods/Core" and project.mods["Core"].profile == "vanilla"


@pytest.mark.parametrize("argv", [["style", "check"], ["test"], ["snapshot", ".", "--out", "x"]])
def test_multi_mod_root_without_mod_lists_names(tmp_path, run, monkeypatch, argv):
    repo = _monorepo(tmp_path, run)
    monkeypatch.chdir(repo)
    code, _, err = run(*argv)
    assert code in (1, 2) and "Which mod?" in err and "Core, Deco, Drives" in err and "Traceback" not in err
