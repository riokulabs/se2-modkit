# Modeling

Keen's look comes from big chamfers, stacked shells with real shadow gaps, and detail cut in rather than stuck on.
`se2modkit.style.helpers` draws to these sizes.

## Edge language and hardware

| Element | Value | Key |
|---|---|---|
| Big 45 degree chamfer on outer housing edges | 0.09 m | `edge.chamfer_large` |
| Crisp small break (stepped chamfers) | 0.015 m | `edge.chamfer_small` |
| Shadow gap between split cover plates | 0.016 m | `edge.shadow_gap` |
| Non-mounting parts stay inside the box by | 0.027 m | `mount.keep_out` |
| Bolt head radius | 0.009 m | `hw.bolt_r` |
| Status lens radius | 0.012 m | `hw.led_size` |

No soft rounds: no subdivision-style rounding anywhere.

## Layering

- **Two to three shells per face**: a dark inner casing, painted pillars or skins flush with the bounds, painted cover
  plates a few centimeters below them. The steps and shadow gaps read at distance.
- **Split long plates** with a real gap, not a decal seam.
- **Cut in, don't add.** Slots, grip slots, louver openings, notched corners and lens bores are holes through the
  cover plate showing the dark layer behind.
- **Stepped chamfers**: small break, step, big chamfer, step, small break. Mark those edges sharp.
- **Louvers** are angled dark slats in a recessed opening, never a glowing grille.
- **Lights are small**: lenses in bezels, not light bars. Feature lights sit in a raised, screwed bezel.

## Detail budget

- Geometry is for silhouette, shadow and motion; decals are for flat detail under about a centimeter of relief.
- Most of each face is rest area; detail clusters at functional nodes (joints, ports, service points).
- A big, medium, small hierarchy; no uniform greeble.
- Round parts: enough sides to keep the silhouette error near a millimeter at LOD0; never auto-sharpen a round part by
  angle; mating round parts share segment count and phase.

## Real hardware

- Fasteners never share the material of the part they hold: bare steel or black oxide on paint.
- Hatches: hinges on one edge, latches on the other, a shut line and a gasket.
- Cables bend gently, enter through glands, are clipped along their run and end at a box or connector.
- Plates that meet are joined: a weld, bolts or a gusset.
- Size anything a person touches for a suited human.

## Grid, mount faces and size codes

- Large grid cells are 2.5 m; mount and snap logic works on a quarter-meter sub-grid.
- The mesh fills exactly the block's cell volume. Decorative frames sit a few millimeters inside the bounds so decals
  never poke out; parts that should touch the bounds (port rings, mount hints) touch them exactly.
- Mounting faces are flush. Functional faces (nozzles, muzzles, vents) are never mountable.
- Size codes follow vanilla: the variant folder is named by the block's largest dimension in centimeters
  (`250` is a large-grid cube).
- Conveyor ports sit on cell edges; reuse Keen's port objects and the `ConveyorsAtlas` hatch face.
