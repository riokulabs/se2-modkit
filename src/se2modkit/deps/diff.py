"""`se2mod deps baseline diff`: compare the CURRENT game install + Mod SDK against a frozen baseline.

Read-only on the game and SDK. The report goes to `<cache>/baseline/reports/<baseline name>-<timestamp>/` (report.md,
report.json, current manifests, text and API diffs). Exit code: 0 no changes, 1 changes found.
"""

from __future__ import annotations

import csv
import datetime
import difflib
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from ..env.core import SE2Env
from ..env.steam import GAME_APP_ID, SDK_APP_ID
from .baseline import (AREA_ORDER, BaselineError, archive_path, area_of, baseline_root, inside_cache,
                       is_text_candidate, parse_acf, read_archive_members, read_manifest, roots, run_asminv,
                       scan_tree, write_manifest)

_NUM = re.compile(r"(\d+)")


def newest_baseline(env: SE2Env) -> Path:
    """The baseline in the cache with the highest version number."""
    root = baseline_root(env)
    cands = [d for d in root.iterdir() if (d / "version-evidence.json").is_file()] if root.is_dir() else []
    if not cands:
        raise BaselineError(f"No baseline under {root}; capture one with `se2mod deps baseline capture`.")
    return max(cands, key=lambda d: [int(n) for n in _NUM.findall(d.name)])


def _inv(p: Path) -> dict[str, dict]:
    d = {}
    with open(p, encoding="utf-8") as f:
        rd = csv.reader(f, delimiter="\t")
        hdr = next(rd)
        for r in rd:
            d[r[0]] = dict(zip(hdr, r))
    return d


def _plus_minus(d: list[str]) -> tuple[int, int]:
    plus = sum(1 for ln in d if ln.startswith("+") and not ln.startswith("+++"))
    minus = sum(1 for ln in d if ln.startswith("-") and not ln.startswith("---"))
    return plus, minus


def run(env: SE2Env, *, baseline_dir: Path | None = None, out: Path | None = None, workers: int = 16,
        fast: bool = False, no_api: bool = False, asminv: Path | None = None,
        max_diff_lines: int = 3000) -> tuple[int, Path]:
    """Diff the install against `baseline_dir` (default: the newest cached baseline); `(exit code, report dir)`."""
    trees = roots(env)
    base = Path(baseline_dir) if baseline_dir else newest_baseline(env)
    if not (base / "version-evidence.json").is_file():
        raise BaselineError(f"{base} is not a baseline (no version-evidence.json).")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if out is None:
        out = baseline_root(env) / "reports" / f"{base.name}-{stamp}"
    out = inside_cache(env, out)
    frozen = next((d for d in (out, *out.parents) if (d / "version-evidence.json").is_file()), None)
    if frozen is not None:
        raise BaselineError(f"Refusing to write the report into the frozen baseline {frozen}; pick another --out.")
    for tree, r in trees.items():
        if not r.is_dir():
            raise BaselineError(f"The {tree} folder {r} is missing.")
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    print("Scanning current install + SDK (read-only)...", file=sys.stderr)
    cur, basem = {}, {}
    for tree, root in trees.items():
        basem[tree] = read_manifest(base / f"manifest-{tree}.tsv.gz")
        print(f"- {tree}: {root}", file=sys.stderr)
        rows = scan_tree(root, workers, basem[tree] if fast else None, progress=True)
        write_manifest(rows, out / f"current-manifest-{tree}.tsv.gz")
        cur[tree] = {r[0]: (r[1], r[2], r[3]) for r in rows}

    added, removed, changed, mtime_only = [], [], [], []
    for tree in trees:
        b, c = basem[tree], cur[tree]
        added += [(tree, p, c[p][0]) for p in sorted(c.keys() - b.keys())]
        removed += [(tree, p, b[p][0]) for p in sorted(b.keys() - c.keys())]
        for p in sorted(b.keys() & c.keys()):
            if b[p][1] != c[p][1]:
                changed.append((tree, p, b[p][0], c[p][0]))
            elif b[p][2] != c[p][2]:
                mtime_only.append((tree, p))

    base_ver = json.loads((base / "version-evidence.json").read_text(encoding="utf-8"))
    cur_ver = {"captured": datetime.datetime.now().isoformat(timespec="seconds"),
               "game_appmanifest": parse_acf(env, GAME_APP_ID), "sdk_appmanifest": parse_acf(env, SDK_APP_ID)}
    ver_changes = []
    for k in ("game_appmanifest", "sdk_appmanifest"):
        for f in ("buildid", "TargetBuildID", "SizeOnDisk", "LastUpdated"):
            if base_ver.get(k, {}).get(f) != cur_ver[k].get(f):
                ver_changes.append((k, f, base_ver.get(k, {}).get(f), cur_ver[k].get(f)))

    asm_changes, api_changes = [], []
    if not no_api and (base / "assembly-inventory.tsv").exists() and run_asminv(env, asminv, out / "current-asm"):
        bi, ci = _inv(base / "assembly-inventory.tsv"), _inv(out / "current-asm" / "assembly-inventory.tsv")
        for k in sorted(bi.keys() | ci.keys()):
            x, y = bi.get(k), ci.get(k)
            if x is None or y is None:
                asm_changes.append((k, "added" if x is None else "removed", "", ""))
                continue
            for fld in ("AssemblyVersion", "FileVersion", "ProductVersion"):
                if x[fld] != y[fld]:
                    asm_changes.append((k, fld, x[fld], y[fld]))
        bapi_dir = out / "baseline-api"
        api_archive = archive_path(base, "api-surface")
        if api_archive.exists() and not bapi_dir.exists():
            bapi_dir.mkdir(parents=True)
            flag = ["-I", "zstd"] if api_archive.name.endswith(".zst") else ["-z"]
            subprocess.run(["tar", *flag, "-xf", str(api_archive), "-C", str(bapi_dir)], check=True)
        bapi = ({p.relative_to(bapi_dir / "api").as_posix(): p for p in (bapi_dir / "api").rglob("*.api.txt")}
                if (bapi_dir / "api").exists() else {})
        capi = {p.relative_to(out / "current-asm" / "api").as_posix(): p
                for p in (out / "current-asm" / "api").rglob("*.api.txt")}
        (out / "api-diffs").mkdir(exist_ok=True)
        for k in sorted(bapi.keys() | capi.keys()):
            if k not in bapi or k not in capi:
                api_changes.append((k, "added" if k not in bapi else "removed", 0, 0))
                continue
            A = bapi[k].read_text(encoding="utf-8", errors="replace").splitlines()
            B = capi[k].read_text(encoding="utf-8", errors="replace").splitlines()
            if A == B:
                continue
            d = list(difflib.unified_diff(A, B, "baseline/" + k, "current/" + k, lineterm="", n=0))
            plus, minus = _plus_minus(d)
            (out / "api-diffs" / (k.replace("/", "__") + ".diff")).write_text("\n".join(d) + "\n", encoding="utf-8",
                                                                              newline="\n")
            api_changes.append((k, "changed", plus, minus))

    text_diffs = []
    text_changed = [(t, p) for (t, p, _, _) in changed if is_text_candidate(p)]
    old = read_archive_members(archive_path(base, "text-content"), {f"{t}/{p}" for t, p in text_changed})
    (out / "diffs").mkdir(exist_ok=True)
    for t, p in text_changed:
        key = f"{t}/{p}"
        try:
            new_txt = (trees[t] / p).read_bytes().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        if key not in old:
            text_diffs.append((t, p, None, None, ["(baseline copy not in archive)"]))
            continue
        old_txt = old[key].decode("utf-8", "replace").splitlines()
        d = list(difflib.unified_diff(old_txt, new_txt, "baseline/" + key, "current/" + key, lineterm="", n=2))
        plus, minus = _plus_minus(d)
        tail = "\n...truncated...\n" if len(d) > max_diff_lines else "\n"
        (out / "diffs" / (key.replace("/", "__") + ".diff")).write_text("\n".join(d[:max_diff_lines]) + tail,
                                                                        encoding="utf-8", newline="\n")
        text_diffs.append((t, p, plus, minus, d))

    grouped: dict = defaultdict(lambda: {"added": [], "removed": [], "changed": []})
    for t, p, s in added:
        grouped[area_of(t, p)]["added"].append((t, p, s))
    for t, p, s in removed:
        grouped[area_of(t, p)]["removed"].append((t, p, s))
    for t, p, s0, s1 in changed:
        grouped[area_of(t, p)]["changed"].append((t, p, s0, s1))
    any_change = bool(added or removed or changed or ver_changes or asm_changes or api_changes)

    L = ["# SE2 update diff report",
         f"- generated: {datetime.datetime.now().isoformat(timespec='seconds')} (scan took {time.time() - t0:.0f}s, "
         f"{'FAST' if fast else 'full'} hash mode)",
         f"- baseline: `{base}` (captured {base_ver.get('captured')}, version {base_ver.get('game_version')})",
         "", "## Verdict",
         "**NO CHANGES** - install and SDK are identical to the baseline." if not any_change else
         f"**CHANGES FOUND**: {len(added)} added, {len(removed)} removed, {len(changed)} changed files; "
         f"{len(ver_changes)} version-manifest fields; {len(asm_changes)} assembly version fields; "
         f"{len(api_changes)} API-surface files differ."]
    if mtime_only:
        L.append(f"- note: {len(mtime_only)} files have a different mtime but identical content (ignored).")
    L += ["", "## Version"]
    for k in ("game_appmanifest", "sdk_appmanifest"):
        b, c = base_ver.get(k, {}), cur_ver[k]
        L.append(f"- {k}: buildid {b.get('buildid')} -> {c.get('buildid')}"
                 f"{'  (CHANGED)' if b.get('buildid') != c.get('buildid') else ''}; "
                 f"LastUpdated {b.get('LastUpdated')} -> {c.get('LastUpdated')}")
    if ver_changes:
        L.append("")
        L += [f"  - {k}.{f}: {x} -> {y}" for k, f, x, y in ver_changes]
    if asm_changes:
        L += ["", "### Assembly version changes"]
        L += [f"- `{k}` {f}: {x} -> {y}" for k, f, x, y in asm_changes[:300]]
        if len(asm_changes) > 300:
            L.append(f"- ... {len(asm_changes) - 300} more")
    L += ["", "## Summary by area", "| Area | Added | Removed | Changed |", "|---|---:|---:|---:|"]
    areas = [x for x in AREA_ORDER if x in grouped] + sorted(x for x in grouped if x not in AREA_ORDER)
    for ar in areas:
        g = grouped[ar]
        L.append(f"| {ar} | {len(g['added'])} | {len(g['removed'])} | {len(g['changed'])} |")
    if not areas:
        L.append("| (none) | 0 | 0 | 0 |")
    L.append("")
    for ar in areas:
        g = grouped[ar]
        L.append(f"## {ar}")
        for kind in ("added", "removed", "changed"):
            items = g[kind]
            if not items:
                continue
            L.append(f"### {kind} ({len(items)})")
            for it in items[:400]:
                if kind == "changed":
                    L.append(f"- [{it[0]}] `{it[1]}`  {it[2]:,} -> {it[3]:,} bytes")
                else:
                    L.append(f"- [{it[0]}] `{it[1]}`  {it[2]:,} bytes")
            if len(items) > 400:
                L.append(f"- ... {len(items) - 400} more (see current-manifest vs baseline manifest)")
        L.append("")
    if text_diffs:
        L += ["## Changed text definitions (unified diff summary)", f"Full diffs: `{out / 'diffs'}`", ""]
        for t, p, plus, minus, d in sorted(text_diffs, key=lambda x: -((x[2] or 0) + (x[3] or 0)))[:200]:
            L += [f"### [{t}] `{p}`  (+{plus}/-{minus})", "```diff", *d[:30]]
            if len(d) > 30:
                L.append(f"... ({len(d) - 30} more diff lines)")
            L.append("```")
        if len(text_diffs) > 200:
            L.append(f"... {len(text_diffs) - 200} more changed text files (see diffs/)")
    if api_changes:
        L += ["", "## Public API surface changes (assemblies)", f"Full diffs: `{out / 'api-diffs'}`"]
        L += [f"- `{k}`: {kind} (+{plus}/-{minus})" for k, kind, plus, minus in api_changes]
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    (out / "report.json").write_text(json.dumps({
        "any_change": any_change, "added": added, "removed": removed, "changed": changed,
        "mtime_only_count": len(mtime_only), "version_changes": ver_changes, "assembly_changes": asm_changes,
        "api_changes": api_changes, "text_diff_files": [(t, p, pl, mi) for t, p, pl, mi, _ in text_diffs]},
        indent=1), encoding="utf-8", newline="\n")
    print("\n".join(L[:12]))
    print(f"\nReport: {out / 'report.md'}")
    print("RESULT: " + ("CHANGES FOUND" if any_change else "no changes"))
    return (1 if any_change else 0), out
