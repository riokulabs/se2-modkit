"""`se2modkit.testsel`: import-graph test selection, registered markers, changed files, pytest summary parsing."""

import pytest
from minimod import git, make_mod

from se2modkit import testsel


@pytest.fixture
def mod(tmp_path):
    return make_mod(tmp_path / "Mini Mod")


def names(paths):
    return sorted(p.name for p in paths)


def test_graph_maps_source_module_to_transitive_importers(mod):
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    base = mod / "Source/Model/pkg/base.py"
    # base <- mid (relative import) <- top (absolute) <- Tests/helper <- test_top; and mid <- test_mid
    assert names(g.tests_for([base])) == ["test_mid.py", "test_top.py"]
    assert names(g.tests_for([mod / "Source/Model/pkg/top.py"])) == ["test_top.py"]
    assert names(g.tests_for([mod / "Tests/helper.py"])) == ["test_top.py"]


def test_graph_unimported_module_selects_nothing(mod):
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert g.tests_for([mod / "Source/Model/pkg/lonely.py"]) == []


def test_changed_test_file_selects_itself(mod):
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for([mod / "Tests/test_plain.py"])) == ["test_plain.py"]


def test_changed_conftest_selects_every_test_below_it(mod):
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for([mod / "Tests/conftest.py"])) == [
        "test_data.py", "test_mid.py", "test_plain.py", "test_top.py"]


def test_changed_data_file_selects_tests_naming_it(mod):
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for([mod / "Source/Model/data/table.json"])) == ["test_data.py"]


def test_package_init_change_selects_package_users(mod):
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for([mod / "Source/Model/pkg/__init__.py"])) == ["test_mid.py", "test_top.py"]


def test_module_selector_dotted_or_path(mod):
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for_module("pkg.mid")) == ["test_mid.py", "test_top.py"]
    assert names(g.tests_for_module(str(mod / "Source/Model/pkg/top.py"))) == ["test_top.py"]
    with pytest.raises(testsel.SelectionError, match="pkg.nope"):
        g.tests_for_module("pkg.nope")


def test_block_selector_matches_name_tokens(mod):
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    # "top" names pkg/top.py (imported by Tests/helper -> test_top) and test_top.py itself
    assert names(g.tests_for_block("top")) == ["test_top.py"]
    assert names(g.tests_for_block("MID")) == ["test_mid.py"]
    assert g.tests_for_block("zzz") == []
    # block selection follows test-side imports only: base is reached by test_mid/test_top through source modules
    # (mid, top), which a block query does not walk, so "base" names no test
    assert g.tests_for_block("base") == []
    assert names(g.tests_for_block("helper")) == ["test_top.py"]


def test_syntax_error_file_does_not_break_graph(mod):
    (mod / "Source/Model/pkg/bad.py").write_text("def (:\n", encoding="utf-8")
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for([mod / "Source/Model/pkg/base.py"])) == ["test_mid.py", "test_top.py"]


def test_registered_markers_from_conftest_and_ini(mod):
    assert testsel.registered_markers(mod, mod / "Tests") == {"slow", "build"}
    (mod / "pytest.ini").write_text("[pytest]\nmarkers =\n    sdkbuild: real sdk build\n    other\n", encoding="utf-8")
    assert testsel.registered_markers(mod, mod / "Tests") == {"slow", "build", "sdkbuild", "other"}


def test_fast_expression_only_uses_registered_markers():
    assert testsel.fast_expression({"slow", "build", "path_a"}) == "not build and not slow"
    assert testsel.fast_expression(set()) is None


def test_changed_files_working_tree_index_untracked_and_base(tmp_path):
    mod = make_mod(tmp_path / "m", repo=True)
    assert testsel.changed_files(mod) == []
    (mod / "Source/Model/pkg/mid.py").write_text("from . import base\n# edit\n", encoding="utf-8")
    (mod / "Tests/test_new.py").write_text("def test_n():\n    pass\n", encoding="utf-8")
    (mod / "Source/Model/pkg/lonely.py").write_text("X = 2\n", encoding="utf-8")
    git(mod, "add", "Source/Model/pkg/lonely.py")
    got = names(testsel.changed_files(mod))
    assert got == ["lonely.py", "mid.py", "test_new.py"]
    git(mod, "add", "-A")
    git(mod, "commit", "-q", "-m", "feat: second")
    assert testsel.changed_files(mod) == []
    assert names(testsel.changed_files(mod, base="HEAD~1")) == ["lonely.py", "mid.py", "test_new.py"]


def test_changed_files_errors_are_one_sentence(tmp_path):
    with pytest.raises(testsel.SelectionError, match="not a git repository"):
        testsel.changed_files(tmp_path)
    mod = make_mod(tmp_path / "m", repo=True)
    with pytest.raises(testsel.SelectionError, match="nosuchref"):
        testsel.changed_files(mod, base="nosuchref")


@pytest.mark.parametrize("line, want", [
    ("3 passed in 0.12s", {"passed": 3}),
    ("1 failed, 2 passed, 1 skipped, 2 deselected in 1.50s", {"failed": 1, "passed": 2, "skipped": 1, "deselected": 2}),
    ("========= 2 passed, 1 warning in 3.00s =========", {"passed": 2, "warnings": 1}),
    ("1 error in 0.40s", {"errors": 1}),
    ("2 passed, 1 xfailed, 1 xpassed in 0.01s (0:00:00)", {"passed": 2, "xfailed": 1, "xpassed": 1}),
    ("no tests ran in 0.01s", {}),
    ("4 deselected in 0.02s", {"deselected": 4}),
])
def test_parse_summary(line, want):
    out = "noise\n" + line + "\n"
    s = testsel.parse_summary(out)
    assert s is not None
    assert s.counts == want


def test_parse_summary_rejects_garbage():
    assert testsel.parse_summary("collected 3 items\nKeyboardInterrupt\n") is None
    assert testsel.parse_summary("") is None


def test_summary_line_always_has_core_counts():
    s = testsel.parse_summary("2 passed, 1 deselected in 0.5s\n")
    assert s.line() == "2 passed, 0 failed, 0 skipped, 1 deselected in 0.5s"


def test_failure_sections_keep_failures_and_short_summary():
    out = "\n".join([
        "..F.", "=================== FAILURES ===================", "___ test_broken ___",
        "Tests/test_fail.py:7: in test_broken", "E   AssertionError: left is not two",
        "=========== short test summary info ============", "FAILED Tests/test_fail.py::test_broken - Assert",
        "1 failed, 3 passed in 0.10s",
    ])
    sec = testsel.failure_sections(out)
    assert sec[0].startswith("=") and "FAILURES" in sec[0]
    assert "E   AssertionError: left is not two" in sec
    assert sec[-1].startswith("FAILED ")
    assert not any("passed in" in s for s in sec)
    assert testsel.failure_sections("4 passed in 0.1s\n") == []


# --- edge cases: deleted, renamed and string-named modules -------------------------------------------------------


def test_deleted_module_selects_its_importers(tmp_path):
    mod = make_mod(tmp_path / "m", repo=True)
    (mod / "Source/Model/pkg/mid.py").unlink()
    changed = testsel.changed_files(mod)
    assert names(changed) == ["mid.py"] and not changed[0].exists()
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for(changed)) == ["test_mid.py", "test_top.py"]


def test_renamed_module_selects_importers_of_the_old_name(tmp_path):
    mod = make_mod(tmp_path / "m", repo=True)
    git(mod, "mv", "Source/Model/pkg/top.py", "Source/Model/pkg/top2.py")
    changed = testsel.changed_files(mod)
    assert names(changed) == ["top.py", "top2.py"]
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for(changed)) == ["test_top.py"]


def test_python_file_named_in_a_string_selects_the_reader(mod):
    (mod / "Tests/test_reads.py").write_text(
        "import os\nHERE = os.path.dirname(__file__)\n\n\ndef test_src():\n"
        "    assert open(os.path.join(HERE, '..', 'Source', 'Model', 'pkg', 'lonely.py')).read()\n", encoding="utf-8")
    (mod / "Tests/test_dyn.py").write_text(
        "import importlib\n\n\ndef test_dyn():\n    assert importlib.import_module('pkg.base').VALUE == 1\n",
        encoding="utf-8")
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for([mod / "Source/Model/pkg/lonely.py"])) == ["test_reads.py"]
    assert "test_dyn.py" in names(g.tests_for([mod / "Source/Model/pkg/base.py"]))


def test_conftest_imports_select_every_test_below_it(mod):
    conf = mod / "Tests/conftest.py"
    conf.write_text(conf.read_text(encoding="utf-8") + "\nimport pkg.lonely  # noqa\n", encoding="utf-8")
    g = testsel.ImportGraph.build(mod, mod / "Tests")
    assert names(g.tests_for([mod / "Source/Model/pkg/lonely.py"])) == [
        "test_data.py", "test_mid.py", "test_plain.py", "test_top.py"]


def test_parse_summary_takes_the_last_summary_line():
    out = "\n".join(["____ test_x ____", "----- Captured stdout call -----", "99 passed in 1.00s",
                     "==== short test summary info ====", "FAILED t.py::test_x", "1 failed, 2 passed in 0.30s",
                     "INTERNALERROR> Traceback (most recent call last):", *[f"INTERNALERROR>   line {i}" for i in range(40)]])
    s = testsel.parse_summary(out)
    assert s.counts == {"failed": 1, "passed": 2}
