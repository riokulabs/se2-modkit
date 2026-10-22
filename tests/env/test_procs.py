from pathlib import Path

from se2modkit.env import procs


def _proc(root: Path, pid: int, comm: str, argv: list[str]) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n", encoding="utf-8")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


def test_find_processes_by_comm_and_wine_argv0(tmp_path):
    _proc(tmp_path, 10, "Modern.bin", ["/x/Pulsar/Modern.bin", "-plugin"])
    _proc(tmp_path, 11, "SpaceEngineers2", ["Z:\\games\\SpaceEngineers2.exe", "-skipintro"])  # comm truncated
    _proc(tmp_path, 12, "bash", ["bash"])
    (tmp_path / "self").mkdir()
    hits = procs.find_processes(("modern.bin", "SpaceEngineers2.exe"), proc=tmp_path)
    assert sorted(pid for pid, _ in hits) == [10, 11]


def test_game_running(tmp_path):
    assert procs.game_running(proc=tmp_path, environ={}) is False
    _proc(tmp_path, 20, "Interim.bin", ["Interim.bin"])
    assert procs.game_running(proc=tmp_path, environ={}) is True


def test_game_running_honours_process_name(tmp_path):
    _proc(tmp_path, 30, "MyBuild.bin", ["MyBuild.bin"])
    assert procs.game_running(proc=tmp_path, environ={}) is False
    assert procs.game_running(proc=tmp_path, environ={"SE2_PROCESS_NAME": "MyBuild.bin"}) is True


def test_windows_tasklist_parse():
    out = '"SpaceEngineers2.exe","4242","Console","1","1.234.567 K"\r\n"explorer.exe","7","Console","1","9 K"\r\n'
    assert procs._parse_tasklist(out) == [
        (4242, "SpaceEngineers2.exe", "SpaceEngineers2.exe", "SpaceEngineers2.exe"),
        (7, "explorer.exe", "explorer.exe", "explorer.exe"),
    ]


def test_running_processes_keeps_three_tuple(tmp_path):
    _proc(tmp_path, 50, "x", ["/a b/x", "-y"])
    assert procs.running_processes(proc=tmp_path) == [(50, "x", "/a b/x -y")]


def test_constants():
    assert "Modern.bin" in procs.GAME_PROCS
    assert "VRAGE3.ModHUB.exe" in procs.HUB_PROCS


def test_wine_process_with_spaces_in_path(tmp_path):
    _proc(tmp_path, 40, "VRAGE3.ModHUB.e", ["S:\\steamapps\\common\\Space Engineers 2 - Mod SDK\\VRAGE3.ModHUB.exe"])
    hits = procs.find_processes(procs.HUB_PROCS, proc=tmp_path)
    assert [pid for pid, _ in hits] == [40]


def test_game_running_wine_path_with_spaces(tmp_path):
    _proc(tmp_path, 41, "SpaceEngineers2",
          ["Z:\\mnt\\Games Drive\\SteamLibrary\\steamapps\\common\\SpaceEngineers2\\Game2\\SpaceEngineers2.exe",
           "-skipintro"])
    assert procs.game_running(proc=tmp_path, environ={}) is True


def test_argv0_with_spaces_matches_even_if_comm_differs(tmp_path):
    _proc(tmp_path, 42, "wine64-preloader", ["C:\\Program Files\\Some Dir\\VRage.Editor.exe", "-x"])
    assert [pid for pid, _ in procs.find_processes(procs.HUB_PROCS, proc=tmp_path)] == [42]
