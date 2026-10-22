# Block archetypes

Start every block from the vanilla block that already does its job. Clone its definition chain, keep what the game
needs, change the art and the stats.

## How a vanilla block is assembled

```
PrefabBinding                     what makes it a block
 ├─ server composite              server components (Key -> component type + definition GUID)
 │    └─ server prefab            initial state; progression and rewards name this prefab
 └─ client prefab
      └─ client composite         render, audio, effects
component definitions             block definition (UI data, recipe, BlockKind, PCU, power),
                                  BlockModelComponentDefinition (models, dummies, build stages), ...
BlockKind -> BlockGroup -> Subcategory -> Category      the build menu
```

- **The server prefab is the block** for unlocks and rewards. Nothing references the binding itself.
- **BlockKind membership is computed** from each block definition's `BlockKind`. A clone that keeps the vanilla kind
  becomes an extra size of the vanilla tile: give every clone its own kind.
- **The SDK sources are the truth.** Compiled definitions are stored as deltas against a template that they no longer
  name; the SDK partial definitions keep `BaseDefinition`.
- **Fragility is inherited** through `BaseDefinition`: a clone keeps its source's class unless overridden.

## Which archetype to clone

Clone sources are vanilla block names (SDK folder `Blocks/<Family>/<Size>`).

| Function of the new block | Archetype | Clone this vanilla block |
|---|---|---|
| frame, wall, catwalk, decor that breaks | Structural/decor | WallFlat |
| glazing | Windows (non-breakable) | WindowFlat |
| bed, seat shell | Decor shell | Bed |
| conveyor piece | Conveyor | ConveyorStraight |
| storage | Cargo | CargoContainer |
| item converter | Production | Refinery |
| generator from items | Item to resource | Reactor |
| solar, wind | Renewable | SolarPanel |
| battery | Battery | Battery |
| gas tank | Tank | Hydrogen tank |
| thruster | Thruster | Ion thruster |
| gyroscope, gravity | Gyroscope | Gyroscope |
| door | Door | SlideDoorNarrow |
| rotor, hinge, piston | Mechanical pair | Rotor (block and head) |
| connector, landing gear | Parking | Connector |
| welder, grinder, drill | Ship tool | Welder |
| fixed gun, turret | Weapon | GatlingCannon |
| light | Light | Interior light |
| screen | LCD | LCD |
| timer, sensor, button, event controller | Automation | Timer |
| antenna, ore detector | Communication | Antenna |

## Rules

- **One archetype per block.** Its fragility class, effects and construction follow that archetype
  ([build and damage](60-build-and-damage.md)).
- **Record the clone source** in the block's provenance; the side-by-side preview puts the block next to it
  ([previews and review](95-previews-and-review.md)).
- **Match the clone source's stat band** per volume (house rule R4).
- Prefer data-only behavior that the archetype already has over new script logic (house rule R5).
