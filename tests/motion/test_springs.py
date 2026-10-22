"""`motion.springs`: the closed-form damped spring with a leading goal, checked three ways: against values our C#
spring computed (the recorded table below: the C# source run with dotnet, one `Advance` per row), against an
independent RK4 integration of the same ODE, and on the closed form's own properties (one step of h equals any split
of h; it settles on the target)."""

import math

import pytest

from se2modkit.motion import springs as SP

# (omega, zeta, lead ratio, x0, v0, g0, target, h, x1, v1, g1): the C# Spring.Advance, one step
CSHARP = [
    (14, 0.6, 0, 0, 0, 0, 1, 0.05, 0.18155307218140626, 6.107748536287436, 1),
    (14, 0.6, 0.5, 0, 0, 0, 1, 0.05, 0.020976090341253056, 1.124038872881072, 0.29531191028128656),
    (12, 1, 0, 0.2, -0.5, 0.2, 1, 0.1, 0.4548384764389143, 3.4998767424197883, 1),
    (12, 1, 0.35, 0.2, -0.5, 0.2, 1, 0.1, 0.22587540403874928, 0.9917643252719133, 0.47436254414795453),
    (9, 1.6, 0, 1, 0, 1, 0, 0.07, 0.8909028535512914, -2.289499743240348, 0),
    (9, 1.6, 0.8, 1, 0.3, 1.2, 0, 0.07, 1.0080116766743887, -0.3011727012320673, 0.7249312594270376),
    (10, 1, 1, 0, 0, 0, 1, 0.2, 0.33212481427796803, 2.749628028128029, 0.8775435717470181),       # resonance nudge
    (20, 0.3, 2, 0.5, 1, -0.1, 0.25, 0.013, 0.4944767845608651, -1.6534506626081105, 0.04191780821043198),
]


@pytest.mark.parametrize("row", CSHARP)
def test_springs_match_recorded_csharp_values(row):
    w, z, lead, x, v, g, target, h, x1, v1, g1 = row
    a = SP.Axis(x, v, g)
    SP.Spring(w, z, lead).advance(a, target, h)
    assert (a.x, a.v, a.g) == pytest.approx((x1, v1, g1), rel=1e-12, abs=1e-12)


def _rk4(w, z, lam, x, v, g, target, t, n=20000):
    """x'' = w^2 (g - x) - 2 z w x',  g' = lam (target - g)  (lam = 0: g = target at once)."""
    if lam == 0:
        g = target
    h = t / n

    def f(s):
        x, v, g = s
        return (v, w * w * (g - x) - 2 * z * w * v, lam * (target - g))
    s = (x, v, g)
    for _ in range(n):
        k1 = f(s)
        k2 = f(tuple(a + h / 2 * b for a, b in zip(s, k1)))
        k3 = f(tuple(a + h / 2 * b for a, b in zip(s, k2)))
        k4 = f(tuple(a + h * b for a, b in zip(s, k3)))
        s = tuple(a + h / 6 * (b + 2 * c + 2 * d + e) for a, b, c, d, e in zip(s, k1, k2, k3, k4))
    return s


@pytest.mark.parametrize("w,z,lead", [(14, 0.6, 0.0), (14, 0.6, 0.5), (12, 1.0, 0.0), (12, 1.0, 0.35), (9, 1.6, 0.0),
                                      (9, 1.6, 0.8), (25, 0.2, 1.3)])
def test_matches_rk4(w, z, lead):
    sp = SP.Spring(w, z, lead)
    a = SP.Axis(0.1, -0.4, 0.3)
    sp.advance(a, 1.0, 0.37)
    want = _rk4(w, z, sp.lead, 0.1, -0.4, 0.3, 1.0, 0.37)
    assert (a.x, a.v, a.g) == pytest.approx(want, abs=1e-8)


@pytest.mark.parametrize("w,z,lead", [(14, 0.6, 0.5), (12, 1.0, 0.0), (9, 1.6, 0.8)])
def test_step_split_invariant(w, z, lead):
    """Exact for any step: one step of h equals three of h / 3 (the motion does not depend on the frame rate)."""
    sp = SP.Spring(w, z, lead)
    a, b = SP.Axis(0.0, 0.0, 0.0), SP.Axis(0.0, 0.0, 0.0)
    sp.advance(a, 1.0, 0.09)
    for _ in range(3):
        sp.advance(b, 1.0, 0.03)
    assert (a.x, a.v, a.g) == pytest.approx((b.x, b.v, b.g), abs=1e-12)


def test_settles_and_stays():
    sp = SP.Spring(14, 0.9, 0.5)
    a = SP.Axis(0.0, 0.0, 0.0)
    for _ in range(400):
        sp.advance(a, 1.0, 1 / 60)
    assert a.near(1.0, 1.0, 1e-6, sp.omega) and a.is_finite
    sp.advance(a, 1.0, 5.0)
    assert a.x == pytest.approx(1.0, abs=1e-9)


def test_house_feel_overshoot_within_band():
    """The house opening spring (zeta 0.85, lead ratio 4) overshoots its travel by 0.2-0.9 % (the settle bounce of
    the style profile's animation.overshoot band); a critically damped one does not overshoot."""
    assert 0.002 <= SP.overshoot(SP.Spring(1.0, 0.85, 4.0)) <= 0.009
    assert 0.002 <= SP.overshoot(SP.Spring(14.0, 0.85, 4.0)) <= 0.009
    assert SP.overshoot(SP.Spring(14.0, 1.0, 4.0)) == pytest.approx(0.0, abs=1e-9)


def test_resonance_nudge():
    """lead = omega with zeta = 1 is a double root: the lead rate is nudged by 5 %."""
    assert SP.Spring(10, 1.0, 1.0).lead == pytest.approx(10.5)
    assert SP.Spring(10, 0.6, 0.5).lead == pytest.approx(5.0)


def test_regimes():
    assert SP.Spring(10, 0.5, 0).regime == -1 and SP.Spring(10, 1.00005, 0).regime == 0
    assert SP.Spring(10, 1.5, 0).regime == 1


def test_leave_stop():
    a = SP.Axis(0.0, 0.0, -0.2)
    a.leave_stop(1.0)
    assert a.g == 0.0
    b = SP.Axis(1.0, 0.0, 1.3)
    b.leave_stop(0.0)
    assert b.g == 1.0
    c = SP.Axis(0.5, 0.0, -0.2)
    c.leave_stop(1.0)
    assert c.g == -0.2                      # not on a stop: untouched
    assert not SP.Axis(math.nan, 0, 0).is_finite


def test_trajectory_samples_inclusive():
    rows = SP.trajectory(SP.Spring(14, 0.85, 4.0), SP.Axis(), 1.0, 0.5, 0.1)
    assert [round(t, 6) for t, *_ in rows] == [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
