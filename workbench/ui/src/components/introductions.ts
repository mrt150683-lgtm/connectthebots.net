/**
 * Introductions and first steps.
 *
 * Each acceptance is a deliberate action in the current demo perspective; the
 * button names the owner it acts for. Accepting sends the digest of the exact
 * context on screen, so a context that changed in the meantime is refused.
 * The interface never shows a reply, acceptance or message that a person did
 * not make, and keeps "introduction accepted", "first step agreed" and "step
 * reported" visibly separate.
 */
import type { Actions } from "../actions.js";
import type { FirstStepView, IntroductionView } from "../contracts.js";
import { formatDate, formatTime, h, nextId } from "../dom.js";
import type { AppState } from "../state.js";
import { chips, emptyState, sectionHeading, statusPill } from "./common.js";

function contextView(intro: IntroductionView): HTMLElement | null {
  const c = intro.context;
  if (!c) return null;
  return h("div", { className: "context" },
    h("div", { className: "answer" }, h("h4", {}, "What could we achieve?"),
      h("p", {}, c.achieve.text), h("p", { className: "muted small" }, `From ${c.achieve.from_alias}'s notice: “${c.achieve.title}”`)),
    h("div", { className: "answer" }, h("h4", {}, "What does each person contribute?"),
      h("ul", { className: "plain" }, c.contributions.map((item) => h("li", {}, h("strong", {}, `${item.alias}: `), chips(item.offers))))),
    h("div", { className: "answer" }, h("h4", {}, "What does each person gain?"),
      h("ul", { className: "plain" }, c.gains.map((item) => h("li", {}, h("strong", {}, `${item.alias}: `), chips(item.gains))))),
    h("div", { className: "answer" }, h("h4", {}, "What still needs checking?"), h("ul", {}, c.to_check.map((item) => h("li", {}, item)))),
    h("div", { className: "answer" }, h("h4", {}, "What could we do first?"), h("p", {}, c.suggested_first_step.label),
      h("p", { className: "muted small" }, "A suggestion only. Agreeing a first step happens separately, after both people accept.")),
    c.note ? h("div", { className: "untrusted" }, h("p", { className: "untrusted-label" }, `Note from ${c.note_author_alias}, in their own words`), h("p", {}, c.note)) : null);
}

function firstStepPanel(intro: IntroductionView, step: FirstStepView, state: AppState, actions: Actions, me: string): HTMLElement {
  const steps = state.session?.vocabularies.first_steps ?? [];
  const actionId = nextId("step-action");
  const detailId = nextId("step-detail");
  const choice = h("select", { id: actionId }, steps.map((item) => h("option", { value: item.id, selected: item.id === (step.action?.id ?? intro.context?.suggested_first_step.id) }, item.label)));
  const detail = h("input", { type: "text", id: detailId, maxlength: 200, autocomplete: "off" });
  const proposeForm = h("form", { className: "step-form", novalidate: true },
    h("div", { className: "field" }, h("label", { for: actionId }, step.state === "none" ? "Propose a small first step" : "Propose a different step instead"), choice),
    h("div", { className: "field" }, h("label", { for: detailId }, "Details such as timing (optional)"), detail),
    h("button", { type: "submit", className: "button secondary" }, `Propose as ${me}`));
  proposeForm.addEventListener("submit", (event) => {
    event.preventDefault();
    actions.introAction(intro.intro_id, "step-propose", { action: choice.value, detail: detail.value });
  });
  const canReport = intro.actions.includes("report_step");
  return h("section", { className: "first-step" },
    h("h4", {}, "First step"),
    h("p", { className: "step-state" }, step.state_label),
    step.action ? h("dl", { className: "facts" },
      h("dt", {}, "Proposed step"), h("dd", {}, step.action.label + (step.detail ? ` (${step.detail})` : "")),
      h("dt", {}, "Proposed by"), h("dd", {}, step.proposed_by_alias ?? ""),
      h("dt", {}, "Agreed by"), h("dd", {}, step.agreed_by_aliases.join(" and ") || "Nobody yet"),
      h("dt", {}, "Outcome reports"), h("dd", {}, h("ul", { className: "plain" }, step.reports.map((r) =>
        h("li", {}, `${r.alias}: ${r.outcome === null ? "no report" : r.outcome === "happened" ? "reported it happened" : "reported it did not happen"}`))))) : null,
    intro.actions.includes("agree_step") ? h("button", { type: "button", className: "button primary",
      onclick: () => actions.introAction(intro.intro_id, "step-agree", { version: step.version }) }, `Agree this step as ${me}`) : null,
    canReport ? h("div", { className: "button-row" },
      h("span", { className: "muted small" }, "After the step, report what actually happened:"),
      h("button", { type: "button", className: "button secondary", onclick: () => actions.introAction(intro.intro_id, "step-report", { version: step.version, outcome: "happened" }) }, "It happened"),
      h("button", { type: "button", className: "button secondary", onclick: () => actions.introAction(intro.intro_id, "step-report", { version: step.version, outcome: "did_not_happen" }) }, "It did not happen")) : null,
    intro.actions.includes("propose_step") ? proposeForm : null);
}

function changeForm(intro: IntroductionView, state: AppState, actions: Actions): HTMLElement {
  const steps = state.session?.vocabularies.first_steps ?? [];
  const stepId = nextId("change-step");
  const noteId = nextId("change-note");
  const choice = h("select", { id: stepId }, steps.map((item) => h("option", { value: item.id, selected: item.id === intro.context?.suggested_first_step.id }, item.label)));
  const note = h("textarea", { id: noteId, maxlength: 280, rows: 2 });
  note.value = intro.context?.note ?? "";
  const form = h("form", { className: "change-form", novalidate: true },
    h("p", { className: "callout caution" }, "Any change creates a new version of the context and clears every acceptance, including the other person's."),
    h("div", { className: "field" }, h("label", { for: stepId }, "Suggested first step"), choice),
    h("div", { className: "field" }, h("label", { for: noteId }, "Note (optional)"), note),
    h("button", { type: "submit", className: "button secondary" }, "Save change"));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    actions.introAction(intro.intro_id, "change", { first_step: choice.value, note: note.value });
  });
  return h("details", { className: "secondary-actions" }, h("summary", {}, "Suggest a change"), form);
}

function introCard(intro: IntroductionView, state: AppState, actions: Actions): HTMLElement {
  const me = state.workspace?.owner.alias ?? "";
  const tone = intro.status === "open" ? "positive" : intro.status === "pending" ? "caution" : "neutral";
  const mine = intro.acceptances.find((a) => a.is_you);
  const theirs = intro.acceptances.find((a) => !a.is_you);
  const counterpart = state.session?.owners.find((o) => o.alias === intro.counterpart_alias);
  const pendingActions = intro.status === "pending" ? h("div", { className: "intro-actions" },
    intro.acceptance_cleared_by_change ? h("p", { className: "callout caution", role: "status" },
      "The context changed after you accepted, so your earlier acceptance was cleared. Review the current version.") : null,
    h("div", { className: "button-row" },
      intro.actions.includes("accept") && intro.proposal_digest ? h("button", { type: "button", className: "button primary",
        onclick: () => actions.introAction(intro.intro_id, "accept", { proposal_digest: intro.proposal_digest }) }, `Accept this exact context as ${me}`) : null,
      intro.actions.includes("withdraw_acceptance") ? h("button", { type: "button", className: "button secondary",
        onclick: () => actions.introAction(intro.intro_id, "withdraw-acceptance") }, "Withdraw my acceptance") : null,
      h("button", { type: "button", className: "button secondary", onclick: () => actions.introAction(intro.intro_id, "decline", {},
        { title: `Decline the introduction with ${intro.counterpart_alias}?`, body: "Declining closes it for both of you. A new introduction would be needed later.", label: "Decline" }) }, "Decline"),
      h("button", { type: "button", className: "button danger", onclick: () => actions.introAction(intro.intro_id, "block", {},
        { title: `Block ${intro.counterpart_alias}?`, body: "Blocking hides your notices from each other and closes every introduction between you.", label: "Block" }) }, "Block")),
    mine?.accepted && !theirs?.accepted && counterpart ? h("p", { className: "muted small" },
      `Waiting for ${intro.counterpart_alias}. Nobody accepts for them. `,
      h("button", { type: "button", className: "link-button", onclick: () => actions.selectOwner(counterpart.owner_id) },
        `Switch the demo perspective to ${intro.counterpart_alias}`)) : null,
    changeForm(intro, state, actions)) : null;
  return h("article", { className: `intro-card status-${intro.status}`, "aria-labelledby": `${intro.intro_id}-title` },
    h("div", { className: "intro-head" },
      h("div", {},
        h("p", { className: "eyebrow" }, intro.direction === "outgoing" ? "You proposed" : `${intro.counterpart_alias} proposed`),
        h("h3", { id: `${intro.intro_id}-title` }, `Introduction with ${intro.counterpart_alias}`)),
      statusPill(intro.status_label, tone)),
    intro.status_detail ? h("p", { className: "muted" }, intro.status_detail) : null,
    h("ul", { className: "acceptances", "aria-label": "Acceptances" }, intro.acceptances.map((a) =>
      h("li", {}, h("span", { "aria-hidden": "true" }, a.accepted ? "✓ " : "○ "), `${a.is_you ? "You (" + a.alias + ")" : a.alias}: ${a.accepted ? "accepted this version" : "not accepted"}`))),
    h("p", { className: "muted small" },
      `Context version ${intro.context_version}${intro.proposal_digest ? ` · reference ${intro.proposal_digest.slice(0, 12)}…` : ""} · proposed ${formatDate(intro.created_at)}` +
      (intro.status === "pending" ? ` · expires ${formatTime(intro.expires_at)}` : "")),
    contextView(intro),
    pendingActions,
    intro.channel ? h("section", { className: `channel${intro.channel.status === "closed" ? " channel-closed" : ""}` },
      h("h4", {}, intro.channel.status === "closed" ? "Simulated channel closed" : "Simulated channel"),
      h("p", {}, `${intro.channel.status === "closed" ? "Previously opened" : "Opened"} ${formatTime(intro.channel.opened_at)}. ` +
        `${intro.channel.status === "closed" ? "This connection has ended. " : ""}${intro.channel.simulation_notice} Not encrypted, not persistent. ` +
        `Human messages: ${intro.channel.human_messages}. This prototype has no messaging and never writes on anyone's behalf.`)) : null,
    intro.first_step ? firstStepPanel(intro, intro.first_step, state, actions, me) : null,
    intro.actions.includes("close") ? h("section", { className: "connection-close", "aria-label": "Finish this connection" },
      h("p", { className: "muted small" }, "Ready to finish here? Closing frees both people's introduction capacity and keeps the existing outcome reports. It does not count as a successful collaboration."),
      h("button", { type: "button", className: "button secondary", disabled: state.busy !== null,
        onclick: () => actions.introAction(intro.intro_id, "close", { context_version: intro.context_version },
          { title: `Close connection with ${intro.counterpart_alias}?`, body: "This ends the connection and frees capacity for both people. Existing reports are kept. Neither person is blocked, and closing does not claim that a step happened or was useful.", label: "Close connection" }) }, "Close connection")) : null,
    intro.status === "open" && intro.actions.includes("block") ? h("details", { className: "secondary-actions" }, h("summary", {}, "Block this person"),
      h("button", { type: "button", className: "button danger", onclick: () => actions.introAction(intro.intro_id, "block", {},
        { title: `Block ${intro.counterpart_alias}?`, body: "Blocking closes the simulated channel. Anything already shared cannot be recalled.", label: "Block" }) },
      `Block ${intro.counterpart_alias}`)) : null,
    h("p", { className: "reminder" }, intro.reminder));
}

export function introductionsPanel(state: AppState, actions: Actions): HTMLElement {
  const workspace = state.workspace;
  if (!workspace) return h("div", { className: "loading", "aria-busy": "true" }, "Loading…");
  const heading = sectionHeading("Step 4 · Introductions", "Introductions and first steps",
    "Both people must accept the same current context before a simulated channel opens. Any change clears earlier acceptances.",
    "panel-heading-introductions");
  const intros = workspace.introductions;
  const active = intros.filter((i) => i.status === "pending" || i.status === "open");
  const past = intros.filter((i) => !(i.status === "pending" || i.status === "open"));
  return h("section", { className: "panel" }, heading,
    h("p", { className: "muted" }, `Capacity: ${workspace.capacity.in_use} of ${workspace.capacity.limit} introductions in use.`),
    intros.length === 0 ? emptyState("No introductions yet", "Find a possibility in Discover and propose an introduction. Incoming proposals also appear here.",
      h("button", { type: "button", className: "button secondary", onclick: () => actions.selectTab("discover", true) }, "Go to Discover")) : null,
    active.map((intro) => introCard(intro, state, actions)),
    past.length ? h("details", { className: "result-group" }, h("summary", {}, `Closed, declined or expired (${past.length})`),
      past.map((intro) => introCard(intro, state, actions))) : null);
}
