# Scene Director - Prompt-Faithful Pipeline

## When To Use

This stage is Phase 4: the shot contract. The contract is this pipeline's scene
plan, and every later stage reads it.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Process | `.agents/skills/open-montage/SKILL.md`, Phase 4 | The contract's columns |
| Process | `.agents/skills/seedance-2-5/SKILL.md` | LENS LOCK, COUNT LOCK, section order |
| Prior artifacts | `state.artifacts["script"]["script"]` | Shot order and timings |
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Tool | `scripts/om_prompt_lint.py frame` | Framing feasibility check |

## Process

### 1. Write The Contract

`artifacts/shot_contract.json`, one row per shot, with the columns Phase 4 names.
The scripts read this shape; other keys are yours to add, and the board shows them
on the brief card as recorded decisions.

### 2. Check Every Framing

`scripts/om_prompt_lint.py frame --fov <degrees> --distance <metres>` for each
row. The lens at that distance must hold everything `frame_contains` names, at
every moment of the shot, including after the subject sits up, turns or reaches.

### 3. Assign The References

Every `reference_ids` cell points at an accepted file, an `asset://` ID or a
trusted same-account output. One reference per element that must stay consistent;
the same file in every shot that contains that element.

### 4. Record Why

A lens, a plate angle or a trim point is your decision to make. Record the reason
in the contract. It appears on the brief card, and it is what you report with the
final cut.

## Quality Gate

- every `frame_contains` entry is a literal noun,
- no row asks a lens to show something outside its frame,
- every `reference_ids` cell resolves,
- `carry_over` names exactly what must be identical to the previous shot.

## Common Pitfalls

- A camera position given twice, as a number and as a landmark, at odds.
- A `frame_contains` list that reads as exclusive. List everything the frame holds.
- Planning more than one shot per generation for a deliverable.
