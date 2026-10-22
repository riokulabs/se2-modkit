"""`previews.toml`: a mod's declarative preview shots, rendered by `se2mod preview` (no Blender here).

The file sits in the mod folder (beside the .vrgproj) or in its model source (`Source/Model/previews.toml`):

    [defaults]                      # optional; every shot inherits these
    resolution = [1600, 1000]
    lighting = "studio"             # studio | flat | dark (preview.base.LIGHTING)

    [sets.review]                   # a set renders into <Source/Model>/preview/review/ (git-ignored)
    blocks = ["XX_Box", "XX_Box_Large"]

    [[sets.review.shots]]
    name = "front"                  # file: <name>_<block>.png (or <name>.png for one block or `together`)
    camera = "3q_front"             # a preset (CAMERAS) or {position = [x, y, z], target = [x, y, z], lens = 50};
                                    # positions in metres from the block's centre, +Y = front, Z up
    state = "intact"                # STATES; "construction" takes `stage` (0..1); damage states take optional
                                    # `zones = {Fracture_01 = "dead"}` (intact | damaged | dead per zone)
    lod = 0                         # intact only: which exported LOD
    paint = "keen"                  # preview.mats.PAINTS
    together = false                # true: the shot's blocks side by side in one image

Shots render from the exported models (`<Source/Model>/Models/<block>/`), so export the blocks first. `select` picks
what a command line asks for; `job` is the JSON the Blender side (`preview.run`) reads.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["CAMERAS", "PLAN_NAMES", "STATES", "PlanError", "PreviewSet", "Shot", "contained", "find_plan", "job", "load_plan",
           "parse_plan", "select"]

PLAN_NAMES = ("previews.toml",)
CAMERAS = {   # preset -> (direction from the block to the camera, orthographic)
    "front": ((0, 1, 0), True), "back": ((0, -1, 0), True), "left": ((-1, 0, 0), True), "right": ((1, 0, 0), True),
    "top": ((0, 0.0001, 1), True), "bottom": ((0, 0.0001, -1), True),
    "3q_front": ((-0.6, 0.8, 0.45), False), "3q_rear": ((0.6, -0.8, 0.45), False),
    "3q_front_low": ((-0.6, 0.8, 0.12), False), "3q_front_high": ((-0.5, 0.6, 0.9), False),
}
STATES = {   # name -> (pipeline.states shot it draws, the kind of extra parameters it takes)
    "intact": ("done", None), "construction": (None, "stage"), "done_damaged": ("done_damaged", "zones"),
    "one_hit": ("one_hit", "zones"), "battered": ("battered", "zones"), "broken": ("broken", "zones"),
    "damaged": ("battered", "zones"), "zones-dead": ("broken", "zones"), "debris": ("debris", None),
}
LIGHTING = ("studio", "flat", "dark")
ZONE_STATES = ("intact", "damaged", "dead")
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_SHOT_KEYS = {"name", "blocks", "camera", "state", "stage", "zones", "lod", "paint", "lighting", "resolution", "lens",
              "fill", "together"}
_DEFAULT_KEYS = _SHOT_KEYS - {"name", "blocks"}


class PlanError(ValueError):
    """The plan cannot be used; the message is one plain sentence naming the file and the entry."""


@dataclass(frozen=True)
class Shot:
    name: str
    blocks: tuple[str, ...]
    camera: str | dict = "3q_front"
    state: str = "intact"
    stage: float | None = None
    zones: dict | None = None
    lod: int = 0
    paint: str = "keen"
    lighting: str = "studio"
    resolution: tuple[int, int] = (1600, 1000)
    lens: float = 50.0
    fill: float = 0.8
    together: bool = False


@dataclass(frozen=True)
class PreviewSet:
    name: str
    blocks: tuple[str, ...]
    shots: tuple[Shot, ...] = field(default_factory=tuple)


def find_plan(mod_root: Path, source_model: Path | None = None) -> Path | None:
    for d in (mod_root, source_model):
        if d is None:
            continue
        for n in PLAN_NAMES:
            if (Path(d) / n).is_file():
                return Path(d) / n
    return None


def load_plan(path: Path) -> dict[str, PreviewSet]:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise PlanError(f"{path}: cannot read it ({e.strerror or e}).") from None
    except tomllib.TOMLDecodeError as e:
        raise PlanError(f"{path}: not valid TOML ({e}).") from None
    return parse_plan(data, str(path))


_RESERVED = re.compile(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", re.I)   # Windows device names


def _safe_name(n) -> bool:
    """A name that becomes part of a file path: letters, digits, '_', '.', '-'; no separators, no '..', no Windows
    device name (CON, NUL, COM1 ... with any extension) and no trailing dot."""
    return (isinstance(n, str) and bool(_NAME_RE.fullmatch(n)) and ".." not in n and not n.endswith(".")
            and not _RESERVED.fullmatch(n))


def contained(base: Path, path: Path, what: str) -> Path:
    """`path` resolved, if it stays inside `base` (symlinks and '..' resolved); PlanError otherwise."""
    b, p = Path(base).resolve(), Path(path).resolve()
    if not p.is_relative_to(b):
        raise PlanError(f"{what} {path} leaves {base}; use plain names (letters, digits, '_', '.', '-').")
    return p


def _num(v, where, lo=None, hi=None, kind=float):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (kind is int and not isinstance(v, int)):
        raise PlanError(f"{where}: expected a{'n integer' if kind is int else ' number'}, got {v!r}.")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise PlanError(f"{where}: {v!r} is out of range ({lo}..{hi}).")
    return kind(v)


def _vec3(v, where):
    if not (isinstance(v, list) and len(v) == 3):
        raise PlanError(f"{where}: expected [x, y, z].")
    return [_num(x, where) for x in v]


def _shot(raw: Mapping, defaults: Mapping, set_blocks: tuple[str, ...], where: str) -> Shot:
    if not isinstance(raw, Mapping):
        raise PlanError(f"{where}: a shot is a table ([[sets.<set>.shots]]).")
    unknown = sorted(set(raw) - _SHOT_KEYS)
    if unknown:
        raise PlanError(f"{where}: unknown key(s) {', '.join(unknown)} (known: {', '.join(sorted(_SHOT_KEYS))}).")
    v = {**defaults, **raw}
    name = v.get("name")
    if not _safe_name(name):
        raise PlanError(f"{where}: name {name!r} must be letters, digits, '_', '.' or '-' (no '..', no device name such as CON or NUL).")
    where = f"{where} ({name})"
    blocks = v.get("blocks", set_blocks)
    if not (isinstance(blocks, list | tuple) and blocks):
        raise PlanError(f"{where}: no blocks (give the set or the shot a blocks = [...] list).")
    bad = [b for b in blocks if not _safe_name(b)]
    if bad:
        raise PlanError(f"{where}: block name(s) {', '.join(map(repr, bad))} must be letters, digits, '_', '.' or "
                        "'-' (no '..', no path separators).")
    cam = v.get("camera", "3q_front")
    if isinstance(cam, str):
        if cam not in CAMERAS:
            raise PlanError(f"{where}: unknown camera {cam!r} (presets: {', '.join(CAMERAS)}).")
    elif isinstance(cam, Mapping):
        extra = sorted(set(cam) - {"position", "target", "lens", "ortho"})
        if extra or "position" not in cam:
            raise PlanError(f"{where}: a custom camera is {{position = [x, y, z], target = [x, y, z], lens = 50}}.")
        cam = {"position": _vec3(cam["position"], f"{where} camera.position"),
               "target": _vec3(cam.get("target", [0, 0, 0]), f"{where} camera.target"),
               "lens": _num(cam.get("lens", 50), f"{where} camera.lens", 1, 1000),
               **({"ortho": _num(cam["ortho"], f"{where} camera.ortho", 0.001)} if "ortho" in cam else {})}
    else:
        raise PlanError(f"{where}: camera must be a preset name or a table.")
    state = v.get("state", "intact")
    if state not in STATES:
        raise PlanError(f"{where}: unknown state {state!r} (use {', '.join(STATES)}).")
    stage = v.get("stage")
    if state == "construction":
        if stage is None:
            raise PlanError(f"{where}: state \"construction\" needs stage = 0..1 (the build ratio).")
        stage = _num(stage, f"{where} stage", 0.0, 0.9999)
    elif stage is not None:
        raise PlanError(f"{where}: stage only goes with state = \"construction\".")
    zones = v.get("zones")
    if zones is not None:
        if STATES[state][1] != "zones":
            raise PlanError(f"{where}: zones only go with a damage state (damaged, zones-dead, one_hit, ...).")
        if not isinstance(zones, Mapping) or not all(z in ZONE_STATES for z in zones.values()):
            raise PlanError(f"{where}: zones = {{<zone id> = \"intact\" | \"damaged\" | \"dead\"}}.")
        zones = dict(zones)
    lighting = v.get("lighting", "studio")
    if lighting not in LIGHTING:
        raise PlanError(f"{where}: unknown lighting {lighting!r} (use {', '.join(LIGHTING)}).")
    res = v.get("resolution", [1600, 1000])
    if not (isinstance(res, list | tuple) and len(res) == 2):
        raise PlanError(f"{where}: resolution = [width, height].")
    res = (_num(res[0], f"{where} resolution", 16, 8192, int), _num(res[1], f"{where} resolution", 16, 8192, int))
    paint = v.get("paint", "keen")
    from .mats import PAINTS
    if paint not in PAINTS:
        raise PlanError(f"{where}: unknown paint {paint!r} (use {', '.join(PAINTS)}).")
    together = v.get("together", False)
    if not isinstance(together, bool):
        raise PlanError(f"{where}: together must be true or false.")
    return Shot(name=name, blocks=tuple(blocks), camera=cam, state=state, stage=stage, zones=zones,
                lod=_num(v.get("lod", 0), f"{where} lod", 0, 9, int), paint=paint, lighting=lighting,
                resolution=res, lens=_num(v.get("lens", 50), f"{where} lens", 1, 1000),
                fill=_num(v.get("fill", 0.8), f"{where} fill", 0.05, 1.0), together=together)


def parse_plan(data: Mapping, where: str = "previews.toml") -> dict[str, PreviewSet]:
    unknown = sorted(set(data) - {"defaults", "sets"})
    if unknown:
        raise PlanError(f"{where}: unknown top-level key(s) {', '.join(unknown)} (use [defaults] and [sets.<name>]).")
    defaults = data.get("defaults", {})
    if not isinstance(defaults, Mapping) or set(defaults) - _DEFAULT_KEYS:
        raise PlanError(f"{where}: [defaults] may set only {', '.join(sorted(_DEFAULT_KEYS))}.")
    sets = data.get("sets", {})
    if not isinstance(sets, Mapping) or not sets:
        raise PlanError(f"{where}: no preview set (add [sets.<name>] with blocks and [[sets.<name>.shots]]).")
    out = {}
    for sname, raw in sets.items():
        w = f"{where} [sets.{sname}]"
        if not _safe_name(sname):
            raise PlanError(f"{w}: a set name is letters, digits, '_', '.' or '-'.")
        if not isinstance(raw, Mapping) or set(raw) - {"blocks", "shots"}:
            raise PlanError(f"{w}: a set holds blocks = [...] and [[sets.{sname}.shots]] only.")
        blocks = raw.get("blocks", [])
        if not isinstance(blocks, list):
            raise PlanError(f"{w}: blocks must be a list of block names.")
        bad = [b for b in blocks if not _safe_name(b)]
        if bad:
            raise PlanError(f"{w}: block name(s) {', '.join(map(repr, bad))} must be letters, digits, '_', '.' or "
                            "'-' (no '..', no path separators).")
        shots_raw = raw.get("shots", [])
        if not isinstance(shots_raw, list) or not shots_raw:
            raise PlanError(f"{w}: no shots (add [[sets.{sname}.shots]] entries).")
        shots = tuple(_shot(s, defaults, tuple(blocks), f"{w} shot {i + 1}") for i, s in enumerate(shots_raw))
        names = [s.name for s in shots]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise PlanError(f"{w}: shot names repeat: {', '.join(dup)}.")
        out[sname] = PreviewSet(sname, tuple(blocks), shots)
    return out


def select(plan: Mapping[str, PreviewSet], *, set_name: str | None = None, blocks: Iterable[str] = (),
           shot: str | None = None, still: bool = False) -> tuple[PreviewSet, list[Shot]]:
    """The set and the shots a command line asks for: `set_name` (needed when the plan has several sets), only the
    shots of `blocks` (each shot's blocks cut down to them), only `shot`; `still`: the first remaining shot of the
    first remaining block."""
    if set_name is None:
        if len(plan) != 1:
            raise PlanError(f"Which preview set? The plan has: {', '.join(plan)} (pass --set NAME).")
        set_name = next(iter(plan))
    if set_name not in plan:
        raise PlanError(f"No preview set {set_name!r}; the plan has: {', '.join(plan)}.")
    ps = plan[set_name]
    want = list(blocks)
    shots = list(ps.shots)
    if shot is not None:
        shots = [s for s in shots if s.name == shot]
        if not shots:
            raise PlanError(f"No shot {shot!r} in set {set_name}; it has: {', '.join(s.name for s in ps.shots)}.")
    if want:
        cut = []
        for s in shots:
            bs = tuple(b for b in s.blocks if b in want)
            if bs:
                cut.append(Shot(**{**asdict(s), "blocks": bs}))
        if not cut:
            raise PlanError(f"No shot of set {set_name} shows {', '.join(want)}.")
        shots = cut
    if still:
        s = shots[0]
        shots = [Shot(**{**asdict(s), "blocks": s.blocks[:1], "together": False})]
    return ps, shots


def job(*, mod_name: str, prefix: str, paths: Mapping[str, str | None], out: Path, shots: Iterable[Shot],
        fast: bool) -> dict:
    """The JSON document `preview.run` reads inside Blender."""
    return {"mod": {"name": mod_name, "prefix": prefix, "paths": {k: (str(v) if v else None) for k, v in paths.items()}},
            "out": str(out), "fast": bool(fast), "shots": [asdict(s) for s in shots]}
