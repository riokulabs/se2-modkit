"""Previews: Blender scenes of a mod's built blocks, icons and state renders, and the sheets that put them side by side.

Renders go into the mod's git-ignored preview folder (`base.output_dir`), never into a commit. Modules:

  compose     contact sheets and before/after pairs of any images (`se2mod sheet` / `se2mod pair`; Pillow, lazily)
  sheets      the preview sets' own sheets: a set's contact sheet, the states sheet, the icon sheet (Pillow, lazily)
  texcache    the private texture cache the preview materials convert game textures into (no Blender)
  plan        `previews.toml`: the declarative shot list `se2mod preview` renders (no Blender)
  base        Blender: the shot scene (EEVEE, AgX, the neutral four-light rig, floor grid), painted copies of built
              blocks, read-only vanilla FBX, the scale figure, the output folder
  mats        Blender: material stand-ins by Keen slot name and by material GUID (paints, atlases, construction,
              Deformed, broken glass)
  turntable   Blender: the generic per-block set (orthographic views, 3/4 views, close-ups, LOD row, paints, vanilla
              comparison)
  icons       Blender: build-menu / inventory icons on a transparent 200 px tile (the vanilla look)
  states      Blender: build-stage and damage-state renders of exported blocks beside their vanilla analogues

The Blender modules import bpy at module level when it is there; the rest is plain Python. Pillow is never imported
by a Blender module.
"""

from .compose import PILLOW_HINT, PreviewError, check_output_size, make_pair, make_sheet, sheet_size

__all__ = ["PILLOW_HINT", "PreviewError", "check_output_size", "make_pair", "make_sheet", "sheet_size"]
