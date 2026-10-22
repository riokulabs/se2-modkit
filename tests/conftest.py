import functools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "env"))  # fake_steam / fake_sdk builders

from se2modkit import cli  # noqa: E402


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """Run `cli.main` against a synthetic home: no real env vars, no user config, no system locations."""
    def go(*argv, environ=None):
        monkeypatch.setattr(cli, "discover_env", functools.partial(
            cli.discover_env, environ=environ or {}, platform="linux", home=tmp_path / "home", config={},
            sysroot=tmp_path / "sys"))
        code = cli.main(list(argv))
        cap = capsys.readouterr()
        return code, cap.out, cap.err
    return go


@pytest.fixture(autouse=True)
def _build_state(tmp_path_factory, monkeypatch):
    """Incremental build fragments (pipeline.incremental) go to a temp folder, never the user cache."""
    monkeypatch.setenv("SE2MODKIT_BUILD_STATE", str(tmp_path_factory.mktemp("build-state")))
