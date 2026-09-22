# Script Director - Prompt-Faithful Pipeline

## When To Use

The brief's shot list is the script. This stage records it as one, so the board
can show it and the later stages have a contract to point at.

There is no writing to do here. Inventing narration for a brief that asks for
none is a defect, not a contribution.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Process | `.agents/skills/open-montage/SKILL.md` | The method |
| Prior artifacts | `state.artifacts["idea"]["brief"]` | The approved brief card |
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |

## Process

### 1. One Section Per Shot

Each section takes the shot's id, its title as the `label`, its brief timecode as
`start_seconds`/`end_seconds`, and the shot's own action line as `text`. Record in
`metadata` that the text is the brief's action, not narration.

### 2. Let The Script Writer Do It

`scripts/om_stop.py` builds this artifact from `artifacts/shot_contract.json` and
writes the checkpoint. It runs on its own when you reach the next stop; you do not
call it for this stage.

### 3. If The Brief Does Carry Narration

Then it is narration: put it in `text` and keep the action in
`speaker_directions`. A voice-over brief still runs this pipeline.

## Quality Gate

- one section per shot,
- section timings cover the whole runtime with no gap and no overlap,
- nothing in `text` that the brief did not say.

## Common Pitfalls

- Writing narration to fill the field.
- Letting the section timings drift from the brief's timecodes because a shot is
  generated longer than it is delivered. The script follows the delivered length.
