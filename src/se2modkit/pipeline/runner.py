"""`Runner`: the build flow a mod's build script runs inside Blender.

    se2mod blender run Source/Model/<build>.py -- [BLOCK|PREFIX* ...|all] [--no-render] [--export] [--check-placement]

A mod hands it its hooks, builders and catalogue (the scaffolded build script does exactly that):

    Runner(hooks=hooks.build_hooks(), builders=catalog.BUILDERS, catalogue=catalog.catalogue).main(argv)

Per selected block: the family's LOD builder (`builders[family](spec, lod) -> [model Part]`, the LOD count fitted to
Keen's checker by `assemble.fit_lods`), its collision boxes (`collision[family](spec) -> {"regular", "occupancy",
"mount": [(name, lo, hi)]}`, Blender frame), the LOD / limits / placement entry, the family's `extras` (subparts,
geometry keys), then the z-fight audit (`zfight.audit_built`). A family may instead have a whole-block builder
(`block_builders[family](runner, spec, render) -> report entry`, which stores its objects in `runner.built`).
Words in `targets` (`Target`) build non-block targets (their report key, their export step). Only stale blocks are
built: a block whose inputs (`incremental.block_inputs`: its builder and the sources it imports, its catalogue entry,
its hooks tables, the modkit, the style profile, the runtime) match its last fragment keeps that fragment's entry
(`--force` rebuilds all). Blocks not selected keep their last entry in build_report.json (`report.merged_blocks`),
written with the modkit provenance (`report.write`); the bytes are those of a full build. What is tracked and what a
mod must declare (`inputs=`, `pipeline.inputs`) and what never counts (`outputs=`: globs, relative to the mod folder, of
files the build writes itself; a file the build writes but does not declare stays an input, keyed by its content):
docs/incremental-builds.md. `--export` writes `<models>/<name>/` for every block built in the run (`export.export_block`;
Keen's VRAGE Tools add-on needs a GPU context: run Blender windowed). `--check-placement` re-runs the
placement analysis on the exported files of the selected blocks (`placement_check.check`). Unless `--no-render`, the
mod's `preview(built, tag)` renders (tag: $SE2MODKIT_PREVIEW, or the legacy
$<prefix>PREVIEW, else "adhoc").

The module imports offline (selection and the command line are plain Python); building needs Blender.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from . import incremental as IN
from . import report as RP
from .hooks import HooksError, ModHooks

__all__ = ["DONE", "FAILED", "OPTIONS", "Runner", "Target", "USAGE", "VALUE_OPTIONS", "script_argv"]

OPTIONS = ("--no-render", "--export", "--check-placement", "--force", "--plan", "--blocks-only")
VALUE_OPTIONS = ("--status", "--worker", "--adopt")
USAGE = """Build the mod's models (inside Blender).

    se2mod blender run Source/Model/<build>.py -- [BLOCK|PREFIX* ...|all] [--no-render] [--force]
    se2mod blender run --windowed Source/Model/<build>.py -- [BLOCK ...|all] --no-render --export
    se2mod blender run Source/Model/<build>.py -- [BLOCK ...|all] --check-placement
    se2mod build-models MOD [BLOCK ...] [--jobs N] [--force] [--export]     (parallel headless workers)
    se2mod build-models MOD --release       (what a publish needs: clean tree, every block rebuilt and exported, stamped)

BLOCK is a block name, PREFIX* every catalogue block starting with PREFIX (quote it in the shell), "all" (or nothing)
the pipeline's blocks. --export writes the game models (FBX, collision glTF, export.json) of every block built in the
run; it needs Keen's VRAGE Tools add-on, so run Blender windowed. --check-placement checks the exported files'
placement instead of building. --no-render skips the previews. Only stale blocks are built (any input changed since
their last build, see se2modkit.pipeline.incremental); --force builds every selected block. --status=FILE writes what
was stale and built as JSON; --plan stops before building when a block is stale; --worker=DIR builds the named blocks
into fragments in DIR (no report); --adopt=DIR takes such fragments in first."""


DONE, FAILED = "=== BUILD DONE ===", "=== BUILD FAILED ==="   # the windowed run's log markers


def script_argv(argv: list[str] | None = None) -> list[str]:
    """The build script's own arguments: those after "--" (Blender passes its own before it), else argv[1:]."""
    argv = sys.argv if argv is None else argv
    return argv[argv.index("--") + 1:] if "--" in argv else argv[1:]


@dataclass(frozen=True)
class Target:
    """A non-block build target a word on the command line names (e.g. a mod's item models): `build(runner)` returns
    the value stored under the top-level report key `key` before the report is written; `export(runner, ops)` runs
    after the blocks' export. A run that names only targets builds no block (the word does not mean "all")."""
    key: str
    build: Callable
    export: Callable | None = None


def _write_status(path: Path | None, status: dict) -> None:
    if path is not None:
        RP.write_json(path, status)


class _LodTable:
    """The LOD-count object assemble.fit_lods expects: n_lods(spec) and LOD_COUNT (per block, set by fit_lods)."""

    def __init__(self, default):
        self.default, self.LOD_COUNT = default, {}

    def n_lods(self, spec):
        return self.LOD_COUNT.get(spec.name, self.default)


class Runner:
    """One build run. `built` keeps the objects of every block built in the run, by name: {"lods": [(coll, objs)],
    "collision": [(kind, ob)], "boxes", "root"[, "subparts", "subpart_collision", "mid"]}."""

    def __init__(self, hooks: ModHooks, builders: dict[str, Callable], catalogue: Callable[[], list], *,
                 collision: dict[str, Callable] | None = None, extras: dict[str, list[Callable]] | None = None,
                 block_builders: dict[str, Callable] | None = None, pipeline_blocks: Callable[[], list] | None = None,
                 bounds: Callable | None = None, placement: Callable | None = None, analyse: Callable | None = None,
                 audits: list[Callable] | None = None, post_audits: list[Callable] | None = None,
                 preview: Callable | None = None, report_writer: Callable[[Path, dict], None] | None = None,
                 targets: dict[str, Target] | None = None, n_lods: int = 4,
                 inputs: Callable[[object], list] | None = None, outputs: list[str] | tuple[str, ...] = ()):
        if not isinstance(hooks, ModHooks):
            raise HooksError(f"The build needs the mod's se2modkit.pipeline.ModHooks, not a {type(hooks).__name__}: "
                             "make build_hooks() return ModHooks.for_mod(...).")
        hooks.check()
        self.hooks = hooks
        self.builders = dict(builders)
        self.catalogue = catalogue
        self.collision = dict(collision or {})
        self.extras = {k: list(v) for k, v in (extras or {}).items()}
        self.block_builders = dict(block_builders or {})
        self.pipeline_blocks = pipeline_blocks
        self.bounds = bounds
        self.placement_fn = placement
        self.analyse = analyse
        self.audits = list(audits or ())
        self.post_audits = list(post_audits or ())
        self.targets = dict(targets or {})
        self.preview = preview
        self.report_writer = report_writer
        self.lods = _LodTable(n_lods)
        self.built: dict[str, dict] = {}
        self.bevel_hazards: dict[str, int] = {}
        self.inputs = inputs
        if isinstance(outputs, (str, os.PathLike)):
            outputs = [outputs]
        self.output_globs = tuple(IN.output_glob(g) for g in outputs)   # files the mod's build writes itself
        self._store: IN.Store | None = None
        self._run_outputs: list[Path] = []     # this run's status file and worker folder
        self._run_written: set[str] = set()    # a merge run: what the workers wrote (their own writes, not an edit)

    # ------------------------------------------------------------------------------------------------ selection
    def _families(self) -> set[str]:
        return set(self.builders) | set(self.block_builders)

    def default_blocks(self) -> list:
        """The pipeline's blocks ("all"): `pipeline_blocks()`, else every catalogue block whose family has a
        builder."""
        if self.pipeline_blocks is not None:
            return list(self.pipeline_blocks())
        fams = self._families()
        return [b for b in self.catalogue() if b.family in fams]

    def select(self, names: list[str]) -> list:
        """The specs to build: "all" (or nothing) = the pipeline's blocks; otherwise each name, where a name ending in
        "*" is a prefix over the whole catalogue. Catalogue order, no duplicates. An unknown name raises SystemExit
        with the known names."""
        if not names or names == ["all"]:
            return self.default_blocks()
        cat = list(self.catalogue())
        known = [b.name for b in cat]
        picked, missing = set(), []
        for n in names:
            hits = {k for k in known if k.startswith(n[:-1])} if n.endswith("*") else {n} & set(known)
            if not hits:
                missing.append(n)
            picked |= hits
        if missing:
            raise SystemExit(f"Unknown block(s): {', '.join(missing)}. The catalogue has: {', '.join(known)}.")
        return [b for b in cat if b.name in picked]

    # ------------------------------------------------------------------------------------------------ Blender
    def install_bevel_guard(self):
        """model.objects.part_to_object bevels with clamp-overlap, which clamps the WHOLE part: one zero-length edge
        silently kills every bevel of that Part. Wrap part_to_object once and record such edges per object name."""
        from ..model import objects as OB
        orig = getattr(OB.part_to_object, "_se2_orig", OB.part_to_object)
        hazards = self.bevel_hazards

        def guarded(p, coll, name, *a, **k):
            if p.bevel[0] > 0 and p.bevel[1] > 0:
                n = sum(1 for e in p.bm.edges if e.calc_length() < 1e-6)
                if n:
                    hazards[name] = n
            return orig(p, coll, name, *a, **k)
        guarded._se2_orig = orig
        OB.part_to_object = guarded

    def env(self, name: str) -> str | None:
        """`SE2MODKIT_<name>`, else the legacy `<prefix><name>` (e.g. XX_PREVIEW; accepted for one release)."""
        return os.environ.get(f"SE2MODKIT_{name}") or os.environ.get(f"{self.hooks.prefix}{name}") or None

    def bevel_guard(self, spec) -> dict:
        return {k: v for k, v in self.bevel_hazards.items() if k.startswith(spec.name + "_")}

    def placement(self, spec, lod0_objs, boxes):
        """(report entry, raw analysis) of the block's placement: the mod's `placement`, else assemble.placement
        on its `bounds(spec)`."""
        if self.placement_fn is not None:
            return self.placement_fn(spec, lod0_objs, boxes)
        if self.bounds is None:
            raise SystemExit(f"{spec.name}: the runner needs bounds=fn(spec) -> cell bounds (or placement=...).")
        from . import assemble
        return assemble.placement(spec, lod0_objs, boxes, self.bounds(spec))

    def build_block(self, spec, render: bool) -> dict:
        """Build one block (every LOD, collision, report entry); `built[name]` keeps the objects for export and
        previews."""
        if spec.family in self.block_builders:
            return IN.call_builder(self.block_builders[spec.family], self, spec, render)
        from ..model import objects as OB
        from . import assemble
        if spec.family not in self.collision:
            raise SystemExit(f"{spec.name}: no collision boxes for family {spec.family} (Runner collision=...).")
        root = OB.new_collection(spec.name)
        # through call_builder: a read counts for this block alone only along the builder's own path from this call
        lods = assemble.fit_lods(spec, partial(IN.call_builder, self.builders[spec.family]), self.lods, root)
        boxes = self.collision[spec.family](spec)
        ccoll = OB.new_collection(f"{spec.name}_collision", root)
        cobjs = assemble.collision_objects(spec, boxes, ccoll)
        entry = RP.empty_entry(spec)
        entry["lods"], lim = assemble.lod_report(spec, lods)
        entry["model_size"] = lim["size"]
        entry["render_bounds"] = assemble.render_bounds(lods[0][1])
        entry["placement"], raw = self.placement(spec, lods[0][1], boxes)
        entry["placement"]["mount_cells"] = {d: [list(c) for c in f.get("mount_cell_list", [])]
                                             for d, f in raw["faces"].items()}
        entry["colliders"] = {"regular": len(boxes["regular"]), "max": lim["colliders"]["max"],
                              "occupancy_hints": len(boxes["occupancy"]), "mount_hints": len(boxes["mount"])}
        self.built[spec.name] = {"lods": lods, "collision": cobjs, "boxes": boxes, "root": root}
        for fn in self.extras.get(spec.family, ()):
            fn(spec, entry, self.built[spec.name])
        entry["bevel_guard"] = self.bevel_guard(spec)
        print("PLACEMENT", entry["placement"]["summary"])
        return entry

    def audit(self, spec) -> dict:
        """The block's z-fight entry: zfight.audit_built's, with the mod's `audits` (fn(spec, built) -> {key: value},
        run before it, while the decal tags are still on the meshes) and `post_audits` (the same, run after it, on the
        stripped meshes) merged in. List values are fault rows (printed); other values are recorded only."""
        from . import zfight
        built = self.built.get(spec.name)
        extra = {}
        for fn in self.audits:
            extra.update(fn(spec, built) or {})
        zf = zfight.audit_built(self.hooks, spec, built)
        for fn in self.post_audits:
            extra.update(fn(spec, built) or {})
        zf.update(extra)
        bad = zfight.conflicts(zf) + tuple(v for k, v in sorted(extra.items()) if isinstance(v, list))
        print(f"  zfight: {len(zf['elements'])} decal elements; conflicts {[len(b) for b in bad]}")
        for b in bad:
            for row in b[:40]:
                print("   ", row)
        return zf

    def vrt_ops(self):
        """Keen's VRAGE Tools operators (model.export.vrt_ops; None when the add-on is not loaded)."""
        from ..model import export as EX
        return EX.vrt_ops()

    def export(self, ops=None) -> list[dict]:
        """Export every block built in the run (export.export_block); returns the manifests."""
        from . import export as XP
        ops = self.vrt_ops() if ops is None else ops
        by_name = {b.name: b for b in self.catalogue()}
        out = []
        for name, b in self.built.items():
            m = XP.export_block(self.hooks, by_name[name], b["lods"], b["collision"], b["boxes"], ops,
                                subparts=b.get("subparts"), subpart_collision=b.get("subpart_collision"),
                                mid=b.get("mid"))
            print(f"exported {name}: NF {[lod['tris'] for lod in m['nonfractured']['lods']]} tris, "
                  f"{m['nonfractured']['collision']['colliders']} colliders; Fractured "
                  f"{[lod['tris'] for lod in m['fractured']['lods']]} tris, zones "
                  f"{[z['id'] for z in m['fractured']['zones']]}"
                  + (f"; Damaged {[lod['tris'] for lod in m['damaged']['lods']]} tris, dents {m['damaged']['dent']}"
                     if m.get("damaged") else ""))
            out.append(m)
        return out

    def check_placement(self, specs) -> list[dict]:
        """placement_check.check on every selected block that has an export.json; the runner's `analyse` is called
        as analyse(spec, tris, regular, occupancy, mount, fracture_hulls=(), *, bounds, shift) (per block: e.g. its
        mount directions)."""
        from . import placement_check as PC
        if self.bounds is None:
            raise SystemExit("--check-placement needs the runner's bounds=fn(spec) -> cell bounds.")
        models = Path(self.hooks.paths.models)
        return [PC.check(self.hooks, s, self.bounds(s), analyse=partial(self.analyse, s) if self.analyse else None)
                for s in specs if (models / s.name / "export.json").is_file()]

    @staticmethod
    def require_blender():
        try:
            import bpy  # noqa: F401
        except ImportError:
            raise SystemExit("The model build runs inside Blender: se2mod blender run Source/Model/<build>.py -- "
                             "<blocks>.") from None

    def prepare(self):
        """Inside Blender, before the first block: the bevel guard and the z-fight decal tags."""
        from . import zfight
        self.install_bevel_guard()
        zfight.install(self.hooks)       # tag decal quads per element (measured below, stripped before export)

    # ------------------------------------------------------------------------------------------------ main
    def _read_report(self) -> dict:
        import json
        path = Path(self.hooks.paths.report)
        if path.is_file():   # merge: blocks not rebuilt keep their last entry
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop(RP.PROVENANCE_KEY, None)
            return data
        return {"blocks": {}}

    def store(self) -> IN.Store:
        """The block fragments of this mod's report (`incremental.Store`; `$SE2MODKIT_BUILD_STATE` moves them)."""
        if self._store is None:
            self._store = IN.Store.for_report(Path(self.hooks.paths.report), self.hooks.name)
        return self._store

    def block_callables(self, spec) -> list:
        """What the runner calls to build `spec` (part of its key: their identity, defaults and source files)."""
        fam = spec.family
        return [self.block_builders.get(fam) or self.builders.get(fam), self.collision.get(fam),
                *self.extras.get(fam, ()), *self.audits, *self.post_audits, self.placement_fn, self.bounds,
                self.catalogue, self.pipeline_blocks]

    def sources(self, specs=None) -> IN.Sources:
        """The run's shared `incremental.Sources`: every callable the runner and the hooks hold (all families, so a
        block's key never depends on what else is selected) explains the loaded mod modules it imports; anything
        loaded that none explains counts for every block."""
        import dataclasses
        files: set[Path] = set()
        held = [self.builders, self.collision, self.extras, self.block_builders, self.audits, self.post_audits,
                self.placement_fn, self.bounds, self.catalogue, self.pipeline_blocks, self.inputs]
        held += [getattr(self.hooks, f.name) for f in dataclasses.fields(self.hooks) if f.compare]
        for value in held:
            try:
                IN.canon(value, files)
            except IN.Unstable:
                pass
        paths = self.hooks.paths
        return IN.Sources(Path(paths.source_model) if paths.source_model else None,
                          Path(paths.root) if paths.root else None, files)

    def block_inputs(self, spec, sources: IN.Sources | None = None) -> dict[str, str]:
        """The block's named input hashes (`incremental.block_inputs`)."""
        data = list(self.inputs(spec) or ()) if self.inputs is not None else []
        return IN.block_inputs(self.hooks, spec, self.block_callables(spec), data=data,
                               sources=sources if sources is not None else self.sources())

    def _output_dirs(self) -> list[Path]:
        p = self.hooks.paths
        return [Path(p.models), Path(p.report), self.store().root, IN.lock_path(Path(p.report)).parent,
                *self._run_outputs]

    def mod_dir(self) -> Path | None:
        p = self.hooks.paths
        return Path(p.root) if p.root else (Path(p.source_model) if p.source_model else None)

    def outputs(self) -> IN.Outputs:
        """What a build writes, never an input (`incremental.Outputs`): the models, the report, the fragments and the
        mod's `outputs=` globs (relative to the mod folder). Nothing else: a file the build writes and reads back
        stays an input, keyed by its content like any other."""
        return IN.Outputs(dirs=self._output_dirs(), globs=self.output_globs, root=self.mod_dir())

    def written_inputs(self) -> set[str]:
        """The paths this process wrote during the run that count as inputs when read (not outputs, not temp)."""
        return IN.input_paths(IN.written(), outputs=self.outputs(), mod_dir=self.mod_dir())

    def own_code(self, spec) -> IN.OwnCode:
        """The block's own code: its family's builder (or block builder) as `incremental.own_code` unwraps it. A read
        made by that code, with only it and I/O plumbing between the read and `build_block`'s call into the builder,
        counts for this block only; any other read counts for every later block of the process too."""
        fam = spec.family
        return IN.own_code(self.block_builders.get(fam) or self.builders.get(fam))

    def warn_rewritten(self, frags: dict, written: set[str]) -> list[str]:
        """One warning per file this run's build wrote after a block read it (that block rebuilds on the next run;
        a file the build changes every run, like a shared log, rebuilds every run)."""
        out = []
        root = self.mod_dir()
        for e, blocks in IN.rewritten(frags, written, self.outputs()).items():
            body = Path(e.removeprefix("dir:"))
            try:
                shown = body.relative_to(root).as_posix() if root is not None else body.as_posix()
            except ValueError:
                shown = body.as_posix()
            shown += "/" if e.startswith("dir:") else ""
            out.append(f"{shown} was changed by this build after {', '.join(blocks)} read it, so "
                       f"{'they rebuild' if len(blocks) > 1 else 'it rebuilds'} on the next run (every run, if the "
                       "build changes it each time); if it is a build output (a log, a cache), declare it in "
                       "Runner(outputs=[...])")
        for w in out:
            print(f"warning: {w}")
        return out

    def stale(self, specs, *, force: bool, export: bool) -> tuple[dict, dict, dict]:
        """({name: inputs}, {name: last fragment}, {name: [reasons]}) for `specs`; a block with no reason is fresh.
        A block is stale when an input changed, a file its last build read changed or went away, or (with `export`)
        its models are not what an export under its current key wrote."""
        store = self.store()
        sources = self.sources()
        inputs = {s.name: self.block_inputs(s, sources) for s in specs}
        frags = {s.name: store.load(s.name) for s in specs}
        models = Path(self.hooks.paths.models)
        reasons = {}
        for s in specs:
            frag = frags[s.name]
            why = ["forced"] if force else IN.stale_reasons(inputs[s.name], frag)
            changed = [] if why else IN.reads_changed(frag.get("reads"), self.outputs(), ignore=self._run_written)
            if changed:
                why = ["files read: " + ", ".join(Path(x.removeprefix("dir:")).name + ("/" if x.startswith("dir:")
                                                                                      else "") for x in changed[:3])]
            if not why and export and not IN.export_fresh(frag, inputs[s.name], models / s.name):
                why = ["models not exported under this key"]
            reasons[s.name] = why
        return inputs, frags, reasons

    def adopt(self, *folders: Path) -> list[str]:
        """Copy the fragments the workers wrote in `folders` into the store (their keys are checked like any other)
        and take in the paths they wrote (`_run_written`). A file more than one worker wrote is keyed as
        `incremental.SEVERAL_WRITERS` in the fragments of those workers' blocks that read it: what such a block saw
        cannot be told, so it rebuilds on the next run."""
        dst = self.store()
        known = {b.name for b in self.catalogue()}
        stores = [IN.Store(f) for f in folders]
        wrote = [st.load_written() for st in stores]
        out = []
        for i, src in enumerate(stores):
            self._run_written |= wrote[i]
            mine = IN.path_forms(wrote[i])
            others = IN.path_forms(set().union(*(w for j, w in enumerate(wrote) if j != i)))
            for name in src.blocks():
                frag = src.load(name)
                if name not in known or frag is None:
                    continue
                if mine and others:
                    reads = frag.get("reads") or {}
                    for e in reads:
                        if IN.touched_by(e, mine) and IN.touched_by(e, others):
                            reads[e] = IN.SEVERAL_WRITERS
                dst.save(name, frag)
                out.append(name)
        return out

    def _build_entry(self, spec, render: bool) -> tuple[dict, dict]:
        """(the block's report entry, {file: hash} of the files its build read)."""
        outputs = self.outputs()
        mod_dir = self.mod_dir()
        with IN.recording(own=self.own_code(spec), outputs=outputs, mod_dir=mod_dir) as seen:
            entry = self.build_block(spec, render)
            entry["zfight"] = self.audit(spec)
        print(f"built {spec.name}")
        # read outside any block (e.g. a catalogue's data file): every block; read by shared code while some block
        # built (a helper, a cached loader, a lazily loaded module): that block and every later one; read from the
        # block's own builder code: this block
        pinned: set[str] = set()
        proc = IN.process_reads(outputs=outputs, mod_dir=mod_dir, pinned=pinned)
        reads = dict(proc)
        reads.update(IN.lazy_imports(outputs=outputs, mod_dir=mod_dir, written=seen.written))
        reads.update(IN.block_reads(seen, outputs=outputs, mod_dir=mod_dir))
        # what was read at import time (or is unknown) wins over the block's own record of the same file: a block that
        # rewrote its own import-time input was built from the old content too, so it rebuilds once more
        reads.update({k: proc[k] for k in pinned})
        return entry, reads

    @staticmethod
    def _parse(argv: list[str]) -> tuple[set[str], dict[str, list[str]], list[str]]:
        flags, values, words, unknown = set(), {k: [] for k in VALUE_OPTIONS}, [], []
        for a in argv:
            if not a.startswith("--"):
                words.append(a)
            elif a in OPTIONS:
                flags.add(a)
            elif a.split("=", 1)[0] in VALUE_OPTIONS and "=" in a and a.split("=", 1)[1]:
                k, v = a.split("=", 1)
                values[k].append(v)
            else:
                unknown.append(a)
        if unknown:
            raise SystemExit(f"Unknown option(s) {' '.join(unknown)}; the options are {', '.join(OPTIONS)}, "
                             f"{', '.join(k + '=PATH' for k in VALUE_OPTIONS)}.")
        return flags, values, words

    def run(self, argv: list[str]) -> int:
        """Build, report, export and render (raises SystemExit with a plain message on a usage error). Only stale
        blocks are built (all with --force); the others keep the entry of their fragment. Holds the mod's build lock
        (a --worker writes only its own folder and takes none)."""
        flags, values, words = self._parse(argv)
        if values["--worker"]:
            with IN.run_scope():
                return self._run(flags, values, words)
        try:
            with IN.build_lock(Path(self.hooks.paths.report), self.hooks.name), IN.run_scope():
                return self._run(flags, values, words)
        except IN.LockHeld as e:
            raise SystemExit(str(e)) from None

    def _run(self, flags: set[str], values: dict[str, list[str]], words: list[str]) -> int:
        render, do_export, force = "--no-render" not in flags, "--export" in flags, "--force" in flags
        status_path = Path(values["--status"][-1]) if values["--status"] else None
        worker = Path(values["--worker"][-1]) if values["--worker"] else None
        self._run_outputs = [p for p in (status_path, worker) if p is not None]
        if "--blocks-only" in flags:     # exact block names only (se2mod check): no "all", prefix or target word
            bad = [w for w in words if w == "all" or w.endswith("*") or w in self.targets]
            known = {b.name for b in self.catalogue()}
            bad += [w for w in words if w not in bad and w not in known]
            if bad or not words:
                raise SystemExit(f"Not a block name: {', '.join(bad) or '(none given)'}; name one block of the "
                                 "catalogue exactly (no \"all\", prefix* or target word).")
        targets = [self.targets[w] for w in dict.fromkeys(words) if w in self.targets]
        words = [w for w in words if w not in self.targets]
        specs = self.select(words) if words or not targets else []   # targets alone: no block ("all" not implied)
        if "--check-placement" in flags:
            self.require_blender()
            done = self.check_placement(specs)
            print(f"checked the placement of {len(done)} exported blocks")
            return 0
        if not specs and not targets:
            raise SystemExit("No block to build: the catalogue is empty.")
        fams = self._families()
        nobuilder = sorted({s.family for s in specs} - fams)
        if nobuilder:
            raise SystemExit(f"There is no builder for family {', '.join(nobuilder)} (Runner builders=...).")
        if worker is not None and (do_export or targets):
            raise SystemExit("--worker builds blocks only: drop --export and the target words.")
        # a merge run: the workers' writes are the build's own, not an edit made while it ran; a block that read such
        # a file before it changed is fresh for this merge and rebuilds on the next run (its fragment keeps the old hash)
        self._run_written = set()
        if values["--adopt"]:
            self.adopt(*(Path(f) for f in values["--adopt"]))
        inputs, frags, reasons = self.stale(specs, force=force or worker is not None, export=do_export)
        stale = [s for s in specs if reasons[s.name]]
        status = {"selected": [s.name for s in specs], "stale": [s.name for s in stale],
                  "reasons": {s.name: reasons[s.name] for s in stale}, "built": [],
                  "seconds": {s.name: (frags[s.name] or {}).get("seconds") for s in stale}}
        for s in stale:
            print(f"stale {s.name}: {', '.join(reasons[s.name])}")
        if len(stale) < len(specs):
            print(f"up to date: {len(specs) - len(stale)} block(s) (use --force to rebuild)")
        if values["--adopt"]:
            status["warnings"] = self.warn_rewritten(frags, self._run_written)
        if "--plan" in flags and stale:          # the caller builds them (parallel workers), then runs again
            _write_status(status_path, status)
            print(f"plan: {len(stale)} stale block(s)")
            return 0
        if stale or targets:
            self.require_blender()
            self.prepare()
        if worker is not None:                   # a parallel worker: fragments only, no report
            out = IN.Store(worker)
            for spec in stale:
                t0 = time.monotonic()
                entry, reads = self._build_entry(spec, render=False)
                out.save(spec.name, IN.fragment(spec.name, inputs[spec.name], IN.normalise(entry), reads=reads,
                                                seconds=time.monotonic() - t0))
                status["built"].append(spec.name)
            written = self.written_inputs()
            if written:
                out.save_written(written)
            _write_status(status_path, status)
            return 0
        data = self._read_report()
        data.setdefault("blocks", {})
        new: dict[str, dict] = {}
        for spec in specs:
            if spec.name in status["reasons"]:
                t0 = time.monotonic()
                entry, reads = self._build_entry(spec, render)
                try:
                    entry = IN.normalise(entry)
                except (TypeError, ValueError) as e:   # JSON cannot hold it: no fragment, the block stays stale
                    print(f"warning: {spec.name}: no fragment kept ({e}); it rebuilds every run")
                else:
                    old = frags[spec.name] or {}
                    # the models on disk stay valid only for a rebuild of the very same inputs and reads (--force)
                    same = old.get("export_key") == IN.key(inputs[spec.name]) and old.get("reads") == reads
                    new[spec.name] = IN.fragment(spec.name, inputs[spec.name], entry, reads=reads,
                                                 seconds=time.monotonic() - t0,
                                                 export_key=old.get("export_key") if same else None,
                                                 export_files=old.get("export_files") if same else None)
                status["built"].append(spec.name)
            else:
                entry = frags[spec.name]["entry"]
            data["blocks"][spec.name] = entry
        for t in targets:       # the non-block targets (built after the blocks, under their own report key)
            data[t.key] = t.build(self)
        data["blocks"] = RP.merged_blocks(data["blocks"], list(self.catalogue()), self.default_blocks())
        RP.write(Path(self.hooks.paths.report), data, writer=self.report_writer)
        if do_export:
            ops = self.vrt_ops()
            if self.built:
                self.export(ops)
                models = Path(self.hooks.paths.models)
                for name in self.built:
                    if name in new:
                        new[name]["export_key"] = new[name]["key"]
                        new[name]["export_files"] = IN.models_digest(models / name)
            for t in targets:
                if t.export is not None:
                    t.export(self, ops)
        store = self.store()
        for name, frag in new.items():
            store.save(name, frag)
        if not values["--adopt"]:
            status["warnings"] = self.warn_rewritten({s.name: new.get(s.name) or frags[s.name] for s in specs},
                                                     self.written_inputs())
        if render and self.preview is not None:
            if self.built or targets:
                out = self.preview(self.built, self.env("PREVIEW") or "adhoc")
                if out:
                    print(f"previews in {out}")
            else:
                print("no preview: nothing rebuilt (use --force, or se2mod preview)")
        _write_status(status_path, status)
        print(f"wrote {self.hooks.paths.report} ({len(specs)} blocks, {len(status['built'])} built)")
        return 0

    def launch(self, argv: list[str]) -> int | None:
        """Run the build the way the Blender session needs: in the background (or plain Python) it is `main` (the
        exit code); in a windowed Blender (--export: Keen's add-on needs a GPU context) it waits for the window, runs
        `main` from a timer, prints DONE / FAILED and quits Blender (unless SE2MODKIT_NO_QUIT, or the legacy
        <prefix>NO_QUIT, is set), and returns
        None at once (the script must not exit then)."""
        try:
            import bpy
        except ImportError:
            return self.main(argv)
        if bpy.app.background:
            return self.main(argv)

        def run_windowed():
            import traceback
            try:
                rc = self.main(argv)
                print(DONE if rc == 0 else FAILED)
            except BaseException:  # noqa: BLE001 (the window must still close)
                traceback.print_exc()
                print(FAILED)
            finally:
                if not self.env("NO_QUIT"):
                    bpy.ops.wm.quit_blender()
            return None
        bpy.app.timers.register(run_windowed, first_interval=2.0)
        return None

    def main(self, argv: list[str]) -> int:
        """The exit code: 0 done, 2 a usage or setup error (one plain sentence on stderr), 1 a failed build (the
        traceback on stderr: a headless build that breaks must fail the shell)."""
        if "--help" in argv or "-h" in argv:
            print(USAGE)
            return 0
        try:
            return self.run(list(argv))
        except SystemExit as e:
            if e.code in (None, 0):
                return 0
            if isinstance(e.code, str):
                print(e.code, file=sys.stderr)
                return 2
            return int(e.code)
        except BaseException:  # noqa: BLE001 (any failure is the build's exit code, never a silent 0)
            import traceback
            traceback.print_exc()
            sys.stderr.flush()
            return 1
