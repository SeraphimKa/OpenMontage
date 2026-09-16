---
name: open-montage
description: |
  Prompt-faithful production from a brief: extract a checklist from the brief, write image and video prompts against it, have a clean reviewer check every prompt and every output against the brief, generate reference images for anything the held photos do not cover, run edit and regeneration rounds with a fresh reviewer each round, and generate video one shot per call. Use when: (1) producing any clip that will be delivered to a client, (2) a brief arrives as text, images or video with a shot list, (3) a user says the output did not follow the prompt or drifts between shots, (4) regenerating or editing a shot after a deviation. Wraps the seedance-2-5 prompt grammar with the process around it.
---

# OpenMontage: prompt-faithful production

A generation is faithful when every point of the brief is visible in the output and
nothing is there that the brief did not ask for. The model owes you nothing the prompt
did not name, and shows nothing the framing did not contain. This skill is the process;
the prompt grammar lives in `seedance-2-5/SKILL.md`. Read that first.

Team defaults: `seedance_ark`, `model_variant: "2.5"`, `resolution: "480p"`,
`custom_price_cny_per_million_tokens: 77.0`. 480p is for approval; 720p is an explicit ask.
Image generation routes through `image_selector`, which picks from the configured keys.

## The reviewer

Every review in this skill is done by a **clean reviewer**: a separate agent (a Herdr
worker under the `shephrd` policy, or a subagent) that receives only the brief, the
checklist, the prompt or output under review, and nothing from the conversation that
produced them. The author never reviews their own prompt or clip. A new reviewer is
spawned for every round; a reviewer that has seen a previous round carries its verdicts
into the next one.

A review returns one verdict per checklist point, `present`, `absent`, `altered` or
`added`, plus a one-line reason for anything not `present`. `added` marks something in
the output the brief never asked for. The review is written to a file next to the thing
it reviewed, so the next round and the user can read it.

## Phase 1. Checklist from the brief

The brief can be text, images, video, or all three. Reduce it to
`projects/<id>/artifacts/brief_checklist.json`: one entry per requirement, each a
literal noun or action a camera could photograph, tagged `character`, `environment`,
`prop`, `action`, `camera`, `look` or `audio`. Supplied images and video become entries
too, each naming the file and what it establishes.

Two rules decide adherence more than any other:

- **Literal, never figurative.** The model renders comparisons as objects. "The lens bends
  the pool into a bowl of blue" produced a round pool with no lane ropes. Write what the
  camera sees: "rectangular 50 m pool, lane ropes in place, seen through an 8 mm fisheye".
- **Framing must contain the element.** A macro on a child's face cannot show water at
  the knees. If the waterline is a requirement, the lens has to be wide enough to hold it.

Done when every brief sentence maps to at least one entry and no entry is a metaphor.

## Phase 2. Reference images

### 2a. Choose held images

Go through the images and video frames the brief supplied and assign each to the
checklist entries it covers. A held photo of a character or location beats any
generated one: it is what the client already accepted. Record the mapping in
`projects/<id>/assets/reference/README.md`.

### 2b. Prompts for what is not covered

For every `character`, `environment` and `prop` entry without a held image, write an
image prompt. One prompt per element, one element per image, following the
character-sheet method in `seedance-2-5/SKILL.md` for faces: a sharp close-up, neutral
and smiling. Locations as the widest useful view. Props alone on a plain ground.

### 2c. Review the prompts

A clean reviewer checks each prompt against its checklist entries: every entry present,
nothing added, nothing figurative. Fix and re-review until every prompt passes. Only then
generate.

### 2d. Generate and review the images

Generate through `image_selector` into `projects/<id>/assets/reference/<role>.png`.
A clean reviewer views each image against its prompt and checklist entries and writes
`<role>.review.json` beside it. Then open the folder for the user:

```
xdg-open <dir>      # Linux
open <dir>          # macOS
explorer <dir>      # Windows
```

## Phase 3. Rounds

The user answers with edits and regeneration requests, often mixed. Split them first,
because they cost different things and are reviewed differently:

| Request | Meaning | Action |
|---|---|---|
| **Edit** | keep this image, change one named thing | image-to-image or an inpaint with the held image as base; the review checks that only the named thing changed |
| **Regeneration** | this image is wrong, make it again | change exactly one thing in the prompt, or add a reference; the review is the full checklist again |

Log each round as `rounds/<n>.json`: the requests, how each was classified, what was
changed, and the reviewer's verdict. Every round gets a new clean reviewer. When the
same element fails twice, stop rewording: split the element into its own image or supply
a photo of it. Phase 3 ends when the user accepts the reference set.

## Phase 4. Shot contract

Write `projects/<id>/artifacts/shot_contract.json`, one row per shot:

| Column | What goes in it |
|---|---|
| `id` | `shot_01` … |
| `seconds` | 2.5 to 6 |
| `lens` | one focal per shot, stated as the seedance-2-5 LENS LOCK |
| `frame_contains` | every checklist entry that must be visible, as literal nouns |
| `action` | exactly one action |
| `carry_over` | what must be identical to the previous shot: location, faces, count, wetness state |
| `reference_ids` | the reference images from Phase 2 this shot needs, the same file for every shot that contains that element |

Done when every `frame_contains` entry is a noun, no row asks a lens to show something
outside its frame, and every `reference_ids` cell points at an accepted file.

## Phase 5. Video prompts

One generation per shot for deliverables, 4 to 6 s at 480p, `operation:
"reference_to_video"` with `reference_image_paths` from the contract. Up to three
shots per generation only for mood or block-out work. Seven shots in 18 s drifted on
every axis in The Flood, 2026-09-14, and is not a delivery unit. At 480p on 2.5 a 5 s
shot is about 0.51 USD and an 18 s block about 1.85 USD.

Write each prompt in the seedance-2-5 section order, then per shot:

- Name each `frame_contains` element twice: in the shot line and again in POSITIVE LOCKS.
- Restate `carry_over` verbatim from the previous shot's prompt, same words, same order.
- Put the lens in the shot line and in LENS LOCK. Add COUNT LOCK whenever a number of
  subjects matters ("exactly four wolves, never five, no reflection adding a fifth").
- Substitute rather than forbid. "No round pool" is weak; "rectangular pool with lane
  ropes" is enforceable.

A clean reviewer checks every prompt against its contract row and the checklist:
nothing missing, nothing added, nothing figurative, references named by role. Fix and
re-review until it passes.

## Phase 6. Generate, score, iterate

Generate the riskiest shot first and announce the per-shot and batch cost before the
first paid call. Score it before anything else runs.

Scoring is a clean review of the whole clip: a 1 s contact sheet (`fps=1,tile=NxM`)
plus a real-time watch, written to `projects/<id>/artifacts/adherence/<shot_id>.json`:

```json
{"shot_id": "shot_05", "clip": "assets/video/shot_05.mp4",
 "frame_contains": {"rectangular pool": "present", "lane ropes": "absent", "four wolves mid-air": "present"},
 "lens_obeyed": true, "count_obeyed": true, "carry_over_held": true, "added": [],
 "verdict": "regenerate", "change_next": "add lane ropes to POSITIVE LOCKS; add pool reference image"}
```

Verdicts: all `present`, locks held, nothing `added`: `accept`. Otherwise the Phase 3
rules apply to the shot: classify the fix as an edit (the 2.5 skill's "change only X"
correction) or a regeneration, change one thing named in `change_next`, prefer adding a
reference over rewording, new clean reviewer each round, and after the same element fails
twice split the shot or supply a reference of the element. Record the accepted shot's
prompt, references and score in its asset manifest `generation_summary`, so a retake can
be regenerated identically.

## Phase 7. Present

Deliver the clips with every `absent`, `altered` and `added` item listed before the user
finds them. A deviation you name is a decision; one they find is a complaint.

## Deviations seen so far

| Prompt wording | What rendered | Contract fix |
|---|---|---|
| "over the bowl of blue" (fisheye, pool) | round pool, no lane ropes | "rectangular pool, lane ropes in place" + pool reference |
| "bodies forming numerals 7, 9, 11" | fur-textured digits floating | describe the pose: "two wolves vertical, tails together, forming a 1" |
| macro on face, "water at the knees" | water implied, never visible | widen to medium shot, or a second shot for the waterline |
| "weathered old man" (no reference) | a recognisable actor | invented feature list + reference image |

Add a row whenever a review fails, so the next checklist is written against it.
