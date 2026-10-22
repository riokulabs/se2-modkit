"""Synthetic `ModHooks` for the pipeline, defs and stage tests: a mod `XX_Mod` with prefix `XX_` in a temp dir."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from se2modkit.pipeline import ModHooks
from se2modkit.project import ModPaths

# Hand-written stamps (not the installed SDK's): a test sees exactly these in what it stages.
VRAGE = {"System.Runtime": "1.0.0.0", "VRage": "9.8.7.6"}
GAME = {"Game2": "9.8.7.6", "System.Runtime": "1.0.0.0", "VRage": "9.8.7.6"}
STAMPS = (VRAGE, GAME)
NAME = "XX_Mod"


@dataclass
class Spec:
    """The block fields the clone and stage steps read."""
    name: str
    vanilla_source: str = ""
    copy_from: str = ""
    family: str = "box"


def paths(root: Path) -> ModPaths:
    sm = root / "Source" / "Model"
    return ModPaths(models=sm / "Models", report=sm / "build_report.json", staged=sm / "integration" / "Assets",
                    golden_states=root / "Tests" / "golden" / "states_baseline.json", source_model=sm,
                    asset_guids=sm / "asset_guids.json", root=root)


def make_hooks(root: Path, **kw) -> ModHooks:
    root.mkdir(parents=True, exist_ok=True)
    kw.setdefault("bundle_stamps", copy.deepcopy(STAMPS))   # never the shared constants (xdist, test order)
    return ModHooks(prefix="XX_", name=NAME, paths=paths(root), **kw)


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
