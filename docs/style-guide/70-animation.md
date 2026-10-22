# Animation

Motion has two layers, matching house rule R5: honest data-only motion that always works, and scripted motion that
adds the feel.

## Scripted motion (the house feel)

| Rule | Value | Key |
|---|---|---|
| Spring overshoot (share of the travel) | 0.002 to 0.009 | `animation.overshoot` |

- Closed-form springs with a lead goal; each step finishes before the next starts.
- A client sequencer writes the subparts' relative transforms. Nothing is synced that the server does not need.
- A Python port of the springs (`se2modkit.motion.springs`, arriving with the pipeline port) checks the script's
  values.

## Data-only motion

- Use vanilla rigid animations only: continuous spin, back-and-forth slide, two-state slide. They are linear,
  simultaneous and run at one speed both ways, so animate only what stays honest without ordering.
- Rest pose = closed.
- What vanilla offers without scripts:

| Motion | Vanilla component | Vanilla example |
|---|---|---|
| continuous spin | `InfiniteRotationAnimation` | Reactor core, gyroscope rings |
| back-and-forth slide | `InfiniteTranslationAnimation` | Assembler side devices |
| two-state slide | `TranslationAnimation` | Warhead arming plate |
| door leaf | `DoorSegment` between two dummies | SlideDoorNarrow |
| working-state lights | `BlockMaterialSwapper` | most functional blocks |

## Clearance

- Every pose over the sweep is checked clear of fixed geometry and of the other moving parts
  (`se2modkit.motion.sweep`, arriving with the pipeline port).
- A subpart has its own render component: the block's material swapper does not reach it, so author subpart lights
  in the subpart's own model.
