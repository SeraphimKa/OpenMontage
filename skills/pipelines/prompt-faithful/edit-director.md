# Edit Director - Prompt-Faithful Pipeline

## When To Use

Phase 7's first half. The accepted shots become a cut: trimmed, graded alike,
joined in contract order with hard cuts.

Every repeat of this stage is free. Re-assembly costs no generation, so iterate
on the user's words rather than arguing about them.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Process | `.agents/skills/open-montage/SKILL.md`, Phase 7 | The method |
| Manual | `scripts/om_assemble.py` docstring | The `edit.json` shape |
| Prior artifacts | `state.artifacts["assets"]["asset_manifest"]`, `artifacts/shot_contract.json` | The accepted takes and their trims |
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |

## Process

### 1. Default To The Contract

With no `artifacts/edit.json`, the cut is the contract: every shot once, in order,
hard cuts, each trimmed to its `trim.keep_seconds` or `seconds_delivered`.

### 2. Write The User's Words Down

When the user says how the shots go together, each instruction becomes one key in
`artifacts/edit.json`, with their sentence beside it as `"asked": "<their words>"`.
Then run `make assemble PROJECT=<id>` again.

### 3. Say What The File Cannot Express

A speed change, a split screen, a wipe: tell the user it is not available. Never
approximate one silently.

### 4. Keep The Runtime Honest

`make assemble` exits 2 when the cut misses `runtime_seconds`. Fix the trims, or
put the new `runtime_seconds` in `edit.json` because the user changed the length.

## Quality Gate

- every trim keeps the whole of its shot's required action,
- each trim point lands on movement, never on a still pose,
- one grade on every shot alike,
- the runtime matches what was agreed.

## Common Pitfalls

- A tail-anchored trim that cuts off the beat the brief asked for.
- A new grade invented here. A grade earns a name in `GRADES` only after it is
  compared on a real frame beside a stronger version of itself.
