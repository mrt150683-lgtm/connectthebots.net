/**
 * Discovery and the explained shortlist.
 *
 * Search retrieves a bounded set of current notices from boards the viewer
 * belongs to; the server re-checks each one before returning it. The interface
 * says plainly that notices not retrieved were not assessed and that the list
 * is not "the best matches". Explanations show reciprocal benefit, supporting
 * statements, unknowns and hard mismatches in words, with no percentages.
 */
import type { Actions } from "../actions.js";
import type { Direction, Explanation, Option, SearchResult, Statement } from "../contracts.js";
import { h, nextId } from "../dom.js";
import type { AppState } from "../state.js";
import { chips, emptyState, noticeCard, sectionHeading, statusPill } from "./common.js";
import { informationRequestForm, informationRequestsPanel } from "./informationRequests.js";

function filterSelect(label: string, id: string, options: Option[], value: string | null, onChange: (value: string | null) => void): HTMLElement {
  const control = h("select", { id },
    h("option", { value: "" }, "Any"),
    options.map((item) => h("option", { value: item.id, selected: item.id === value }, item.label)));
  control.addEventListener("change", () => onChange(control.value || null));
  return h("div", { className: "field compact-field" }, h("label", { for: id }, label), control);
}

const TONE = { both_meet: "positive", needs_information: "caution", not_a_fit_now: "neutral" } as const;

function resultItem(result: SearchResult, actions: Actions, selected: boolean): HTMLElement {
  const card = result.card;
  const action = h("div", { className: "card-actions" },
    statusPill(result.assessment.pair_label, TONE[result.assessment.pair_status]),
    h("button", { type: "button", id: `explain-${card.notice_id}`, className: `button ${selected ? "primary" : "secondary"}`, "aria-expanded": selected ? "true" : "false",
                  onclick: () => actions.explain(card.notice_id, card.revision) }, "Why this possibility?"));
  return h("li", {}, noticeCard(card, { compact: true, actions: action }));
}

function statements(title: string, items: Statement[], empty: string | null, className = ""): HTMLElement | null {
  if (items.length === 0 && empty === null) return null;
  return h("div", { className: `statements ${className}` }, h("h5", {}, title),
    items.length === 0 ? h("p", { className: "muted small" }, empty ?? "") :
      h("ul", {}, items.map((item) => h("li", {}, item.statement, item.source ? h("span", { className: "source" }, ` (${item.source})`) : null))));
}

function direction(view: Direction): HTMLElement {
  const tone = view.status === "qualified_candidate" ? "positive" : view.status === "not_eligible" || view.status === "not_recommended" ? "neutral" : "caution";
  return h("section", { className: "direction" },
    h("h4", {}, view.heading), statusPill(view.status_label, tone),
    statements("Requirements met", view.requirements_met, null),
    statements("Requirements not met", view.requirements_failed, null, "negative"),
    statements("Requirements still unknown", view.requirements_unknown, null, "caution"),
    statements("Supporting", view.supported, null),
    statements("Known mismatches", view.mismatches, null, "negative"),
    statements("Not stated, so unknown", view.unknowns, null, "caution"),
    view.notes.length ? h("ul", { className: "notes" }, view.notes.map((note) => h("li", {}, note))) : null,
    view.threshold_note ? h("p", { className: "muted small" }, view.threshold_note) : null);
}

function explanationPanel(explanation: Explanation, state: AppState, actions: Actions): HTMLElement {
  const { card, propose } = explanation;
  const data = explanation.explanation;
  const titleId = "explanation-title";
  const firstSteps = state.session?.vocabularies.first_steps ?? [];
  const stepId = nextId("intro-step");
  const noteId = nextId("intro-note");
  const step = h("select", { id: stepId }, firstSteps.map((item) => h("option", { value: item.id, selected: item.id === propose.default_first_step.id }, item.label)));
  const note = h("textarea", { id: noteId, maxlength: 280, rows: 2 });
  const form = h("form", { className: "propose", novalidate: true },
    h("h4", {}, "Propose an introduction"),
    h("p", { className: "muted small" },
      `${card.publisher_alias} will see a shared context built only from both published notices. Nothing is accepted on anyone's behalf.`),
    h("div", { className: "field" }, h("label", { for: stepId }, "Suggested first step"), step),
    h("div", { className: "field" }, h("label", { for: noteId }, "A short note in your own words (optional, up to 280 characters)"), note),
    propose.blockers.length ? h("ul", { className: "blockers", "aria-label": "Why an introduction is not possible yet" },
      propose.blockers.map((b) => h("li", {}, b))) : null,
    h("div", { className: "button-row" },
      h("button", { type: "submit", className: "button primary", disabled: !propose.allowed || state.busy !== null },
        state.busy === "propose" ? "Proposing…" : `Propose introduction to ${card.publisher_alias}`)));
  form.addEventListener("submit", (event) => { event.preventDefault(); actions.propose(card.notice_id, card.revision, step.value, note.value); });
  return h("section", { className: "explanation", "aria-labelledby": titleId },
    h("div", { className: "explanation-head" },
      h("div", {}, h("p", { className: "eyebrow" }, "Why this possibility?"),
        h("h3", { id: titleId, tabindex: -1 }, `${card.title}`),
        h("p", { className: "muted" }, `Published by ${card.publisher_alias} on ${card.board.name}`)),
      h("button", { type: "button", className: "button ghost", onclick: () => actions.closeExplanation() }, "Close")),
    statusPill(data.pair_label, TONE[data.pair_status]),
    h("details", { className: "full-notice" }, h("summary", {}, `Read ${card.publisher_alias}'s full notice`),
      noticeCard(card, { headingLevel: "h4" })),
    h("div", { className: "reciprocal" },
      h("div", {}, h("h4", {}, `What ${card.publisher_alias} could contribute to you`), chips(data.reciprocal.they_offer_you)),
      h("div", {}, h("h4", {}, `What you could contribute to ${card.publisher_alias}`), chips(data.reciprocal.you_offer_them)),
      h("div", {}, h("h4", {}, "Shared focus"), chips(data.reciprocal.shared_focus))),
    h("div", { className: "directions" }, data.directions.map(direction)),
    h("p", { className: "method-note" }, data.method_note),
    informationRequestForm(explanation, state, actions),
    form,
    h("details", { className: "secondary-actions" }, h("summary", {}, "Not for you?"),
      h("p", { className: "muted small" }, `Blocking hides ${card.publisher_alias}'s notices from you and yours from them, and closes any introduction between you.`),
      h("button", { type: "button", className: "button danger", onclick: () => actions.blockNotice(card.notice_id, card.revision, card.publisher_alias) },
        `Block ${card.publisher_alias}`)));
}

export function discoveryPanel(state: AppState, actions: Actions): HTMLElement {
  const session = state.session;
  const workspace = state.workspace;
  if (!session || !workspace) return h("div", { className: "loading", "aria-busy": "true" }, "Loading…");
  const heading = sectionHeading("Step 3 · Discover", "Search the boards you belong to",
    "Search reads current, approved notices only, then checks each one against your saved brief on this device. " +
    "It is a bounded search, not a ranking of everyone.", "panel-heading-discover");
  if (!workspace.brief) {
    return h("section", { className: "panel" }, heading,
      emptyState("Save a brief first", "Discovery compares notices with your saved brief.",
        h("button", { type: "button", className: "button secondary", onclick: () => actions.selectTab("brief", true) }, "Go to your brief")));
  }
  const voc = session.vocabularies;
  const q = state.query;
  const text = h("input", { type: "search", id: "search-text", value: q.text, maxlength: 80, autocomplete: "off",
                             placeholder: "e.g. prototype, repair, maintenance" });
  text.addEventListener("input", () => actions.setQuery({ text: text.value }));
  const textError = state.searchErrors.find((e) => e.field === "text");
  if (textError) { text.setAttribute("aria-invalid", "true"); text.setAttribute("aria-describedby", "search-text-error"); }
  const limit = h("select", { id: "search-limit" }, session.result_limits.map((n) => h("option", { value: n, selected: n === q.limit }, `Up to ${n}`)));
  limit.addEventListener("change", () => actions.setQuery({ limit: Number(limit.value) }));
  const form = h("form", { className: "search-form", role: "search", novalidate: true },
    h("div", { className: "field search-field" }, h("label", { for: "search-text" }, "Words to look for"), text,
      textError ? h("p", { className: "field-error", id: "search-text-error" }, textError.message) : null),
    h("div", { className: "filters" },
      filterSelect("Looking at", "filter-mode", voc.modes.map((m) => ({ id: m.id, label: m.id === "project" ? "Projects and opportunities" : "Contributors" })), q.mode, (value) => actions.setQuery({ mode: value })),
      filterSelect("Focus area", "filter-focus", voc.focus_areas, q.focus_area, (value) => actions.setQuery({ focus_area: value })),
      filterSelect("They offer", "filter-offers", voc.capabilities, q.offers, (value) => actions.setQuery({ offers: value })),
      filterSelect("Engagement basis", "filter-basis", voc.bases, q.basis, (value) => actions.setQuery({ basis: value })),
      filterSelect("Working arrangement", "filter-venue", voc.venues, q.venue, (value) => actions.setQuery({ venue: value })),
      h("div", { className: "field compact-field" }, h("label", { for: "search-limit" }, "Results"), limit)),
    h("div", { className: "button-row" },
      h("button", { type: "submit", id: "search-submit", className: "button primary", disabled: state.busy !== null }, state.busy === "search" ? "Searching…" : "Search boards")));
  form.addEventListener("submit", (event) => { event.preventDefault(); actions.runSearch(); });

  const hasNotice = workspace.publications.some((p) => p.status === "active");
  const publishHint = hasNotice ? null : h("p", { className: "callout", role: "note" },
    "You can search without publishing. To propose an introduction, publish your own notice first so the other person can review who is proposing.");

  let results: HTMLElement | null = null;
  const search = state.search;
  if (state.busy === "search") {
    results = h("p", { className: "loading", "aria-busy": "true" }, "Searching the boards you belong to…");
  } else if (search) {
    const groups: [string, SearchResult[], boolean][] = [
      ["Both sides' stated preferences are met", search.results.filter((r) => r.assessment.pair_status === "both_meet"), true],
      ["Needs information before an introduction", search.results.filter((r) => r.assessment.pair_status === "needs_information"), true],
      ["Not a fit for the current briefs", search.results.filter((r) => r.assessment.pair_status === "not_a_fit_now"), false],
    ];
    const selected = state.explanation?.card.notice_id ?? null;
    results = h("div", { className: "results", "aria-live": "polite" },
      h("p", { className: "coverage" }, search.coverage.statement),
      search.empty_reason === "not_a_member"
        ? emptyState("No boards to search", "You are not a member of any board in this fictional setup.")
        : search.empty_reason === "no_results"
          ? emptyState("No current notices matched", "Try fewer words or remove a filter. Withdrawn, expired, blocked and out-of-audience notices are never shown.")
          : h("div", {}, groups.filter(([, items]) => items.length > 0).map(([title, items, open]) => {
              const list = h("ul", { className: "result-list" }, items.map((item) => resultItem(item, actions, item.card.notice_id === selected)));
              return open ? h("section", { className: "result-group" }, h("h3", { className: "subheading" }, `${title} (${items.length})`), list)
                          : h("details", { className: "result-group" }, h("summary", {}, `${title} (${items.length})`), list);
            }), h("p", { className: "muted small" }, search.coverage.order)));
  } else {
    results = emptyState("No search yet", "Choose words or filters and search. An empty search lists current notices you are allowed to see, up to the limit.");
  }
  return h("section", { className: "panel" }, heading, informationRequestsPanel(state, actions), publishHint, form,
    h("div", { className: `discover-grid${state.explanation ? " with-explanation" : ""}` },
      h("div", {}, results),
      state.explanation ? explanationPanel(state.explanation, state, actions) : null));
}
