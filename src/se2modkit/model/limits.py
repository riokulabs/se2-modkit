"""The SDK's per-model budgets (triangles per LOD, materials, colliders, fractures), so a model can be checked before
the SDK builder validates it.

Limits are per model file and interpolated by that model's bounding-box SURFACE AREA between rows measured for
cubes of 0.25 / 0.5 / 2.5 / 5 m. Fractured models get x1.83425 triangles and 32 materials. Exceeding a triangle
limit is a validator *warning* (suppressible with the project's Unlock* settings), not an error.
"""
import math

ROWS = [(0.25, [4000, 700, 32]), (0.5, [12000, 2000, 400, 32]), (2.5, [24000, 7000, 3000, 400, 32]),
        (5.0, [40000, 14000, 6000, 800, 32])]
FRACTURE_MULT = 1.83425
COLLIDERS = [(0.25, [2, 1]), (0.5, [4, 4]), (1.5, [5, 5]), (2.5, [7, 15])]
COLLIDER_VERTS = [(0.25, [64, 32]), (0.5, [76, 40]), (1.5, [128, 48]), (2.5, [192, 80]), (4.0, [192, 120])]
FRACTURES = [(0.25, [1]), (0.5, [2]), (1.5, [5]), (2.5, [15])]
LARGE_MATS, MEDIUM_MATS, SMALL_MATS = [18, 18, 12, 7, 1], [8, 8, 6, 4, 1], [4, 3, 1]


def surface(x, y, z):
    return 2 * (x * y + y * z + z * x)


def _interp(table, key):
    """(row, lower_index, upper_index, ratio): the row linearly interpolated between the two rows around key
    (no extrapolation past the last row)."""
    for up in range(len(table)):
        if key <= table[up][0]:
            lo = max(0, up - 1)
            span = table[up][0] - table[lo][0]
            r = (key - table[lo][0]) / span if span > 0 else 0.0
            a, b = table[lo][1], table[up][1]
            return [a[min(i, len(a) - 1)] + (b[i] - a[min(i, len(a) - 1)]) * r for i in range(len(b))], lo, up, r
    last = len(table) - 1
    return list(table[last][1]), last, last, 0.0


def _tri_table():
    return [(surface(s, s, s), v) for s, v in ROWS]


def max_triangles(size, lod, bias=0, fractured=False):
    row, lo, up, r = _interp(_tri_table(), surface(*size))
    i = min(bias + lod, len(row) - 1)
    low_row = _tri_table()[lo][1]
    n = int(low_row[-1]) if (r < 25 / 74 and i == len(low_row) - 1) else int(row[i])
    return int(math.floor(n * FRACTURE_MULT)) if fractured else n


def optimal_lods(size, lod0_tris):
    row, lo, up, r = _interp(_tri_table(), surface(*size))
    bias = len(row) - 1
    while row[bias] < lod0_tris and bias > 0:
        bias -= 1
    n = len(_tri_table()[up][1]) if r > 25 / 74 else len(_tri_table()[lo][1])
    if n <= bias:
        return 1, n
    return min(n - bias, 8), bias


def max_materials(size, lod, total_lods, fractured=False):
    if lod == total_lods - 1:
        return 1
    if fractured:
        return 32
    m = max(size)
    tab = LARGE_MATS if m > 2 else (MEDIUM_MATS if m >= 0.5 else SMALL_MATS)
    return tab[min(lod, len(tab) - 1)]


def collider_limits(size, fractured=False):
    m = max(size)
    count = _interp(COLLIDERS, m)[0][1 if fractured else 0]
    verts = _interp(COLLIDER_VERTS, m)[0][1] * (1.5 if fractured else 1.0)
    return int(count), int(verts)


def model_limits(size, lod0_tris=10 ** 9, fractured=False):
    """Every limit for a model of bounding-box `size` (m) with `lod0_tris` triangles in LOD0.

    lod0_tris defaults to 'huge', i.e. the limits for the largest LOD0 the size allows (lod_bias 0)."""
    n, bias = optimal_lods(size, lod0_tris)
    return {
        "size": [round(s, 3) for s in size], "surface": round(surface(*size), 3), "optimal_lods": n, "lod_bias": bias,
        "max_fractures": int(round(_interp(FRACTURES, max(size))[0][0])),
        "lods": [{"max_tris": max_triangles(size, i, bias, fractured), "max_materials": max_materials(size, i, n, fractured)}
                 for i in range(n)],
        "colliders": dict(zip(("max", "max_verts"), collider_limits(size, fractured))),
    }
