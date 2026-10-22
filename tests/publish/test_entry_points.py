"""`se2mod publish ...` hands over to the same `main` as the `se2publish` script; the worktree gate says why."""
import tomllib
from pathlib import Path

import pytest
from pubfix import git

from se2modkit import cli as se2mod
from se2modkit.publish import cli
from se2modkit.publish.project import Refusal, check_canonical, load_project


def test_se2mod_publish_delegates(mod, monkeypatch, capsys):
    seen = []
    real = cli.main
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(argv) or real(argv))
    assert se2mod.main(["publish", "ledger", str(mod)]) == 0
    assert seen == [["ledger", str(mod)]] and "no publishes recorded" in capsys.readouterr().out
    assert se2mod.main(["publish"]) == 0 and "se2publish" in capsys.readouterr().out
    assert "publish" in se2mod.commands()


def test_se2publish_script_entry_point():
    data = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["se2publish"] == "se2modkit.publish.cli:main"


def test_worktree_refusal_names_the_reason(mod):
    wt = mod.parent / "TestMod-wt"
    git(mod, "worktree", "add", "-q", str(wt))
    with pytest.raises(Refusal) as e:
        check_canonical(load_project(wt))
    msg = str(e.value)
    assert "refused on purpose" in msg and "ledger" in msg and msg.count(". ") == 0
