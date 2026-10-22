"""Stage a mod for upload: scratch copy -> SDK --prepareForPublish -> committed Scripts/ -> checks -> manifest.

The HUB's staging is an allow-list (Assets/ items with their metafiles (stripped unless DoNotStripExtensions), the
asset processing outputs + Content caches, Worlds/, the .vrgproj), plus thumb.jpg. It never adds Scripts/; we add the committed Scripts/**/*.cs from git
HEAD and nothing else, then re-check the whole tree against the rules below before anything can be uploaded.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import gitutil
from .builder import BuildResult, clean_sandbox, run_builder, win_path
from .hostenv import BuildEnv
from .project import ModProject, Refusal

# What a staged mod may contain at its top level (the HUB's allow-list + Scripts/).
def allowed_top(p: ModProject) -> set[str]:
    return {"Assets", "Assets.meta", "Content", "Worlds", "Scripts", p.vrgproj.name, "thumb.jpg"}


SCRATCH_INPUTS = ("Assets", "Assets.meta", "Content", "Content.meta", "Worlds",
                  ".hashescache", ".hashescache.json", ".referencescache", ".referencescache.json",
                  ".startupblobscache.json")
SHIPPING_PATHSPECS = ("Assets", "Assets.meta", "Content.meta", "Worlds", "Scripts")

FORBIDDEN_DIRS = {".git", "tests", "test", "source", "sources", "docs", "doc", "decompiled", "__pycache__",
                  ".pytest_cache", "node_modules", ".superpowers", ".vs", ".idea", ".vscode", "bin", "obj",
                  "previews", "preview", "backup", "backups"}
FORBIDDEN_EXT = {".dll", ".exe", ".pdb", ".so", ".dylib", ".py", ".pyc", ".sh", ".bat", ".cmd", ".ps1",
                 ".blend", ".blend1", ".md", ".env", ".pem", ".key", ".p12", ".pfx", ".kdbx", ".sln",
                 ".csproj", ".zip", ".7z", ".rar", ".tar", ".gz", ".vdf", ".acf", ".log", ".bak", ".orig"}
FORBIDDEN_NAMES = {"id_rsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc", "config.vdf", "loginusers.vdf",
                   "steam_appid.txt", "ModProject.modhub-metadata", "README", "NOTES", "LICENSE"}
PREVIEW_NAME = re.compile(r"(^|[_\-. ])previews?([_\-. ]|$)", re.I)
SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"\bghp_[A-Za-z0-9]{36}\b"), re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"), re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}"), re.compile(rb"steamLoginSecure"),
    re.compile(rb"\bsk-[A-Za-z0-9]{40,}\b"),
]
DECOMPILED_MARKERS = [b"// Decompiled with", b"ILSpy", b"dnSpy", b"#region Assembly ", b"_003C",
                      b"<PrivateImplementationDetails>", b"[CompilerGenerated]", b"C:\\BuildAgent\\"]
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
TEXT_SCAN_MAX = 4 * 1024 * 1024


@dataclass
class Limits:
    max_file_bytes: int = 256 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_files: int = 50_000


@dataclass
class ManifestEntry:
    path: str
    size: int
    sha256: str
    origin: str          # "sdk" (builder output), "git:HEAD" (Scripts), "thumbnail"


@dataclass
class Manifest:
    entries: list[ManifestEntry]

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries)

    def digest(self) -> str:
        h = hashlib.sha256()
        for e in sorted(self.entries, key=lambda e: e.path):
            h.update(f"{e.path}\t{e.size}\t{e.sha256}\n".encode())
        return h.hexdigest()

    def by_top(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for e in self.entries:
            top = e.path.split("/", 1)[0]
            n, b = out.get(top, (0, 0))
            out[top] = (n + 1, b + e.size)
        return dict(sorted(out.items()))

    def to_json(self) -> dict:
        return {"total_bytes": self.total_bytes, "files": len(self.entries), "sha256": self.digest(),
                "entries": [e.__dict__ for e in sorted(self.entries, key=lambda e: e.path)]}


@dataclass
class StageResult:
    stage_dir: Path
    manifest: Manifest
    scripts: list[str]
    head: str
    warnings: list[str] = field(default_factory=list)
    build: BuildResult | None = None
    scratch: Path | None = None

    def captured_reference(self) -> list[str]:
        """Reference content cache files --captureReferenceCache wrote into the scratch project.

        The HUB writes these into the user's project so the next publish
        ships them and keeps auto-generated data stable; they are not in the stage of the publish that captured
        them (the copy walks the project tree loaded before the capture)."""
        if self.scratch is None:
            return []
        return [rel for rel in REFERENCE_FILES if (self.scratch / rel).is_file()]


REFERENCE_FILES = ("Assets/contentcache_reference.vrb", "Assets/contentcache_reference.vrb.meta")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- git-HEAD scripts --------------------------------------------------------------------------------------------

def collect_head_scripts(repo: Path, dest: Path | None = None) -> tuple[list[str], list[str]]:
    """Write every committed Scripts/**/*.cs blob at HEAD into dest/Scripts (if dest). Returns (paths, warnings).

    Untracked or modified working-tree scripts are never used; they are only reported."""
    warnings: list[str] = []
    rows = gitutil.ls_tree(repo, "HEAD", "Scripts")
    shipped: list[str] = []
    for mode, typ, sha, path in rows:
        if typ != "blob":
            continue
        if mode == "120000":
            raise Refusal(f"{path} is a symlink in git; scripts must be plain files")
        if not path.endswith(".cs"):
            warnings.append(f"{path}: not a .cs file, not shipped")
            continue
        data = gitutil.cat_blob(repo, sha)
        if data.startswith(LFS_POINTER):
            raise Refusal(f"{path} is a git-lfs pointer, not source")
        for marker in DECOMPILED_MARKERS:
            if marker in data:
                raise Refusal(f"{path} contains '{marker.decode(errors='replace')}', which looks like decompiled "
                              "code; it must not ship")
        shipped.append(path)
        if dest is not None:
            out = dest / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
    for xy, path in gitutil.status_porcelain(repo, ["Scripts"]):
        what = "untracked" if xy == "??" else "uncommitted changes in"
        warnings.append(f"{what} {path} - NOT shipped (only git HEAD is)")
    return sorted(shipped), warnings


# --- checks ------------------------------------------------------------------------------------------------------

def check_tree(root: Path, p: ModProject, limits: Limits, origins: dict[str, str]) -> tuple[Manifest, list[str]]:
    """Walk the staged tree, refuse anything that must not ship, and build the manifest."""
    errors: list[str] = []
    entries: list[ManifestEntry] = []
    allowed = allowed_top(p)
    for top in sorted(x.name for x in root.iterdir()):
        if top not in allowed:
            errors.append(f"unexpected top-level entry '{top}' (allowed: {', '.join(sorted(allowed))})")
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if path.is_symlink():
            errors.append(f"{rel}: symlink")
            continue
        if path.is_dir():
            if parts[-1].lower() in FORBIDDEN_DIRS:
                errors.append(f"{rel}/: '{parts[-1]}' folders must not ship")
            continue
        name = parts[-1]
        ext = path.suffix.lower()
        if any(seg.lower() in FORBIDDEN_DIRS for seg in parts[:-1]):
            continue  # already reported at the directory
        if ext in FORBIDDEN_EXT:
            errors.append(f"{rel}: '{ext}' files must not ship")
        if name in FORBIDDEN_NAMES or path.stem in FORBIDDEN_NAMES:
            errors.append(f"{rel}: must not ship")
        if PREVIEW_NAME.search(path.stem):
            errors.append(f"{rel}: preview renders must not ship")
        if ext == ".cs" and parts[0] != "Scripts":
            errors.append(f"{rel}: C# outside Scripts/ (decompiled or stray source?)")
        if parts[0] == "Scripts" and ext != ".cs":
            errors.append(f"{rel}: only .cs files may ship under Scripts/")
        size = path.stat().st_size
        if size > limits.max_file_bytes:
            errors.append(f"{rel}: {size} bytes is over the per-file limit {limits.max_file_bytes}")
        with path.open("rb") as f:
            head = f.read(64)
        if head[:2] == b"MZ":
            errors.append(f"{rel}: Windows executable/DLL (Keen DLLs must never ship)")
        if head[:4] == b"\x7fELF":
            errors.append(f"{rel}: ELF binary")
        if size <= TEXT_SCAN_MAX:
            data = path.read_bytes()
            if data.startswith(LFS_POINTER):
                errors.append(f"{rel}: git-lfs pointer (run `git lfs pull` before staging)")
            for pat in SECRET_PATTERNS:
                if pat.search(data):
                    errors.append(f"{rel}: looks like it contains a secret ({pat.pattern[:30]!r})")
            if ext == ".cs":
                for marker in DECOMPILED_MARKERS:
                    if marker in data:
                        errors.append(f"{rel}: decompiled-code marker '{marker.decode(errors='replace')}'")
        entries.append(ManifestEntry(rel, size, _sha256(path), origins.get(rel, "sdk")))
    man = Manifest(entries)
    if len(entries) > limits.max_files:
        errors.append(f"{len(entries)} files is over the limit {limits.max_files}")
    if man.total_bytes > limits.max_total_bytes:
        errors.append(f"total {man.total_bytes} bytes is over the limit {limits.max_total_bytes}")
    for required in (p.vrgproj.name, "Content/contentcache.vrb", "Content/definitionsets.vrb"):
        if not (root / required).exists():
            errors.append(f"missing {required} (the builder's prepare step did not complete)")
    return man, errors


def dirty_shipping_inputs(p: ModProject) -> list[str]:
    specs = [s for s in SHIPPING_PATHSPECS if (p.folder / s).exists()] + [p.vrgproj.name]
    return [f"{xy.strip() or '?'} {path}" for xy, path in gitutil.status_porcelain(p.folder, specs)]


# --- scratch copy ------------------------------------------------------------------------------------------------

def make_scratch(p: ModProject, scratch: Path, env: BuildEnv, standin: Path) -> Path:
    """Copy only what the builder needs (never Scripts/, Source/, Tests/, docs/, .git) and point the copy at the
    SDK's Vanilla/Engine projects and the game binaries, as the Editor would via its command line."""
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    for name in SCRATCH_INPUTS:
        src = p.folder / name
        if src.is_dir():
            shutil.copytree(src, scratch / name, symlinks=True)
        elif src.is_file():
            shutil.copy2(src, scratch / name)
    # standin: <standin>/SDK/GameData/{Vanilla,Engine} -> SDK projects; <standin>/SDK/Game2 and <standin>/Game2 -> game
    # binaries, so the SDK projects' relative "..\..\Game2" / "..\..\..\Game2" search paths resolve.
    if standin.exists():
        shutil.rmtree(standin)
    (standin / "SDK/GameData").mkdir(parents=True)
    (standin / "SDK/GameData/Vanilla").symlink_to(env.sdk_dir / "GameData/Vanilla")
    (standin / "SDK/GameData/Engine").symlink_to(env.sdk_dir / "GameData/Engine")
    (standin / "SDK/Game2").symlink_to(env.game_dir / "Game2")
    (standin / "Game2").symlink_to(env.game_dir / "Game2")       # Engine.vrgproj looks in ..\..\..\Game2
    data = json.loads(p.vrgproj.read_text(encoding="utf-8-sig"))
    v = data["$Value"]
    dirs = v.setdefault("ProjectDirectories", [])
    sd = win_path(standin / "SDK/GameData")
    if sd not in dirs:
        dirs.append(sd)
    game_bin = win_path(env.game_dir / "Game2")
    for key in ("GameExecutableSearchPaths", "ReferencedAssembliesSearchPaths"):
        paths = v.setdefault(key, [])
        if game_bin not in paths:
            paths.append(game_bin)
    proj = scratch / p.vrgproj.name
    proj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return proj


def stage(p: ModProject, env: BuildEnv, work: Path, thumbnail: bytes | None, *, sandbox: bool,
          limits: Limits | None = None, prebuild: bool = True, timeout: int = 2700,
          keep_sandbox: bool = False) -> StageResult:
    limits = limits or Limits()
    work.mkdir(parents=True, exist_ok=True)
    head = gitutil.head_sha(p.folder)
    scratch = work / "src" / p.folder.name
    stage_dir = work / "stage"
    proj = make_scratch(p, scratch, env, work / "standin")
    if stage_dir.exists():
        shutil.rmtree(stage_dir)      # --prepareForPublish refuses an existing publish directory
    sandbox_dir = work / "sandbox"
    try:
        build = run_builder(env, proj, stage_dir, work / "builder.log", sandbox=sandbox, sandbox_dir=sandbox_dir,
                            prebuild=prebuild, timeout=timeout)
    finally:
        if sandbox and not keep_sandbox:
            clean_sandbox(sandbox_dir)
    if build.returncode == 0 and not (stage_dir / p.vrgproj.name).exists():
        # The builder can log "Publish preparation ... Project validation failed" and still exit 0.
        lines = build.log.read_text(errors="replace").splitlines()
        fails = [ln.strip() for ln in lines if " fail: " in ln or "Error:" in ln][-15:]
        raise Refusal("SDK --prepareForPublish did not produce a stage (exit 0, but it logged a failure); "
                      f"log {build.log}:\n  " + "\n  ".join(fails or lines[-15:]))
    if build.returncode != 0:
        lines = build.log.read_text(errors="replace").splitlines()
        tail = [ln for ln in lines if " fail: " in ln or "error" in ln.lower()][-15:] or lines[-25:]
        raise Refusal(f"SDK builder failed (exit {build.returncode}); log {build.log}:\n  " + "\n  ".join(tail))
    res = finish_stage(p, stage_dir, thumbnail, head, limits, build)
    res.scratch = scratch
    return res


def finish_stage(p: ModProject, stage_dir: Path, thumbnail: bytes | None, head: str, limits: Limits,
                 build: BuildResult | None = None) -> StageResult:
    """Everything after the builder: restore the real .vrgproj, add HEAD scripts and the thumbnail, check, list."""
    warnings: list[str] = []
    # The staged project file must be the user's, not the scratch copy pointed at our standin paths.
    staged_proj = stage_dir / p.vrgproj.name
    if staged_proj.exists():
        staged_proj.write_bytes(p.vrgproj.read_bytes())
    if (stage_dir / "Scripts").exists():
        raise Refusal("the builder output already contains Scripts/; refusing to mix it with git HEAD scripts")
    scripts, w = collect_head_scripts(p.folder, stage_dir)
    warnings += w
    origins = {s: "git:HEAD" for s in scripts}
    if thumbnail is not None:      # the committed thumbnail bytes (read from git HEAD by the caller)
        (stage_dir / "thumb.jpg").write_bytes(thumbnail)
        origins["thumb.jpg"] = "thumbnail"
    man, errors = check_tree(stage_dir, p, limits, origins)
    if errors:
        raise Refusal("staged content failed the safety checks:\n  " + "\n  ".join(errors))
    (stage_dir.parent / "manifest.json").write_text(json.dumps(man.to_json(), indent=1) + "\n")
    return StageResult(stage_dir, man, scripts, head, warnings, build)
