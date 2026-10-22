"""Damage effects (plain Python, no bpy): which blocks spark, the vanilla 4.0 band they copy, and the
ParticleEffects-type dummies the bands bind to.

Which blocks spark is the mod's: `hooks.spark_kind` {family: fn(spec) -> a SPARK_SOURCES key | None} and
`hooks.per_zone` {family: fn(spec) -> bool} (one emitter per zone, for long blocks). The vanilla sources (read from
the vanilla reference at build time, never copied here):
  cable    CableStraight250's Structural BlockEffects Copy: one band 0 - 0.99, ElectricalDamageMedium x1 on the
           ParticleEffects dummy;
  breaker  CircuitBreaker's Structural BlockEffects Copy: the same band with ElectricalDamageSmall x1;
  cp25     ControlPanel25's BlockEffects Copy: its band-0 Update (upper 0.99, ElectricalDamageSmall x0.3).

Where the effects play in game 4.0:
  * a damage band's LOOPED effect spawns once per model dummy of the band's Dummy type, and each instance stops when
    its dummy's SupportingFracture zone is dead (spawned stopped if the zone is already dead);
  * the one-shot death effect and the fracture effect's orientation take the FIRST dummy of the type (the first in
    Dummies order) with NO SupportingFracture check.
So the centre ParticleEffects dummy can be supported by a zone (its sparks stop when that zone falls off) and stay
the death-effect origin; the per-zone dummies follow it in the Dummies list, so they never move the death effect.
"""

from __future__ import annotations

import json
import os

__all__ = ["EffectsError", "MAX_EMITTERS", "PARTICLE_EFFECTS_TYPE", "SPARK_SOURCES", "TIMER_EFFECTS",
           "blender_to_game", "centre_zone", "emitter_zones", "per_zone", "spark_kind", "vanilla_band",
           "vanilla_effects", "zone_dummies"]


class EffectsError(RuntimeError):
    """A damage-effect input is missing; the message is one plain sentence."""


PARTICLE_EFFECTS_TYPE = "e9f28118-a0cf-4c6c-b6ad-2e0719e6cf5a"   # System/DummyTypes/ParticleEffects (the one constant)

SPARK_SOURCES = {
    "cable": "Blocks/CablesSet/Straight/250/CableStraight250_StructuralBlockEffectsComponentDefinition.partialdef",
    "breaker": "Blocks/SmallClutterProps/CircuitBreaker/100/CircuitBreaker_StructuralBlockEffectsComponentDefinition"
               ".partialdef",
    "cp25": "Blocks/ControlPanel/25/ControlPanel25_BlockEffectsComponentDefinition.partialdef",
}
TIMER_EFFECTS = "2cad179b-6b5b-41d9-99ea-e5e44f102485"   # Templates/Blocks/BlockEffects/Timer_BlockEffects... (3 bands)
# At most 3 ParticleEffects-type dummies (spark emitters) per block: vanilla CircuitBreaker has 1; eight zones
# sparking at once read too busy.
MAX_EMITTERS = 3


def spark_kind(hooks, spec):
    """The vanilla spark band a block copies (a SPARK_SOURCES key) or None: its family's `hooks.spark_kind` entry
    decides; a family without one does not spark."""
    fn = hooks.spark_kind.get(spec.family)
    return fn(spec) if fn else None


def per_zone(hooks, spec):
    """Whether a spark block gets one emitter per zone (its family's `hooks.per_zone`), else only the centre one."""
    fn = hooks.per_zone.get(spec.family)
    return bool(fn(spec)) if fn else False


def _vanilla_root(vanilla):
    if vanilla is None:
        from ..model import config
        vanilla = config.VANILLA_REF
    if not vanilla:
        raise EffectsError("The spark bands are read from the vanilla reference: set `vanilla_ref` in the user "
                           "config (the Mod SDK's vanilla Assets folder) or pass vanilla=.")
    return vanilla


def vanilla_effects(kind, vanilla=None):
    """The OnDamagedBlockParticleEffect delta of SPARK_SOURCES[kind] (vanilla 4.0, read-only). vanilla: the vanilla
    reference root (default: the `vanilla_ref` config key, model.config.VANILLA_REF)."""
    path = os.path.join(_vanilla_root(vanilla), SPARK_SOURCES[kind])
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)["$Value"]["Manipulator"]["OnDamagedBlockParticleEffect"]


def vanilla_band(kind, vanilla=None):
    """The band Value of that vanilla file's OnDamagedBlockParticleEffect (its one change)."""
    ch = vanilla_effects(kind, vanilla)["Changed"]
    if len(ch) != 1:
        raise EffectsError(f"{SPARK_SOURCES[kind]}: expected one OnDamagedBlockParticleEffect change, found {len(ch)}.")
    return ch[0]["Value"]


def blender_to_game(p, shift):
    """Blender block frame -> the game model frame: (x, z, -y) + shift."""
    return (round(p[0] + shift[0], 6), round(p[2] + shift[1], 6), round(-p[1] + shift[2], 6))


def _centre(z):
    return tuple((a + b) / 2 for a, b in zip(z["lo"], z["hi"]))


def _holds(z, p, eps=1e-6):
    return all(lo - eps <= c <= hi + eps for c, lo, hi in zip(p, z["lo"], z["hi"]))


def _lead(zones):
    """The zone of a group that carries its emitter: the largest health share (ties: the earlier id)."""
    return max(zones, key=lambda z: (z.get("health_share", 0), [-ord(c) for c in z["id"]]))


def emitter_zones(zones, centre):
    """The zones (other than the centre one) that get their own spark emitter. Up to MAX_EMITTERS zones: all of them.
    More: the zones are grouped along the block's longest axis (union AABB; ties: x, y, z) into the ones wholly below
    the centre zone's AABB, the ones wholly above it and the rest (the centre group, served by the centre dummy);
    each outer group's emitter goes to its lead zone (_lead)."""
    others = [z for z in zones if z["id"] != centre]
    if len(zones) <= MAX_EMITTERS:
        return others
    ext = [max(z["hi"][i] for z in zones) - min(z["lo"][i] for z in zones) for i in range(3)]
    a = max(range(3), key=lambda i: (ext[i], -i))
    c = next(z for z in zones if z["id"] == centre)
    below = [z for z in others if z["hi"][a] <= c["lo"][a] + 1e-6]
    above = [z for z in others if z["lo"][a] >= c["hi"][a] - 1e-6]
    return [_lead(g) for g in (below, above) if g]


def centre_zone(zones):
    """The zone whose hull AABB holds the block centre (Blender origin); of several (a seam through the centre) or
    none, the largest health share (ties: the earlier id)."""
    return _lead([z for z in zones if _holds(z, (0.0, 0.0, 0.0))] or list(zones))["id"]


def zone_dummies(fractured, shift, per_zone, prefer_role=None):
    """[(name, game position, supporting zone id)] of a spark block's ParticleEffects-type dummies: first the centre
    one ("ParticleEffects", at the block centre = shift, supported by centre_zone), then with per_zone one
    "ParticleEffects_<zone>" at the centre of the hull AABB of each emitter_zones zone (at most MAX_EMITTERS in all).
    prefer_role (e.g. "hide" for a block whose cables are its hide role): a per-zone dummy goes to the centre of the
    zone's box of that role (fractured["role_bounds"][zone][role], Blender [x0, y0, z0, x1, y1, z1]) when the zone has
    one, so it sparks there; else to its hull AABB's centre. The centre dummy never moves."""
    zones = fractured["zones"]
    rb = fractured.get("role_bounds") or {}
    c = centre_zone(zones)
    out = [("ParticleEffects", tuple(shift), c)]

    def at(z):
        box = rb.get(z["id"], {}).get(prefer_role) if prefer_role else None
        return _centre({"lo": box[:3], "hi": box[3:]}) if box else _centre(z)

    if per_zone:
        out += [(f"ParticleEffects_{z['id']}", blender_to_game(at(z), shift), z["id"])
                for z in emitter_zones(zones, c)]
    return out
