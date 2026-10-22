from pathlib import Path

import pytest

from se2modkit.publish.project import Refusal
from se2modkit.publish.publish import preflight_live


def _proc(root: Path, pid: int, comm: str, argv: list[str]) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n", encoding="utf-8")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


def test_refuses_when_process_list_unavailable(tmp_path):
    with pytest.raises(Refusal) as e:
        preflight_live(proc=tmp_path, environ={})  # empty listing: cannot tell whether the game runs
    msg = str(e.value)
    assert "\n" not in msg and "process" in msg.lower()


def test_refuses_without_listing_even_when_running_game_allowed(tmp_path):
    with pytest.raises(Refusal):
        preflight_live(True, proc=tmp_path, environ={})


def test_passes_when_nothing_relevant_runs(tmp_path):
    _proc(tmp_path, 10, "bash", ["bash"])
    preflight_live(proc=tmp_path, environ={})


def test_refuses_running_game(tmp_path):
    _proc(tmp_path, 10, "Modern.bin", ["/x/Modern.bin"])
    with pytest.raises(Refusal, match="running"):
        preflight_live(proc=tmp_path, environ={})
    preflight_live(True, proc=tmp_path, environ={})


def test_honours_se2_process_name(tmp_path):
    _proc(tmp_path, 10, "MyBuild.bin", ["MyBuild.bin"])
    preflight_live(proc=tmp_path, environ={})
    with pytest.raises(Refusal, match="running"):
        preflight_live(proc=tmp_path, environ={"SE2_PROCESS_NAME": "MyBuild.bin"})


def test_refuses_running_hub(tmp_path):
    _proc(tmp_path, 10, "VRAGE3.ModHUB.e", ["C:\\x\\VRAGE3.ModHUB.exe"])
    with pytest.raises(Refusal, match="Mod HUB"):
        preflight_live(True, proc=tmp_path, environ={})
