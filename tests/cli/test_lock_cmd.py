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


def test_release_held_by_another_mentions_force(tmp_path, capsys):
    blend = tmp_path / "drive.blend"
    assert cli.main(["lock", "take", str(blend), "--owner", "A"]) == 0
    assert cli.main(["lock", "release", str(blend), "--owner", "B"]) == 1
    assert "--force" in capsys.readouterr().err


@pytest.mark.parametrize("ttl", ["0", "-5"])
def test_take_with_a_ttl_that_is_not_positive_exits_2(tmp_path, capsys, ttl):
    assert cli.main(["lock", "take", str(tmp_path / "drive.blend"), "--ttl", ttl]) == 2
    err = capsys.readouterr().err.strip()
    assert err and "Traceback" not in err and len(err.splitlines()) == 1


def test_a_busy_lock_exits_1_with_one_plain_sentence(tmp_path, capsys, monkeypatch):
    from se2modkit import lease
    from se2modkit.jobqueue import FileLock, QueueError

    class Stuck(FileLock):
        def acquire(self, blocking=True, timeout=60.0):
            raise QueueError(f"Timed out waiting for the queue lock {self.path}.")

    monkeypatch.setattr(lease, "FileLock", Stuck)
    assert cli.main(["lock", "take", str(tmp_path / "drive.blend")]) == 1
    err = capsys.readouterr().err.strip()
    assert len(err.splitlines()) == 1 and err.endswith(".") and "queue lock" not in err
