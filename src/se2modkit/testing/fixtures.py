"""Fixtures of the se2modkit pytest plugin (registered by `se2modkit.testing.plugin`).

A mod's `Tests/conftest.py` may override any of them by name, e.g. `staged_assets` (where this run staged the package)
or `scratch_stage` (how to stage the package into a scratch dir, for the `fresh_clone` and `determinism` gates).

  se2_env        (session) the discovered environment (`env.core.discover_env`)
  se2_mod        the mod under test: a gate's own mod, else the `[tool.se2modkit]` mod containing the test file
  mod_root       the mod's directory (the one holding its .vrgproj)
  mod_assets     the mod's Assets/
  staged_assets  the staged package (`paths.staged`) as it is on disk (not restaged: may be stale; a warning says so
                 when it is older than the build report), or None when nothing is staged
  sdk_assets     (session) the Mod SDK's vanilla Assets; skips without the SDK
  type_index     (session) a `validate.facts.TypeIndex` for the installed game; skips without type facts or a decompile
  universe       the load as installed: vanilla, the mod's Assets, then the staging (`validate.clientserver.Universe`)
  build_report   the mod's build_report.json; skips when the mod is not built
  style_profile  the mod's style profile (its `profile` with its `[style]` overrides)
  scratch_stage  None, or fn(mod_assets: Path, out: Path, guids: Path) staging the package from `mod_assets` into
                 `out` with the asset GUID store `guids` (a mod's conftest provides it)
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import pytest

from ..project import ModConfig, Project, ProjectError, load_project

__all__ = ["build_report", "facts_status", "item_mod", "load_type_index", "mod_assets", "mod_for", "mod_root",
           "project_for", "scratch_stage", "sdk_assets", "se2_env", "se2_mod", "staged_assets", "style_profile",
           "type_index", "universe"]

_PROJECTS: dict[Path, Project | None] = {}


def project_for(path: Path) -> Project | None:
    """The `[tool.se2modkit]` project at or above `path` (cached per directory), or None."""
    d = Path(path)
    d = d if d.is_dir() else d.parent
    if d not in _PROJECTS:
        try:
            _PROJECTS[d] = load_project(d)
        except ProjectError:
            _PROJECTS[d] = None
    return _PROJECTS[d]


def mod_for(path: Path) -> ModConfig | None:
    """The mod containing `path` (the only mod of a single-mod project), or None."""
    proj = project_for(path)
    if proj is None:
        return None
    mod = proj.containing(path)
    if mod is None and len(proj.mods) == 1:
        mod = next(iter(proj.mods.values()))
    return mod


def item_mod(node) -> ModConfig | None:
    """A test item's mod: a gate item's own mod, else the mod containing its file."""
    callspec = getattr(node, "callspec", None)
    if callspec is not None and "se2_gate" in callspec.params:
        return callspec.params["se2_gate"].mod
    path = getattr(node, "path", None)
    return mod_for(Path(path)) if path is not None else None


def facts_status(env) -> tuple[Path | None, Path | None, str]:
    """(facts file for the installed game if present, decompiled dir if set and present, why neither is usable)."""
    from ..validate.facts import FactsError, facts_path
    facts = None
    if env.game_version:
        try:
            p = facts_path(env.game_version, cache_dir=env.cache_dir)
            facts = p if p.is_file() else None
        except FactsError:
            facts = None
    dec = Path(env.decompiled_dir) if env.decompiled_dir is not None and Path(env.decompiled_dir).is_dir() else None
    why = ("no type facts for the installed game and no decompiled game: set SE2_DECOMPILED (or decompiled_dir in the "
           "se2modkit user config), then run `se2mod facts build`")
    return facts, dec, why


def load_type_index(env):
    """A TypeIndex for the installed game: its facts from the user cache, else built from the decompiled game (and
    cached for the next run), else None. Facts made from another decompile than the configured one are rebuilt."""
    import os

    from ..validate.facts import FactsError, TypeIndex, build_facts, load_facts, write_facts
    facts, dec, _ = facts_status(env)
    if facts is not None:
        data = load_facts(facts)
        if dec is None or data.get("source") == Path(os.path.realpath(dec)).name:
            return TypeIndex(data)
    if dec is None:
        return None
    data = build_facts(dec, env.game_version or "unknown")
    if env.game_version:
        try:
            write_facts(data, cache_dir=env.cache_dir)
        except (FactsError, OSError):
            pass
    return TypeIndex(data)


@pytest.fixture(scope="session")
def se2_env():
    from ..env.core import discover_env
    return discover_env()


@pytest.fixture
def se2_mod(request) -> ModConfig:
    mod = item_mod(request.node)
    if mod is None:
        pytest.skip("not inside a [tool.se2modkit] mod (no pyproject.toml with that table names a mod here)")
    return mod


@pytest.fixture
def mod_root(se2_mod) -> Path:
    return Path(se2_mod.root)


@pytest.fixture
def mod_assets(se2_mod) -> Path:
    return Path(se2_mod.paths.assets)


class StaleStaging(UserWarning):
    """The default `staged_assets` is older than the mod's build report."""


_STALE_WARNED: set[Path] = set()


def _newest_mtime(root: Path) -> float | None:
    newest = None
    for dp, _, fs in os.walk(root):
        for f in fs:
            try:
                m = os.stat(os.path.join(dp, f)).st_mtime
            except OSError:
                continue
            newest = m if newest is None else max(newest, m)
    return newest


@pytest.fixture
def staged_assets(se2_mod) -> Path | None:
    """The mod's staged package (`paths.staged`) AS IT IS ON DISK, or None when nothing is staged. This default does
    not stage: the files may be left over from an older run. When their newest file is older than the build report,
    a StaleStaging warning says so (once per mod and session). A mod whose tests stage the package themselves
    overrides this fixture in its conftest to return that run's staging."""
    p = Path(se2_mod.paths.staged)
    if not p.is_dir():
        return None
    rep = Path(se2_mod.paths.report)
    if p not in _STALE_WARNED and rep.is_file():
        newest = _newest_mtime(p)
        if newest is None or newest < rep.stat().st_mtime:
            _STALE_WARNED.add(p)
            warnings.warn(f"{se2_mod.name}: the staged package in {p} is older than {rep.name}; restage it, or "
                          "override the staged_assets fixture with one that stages", StaleStaging, stacklevel=2)
    return p


@pytest.fixture(scope="session")
def sdk_assets(se2_env) -> Path:
    p = se2_env.sdk_assets
    if p is None or not Path(p).is_dir():
        pytest.skip("no Mod SDK vanilla Assets: install the Space Engineers 2 Mod SDK or set SE2_SDK_ASSETS")
    return Path(p)


@pytest.fixture(scope="session")
def type_index(se2_env):
    ti = load_type_index(se2_env)
    if ti is None:
        pytest.skip(facts_status(se2_env)[2])
    return ti


@pytest.fixture
def universe(sdk_assets, mod_assets, staged_assets):
    from ..validate.clientserver import Universe
    roots = [sdk_assets, mod_assets] + ([staged_assets] if staged_assets is not None else [])
    return Universe([str(r) for r in roots], cached_roots=[str(sdk_assets)])


@pytest.fixture
def build_report(se2_mod) -> dict:
    p = Path(se2_mod.paths.report)
    if not p.is_file():
        pytest.skip(f"{se2_mod.name} is not built (no {p.name}): build the models first")
    with open(p, encoding="utf-8-sig") as fh:
        return json.load(fh)


@pytest.fixture
def style_profile(se2_mod):
    from ..style.profile import load
    return load(se2_mod.profile, overrides=se2_mod.style)


@pytest.fixture
def scratch_stage():
    return None
