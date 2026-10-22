"""`se2mod defs`: the se2defs explorer over a synthetic content root."""

import json

import pytest

G_A = "30000000-0000-4000-8000-000000000001"
G_B = "30000000-0000-4000-8000-000000000002"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "Content"
    (r / "Blocks").mkdir(parents=True)
    (r / "Blocks" / "A.def").write_text(json.dumps({"$Type": "Game2:Keen.Game2.Test.ADefinitionObjectBuilder",
                                                    "$Value": {"Guid": G_A}}), encoding="utf-8")
    (r / "Blocks" / "B.def").write_text(json.dumps({"$Type": "Game2:Keen.Game2.Test.BDefinitionObjectBuilder",
                                                    "$Value": {"Guid": G_B, "Ref": G_A}}), encoding="utf-8")
    return r


def test_types_guid_show(run, root):
    code, out, _ = run("defs", "--root", str(root), "types")
    assert code == 0 and "Keen.Game2.Test.ADefinitionObjectBuilder" in out
    code, out, _ = run("defs", "--root", str(root), "guid", G_A)
    lines = sorted(out.splitlines())
    assert lines == ["DEFINES Blocks/A.def  [Keen.Game2.Test.ADefinitionObjectBuilder]",
                     "refs    Blocks/B.def  [Keen.Game2.Test.BDefinitionObjectBuilder]"]
    code, out, _ = run("defs", "--root", str(root), "show", G_B)
    assert code == 0 and json.loads(out.split("\n", 1)[1])["$Value"]["Ref"] == G_A
    code, _, err = run("defs", "--root", str(root), "show", "nope")
    assert code == 1 and "GUID" in err


def test_default_root_needs_game(run):
    code, _, err = run("defs", "types")
    assert code == 1 and "Space Engineers 2 not found" in err and "Traceback" not in err
