# Compose Director - Prompt-Faithful Pipeline

## When To Use

Phase 7's second half. The deliverable exists; now someone who has seen none of
the shot reviews watches it as a film.

A reviewer who saw one clip at a time cannot see a blanket change colour across a
cut. That is what this stage is for.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Process | `.agents/skills/open-montage/SKILL.md`, Phase 7 | The whole-cut review |
| Prior artifacts | `state.artifacts["edit"]["edit_decisions"]` | The cut as assembled |
| Schema | `schemas/artifacts/render_report.schema.json`, `final_review.schema.json` | Artifact validation |
| Tool | `make cut-sheet PROJECT=<id>` | The sheets the reviewer needs |

## Runtime Routing

This pipeline is locked to `render_runtime = "ffmpeg"`, chosen at `idea` and
carried through `edit_decisions` unchanged. Remotion and HyperFrames compose a
video out of React or HTML scenes; here there is nothing to compose. The shots
are generated footage and `scripts/om_assemble.py` splices them.

- If `edit_decisions.render_runtime` is anything other than `ffmpeg`, stop. That
  is a CRITICAL governance violation, not a detail. Surface it to the user, route
  the decision back to `idea` to re-lock the runtime, log a
  `render_runtime_selection` correction in `decision_log`, and resume.
- Never silently rewrite `render_runtime` in `edit_decisions`. What the user
  approved at the brief card is footage cut together, and that is the promise.
- `video_compose` is not used on this pipeline, so its engine-selection logic
  never runs. `scripts/om_stop.py` records `render_runtime: "ffmpeg"` in the
  derived `edit_decisions` for exactly this reason.

## Process

### 1. Build The Sheets

`make cut-sheet` writes a 1 fps sheet of the whole deliverable and, for every cut,
the last frame before it beside the first frame after it. That pair is where a
jump in location, wardrobe or light shows.

### 2. Get A Fresh Reviewer

Give them the brief, the checklist, the deliverable, `cut_report.json` and the
sheets. They watch it in real time, with sound. They write
`artifacts/final/cut_review.json`: a verdict per checklist entry across the whole
cut, a verdict per editing instruction, and `held` or `jumped` for every adjacent
pair.

### 3. Send Back A Jump

A `jumped` on a `must` entry sends one of the two shots back to the asset stage —
the one further from the references. Add a reference of the element that jumped,
cropped from the better shot's frame on the cut side, holding no face.

### 4. Write Both Artifacts

`render_report` is a record of what ffmpeg produced and `om_stop` derives it from
the cut report. `final_review` is a judgement and is owed by you: a script must
never synthesise a review.

## Quality Gate

- runtime matches `runtime_seconds`,
- every `must` entry `present` across the whole cut,
- every remaining `jumped`, `absent`, `altered`, `added` and `unverified` is
  written down for the publish stage,
- `cut_review.json` exists and a person read it.

## Common Pitfalls

- Accepting the cut on the sheets alone. Watch it, with sound.
- Treating `final_review` and `cut_review.json` as the same file. They have
  different schemas and different jobs; the run owes both.
