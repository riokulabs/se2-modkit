"""`se2mod facts build`: type facts from a decompiled game, into the user cache only."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validate"))
from synth import write_decompile  # noqa: E402


def test_build_writes_into_the_cache(run, tmp_path):
    dec = write_decompile(tmp_path / "dec")
    code, out, err = run("facts", "build", "--decompiled", str(dec), "--game-version", "0.0.0.1")
    assert code == 0, out + err
    p = tmp_path / "home" / ".cache" / "se2modkit" / "facts" / "0.0.0.1.json"
    assert json.loads(p.read_text())["game_version"] == "0.0.0.1"
    assert str(p) in out and "types" in out


def test_decompiled_from_the_environment(run, tmp_path):
    dec = write_decompile(tmp_path / "dec")
    code, out, err = run("facts", "build", "--game-version", "0.0.0.2", environ={"SE2_DECOMPILED": str(dec)})
    assert code == 0, out + err
    assert (tmp_path / "home" / ".cache" / "se2modkit" / "facts" / "0.0.0.2.json").is_file()


def test_no_decompile_is_one_sentence(run):
    code, out, err = run("facts", "build", "--game-version", "0.0.0.1")
    assert code == 1 and "SE2_DECOMPILED" in err and "Traceback" not in err


def test_no_game_version_is_one_sentence(run, tmp_path):
    code, out, err = run("facts", "build", "--decompiled", str(write_decompile(tmp_path / "dec")))
    assert code == 1 and "--game-version" in err


def test_refuses_a_cache_inside_a_repo(run, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    dec = write_decompile(tmp_path / "dec")
    code, out, err = run("facts", "build", "--decompiled", str(dec), "--game-version", "0.0.0.1",
                         environ={"XDG_CACHE_HOME": str(repo / "cache")})
    assert code == 1 and "git work tree" in err
    assert not (repo / "cache").exists()


def test_refuses_a_decompile_tree_as_output(run, tmp_path):
    """The facts never land beside the decompile: only the cache path is ever written."""
    dec = write_decompile(tmp_path / "dec")
    before = sorted(p.relative_to(dec) for p in dec.rglob("*"))
    run("facts", "build", "--decompiled", str(dec), "--game-version", "0.0.0.1")
    assert sorted(p.relative_to(dec) for p in dec.rglob("*")) == before
