"""`se2mod lock`: the CLI around `lease`."""
import pytest

from se2modkit import cli


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)


def test_lock_registered():
    assert "lock" in cli.commands()


def test_take_list_release(tmp_path, capsys, monkeypatch):
    blend = tmp_path / "drive.blend"
    monkeypatch.setenv("SE2_SESSION", "SE2 Mod Builder")
    assert cli.main(["lock", "take", str(blend), "--ttl", "1h"]) == 0
    assert cli.main(["lock", "list"]) == 0
    out = capsys.readouterr().out
    assert "SE2 Mod Builder" in out and "drive.blend" in out
    assert cli.main(["lock", "release", str(blend)]) == 0


def test_take_held_by_another_exits_1_naming_the_holder(tmp_path, capsys):
    blend = tmp_path / "drive.blend"
    assert cli.main(["lock", "take", str(blend), "--owner", "A"]) == 0
    assert cli.main(["lock", "take", str(blend), "--owner", "B"]) == 1
    assert "held by A" in capsys.readouterr().err
