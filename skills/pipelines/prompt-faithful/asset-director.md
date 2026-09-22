# Asset Director - Prompt-Faithful Pipeline

## When To Use

This is the paid stage and the long one: Phases 2, 3, 5 and 6. Reference images,
review rounds, video prompts, then one generation per shot with an independent
score on every take.

It carries **two** of the run's four stops: the reference set, and the accepted
shots. Both are this stage's gate, taken in that order.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Process | `.agents/skills/open-montage/SKILL.md`, Phases 2, 3, 5, 6 | The method |
| Process | `.agents/skills/seedance-2-5/SKILL.md` | The prompt grammar |
| Prior artifacts | `state.artifacts["scene_plan"]["scene_plan"]`, `artifacts/shot_contract.json` | The contract |
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |
| Tools | `make shot`, `make shot-go`, `make shot-sheet`, `make prompt-lint` | The only sanctioned way to spend |

## Process

### 1. Announce Before Spending

State the tool, the provider, the model, the reason, and whether this is a sample
or a batch, before the first paid call. `make shot` plans and prices for free;
nothing is charged until `make shot-go`.

### 2. Stop One - The Reference Set

Generate and review the references, then `make stop PROJECT=<id> STOP=references`.
It prints the labels (`R1`, `R2`, …). Name them to the user and end your turn. On
a yes, `make stop-ok PROJECT=<id> STOP=references`.

### 3. Generate Riskiest First

Score the first shot before anything else runs. Then generate the rest
concurrently: they are independent calls and a filter refusal costs nothing.

### 4. Score Every Take

A clean reviewer who wrote none of the prompt watches the whole clip, not a
frame, and writes `artifacts/adherence/<shot>.json`. The reviewer's verdict
stands. An author who disagrees takes both readings to the user.

### 5. Stop Two - The Accepted Shots

`make stop PROJECT=<id> STOP=shots`. It prints every take's label (`S1-A`,
`S2-A`, `S2-B`, …), marks which is accepted, and re-opens this stage for the
second review. On a yes, `make stop-ok PROJECT=<id> STOP=shots`.

### 6. Take Feedback By Label

`S2-B is too dark` is a `shot_02` round under Phase 3's rules. Repeat the label
back when you report what you changed.

## Quality Gate

- every `must` entry `present`, the locks held, nothing `added`,
- every accepted take has an adherence file naming the clip it judged,
- spend inside `budget_usd`, no shot past `max_attempts_per_shot`,
- a superseded take is kept as `<shot>.attemptN.mp4`, never overwritten.

## Common Pitfalls

- Rescoring your own clip because the reviewer was harsh.
- Counting a provider filter refusal as an adherence failure. It is free and it
  says nothing about the prompt.
- Rewording a prompt a third time. After the same element fails twice, split the
  shot or supply a reference of the element.
- Sending a held photo of a person to Ark. Read "People on the Ark route".
