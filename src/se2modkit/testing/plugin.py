"""The se2modkit pytest plugin (entry point `pytest11`, loaded in every pytest run where se2-modkit is installed).

Markers (registered here; the skips apply to tests inside a `[tool.se2modkit]` mod, and `sdkbuild` everywhere):
  build       needs the mod's built models (build_report.json); skipped when the mod is not built
  sdkbuild    runs the real SDK build: opt-in with SE2MODKIT_SDK_BUILD=1 (the mod's old `<PREFIX>SDK_BUILD=1` is
              accepted as an alias for one release)
  blender     needs Blender; skipped without it
  sdk         needs the Mod SDK's vanilla Assets; skipped without them
  decompiled  needs the game's type facts or a decompiled game; skipped without both
  scripts     needs the mod's optional C# layer (Scripts/); skipped without it
  slow        a long check (seconds); runs by default, deselect with -m "not slow" for a quick inner loop

Fixtures: `se2modkit.testing.fixtures`. Gates: `se2modkit.testing.gates`, collected where a mod's tests import
`test_gate` (`Tests/test_gates.py`: `from se2modkit.testing.gates import test_gate`), one item per gate listed in
the mod's `[tool.se2modkit] gates`. `--se2-gate NAME` (repeatable) runs only those gates and deselects every other
test (`se2mod test --gate NAME`).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from . import gates
from .fixtures import (  # noqa: F401  (fixtures: pytest registers what this plugin module holds)
    build_report,
    facts_status,
    item_mod,
    mod_assets,
    mod_root,
    scratch_stage,
    sdk_assets,
    se2_env,
    se2_mod,
    staged_assets,
    style_profile,
    type_index,
    universe,
)

MARKERS = {
    "build": "needs the mod's built models (build_report.json); skipped when the mod is not built",
    "sdkbuild": "runs the real SDK build; opt-in with SE2MODKIT_SDK_BUILD=1",
    "blender": "needs Blender; skipped without it",
    "sdk": "needs the Space Engineers 2 Mod SDK's vanilla Assets; skipped without them",
    "decompiled": "needs the game's type facts (se2mod facts build) or a decompiled game; skipped without both",
    "scripts": "needs the mod's optional C# layer (Scripts/); skipped without it",
    "slow": "a long check; runs by default, deselect with -m 'not slow' for a quick inner loop",
}
SDK_BUILD_ENV = "SE2MODKIT_SDK_BUILD"


def pytest_addoption(parser):
    group = parser.getgroup("se2modkit")
    group.addoption("--se2-gate", action="append", default=[], metavar="NAME",
                    help="run only this shared gate (repeatable); every other test is deselected")


def pytest_configure(config):
    for name, text in MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {text}")
    unknown = sorted(set(config.getoption("se2_gate") or ()) - set(gates.GATES))
    if unknown:
        raise pytest.UsageError(f"Unknown gate {unknown[0]!r} for --se2-gate; known: {', '.join(gates.GATES)}.")


def pytest_generate_tests(metafunc):
    if "se2_gate" not in metafunc.fixturenames:
        return
    cases = gates.cases_for(Path(metafunc.definition.path))
    metafunc.parametrize("se2_gate", [pytest.param(c, marks=[getattr(pytest.mark, m) for m in c.marks], id=c.id)
                                      for c in cases])


def pytest_collection_modifyitems(config, items):
    wanted = set(config.getoption("se2_gate") or ())
    if not wanted:
        return
    keep, drop = [], []
    for item in items:
        callspec = getattr(item, "callspec", None)
        case = callspec.params.get("se2_gate") if callspec is not None else None
        (keep if case is not None and case.name in wanted else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def _sdk_build_opted_in(mod) -> bool:
    names = [SDK_BUILD_ENV] + ([f"{mod.prefix}SDK_BUILD"] if mod is not None and mod.prefix else [])
    return any(os.environ.get(n) == "1" for n in names)


@pytest.fixture(autouse=True)
def _se2modkit_markers(request):
    """Skip a marked test whose requirement is missing (one sentence naming it and how to provide it)."""
    node = request.node
    marks = {m.name for m in node.iter_markers()} & set(MARKERS)
    if not marks:
        return
    mod = item_mod(node)
    if "sdkbuild" in marks and not _sdk_build_opted_in(mod):
        alias = f" (or {mod.prefix}SDK_BUILD=1)" if mod is not None and mod.prefix else ""
        pytest.skip(f"opt-in: set {SDK_BUILD_ENV}=1{alias} to run the real SDK build")
    if mod is None:
        return
    env_marks = marks & {"sdk", "decompiled", "blender"}
    if env_marks:
        env = request.getfixturevalue("se2_env")
        if "sdk" in env_marks and (env.sdk_assets is None or not Path(env.sdk_assets).is_dir()):
            pytest.skip("no Mod SDK vanilla Assets: install the Space Engineers 2 Mod SDK or set SE2_SDK_ASSETS")
        if "decompiled" in env_marks:
            facts, dec, why = facts_status(env)
            if facts is None and dec is None:
                pytest.skip(why)
        if "blender" in env_marks and env.blender is None:
            pytest.skip("no Blender: install Blender or set SE2_BLENDER")
    if "build" in marks and not Path(mod.paths.report).is_file():
        pytest.skip(f"{mod.name} is not built (no {Path(mod.paths.report).name}): build the models first")
    if "scripts" in marks and not (Path(mod.root) / "Scripts").is_dir():
        pytest.skip(f"{mod.name} has no Scripts/: the data-only package (the C# layer is optional)")
