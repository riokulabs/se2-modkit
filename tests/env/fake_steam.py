"""Builder for synthetic Steam trees (hand-written, no real Steam data)."""

from __future__ import annotations

from pathlib import Path


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def make(
    root: Path,
    *,
    libraries: list[Path] | None = None,
    apps: dict[int, dict] | None = None,
) -> Path:
    """Create a Steam root at `root` plus extra libraries.

    `apps` maps app id to {"library": Path (default root), "installdir": str, "buildid": int}.
    """
    extra = libraries or []
    all_libs = [root, *extra]
    for lib in all_libs:
        (lib / "steamapps" / "common").mkdir(parents=True, exist_ok=True)
    lines = ['"libraryfolders"', "{"]
    for i, lib in enumerate(all_libs):
        lines += [f'\t"{i}"', "\t{", f'\t\t"path"\t\t"{_q(str(lib))}"', '\t\t"label"\t\t""', "\t}"]
    lines.append("}")
    (root / "steamapps" / "libraryfolders.vdf").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    for app_id, spec in (apps or {}).items():
        lib = spec.get("library", root)
        installdir = spec["installdir"]
        (lib / "steamapps" / "common" / installdir).mkdir(parents=True, exist_ok=True)
        acf = (
            '"AppState"\n{\n'
            f'\t"appid"\t\t"{app_id}"\n'
            f'\t"installdir"\t\t"{_q(installdir)}"\n'
            f'\t"buildid"\t\t"{spec.get("buildid", 1)}"\n'
            "}\n"
        )
        (lib / "steamapps" / f"appmanifest_{app_id}.acf").write_text(acf, encoding="utf-8", newline="\n")
    return root
