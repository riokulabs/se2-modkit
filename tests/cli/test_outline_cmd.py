"""`se2mod outline FILE [--symbol NAME]`: an ast map of a Python file, and bounded source of one symbol."""

import pytest

SRC = '''\
"""Doc."""
import os

LIMIT = 10
names: list[str] = []
A, B = 1, 2


def plain(a, b=2, *args, **kw):
    return a + b


@decorated
def wrapped(x):
    """Doc."""
    return x


class Thing(Base):
    KIND = "t"

    def __init__(self, n):
        self.n = n

    @property
    def size(self):
        return self.n

    class Inner:
        def deep(self):
            pass


if os.name == "nt":
    def windows_only():
        pass

async def fetch():
    pass
'''


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "my mod" / "sample.py"
    p.parent.mkdir()
    p.write_text(SRC, encoding="utf-8")
    return p


def test_outline_lists_top_level_with_ranges(run, src):
    code, out, err = run("outline", str(src))
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].endswith("sample.py (39 lines)")
    assert "L4-6 LIMIT, names, A, B" in out  # runs of module-level names share one line
    assert "L9-10 def plain(a, b=2, *args, **kw)" in out
    assert "L13-16 @decorated def wrapped(x)" in out
    assert "L19-31 class Thing(Base)" in out
    assert "  L20 KIND" in out
    assert "  L22-23 def __init__(self, n)" in out
    assert "  L25-27 @property def size(self)" in out
    assert "  L29-31 class Inner" in out
    assert "    L30-31 def deep(self)" in out
    assert "L35-36 def windows_only()" in out
    assert "L38-39 async def fetch()" in out
    assert "import" not in out.split("\n", 1)[1]


def test_long_name_runs_wrap(run, tmp_path):
    p = tmp_path / "consts.py"
    p.write_text("".join(f"CONSTANT_NUMBER_{i} = {i}\n" for i in range(30)), encoding="utf-8")
    code, out, err = run("outline", str(p))
    assert code == 0
    lines = out.splitlines()[1:]
    assert 1 < len(lines) < 10 and all(len(ln) <= 120 for ln in lines)
    assert lines[0].startswith("L1-") and "CONSTANT_NUMBER_0, CONSTANT_NUMBER_1" in lines[0]
    assert lines[-1].endswith("-30 CONSTANT_NUMBER_29") or "CONSTANT_NUMBER_29" in lines[-1]


def test_symbol_prints_numbered_source(run, src):
    code, out, err = run("outline", str(src), "--symbol", "plain")
    assert code == 0, err
    lines = out.splitlines()
    assert "L9-10" in lines[0]
    assert lines[1].split() == ["9", "def", "plain(a,", "b=2,", "*args,", "**kw):"]
    assert len(lines) == 3


def test_symbol_method_and_constant(run, src):
    code, out, err = run("outline", str(src), "--symbol", "Thing.size")
    assert code == 0, err
    assert "@property" in out and "return self.n" in out and "KIND" not in out
    code, out, err = run("outline", str(src), "--symbol", "LIMIT")
    assert code == 0 and "LIMIT = 10" in out


def test_symbol_is_bounded(run, tmp_path):
    p = tmp_path / "big.py"
    p.write_text("def big():\n" + "    x = 1\n" * 500, encoding="utf-8")
    code, out, err = run("outline", str(p), "--symbol", "big", "--max-lines", "50")
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 52
    assert "451 more lines" in lines[-1] and "sed -n" in lines[-1]


def test_unknown_symbol_suggests(run, src):
    code, out, err = run("outline", str(src), "--symbol", "plian")
    assert code == 1
    assert "plain" in err and len(err.strip().splitlines()) == 1


@pytest.mark.parametrize("name, content, needle", [
    ("notes.md", "# hi\n", "not a Python file"),
    ("broken.py", "def (:\n", "line 1"),
])
def test_bad_inputs_are_one_sentence(run, tmp_path, name, content, needle):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    code, out, err = run("outline", str(p))
    assert code == 1
    assert needle in err and "Traceback" not in err and len(err.strip().splitlines()) == 1


def test_missing_file(run, tmp_path):
    code, out, err = run("outline", str(tmp_path / "nope.py"))
    assert code == 1 and "nope.py" in err and len(err.strip().splitlines()) == 1
