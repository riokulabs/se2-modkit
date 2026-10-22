"""Fixtures for the pipeline tests: `hooks`, a `ModHooks` of a mod `XX_Mod` (prefix `XX_`) with one toy fracture
plan, and `synthetic_spec(...)` blocks of a family "box" (helpers in pipefix.py)."""

from __future__ import annotations

import pytest

from hookfix import make_hooks
from pipefix import make_spec, toy_plan


@pytest.fixture
def synthetic_spec():
    """`synthetic_spec(family="box", size=(1, 1, 1), name=None, variant="")` -> a block spec."""
    return make_spec


@pytest.fixture
def hooks(tmp_path):
    """A `ModHooks` for XX_Mod with the toy plan registered for family "box" and its dent / jag parameters."""
    h = make_hooks(tmp_path / "mod")
    h.plans["box"] = toy_plan
    h.dent["box"] = dict(amp=0.02, freq=5.0, fine=0.006, fine_freq=19.0, max_edge=0.05)
    h.jag["box"] = dict(step=0.02, amp=0.015, freq=9.0, reach=2.5)
    return h
