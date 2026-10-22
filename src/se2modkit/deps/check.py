"""`se2mod deps check`: what did a Space Engineers 2 update change that our mods and tools depend on?

Reads a recorded dependency file (`--json`, else the `our-game-dependencies.json` of the mod or project here, else
modkit's own, empty one) and re-checks every recorded game source:

  * vanilla / binary files (SDK, game install): sha256 unchanged, changed or missing;
  * cloned SDK folders: the tree hash of their .def/.partialdef files;
  * vanilla GUIDs: still defined (definitions, asset ids) or still present (component keys) in the SDK and the game;
  * vanilla names recorded in a file's role: still present in that file;
  * decompiled citations: the file's sha256 and the anchor text at the cited line (+-8) in the decompiled tree;
  * Steam buildids of the game (1133870) and the Mod SDK (3578990);
  * with `refs`: that every file:line in our code still exists.

Path tags in the file: `SDK:`, `GAME:`, `STEAMAPPS:` (the game's library steamapps) and `WS:` (the workspace).

Read-only on the game and the SDK. `--report-json PATH` and `--rebaseline [PATH]` write where they are told (never
under the game or SDK); a bare `--rebaseline` writes `<cache>/deps/our-game-dependencies.rebaselined.json`. Exit
code: 0 nothing relevant changed; 1 a high-risk dependency changed; 2 something (med or high) is missing; 3 no
`--json` given and the file found lists no dependencies.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ..env.core import SE2Env, require
from ..env.steam import GAME_APP_ID, SDK_APP_ID
from ..project import ProjectError, load_project

BUNDLED = Path(__file__).resolve().with_name("our-game-dependencies.json")
REBASELINE_NAME = "our-game-dependencies.rebaselined.json"
RISK = {"low": 1, "med": 2, "high": 3}
GUID_RX = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
OWN_RX = re.compile(r'"(?:Guid|AssetID)"\s*:\s*"([0-9a-fA-F-]{36})"')
BUILDID_RX = re.compile(r'"buildid"\s+"(\d+)"')


class DepsError(ProjectError):
    """The dependency check cannot proceed; reported as one plain sentence."""


@dataclass
class Roots:
    ws: Path
    game: Path
    sdk: Path
    dec: Path
    baseline_version: str = "the baseline"

    @property
    def steamapps(self) -> Path:
        return self.game.parent.parent

    def acf(self, app: str) -> Path:
        """An app manifest, from the library the app is installed in (the game and SDK may sit in different ones)."""
        base = {str(GAME_APP_ID): self.game, str(SDK_APP_ID): self.sdk}.get(str(app), self.game)
        return base.parent.parent / f"appmanifest_{app}.acf"

    def resolve(self, p: str | None) -> Path | None:
        if not p:
            return None
        for tag, root in (("SDK:", self.sdk), ("GAME:", self.game), ("STEAMAPPS:", self.steamapps), ("WS:", self.ws)):
            if p.startswith(tag):
                return root / p[len(tag):]
        if p.startswith("~"):
            return Path(os.path.expanduser(p))
        if p.startswith("/"):
            return Path(p)
        return None


class _Cache:
    def __init__(self) -> None:
        self.sha: dict[str, str | None] = {}
        self.text: dict[str, str | None] = {}

    def sha256(self, p: Path | str) -> str | None:
        p = str(p)
        if p not in self.sha:
            try:
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    for b in iter(lambda: f.read(1 << 20), b""):
                        h.update(b)
                self.sha[p] = h.hexdigest()
            except OSError:
                self.sha[p] = None
        return self.sha[p]

    def read(self, p: Path | str) -> str | None:
        p = str(p)
        if p not in self.text:
            try:
                self.text[p] = Path(p).read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                self.text[p] = None
        return self.text[p]


def tree_hash(folder: Path, c: _Cache) -> tuple[str | None, int]:
    files = sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix in (".def", ".partialdef"))
    if not files:
        return None, 0
    blob = "".join(f"{os.path.relpath(p, folder)}={c.sha256(p)}\n" for p in files)
    return hashlib.sha256(blob.encode()).hexdigest(), len(files)


def ranges(lines: str | None) -> list[tuple[int, int]]:
    out = []
    for part in re.split(r",\s*", str(lines or "")):
        m = re.match(r"(\d+)(?:-(\d+))?$", part.strip())
        if m:
            a = int(m.group(1))
            out.append((a, int(m.group(2) or a)))
    return out


class GuidIndex:
    """Every GUID defined (Guid / AssetID) or merely present in the SDK vanilla assets and the game content."""

    def __init__(self, roots: Roots):
        self.defined: set[str] = set()
        self.present: set[str] = set()
        dirs = [roots.sdk / "GameData/Vanilla/Assets", roots.sdk / "GameData/Engine/Assets",
                roots.game / "GameData/Vanilla/Content", roots.game / "VRage/GameData/Engine/Content"]
        for d in dirs:
            for dp, _, fs in os.walk(d):
                for f in fs:
                    if not f.endswith((".def", ".partialdef", ".meta")):
                        continue
                    try:
                        t = Path(dp, f).read_text(encoding="utf-8-sig", errors="replace")
                    except OSError:
                        continue
                    self.defined.update(g.lower() for g in OWN_RX.findall(t))
                    if not f.endswith(".meta"):
                        self.present.update(GUID_RX.findall(t.lower()))


def check_file(entry: dict, roots: Roots, c: _Cache):
    p = roots.resolve(entry.get("path"))
    if p is None:
        return None
    if "sha256_tree" in entry:
        if not p.is_dir():
            return ("missing", f"folder {entry['path']} is gone")
        h, n = tree_hash(p, c)
        if h != entry["sha256_tree"]:
            return ("changed", f"tree {entry['path']}: {entry.get('files')} -> {n} definition files, hash differs")
        return ("ok", None)
    if "sha256" not in entry:
        return ("ok", None) if p.exists() else ("missing", f"{entry['path']} is gone")
    if not p.exists():
        return ("missing", f"{entry['path']} is gone")
    h = c.sha256(p)
    if entry["sha256"] is None:
        return ("ok", None)
    if h != entry["sha256"]:
        role = entry.get("role", "")
        extra = ""
        if role.startswith("defines '"):
            w = role[len("defines '"):-1]
            extra = "" if w in (c.read(p) or "") else f"; '{w}' no longer occurs in it"
        elif role.startswith("uses event:/"):
            ev = role[len("uses "):]
            extra = "" if f'"{ev}"' in (c.read(p) or "") else f"; {ev} no longer used there"
        return ("changed", f"{entry['path']} ({role or 'file'}) sha256 differs{extra}")
    return ("ok", None)


def _near(anchor: str, rs: list[tuple[int, int]], ls: list[str]) -> bool:
    return any(anchor in line for a, b in rs for line in ls[max(0, a - 9): b + 8])


def check_decompiled(entry: dict, roots: Roots, c: _Cache):
    cite = entry.get("decompiled")
    if not cite:
        return None
    file, _, lines = cite.partition(":")
    p = roots.dec / file
    if not p.exists():
        return ("missing", f"decompiled {file} not found in {roots.dec}")
    t = c.read(p) or ""
    ls = t.splitlines()
    anchor = entry.get("anchor")
    status, msgs = "ok", []
    if entry.get("sha256") and c.sha256(p) != entry["sha256"]:
        status = "changed"
        msgs.append(f"decompiled {file} differs from the {roots.baseline_version} baseline")
    if anchor:
        rs = ranges(lines)
        near = _near(anchor, rs, ls) if rs else anchor in t
        if not near:
            hits = [i for i, line in enumerate(ls, 1) if anchor in line][:5]
            if hits:
                status = "changed"
                msgs.append(f"anchor '{anchor}' moved: {file}:{lines} -> lines {hits}")
            else:
                return ("missing", f"anchor '{anchor}' is gone from {file}")
    return (status, "; ".join(msgs) or None)


def _buildid(roots: Roots, app: str, c: _Cache) -> str | None:
    m = BUILDID_RX.search(c.read(roots.acf(app)) or "")
    return m.group(1) if m else None


def check_dep(d: dict, roots: Roots, gidx: GuidIndex | None, check_refs: bool, c: _Cache) -> list:
    findings = []
    for g in d.get("game_source", []):
        r = (check_file(g, roots, c) if g.get("path")
             else check_decompiled(g, roots, c) if g.get("decompiled") else None)
        if r and r[0] != "ok":
            findings.append(r)
    guid = d.get("guid")
    if guid and gidx is not None:
        if d["kind"] == "component_key":
            if guid not in gidx.present:
                findings.append(("missing", f"key {guid} no longer occurs in any SDK/game definition"))
        elif d["kind"] != "project_dependency" and guid not in gidx.defined:
            findings.append(("missing", f"GUID {guid} is no longer defined in the SDK or game content"))
    if d["kind"] == "project_dependency":
        vp = roots.sdk / "GameData/Vanilla/Vanilla.vrgproj"
        if guid and guid not in (c.read(vp) or "").lower():
            findings.append(("missing", f"Vanilla.vrgproj no longer declares ProjectID {guid}"))
    if d["kind"] == "steam_build" and d.get("buildid"):
        app = d["id"].rsplit(":", 1)[-1]
        now = _buildid(roots, app, c)
        if now != d["buildid"]:
            findings.append(("changed", f"Steam buildid of app {app}: {d['buildid']} -> {now}"))
    if check_refs:
        for r in d.get("our_refs", []):
            f, _, n = r.rpartition(":")
            p = roots.ws / f
            if not p.exists():
                findings.append(("ref", f"our ref {r}: file gone"))
            elif n.isdigit() and int(n) > len((c.read(p) or "").splitlines()):
                findings.append(("ref", f"our ref {r}: past the end of the file"))
    return findings


def safe_out(path: Path, roots: Roots) -> Path:
    """Where an output may go: anywhere but under the game or SDK (relative paths are relative to the cwd), so a
    recorded dependency file in a mod repository can be refreshed in place."""
    p = Path(path).resolve()
    for root in (roots.game.resolve(), roots.sdk.resolve()):
        if p == root or root in p.parents:
            raise DepsError(f"Refusing to write under {root}: the dependency check is read-only on the game and the "
                            "SDK.")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


DEPS_NAME = "our-game-dependencies.json"


def default_deps_file(cwd: Path | None = None) -> Path:
    """The recorded dependency file of the project here: in the mod's root, then the project root, then the cwd (each
    as `our-game-dependencies.json` or `docs/reference/our-game-dependencies.json`); else modkit's bundled list."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    dirs: list[Path] = []
    try:
        project = load_project(cwd)
    except ProjectError:
        project = None
    if project is not None:
        try:
            dirs.append(project.mod(None, cwd=cwd).root)
        except ProjectError:  # several mods and not inside one: the project root still counts
            pass
        dirs.append(project.root)
    dirs.append(cwd)
    for d in dirs:
        for cand in (d / DEPS_NAME, d / "docs" / "reference" / DEPS_NAME):
            if cand.is_file():
                return cand
    return BUNDLED


def relocate(cite: str, anchor: str | None, roots: Roots, c: _Cache) -> str:
    """The cite "file:lines" with its ranges shifted to where the anchor now is (the hit nearest the old first line,
    one shift for all ranges); unchanged when the anchor is still near, absent, or the cite has no lines."""
    file, _, lines = cite.partition(":")
    rs = ranges(lines)
    ls = (c.read(roots.dec / file) or "").splitlines()
    if not anchor or not rs or _near(anchor, rs, ls):
        return cite
    hits = [i for i, line in enumerate(ls, 1) if anchor in line]
    if not hits:
        return cite
    old_first = rs[0][0]
    delta = min(hits, key=lambda h: abs(h - old_first)) - old_first
    return file + ":" + ", ".join(f"{a + delta}" if a == b else f"{a + delta}-{b + delta}" for a, b in rs)


def rebaseline_doc(doc: dict, roots: Roots, c: _Cache, version: str | None = None) -> dict:
    """A copy of the document with the current hashes / buildids and relocated decompiled cites (ids, refs, risks
    unchanged); the baseline block records the version, buildids and decompiled tree."""
    out = json.loads(json.dumps(doc))
    moved = 0
    for d in out["dependencies"]:
        for g in d.get("game_source", []):
            p = roots.resolve(g.get("path"))
            if p is not None and "sha256_tree" in g and p.is_dir():
                g["sha256_tree"], g["files"] = tree_hash(p, c)
            elif p is not None and "sha256" in g and p.exists():
                g["sha256"] = c.sha256(p)
            elif g.get("decompiled"):
                new = relocate(g["decompiled"], g.get("anchor"), roots, c)
                if new != g["decompiled"]:
                    g["decompiled"], moved = new, moved + 1
                dp = roots.dec / g["decompiled"].partition(":")[0]
                if g.get("sha256") and dp.exists():
                    g["sha256"] = c.sha256(dp)
        if d["kind"] == "steam_build":
            d["buildid"] = _buildid(roots, d["id"].rsplit(":", 1)[-1], c)
    b = out.setdefault("baseline", {})
    for app, key in ((str(GAME_APP_ID), "game_buildid"), (str(SDK_APP_ID), "sdk_buildid")):
        b[key] = _buildid(roots, app, c)
    if version:
        b["game_version"] = version
    try:
        dec = "WS:" + roots.dec.relative_to(roots.ws).as_posix()
    except ValueError:
        dec = "DECOMPILED:"  # outside the workspace: no machine path in a file that gets committed
    b["decompiled"] = f"{dec} ({b.get('game_version')})"
    out.setdefault("roots", {})["DECOMPILED"] = dec
    out["generated"] = datetime.date.today().isoformat()
    print(f"rebaseline: {moved} decompiled cites relocated to their anchors")
    return out


def _roots(env: SE2Env, workspace: Path | None, game: Path | None, sdk: Path | None,
           decompiled: Path | None) -> Roots:
    if game is None:
        require(env, "game_dir")
    if sdk is None:
        require(env, "sdk_dir")
    ws = Path(workspace) if workspace else Path(os.environ.get("SE2_WORKSPACE") or Path.cwd())
    if decompiled:
        dec = Path(decompiled)
    elif env.decompiled_dir is not None:
        dec = env.decompiled_dir
    else:
        cur = ws / "tools" / "decompiled-current"
        dec = cur if cur.exists() else ws / "tools" / "decompiled"
    return Roots(ws=ws, game=Path(game or env.game_dir), sdk=Path(sdk or env.sdk_dir), dec=dec)


def run(env: SE2Env, *, deps_file: Path | None = None, workspace: Path | None = None, game: Path | None = None,
        sdk: Path | None = None, decompiled: Path | None = None, version: str | None = None, refs: bool = False,
        no_guids: bool = False, min_risk: str = "low", report_json: Path | None = None,
        rebaseline: Path | bool = False, quiet: bool = False) -> int:
    """Check every recorded dependency; print a summary and the changed items; return the exit code.

    `deps_file` defaults to the project's recorded file (`default_deps_file`). `rebaseline=True` also writes a
    rebaselined copy to `<cache>/deps/our-game-dependencies.rebaselined.json`; a path writes it there instead (any
    path outside the game and SDK, relative to the cwd), e.g. over the recorded file itself. Without `deps_file`,
    checking 0 dependencies is an error (exit 3): the wrong file was probably found."""
    roots = _roots(env, workspace, game, sdk, decompiled)
    explicit = deps_file is not None
    deps_file = Path(deps_file) if explicit else default_deps_file()
    try:
        doc = json.loads(deps_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DepsError(f"Cannot read the dependency file {deps_file} ({e}).") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("dependencies"), list):
        raise DepsError(f"{deps_file} is not a dependency file (no \"dependencies\" list).")
    rebaseline_to = (env.cache_dir / "deps" / REBASELINE_NAME if rebaseline is True
                     else (Path(rebaseline) if rebaseline else None))
    # Check where outputs go before the (slow) scan, so a refused path fails at once.
    report_out = safe_out(report_json, roots) if report_json else None
    rebase_out = safe_out(rebaseline_to, roots) if rebaseline_to else None
    base = doc.get("baseline") or {}
    roots.baseline_version = base.get("game_version") or roots.baseline_version
    for p, what in ((roots.game, "game"), (roots.sdk, "SDK")):
        if not p.is_dir():
            print(f"warning: {what} folder not found: {p}", file=sys.stderr)
    c = _Cache()
    gidx = None if no_guids or not any(d.get("guid") for d in doc["dependencies"]) else GuidIndex(roots)
    results = []
    for d in doc["dependencies"]:
        if RISK[d["risk"]] < RISK[min_risk]:
            continue
        f = check_dep(d, roots, gidx, refs, c)
        if f:
            results.append((d, f))
    worst: dict[str, list[str]] = {"missing": [], "changed": [], "ref": []}
    for d, f in results:
        for status, _ in f:
            worst[status].append(d["id"])
    n = sum(1 for d in doc["dependencies"] if RISK[d["risk"]] >= RISK[min_risk])
    print(f"baseline: game {base.get('game_version')} buildid {base.get('game_buildid')}, "
          f"SDK buildid {base.get('sdk_buildid')}; checked {n} dependencies (risk >= {min_risk})")
    for status in ("missing", "changed", "ref"):
        ids = sorted(set(worst[status]))
        by = {r: sum(1 for d, f in results if d["id"] in ids and d["risk"] == r) for r in ("high", "med", "low")}
        print(f"  {status.upper():8} {len(ids):4}  (high {by['high']}, med {by['med']}, low {by['low']})")
    if not quiet:
        for d, f in sorted(results, key=lambda x: (-RISK[x[0]["risk"]], -x[0].get("score", 0), x[0]["id"])):
            print(f"\n[{d['risk']:4}] #{d.get('rank', '?'):<4} {d['id']}  ({d['kind']})\n       {d['title'][:110]}")
            for status, msg in f:
                print(f"       {status.upper()}: {msg}")
            if d.get("our_refs"):
                more = f" (+{d['ref_count'] - 3})" if d.get("ref_count", 0) > 3 else ""
                print(f"       ours: {', '.join(d['our_refs'][:3])}{more}")
    if report_out:
        report_out.write_text(json.dumps([{"id": d["id"], "risk": d["risk"], "kind": d["kind"], "rank": d.get("rank"),
                                           "findings": [{"status": s, "detail": m} for s, m in f]}
                                          for d, f in results], indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"\nreport written to {report_out}")
    if rebase_out:
        rebase_out.write_text(json.dumps(rebaseline_doc(doc, roots, c, version), indent=1) + "\n", encoding="utf-8",
                              newline="\n")
        print(f"rebaselined copy written to {rebase_out}")
    if n == 0 and not explicit:
        print(f"warning: no dependencies checked ({deps_file} lists none at risk >= {min_risk}); pass --json with the "
              "recorded dependency file", file=sys.stderr)
        return 3
    missing_bad = any(d["risk"] in ("high", "med") and any(s == "missing" for s, _ in f) for d, f in results)
    changed_high = any(d["risk"] == "high" and any(s in ("changed", "missing") for s, _ in f) for d, f in results)
    return 2 if missing_bad else 1 if changed_high else 0
