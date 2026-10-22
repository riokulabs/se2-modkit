"""`se2mod review-pack`: commits + stat + source diff, generated paths collapsed to one line each."""

import subprocess

import pytest


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "my repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "Assets").mkdir()
    (r / "src").mkdir()
    (r / "src" / "code.py").write_text("a = 1\n" * 30, encoding="utf-8")
    (r / "src" / "with space.py").write_text("x = 1\n", encoding="utf-8")
    (r / "Assets" / "x.def").write_text("{}\n", encoding="utf-8")
    (r / "model.fbx").write_bytes(b"\x00\x01\x02")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "feat: first")
    git(r, "branch", "base")
    (r / "src" / "code.py").write_text("a = 1\n" * 15 + "b = 2\n" + "a = 1\n" * 15, encoding="utf-8")
    (r / "src" / "with space.py").write_text("x = 2\n", encoding="utf-8")
    (r / "Assets" / "x.def").write_text("".join(f'"k{i}": {i},\n' for i in range(500)), encoding="utf-8")
    (r / "model.fbx").write_bytes(b"\x00\x09\x08\x07")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "feat: second change")
    return r


def test_pack_contents(run, repo, tmp_path):
    out = tmp_path / "pack.txt"
    code, stdout, err = run("review-pack", str(repo), "base", "HEAD", "--out", str(out))
    assert code == 0, err
    text = out.read_text(encoding="utf-8")
    assert stdout.strip() == f"{out} {len(text.splitlines())}"
    assert "feat: second change" in text and "feat: first" not in text
    assert "## Stat" in text and "src/code.py" in text
    assert "+b = 2" in text and "-x = 1" in text and "+x = 2" in text  # full diff, incl. a path with a space
    assert "k499" not in text  # no diff body for the generated def
    assert text.count("Assets/x.def | +500 -1") == 1
    assert text.count("model.fbx | binary") == 1
    assert "@@" in text and "diff --git a/Assets" not in text and "diff --git a/model.fbx" not in text


def test_extra_exclude_collapses_more(run, repo, tmp_path):
    out = tmp_path / "pack.txt"
    code, *_ = run("review-pack", str(repo), "base", "HEAD", "--out", str(out), "--exclude", "src/with*")
    text = out.read_text(encoding="utf-8")
    assert code == 0 and "+x = 2" not in text and text.count("src/with space.py | +1 -1") == 1


def test_binary_source_file_is_noted_not_dumped(run, repo, tmp_path):
    (repo / "blob.dat").write_bytes(b"\x00\xff\x00")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: blob")
    out = tmp_path / "pack.txt"
    code, *_ = run("review-pack", str(repo), "base", "HEAD", "--out", str(out))
    assert code == 0 and "Binary files" in out.read_text(encoding="utf-8")


def test_base_not_an_ancestor(run, repo, tmp_path):
    git(repo, "checkout", "-q", "-b", "side", "base")
    (repo / "side.txt").write_text("s\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "side")
    out = tmp_path / "pack.txt"
    code, stdout, err = run("review-pack", str(repo), "main", "side", "--out", str(out))
    assert code == 1 and stdout == "" and not out.exists()
    assert "not an ancestor" in err and len(err.strip().splitlines()) == 1 and "Traceback" not in err


def test_bad_ref_and_not_a_repo(run, repo, tmp_path):
    out = tmp_path / "pack.txt"
    code, _, err = run("review-pack", str(repo), "nosuchref", "HEAD", "--out", str(out))
    assert code == 1 and "nosuchref" in err and len(err.strip().splitlines()) == 1
    plain = tmp_path / "plain"
    plain.mkdir()
    code, _, err = run("review-pack", str(plain), "a", "b", "--out", str(out))
    assert code == 1 and "not a git repository" in err


def test_no_source_changes_does_not_dump_everything(run, tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "Assets").mkdir()
    (r / "Assets" / "a.def").write_text("1\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "one")
    (r / "Assets" / "a.def").write_text("2\n", encoding="utf-8")
    git(r, "commit", "-q", "-am", "two")
    out = tmp_path / "p.txt"
    code, *_ = run("review-pack", str(r), "HEAD~1", "HEAD", "--out", str(out))
    text = out.read_text(encoding="utf-8")
    assert code == 0 and "(no source changes)" in text and "+2" not in text


def test_is_generated_defaults():
    from se2modkit.cli.review_cmd import DEFAULT_EXCLUDES, is_generated
    for p in ("Assets/a/b.def", "x.fbx", "a/b/c.gltf", "m.bin", "Foo/Models/a.txt", "Models/a.txt", "Content/z",
              "poetry.lock", "sub/Cargo.lock"):
        assert is_generated(p, DEFAULT_EXCLUDES), p
    for p in ("src/a.py", "docs/Assetsnote.md", "README.md"):
        assert not is_generated(p, DEFAULT_EXCLUDES), p
