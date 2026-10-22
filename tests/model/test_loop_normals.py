"""The per-loop normal hook (se2modkit/model/normals.py, objects.apply_loop_normals). The numpy part runs anywhere; the
end-to-end check runs Blender headless when it is on PATH (skipped otherwise)."""
import json
import os
import shutil
import subprocess

import numpy as np
import pytest

from se2modkit.model.normals import LOOP_NORMAL_LAYER, merge_loop_normals, radial_normal

HERE = os.path.dirname(os.path.abspath(__file__))


def test_merge_keeps_unhooked_corners():
    cur = np.array([[0, 0, 2.0], [1, 0, 0], [0, 3, 0]])
    hook = np.array([[0, 0, 0.0], [0, 0, 5], [0, 0, 0]])
    out = merge_loop_normals(cur, hook)
    assert np.allclose(out, [[0, 0, 1], [0, 0, 1], [0, 1, 0]])


def test_merge_all_zero_is_identity_up_to_length():
    rng = np.random.default_rng(1)
    cur = rng.normal(size=(50, 3))
    out = merge_loop_normals(cur, np.zeros((50, 3)))
    assert np.allclose(out, cur / np.linalg.norm(cur, axis=1)[:, None])


def test_merge_shape_mismatch_and_degenerate():
    with pytest.raises(ValueError):
        merge_loop_normals(np.zeros((2, 3)), np.zeros((3, 3)))
    assert np.allclose(merge_loop_normals(np.zeros((1, 3)), np.zeros((1, 3))), [[0, 0, 1]])


def test_radial_normal():
    assert np.allclose(radial_normal((0.3, 0.0, 2.0), (0, 0, 0), (0, 0, 1)), (1, 0, 0))
    assert np.allclose(radial_normal((1.0, 1.0, 5.0), (0, 0, 5), (0, 0, 7)), np.array((1, 1, 0)) / np.sqrt(2))
    with pytest.raises(ValueError):
        radial_normal((0, 0, 1), (0, 0, 0), (0, 0, 1))


def test_layer_name_is_stable():
    assert LOOP_NORMAL_LAYER == "se2_loop_normal"     # Parts write it by name (geom.loop_normal_layer)


@pytest.fixture(scope="module")
def blender_run(tmp_path_factory):
    exe = shutil.which("blender")
    if not exe:
        pytest.skip("Blender not on PATH")
    out = tmp_path_factory.mktemp("ln") / "out.json"
    subprocess.run([exe, "-b", "--factory-startup", "--python", os.path.join(HERE, "blender_loop_normals.py"), "--",
                    str(out)], check=True, capture_output=True, timeout=300)
    with open(out) as f:
        return json.load(f)


def _radial(co):
    v = np.array(co, float) * (0, 1, 1)
    return v / np.linalg.norm(v)


def test_hook_sets_radial_normals(blender_run):
    """Every hooked corner exports exactly its authored radial normal (the twisted, sheared segment included), and
    the hook attribute is gone from the mesh."""
    h = blender_run["hooked"]
    n, co = np.array(h["normals"]), h["co"]
    want = np.array([_radial(c) for c in co])
    assert np.abs(n - want).max() < 1e-3
    assert not h["attr_left"]


def test_no_hook_no_change(blender_run):
    """Off by default: a Part without the layer exports the area-weighted smooth normals, which on the sheared
    segment differ from the radial ones (the banding the hook exists to fix)."""
    p = blender_run["plain"]
    n = np.array(p["normals"])
    want = np.array([_radial(c) for c in p["co"]])
    assert not p["attr_left"]
    assert np.abs(n - want).max() > 1e-3          # the default shading is not radial on the twisted quads
    assert np.allclose(np.linalg.norm(n, axis=1), 1, atol=1e-4)


def test_partial_hook_keeps_the_rest(blender_run):
    """Corners left at zero keep the default: the partially hooked tube's unhooked faces match the plain tube's
    corner for corner, except at the shared middle ring (one smooth fan with the hooked faces)."""
    p, q = blender_run["plain"], blender_run["partial"]
    assert p["co"] == q["co"] and p["face"] == q["face"]
    n_p, n_q = np.array(p["normals"]), np.array(q["normals"])
    x = np.array([c[0] for c in q["co"]])
    far = x > 0.3       # the last ring: only unhooked faces meet there
    assert far.any() and np.abs(n_p[far] - n_q[far]).max() < 1e-4
    near = x < 0.1      # the first ring: hooked corners only
    assert np.abs(n_q[near] - np.array([_radial(c) for c in np.array(q["co"])[near]])).max() < 1e-3
