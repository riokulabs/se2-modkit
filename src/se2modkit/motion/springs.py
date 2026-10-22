"""The closed-form damped spring with a leading goal, the motion behind our scripted animations (the house feel:
spring-eased, sequenced moves). Exact for any step, so a motion does not depend on the frame rate: one advance of h
equals any split of h (up to rounding). A line-for-line port of our C# spring (`Spring.Advance`); the tests check it
against values that C# computed, an RK4 integration and its own split invariance.

    x'' + 2 zeta omega x' + omega^2 x = omega^2 g,      g' = lambda (target - g)

The goal g eases toward the target at the lead rate lambda = lead_ratio x omega (0: the goal jumps to the target);
the axis advances in closed form (under-, critically or over-damped). When the lead rate equals a spring root
(resonance) it is nudged by 5 %; the feel is unchanged.
"""

from __future__ import annotations

import math

__all__ = ["Axis", "Spring", "overshoot", "trajectory"]


class Axis:
    """One axis: position x, velocity v and the lead goal g the spring chases (the C# Axis)."""

    __slots__ = ("x", "v", "g")

    def __init__(self, x=0.0, v=0.0, g=0.0):
        self.x, self.v, self.g = float(x), float(v), float(g)

    @property
    def is_finite(self):
        return all(math.isfinite(c) for c in (self.x, self.v, self.g))

    def leave_stop(self, target):
        """A part resting on a stop (0 or 1) with its goal preloaded past it starts from the stop: asked away from
        the stop, the goal is pulled back onto it (else the part first sits pressed into the stop for a few ms, a
        step-size dependent delay). At rest on a stop the net acceleration is zero either way: the motion stays
        continuous."""
        if self.x <= 0.0 and self.v <= 0.0 and self.g < 0.0 and target > 0.0:
            self.g = 0.0
        elif self.x >= 1.0 and self.v >= 0.0 and self.g > 1.0 and target < 1.0:
            self.g = 1.0

    def near(self, x, goal, eps, omega):
        """Within eps of x (and of the goal), with a speed under eps x omega."""
        return abs(self.x - x) <= eps and abs(self.v) <= eps * omega and abs(self.g - goal) <= eps

    def __repr__(self):
        return f"Axis(x={self.x!r}, v={self.v!r}, g={self.g!r})"


class Spring:
    """A damped spring with a leading goal (the C# Spring), advanced in closed form."""

    def __init__(self, omega, zeta, lead_ratio):
        self.omega = float(omega)
        self.sigma = zeta * omega                 # decay rate
        self.wd = 0.0                             # damped frequency (zeta < 1)
        self.r1 = self.r2 = 0.0                   # real roots (zeta > 1)
        if abs(zeta - 1.0) < 1e-4:
            self.regime = 0                       # critically damped
        elif zeta < 1.0:
            self.regime = -1
            self.wd = omega * math.sqrt(1.0 - zeta * zeta)
        else:
            self.regime = 1
            root = omega * math.sqrt(zeta * zeta - 1.0)
            self.r1 = -self.sigma + root
            self.r2 = -self.sigma - root
        lead = lead_ratio * omega
        den = lead * lead - 2.0 * zeta * omega * lead + omega * omega
        if lead > 0.0 and abs(den) < 1e-3 * omega * omega:
            # resonance (the lead rate equals a spring root): nudge the lead off it; the feel is unchanged
            lead *= 1.05
            den = lead * lead - 2.0 * zeta * omega * lead + omega * omega
        self.lead = lead                          # lambda; 0 = the goal jumps to the target
        self.gain = omega * omega / den if lead > 0.0 else 0.0   # the particular solution's gain

    def advance(self, a, target, h):
        """Advance Axis a by h seconds toward target (in place; returns a)."""
        # goal: g(t) = target + G0 e^{-lambda t}; particular solution x_p(t) = target + K e^{-lambda t}
        g0 = a.g - target if self.lead > 0.0 else 0.0
        k = self.gain * g0
        decay = math.exp(-self.lead * h) if self.lead > 0.0 else 0.0
        # homogeneous part: e = x - x_p, u = v - x_p'
        e = a.x - target - k
        u = a.v + self.lead * k
        e, u = self._homogeneous(e, u, h)
        a.x = target + k * decay + e
        a.v = -self.lead * k * decay + u
        a.g = target + g0 * decay
        return a

    def _homogeneous(self, e, u, h):
        w = self.omega
        if self.regime == 0:
            ex = math.exp(-w * h)
            b = u + w * e
            return (e + b * h) * ex, (u - w * b * h) * ex
        if self.regime < 0:
            ex = math.exp(-self.sigma * h)
            c, s = math.cos(self.wd * h), math.sin(self.wd * h)
            e1 = ex * (e * c + (u + self.sigma * e) / self.wd * s)
            return e1, ex * (u * c - (self.sigma * u + w * w * e) / self.wd * s)
        a1 = (u - self.r2 * e) / (self.r1 - self.r2)
        a2 = e - a1
        x1, x2 = math.exp(self.r1 * h), math.exp(self.r2 * h)
        return a1 * x1 + a2 * x2, a1 * self.r1 * x1 + a2 * self.r2 * x2


def trajectory(spring, axis, target, t_end, h):
    """[(t, x, v, g)] sampled every h from 0 to t_end (inclusive) for a fixed target: a convenience for curves and
    previews (a rig drives its targets per channel itself)."""
    out = [(0.0, axis.x, axis.v, axis.g)]
    n = int(round(t_end / h))
    for i in range(1, n + 1):
        spring.advance(axis, target, h)
        out.append((i * h, axis.x, axis.v, axis.g))
    return out


def overshoot(spring, t_end=None, steps=4000):
    """How far the step response from rest (0 -> 1) overshoots the target, as a share of the travel (0.0 when it
    never passes it). Sampled on `steps` exact closed-form steps up to `t_end` (default 30 / omega: settled)."""
    t_end = 30.0 / spring.omega if t_end is None else t_end
    a, peak, h = Axis(), 0.0, t_end / steps
    for _ in range(steps):
        spring.advance(a, 1.0, h)
        peak = max(peak, a.x)
    return max(0.0, peak - 1.0)
