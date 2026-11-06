"""The Blender half of `se2mod describe`: run with the .blend open, `-- SPEC.json STATUS.json`. Reads the spec
({"source", "collections", "envelope", "targets"}), writes {"describe": report} to STATUS.json and never saves the
.blend. A collection that is not in the file fails the job with the names it has."""

import json
import sys

import bpy  # pylint: disable=import-error

from se2modkit import describe as D


def main(argv) -> int:
    spec_path, status_path = argv
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    rep = D.describe_scene(spec.get("collections") or None, envelope=spec.get("envelope"),
                           targets=spec.get("targets"), source=spec.get("source"))
    if rep["missing"]:
        have = ", ".join(sorted(c.name for c in bpy.data.collections)) or "none"
        rep = {"error": f"no collection named {', '.join(rep['missing'])} in the file; it has: {have}"}
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump({"describe": rep}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:]))
