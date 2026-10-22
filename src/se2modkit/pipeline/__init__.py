"""The Blender-to-game pipeline. `ModHooks` is how a mod plugs its tables into it.

Modules (import them by name; the Blender-side ones import bpy only inside the functions that need it):
  hooks           ModHooks, the per-mod registry
  construction    BuildProgressModels stages from the Fractured LOD0's mesh parts
  scaffold        the construction cage plan (pure); fracture_mesh.scaffold_object builds it
  fracture        fracture plans (zones, seams, hulls, roles, Keen's limits), dispatched on hooks.plans
  fracture_mesh   the Blender half: cut, cap, densify and dent (Blender only)
  fracture_rules  Keen's import rules, checked offline on the exported FBX / glTF
  interior        what a broken zone shows inside (Keen's way: a solid core)
  standins        parked subpart stand-ins baked into the Fractured / Damaged models
  effects         damage sparks: which blocks, the vanilla band, the ParticleEffects dummies
  states          build-stage / damage-state evaluation and the save-compatibility baseline
  zfight          the z-fight audit (decal elements, coplanar faces); its rules are plain Python
  placer          offline emulation of the game's block placer (plain Python)
  assemble        parts -> LOD objects, limits, collision objects, the placement entry (Blender)
  report          build_report.json entries and the writer (adds the "se2modkit" provenance key)
  export          one built block -> NonFractured / Fractured / Damaged FBX + collision glTF + export.json (Blender)
  placement_check the placement analysis re-run on the exported files (Blender)
  connect         mechanical connectivity check; realism: the registry-based realism checks (Blender)
  runner          Runner: the build script's flow (select, build, report, z-fight, export, previews)
  incremental     per-block input keys, read tracking and report fragments (only stale blocks rebuild)
  inputs          report reads the audit hook cannot see (Blender's C loaders, anything else)
  menu            build-menu (G-screen) groups, kinds, owners, priorities and sizes (plain Python)
  texts           the mod's en-US texts: display strings as localisation keys, never a vanilla key
  recipes         block costs by the game's mass rule from a vanilla source block, the vanilla item table

Scripted motion (springs, swept clearance) lives in se2modkit.motion.
"""

from . import incremental as _incremental
from .hooks import ModHooks, Stamps

_incremental.install_read_hook()   # files a build reads count as its inputs (pipeline.incremental)

__all__ = ["ModHooks", "Stamps"]
