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
Image generation routes through `image_selector` with `preferred_provider: "ark"`
(`seedream_ark`, the same key as the video tool, about 0.035 USD an image).

Paid calls, scoring sheets and assembly run through `make shot`, `make shot-go`,
`make shot-sheet`, `make prompt-lint`, `make assemble` and `make cut-sheet`; each script's docstring in
`scripts/om_*.py` is its manual. They read the shot contract, price and validate before
anything is spent, log every attempt to `artifacts/spend_log.json`, and keep a
superseded take as `<shot>.attemptN.mp4`.

Run all Python in this repo with the project's virtual environment: the `make` targets
do it for you, and anything else is `.venv/bin/python ...` (`make setup` creates it).
A system `python` does not carry the repo's dependencies, and one failure is misleading:
without Pillow the Ark tool reports a healthy reference image as "unreadable or corrupt".
Before debugging an image or an import error, check which interpreter ran.

Spend the user's attention at four points: the **brief card** (conflicts between the
brief and its own media, `person_route`, the budget), the **reference set** (Phase 3),
the **accepted shots** (end of Phase 6) and the **final cut** with its deviations
(Phase 8). Craft decisions in between, such as
a lens, a plate's angle or a trim point, are yours: decide, record the reason in the
contract, report it with the final cut. Provider and model changes, and every stopping
rule below, go to the user when they happen.

## Where this sits in the pipeline

AGENT_GUIDE Rule Zero applies: this skill runs inside the **`prompt-faithful`**
pipeline, with `init_project`, checkpoints and `decision_log` as
`skills/meta/checkpoint-protocol.md` describes. The `make` targets above are this
skill's sanctioned way to call `seedance_ark` inside the `assets` stage. The phases land
in the stages like this:

| Stage | Phases | Gate shown to the user |
|---|---|---|
| `idea` | 1, plus "People on the Ark route" | **stop 1**, the brief card: checklist, conflicts, `person_route`, `budget_usd` |
| `script` | none: the brief's shot list is the script | none; derived from the contract |
| `scene_plan` | 4 | none; the shot contract is the scene plan |
| `assets` | 2, 3, 5, 6 | **stops 2 and 3**: the reference set when Phase 2d opens the folder, then the accepted shots with their scores after Phase 6 |
| `edit`, `compose` | 7 | none; derived from the cut report. `compose` still owes `final_review`, which is a reviewer's judgement and is never derived |
| `publish` | 8 | **stop 4**, the final cut with its deviations |

Use `prompt-faithful`, not `cinematic`. `cinematic` opens at `research` and `proposal`,
which exist to discover a direction and offer at least three of them; their artifacts
demand web sources and three concept directions that a shot-list brief does not have,
and inventing them to pass a schema is a defect. A brief that arrives as *video* still
needs the reference analysis in `skills/meta/video-reference-analyst.md` first.

`seedance_ark` is not in the manifest's `tools_available` for `assets`; log the team
route as a `decision_log` entry.

### Show the user the board

Open the board as the production starts, `python -m backlot open <id>`, and leave it
open. It is where the user reads the brief, the reference set, every take of every shot
and the final cut, each under a short label: `BRIEF`, `R3`, `S2`, `S2-B`, `CUT`. The
label scheme is in `backlot/README.md`.

At each of the four stops run `make stop PROJECT=<id> STOP=brief|references|shots|cut`,
tell the user the labels it prints, and end your turn. When they approve, run
`make stop-ok` with the same `STOP=`. The script builds each stage's artifact from the
contract, the spend log, the adherence files and the cut report, and writes the
ungated stages between the stops on the way. Write `artifacts/<name>.json` yourself
whenever you want to say more than the contract can; a file on disk always wins.

Take the user's feedback by label and repeat the label back when you answer: `S2-B is
too dark` is a shot_02 round under Phase 3's rules, `drop R4` is a reference-set change.

Rounds: the guide's two-round cap governs a stage's self-review. Prompt reviews in this
skill are free and cap at three rounds per prompt; findings left after round three are
fixed, then carried to the final cut as named risks. Paid rounds are capped by the
stopping rules.

## Stopping rules

Agreed on the brief card and written at the top of `shot_contract.json`:
`budget_usd` for the whole project, `max_attempts_per_shot` (3 billed takes unless the
user says otherwise) and `runtime_seconds`. `make shot-go` refuses a take that would
pass the budget or the attempt cap and exits 6; `make assemble` exits 2 when the cut
misses `runtime_seconds`. The other exits: 1 a contract or payload error, fix it; 4 a
person image was refused, change `person_route`; 5 a provider failure, read the error.

The budget counts what `artifacts/spend_log.json` holds, which is every `make shot-go`
take. So run a face-sheet clip as a contract row too (`return_last_frame: true`, no
references), and add `image_selector` spend to the log by hand as an attempt with its
`usd`.

- **Accept** a shot when every `must` entry is `present`, the locks held and nothing is
  `added`. A `should` entry that is `absent` or `altered` is accepted and listed as a
  deviation.
- **Stop and ask the user** at exit 6, at exit 3 (the output filter used its retries),
  and when the same `must` element fails twice after a split or a new reference. A split
  makes new shot ids, each with its own attempt cap.
  Bring the takes so far, what each one missed, and two options with their cost.
- **The reviewer's verdict stands.** An author who disagrees sends the disagreement to
  the user with both readings; the author never rescores.
- A filter refusal is free and counts toward neither the attempt cap nor "failed twice".

Beyond the contract's `budget_usd`, this machine may carry a monthly ceiling, `OM_MONTHLY_CAP_USD` in `.env`: a hook refuses `make shot-go` once the month's logged spend across every project reaches it. `make spend` shows where the month stands. When the hook refuses, tell the user the figures and stop; the cap is theirs to raise.

## The reviewer

Every review in this skill is done by a **clean reviewer**: the `clean-reviewer`
subagent in `.claude/agents/` (or a Herdr worker under the `shephrd` policy) that
receives only the paths of the brief, the checklist, the prompt or output under review
and the verdict file to write, and nothing from the conversation that produced them. The author never reviews their own prompt or clip. A new reviewer is
spawned for every round; a reviewer that has seen a previous round carries its verdicts
into the next one.

A review returns one verdict per checklist point, `present`, `absent`, `altered` or
`added`, plus a one-line reason for anything not `present`. `added` marks something in
the output the brief never asked for. The review is written to a file next to the thing
it reviewed, so the next round and the user can read it.

### Pi reviewer handoff

For a read-only Pi reviewer, omit an explicit `acceptance` setting: its inferred policy
is no acceptance report. Request the review as inline JSON, then have the parent persist
that returned JSON beside the reviewed prompt or asset. This avoids mixing the review
payload with Pi's separate `acceptance-report` envelope. Use `outputMode: "file-only"`
only after the reviewer route has been verified to persist the requested output path.

## People on the Ark route

Read this before Phase 1 whenever the route is `seedance_ark` and the brief contains a
person. It governs Phases 2, 4, 5 and 6.

Seedance 2.0 and 2.5 on Ark "do not support directly uploading reference images/videos
that contain real human faces". Observed on this team's account, 2026-09-17: the request
is refused at task creation within seconds, at no cost, with
`InputImageSensitiveContentDetected.PrivacyInformation ... may contain real person`. A
client photo, a stock photo and a face from another generator are all refused. Images
without a face are unaffected, and none of the rules below apply to them.

### Decide the route with the user first

Ask one question before any person work: **must this be the actual person, or a
consistent person who fits the description?** Record the answer as `person_route` on
each `character` entry of the checklist.

| Answer | `person_route` | What happens |
|---|---|---|
| a consistent invented person | `trusted_output` | The agent builds a face sheet on the same Ark account (below). |
| any believable person, fast | `preset_character` | The user picks a character in the ModelArk Model Playground, Digital Character Library tab, and gives the agent its asset ID. Free, searchable in plain language or by gender, age and nationality. A character can hold several assets, each with its own ID. First use needs one agreement click. |
| the actual person | `real_person_asset` | Human steps below. The agent waits for an asset ID. |
| the actual person, and verification will not happen | `text_sheet` | Only after the user accepts in writing that the result is a consistent invented face, not the approved one. Read the photo at full resolution; write colouring, proportions, hairline, brows, nose, chin, marks, stubble; paste it verbatim into every shot with the seedance-2-5 IDENTITY / NO-IP LOCK. |

While a human step is pending, continue everything that has no person in it: checklist,
environment and prop references, the shot contract. Start person shots when the asset
ID arrives.

**`real_person_asset`, who does what.** *Account holder:* completes real-person or
enterprise authentication on the BytePlus account, then in Model Playground opens My
assets, Real-human, Add real-human assets, creates an asset group, sets the
authorisation validity period and shares the QR invitation. *The person:* scans it, logs
in to their own BytePlus account, passes real-person verification (it can fail on
lighting or angle; retry), and uploads images. Recommended: portrait orientation, one
front full-body image, one neutral shoulders-up close-up with the face filling two
thirds. *Account holder:* accepts the asset; it must show **Active** on its detail page.
*Agent:* receives the asset ID. One group holds one person. One verification covers
later makeup and styling uploads, each of which still passes a face consistency check.
The free tier is console only, 50 assets and 50 groups shared with the virtual library.

**`trusted_output`, building the face sheet.** Ark trusts these face-bearing outputs of
the *same account* for *30 days from generation*: Seedance 2.0 and 2.5 videos (made
after 2026-03-11), the last-frame image those calls return, and Seedream 5.0 lite
text-to-image (both after 2026-04-16). `seedream_ark` (provider `ark`, the same key as
`seedance_ark`) generates on this account and saves the file byte for byte with its
`trust_expires`. Proven 2026-09-17: a `seedream_ark` face (`seedream-5-0-260128`), sent
back as the saved local file in `reference_image_paths`, was accepted by Seedance 2.5 and
the clip held the same man. So the face sheet is one image call of about 0.035 USD: call
`image_selector` with `preferred_provider: "ark"`, confirm `selected_provider`, save to
`assets/reference/trusted/<role>.jpeg`, and keep the file untouched. Not yet observed: a
file re-sent days later, so on a multi-day job re-test with one 480p shot before a batch.
`seedream_image` and `atlas_image` run on other platforms, so their faces are refused.
The older method, for when Seedream is unavailable:

1. Run a 4 s `seedance_ark` `text_to_video` clip of the character that **ends** on the
   neutral close-up, with `return_last_frame: true`. A smiling plate is a second clip.
2. The result carries `video_url`, `last_frame_url` and the saved `output_path`. Both
   URLs expire in 24 hours, and the tool does not save the frame. Download
   `last_frame_url` at once, byte for byte with a plain HTTP GET, to
   `assets/reference/trusted/<role>.<ext>`.
3. Record in `assets/reference/README.md`: task ID, generation date, `trust_expires`
   (generation date plus 30 days), and both URLs. Check `trust_expires` before every
   paid call and rebuild the sheet when it has passed.
4. A clean reviewer compares the frame to the brief, and to the held photo when there is
   one, feature by feature. The user accepts it in Phase 3 like any other reference.

Only the original file is trusted. Editing voids it, compressing or forwarding "may
invalidate trust verification", and another account or platform is never trusted. So
the frame goes back in untouched: no crop, no resize, no format change, no frame pulled
from the mp4 with ffmpeg. Within 24 hours pass `last_frame_url` itself. After that, pass
the saved file; the tool sends its raw bytes. **Whether Ark still trusts that re-sent
copy is unverified. Prove it with one 480p shot before planning a multi-day job on
it.** The vendor's own advice is to transfer originals to BytePlus TOS storage.

### Passing assets to the tool

- `asset://<asset_id>` and `https://` output URLs go in `reference_image_urls`; output
  videos in `reference_video_urls`. Local files go in `reference_image_paths`. A saved
  mp4 cannot be sent back: video references are URL or `asset://` only.
- In the prompt, name assets by type and order: "Image 1", "Video 1". The tool orders
  images as `reference_image_urls` first, then `reference_image_paths`. The asset ID
  never appears in prompt text.
- A preset character or a face sheet chosen by the user still gets a clean review
  against the brief before the first paid shot.

### The output filter

Trust covers the input only. Observed on this account: a finished clip can be discarded
with `OutputVideoSensitiveContentDetected.PolicyViolation` when the rendered face reads
as a recognisable person. It is a random refusal, free, and costs about five minutes.
The same prompt passed and failed on consecutive tries. Announce person shots at twice
the per-shot cost, retry once unchanged, and after two refusals stop and ask the user.
A refusal is not an adherence failure and does not count toward "fails twice".

When a person image is refused, change route using the table; leave the image alone.
Cropping, covering the face, adding noise or looping retries to get a real person past
the filter is out of bounds for client work. The private *virtual* portrait library is
for characters that "must not resemble any real human person", uploads are reviewed,
and uploading needs the Advanced Creation Rights entry tier, which requires enterprise
verification.

## Phase 1. Checklist from the brief

The brief can be text, images, video, or all three. Reduce it to
`projects/<id>/artifacts/brief_checklist.json`: one entry per requirement, each a
literal noun or action a camera could photograph, tagged `character`, `environment`,
`prop`, `action`, `camera`, `look` or `audio`. Supplied images and video become entries
too, each naming the file and what it establishes. Give each entry a `priority`:
`must` for what the brief states outright (who, where, each action, each camera move,
durations), `should` for texture it mentions in passing. When unsure, `must`. The user
sees the split on the brief card and can move entries.

Size the list by what a reviewer can check, not by the brief's word count: every entry
is re-judged in every review, so each one is minutes on every round. One entry per
thing, never one per adjective; a NEGATIVE list becomes one entry per kind (no on-screen
text, no music, no English), not one per word. A single-shot brief fits in about 25
entries; past 40, merge before the card goes to the user.

Two rules decide adherence more than any other:

- **Literal, never figurative.** The model renders comparisons as objects. "The lens bends
  the pool into a bowl of blue" produced a round pool with no lane ropes. Write what the
  camera sees: "rectangular 50 m pool, lane ropes in place, seen through an 8 mm fisheye".
- **Framing must contain the element.** A macro on a child's face cannot show water at
  the knees. If the waterline is a requirement, the lens has to be wide enough to hold it.

Done when every brief sentence maps to at least one entry, no entry is a metaphor,
every entry has a `priority`, and the user has approved the brief card with its budget.

## Phase 2. Reference images

### 2a. Choose held images

Go through the images and video frames the brief supplied and assign each to the
checklist entries it covers. A held photo of a character or location beats any
generated one: it is what the client already accepted. Record the mapping in
`projects/<id>/assets/reference/README.md`.

On `seedance_ark` a photo showing a person covers no entry as a reference. It stays in
the project as the comparison target for reviewers and the source for a text sheet, and
its `character` entries count as uncovered in 2b. See "People on the Ark route".

### 2b. Prompts for what is not covered

For every `character`, `environment` and `prop` entry without a held image, write an
image prompt. One prompt per element, one element per image, following the
character-sheet method in `seedance-2-5/SKILL.md` for faces: a sharp close-up, neutral
and smiling. A face for the `seedance_ark` route follows "People on the Ark route"
instead: the prompt written here becomes the prompt of the face-sheet clip. Locations as the widest useful view. Props alone on a plain ground.

### 2c. Review the prompts

A clean reviewer checks each prompt against its checklist entries: every entry present,
nothing added, nothing figurative. Prompts are text, so use the `prompt-reviewer`
subagent (a faster model; `clean-reviewer` is for images, clips and the cut). Independent
items get independent reviewers launched together, one per prompt, never one after
another. Fix and re-review until every prompt passes. Only then generate.

### 2d. Generate and review the images

Generate through `image_selector` into `projects/<id>/assets/reference/<role>.png`
(faces for Ark excepted, see above).
A clean reviewer views each image against its prompt and checklist entries and writes
`<role>.review.json` beside it, one reviewer per image, all launched together. Then open
the folder for the user:

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
| **Edit** | keep this image or clip, change one named thing | the capability check below; the review checks that only the named thing changed |
| **Regeneration** | this image or clip is wrong, make it again | change exactly one thing in the prompt, or add a reference; the review is the full checklist again |

A change the user named in one phrase ("a bit lighter skin", "drop the lemons") is
applied without a prompt review: change that one thing, generate, and review the result
once against the checklist. The prompt review exists to catch what an author adds or
misreads, and the user has just said the words themselves.

An edit exists only where a tool can take the previous result back in. Check before
promising one, and when the check fails classify the request as a regeneration and say
so:

- **Image edit.** `image_selector` with `generation_mode: "edit"` and the held image in
  `image_path`. Four providers declare `image_edit`: `ark` (the team default, proven
  2026-09-17: only the named thing changed), `atlascloud`, `grok` and `kling_official`.
  Name one in `preferred_provider` and confirm `selected_provider` in
  the result is the one you named: an unavailable provider is replaced by the next in
  rank without a word, and when no edit-capable provider is configured the selector
  runs plain text-to-image. Masked inpainting has no tool here.
- **Video edit.** The seedance-2-5 "change only X" correction needs the previous take
  sent back as a video reference, and Ark accepts that only as the provider URL: a saved
  mp4 cannot be uploaded. `make shot-go` logs each take's `video_url` and `urls_expire`
  (24 hours) in `artifacts/spend_log.json`. Inside the window, put the URL in the shot
  row's `reference_video_urls`, its length in `reference_video_durations`, and write the
  correction prompt. Input video is billed: without the duration the estimate assumes
  15 s, about four times a plain take. Outside the window, the request is a regeneration
  with the full review.

Log each round as `rounds/<n>.json`: the requests, how each was classified, what was
changed, and the reviewer's verdict. Every round gets a new clean reviewer. When the
same element fails twice, stop rewording: split the element into its own image or supply
a photo of it. Phase 3 ends when the user accepts the reference set.

## Phase 4. Shot contract

Write `projects/<id>/artifacts/shot_contract.json`, one row per shot:

| Column | What goes in it |
|---|---|
| `id` | `shot_01` … |
| `seconds` | whole seconds, 4 to 6 (the tool's minimum is 4) |
| `trim` | only when the brief's shot is shorter than 4 s: `{"keep_seconds": "0.0-3.0"}`, chosen so every required action falls inside it and the cut lands on movement |
| `lens` | one focal per shot, stated as the seedance-2-5 LENS LOCK |
| `frame_contains` | every checklist entry that must be visible, as literal nouns |
| `action` | exactly one action |
| `carry_over` | what must be identical to the previous shot: location, faces, count, wetness state |
| `reference_ids` | the reference images from Phase 2 this shot needs, the same file for every shot that contains that element; for a person on Ark, an `asset://` ID or a trusted same-account output with its `trust_expires`, never a held photo |

The scripts read this shape; other keys are yours to add:

```json
{"budget_usd": 6.0, "max_attempts_per_shot": 3, "runtime_seconds": 15,
 "grade_preset": "none", "route": {"resolution": "480p"},
 "shots": [{"id": "shot_01", "seconds": 4, "trim": {"keep_seconds": "0.0-3.0"},
            "lens": "47 degrees", "reference_ids": ["assets/reference/room.png"]}]}
```

`route` overrides a team default. Optional row keys: `prompt_file` (default
`artifacts/prompts/<id>.txt`), `return_last_frame`, `generate_audio`, `resolution`.

Done when every `frame_contains` entry is a noun, no row asks a lens to show something
outside its frame, and every `reference_ids` cell points at an accepted file, asset ID
or trusted output. `reference_ids` is a contract column; Phase 5 maps it to tool inputs.

## Phase 5. Video prompts

One generation per shot for deliverables, 4 to 6 s at 480p, `operation:
"reference_to_video"`, with the contract's references mapped to `reference_image_paths`
for files and `reference_image_urls` for `asset://` IDs and output URLs. People on Ark
follow "People on the Ark route"; a held photo of a person is never sent. Continuity
comes from the same reference files in every shot, so a returned last frame anchors a
face from its sheet clip and is not used to chain one shot into the next. Up to three
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
- A shot with a person and a reference image carries the seedance-2-5 REFERENCE USE
  section: what the images show, and the person's state from the first frame to the
  last. Plates of an empty room otherwise win the opening and the person appears mid-shot.
- Give every action a time-coded beat (`0.6 to 1.4 s — he turns his head`). In a trimmed
  shot every required beat ends before the trim point.
- POSITIVE LOCKS is an inventory of what is in frame. An exclusive list ("and those
  three things only") deletes every required element it leaves out.
- State each camera position once, as one value. Check every framing with
  `scripts/om_prompt_lint.py frame --fov <degrees> --distance <metres>`: the lens at
  that distance holds everything the frame names at every moment of the shot, including
  after the subject sits up, turns or reaches.

Run `make prompt-lint PROJECT=<id>` until it reports 0 errors.

A `prompt-reviewer` checks every prompt against its contract row and the checklist:
nothing missing, nothing added, nothing figurative, references named by role. Fix and
re-review until it passes.

## Phase 6. Generate, score, iterate

Generate the riskiest shot first and announce the per-shot and batch cost before the
first paid call. Score it before anything else runs.
Once that first shot is accepted, generate the remaining shots concurrently: they are
independent calls and a filter refusal costs nothing. `make shot-go` applies the output
filter's retry rule and exits 3 when the next roll is the user's decision;
`make shot-sheet` builds the contact sheets for the reviewer.

Scoring is a clean review of the whole clip: a 1 s contact sheet (`fps=1,tile=NxM`)
plus a real-time watch, with the previous accepted shot's `<shot_id>_last.png` beside it
and the `<shot_id>_audio.txt` transcript the same command writes (the contract's
`dialogue_language` sets the transcriber's language; accent and lip-sync stay `unverified`
until a native speaker listens),
so `carry_over` is judged against a picture, written to `projects/<id>/artifacts/adherence/<shot_id>.json`:

```json
{"shot_id": "shot_05", "clip": "assets/video/shot_05.mp4",
 "frame_contains": {"rectangular pool": {"priority": "must", "verdict": "present"},
                    "lane ropes": {"priority": "must", "verdict": "absent"},
                    "poolside flags": {"priority": "should", "verdict": "present"}},
 "lens_obeyed": true, "count_obeyed": true, "carry_over_held": true, "added": [],
 "verdict": "regenerate", "change_next": "add lane ropes to POSITIVE LOCKS; add pool reference image"}
```

Verdicts follow "Stopping rules": every `must` `present`, locks held, nothing `added`:
`accept`. Otherwise the Phase 3 rules apply to the shot, including its capability check: classify the fix as an edit (the 2.5 skill's "change only X"
correction) or a regeneration, change one thing named in `change_next`, prefer adding a
reference over rewording, new clean reviewer each round, and after the same element fails
twice split the shot or supply a reference of the element. Record the accepted shot's
prompt, references and score in its asset manifest `generation_summary`, so a retake can
be regenerated identically.

## Phase 7. Final cut

Accepted shots are not yet a film: a reviewer who saw one clip at a time cannot see a
blanket change pattern across a cut.

`make assemble PROJECT=<id>` trims each shot to its `trim`, applies the contract's
`grade_preset` to every shot alike, joins them in contract order with hard cuts
into `renders/<id>.mp4`, fades the sound across each cut in sync, and writes `artifacts/final/cut_report.json`
(order, cut points, runtime against `runtime_seconds`). A new grade earns a name in
`GRADES` after it is compared on a real frame beside a stronger version of itself.
`make cut-sheet PROJECT=<id>` adds a sheet of the whole cut and, for every cut, the
frame before it beside the frame after it.

**Editing instructions.** When the user says how the shots go together (order, a shot
dropped or repeated, a tighter trim, a dissolve, music, a title, fades), write their
words into `artifacts/edit.json` and run `make assemble` again; the shape is in the
docstring of `scripts/om_assemble.py`. Each instruction becomes one key, the user's
sentence goes beside it as `"asked": "<their words>"`, and an instruction the file cannot
express (a speed change, a split screen, a wipe) is said to the user as such, never
approximated. Every repeat of this step is free: re-assembly costs no generation. When
the instructions change the length, put the new `runtime_seconds` in `edit.json`.

A clean reviewer who has seen none of the shot reviews gets the brief, the checklist,
the deliverable, `cut_report.json` and the sheets, watches it in real time with sound,
and writes `artifacts/final/cut_review.json` (the pipeline's own `final_review`
artifact has a different schema and is still owed by `compose`):

- one verdict per checklist entry, judged across the whole cut;
- one verdict per editing instruction in `edit.json`: `done`, `altered` or `missing`;
- shot order and each shot's length against the brief, and the runtime;
- for each adjacent pair: location, wardrobe, props, light direction, the person's
  state, and the audio across the cut, each `held` or `jumped` with what changed;
- anything the reviewer could not judge at this resolution, as `unverified`.

A `jumped` on a `must` entry sends one of the two shots back to Phase 6, the one
further from the references. Add a reference of the element that jumped, cropped from
the better shot's frame on the cut side and holding no face; on Ark a pulled frame that
shows a face is refused.
Done when the runtime is right, every `must` is `present` across the cut, and every
remaining `jumped`, `absent`, `altered`, `added` and `unverified` is written down for
Phase 8.

## Phase 8. Present

Deliver the cut with every `absent`, `altered`, `added`, `jumped` and `unverified` item
listed before the user finds them, the spend against `budget_usd`, and the `publish`
checkpoint as `awaiting_human`. A deviation you name is a decision; one they find is a
complaint.

## Deviations seen so far

| Prompt wording | What rendered | Contract fix |
|---|---|---|
| "over the bowl of blue" (fisheye, pool) | round pool, no lane ropes | "rectangular pool, lane ropes in place" + pool reference |
| "bodies forming numerals 7, 9, 11" | fur-textured digits floating | describe the pose: "two wolves vertical, tails together, forming a 1" |
| macro on face, "water at the knees" | water implied, never visible | widen to medium shot, or a second shot for the waterline |
| "weathered old man" (no reference) | a recognisable actor | invented feature list, IDENTITY / NO-IP LOCK, and a same-account face sheet or preset character |
| client photo of a person sent as reference on Ark | request refused, `PrivacyInformation ... may contain real person` | pick a `person_route` |
| face sheet made with `seedream_image` or another generator | refused: other platforms are not trusted | 4 s `seedance_ark` clip, `return_last_frame: true`, use `last_frame_url` |
| person shot whose only references are plates of the empty room | bed empty for 2.2 s, then the man materialises awake | REFERENCE USE section, plus a POSITIVE LOCK that he is in the bed in the very first frame |
| POSITIVE LOCKS "the frame holds his face, the pillows and the wall, and those three things only" | caught in review: would have deleted the sweater, the blanket and the lamp light the same shot requires | list everything the settled frame holds |
| 63° lens two metres up asked to hold a whole bed; a camera height given as a number and as a landmark 0.4 m apart | caught in review: the model breaks the LENS LOCK, floats through the ceiling or drops the subject below frame | `om_prompt_lint.py frame`; one value per position |
| overhead reference plate that only reached 70 to 80° | the shot still rendered a true nadir | "a full 90 degrees straight down" in the shot line and POSITIVE LOCKS: explicit prompt text can beat a weak reference |
| cool window key against a warm lamp, on Seedance 2.5 | the blue arrives at half strength in every clip | `grade_preset: "amber-blue-night"` at assembly |
| the same "navy, cream and rust plaid wool blanket" in every shot's CARRY-OVER | dark and nearly colourless in shot 2, bright cream and rust in shot 3; four single-clip reviews all passed it | the Phase 7 pair sheets; a faceless crop of the prop as a reference in both shots |

Add a row whenever a review fails, so the next checklist is written against it.
