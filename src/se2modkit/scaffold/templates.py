"""The scaffold's file templates (`files/*.tmpl`, `string.Template` placeholders) and the files they become."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from string import Template

FILES_DIR = Path(__file__).resolve().parent / "files"
MODKIT_GIT = "https://github.com/riokulabs/se2-modkit"

GITATTRIBUTES = "".join(f"*.{ext} filter=lfs diff=lfs merge=lfs -text\n"
                        for ext in ("fbx", "png", "dds", "tif", "tga", "gltf", "bin", "wav", "ogg", "mwm"))


# The shared gates a new mod runs (Tests/test_gates.py, se2modkit.testing.gates). `bundles` fails on an Assets/ with
# no asset file (it would check nothing), so the mod adds it once it has definitions; the others need a build, the SDK
# or the game's type facts and are listed once the mod has them.
DEFAULT_GATES = '["json", "guids"]'

def text(name: str) -> str:
    return (FILES_DIR / name).read_text(encoding="utf-8")


def render(name: str, values: dict[str, str]) -> str:
    """`files/<name>` with every placeholder filled; a missing value is an error, not an empty string."""
    return Template(text(name)).substitute(values)


GITIGNORE = text("gitignore.tmpl")


def modkit_pin(version: str) -> str:
    """The modkit pin for a new mod: the release tag `v<version>` of the modkit repo (never a branch)."""
    return f"se2-modkit[publish] @ git+{MODKIT_GIT}@v{version}"


def values(*, name: str, prefix: str, pkg: str, profile: str, dist: str, modkit_version: str,
           library: bool) -> dict[str, str]:
    build_script = "build_" + "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    vrgproj = f"{name}.vrgproj"
    kind = "library mod" if library else "mod"
    return {
        "name": name, "prefix": prefix, "pkg": pkg, "profile": profile, "dist": dist,
        "modkit_version": modkit_version, "modkit_pin": modkit_pin(modkit_version), "build_script": build_script,
        "vrgproj": vrgproj,
        "description": f"Space Engineers 2 {kind}: {name}.",
        "name_toml": json.dumps(name), "name_py": json.dumps(name), "prefix_py": json.dumps(prefix),
        "vrgproj_py": json.dumps(vrgproj), "name_shell": shlex.quote(name),
        "gates": DEFAULT_GATES,
    }


def mod_files(v: dict[str, str], *, library: bool) -> dict[str, str]:
    """Relative path -> content for one mod's own files (not the repo-level ones)."""
    if library:
        return {
            "README.md": render("readme_library.md.tmpl", v),
            "CHANGELOG.md": render("changelog.md.tmpl", v),
            "Assets/.gitkeep": "",
            "Scripts/.gitkeep": "",
            "Tests/test_smoke.py": render("test_smoke_library.py.tmpl", v),
        }
    pkg = v["pkg"]
    return {
        "README.md": render("readme_mod.md.tmpl", v),
        "CHANGELOG.md": render("changelog.md.tmpl", v),
        "Assets/.gitkeep": "",
        f"Source/Model/{pkg}/__init__.py": render("model_init.py.tmpl", v),
        f"Source/Model/{pkg}/hooks.py": render("model_hooks.py.tmpl", v),
        f"Source/Model/{pkg}/catalog.py": render("model_catalog.py.tmpl", v),
        f"Source/Model/{v['build_script']}.py": render("build.py.tmpl", v),
        "Tests/conftest.py": text("tests_conftest.py.tmpl"),
        "Tests/test_smoke.py": render("test_smoke_mod.py.tmpl", v),
        "Tests/test_gates.py": text("test_gates.py.tmpl"),
    }


def repo_files(v: dict[str, str], *, gitattributes: bool = True) -> dict[str, str]:
    """Relative path -> content for the files a single-mod repo has at its root besides the mod's own
    (`.gitattributes` left out when the org template provides it)."""
    out = {
        "pyproject.toml": render("pyproject.toml.tmpl", v),
        ".gitignore": GITIGNORE,
        ".gitattributes": GITATTRIBUTES,
        "LICENSE": text("LICENSE"),
    }
    if not gitattributes:
        del out[".gitattributes"]
    return out
