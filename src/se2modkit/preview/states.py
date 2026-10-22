"""Build-state and damage-state renders of a mod's blocks AS EXPORTED, each beside its vanilla analogue (Blender).

Inputs are the export step's files (`hooks.paths.models / <block> /` NonFractured, Fractured and Damaged LOD0 FBX and
export.json), the installed BlockModelDefinition stages and dummies (or a `stages_dir` holding <block>.json and
<block>.bmd.json) and, for the analogues, the read-only vanilla reference (`vanilla_ref`). The state logic is
`pipeline.states` (plain Python); this module only draws it.

Output of `render_set`: `<out>/<block>/<shot>_<view>.png` (views front / rear: 3/4 from the block's front and from
behind), `<block>/shots.json`, `<block>/info.json` (the FR LOD0 triangle shares for the sheet) and `manifest.json`
= {block: [shot, ...]}; the analogues go to `V_<name>/` and `analogues.json`. `preview.sheets.states_sheet` lays a set
out (with a before set beside it).

Shots (pipeline.states.ALL_SHOTS): c00 .. c77 the Fractured LOD0 with the cumulative stage changes (hidden parts
removed, SetMaterial in the construction material by GUID); done the NonFractured LOD0; done_damaged / one_hit /
battered / broken the Fractured LOD0 with the damaged-only changes and the zone states of `damage_scenario` (damaged
zone: its Damaged-model parts in the DM's slot materials; dead zone: hidden, so the neighbours' FracturedMaterial01
caps show). Stand-ins (`_Subpart` slots) show in every shot but `done`: any health loss ends Functional, so the game
despawns the real subparts and shows the stand-ins. A block without fracture zones but with a Damaged model also
gets the `debris` shot (its whole Damaged model). The camera is fitted to the NonFractured model, so every shot of a
block is framed alike. `markers=True` puts an orange octahedron, pinned up through the top, at every staged
ParticleEffects dummy (none for a dummy whose zone is dead).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:   # Blender only
    import bmesh
    import bpy
    from mathutils import Matrix, Vector
except ImportError:   # pragma: no cover - outside Blender
    bmesh = bpy = Matrix = Vector = None

from ..defs.material_guids import damaged_table
from ..pipeline import states as SV
from . import mats as PM

__all__ = ["FRONT", "REAR", "Part", "Subject", "bounds", "compose", "load", "new_scene", "ours", "render_set",
           "render_subject", "shoot", "split", "vanilla_subject", "write_manifests"]

RES = (640, 480)
SAMPLES = 24
FRONT, REAR = (-0.72, 0.62, 0.42), (0.72, -0.62, 0.42)
HIGH_VIEWS = {"front": (-0.55, 0.50, 0.80), "rear": (0.55, -0.50, 0.80)}   # flat blocks (floors, roofs)
MARK_COL = (1.0, 0.42, 0.02)
BROKEN_TEX = {"EmissiveOffBroken": ("EmissiveOffBroken", "EmissiveOffBroken_albedo.png"),
              "Display01Broken": ("Display01Broken", "Display01Broken_albedo.png")}


# ---------------------------------------------------------------------------------------------------- helpers
def _tex_mat(name, png, rough=0.8, metal=0.1):
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    if png and os.path.exists(png):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = bpy.data.images.load(png, check_existing=True)
        tc = nt.nodes.new("ShaderNodeTexCoord")
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (2.5, 2.5, 2.5)   # vanilla's cut density is ~0.4 UV/m: show the grain
        nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
        nt.links.new(t.outputs["Color"], b.inputs["Base Color"])
    else:
        print("NO TEXTURE", png)
        b.inputs["Base Color"].default_value = (0.2, 0.15, 0.12, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    return m


def frac_mat():
    d = PM.sdk_mats()
    return _tex_mat("pvs_frac", os.path.join(d, "FracturedMaterial01", "FracturedMaterial01_albedo.png") if d else "")


def broken_mat(target):
    if target in BROKEN_TEX and PM.sdk_mats():
        d, f = BROKEN_TEX[target]
        return _tex_mat("pvs_" + target, os.path.join(PM.sdk_mats(), d, f), 0.6, 0.2)
    return PM.get("EmOff")


def slot(m):
    return (m.name if m else "").split(".")[0]


def load(path):
    """Import an FBX; its objects unlinked from every collection. Returns the mesh objects."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    for o in new:
        for c in list(o.users_collection):
            c.objects.unlink(o)
    return [o for o in new if o.type == "MESH"]


def bounds(objs, m=None):
    pts = [(m or Matrix()) @ o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    return (Vector([min(p[i] for p in pts) for i in range(3)]), Vector([max(p[i] for p in pts) for i in range(3)]))


def new_scene(tag, res=RES, samples=SAMPLES):
    sc = bpy.data.scenes.new("pvs_" + tag)
    r = sc.render
    r.engine = "BLENDER_EEVEE"
    r.resolution_x, r.resolution_y = res
    r.film_transparent = False
    for k, v in (("taa_render_samples", samples), ("use_raytracing", True), ("use_shadows", True)):
        try:
            setattr(sc.eevee, k, v)
        except Exception:
            pass
    sc.view_settings.view_transform = "AgX"
    try:
        sc.view_settings.look = "AgX - Base Contrast"
    except Exception:
        pass
    w = bpy.data.worlds.new("pvs_w_" + tag)
    w.use_nodes = True
    w.node_tree.nodes["Background"].inputs[0].default_value = (0.36, 0.37, 0.39, 1)
    w.node_tree.nodes["Background"].inputs[1].default_value = 0.55
    sc.world = w
    return sc


def shoot(sc, objs, direction, path, lens=60, fill=0.80):
    """A camera along `direction` fitted so the objects' bounds fill `fill` of the frame, a three-light rig scaled to
    them, one render to `path`."""
    from bpy_extras.object_utils import world_to_camera_view
    lo, hi = bounds(objs)
    tgt = (lo + hi) / 2
    size = (hi - lo).length
    d = Vector(direction).normalized()
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.lens = lens
    cam.data.clip_start, cam.data.clip_end = 0.01, 100
    sc.collection.objects.link(cam)
    sc.camera = cam
    dist = size * 1.5
    for _ in range(6):
        cam.location = tgt + d * dist
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        sc.view_layers[0].update()
        cs = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
        p = [world_to_camera_view(sc, cam, c) for c in cs]
        span = max(max(q.x for q in p) - min(q.x for q in p), max(q.y for q in p) - min(q.y for q in p))
        dist *= span / fill
    right = d.cross(Vector((0, 0, 1))).normalized()
    for nm, off, e, col in (("key", -1.0 * right + 0.5 * d + Vector((0, 0, 1.0)), 260, (1.0, 0.97, 0.93)),
                            ("fill", 1.2 * right + 0.9 * d + Vector((0, 0, 0.2)), 70, (0.9, 0.94, 1.0)),
                            ("rim", -1.2 * d + 0.3 * right + Vector((0, 0, 0.8)), 200, (0.85, 0.9, 1.0))):
        ld = bpy.data.lights.new(nm, "AREA")
        ld.energy, ld.size, ld.color = e * size * size, 1.6 * size, col
        lo_ = bpy.data.objects.new(nm, ld)
        lo_.location = tgt + off * 1.8 * size
        lo_.rotation_euler = (tgt - lo_.location).to_track_quat("-Z", "Y").to_euler()
        sc.collection.objects.link(lo_)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sc.render.filepath = str(path)
    bpy.ops.render.render(write_still=True, scene=sc.name)
    print("WROTE", path)
    return path


# ---------------------------------------------------------------------------------------------------- parts
class Part:
    """One single-material piece of an imported model: its mesh part name, zone, slot material name."""

    def __init__(self, ob, group, slot_name, name=""):
        self.ob, self.group, self.slot = ob, group, slot_name
        # (some vanilla Deformed objects carry no Group property; their name still says the zone)
        self.zone = SV.zone_of(group) or SV.zone_of(name)
        self.part = SV.mesh_part(group, slot_name)
        self.standin = slot_name.lower().endswith("_subpart")


def split(objs):
    """Every imported mesh object split per material slot (a mesh part is <Group>-<material>: one slot each)."""
    out = []
    for o in objs:
        group = o.get("Group") or o.get("group") or None
        used = sorted({p.material_index for p in o.data.polygons})
        for i in used:
            if i >= len(o.material_slots) or not o.material_slots[i].material:
                continue
            s = slot(o.material_slots[i].material)
            bm = bmesh.new()
            bm.from_mesh(o.data)
            bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index != i], context="FACES")
            for f in bm.faces:
                f.material_index = 0
            me = bpy.data.meshes.new(f"{o.name}_{s}")
            bm.to_mesh(me)
            bm.free()
            me.materials.append(o.material_slots[i].material)
            ob = bpy.data.objects.new(me.name, me)
            ob.matrix_world = o.matrix_world.copy()
            out.append(Part(ob, group, s, o.name))
        me = o.data
        bpy.data.objects.remove(o)
        if not me.users:
            bpy.data.meshes.remove(me)
    return out


def put(sc, part, mat):
    c = part.ob.copy()
    c.material_slots[0].link = "OBJECT"
    c.material_slots[0].material = mat
    sc.collection.objects.link(c)
    return c


def real(hooks, part, damaged=False, dm_slots=None, paint="keen"):
    """The game-material stand-in of a part (damaged: the Damaged model's slot material by GUID, else the slot swap
    vanilla Deformed models make, `defs.material_guids.damaged_table`)."""
    n = part.slot
    if n.startswith("FracturedMaterial"):
        return frac_mat()
    if damaged:
        g = (dm_slots or {}).get(n)
        if g and g in PM.BY_GUID:
            return PM.get(g)
        table = damaged_table(hooks)
        if n in table:
            t = table[n]
            return PM.get(PM.keen_key(t)) if t.startswith("WindowGlassBroken") else broken_mat(t)
    return PM.get(PM.keen_key(n, paint))


# ---------------------------------------------------------------------------------------------------- subjects
class Subject:
    """Everything a block's shots need: parts of NF / FR / DM LOD0, stages, zones, dummies, model translation."""

    def __init__(self, key, family, nf, fr, dm, stages, fractured, dummies, shift, dm_slots, info=None):
        self.key, self.family = key, family
        self.nf = split(load(nf))
        self.fr = split(load(fr)) if fr else []
        self.dm = split(load(dm)) if dm else []
        self.stages, self.fractured, self.dummies, self.shift, self.dm_slots = stages, fractured, dummies, shift, dm_slots
        self.info = info or {}

    def free(self):
        for p in self.nf + self.fr + self.dm:
            me = p.ob.data
            bpy.data.objects.remove(p.ob)
            bpy.data.meshes.remove(me)


def ours(hooks, name, family, src=None, stages_dir=None):
    """A mod block from its exported models (`src`: a copy of the models folder, `pipeline.states.capture_src`)."""
    models = src or hooks.paths.models
    man = SV.manifest(hooks, name, models)
    stages, bmd = None, None
    if stages_dir:
        sd = Path(stages_dir)
        stages = json.loads((sd / f"{name}.json").read_text(encoding="utf-8"))
        p = sd / f"{name}.bmd.json"
        bmd = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    stages = stages if stages is not None else SV.installed_stages(hooks, name)
    dummies = SV.dummies_of(bmd) if bmd else SV.installed_dummies(hooks, name)
    frac = man.get("fractured") or {}
    nf = SV.model_fbx(hooks, name, "nf", models)
    if nf is None:
        raise FileNotFoundError(f"{name} has no exported NonFractured model under {models}: export it first "
                                "(se2mod blender run --windowed <build script> -- <block> --no-render --export).")
    return Subject(name, family, nf, SV.model_fbx(hooks, name, "fr", models), SV.model_fbx(hooks, name, "dm", models),
                   stages, frac, dummies, tuple(man.get("shift") or (0, 0, 0)), SV.dm_slot_map(hooks, name, models),
                   {"fr_lod0_tris": (frac.get("lods") or [{}])[0].get("tris"),
                    "role_tris": dict(frac.get("role_tris") or {},
                                      scaffold=((frac.get("scaffold") or {}).get("tris") or {}).get("0", 0))})


def vanilla_subject(name, family):
    """A vanilla analogue block from the read-only reference."""
    m = SV.analogue_models(name)
    fr = m["fr"] or m["nf"]
    return Subject("V_" + name, family, m["nf"][0], fr[0], (m["dm"] or (None,))[0], SV.analogue_stages(name),
                   SV.analogue_fractured(name), SV.dummies_of(SV.analogue_bmd(name)), fr[1],
                   SV.analogue_dm_slots(name))


# ---------------------------------------------------------------------------------------------------- shots
def to_blender(subj, pos):
    """Keen model frame (Y up, translation = the .model's) -> the imported FBX's Blender frame (Z up)."""
    x, y, z = (pos["X"] - subj.shift[0], pos["Y"] - subj.shift[1], pos["Z"] - subj.shift[2])
    return Vector((x, -z, y))


def marker_mat():
    m = bpy.data.materials.get("pv_marker")
    return m or PM.simple("pv_marker", MARK_COL, 0.4, 0.0, emit=MARK_COL, estr=4.0)


def markers(sc, subj, zst, lo, hi):
    size = max(0.012, 0.035 * max(hi - lo))
    for d in SV.effect_dummies(subj.dummies):
        z = (d.get("SupportingFracture") or {}).get("Id")
        if z and zst.get(z) == "dead":
            continue
        p = to_blender(subj, d["Transform"]["Transform"]["Position"])
        top = Vector((p.x, p.y, hi.z + 2.5 * size))
        bm = bmesh.new()
        for c, s in ((p, size), (top, size * 0.8)):
            vs = [bm.verts.new(c + Vector(v) * s) for v in
                  ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
            for a in (0, 1):
                for b in (2, 3):
                    for e in (4, 5):
                        bm.faces.new((vs[a], vs[b], vs[e]))
        r = size * 0.12
        q = [bm.verts.new(c + Vector((dx, dy, 0))) for c in (p, top) for dx, dy in ((r, 0), (0, r), (-r, 0), (0, -r))]
        for i in range(4):
            bm.faces.new((q[i], q[(i + 1) % 4], q[4 + (i + 1) % 4], q[4 + i]))
        me = bpy.data.meshes.new("marker")
        bm.to_mesh(me)
        bm.free()
        me.materials.append(marker_mat())
        sc.collection.objects.link(bpy.data.objects.new("marker", me))


def has_debris(subj):
    """An unfractured block with a Damaged model: its dented debris gets the extra DEBRIS_SHOT panel."""
    return bool(subj.dm) and not (subj.fractured or {}).get("zones") and not subj.key.startswith("V_")


def compose(hooks, sc, subj, shot, with_markers=False, *, ratio=None, zones=None, paint="keen"):
    """Link the parts a shot shows into scene sc. `shot` is a pipeline.states shot name; `ratio` (0..1) renders any
    build ratio instead of a named construction shot; `zones` ({zone id: intact | damaged | dead}) any damage
    scenario instead of the named one."""
    if shot == SV.DEBRIS_SHOT:
        for p in subj.dm:
            put(sc, p, real(hooks, p, damaged=True, dm_slots=subj.dm_slots, paint=paint))
        return
    if shot == "done" and ratio is None and zones is None:
        for p in subj.nf:
            put(sc, p, real(hooks, p, paint=paint))
        return
    if ratio is not None or (shot in SV.SHOTS and zones is None):
        st = SV.part_state(subj.stages, SV.SHOTS[shot] if ratio is None else ratio)
        for p in subj.fr:
            shown, mat = st.get(p.part, (True, None))
            if shown:
                put(sc, p, PM.get(PM.key_for_guid(mat)) if mat else real(hooks, p, paint=paint))
        return
    zst = zones if zones is not None else (SV.damage_scenario(subj.fractured, shot) if shot != "done_damaged" else {})
    st = SV.part_state(subj.stages, 1.0, damaged=True)
    for p in subj.fr:       # (stand-ins included: a damaged block is never Functional)
        if zst.get(p.zone, "intact") != "intact":
            continue
        shown, mat = st.get(p.part, (True, None))
        if shown:
            put(sc, p, PM.get(PM.key_for_guid(mat)) if mat else real(hooks, p, paint=paint))
    for p in subj.dm:
        if zst.get(p.zone) != "damaged":
            continue
        if st.get(p.part, (True, None))[0]:
            put(sc, p, real(hooks, p, damaged=True, dm_slots=subj.dm_slots, paint=paint))
    if with_markers:
        lo, hi = bounds([p.ob for p in subj.nf])
        markers(sc, subj, zst, lo, hi)


def clear_scene(sc):
    for o in list(sc.collection.objects):
        bpy.data.objects.remove(o)
    bpy.data.scenes.remove(sc)


def render_subject(hooks, subj, out, with_markers=False, *, high=False, samples=SAMPLES, res=RES):
    """Every shot x view of one subject into `<out>/<key>/`, plus its shots.json and info.json."""
    out = Path(out)
    frame = [p.ob for p in subj.nf]
    views = HIGH_VIEWS if high else {"front": FRONT, "rear": REAR}
    done = []
    for shot in SV.ALL_SHOTS + ((SV.DEBRIS_SHOT,) if has_debris(subj) else ()):
        for view, direction in views.items():
            sc = new_scene(f"{subj.key}_{shot}_{view}", res, samples)
            compose(hooks, sc, subj, shot, with_markers)
            shoot(sc, frame, direction, out / subj.key / f"{shot}_{view}.png")
            clear_scene(sc)
        done.append(shot)
    (out / subj.key).mkdir(parents=True, exist_ok=True)
    (out / subj.key / "shots.json").write_text(json.dumps(done), encoding="utf-8")
    (out / subj.key / "info.json").write_text(json.dumps(subj.info), encoding="utf-8")
    subj.free()
    for kind in (bpy.data.cameras, bpy.data.lights, bpy.data.worlds):
        for d in [d for d in kind if not d.users]:
            kind.remove(d)


def write_manifests(out):
    """manifest.json (our blocks) and analogues.json (V_*) from every <block>/shots.json in the set."""
    out = Path(out)
    man, ana = {}, {}
    for p in sorted(out.glob("*/shots.json")):
        k = p.parent.name
        (ana if k.startswith("V_") else man)[k] = json.loads(p.read_text(encoding="utf-8"))
    for fn, d in (("manifest.json", man), ("analogues.json", ana)):
        (out / fn).write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")


def render_set(hooks, families, out, names=None, *, src=None, stages_dir=None, markers=False, analogues=True,
               analogues_only=False, high_families=()):
    """The states set of `names` (default: every block of `families` = {block name: family}) into `out`."""
    names = list(names or families)
    fams = []
    for n in names:
        for a in SV.analogues(hooks, families[n]):
            if a not in [x for x, _ in fams]:
                fams.append((a, families[n]))
    if not analogues_only:
        for n in names:
            render_subject(hooks, ours(hooks, n, families[n], src, stages_dir), out, markers,
                           high=families[n] in high_families)
    if analogues or analogues_only:
        for a, f in fams:
            render_subject(hooks, vanilla_subject(a, f), out, markers, high=f in high_families)
    write_manifests(out)
    return Path(out)
