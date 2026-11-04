"""The look-dev scene: a block judged in context, not on a void.

Every look picture puts the block where a player meets it: mounted on a section of vanilla armor hull, beside vanilla
neighbors and a scale figure, lit neutrally, seen from a standing player's eye with the game's own field of view.
Sets (`SETS`): `hull` (the armor section and the figure), `neighbors` (the default: `hull` plus the vanilla blocks the
mod lists in `previews.toml` `[lookdev] neighbors`), `interior` (a room of armor with ceiling lights) and `space`
(black sky and one hard sun; the "mood" extra, never the default).

The block sits centered on the origin with its bottom (its -Z mount face) on the hull's top at z = 0
(`anchor_offset`). Eye views (`shots.View.eye`) stand the player on the hull 2 to 6 m from the block's side at
`EYE_HEIGHT_M` (`CROUCH_HEIGHT_M` for the low and mount views) and use `game_lens`; the other views are orthographic
studio views fit to the block's bounds.

`viewpoint`, `game_lens`, the set table and the layout helpers are plain Python; `build_set` needs Blender. Armor
and vanilla models are read at render time from the Mod SDK and the vanilla reference, never copied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

try:   # Blender only
    import bmesh
    import bpy
    from mathutils import Matrix, Vector
except ImportError:   # pragma: no cover - outside Blender
    bmesh = bpy = Matrix = Vector = None

from . import shots
from .compose import PreviewError

__all__ = ["CROUCH_HEIGHT_M", "DEFAULT_SET", "EYE_FILL", "EYE_HEIGHT_M", "GAME_FOV_V", "GRID", "HULL_PLATE", "SETS", "SET_SPECS",
           "LookScene", "SetSpec", "anchor_offset", "build_set", "figure_spot", "game_lens", "hull_cells",
           "mount_rotation", "neighbor_slots", "rotated_half", "set_spec", "viewpoint"]

GAME_FOV_V = 1.22          # rad, vertical: the game's default field of view
EYE_HEIGHT_M = 1.65        # standing eye above the floor; the game camera follows the head bone, so this is measured
CROUCH_HEIGHT_M = 1.0      # crouched eye, for the low and mount views
EYE_FILL = 0.75            # share of the frame a block fills in an eye view (the game's lens is wide)
GRID = {"large": 2.5, "small": 0.5}
HULL_PLATE = "Shared/Plane/ArmorPlate_Plane.fbx"     # under the SDK's Assets/Armors
_LOW = ("3q_low", "mount")


@dataclass(frozen=True)
class SetSpec:
    hull: bool = True
    neighbors: bool = False
    room: bool = False
    mood: bool = False
    lighting: str = "studio"
    world: tuple[float, float, float] = (0.10, 0.105, 0.115)


SETS = ("hull", "neighbors", "interior", "space")
DEFAULT_SET = "neighbors"
SET_SPECS = {
    "hull": SetSpec(),
    "neighbors": SetSpec(neighbors=True),
    "interior": SetSpec(neighbors=True, room=True, lighting="flat", world=(0.02, 0.02, 0.025)),
    "space": SetSpec(mood=True, lighting="dark", world=(0.0, 0.0, 0.0)),
}


def set_spec(name: str) -> SetSpec:
    if name not in SET_SPECS:
        raise PreviewError(f"unknown look-dev set {name!r} (sets: {', '.join(SETS)}).")
    return SET_SPECS[name]


def game_lens(aspect: float = 16 / 9) -> float:
    """Blender lens (mm, 36 mm sensor across the wider side) that matches the game's default vertical field of view."""
    sensor_h = 36.0 / aspect if aspect >= 1 else 36.0
    return sensor_h / 2 / math.tan(GAME_FOV_V / 2)


def _horiz(direction) -> tuple[float, float]:
    x, y = direction[0], direction[1]
    n = math.hypot(x, y)
    return (x / n, y / n) if n > 1e-6 else (0.0, 1.0)


def viewpoint(view: str, half, *, center=(0.0, 0.0, 0.0), floor_z: float = 0.0, aspect: float = 16 / 9,
              fill: float = 0.8, eye_fill: float | None = None):
    """(camera location, target, lens mm, ortho scale or None) for a canonical view of a block with half extents
    `half` centered at `center`."""
    v = shots.VIEWS[shots.canonical(view)]
    c = tuple(float(x) for x in center)
    if not v.eye:
        # Blender's ortho scale spans the frame's wider side; renders here are landscape, so it is the width and
        # the screen-vertical extent is scaled by the aspect.
        vert = 1 if abs(v.direction[2]) > 0.5 else 2
        horiz = [i for i in range(3) if abs(v.direction[i]) < 0.5 and i != vert][0]
        width = max(2 * abs(half[horiz]), 2 * abs(half[vert]) * aspect)
        dist = math.sqrt(sum(h * h for h in half)) * 2 + 1
        loc = tuple(c[i] + v.direction[i] * dist for i in range(3))
        return loc, c, 50.0, width / fill
    lens = game_lens(aspect)
    eye_fill = EYE_FILL if eye_fill is None else eye_fill
    hx, hy = _horiz(v.direction)
    side = abs(half[0] * hx) + abs(half[1] * hy)
    # A block more than twice a player's eye height is seen the way players see big blocks: hovering at its
    # mid-height.
    tall = half[2] > EYE_HEIGHT_M
    if view in _LOW:
        height = EYE_HEIGHT_M if tall else CROUCH_HEIGHT_M
    else:
        height = max(EYE_HEIGHT_M, c[2] - floor_z) if tall else EYE_HEIGHT_M
    if view == "inside":
        loc = (c[0] + hx * side * 0.6, c[1] + hy * side * 0.6, floor_z + height)
        return loc, (c[0] - hx * side, c[1] - hy * side, c[2]), lens, None
    dist = shots.fit_distance(half, (hx, hy, 0.0), lens, aspect, eye_fill)
    if view == "mount":
        dist = side + 2.0
    else:
        dist = max(dist, side + 2.0) if tall else min(max(dist, side + 2.0), side + 6.0)
    return (c[0] + hx * dist, c[1] + hy * dist, floor_z + height), c, lens, None


_MOUNT = {   # rotation (rows) that turns the named face of a block down onto the hull (-Z)
    "-Z": ((1, 0, 0), (0, 1, 0), (0, 0, 1)), "+Z": ((1, 0, 0), (0, -1, 0), (0, 0, -1)),
    "+Y": ((1, 0, 0), (0, 0, 1), (0, -1, 0)), "-Y": ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    "+X": ((0, 0, 1), (0, 1, 0), (-1, 0, 0)), "-X": ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
}


def mount_rotation(face: str):
    """3x3 rotation (rows) that puts the block's `face` (-Z, +Z, -Y, +Y, -X, +X) on the hull. A thruster mounts by
    its back, the face away from its nozzle."""
    if face not in _MOUNT:
        raise PreviewError(f"unknown mount face {face!r} (faces: {', '.join(_MOUNT)}).")
    return _MOUNT[face]


def rotated_half(half, face: str) -> tuple[float, float, float]:
    r = mount_rotation(face)
    return tuple(sum(abs(r[i][j]) * half[j] for j in range(3)) for i in range(3))


def _cells_along(h: float, g: float, margin: int) -> list[float]:
    k = max(1, math.ceil(2 * h / g - 1e-6))
    return [round(i * g - (k - 1) * g / 2, 6) for i in range(-margin, k + margin)]


def hull_cells(half, grid: str = "large", margin: int = 2) -> list[tuple[float, float]]:
    """Centers of the hull plates: the block's footprint plus `margin` free cells on every side."""
    g = GRID[grid]
    return [(x, y) for x in _cells_along(half[0], g, margin) for y in _cells_along(half[1], g, margin)]


def neighbor_slots(half, grid: str = "large", count: int = 4) -> list[tuple[float, float]]:
    """Where vanilla neighbors stand, one free cell out from the block: left, right, behind, then behind-left."""
    g = GRID[grid]
    ox, oy = half[0] + 1.5 * g, half[1] + 1.5 * g
    return [(-ox, 0.0), (ox, 0.0), (0.0, -oy), (-ox, -oy)][:count]


def figure_spot(half, grid: str = "large") -> tuple[float, float]:
    """The scale figure: just off the block's front-right corner, in the 3/4 front view."""
    g = GRID[grid]
    return (half[0] + 0.75 * g, half[1] + 0.75 * g)


def anchor_offset(lo, hi) -> tuple[float, float, float]:
    """Translation that centers a block's bounds on the origin with its bottom on the hull (z = 0)."""
    return (-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])


# ---------------------------------------------------------------- Blender

@dataclass
class LookScene:
    shot: object               # preview.base.Shot
    used_armor: bool
    floor_z: float = 0.0


_PLATE: dict = {}


def _plate(armor_dir: Path, paint: str):
    """The SDK's flat armor side plate, imported once, read only, turned so its thin axis is Z and its top at z = 0,
    in an excluded collection. Returns (collection, size across)."""
    from . import base
    from . import mats as PM
    key = (str(armor_dir), paint)
    if key in _PLATE:
        return _PLATE[key]
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(Path(armor_dir) / HULL_PLATE))
    new = [o for o in bpy.data.objects if o not in before]
    c = bpy.data.collections.new("ld_hull_plate")
    bpy.context.scene.collection.children.link(c)
    for o in new:
        for uc in list(o.users_collection):
            uc.objects.unlink(o)
        c.objects.link(o)
        if o.type == "MESH":   # the SDK plate's detail lives in armor skin textures; stand in with armor grey
            o.data.materials.clear()
            o.data.materials.append(PM.get("Grey"))
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(b) for o in new if o.type == "MESH" for b in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    size = hi - lo
    thin = min(range(3), key=lambda i: size[i])
    turn = {0: Matrix.Rotation(math.radians(90), 4, "Y"), 1: Matrix.Rotation(math.radians(-90), 4, "X"),
            2: Matrix.Identity(4)}[thin]
    centered = turn @ Matrix.Translation(-(lo + hi) / 2)          # centered in plane, thin axis along Z
    top = max((centered @ p)[2] for p in pts)
    fix = Matrix.Translation((0, 0, -top)) @ centered               # then the top face down to z = 0
    for o in new:
        if o.parent is None:
            o.matrix_world = fix @ o.matrix_world
    across = max(size[i] for i in range(3) if i != thin)
    base._exclude(c)
    _PLATE[key] = (c, across)
    return _PLATE[key]


def _box(name, lo, hi, mat):
    bm = bmesh.new()
    vs = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    bmesh.ops.scale(bm, vec=Vector(hi) - Vector(lo), verts=vs)
    bmesh.ops.translate(bm, vec=(Vector(lo) + Vector(hi)) / 2, verts=vs)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    return bpy.data.objects.new(name, me)


def build_set(set_name: str, half, *, name: str, grid: str = "large", neighbors=(), paint: str = "keen",
              armor_dir: Path | None = None, res=(1600, 900)) -> LookScene:
    """A new scene for one look shot: the hull, the figure, the set's neighbors, room or sky. The caller links the
    block (offset by `anchor_offset`) and sets the camera with `viewpoint`."""
    from . import base
    from . import mats as PM
    spec = set_spec(set_name)
    g = GRID[grid]
    cells = hull_cells(half, grid)
    shot = base.Shot(name, res=res, floor=None, world=spec.world, lighting=spec.lighting,
                     light_scale=max(1.0, max(half) / 1.5))
    used_armor = False
    if armor_dir and (Path(armor_dir) / HULL_PLATE).is_file():
        plate, across = _plate(Path(armor_dir), paint)
        s = g / across
        for x, y in cells:
            shot.inst(plate, Matrix.Translation((x, y, 0)) @ Matrix.Scale(s, 4))
        seam = PM.get("Darker")
        xs, ys = sorted({x for x, _ in cells}), sorted({y for _, y in cells})
        x0, x1, y0, y1 = xs[0] - g / 2, xs[-1] + g / 2, ys[0] - g / 2, ys[-1] + g / 2
        w = g * 0.012
        for i, x in enumerate([xs[0] - g / 2] + [x + g / 2 for x in xs]):
            shot.link(_box(f"ld_seam_x{i}", (x - w, y0, 0), (x + w, y1, 0.003), seam))
        for i, y in enumerate([ys[0] - g / 2] + [y + g / 2 for y in ys]):
            shot.link(_box(f"ld_seam_y{i}", (x0, y - w, 0), (x1, y + w, 0.003), seam))
        used_armor = True
    else:
        xs, ys = [x for x, _ in cells], [y for _, y in cells]
        shot.grid(min(xs) - g / 2, max(xs) + g / 2, min(ys) - g / 2, max(ys) + g / 2, step=g)
    shot.inst(base.human(), Matrix.Translation((*figure_spot(half, grid), 0.0)))
    if spec.neighbors:
        for rel, (sx, sy) in zip(neighbors, neighbor_slots(half, grid, len(neighbors))):
            coll, lo, hi = base.vanilla(rel, paint)
            nx, ny = (hi.x - lo.x) / 2, (hi.y - lo.y) / 2
            px = sx + math.copysign(nx, sx) if sx else 0.0
            py = sy + math.copysign(ny, sy) if sy else 0.0
            dx, dy, dz = anchor_offset(lo, hi)
            shot.inst(coll, Matrix.Translation((px + dx, py + dy, dz)))
    if spec.room:
        xs, ys = [x for x, _ in cells], [y for _, y in cells]
        x0, x1, y0, y1 = min(xs) - g / 2, max(xs) + g / 2, min(ys) - g / 2, max(ys) + g / 2
        h = max(2 * half[2] + 1.5, 3.5)
        wall = PM.get("Darker")
        for nm, lo, hi in (("ld_wall_back", (x0, y0 - 0.25, 0), (x1, y0, h)),
                           ("ld_wall_left", (x0 - 0.25, y0, 0), (x0, y1, h)),
                           ("ld_wall_right", (x1, y0, 0), (x1 + 0.25, y1, h)),
                           ("ld_ceiling", (x0, y0, h), (x1, y1, h + 0.25))):
            shot.link(_box(nm, lo, hi, wall))
        for i, x in enumerate((x0 + (x1 - x0) / 3, x0 + 2 * (x1 - x0) / 3)):
            ld = bpy.data.lights.new(f"{name}_panel{i}", "AREA")
            ld.energy, ld.size, ld.color = 260 * max(1.0, max(half) / 1.5) ** 2, 1.2, (0.95, 0.97, 1.0)
            o = bpy.data.objects.new(ld.name, ld)
            o.location = (x, (y0 + y1) / 2, h - 0.05)
            shot.link(o)
    if spec.mood:
        sun = bpy.data.lights.new(f"{name}_sun", "SUN")
        sun.energy, sun.angle = 4.0, math.radians(0.5)
        o = bpy.data.objects.new(sun.name, sun)
        o.rotation_euler = (math.radians(50), 0, math.radians(35))
        shot.link(o)
    return LookScene(shot, used_armor)
