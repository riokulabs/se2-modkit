"""Decal atlases: trims (horizontal strips) and element atlases (Parts / Stickers), mapped from vanilla.

Vanilla decals are separate floating quads ~2 mm above the surface using UV0 (UVChannel_1 is all zeros on
decal meshes). Element rectangles below were measured from the vanilla atlas textures and cross-checked
against UV rectangles harvested from 198 vanilla FBX files (decal objects named e.g.
'AtlasDecal_Parts01_05_Colorable' -> u 0.662-0.682, v 0.974-0.994).
"""
import bmesh

from .config import DECAL_OFFSET, P1, S1, S2, T1, T2, T3

# ---------------------------------------------------------------- trims: (material, v0, v1); U tiles
TRIM_ASPECT = 4.0  # trim atlases are 1:4 (w:h)
TRIMS = {
    "panel_line": (T1, 0.7385, 0.7425),   # thin seam line (alpha: line only)
    "vent":       (T2, 0.557, 0.589),     # louvre row; slots see-through -> lay over Darker
    "hex_plates": (T2, 0.805, 0.840),     # strip with two hex access plates
    "detail":     (T3, 0.894, 0.918),     # mostly transparent line-detail band
}

# ---------------------------------------------------------------- Parts01 (4096^2): 4 material quadrants
# Quadrants: top-left Chrome, top-right Colorable, bottom-left Darker, bottom-right Yellow. The same element
# sits at the same offset in each quadrant. Rects below are in pixels of the 2048^2 Colorable quadrant,
# measured on a 1600 px preview (x0, y0, x1, y1 from the top-left), so u = 0.5 + x/3200, v = 1 - y/3200.
PARTS01_OFFSET = {"Colorable": (0.0, 0.0), "Chrome": (-0.5, 0.0), "Darker": (-0.5, -0.5), "Yellow": (0.0, -0.5)}
PARTS01 = {
    "bolt":          (10, 10, 90, 90),        # round bolt head
    "bolt_cross":    (110, 10, 190, 90),      # cross-head bolt
    "bolt_ring":     (210, 10, 290, 90),
    "capsule_plate": (890, 22, 1070, 80),     # small plate with two screws
    "handle":        (938, 110, 1035, 240),   # grab handle
    "round_hatch":   (512, 470, 675, 630),    # round service hatch with latch
    "louvre_plate":  (975, 655, 1145, 810),   # plate with louvres
    "grille_quad":   (1175, 650, 1370, 835),  # four-pane fine grille (radiator)
    "plate_rivets":  (1395, 655, 1530, 880),  # riveted access plate (vertical)
    "vent_bars":     (665, 665, 765, 795),    # stacked bar vent
    "round_rivets":  (12, 825, 188, 1000),    # riveted round cover
    "plate_tab":     (1160, 865, 1345, 1015), # hatch with hinge tabs
    "fan":           (855, 1030, 1040, 1215), # radial fan / intake
    "grille_wide":   (1070, 1050, 1350, 1235),  # wide intake grille
    "dot_grid":      (275, 1255, 360, 1340),  # rivet / sensor dot grid
    "hatch_slots":   (990, 1270, 1240, 1440), # maintenance hatch with slots
    "frame_square":  (700, 1240, 965, 1505),  # square bordered hatch frame
    "truss_strip":   (218, 1385, 668, 1512),  # strip with diagonal ribs
    "slot_long":     (1545, 660, 1577, 780),  # long slot
}

# ---------------------------------------------------------------- Stickers02 (2048^2), own colours
# Rects in px of a 1600 px preview of the full atlas: u = x/1600, v = 1 - y/1600.
STICKERS02 = {
    "moving_parts":    (1440, 605, 1570, 722),   # CAUTION! MOVING PARTS
    "remote_operated": (910, 1110, 1270, 1182),  # Remote Operated System / automated equipment
    "hv_supply":       (200, 1325, 415, 1392),   # WARNING HIGH VOLTAGE POWER SUPPLY (text only)
    "caution_maint":   (2, 1160, 192, 1218),     # CAUTION MAINTENANCE REQUIRED (text only)
    "barcode_long":    (1280, 1108, 1590, 1182),
    "warn_instr":      (1165, 1188, 1590, 1258), # WARNING / INSTRUCTION panel
    "tri_electric":    (622, 408, 812, 572),     # yellow triangle, electric hazard (bounds only: see HAZARD_TRIANGLES)
    "danger_hv":       (198, 582, 440, 750),     # DANGER HIGH VOLTAGE
    "tri_hot":         (745, 408, 945, 572),     # yellow triangle, hot surface (bounds only: see HAZARD_TRIANGLES)
}
# The atlas packs its warning triangles alternately point-down / point-up, interlocked, so the bounding rectangles
# above also catch corners of the neighbouring triangles (seen in game as diagonal slivers beside the sign). element()
# draws these keys as exact triangles instead: (base-left, base-right, apex) in atlas px (2048^2), the straight-edge
# vertices of the orange border plus ~3 px of transparent margin (measured on the 1600 px preview at 30 deg edges,
# clear of both neighbours). "down" = stored point-down; element() turns it 180 deg so every sign is drawn point up
# (ISO 7010 W012 / W017). A mod's own style module may share these values.
HAZARD_TRIANGLES = {
    "tri_electric": ("down", (788, 514), (1054, 514), (921, 742)),
    "tri_hot":      ("up",   (950, 726), (1218, 726), (1084, 498)),
}
# Hazard stripe bands at the top of Stickers02: one colour of diagonal stripes, alpha between the stripes,
# so lay the yellow band over Darker for a yellow/black hazard border. Rows in atlas px (2048 high).
STRIPE_ROWS = {"white": (0, 32), "blue": (32, 64), "black": (64, 96), "yellow": (96, 128), "red": (128, 160)}

# ---------------------------------------------------------------- Stickers01 (4096^2), icon stencils
# 4 quadrants: TL black, TR white, BL black (grunge), BR white (grunge). Icons on a 50 px grid of a 1600 px
# preview of the full atlas: u = x/1600, v = 1 - y/1600; white variant +0.5 u, grunge variants -0.5 v.
STICKERS01_OFFSET = {"black": (0.0, 0.0), "white": (0.5, 0.0), "black_grunge": (0.0, -0.5), "white_grunge": (0.5, -0.5)}
STICKERS01 = {
    "network_node": (52, 352, 100, 400),   # three-node network glyph (network stencil)
    "chevrons":     (153, 52, 200, 98),    # >> direction
    "warning_tri":  (553, 152, 598, 197),
    "power":        (150, 205, 200, 247),  # POWER stencil
    "bolt_hazard":  (2, 202, 48, 248),     # lightning
    "gear":         (2, 152, 48, 198),
    "arrow_right":  (650, 52, 700, 98),
    "lock":         (402, 202, 448, 248),
}


# Optional callbacks a mod can install (e.g. to register fasteners / labels in a connectivity model):
#   HOOKS["fastener"](part_name, key, variant)   called for every bolt decal
#   HOOKS["label"](kind, position, normal)       called for every hazard-label decal
HOOKS = {"fastener": None, "label": None}
HAZARD_LABELS = {"tri_hot": "hot", "tri_electric": "hv", "moving_parts": "moving"}


def element_uv(atlas, key, variant=None):
    """(u0, v0, u1, v1) of an atlas element."""
    if atlas == P1:
        x0, y0, x1, y1 = PARTS01[key]
        du, dv = PARTS01_OFFSET[variant or "Colorable"]
        return (0.5 + x0 / 3200 + du, 1 - y1 / 3200 + dv, 0.5 + x1 / 3200 + du, 1 - y0 / 3200 + dv)
    if atlas == S2:
        x0, y0, x1, y1 = STICKERS02[key]
        return (x0 / 1600, 1 - y1 / 1600, x1 / 1600, 1 - y0 / 1600)
    if atlas == S1:
        x0, y0, x1, y1 = STICKERS01[key]
        du, dv = STICKERS01_OFFSET[variant or "black"]
        return (x0 / 1600 + du, 1 - y1 / 1600 + dv, x1 / 1600 + du, 1 - y0 / 1600 + dv)
    raise KeyError(atlas)


def element_aspect(atlas, key):
    u0, v0, u1, v1 = element_uv(atlas, key)
    return (u1 - u0) / (v1 - v0)


def _quad(p, F, corners, d, mat, uvs):
    bm = p.bm
    uvl = bm.loops.layers.uv.verify()
    f = bm.faces.new([bm.verts.new(F(x, y, d + DECAL_OFFSET)) for x, y in corners])
    f.material_index = p.mi(mat)
    for loop, uv in zip(f.loops, uvs):
        loop[uvl].uv = uv
    return f


def trim(p, F, a, b, length, height, kind, d=0.0, vertical=False):
    """A trim strip decal on face F centred at (a, b); U tiles along the length."""
    mat, v0, v1 = TRIMS[kind]
    l2, h2 = length / 2, height / 2
    if vertical:
        corners = [(a + h2, b - l2), (a + h2, b + l2), (a - h2, b + l2), (a - h2, b - l2)]
    else:
        corners = [(a - l2, b - h2), (a + l2, b - h2), (a + l2, b + h2), (a - l2, b + h2)]
    ul = length / height * (v1 - v0) * TRIM_ASPECT
    return _quad(p, F, corners, d, mat, ((0, v0), (ul, v0), (ul, v1), (0, v1)))


def stripes(p, F, a, b, length, height, colour="yellow", d=0.0, vertical=False):
    """Hazard stripe band (Stickers02): diagonal stripes tiling in U."""
    r0, r1 = STRIPE_ROWS[colour]
    v0, v1 = 1 - r1 / 2048, 1 - r0 / 2048
    l2, h2 = length / 2, height / 2
    if vertical:
        corners = [(a + h2, b - l2), (a + h2, b + l2), (a - h2, b + l2), (a - h2, b - l2)]
    else:
        corners = [(a - l2, b - h2), (a + l2, b - h2), (a + l2, b + h2), (a - l2, b + h2)]
    ul = length / height * (v1 - v0)  # square atlas
    return _quad(p, F, corners, d, S2, ((0, v0), (ul, v0), (ul, v1), (0, v1)))


def hazard_triangle_uv(key):
    """(base-left, base-right, apex) UVs of a Stickers02 warning triangle drawn point up, and its height / base ratio."""
    stored, (xl, yb), (xr, _), (xa, ya) = HAZARD_TRIANGLES[key]
    if stored == "down":  # turned 180 deg: the atlas's right top corner becomes the drawn base-left
        (xl, xr) = (xr, xl)
    uv = [(x / 2048, 1 - y / 2048) for x, y in ((xl, yb), (xr, yb), (xa, ya))]
    return uv, abs(ya - yb) / abs(xr - xl)


def element(p, F, a, b, width, atlas, key, variant=None, d=0.0, rotate=False):
    """An atlas element (hatch, label, stencil) of the given width, keeping the element's aspect. Stickers02 warning
    triangles (HAZARD_TRIANGLES) come out as one exact triangle face, point up, `width` = the base."""
    u0, v0, u1, v1 = element_uv(atlas, key, variant)
    if key.startswith("bolt") and HOOKS["fastener"]:
        HOOKS["fastener"](p.name, key, variant)
    if key in HAZARD_LABELS and HOOKS["label"]:
        HOOKS["label"](HAZARD_LABELS[key], F(a, b, d), F.n)
    if atlas == S2 and key in HAZARD_TRIANGLES:
        uvs, ratio = hazard_triangle_uv(key)
        w2, h2 = width / 2, width * ratio / 2
        st = ((-w2, -h2), (w2, -h2), (0.0, h2))
        corners = [(a - t, b + s) if rotate else (a + s, b + t) for s, t in st]
        return _quad(p, F, corners, d, atlas, uvs)
    asp = (u1 - u0) / (v1 - v0)
    h = width / asp
    w2, h2 = width / 2, h / 2
    if rotate:  # element's U runs along the face's b axis
        corners = [(a + h2, b - w2), (a + h2, b + w2), (a - h2, b + w2), (a - h2, b - w2)]
    else:
        corners = [(a - w2, b - h2), (a + w2, b - h2), (a + w2, b + h2), (a - w2, b + h2)]
    return _quad(p, F, corners, d, atlas, ((u0, v0), (u1, v0), (u1, v1), (u0, v1)))

# ---------------------------------------------------------------- AtlasFont_BarlowCondensed (2048^2) stencil font
# Glyph boxes in atlas px of the clean top-left quadrant (measured from the alphamask by connected components):
# u = x / 2048, v = 1 - y / 2048. Cap height ~129 px. The vanilla welder's "WD-03" uses this material.
FONT_PX = 2048
CAP_PX = 129.0
GLYPHS = {"0": (626, 8, 693, 140), "1": (896, 598, 935, 726), "2": (581, 324, 648, 453), "3": (93, 344, 157, 473), "4": (197, 490, 272, 618), "5": (175, 342, 239, 471), "6": (460, 183, 524, 313), "7": (177, 638, 242, 766), "8": (712, 8, 776, 139), "9": (794, 8, 857, 140), "-": (960, 926, 1010, 944), "A": (103, 492, 179, 620), "B": (737, 454, 805, 582), "C": (203, 193, 270, 323), "D": (8, 638, 74, 766), "E": (425, 636, 488, 764), "F": (668, 617, 730, 745), "G": (289, 193, 356, 323), "H": (93, 638, 158, 766), "I": (954, 598, 974, 726), "J": (898, 157, 964, 286), "K": (290, 490, 361, 618), "L": (506, 629, 568, 757), "M": (8, 492, 84, 620), "N": (470, 483, 540, 611), "O": (116, 193, 184, 323), "P": (909, 452, 977, 580), "Q": (542, 8, 608, 157), "R": (823, 452, 891, 580), "S": (375, 186, 441, 316), "T": (648, 471, 719, 599), "U": (812, 158, 880, 287), "V": (940, 305, 1013, 433), "W": (812, 306, 921, 434), "X": (380, 490, 451, 618), "Y": (559, 480, 629, 608), "Z": (343, 636, 406, 764), "/": (823, 598, 878, 726)}


def text(p, F, a, b, height, string, d=0.0, track=0.10, rotate=False):
    """Stencil text on face F, left-aligned at (a, b) = (start, cap middle), cap height `height` (m).
    Returns the advance (m). With rotate=True the text runs along the face's b axis."""
    from .config import FONT
    s = height / CAP_PX
    x = 0.0
    for ch in string:
        if ch == " ":
            x += 0.45 * height
            continue
        x0, y0, x1, y1 = GLYPHS[ch]
        w, h = (x1 - x0) * s, (y1 - y0) * s
        # glyph boxes are tight, so each glyph is centred on the cap middle (the dash lands mid-height)
        u0, u1 = x0 / FONT_PX, x1 / FONT_PX
        v0, v1 = 1 - y1 / FONT_PX, 1 - y0 / FONT_PX
        if rotate:   # reading direction along +b, glyph tops toward -a (right x up = +n: not mirrored)
            corners = [(a + h / 2, b + x), (a + h / 2, b + x + w), (a - h / 2, b + x + w), (a - h / 2, b + x)]
            uvs = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
        else:
            corners = [(a + x, b - h / 2), (a + x + w, b - h / 2), (a + x + w, b + h / 2), (a + x, b + h / 2)]
            uvs = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
        _quad(p, F, corners, d, FONT, uvs)
        x += w + track * height
    return x


def text_width(height, string, track=0.10):
    s = height / CAP_PX
    w = 0.0
    for ch in string:
        if ch == " ":
            w += 0.45 * height
            continue
        x0, _, x1, _ = GLYPHS[ch]
        w += (x1 - x0) * s + track * height
    return w - track * height
