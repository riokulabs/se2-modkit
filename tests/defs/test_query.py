"""`defs show` (bounded excerpts) and `chain resolve --fields`, over hand-written synthetic definitions."""

import json

import pytest

G1 = "40000000-0000-4000-8000-000000000001"
G2 = "40000000-0000-4000-8000-000000000002"
T = "Game2:Keen.Game2.Test.WidgetDefinitionObjectBuilder"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "Content"
    (r / "Blocks").mkdir(parents=True)
    value = {"Guid": G1, "Name": "Widget", "Items": [{"Id": i, "Tag": f"t{i}"} for i in range(40)],
             "Nested": {"Deep": {"Leaf": 7}}}
    (r / "Blocks" / "W.def").write_text(json.dumps({"$Type": T, "$Value": value}), encoding="utf-8")
    return r


def test_show_by_guid_is_bounded(run, root):
    code, out, err = run("defs", "--root", str(root), "show", G1, "--max", "10")
    assert code == 0, err
    lines = out.splitlines()
    assert len(lines) == 12 and lines[0].startswith("# ") and lines[-1].startswith("… ") and lines[-1].endswith("more lines (use --max)")


def test_show_default_cap_is_60(run, root):
    code, out, _ = run("defs", "--root", str(root), "show", G1)
    assert code == 0 and len(out.splitlines()) == 62


def test_show_path_selects_subtree(run, root):
    code, out, _ = run("defs", "--root", str(root), "show", G1, "--path", "Nested.Deep.Leaf")
    assert code == 0 and json.loads(out) == 7
    code, out, _ = run("defs", "--root", str(root), "show", G1, "--path", "Items.3.Tag")
    assert code == 0 and json.loads(out) == "t3"
    code, out, _ = run("defs", "--root", str(root), "show", G1, "--path", "Items.0")
    assert json.loads(out) == {"Id": 0, "Tag": "t0"}


def test_show_file_path(run, root):
    code, out, err = run("defs", "show", str(root / "Blocks" / "W.def"), "--path", "Name")
    assert code == 0, err
    assert json.loads(out) == "Widget"


@pytest.mark.parametrize("extra,word", [(["--path", "Nope"], "Nope"), (["--path", "Items.99"], "99"),
                                        (["--path", "Items.x"], "x"), (["--path", "Name.deeper"], "deeper")])
def test_show_bad_path_is_one_sentence(run, root, extra, word):
    code, out, err = run("defs", "--root", str(root), "show", G1, *extra)
    assert code == 1 and out == ""
    assert len(err.strip().splitlines()) == 1 and word in err and "Traceback" not in err


def test_show_bad_guid_and_unknown_guid(run, root):
    code, _, err = run("defs", "--root", str(root), "show", "not-a-guid")
    assert code == 1 and len(err.strip().splitlines()) == 1 and "GUID" in err
    code, _, err = run("defs", "--root", str(root), "show", "40000000-0000-4000-8000-0000000000ff")
    assert code == 1 and len(err.strip().splitlines()) == 1 and "not declared" in err


def test_show_max_must_be_positive(run, root):
    code, _, err = run("defs", "--root", str(root), "show", G1, "--max", "0")
    assert code == 1 and len(err.strip().splitlines()) == 1


# --- chain resolve --fields -------------------------------------------------------------------------------------


def test_chain_resolve_fields(run, root):
    code, out, err = run("chain", "--root", str(root), "resolve", G1, "--fields", "name,type,file")
    assert code == 0, err
    assert out.splitlines() == ["name: Widget", "type: WidgetDefinition", "file: Blocks/W.def"]


def test_chain_resolve_unknown_field(run, root):
    code, out, err = run("chain", "--root", str(root), "resolve", G1, "--fields", "name,bogus")
    assert code == 1 and out == "" and len(err.strip().splitlines()) == 1 and "bogus" in err


def test_chain_resolve_bad_guid_with_fields(run, root):
    code, _, err = run("chain", "--root", str(root), "resolve", "zzz", "--fields", "name")
    assert code == 1 and len(err.strip().splitlines()) == 1


def test_chain_resolve_without_fields_unchanged(run, root):
    code, out, _ = run("chain", "--root", str(root), "resolve", G1)
    assert code == 0 and out.startswith("# Blocks/W.def")


def test_chain_resolve_name_falls_back_to_file_stem(run, root):
    g = "40000000-0000-4000-8000-000000000003"
    (root / "Blocks" / "Nameless.def").write_text(json.dumps({"$Type": T, "$Value": {"Guid": g}}), encoding="utf-8")
    code, out, _ = run("chain", "--root", str(root), "resolve", g, "--fields", "name")
    assert code == 0 and out == "name: Nameless\n"
