# Backlot — the living storyboard

A read-only local board that shows a production happening: pipeline stages
lighting up, the script as a screenplay page, the scene plan as a filmstrip
that fills in as assets generate, decisions, spend, and activity — all
derived from what the pipeline already writes to `projects/<id>/`.

```bash
python -m backlot open <project-id>   # start server if needed + open browser
python -m backlot open                # library view (all projects)
python -m backlot serve --port 4750   # run the server in the foreground
```

## How it stays live

No agent involvement. A `watchfiles` watcher on `projects/` publishes change
notifications over SSE; the browser refetches board state. State sources:

| Board element | Disk source |
|---|---|
| identity / rail order | `project.json` + `pipeline_defs/<type>.yaml` |
| stage states, gates, versions | `checkpoint_<stage>.json` + `history/` |
| script card / modal | `artifacts/script.json` |
| filmstrip cards | `scene_plan × script × asset_manifest` join |
| generating shimmer, activity | `events.jsonl` (written by `BaseTool` instrumentation) |
| cost meter | checkpoint `cost_snapshot`, else the open-montage spend log |
| renders | `renders/*.mp4` (+ root-level mp4 heuristic) |
| brief card `BRIEF` | `artifacts/shot_contract.json` + `brief_checklist.json` + `brief_source.md` |
| reference set `R1..Rn` | `assets/reference/**` + each file's `*.review.json` |
| shots `S1..Sn`, takes `S1-A..` | contract rows × `artifacts/prompts/<shot>.txt` × `assets/video/<shot>[.attemptN].mp4` × `artifacts/adherence/<shot>.json` |
| take cost, total spend | `artifacts/spend_log.json`, else the contract's inline `spend_log` |
| final cut `CUT` | `artifacts/final/cut_report.json` + `cut_review.json` + `artifacts/edit.json` |

## Open-montage runs

A production made with the `open-montage` skill writes a shot contract instead
of a scene plan, so the filmstrip stays empty and `backlot/shots.py` renders it
instead: the brief, the reference set, every take of every shot with its
prompt, its independent adherence verdict, its cost and whether it was
accepted, and the final cut. Nothing on that board is clicked to be useful —
each item carries a short label the user can name in chat:

| Label | What it is |
|---|---|
| `BRIEF` | the brief card: checklist, conflicts, budget, runtime, grade |
| `R1`…`Rn` | reference files under `assets/reference/`, ordered by path |
| `S1`…`Sn` | shots, in shot-contract row order |
| `S2-A`, `S2-B` | that shot's takes: kept attempts by attempt number, the current take last |
| `CUT` | the assembled deliverable |

Labels are derived from disk, so a reload never renumbers them, and a new take
never renumbers the kept ones (`om_shot.py` moves the current take to
`<shot>.attemptN.mp4` with an N above every attempt already there).

The skill's four stops reach the stage rail through one script:

```bash
make stop    PROJECT=<id> STOP=brief|references|shots|cut   # awaiting_human
make stop-ok PROJECT=<id> STOP=brief|references|shots|cut   # the user approved
```

`scripts/om_stop.py` derives each stage's canonical artifact from the same files
and hands it to `lib/checkpoint.py`, which owns the gate, the prerequisites, the
schema and the history. Those four stops are the gated stages of the
`prompt-faithful` pipeline (`idea`, `assets` twice, `publish`); the ungated ones
between them — `script`, `scene_plan`, `edit`, `compose` — are projections of
the shot contract and the cut report, and the script fills them on the way. It
never fills a gated stage, and an artifact the agent wrote at
`artifacts/<name>.json` always wins over a derived one.

Once those stages exist the ordinary filmstrip works too: the derived
`scene_plan` and `asset_manifest` give it the join it needs, so a shot project
shows both views.

Projects without checkpoints degrade gracefully to a "what the watcher
found" view — media, snapshots, renders.

**Replay**: a completed run can be scrubbed end-to-end (▶ REPLAY RUN on the
board) — reconstructed from checkpoint history and event timestamps.

Try it without a real production:

```bash
python scripts/backlot_simulate_run.py          # live demo run (~1 min)
python -m backlot open backlot-demo-run
```

Design doc: `internal/design/LIVING_STORYBOARD.md`.
