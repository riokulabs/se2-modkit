"""`se2mod deps baseline capture`: freeze a game + Mod SDK baseline into the user cache (read-only on game and SDK).

Writes into `<cache>/baseline/<version>/` (new or empty; baselines are frozen):
  manifest-game.tsv.gz, manifest-sdk.tsv.gz   path size sha256 mtime of every game / SDK file
  text-content.tar.zst (.tar.gz without zstd) every text file (members game/<rel>, sdk/<rel>)
  version-evidence.json                       version, appmanifests, file counts, key assembly versions
  with an AsmInventory tool:                  assembly-inventory.tsv, api-surface archive (needs dotnet)
  with a decompiled tree:                     decompiled-<version> archive

The scan and manifest code here is shared with `deps.diff`, so the formats cannot drift. Nothing captured is ever
written into a repository.
"""

from __future__ import annotations

import concurrent.futures as cf
import csv
import datetime
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from ..env.core import SE2Env, require
from ..env.steam import GAME_APP_ID, SDK_APP_ID
from ..project import ProjectError

TEXT_EXTS = {"def", "partialdef", "vrgproj", "meta", "json", "xml", "txt", "cs", "sbc", "model", "loc-texts",
             "config", "targets", "props", "ini", "conf", "vdf", "fspro", "cshtml", "js", "hlsl", "hlsli",
             "animation", "texpreset", "container-info", "armorside", "comparm", "fshash", "csproj", "yaml",
             "yml", "md"}
ASM_EXTS = {"dll", "exe", "so", "dylib"}
MANIFEST_HEADER = ["path", "size", "sha256", "mtime"]
KEY_ASSEMBLIES = ("game/Game2/Game2.Game.dll", "game/Game2/SpaceEngineers2.dll", "game/Game2/VRage.Core.dll")
AREA_ORDER = ["Assemblies", "GameData definitions", "SDK assets", "Templates", "Materials", "Shaders",
              "GameData binary content", "SDK ExampleMod"]


class BaselineError(ProjectError):
    """A baseline cannot be captured or compared; reported as one plain sentence."""


# --------------------------------------------------------------------------- roots and outputs

def roots(env: SE2Env) -> dict[str, Path]:
    require(env, "game_dir", "sdk_dir")
    return {"game": env.game_dir, "sdk": env.sdk_dir}


def baseline_root(env: SE2Env) -> Path:
    return env.cache_dir / "baseline"


def inside_cache(env: SE2Env, p: Path) -> Path:
    """`p` resolved, if it is under `<cache>/baseline`; else a BaselineError (nothing captured leaves the cache)."""
    root = baseline_root(env).resolve()
    q = Path(p).resolve()
    if q != root and root not in q.parents:
        raise BaselineError(f"Refusing to write {q}: baselines and reports go under the user cache {root}.")
    for tree in (env.game_dir, env.sdk_dir):
        if tree is not None and (q == tree.resolve() or tree.resolve() in q.parents):
            raise BaselineError(f"Refusing to write under {tree}: baselines are read-only on the game and the SDK.")
    return q


# --------------------------------------------------------------------------- scanning (read-only)

def ext_of(p: str) -> str:
    n = os.path.basename(p)
    return n.rsplit(".", 1)[-1].lower() if "." in n else ""


def sha256_file(path: str | Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb", buffering=0) as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def list_files(root: Path) -> list[str]:
    out = []
    stack = [str(root)]
    root_s = str(root)
    while stack:
        d = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                else:
                    out.append(Path(os.path.relpath(e.path, root_s)).as_posix())
    out.sort()
    return out


def scan_tree(root: Path, workers: int = 16, reuse: dict | None = None, progress: bool = False) -> list[tuple]:
    """(relpath, size, sha256, mtime_int) of every file; `reuse` ({path: (size, sha, mtime)}) skips unchanged ones."""
    files = list_files(root)
    root_s = str(root)
    done = [0]

    def one(rel: str) -> tuple:
        p = os.path.join(root_s, rel)
        st = os.lstat(p)
        mt = int(st.st_mtime)
        if os.path.islink(p):
            return (rel, 0, "symlink:" + os.readlink(p), mt)
        if reuse is not None:
            r = reuse.get(rel)
            if r and r[0] == st.st_size and r[2] == mt:
                return (rel, st.st_size, r[1], mt)
        try:
            h = sha256_file(p)
        except OSError as ex:
            h = "ERROR:" + str(ex)
        done[0] += 1
        if progress and done[0] % 5000 == 0:
            print(f"  hashed {done[0]} / {len(files)}", file=sys.stderr, flush=True)
        return (rel, st.st_size, h, mt)

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(one, files, chunksize=64))


def write_manifest(rows: list[tuple], path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, escapechar="\\")
        w.writerow(MANIFEST_HEADER)
        for r in rows:
            w.writerow(r)


def read_manifest(path: Path) -> dict[str, tuple[int, str, int]]:
    d = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            r = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE, escapechar="\\")
            next(r)
            for row in r:
                d[row[0]] = (int(row[1]), row[2], int(row[3]))
    except (OSError, ValueError, StopIteration) as e:
        raise BaselineError(f"Cannot read the baseline manifest {path} ({e}).") from e
    return d


def area_of(tree: str, rel: str) -> str:
    e = ext_of(rel)
    low = rel.lower()
    if e in ASM_EXTS:
        return "Assemblies"
    if "/templates/" in low or e == "fstemplate":
        return "Templates"
    if tree == "sdk" and rel.startswith("ExampleMod1/"):
        return "SDK ExampleMod"
    if ("/materials/" in low or "blockmaterials" in low or low.startswith("blendermateriallibrary")
            or e == "texpreset"):
        return "Materials"
    if e in ("hlsl", "hlsli"):
        return "Shaders"
    if tree == "game" and rel.startswith("GameData/") and e in TEXT_EXTS:
        return "GameData definitions"
    if tree == "game" and rel.startswith("GameData/"):
        return "GameData binary content"
    if tree == "sdk" and rel.startswith("GameData/"):
        return "SDK assets"
    return "Other (" + tree + ")"


def is_text_candidate(rel: str) -> bool:
    return ext_of(rel) in TEXT_EXTS


# --------------------------------------------------------------------------- version evidence

def parse_acf(env: SE2Env, appid: int | str) -> dict:
    """Selected fields of an app manifest, from the library the app is installed in."""
    d = {GAME_APP_ID: env.game_dir, SDK_APP_ID: env.sdk_dir}.get(int(appid))
    p = d.parent.parent / f"appmanifest_{appid}.acf" if d is not None else None
    out: dict = {}
    if p is not None and p.exists():
        for m in re.finditer(r'^\s*"(\w+)"\s+"([^"]*)"', p.read_text(encoding="utf-8", errors="replace"), re.M):
            out.setdefault(m.group(1), m.group(2))
    keep = ("appid", "name", "installdir", "buildid", "TargetBuildID", "LastUpdated", "SizeOnDisk", "StateFlags")
    return {k: out.get(k) for k in keep}


def run_asminv(env: SE2Env, asminv: Path | None, outdir: Path, no_api: bool = False) -> Path | None:
    """Run the AsmInventory tool (`dotnet AsmInventory.dll`) into `outdir`; None when it is not available."""
    if asminv is None or not Path(asminv).is_file():
        return None
    if shutil.which("dotnet") is None:
        raise BaselineError("dotnet not found on PATH; it runs the AsmInventory tool (or leave out --api).")
    outdir.mkdir(parents=True, exist_ok=True)
    args = ["dotnet", str(asminv), "--game", str(env.game_dir), "--sdk", str(env.sdk_dir), "--out", str(outdir)]
    if no_api:
        args.append("--no-api")
    subprocess.run(args, check=True)
    return outdir


# --------------------------------------------------------------------------- archives

def _zstd() -> str | None:
    return shutil.which("zstd")


def archive_path(base: Path, stem: str) -> Path:
    """`stem.tar.zst` when zstd is available, else `stem.tar.gz`; an existing one of either wins."""
    for suffix in (".tar.zst", ".tar.gz"):
        if (base / (stem + suffix)).exists():
            return base / (stem + suffix)
    return base / (stem + (".tar.zst" if _zstd() else ".tar.gz"))


class _ArchiveWriter:
    def __init__(self, out: Path):
        self.out = out
        self.proc = None
        if out.name.endswith(".tar.zst"):
            self.proc = subprocess.Popen([_zstd() or "zstd", "-19", "--long=27", "-T0", "-q", "-o", str(out), "-f"],
                                         stdin=subprocess.PIPE)
            self.tf = tarfile.open(fileobj=self.proc.stdin, mode="w|")
        else:
            self.tf = tarfile.open(out, mode="w:gz")

    def __enter__(self) -> tarfile.TarFile:
        return self.tf

    def __exit__(self, *exc) -> None:
        self.tf.close()
        if self.proc is not None:
            self.proc.stdin.close()
            if self.proc.wait():
                raise BaselineError(f"zstd failed writing {self.out}.")


def tar_dir(src: Path, arcname: str, out: Path) -> None:
    with _ArchiveWriter(out) as tf:
        tf.add(str(src), arcname=arcname)


def read_archive_members(archive: Path, wanted: set[str]) -> dict[str, bytes]:
    """Stream the archive once and return {member name: bytes} for the names in `wanted`."""
    out: dict[str, bytes] = {}
    if not wanted or not Path(archive).exists():
        return out
    proc = None
    if str(archive).endswith(".tar.zst"):
        if not _zstd():
            raise BaselineError(f"zstd not found on PATH; it is needed to read {archive}.")
        proc = subprocess.Popen([_zstd(), "-dc", "--long=27", str(archive)], stdout=subprocess.PIPE)
        tf = tarfile.open(fileobj=proc.stdout, mode="r|")
    else:
        tf = tarfile.open(archive, mode="r:gz")
    try:
        for m in tf:
            if m.name in wanted:
                f = tf.extractfile(m)
                out[m.name] = f.read() if f else b""
                if len(out) == len(wanted):
                    break
    finally:
        tf.close()
        if proc is not None:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
    return out


# --------------------------------------------------------------------------- capture

def capture(env: SE2Env, version: str | None = None, *, dest: Path | None = None, workers: int = 16,
            asminv: Path | None = None, api: bool = False, decompiled: Path | None = None) -> Path:
    """Capture the installed game and SDK into `<cache>/baseline/<version>` (or `dest`, also under it)."""
    trees = roots(env)
    version = version or env.game_version
    if not version:
        raise BaselineError("The installed game version is unknown; pass --version.")
    base = inside_cache(env, dest if dest is not None else baseline_root(env) / version)
    if base.exists() and any(base.iterdir()):
        raise BaselineError(f"Refusing to write into non-empty {base}: baselines are frozen, pick a new version or "
                            "directory.")
    if api and (asminv is None or not Path(asminv).is_file()):
        raise BaselineError("--api needs the AsmInventory tool; pass --asminv PATH/AsmInventory.dll.")
    base.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows_all = {}
    for tree, root in trees.items():
        print(f"hashing {tree} ...", file=sys.stderr, flush=True)
        rows = scan_tree(root, workers, None, progress=True)
        write_manifest(rows, base / f"manifest-{tree}.tsv.gz")
        rows_all[tree] = rows
        print(f"  {tree}: {len(rows)} files, {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    print("building text archive ...", file=sys.stderr, flush=True)
    n, skipped = 0, []
    with _ArchiveWriter(archive_path(base, "text-content")) as tf:
        for tree in trees:
            for rel, size, sha, mt in rows_all[tree]:
                if not is_text_candidate(rel) or sha.startswith(("ERROR", "symlink")):
                    continue
                p = trees[tree] / rel
                with open(p, "rb") as fh:
                    if b"\0" in fh.read(8192):
                        skipped.append(f"{tree}/{rel}")
                        continue
                ti = tarfile.TarInfo(f"{tree}/{rel}")
                ti.size, ti.mtime, ti.mode = size, mt, 0o644
                with open(p, "rb") as fh:
                    tf.addfile(ti, fh)
                n += 1
    (base / "text-content-skipped-binary.txt").write_text("\n".join(skipped) + "\n", encoding="utf-8", newline="\n")
    print(f"  archived {n} text files, skipped {len(skipped)} (NUL bytes)", file=sys.stderr)
    ver: dict = {"captured": datetime.datetime.now().isoformat(timespec="seconds"), "game_version": version,
                 "game_appmanifest": parse_acf(env, GAME_APP_ID), "sdk_appmanifest": parse_acf(env, SDK_APP_ID),
                 "manifest_file_counts": {t: len(r) for t, r in rows_all.items()}}
    if api:
        print("assembly inventory + API surface ...", file=sys.stderr, flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = run_asminv(env, asminv, Path(tmp) / "asm")
            shutil.copy(out / "assembly-inventory.tsv", base / "assembly-inventory.tsv")
            tar_dir(out / "api", "api", archive_path(base, "api-surface"))
        lines = [ln.split("\t") for ln in (base / "assembly-inventory.tsv").read_text(encoding="utf-8").splitlines()]
        head, body = lines[0], lines[1:]
        key = {r[0]: dict(zip(head, r)) for r in body}
        ver["key_assembly_versions"] = {k[len("game/"):]: {f: key[k][f] for f in ("AssemblyVersion", "FileVersion",
                                                                                   "ProductVersion")}
                                        for k in KEY_ASSEMBLIES if k in key}
        found = {v["FileVersion"] for v in ver["key_assembly_versions"].values()}
        if found and found != {version}:
            print(f"WARNING: --version {version} but the key assemblies say {sorted(found)}", file=sys.stderr)
        ver["evidence_note"] = (f"Version string {version} comes from the managed assemblies' AssemblyVersion/"
                                "FileVersion (assembly-inventory.tsv); there is no standalone version file.")
    if decompiled:
        print("archiving the decompiled tree ...", file=sys.stderr, flush=True)
        out = archive_path(base, f"decompiled-{version}")
        tar_dir(Path(decompiled).resolve(), "decompiled", out)
        ver["decompiled_archive"] = f"{out.name} (from {decompiled})"
    (base / "version-evidence.json").write_text(json.dumps(ver, indent=1), encoding="utf-8", newline="\n")
    print(f"done in {time.time() - t0:.0f}s", file=sys.stderr)
    return base
