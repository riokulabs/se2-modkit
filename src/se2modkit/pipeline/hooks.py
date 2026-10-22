"""`ModHooks`: the one registry a mod hands the pipeline.

A mod builds one `ModHooks` (usually `ModHooks.for_mod(project.mod(...))`), lets its family modules fill the tables in
place during registration (`hooks.roles["box"] = {...}`, `hooks.plans["box"] = plan`), checks it with `validate()`
and passes it as the `hooks` argument to every pipeline, definition and staging function. Nothing in modkit reads a
mod's tables through a module global or an import side effect.

Tables (empty by default except `damaged_tris_x`; a family is the block family name a mod's specs carry):

    keep_whole        {family: (part names)}               parts never cut by a fracture seam
    roles             {family: {role: (part names)}}       construction roles per part (skin is the default)
    dent              {family: dict(amp, freq, fine, fine_freq, max_edge[, unpin])}   the Damaged dent field
    jag               {family: dict(step, amp, freq, reach)}                           the ragged break
    plans             {family: fn(spec, boxes) -> Plan}    the fracture plan
    unfractured       {block name: reason} or {family: fn(spec) -> reason | None}
                                                           blocks deliberately not breakable (`unfractured_reason`)
    unfractured_boxes (physics box names)                  collision boxes no fracture zone takes
    damaged_tris_x    {"default": 1.2, family: ratio}      Damaged / Fractured triangle ratio cap (`tris_x`)
    standins          {block name: (StandIn, ...)}         parked subparts baked into the Fractured / Damaged model
    min_share_exempt  {(family, zone id)}                  zones kept on their shaped hull under the minimum share
    core_parts        {family: (part names)}               the solid core of a broken zone
    scaffold_zone_boxes  {family: thinnest hull (m)}       caged along its zone hulls (those at least this thick get a
                                                           cage box), not one envelope box; a set means 0.0 for each
    spark_kind        {family: fn(spec) -> str | None}     the spark effect kind
    per_zone          {family: fn(spec) -> bool}           one spark emitter per zone
    analogue          {family: (vanilla block names)}      the vanilla blocks the states harness renders beside ours
    coplanar_subparts {subpart model names}                self-coplanar audit
    leaf_frame        {family: fn(spec, built) -> [rows]}  moving subpart vs frame coplanar audit
    spawned_subparts  {family: fn(spec) -> [(subpart model, pose name, 4x4 Matrix)]}   the subparts a Functional block
                                                           spawns, at their pose in the block frame (subpart vs block
                                                           coplanar audit, pipeline.zfight)
    decal_api         {(file name, function)}             the mod's own decal helpers: each outermost call is one decal
                                                           element of the z-fight audit (on top of modkit's decal API)
    zfight_bury_d     float | None                         how far in front of a decal a same-facing face buries it
                                                           (None: zfight.BURY_D)
    connect_roots     {part name}                          connectivity roots (pipeline.connect)
    realism_manual    {key: ...}                           manual realism verdicts (pipeline.realism)
    material_slots    {slot: (guid, vanilla .def path)}    the mod's own material slots (on top of defs.material_guids)
    damaged_slots     {slot: slot}                         the mod's Damaged-model slot remaps (on top of the generic)
    clone_also        {vanilla copy_from: fn(name) -> ["FROM=TO", ...]}  extra file renames in the clone copy
    clone_drop        {vanilla copy_from: (file-name suffixes)}         cloned files the clone step deletes

`bundle_stamps` pins the `($Bundles` VRage, Game2) stamps a mod writes; None (the default) reads them from the
installed Mod SDK (`env.versions.bundle_stamps`) once, the first time `stamps(env)` gets an env. `stamps()` hands
out fresh copies, so nothing a caller does to a written file's `$Bundles` leaks into later files.

`accumulate_bundles` (default False) reproduces the legacy pipelines exactly: there the stamps were shared module
dicts, and `stage.declare_bundles` added a file's newly used bundles (e.g. Game2) to them, so every later envelope of
the run (.meta files, .model assets, new partialdefs) carried them too. With the flag on, `stamps()` returns one shared
pair per staging run (fresh at `stage.reset`) and `declare_bundles` mutates it as before. It exists only so a mod can
move onto modkit with byte-identical output and drop the extra bundles later in a commit of its own.
"""

from __future__ import annotations

import copy
import numbers
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..env.errors import EnvError
from ..env.versions import bundle_stamps
from ..project import ModConfig, ModPaths, ProjectError

if TYPE_CHECKING:
    from ..env.core import SE2Env

__all__ = ["HooksError", "ModHooks", "Stamps"]

Stamps = tuple[dict[str, str], dict[str, str]]

_PREFIX_RE = re.compile(r"[A-Z][A-Z0-9]{1,7}_")   # as `se2mod new --prefix` accepts it
_CALLABLE_TABLES = ("plans", "spark_kind", "per_zone", "leaf_frame", "spawned_subparts", "clone_also")
# Tables keyed by family that only mean something for a family the mod can fracture.
_FRACTURE_TABLES = ("keep_whole", "roles", "dent", "jag", "core_parts")


class HooksError(ProjectError):
    """The registry is not usable; the message is plain sentences."""


@dataclass(kw_only=True)
class ModHooks:
    prefix: str
    name: str
    paths: ModPaths
    keep_whole: dict[str, tuple[str, ...]] = field(default_factory=dict)
    roles: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    dent: dict[str, dict[str, Any]] = field(default_factory=dict)
    jag: dict[str, dict[str, Any]] = field(default_factory=dict)
    plans: dict[str, Callable] = field(default_factory=dict)
    unfractured: dict[str, str | Callable[[Any], str | None]] = field(default_factory=dict)
    unfractured_boxes: tuple[str, ...] = ()
    damaged_tris_x: dict[str, float] = field(default_factory=lambda: {"default": 1.2})
    standins: dict[str, tuple] = field(default_factory=dict)
    min_share_exempt: set[tuple[str, int]] = field(default_factory=set)
    core_parts: dict[str, tuple[str, ...]] = field(default_factory=dict)
    scaffold_zone_boxes: dict[str, float] = field(default_factory=dict)
    spark_kind: dict[str, Callable] = field(default_factory=dict)
    per_zone: dict[str, Callable] = field(default_factory=dict)
    analogue: dict[str, tuple[str, ...]] = field(default_factory=dict)
    coplanar_subparts: set[str] = field(default_factory=set)
    leaf_frame: dict[str, Callable] = field(default_factory=dict)
    spawned_subparts: dict[str, Callable] = field(default_factory=dict)
    decal_api: set[tuple[str, str]] = field(default_factory=set)
    zfight_bury_d: float | None = None
    # configuration of pipeline.connect / realism (their registry is run state of pipeline.connect, not hooks)
    connect_roots: set[str] = field(default_factory=set)
    realism_manual: dict = field(default_factory=dict)
    material_slots: dict[str, tuple[str, str]] = field(default_factory=dict)
    damaged_slots: dict[str, str] = field(default_factory=dict)
    clone_also: dict[str, Callable[[str], list[str]]] = field(default_factory=dict)
    clone_drop: dict[str, tuple[str, ...]] = field(default_factory=dict)
    bundle_stamps: Stamps | None = None
    accumulate_bundles: bool = False
    # Run state, not configuration: the SDK stamps once read, the run's shared stamps (accumulate_bundles), and the
    # files `stage` wrote in this staging run.
    _stamps: Stamps | None = field(default=None, init=False, repr=False, compare=False)
    _run_stamps: Stamps | None = field(default=None, init=False, repr=False, compare=False)
    _staged: set[str] = field(default_factory=set, init=False, repr=False, compare=False)

    def __post_init__(self):
        if isinstance(self.scaffold_zone_boxes, (set, frozenset, list, tuple)):
            self.scaffold_zone_boxes = {fam: 0.0 for fam in sorted(self.scaffold_zone_boxes)}
        self.unfractured_boxes = tuple(self.unfractured_boxes)

    @classmethod
    def for_mod(cls, mod: ModConfig, **kw) -> ModHooks:
        """Hooks for a project mod: its prefix, name and paths; `kw` sets tables or `bundle_stamps`."""
        return cls(prefix=mod.prefix, name=mod.name, paths=mod.paths, **kw)

    def stamps(self, env: SE2Env | None = None) -> Stamps:
        """`(vrage, game)` bundle stamps: the pinned `bundle_stamps`, else the installed Mod SDK's (read from `env`
        the first time one is given, then kept). A fresh copy per call, except with `accumulate_bundles`: then the
        run's one shared pair (see the module docstring)."""
        if self.bundle_stamps is not None:
            base = self.bundle_stamps
        else:
            if self._stamps is None:
                if env is None:
                    raise EnvError(f"No bundle stamps for {self.name}: pass the environment (the Mod SDK's stamps) "
                                   "to the first staging or clone call, or pin ModHooks.bundle_stamps.")
                self._stamps = bundle_stamps(env)
            base = self._stamps
        if not self.accumulate_bundles:
            return copy.deepcopy(base)
        if self._run_stamps is None:
            self._run_stamps = copy.deepcopy(base)
        return self._run_stamps

    def unfractured_reason(self, spec) -> str | None:
        """Why a block keeps no fractures, or None: a reason keyed by its block name, else the callable keyed by its
        family."""
        v = self.unfractured.get(spec.name)
        if isinstance(v, str):
            return v
        fn = self.unfractured.get(getattr(spec, "family", None))
        return fn(spec) if callable(fn) else None

    def tris_x(self, family: str) -> float:
        """The Damaged / Fractured triangle ratio cap of a family (`damaged_tris_x`, else its "default")."""
        return self.damaged_tris_x.get(family, self.damaged_tris_x["default"])

    def check(self) -> None:
        """Raise `HooksError` (every problem, one sentence each) unless `validate()` is clean."""
        errs = self.validate()
        if errs:
            raise HooksError(f"The hooks of {self.name or '?'} are not usable: " + " ".join(errs))

    def validate(self) -> list[str]:
        """Problems with the registry, one plain sentence each (empty when it is usable)."""
        errs: list[str] = []
        if not _PREFIX_RE.fullmatch(self.prefix or ""):
            errs.append(f"prefix {self.prefix!r}: use 2-8 capital letters or digits starting with a letter, then an "
                        "underscore (e.g. XX_).")
        if not self.name:
            errs.append("name: the mod needs a name.")
        for t in _CALLABLE_TABLES:
            for k, v in getattr(self, t).items():
                if not callable(v):
                    errs.append(f"{t}[{k!r}]: {type(v).__name__} is not callable.")
        for t in _FRACTURE_TABLES:
            for fam in getattr(self, t):
                if fam not in self.plans:
                    errs.append(f"{t}[{fam!r}]: no fracture plan for family {fam!r} in plans.")
        for item in sorted(self.min_share_exempt, key=repr):
            if not (isinstance(item, tuple) and len(item) == 2):
                errs.append(f"min_share_exempt {item!r}: expected a (family, zone id) pair.")
            elif item[0] not in self.plans:
                errs.append(f"min_share_exempt {item!r}: no fracture plan for family {item[0]!r} in plans.")
        for k, v in self.unfractured.items():
            if not (isinstance(v, str) or callable(v)):
                errs.append(f"unfractured[{k!r}]: expected a reason or fn(spec) -> reason | None, got "
                            f"{type(v).__name__}.")
        if not all(isinstance(b, str) for b in self.unfractured_boxes):
            errs.append(f"unfractured_boxes {self.unfractured_boxes!r}: expected physics box names.")
        if not isinstance(self.damaged_tris_x.get("default"), numbers.Real):
            errs.append("damaged_tris_x: needs a numeric \"default\" entry.")
        for k, v in self.damaged_tris_x.items():
            if not (isinstance(v, numbers.Real) and v > 0):
                errs.append(f"damaged_tris_x[{k!r}]: {v!r} is not a positive ratio.")
        for k, v in self.scaffold_zone_boxes.items():
            if not (isinstance(v, numbers.Real) and v >= 0):
                errs.append(f"scaffold_zone_boxes[{k!r}]: {v!r} is not a thickness in metres (>= 0).")
        for item in sorted(self.decal_api, key=repr):
            if not (isinstance(item, tuple) and len(item) == 2 and all(isinstance(x, str) for x in item)):
                errs.append(f"decal_api {item!r}: expected a (file name, function name) pair.")
        if self.zfight_bury_d is not None and not (isinstance(self.zfight_bury_d, numbers.Real)
                                                   and self.zfight_bury_d > 0):
            errs.append(f"zfight_bury_d: {self.zfight_bury_d!r} is not a distance in metres (> 0).")
        if not all(isinstance(r, str) for r in self.connect_roots):
            errs.append("connect_roots: expected part names.")
        for slot, row in self.material_slots.items():
            if not (isinstance(row, tuple) and len(row) == 2 and _is_guid(row[0])):
                errs.append(f"material_slots[{slot!r}]: expected (material GUID, vanilla .def path), got {row!r}.")
        if self.damaged_slots:
            from ..defs.material_guids import material_table
            known = material_table(self)
            for slot, dst in self.damaged_slots.items():
                if dst not in known:
                    errs.append(f"damaged_slots[{slot!r}]: {dst!r} is not a known material slot.")
        return errs


def _is_guid(s) -> bool:
    try:
        return isinstance(s, str) and str(uuid.UUID(s)) == s.lower()
    except ValueError:
        return False
