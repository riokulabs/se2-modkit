"""Style profiles: TOML files in `se2modkit/profiles/` with `extends`, numeric limits, design constants, material
roles and the extra checks a profile switches on.

A mod picks one profile (`[tool.se2modkit] profile`) and may override it in `[tool.se2modkit.style]`, but only to
tighten: a `*min*` key may go up, a `*max*` key may go down, a two-number band may narrow, a list of names may grow
(extra checks, decal prefixes) and `roles` may gain new roles. Fixed lists (`FIXED_LISTS`, e.g. the emissive light
exceptions) cannot change. Design constants (chamfers, bolt and lens sizes,
the stage template) and existing roles cannot change. Anything else raises `ProfileError` naming the key.
"""

from __future__ import annotations

import copy
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"
CONTROL_KEYS = frozenset({"warn_only", "waivers"})   # [tool.se2modkit.style] keys that are not profile overrides
# Lists a mod may not change: growing any of them would relax a check (more paint materials, more archetypes with
# the larger hazard allowance, more emissive slots excepted from the budget) or break the stage template.
FIXED_LISTS = frozenset({"paint.colorable_materials", "hazard.high_archetypes", "construction.stages",
                         "emissive.light_materials"})
_MISSING = object()


class ProfileError(Exception):
    """A profile problem, reported to the user as one plain sentence."""


@dataclass(frozen=True)
class Profile:
    name: str
    chain: tuple[str, ...]
    data: dict

    def get(self, key: str, default=_MISSING):
        """The value at a dotted key (`"paint.colorable_min_share"`)."""
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not _MISSING:
                    return default
                raise ProfileError(f"Style profile '{self.chain[0]}' has no key {key}.")
            node = node[part]
        return copy.deepcopy(node)

    def keys(self) -> list[str]:
        """Every dotted leaf key."""
        out: list[str] = []

        def walk(t, prefix):
            for k, v in t.items():
                if isinstance(v, dict):
                    walk(v, f"{prefix}{k}.")
                else:
                    out.append(f"{prefix}{k}")
        walk(self.data, "")
        return sorted(out)


def available(search: Iterable[Path] | None = None) -> list[str]:
    dirs = list(search or ()) + [PROFILE_DIR]
    return sorted({p.stem for d in dirs if Path(d).is_dir() for p in Path(d).glob("*.toml")})


def _read(name: str, dirs: list[Path]) -> dict:
    for d in dirs:
        p = Path(d) / f"{name}.toml"
        if p.is_file():
            try:
                return tomllib.loads(p.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                raise ProfileError(f"Style profile {p} is not valid TOML ({e}).") from None
    raise ProfileError(f"Unknown style profile '{name}'; known profiles: {', '.join(available(dirs[:-1]))}.")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _is_num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _tighten(base: dict, over: dict, prefix: str = "") -> dict:
    """`base` with the mod's `over` applied, refusing anything that relaxes the profile."""
    out = copy.deepcopy(base)
    for k, new in over.items():
        key = f"{prefix}{k}"
        if not prefix and k in CONTROL_KEYS:
            continue
        if k not in out:
            if prefix == "roles." and isinstance(new, str):
                out[k] = new
                continue
            raise ProfileError(f"Style override {key} is not a key of the profile (a mod can only tighten "
                               f"existing limits or add roles).")
        old = out[k]
        if isinstance(old, dict):
            if not isinstance(new, dict):
                raise ProfileError(f"Style override {key} must be a table.")
            out[k] = _tighten(old, new, key + ".")
        elif _is_num(old):
            if not _is_num(new):
                raise ProfileError(f"Style override {key} must be a number.")
            leaf = k.lower()
            if "min" in leaf:
                ok = new >= old
            elif "max" in leaf:
                ok = new <= old
            else:
                ok = new == old
            if not ok:
                what = ("a house constant; a mod cannot change it" if "min" not in leaf and "max" not in leaf
                        else "relaxes the profile")
                raise ProfileError(f"Style override {key} = {new} {what} (profile value {old}).")
            out[k] = new
        elif isinstance(old, list):
            if not isinstance(new, list):
                raise ProfileError(f"Style override {key} must be a list.")
            if old and all(_is_num(x) for x in old) and key not in FIXED_LISTS:
                if len(old) != 2 or len(new) != 2 or not all(_is_num(x) for x in new) or new[0] < old[0] \
                        or new[1] > old[1] or new[0] > new[1]:
                    raise ProfileError(f"Style override {key} = {new} relaxes the profile band {old} "
                                       f"(a band may only narrow).")
                out[k] = list(new)
            elif key in FIXED_LISTS:
                if new != old:
                    raise ProfileError(f"Style override {key} = {new} is not allowed: the list is fixed by the "
                                       f"profile ({old}); waive a single block in style-waivers.toml instead.")
            else:
                out[k] = old + [x for x in new if x not in old]
        elif new != old:
            raise ProfileError(f"Style override {key} = {new!r} changes a fixed profile value ({old!r}).")
    return out


def load(name: str, *, overrides: dict | None = None, search: Iterable[Path] | None = None) -> Profile:
    """The profile `name` with its `extends` chain resolved, then the mod's `overrides` (tighten only).

    `search` adds directories looked up before modkit's own profiles."""
    dirs = [Path(d) for d in (search or ())] + [PROFILE_DIR]
    chain: list[str] = []
    layers: list[dict] = []
    cur: str | None = name
    while cur is not None:
        if cur in chain:
            raise ProfileError(f"Style profile extends cycle: {' -> '.join([*chain, cur])}.")
        chain.append(cur)
        data = _read(cur, dirs)
        layers.append(data)
        nxt = data.get("extends")
        if nxt is not None and not isinstance(nxt, str):
            raise ProfileError(f"Style profile '{cur}': extends must be a profile name.")
        cur = nxt
    merged: dict = {}
    for layer in reversed(layers):
        merged = _merge(merged, {k: v for k, v in layer.items() if k != "extends"})
    if overrides:
        merged = _tighten(merged, overrides)
    return Profile(name=str(merged.get("name", name)), chain=tuple(chain), data=merged)
