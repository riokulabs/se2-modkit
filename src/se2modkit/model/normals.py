"""Per-loop (corner) normal hook, numpy only (importable without Blender).

A Part may author an exact shading normal for some of its face corners in a bmesh loop float-vector layer named
LOOP_NORMAL_LAYER (geom.loop_normal_layer). objects.part_to_object then sets those corners' custom split normals
after its usual smoothing (objects.apply_loop_normals): a corner whose vector is zero keeps the normal the export
would have given it. No layer, no change: the hook is off unless a Part writes the layer, so every existing caller
(every existing mod) exports exactly as before.

Use it where the analytic normal is known and the area-weighted average is wrong, e.g. the strands of a twisted
cable bundle: each strand is a few sheared, twisted quads per lay segment, and averaging their face normals puts a
diagonal highlight in every segment (bands along the run); the radial normal from the strand's own axis does not.
"""
import numpy as np

LOOP_NORMAL_LAYER = "se2_loop_normal"
MIN_LEN = 1e-6


def merge_loop_normals(current, hook, min_len=MIN_LEN):
    """current, hook: (N, 3) per-corner normals. A hook row at least `min_len` long replaces the current one
    (normalised); a shorter (zero) row keeps the current normal (normalised, or +Z if it is degenerate).
    -> (N, 3) unit normals."""
    cur = np.asarray(current, float).reshape(-1, 3)
    hk = np.asarray(hook, float).reshape(-1, 3)
    if cur.shape != hk.shape:
        raise ValueError(f"current {cur.shape} and hook {hk.shape} differ")
    lh = np.linalg.norm(hk, axis=1)
    lc = np.linalg.norm(cur, axis=1)
    out = np.where(lh[:, None] >= min_len, hk / np.maximum(lh, min_len)[:, None],
                   cur / np.maximum(lc, min_len)[:, None])
    bad = (lh < min_len) & (lc < min_len)
    out[bad] = (0.0, 0.0, 1.0)
    return out


def radial_normal(point, axis_point, axis_dir):
    """The unit normal of a round surface at `point`: from the closest point of its axis line (axis_point, axis_dir)
    out to the point (e.g. a strand of a cable bundle, from the strand's own centreline)."""
    p, a, d = (np.asarray(v, float) for v in (point, axis_point, axis_dir))
    d = d / np.linalg.norm(d)
    r = (p - a) - d * ((p - a) @ d)
    n = np.linalg.norm(r)
    if n < MIN_LEN:
        raise ValueError("point lies on the axis")
    return r / n
