import sys

import pytest


@pytest.mark.parametrize("argv", [("sheet", "x.png"), ("pair", "a.png", "b.png")])
def test_missing_pillow_hint(run, tmp_path, monkeypatch, argv):
    monkeypatch.setitem(sys.modules, "PIL", None)  # `from PIL import ...` raises ImportError
    code, so, se = run(*argv, "--out", str(tmp_path / "o.png"))
    assert code == 1 and so == ""
    assert "pip install 'se2-modkit[preview]'" in se and se.count("\n") == 1
