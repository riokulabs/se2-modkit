"""A synthetic mini-mod for `se2mod test` / `se2modkit.testsel`: a tiny package, a Tests/ dir with an import chain, a
slow-marked test, a data-reading test, and (optionally) a failing test."""

from __future__ import annotations

import subprocess
from pathlib import Path

CONFTEST = '''\
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Source", "Model"))


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: a long check")
    config.addinivalue_line(
        "markers", "build: needs a prior build")
'''

FILES = {
    "Source/Model/pkg/__init__.py": "",
    "Source/Model/pkg/base.py": "VALUE = 1\n",
    "Source/Model/pkg/mid.py": "from . import base\n\n\ndef twice():\n    return base.VALUE * 2\n",
    "Source/Model/pkg/top.py": "import pkg.mid\n\n\ndef four():\n    return pkg.mid.twice() * 2\n",
    "Source/Model/pkg/lonely.py": "X = 0\n",
    "Source/Model/data/table.json": "{\"rows\": 3}\n",
    "Tests/conftest.py": CONFTEST,
    "Tests/helper.py": "from pkg.top import four\n\n\ndef helped():\n    return four()\n",
    "Tests/test_mid.py": "from pkg import mid\n\n\ndef test_twice():\n    assert mid.twice() == 2\n",
    "Tests/test_top.py": ("import helper\nimport pytest\n\n\ndef test_four():\n    assert helper.helped() == 4\n\n\n"
                          "@pytest.mark.slow\ndef test_slow_four():\n    assert helper.helped() == 4\n"),
    "Tests/test_data.py": ("import json\nimport os\n\nTABLE = os.path.join(os.path.dirname(__file__), '..', 'Source', "
                           "'Model', 'data', 'table.json')\n\n\ndef test_rows():\n"
                           "    assert json.load(open(TABLE))['rows'] == 3\n"),
    "Tests/test_plain.py": "def test_plain():\n    assert True\n",
}

FAILING = ("def test_ok():\n    assert True\n\n\ndef test_broken():\n    left = 1\n    assert left == 2, 'left is not two'\n")


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True)
    return r.stdout


def make_mod(root: Path, *, failing: bool = False, repo: bool = False) -> Path:
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    if failing:
        (root / "Tests" / "test_fail.py").write_text(FAILING, encoding="utf-8", newline="\n")
    if repo:
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "feat: first")
    return root
