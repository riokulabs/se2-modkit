"""`pipeline.report`: the build_report.json writer (its top-level "se2modkit" provenance key, which `se2mod
snapshot` ignores) and the per-block entry / merge rules of a build run."""

import json

import se2modkit
from se2modkit import snapshot
from se2modkit.pipeline import report as R


class _Spec:
    def __init__(self, name, cells=(1, 1, 1)):
        self.name, self.cells = name, cells


def test_report_has_modkit_provenance(tmp_path):
    path = tmp_path / "out" / "build_report.json"
    R.write(path, {"blocks": {"XX_A": {"lods": []}}}, modkit_provenance={"version": "9.9", "commit": "abc"})
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["se2modkit"] == {"version": "9.9", "commit": "abc"} and data["blocks"] == {"XX_A": {"lods": []}}
    assert text.endswith("}\n") and "\r" not in text
    R.write(path, {"blocks": {}})                                   # default: this modkit's own provenance
    prov = json.loads(path.read_text(encoding="utf-8"))["se2modkit"]
    assert prov["version"] == se2modkit.__version__ and set(prov) == {"version", "commit"}
    assert prov["commit"] is None or len(prov["commit"]) == 40


def test_provenance_key_is_ignored_by_snapshot(tmp_path):
    path = tmp_path / "build_report.json"
    R.write(path, {"blocks": {}}, modkit_provenance={"version": "1", "commit": "a" * 40})
    one = snapshot._report(path, ("se2modkit",))
    R.write(path, {"blocks": {}}, modkit_provenance={"version": "2", "commit": "b" * 40})
    assert snapshot._report(path, ("se2modkit",)) == one


def test_writer_is_pluggable(tmp_path):
    seen = []
    R.write(tmp_path / "r.json", {"blocks": {}}, modkit_provenance={"version": "1", "commit": None},
            writer=lambda p, d: seen.append((p, d)))
    assert seen and seen[0][1]["se2modkit"]["version"] == "1"


def test_empty_entry_and_merge():
    a, b, c = _Spec("XX_A"), _Spec("XX_B", (2, 1, 1)), _Spec("XX_C")
    assert R.empty_entry(b) == {"cells": [2, 1, 1], "lods": [], "placement": {"occupied": 0, "total": 0, "faces": {},
                                                                                "free_cells": []},
                                "dummies": [], "provenance": []}
    built = R.empty_entry(c)
    built["placement"]["total"] = 8
    old = {"XX_C": built, "XX_Gone": dict(built)}
    # the pipeline's blocks always get an entry; another catalogue block keeps its entry once built; the rest go
    assert R.merged_blocks(old, [a, b, c], [a, b]) == {"XX_A": R.empty_entry(a), "XX_B": R.empty_entry(b),
                                                      "XX_C": built}
