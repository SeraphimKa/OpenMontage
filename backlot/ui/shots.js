// Shot board — the open-montage view of a project (state.shots).
//
// The standard pipeline renders as a scene_plan filmstrip; an open-montage run
// has no scene plan, so it renders here instead: the brief the user agreed, the
// reference set, every take of every shot with its prompt, its adherence
// verdict, its cost and which one was accepted, then the final cut.
//
// Everything is addressed by a short label (BRIEF, R3, S2, S2-B, CUT) so a
// non-technical user can name what they are looking at in chat.

import { el, fmtDuration, fmtMoney, mediaURL, thumbURL } from "/ui/lib.js";

function label(text, extra = "") {
  return el("span", { class: `om-label ${extra}`.trim() }, text);
}

function fact(name, value) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
  return el("div", { class: "approval-fact" }, el("span", {}, name), el("b", {}, String(value)));
}

function facts(items) {
  const kept = items.filter(Boolean);
  return kept.length ? el("div", { class: "approval-facts" }, kept) : null;
}

function clip(value, limit = 200) {
  const text = String(value == null ? "" : value).trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

// A verdict string the reviewer wrote — "accept", "accept with a flagged
// deviation", "regenerate". Only a plain accept is green.
function verdictClass(verdict) {
  const v = String(verdict || "").toLowerCase();
  if (!v) return "";
  if (v.startsWith("accept") && !v.includes("deviation")) return "ok";
  if (v.startsWith("accept")) return "warn";
  return "bad";
}

function playableVideo(projectId, path) {
  const thumb = el("div", { class: "thumb approved" },
    el("video", { src: mediaURL(projectId, path), muted: "", preload: "metadata", playsinline: "" }),
    el("span", { class: "play" }, "▶"));
  thumb.onclick = () => {
    const video = thumb.querySelector("video");
    if (video.paused) video.play().catch(() => {}); else video.pause();
  };
  return thumb;
}

function imageThumb(projectId, path, alt) {
  const img = el("img", { src: thumbURL(projectId, path, 640), loading: "lazy", alt: alt || "" });
  const thumb = el("div", { class: "thumb approved" }, img);
  img.onerror = () => {
    thumb.className = "thumb missing";
    thumb.innerHTML = "";
    thumb.append(el("div", { class: "spec-in" },
      el("span", { class: "warn-ic" }, "⚑"),
      el("div", { class: "spec-desc" }, "file could not be shown"),
      el("div", { class: "spec-shot" }, path)));
  };
  return thumb;
}

// ---------------------------------------------------------------------------
// brief card
// ---------------------------------------------------------------------------

function renderBrief(s, shots, openText) {
  const brief = shots.brief;
  if (!brief) return null;
  const checklist = brief.checklist || {};
  const deliverable = checklist.deliverable || {};
  const spend = shots.spend || {};

  const card = el("div", { class: "om-card" },
    el("div", { class: "om-head" },
      label("BRIEF", "big"),
      el("h3", {}, brief.project || s.title),
      brief.source_path
        ? el("button", {
          type: "button",
          class: "om-btn",
          onclick: () => openText("The brief", brief.summary || "", brief.source_path),
        }, "READ THE BRIEF")
        : null,
    ),
    brief.summary ? el("p", { class: "om-lead" }, clip(brief.summary, 420)) : null,
    facts([
      fact("runtime", brief.runtime_seconds != null ? `${brief.runtime_seconds}s` : null),
      fact("shots", shots.shots.length || null),
      fact("resolution", (brief.route || {}).resolution || deliverable.resolution),
      fact("transitions", deliverable.transitions),
      fact("grade", brief.grade_preset),
      fact("attempt cap", brief.max_attempts_per_shot),
      fact("budget", brief.budget_usd != null ? fmtMoney(brief.budget_usd) : null),
      fact("spent", spend.total_usd != null ? fmtMoney(spend.total_usd) : null),
      fact("checklist", checklist.total ? `${checklist.total} entries` : null),
    ]),
  );

  const tags = Object.entries(checklist.tags || {});
  if (tags.length) {
    card.append(el("div", { class: "om-chips" },
      tags.map(([tag, count]) => el("span", { class: "chip" }, `${tag} ${count}`))));
  }

  const conflicts = (checklist.conflicts || []).filter((c) => c && c.issue);
  if (conflicts.length) {
    card.append(el("div", { class: "om-sub" }, `${conflicts.length} conflict${conflicts.length === 1 ? "" : "s"} between the brief and its own media`));
    card.append(el("ul", { class: "approval-items" }, conflicts.map((c) => el("li", {},
      el("div", { class: "approval-item-title" }, `${c.id || "conflict"} — ${c.status || "open"}`),
      el("p", {}, clip(c.issue, 220))))));
  }

  const notes = brief.notes || [];
  if (notes.length) {
    card.append(el("div", { class: "om-sub" }, "Decisions recorded in the contract"));
    card.append(el("ul", { class: "approval-items" }, notes.slice(0, 6).map((n) => el("li", {},
      el("div", { class: "approval-item-title" }, n.subject || String(n.key).replaceAll("_", " ")),
      el("p", {}, clip(n.text, 200))))));
  }

  return el("div", {},
    el("div", { class: "section-title" }, "Brief",
      el("span", { class: "meta" }, shots.contract_path)),
    card);
}

// ---------------------------------------------------------------------------
// reference set
// ---------------------------------------------------------------------------

function referenceCard(s, item) {
  const card = el("div", { class: "om-ref" },
    el("div", { class: "sc-slate" },
      label(item.label),
      el("span", { class: "dur" }, item.kind === "video" ? "clip" : "still")),
    item.kind === "video" ? playableVideo(s.project_id, item.path) : imageThumb(s.project_id, item.path, item.name),
    el("div", { class: "om-ref-name" }, item.name),
  );
  if (item.used_by && item.used_by.length) {
    card.append(el("div", { class: "om-used" }, "used by ",
      item.used_by.map((l) => label(l, "tiny"))));
  } else {
    card.append(el("div", { class: "om-used dim" }, "not a shot reference"));
  }
  if (item.verdict) {
    card.append(el("div", { class: `om-verdict ${verdictClass(item.verdict)}` }, clip(item.verdict, 60)));
  }
  return card;
}

function renderReferences(s, shots) {
  const refs = shots.references || {};
  const items = refs.items || [];
  if (!items.length) return null;
  const accepted = refs.accepted;
  const strip = el("div", { class: "filmstrip" }, items.map((item) => referenceCard(s, item)));
  return el("div", {},
    el("div", { class: "section-title" }, "Reference set",
      el("span", { class: "meta" },
        `${items.length} file${items.length === 1 ? "" : "s"} · R1–R${items.length}${accepted ? ` · accepted by ${accepted.by || "the user"}${accepted.date ? ` ${accepted.date}` : ""}` : ""}`)),
    el("div", { class: "strip-outer" }, strip));
}

// ---------------------------------------------------------------------------
// shots and their takes
// ---------------------------------------------------------------------------

function takeCard(s, shot, take, openText) {
  const card = el("div", { class: `om-take${take.accepted ? " accepted" : ""}` },
    el("div", { class: "sc-slate" },
      label(take.label, take.accepted ? "ok" : ""),
      take.accepted ? el("span", { class: "om-tick" }, "✓ ACCEPTED") : null,
      el("span", { class: "dur" }, take.cost_usd != null ? fmtMoney(take.cost_usd) : "—")),
    playableVideo(s.project_id, take.path),
    el("div", { class: "om-ref-name" }, take.name),
  );
  if (take.verdict) {
    card.append(el("div", { class: `om-verdict ${verdictClass(take.verdict)}` }, clip(take.verdict, 70)));
  }
  if (take.note) {
    card.append(el("div", { class: "om-note" }, clip(take.note, 120)));
  }
  if (take.review) {
    card.append(el("button", {
      type: "button",
      class: "om-btn",
      onclick: () => openText(`${take.label} — reviewer's verdict`,
        JSON.stringify(take.review, null, 2), take.review_path),
    }, "REVIEWER'S VERDICT"));
  }
  return card;
}

function shotCard(s, shot, openText) {
  const takes = shot.takes || [];
  const card = el("div", { class: "om-card om-shot" },
    el("div", { class: "om-head" },
      label(shot.label, "big"),
      el("h3", {}, shot.title || shot.id),
      shot.brief_timecode ? el("span", { class: "chip" }, shot.brief_timecode) : null,
      shot.accepted_take
        ? el("span", { class: "om-tick" }, `✓ ${shot.accepted_take} ACCEPTED`)
        : el("span", { class: "om-tick pending" }, "NOT ACCEPTED YET"),
    ),
    facts([
      fact("generated", shot.seconds_generated != null ? `${shot.seconds_generated}s` : null),
      fact("in the cut", shot.seconds_delivered != null ? `${shot.seconds_delivered}s` : null),
      fact("takes", takes.length || null),
      fact("spent", shot.cost_usd != null ? fmtMoney(shot.cost_usd) : null),
      fact("references", (shot.reference_labels || []).join(" ")),
      fact("verdict", shot.verdict),
    ]),
  );

  if (shot.action) card.append(el("div", { class: "om-lead" }, clip(shot.action, 260)));

  const buttons = el("div", { class: "om-btns" });
  if (shot.prompt) {
    buttons.append(el("button", {
      type: "button", class: "om-btn",
      onclick: () => openText(`${shot.label} — the prompt that was sent`, shot.prompt, shot.prompt_path),
    }, "THE PROMPT"));
  }
  if (buttons.childNodes.length) card.append(buttons);

  if (takes.length) {
    card.append(el("div", { class: "strip-outer" },
      el("div", { class: "filmstrip" }, takes.map((take) => takeCard(s, shot, take, openText)))));
  } else {
    card.append(el("div", { class: "hint" }, "No take has been generated for this shot yet."));
  }
  return card;
}

function renderShots(s, shots, openText) {
  const rows = shots.shots || [];
  if (!rows.length) return null;
  const totalTakes = rows.reduce((n, shot) => n + (shot.takes || []).length, 0);
  const accepted = rows.filter((shot) => shot.accepted_take).length;
  return el("div", {},
    el("div", { class: "section-title" }, "Shots",
      el("span", { class: "meta" },
        `${rows.length} shots · ${totalTakes} takes · ${accepted} accepted`)),
    el("div", { class: "om-shots" }, rows.map((shot) => shotCard(s, shot, openText))));
}

// ---------------------------------------------------------------------------
// final cut
// ---------------------------------------------------------------------------

function renderCut(s, shots, openText) {
  const cut = shots.cut;
  if (!cut) return null;
  const report = cut.report || {};
  const card = el("div", { class: "om-card" },
    el("div", { class: "om-head" },
      label("CUT", "big"),
      el("h3", {}, "The deliverable"),
      cut.review ? el("span", { class: "om-tick" }, "✓ REVIEWED") : null,
    ),
    facts([
      fact("runtime", report.runtime_seconds != null ? fmtDuration(report.runtime_seconds) : null),
      fact("asked for", report.runtime_expected != null ? fmtDuration(report.runtime_expected) : null),
      fact("runtime ok", report.runtime_ok == null ? null : (report.runtime_ok ? "yes" : "no")),
      fact("cuts at", (report.cuts_at || []).map((t) => `${t}s`).join(" · ")),
      fact("grade", report.grade),
      fact("edited from", report.edited_from),
    ]),
  );
  if (cut.path) {
    card.append(el("div", { class: "render-hero" },
      el("video", { src: mediaURL(s.project_id, cut.path), controls: "", preload: "metadata" })));
    card.append(el("div", { class: "om-ref-name" }, cut.path));
  }

  const buttons = el("div", { class: "om-btns" });
  if (cut.report_path) {
    buttons.append(el("button", {
      type: "button", class: "om-btn",
      onclick: () => openText("CUT — cut report", JSON.stringify(report, null, 2), cut.report_path),
    }, "CUT REPORT"));
  }
  if (cut.review) {
    buttons.append(el("button", {
      type: "button", class: "om-btn",
      onclick: () => openText("CUT — the whole-cut review", JSON.stringify(cut.review, null, 2), cut.review_path),
    }, "WHOLE-CUT REVIEW"));
  }
  if (cut.edit) {
    buttons.append(el("button", {
      type: "button", class: "om-btn",
      onclick: () => openText("CUT — your editing instructions", JSON.stringify(cut.edit, null, 2), cut.edit_path),
    }, "EDITING INSTRUCTIONS"));
  }
  if (buttons.childNodes.length) card.append(buttons);

  if ((cut.sheets || []).length) {
    card.append(el("div", { class: "om-sub" }, "Review sheets — every cut, the frame before beside the frame after"));
    card.append(el("div", { class: "found-grid" },
      cut.sheets.map((path) => imageThumb(s.project_id, path, path))));
  }

  return el("div", {},
    el("div", { class: "section-title" }, "Final cut",
      el("span", { class: "meta" }, cut.report_path || cut.path || "")),
    card);
}

// ---------------------------------------------------------------------------
// entry point
// ---------------------------------------------------------------------------

/** Sections for an open-montage project, in reading order. Empty when the
 *  project is not one (state.shots is null). */
export function renderShotBoard(s, openText) {
  const shots = s.shots;
  if (!shots) return [];
  return [
    renderBrief(s, shots, openText),
    renderReferences(s, shots),
    renderShots(s, shots, openText),
    renderCut(s, shots, openText),
  ].filter(Boolean);
}

/** True when the shot board already shows every render, so the generic
 *  Renders section would only repeat the same player. */
export function shotBoardOwnsRenders(s) {
  const cut = s.shots && s.shots.cut;
  if (!cut || !cut.path) return false;
  const renders = (s.media && s.media.renders) || [];
  return renders.length > 0 && renders.every((r) => r.path === cut.path);
}
