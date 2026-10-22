"""Preview-only material stand-ins by Keen slot name (Blender), for our blocks and for vanilla FBX alike.

The SAME stand-ins are applied to our blocks and to vanilla FBX imported read only, so a side-by-side render compares
shapes and material roles, not shaders. PaintedMetalColorable takes the player paint; decal atlases use the game's
albedo with the paint applied through the colour mask. Game DDS are converted to PNG once into the private texture
cache (`preview.texcache`: per user, mode 0o700, temp files by mkstemp, renamed into place). Nothing here is exported.

Texture sources are discovered (`configure` overrides them): the game's GameData/Vanilla/Content/BlockMaterials and
the Mod SDK's GameData/Vanilla/Assets/BlockMaterials, both read only. Without them every material falls back to a
plain stand-in colour.

The tables are plain dicts a mod may extend at import time: PAINTS (paint name -> linear RGB), SIMPLE / EMIS / GHOSTS
(stand-in key -> look), KEEN_MAP (Keen slot name -> stand-in key), BY_GUID (material GUID -> how to draw it).
"""
import glob
import json
import os

try:   # Blender only
    import bpy
except ImportError:   # pragma: no cover - outside Blender
    bpy = None

from . import texcache

_PATHS = {"game_mats": None, "sdk_mats": None, "texcache": None, "legacy_prefix": None, "discovered": False}


def configure(game_mats=None, sdk_mats=None, texcache_dir=None, legacy_prefix=None):
    """Override the texture sources (the game's and the SDK's BlockMaterials folders) and the cache folder;
    `legacy_prefix` (the mod's prefix) lets its old `<PREFIX>TEXCACHE` variable name the cache for one release."""
    if legacy_prefix is not None:
        _PATHS["legacy_prefix"] = legacy_prefix
    for k, v in (("game_mats", game_mats), ("sdk_mats", sdk_mats), ("texcache", texcache_dir)):
        if v is not None:
            _PATHS[k] = str(v)


def _discover():
    if _PATHS["discovered"]:
        return
    _PATHS["discovered"] = True
    from ..env.core import discover_env
    try:
        env = discover_env()
    except Exception as e:   # a broken config must not stop a preview: plain stand-ins instead
        print("preview materials: no environment (" + str(e).splitlines()[0] + "); plain stand-ins")
        return
    if _PATHS["game_mats"] is None and env.game_dir is not None:
        _PATHS["game_mats"] = str(env.game_dir / "GameData" / "Vanilla" / "Content" / "BlockMaterials")
    if _PATHS["sdk_mats"] is None and env.sdk_assets is not None:
        _PATHS["sdk_mats"] = str(env.sdk_assets / "BlockMaterials")


def game_mats():
    """The game's BlockMaterials folder (DDS), or "" when the game is not found."""
    _discover()
    return _PATHS["game_mats"] or ""


def sdk_mats():
    """The Mod SDK's BlockMaterials folder (PNG), or "" when the SDK is not found."""
    _discover()
    return _PATHS["sdk_mats"] or ""


def _mkstemp(cache, suffix):
    fd, path = texcache.mkstemp(cache, suffix)
    return fd, str(path)


def _cache():
    """The checked private texture cache folder (str)."""
    return str(texcache.ensure(_PATHS["texcache"]) if _PATHS["texcache"] else texcache.cache_dir(legacy_prefix=_PATHS["legacy_prefix"]))

# player paints (linear RGB); "keen" = the default grey vanilla previews show
PAINTS = {"keen": (0.40, 0.41, 0.43), "black": (0.022, 0.023, 0.026), "blue": (0.035, 0.12, 0.42),
          "orange": (0.75, 0.22, 0.03), "white": (0.75, 0.76, 0.78), "teal": (0.02, 0.30, 0.28)}
PAINTS["red"] = (0.50, 0.035, 0.03)
PAINT_REF = 0.40
_CACHE = {}
_IMG = {}


def _node(nt, kind, **kw):
    n = nt.nodes.new(kind)
    for k, v in kw.items():
        setattr(n, k, v)
    return n


def simple(name, col, rough=0.5, metal=0.0, emit=None, estr=0.0, noise=0.0, alpha=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*col, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = estr
    if alpha is not None:
        b.inputs["Alpha"].default_value = alpha
        for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            try:
                setattr(m, attr, val)
            except (AttributeError, TypeError):
                pass
    if noise:   # subtle roughness breakup so large painted plates don't read as CG plastic
        tc = _node(nt, "ShaderNodeTexCoord")
        nz = _node(nt, "ShaderNodeTexNoise")
        nz.inputs["Scale"].default_value = 3.0
        nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
        mr = _node(nt, "ShaderNodeMapRange")
        mr.inputs["To Min"].default_value = rough - noise
        mr.inputs["To Max"].default_value = rough + noise
        nt.links.new(nz.outputs["Fac"], mr.inputs["Value"])
        nt.links.new(mr.outputs["Result"], b.inputs["Roughness"])
    return m


def _find_dds(base, suffix):
    if not game_mats():
        return None
    hits = [p for p in glob.glob(os.path.join(glob.escape(game_mats()), "**", base + suffix + ".dds"), recursive=True)
            if "Construction" not in p]
    return min(hits, key=len) if hits else None


def _dds_png(path):
    """Game DDS (BC7) -> PNG in the cache; BC7_UNORM (98) is relabelled 99 in a COPY so Blender loads it. The copy and
    the PNG are written under mkstemp names in the checked private cache, the PNG renamed into place."""
    cache = _cache()
    out = os.path.join(cache, os.path.basename(path)[:-4] + ".png")
    if os.path.exists(out):
        return out
    if os.path.exists(out + ".failed"):
        return None
    with open(path, "rb") as f:
        d = bytearray(f.read())
    if d[84:88] == b"DX10" and d[128] == 98:
        d[128] = 99
    fd, tmp = _mkstemp(cache, ".dds")
    with os.fdopen(fd, "wb") as f:
        f.write(d)
    fp, tmp_png = _mkstemp(cache, ".png")
    os.close(fp)
    im = bpy.data.images.load(tmp)
    _ = im.size[:]
    im.filepath_raw = tmp_png
    im.file_format = "PNG"
    try:
        im.save()
        os.replace(tmp_png, out)
    except RuntimeError:
        with open(out + ".failed", "w"):
            pass
        os.remove(tmp_png)
        out = None
    bpy.data.images.remove(im)
    os.remove(tmp)
    return out


def _img(path, noncolor=False):
    if path not in _IMG or _IMG[path].name not in bpy.data.images:
        im = bpy.data.images.load(path)
        if noncolor:
            im.colorspace_settings.name = "Non-Color"
        _IMG[path] = im
    return _IMG[path]


def atlas_mat(name, base, paint):
    cm, add = _find_dds(base, "_cm"), _find_dds(base, "_add")
    if not add and base.startswith("AtlasFont_BarlowCondensed"):
        add = _find_dds("AtlasFont_BarlowCondensed_CleanWhite", "_add")
    cm_png = _dds_png(cm) if cm else None
    add_png = _dds_png(add) if add else None
    if not add_png:
        print("NO TEXTURE for", base)
        return simple(name, (0.04, 0.04, 0.045), 0.6)
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    if cm_png:
        t1 = _node(nt, "ShaderNodeTexImage", interpolation="Cubic")
        t1.image = _img(cm_png)
    else:
        t1 = _node(nt, "ShaderNodeRGB")
        t1.outputs[0].default_value = (0.75, 0.75, 0.75, 1)
    t2 = _node(nt, "ShaderNodeTexImage")
    t2.image = _img(add_png, noncolor=True)
    sep = _node(nt, "ShaderNodeSeparateColor")
    nt.links.new(t2.outputs["Color"], sep.inputs[0])
    pc = PAINTS[paint]
    mul = _node(nt, "ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    nt.links.new(sep.outputs[2], mul.inputs[0])
    nt.links.new(t1.outputs["Color"], mul.inputs[6])
    mul.inputs[7].default_value = (min(pc[0] / PAINT_REF, 4), min(pc[1] / PAINT_REF, 4), min(pc[2] / PAINT_REF, 4), 1)
    ao = _node(nt, "ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    ao.inputs[0].default_value = 0.8
    nt.links.new(mul.outputs[2], ao.inputs[6])
    nt.links.new(sep.outputs[0], ao.inputs[7])
    nt.links.new(ao.outputs[2], b.inputs["Base Color"])
    if cm_png:
        nt.links.new(t1.outputs["Alpha"], b.inputs["Metallic"])
    b.inputs["Roughness"].default_value = 0.5
    nt.links.new(t2.outputs["Alpha"], b.inputs["Alpha"])
    for attr, val in (("surface_render_method", "DITHERED"), ("blend_method", "CLIP")):
        try:
            setattr(m, attr, val)
        except (AttributeError, TypeError):
            pass
    return m


def perf_mat(name):
    """Vanilla MetalPerforationSteel: its colour (_cm) and alpha mask (_add alpha), alpha-clipped, two-sided."""
    d = os.path.join(game_mats(), "Constructions", "MetalPerforationSteel")
    cm, add = (_dds_png(p) if os.path.exists(p) else None
               for p in (os.path.join(d, f"MetalPerforationSteel{s}.dds") for s in ("_cm", "_add")))
    if not (cm and add):
        print("NO TEXTURE for MetalPerforationSteel")
        return simple(name, (0.3, 0.3, 0.32), 0.45, 0.8)
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    t1 = _node(nt, "ShaderNodeTexImage")
    t1.image = _img(cm)
    t2 = _node(nt, "ShaderNodeTexImage")
    t2.image = _img(add, noncolor=True)
    nt.links.new(t1.outputs["Color"], b.inputs["Base Color"])
    nt.links.new(t2.outputs["Alpha"], b.inputs["Alpha"])
    b.inputs["Metallic"].default_value = 0.85
    b.inputs["Roughness"].default_value = 0.45
    for attr, val in (("surface_render_method", "DITHERED"), ("blend_method", "CLIP"), ("use_backface_culling", False)):
        try:
            setattr(m, attr, val)
        except (AttributeError, TypeError):
            pass
    return m


def fracture_mat(name):
    """Vanilla FracturedMaterial01 (the torn fracture surface): its SDK albedo PNG (read only), box-projected in
    object space at 2 m per tile (the exported UVs' world scale, se2model UV_SCALE 0.5 / m); a rusty dark stand-in
    when the SDK is missing."""
    png = os.path.join(sdk_mats(), "FracturedMaterial01", "FracturedMaterial01_albedo.png")
    if not os.path.exists(png):
        return simple(name, (0.35, 0.30, 0.27), 0.8, 0.7)
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    tc = _node(nt, "ShaderNodeTexCoord")
    mp = _node(nt, "ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (0.5, 0.5, 0.5)
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    t = _node(nt, "ShaderNodeTexImage", projection="BOX", projection_blend=0.2)
    t.image = _img(png)
    nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
    nt.links.new(t.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Metallic"].default_value = 0.7
    b.inputs["Roughness"].default_value = 0.75
    return m


SIMPLE = {
    "Section": ((0.70, 0.08, 0.06), 0.6, 0.0, 0.0),
    "Darker": ((0.028, 0.03, 0.033), 0.6, 0.3, 0.05), "Grey": ((0.12, 0.125, 0.13), 0.55, 0.4, 0.05),
    "Yellow": ((0.80, 0.50, 0.03), 0.45, 0.2, 0.04), "Brushed": ((0.50, 0.51, 0.52), 0.35, 1.0, 0.05),
    "Chrome": ((0.78, 0.78, 0.8), 0.14, 1.0, 0.0), "MetalDull": ((0.16, 0.16, 0.17), 0.45, 1.0, 0.0),
    "Rubber": ((0.016, 0.016, 0.017), 0.85, 0.0, 0.0), "Plastic2": ((0.05, 0.05, 0.055), 0.6, 0.0, 0.0),
    "Copper": ((0.8, 0.45, 0.25), 0.3, 1.0, 0.0), "Floor": ((0.30, 0.31, 0.32), 0.55, 0.5, 0.06),
    "Wall": ((0.30, 0.31, 0.33), 0.5, 0.5, 0.06), "Human": ((0.10, 0.11, 0.13), 0.7, 0.0, 0.0),
}
EMIS = {"EmOff": ((0.015, 0.017, 0.02), 0.25, None, 0), "EmGreen": ((0.02, 0.02, 0.02), 0.3, (0.15, 1.0, 0.3), 6),
        "EmRed": ((0.02, 0.02, 0.02), 0.3, (1.0, 0.06, 0.03), 6), "EmWhite": ((0.02, 0.02, 0.02), 0.3, (0.9, 0.95, 1.0), 6),
        "EmAmber": ((0.02, 0.02, 0.02), 0.3, (1.0, 0.42, 0.02), 2.5),
        "Terminal": ((0.01, 0.02, 0.03), 0.15, (0.12, 0.45, 0.6), 1.2), "Caption": ((0.8, 0.8, 0.8), 0.5, (1, 1, 1), 0.6)}
GHOSTS = {"GhostSlot": ((0.15, 0.85, 1.0), 0.35), "GhostReg": ((1.0, 0.35, 0.1), 0.25),
          "GhostOcc": ((1.0, 0.85, 0.1), 0.25), "GhostMount": ((0.2, 1.0, 0.3), 0.45),
          "GhostRed": ((1.0, 0.08, 0.06), 0.40)}


def get(key):
    """Stand-in material for a key: 'PMC:<paint>', a SIMPLE / EMIS / GHOSTS name, or '<Atlas...>:<paint>'."""
    if key in _CACHE and _CACHE[key].name in bpy.data.materials:
        return _CACHE[key]
    base, _, var = key.partition(":")
    paint = var or "keen"
    if base == "PMC":
        m = simple("pv_" + key, PAINTS[paint], 0.42, 0.55, noise=0.06)
    elif base in SIMPLE:
        c, r, mt, nz = SIMPLE[base]
        m = simple("pv_" + key, c, r, mt, noise=nz)
    elif base in EMIS:
        c, r, e, s = EMIS[base]
        m = simple("pv_" + key, c, r, 0.0, emit=e, estr=s)
    elif base in GHOSTS:
        c, a = GHOSTS[base]
        m = simple("pv_" + key, c, 0.4, 0.0, emit=c, estr=0.6, alpha=a)
    elif base == "Frosted":   # vanilla FrostedGlass stand-in: milky, half see-through, light passes
        # the emission fakes the room light transmitted through the frosting (an alpha blend alone reads as tinted air)
        m = simple("pv_" + key, (0.82, 0.86, 0.90), 0.4, 0.0, emit=(0.74, 0.80, 0.86), estr=0.22, alpha=0.72)
        for attr in ("use_transparent_shadow",):
            try:
                setattr(m, attr, True)
            except (AttributeError, TypeError):
                pass
    elif base == "GlassThin":   # vanilla WindowGlassThin stand-in: clear, faintly blue-grey,
        # glossy: what is behind reads through it; a little emission so the pane still reads against a dark room
        m = simple("pv_" + key, (0.62, 0.70, 0.76), 0.08, 0.0, emit=(0.55, 0.62, 0.68), estr=0.05, alpha=0.30)
        try:
            m.use_transparent_shadow = True
        except (AttributeError, TypeError):
            pass
    elif base in BY_GUID:
        m = guid_mat("pv_" + key, base)
    elif base == "Perf":
        m = perf_mat("pv_" + key)
    elif base == "Fractured":
        m = fracture_mat("pv_" + key)
    elif base.startswith("Atlas") or base in ("ConveyorsAtlas", "GratingMetallic"):
        m = atlas_mat("pv_" + key, base, paint)
    else:
        print("UNKNOWN preview material", key)
        m = simple("pv_" + key, (1, 0, 1), 0.5)
    _CACHE[key] = m
    return m


# ---------------------------------------------------------------------------------------------------- by GUID
# The materials a build stage's SetMaterial or a Damaged .model's MaterialReference names, keyed by GUID (states
# preview, preview.states). Construction metals use the SDK's own albedo + alpha mask (read only, downsized into
# the texture cache, preview.texcache); the scaffold is shown as a hologram so it reads apart from the ghosted skin.
CONSTRUCTION_UV = 3.0   # our UVs run 0.5 / m: repeat the construction tile so its pattern reads at block scale
# Identifiers of vanilla content that mods reference, as .def files do.
BY_GUID = {
    "2a8225c1-df6c-41a7-ad6f-b58a77bdd88d": ("tex", "ConstructionMetal01"),   # transparent sequence 1/3
    "d5d7f641-9756-4776-abb5-ffd52f2f80a8": ("tex", "ConstructionMetal02"),   # 2/3
    "9f97552e-a56c-4f5a-8c6c-3be96ab453f5": ("tex", "ConstructionMetal03"),   # 3/3
    "857c023a-0cbb-43e0-9af4-5ce18d9ab587": ("tex", "ConstructionMetal09"),   # solid sequence 1/3
    "f49fee87-8abf-4faf-89d1-d49b9ef46a0b": ("tex", "ConstructionMetal12"),   # 2/3
    "851d4d6e-9ec8-467f-87a5-06ef06e97336": ("tex", "ConstructionMetal14"),   # 3/3 (also 25cm / SubpartConstruct)
    "4050183d-1340-4643-b8b9-124981f2121d": ("holo", "BrushedMetal_Scaffold"),  # Scaffolding / Support sequence 1
    "0c3be477-8bff-4e2c-8a0e-d7d842905ae9": ("key", "Brushed"),                # BrushedMetal: Support sequence 2
    "0f2772fd-72d6-4ee9-9211-38d1781ba8b9": ("key", "Fractured"),              # FracturedMaterial01: Fracture preset
    "83c075f5-f177-460d-94a7-8aca19fd4d6f": ("key", "GlassThin"),              # WindowGlass (WindowGlass preset)
    "2cc33fe2-00c5-4a64-a648-d1ea7aa8e3ee": ("key", "GratingMetallic:keen"),   # GratingMetallic preset
    # 4.0 Deformed (Damaged-model) parallax materials: the game's own _cm / _add DDS (deformed_mat)
    "d8847d2a-cbb3-4982-9036-ee7785cce3c1": ("deformed", "PaintedMetalColorableDeformed"),
    "ea15e749-58ce-4ca8-95e2-307af0bd273d": ("deformed", "PaintedMetalDarkerDeformed"),
    "4418666d-ba5b-481c-b9a3-e7233fc001a8": ("deformed", "ChromeDeformed"),
    "d2d76bf2-2c7c-4798-8eec-c515c07b84cc": ("deformed", "BrushedMetalDeformed"),
    "bcefad69-a79d-498b-97c8-5f2c024cfc52": ("key", "EmOff"),                # EmissiveOffBroken
    "0f138e31-e25c-415e-94a8-137b9c3f5f7f": ("key", "EmOff"),                # Display01Broken
    "b9db8238-9373-4690-b743-17e37759179e": ("crack", "Frosted"),            # WindowGlassBroken
    "8badb36c-3551-4f48-b1c1-b86c34bed900": ("crack", "GlassThin"),          # WindowGlassBrokenThin
}
_SDK_NAMES = {}


def sdk_material_name(guid):
    """Name of an SDK BlockMaterials definition by GUID (scanned once, read only); None if unknown."""
    if not _SDK_NAMES and sdk_mats():
        for p in glob.glob(os.path.join(glob.escape(sdk_mats()), "**", "*.def"), recursive=True):
            try:
                with open(p, encoding="utf-8-sig") as f:
                    v = json.load(f).get("$Value") or {}
            except (ValueError, OSError):
                continue
            g = v.get("Guid") or (v.get("Manipulator") or {}).get("Guid")
            if g:
                _SDK_NAMES[g] = os.path.splitext(os.path.basename(p))[0]
    return _SDK_NAMES.get(guid)


def _small_png(src, size=1024):
    """A <=size copy of an SDK PNG in the texture cache (the construction textures are 4096^2), written under a mkstemp
    name and renamed into place (parallel renders share the cache)."""
    cache = _cache()
    out = os.path.join(cache, f"s{size}_" + os.path.basename(src))
    if not os.path.exists(out):
        fd, tmp = _mkstemp(cache, ".png")
        os.close(fd)
        im = bpy.data.images.load(src)
        if max(im.size) > size:
            im.scale(size, size)
        im.filepath_raw = tmp
        im.file_format = "PNG"
        im.save()
        bpy.data.images.remove(im)
        os.replace(tmp, out)
    return out


def construction_mat(name, mat):
    """A construction material from its SDK albedo (+ alpha mask: the transparent sequence's perforation), by UV."""
    hits = glob.glob(os.path.join(glob.escape(sdk_mats()), "**", mat, mat + "_albedo.png"),
                     recursive=True) if sdk_mats() else []
    if not hits:
        print("NO TEXTURE for", mat)
        return simple(name, (0.45, 0.46, 0.48), 0.6, 0.6)
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    uv = _node(nt, "ShaderNodeTexCoord")
    mp = _node(nt, "ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (CONSTRUCTION_UV, CONSTRUCTION_UV, 1)
    nt.links.new(uv.outputs["UV"], mp.inputs["Vector"])
    t = _node(nt, "ShaderNodeTexImage")
    t.image = _img(_small_png(hits[0]))
    nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
    mix = _node(nt, "ShaderNodeMix", data_type="RGBA", blend_type="MIX")   # tame the greeble atlas' contrast
    mix.inputs[0].default_value = 0.45
    nt.links.new(t.outputs["Color"], mix.inputs[6])
    mix.inputs[7].default_value = (0.42, 0.43, 0.45, 1)
    nt.links.new(mix.outputs[2], b.inputs["Base Color"])
    am = hits[0].replace("_albedo.png", "_alphamask.png")
    if os.path.exists(am):
        ta = _node(nt, "ShaderNodeTexImage")
        ta.image = _img(_small_png(am), noncolor=True)
        nt.links.new(mp.outputs["Vector"], ta.inputs["Vector"])
        nt.links.new(ta.outputs["Color"], b.inputs["Alpha"])
        for attr, val in (("surface_render_method", "DITHERED"), ("blend_method", "CLIP"),
                          ("use_backface_culling", False)):
            try:
                setattr(m, attr, val)
            except (AttributeError, TypeError):
                pass
    b.inputs["Metallic"].default_value = 0.6
    b.inputs["Roughness"].default_value = 0.55
    return m


DEFORMED_FALLBACK = {"PaintedMetalColorableDeformed": "PMC:keen", "PaintedMetalDarkerDeformed": "Darker",
                     "ChromeDeformed": "Chrome", "BrushedMetalDeformed": "Brushed"}
DEFORMED_UV = 1.0   # the Deformed textures' tile on the models' own UVs (ours run 0.5 / m, vanilla's alike)


def deformed_mat(name, base, paint="keen"):
    """A 4.0 Deformed parallax material from the game's DDS (read only, converted into the texture cache): _cm colour
    (alpha: metalness), _add R = AO and B = the colour mask the player paint multiplies (as atlas_mat), so the scratched
    paint keeps the player colour on the Colorable one. Without the DDS: the base stand-in, darker and rougher."""
    cm, add = _find_dds(base, "_cm"), _find_dds(base, "_add")
    cm_png, add_png = (_dds_png(cm) if cm else None), (_dds_png(add) if add else None)
    if not (cm_png and add_png):
        print("NO TEXTURE for", base)
        fb = DEFORMED_FALLBACK[base]
        c, r, mt = SIMPLE[fb][:3] if fb in SIMPLE else (PAINTS[paint], 0.42, 0.55)
        return simple(name, tuple(v * 0.72 for v in c), min(1.0, r + 0.2), mt, noise=0.25)
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    uv = _node(nt, "ShaderNodeTexCoord")
    mp = _node(nt, "ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (DEFORMED_UV, DEFORMED_UV, 1)
    nt.links.new(uv.outputs["UV"], mp.inputs["Vector"])
    t1 = _node(nt, "ShaderNodeTexImage")
    t1.image = _img(cm_png)
    t2 = _node(nt, "ShaderNodeTexImage")
    t2.image = _img(add_png, noncolor=True)
    for t in (t1, t2):
        nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
    sep = _node(nt, "ShaderNodeSeparateColor")
    nt.links.new(t2.outputs["Color"], sep.inputs[0])
    pc = PAINTS[paint]
    mul = _node(nt, "ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    nt.links.new(sep.outputs[2], mul.inputs[0])
    nt.links.new(t1.outputs["Color"], mul.inputs[6])
    mul.inputs[7].default_value = (min(pc[0] / PAINT_REF, 4), min(pc[1] / PAINT_REF, 4), min(pc[2] / PAINT_REF, 4), 1)
    ao = _node(nt, "ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    ao.inputs[0].default_value = 0.8
    nt.links.new(mul.outputs[2], ao.inputs[6])
    nt.links.new(sep.outputs[0], ao.inputs[7])
    nt.links.new(ao.outputs[2], b.inputs["Base Color"])
    nt.links.new(t1.outputs["Alpha"], b.inputs["Metallic"])
    b.inputs["Roughness"].default_value = 0.55
    return m


def guid_mat(name, guid):
    kind, arg = BY_GUID[guid]
    if kind == "tex":
        return construction_mat(name, arg)
    if kind == "deformed":
        return deformed_mat(name, arg)
    if kind == "holo":   # the scaffold cage: translucent hologram blue
        return simple(name, (0.10, 0.45, 1.0), 0.3, 0.0, emit=(0.10, 0.45, 1.0), estr=1.2, alpha=0.35)
    if kind == "crack":  # broken glass: the pane's stand-in, greyer and more opaque
        return simple(name, (0.45, 0.48, 0.50), 0.7, 0.0, alpha=0.8)
    return get(arg)


def key_for_guid(guid, fallback="Darker"):
    """Preview key of a material GUID: BY_GUID, else an SDK construction texture by its name, else `fallback`."""
    if guid in BY_GUID:
        return guid
    n = sdk_material_name(guid)
    if n:
        BY_GUID[guid] = ("tex", n) if glob.glob(os.path.join(glob.escape(sdk_mats()), "**", n, n + "_albedo.png"),
                                                recursive=True) else ("key", keen_key(n))
        return guid
    print("unknown material GUID", guid)
    return fallback


KEEN_MAP = {"PaintedMetalDarker": "Darker", "PaintedMetalGrey": "Grey", "PaintedMetalYellow": "Yellow",
            "BrushedMetal": "Brushed", "Chrome": "Chrome", "MetalDull": "MetalDull", "Rubber": "Rubber",
            "Plastic": "Plastic2", "Copper": "Copper", "EmissiveOff": "EmOff", "LCDScreen_Off": "EmOff",
            "LCDScreenOff": "EmOff", "EmissiveColorable": "EmOff", "EmissiveGreen": "EmGreen",
            "ChargeStatusIndicator": "EmGreen", "DynamicIndicator": "EmGreen", "EmissiveRed": "EmRed",
            "EmissiveStatus": "EmGreen", "EmissiveWhite": "EmWhite", "EmissiveAmber": "EmAmber", "EmissiveScriptStatus": "EmAmber", "Terminal": "Terminal", "Display01": "Terminal",
            "MetalPerforationSteel": "Perf", "FracturedMaterial01": "Fractured",
            "FrostedGlass": "Frosted", "WindowGlass": "Frosted", "WindowGlassThin": "GlassThin", "ClearGlass": "Frosted"}
# states preview: vanilla analogues' slots, the EventController lamps (EmissiveOff by default), broken glass
KEEN_MAP.update({"BraidedSteel": "MetalDull", "CarbonFiber": "Plastic2", "Emissive01": "EmOff", "Emissive02": "EmOff",
                 "WindowGlassBroken": "b9db8238-9373-4690-b743-17e37759179e"})
KEEN_MAP.update({"LCDScreen_Off_4": "EmOff", "LCDScreen_Off_4_Back": "EmOff"})   # 4:1 LCD panels


def keen_key(slot_name, paint="keen"):
    n = slot_name.split(".")[0]
    if n.startswith("pv_"):
        return n[3:]
    for suf in ("_Subpart", "_SubPart"):   # vanilla stand-in slots wear their base slot's material
        if n.endswith(suf) and n != suf:
            return keen_key(n[:-len(suf)], paint)
    if n.startswith("PaintedMetalColorable") or n.endswith("_LastLOD"):
        return "PMC:" + paint
    if n in KEEN_MAP:
        return KEEN_MAP[n]
    if n.startswith("Atlas") or n in ("ConveyorsAtlas", "GratingMetallic"):
        return n + ":" + paint
    print("unmapped slot", n)
    return "Darker"


def apply(objs, paint="keen"):
    for o in objs:
        if o.type not in ("MESH", "FONT", "CURVE"):
            continue
        for i, s in enumerate(o.material_slots):
            if s.material:
                o.material_slots[i].material = get(keen_key(s.material.name, paint))
