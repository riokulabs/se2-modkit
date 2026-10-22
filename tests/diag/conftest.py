import functools
import os
from pathlib import Path

import pytest
from diagfix import TOKEN, FakeMcp

from se2modkit.diag import locations
from se2modkit.env.core import discover_env

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def hermetic_env(tmp_path, monkeypatch):
    """diag sees a synthetic Linux machine: no Steam, game, Pulsar or user config; only the SE2_* / diag variables
    a test sets reach discovery."""
    def discover(platform="linux"):
        environ = {k: v for k, v in os.environ.items() if k.startswith(("SE2_", "DOTNET_"))}
        environ["HOME"] = str(tmp_path / "home")
        return discover_env(environ=environ, platform=platform, home=tmp_path / "home", config={},
                            sysroot=tmp_path / "sys")
    monkeypatch.setattr(locations, "_discover", discover)
    monkeypatch.setattr(locations, "_environ", lambda: {k: v for k, v in os.environ.items()
                                                        if k.startswith(("SE2_", "DOTNET_"))})
    locations.set_env(None)
    yield functools.partial(discover)
    locations.set_env(None)


@pytest.fixture
def fix():
    return FIX


@pytest.fixture
def mcp_cfg(tmp_path):
    p = tmp_path / "bridge.cfg"
    p.write_text(f"<?xml version=\"1.0\"?>\n<Config><Port>6789</Port><SecretKey>{TOKEN}</SecretKey></Config>\n")
    return p


@pytest.fixture
def fake_mcp():
    f = FakeMcp()
    yield f
    f.close()
