import json
import re

import pytest

from se2modkit.diag import locations as paths
from se2modkit.diag import probes

ALL = probes.list_probes()


def test_probe_set():
    assert {"status", "perf", "stats", "patches", "inspect", "inventory", "modcheck", "render"} <= set(ALL)


@pytest.mark.parametrize("value", ['plain', 'a"b', "back\\slash", "new\nline\r\t", " sep\u0085", "emoji \U0001F680",
                                   "'); Emit(1); //", "\x00\x1f"])
def test_cs_string_roundtrip(value):
    lit = probes.cs_string(value)
    assert lit.startswith('"') and lit.endswith('"')
    body = lit[1:-1]
    # no raw quote, newline or control character can survive
    assert not re.search(r'(?<!\\)"', body)
    assert not any(ord(ch) < 0x20 or ch in "  \u0085" for ch in body)
    # decode C# escapes back (\uXXXX, \", \\) and compare
    decoded = re.sub(r'\\u([0-9a-f]{4})|\\(["\\])', lambda m: chr(int(m.group(1), 16)) if m.group(1) else m.group(2), body)
    decoded = decoded.encode("utf-16", "surrogatepass").decode("utf-16")
    assert decoded == value


@pytest.mark.parametrize("name", ALL)
def test_every_probe_builds(name):
    code = probes.build(name, {"seconds": 3, "target": 'name:"x"'})
    assert probes.PARAMS_MARKER not in code
    assert "//#include" not in code
    assert '__params["__probe"] = "%s";' % name in code
    assert code.count("Emit(") >= 2          # the prelude's definition + at least one call
    assert "yield return" in code or "Emit(" in code


def test_params_are_escaped():
    code = probes.build("status", {"evil": '"; System.IO.File.Delete("x"); "'})
    assert '__params["evil"] = "\\"; System.IO.File.Delete(\\"x\\"); \\"";' in code


@pytest.mark.parametrize("key", ["a b", "1x", "x;y", "", "x\"y"])
def test_bad_param_names(key):
    with pytest.raises(probes.ProbeError):
        probes.build("status", {key: "1"})


def test_bad_probe_names():
    for n in ["../x", "_prelude", "Status", "x/y"]:
        with pytest.raises(probes.ProbeError):
            probes.build(n)


def test_include_rules(tmp_path):
    (tmp_path / "_prelude.csx").write_text("/*@@PARAMS@@*/\n")
    (tmp_path / "_a.csx").write_text("int A = 1;\n")
    (tmp_path / "_nested.csx").write_text("//#include _a.csx\n")
    (tmp_path / "ok.csx").write_text("//#include _a.csx\nEmit(A);\n")
    (tmp_path / "bad.csx").write_text("//#include ../secret.csx\n")
    (tmp_path / "nest.csx").write_text("//#include _nested.csx\n")
    assert "int A = 1;" in probes.build("ok", probes_dir=tmp_path)
    with pytest.raises(probes.ProbeError):
        probes.build("bad", probes_dir=tmp_path)
    with pytest.raises(probes.ProbeError):
        probes.build("nest", probes_dir=tmp_path)


def test_parse_output_ok():
    text = "some note\n@@SE2DIAG@@" + json.dumps({"probe": "p", "ok": True, "elapsed_ms": 2.5, "missing": ["T.x"],
                                                 "notes": ["n"], "data": {"a": [1, 2]}}) + "\n"
    r = probes.parse_output("p", text)
    assert r.data == {"a": [1, 2]} and r.missing == ["T.x"] and r.console == ["some note"] and r.elapsed_ms == 2.5


def test_parse_output_compile_error():
    with pytest.raises(probes.ProbeError, match="did not compile"):
        probes.parse_output("p", "(12,5): error CS0103: The name 'Foo' does not exist", is_error=True)


def test_parse_output_no_marker():
    with pytest.raises(probes.ProbeError, match="no result"):
        probes.parse_output("p", "Exception: boom", is_error=True)


# ---- read-only lint: probes only call reflection targets on an allow-list and never use mutating APIs ----
FORBIDDEN = [r"\.SetValue\(", r"new\s+Harmony\(", r"\.Patch\(", r"\bUnpatch", r"MarkEntityForClose", r"AddChild\(",
             r"RemoveChild\(", r"SetFractureIntegrity", r"SetMeshPartsVisibility", r"\.Set\(", r"\.Update\(",
             r"SetJobEnabled", r"GC\.Collect\(", r"File\.(Write|Delete|Move|Copy)", r"Directory\.(Delete|Move|Create)",
             r"\+=\s*\(?\w*\s*=>", r"Process\.Start", r"Environment\.Exit"]
ALLOWED_INVOKED = {"GetAllPatchedMethods", "GetPatchInfo", "GetWorldTransform", "GetDefinitionsOfTypeAndDescendants",
                   "TryGetConfiguration"}


@pytest.mark.parametrize("path", sorted(paths.PROBES_DIR.glob("*.csx")), ids=lambda p: p.name)
def test_probes_are_read_only(path):
    text = re.sub(r"//.*", "", path.read_text())
    for rx in FORBIDDEN:
        assert not re.search(rx, text), f"{path.name}: forbidden pattern {rx}"
    named = set(re.findall(r'GetMethod\("(\w+)"', text)) | set(re.findall(r'x\.Name == "(\w+)"', text)) \
        | set(re.findall(r'm\.Name == "(\w+)"', text))
    assert named <= ALLOWED_INVOKED, f"{path.name}: reflection call targets {named - ALLOWED_INVOKED} not allow-listed"
