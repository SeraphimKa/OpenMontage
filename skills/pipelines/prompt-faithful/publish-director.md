# Publish Director - Prompt-Faithful Pipeline

## When To Use

Phase 8. The last stop: the cut goes to the user with its deviations named before
they find them.

A deviation you name is a decision. One they find is a complaint.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Process | `.agents/skills/open-montage/SKILL.md`, Phase 8 | The method |
| Prior artifacts | `state.artifacts["compose"]["render_report"]`, `artifacts/final/cut_review.json` | The cut and its review |
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |

## Process

### 1. List Every Deviation

Every `absent`, `altered`, `added`, `jumped` and `unverified` item from the shot
reviews and the whole-cut review, ranked by how much the user will care. For each:
what it is, why it happened, what was preserved, what was lost, and what a fix
would cost.

### 2. Report The Spend

The total against `budget_usd`, and what each shot cost. The board's cost meter
shows the same number; say it out loud anyway.

### 3. Show The Cut

`make stop PROJECT=<id> STOP=cut` writes this checkpoint as `awaiting_human` and
prints the label (`CUT`). Point the user at it, give them the deviation list, and
end your turn. On a yes, `make stop-ok PROJECT=<id> STOP=cut`.

### 4. Feed The Deviations Forward

Add a row to the skill's "Deviations seen so far" table whenever a review failed,
so the next checklist is written against it.

## Quality Gate

- every deviation declared before the user watches,
- spend reported against the agreed budget,
- the deliverable plays, at the agreed runtime.

## Common Pitfalls

- Leading with the successes and burying the structural deviation at the bottom.
- Offering a fix without its cost.
