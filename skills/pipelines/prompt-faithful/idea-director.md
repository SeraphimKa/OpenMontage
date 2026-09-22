# Idea Director - Prompt-Faithful Pipeline

## When To Use

The brief arrived with its own shot list. This stage turns it into a checklist the
rest of the run is scored against, and puts the brief card in front of the user.

This pipeline has no `research` and no `proposal` stage. There is nothing to
discover and nothing to offer: the client already chose the direction. Every
alternative you weigh here is a craft decision, recorded in
`brief_checklist.json`'s `decisions`, not a concept on a menu.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Process | `.agents/skills/open-montage/SKILL.md`, Phase 1 | The method. Read it first; this file does not repeat it |
| Process | `.agents/skills/seedance-2-5/SKILL.md` | The prompt grammar the checklist is written against |
| Schema | `schemas/artifacts/brief.schema.json` | Artifact validation |
| Board | `backlot/README.md` | The label scheme the user reads back to you |

## Process

### 1. Run Phase 1

Reduce the brief to `artifacts/brief_checklist.json`: one entry per requirement,
each a literal noun or action a camera could photograph, each with a `priority`.
Write the brief verbatim to `artifacts/brief_source.md` first, so the checklist
can be audited against it.

### 2. Name The Conflicts

A brief usually disagrees with its own supplied media. Record each as a `conflict`
with its `status`. Do not resolve one silently; the user decides, and the decision
goes in `decisions` with its `options_considered` and `rejected_because`.

### 3. Choose The Person Route

If the brief contains a person, read "People on the Ark route" in the skill and
agree a `person_route` with the user before any person work starts.

### 4. Lock The Composition Runtime, And Say Why

Lock `render_runtime = "ffmpeg"` and log a `render_runtime_selection` decision in
`decision_log` naming all three runtimes in `options_considered`.

Per AGENT_GUIDE.md → "Present Both Composition Runtimes (HARD RULE)", do not
default silently. The honest sentence to the user is: *"Remotion and hyperframes
compose a video out of React or HTML scenes. This piece is not composed — every
shot is generated footage, and the only editing is trimming, one grade and hard
cuts, which `make assemble` does with ffmpeg. If you want animated text cards,
charts or kinetic typography instead, that is a different pipeline."*

Record `remotion` and `hyperframes` as `rejected_because: "the deliverable is
generated footage spliced by ffmpeg; there is no composition to render"`. If the
brief does want composed scenes, this is the wrong pipeline: say so and route to
`cinematic` or `animated-explainer` before any generation is paid for.

### 5. Agree The Stopping Rules

`budget_usd`, `max_attempts_per_shot` and `runtime_seconds` go at the top of
`artifacts/shot_contract.json`. The scripts enforce them; you do not.

### 6. Show The Brief Card

`make stop PROJECT=<id> STOP=brief` writes this checkpoint as `awaiting_human`
and prints the label to look at (`BRIEF`). Tell the user the label, then end your
turn. On a yes, `make stop-ok PROJECT=<id> STOP=brief`.

## Quality Gate

- every brief sentence maps to at least one checklist entry,
- no entry is a metaphor,
- every entry has a `priority`,
- every conflict has a status and a recorded decision,
- the budget and the runtime are agreed in writing.

## Common Pitfalls

- Writing a figurative entry. The model renders comparisons as objects.
- Resolving a conflict yourself because it looks obvious.
- Starting reference work before the `person_route` is agreed.
