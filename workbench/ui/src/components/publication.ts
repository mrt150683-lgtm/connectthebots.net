/**
 * Publication review: the owner sees the exact notice, audience, purpose and
 * expiry before approving. Approval sends back the digest of exactly that
 * preview; the server recomputes it from the current brief and refuses if
 * anything changed in between. The private draft and the approved publication
 * are shown as separate things.
 */
import type { Actions } from "../actions.js";
import type { OwnNotice, PublicationPreview } from "../contracts.js";
import { formatDate, formatTime, h, nextId } from "../dom.js";
import type { AppState } from "../state.js";
import { chips, emptyState, noticeCard, sectionHeading, statusPill } from "./common.js";

const SECTION_LABELS: Record<string, { label: string; detail: string }> = {
  working_arrangement: { label: "Working arrangement", detail: "How you work and what you accept (remote, rhythm)." },
  feedback: { label: "Feedback style", detail: "The feedback you give and work well with." },
  schedule: { label: "Hours and start window", detail: "Notice only; never scored." },
  commercial_note: { label: "Commercial note", detail: "Your own words about money or terms." },
};

function ownNotice(notice: OwnNotice, actions: Actions): HTMLElement {
  const tone = notice.status === "active" ? "positive" : notice.status === "suspended_brief_changed" ? "caution" : "neutral";
  return h("article", { className: "own-notice" },
    h("div", { className: "own-notice-head" },
      h("h3", {}, `${notice.card.board.name}, revision ${notice.revision}`),
      statusPill(notice.status_label, tone)),
    h("p", { className: "muted small" },
      `Approved from brief version ${notice.brief_version}. Expires ${formatDate(notice.card.expires_at)}. ` +
      `Audience: ${notice.audience_aliases.length} named member${notice.audience_aliases.length === 1 ? "" : "s"} (${notice.audience_aliases.join(", ")}).`),
    h("details", {}, h("summary", {}, "Show the notice as others see it"), noticeCard(notice.card, { headingLevel: "h4" })),
    notice.status === "active" || notice.status === "suspended_brief_changed"
      ? h("button", { type: "button", className: "button secondary", onclick: () => actions.withdraw(notice.notice_id) }, "Withdraw this notice")
      : null);
}

function previewView(preview: PublicationPreview, state: AppState, actions: Actions): HTMLElement {
  const titleId = "publication-preview-title";
  return h("section", { className: "preview", "aria-labelledby": titleId },
    h("p", { className: "eyebrow" }, "Exact preview · not published yet"),
    h("h3", { id: titleId, tabindex: -1 }, "Review exactly what would be published"),
    h("dl", { className: "facts review-facts" },
      h("dt", {}, "Board"), h("dd", {}, preview.board.name),
      h("dt", {}, "Audience"), h("dd", {}, h("p", {}, `${preview.audience.aliases.length} named members: ${preview.audience.aliases.join(", ")}.`),
        h("p", { className: "muted small" }, preview.audience.explanation)),
      h("dt", {}, "Purpose"), h("dd", {}, preview.purpose_label),
      h("dt", {}, "Expires"), h("dd", {}, formatTime(preview.expires_at)),
      h("dt", {}, "From brief"), h("dd", {}, `Version ${preview.brief_version}. Editing the brief invalidates this approval.`),
      h("dt", {}, "Not included"), h("dd", {}, chips(preview.not_included))),
    noticeCard(preview.card, { headingLevel: "h4" }),
    h("details", {}, h("summary", {}, "Exact payload (what the board would store)"),
      h("pre", { className: "payload", tabindex: 0 }, JSON.stringify(preview.payload, null, 2))),
    h("p", { className: "muted small" }, `Approval reference ${preview.approval_digest.slice(0, 16)}… binds this approval to the exact content above.`),
    h("div", { className: "button-row" },
      h("button", { type: "button", className: "button secondary", onclick: () => actions.clearPreview() }, "Back to settings"),
      h("button", { type: "button", className: "button primary", disabled: state.busy !== null, onclick: () => actions.approvePublication(preview) },
        state.busy === "approve" ? "Publishing…" : "Approve and publish this exact notice")));
}

export function publicationPanel(state: AppState, actions: Actions): HTMLElement {
  const workspace = state.workspace;
  const session = state.session;
  if (!workspace || !session) return h("div", { className: "loading", "aria-busy": "true" }, "Loading…");
  const heading = sectionHeading("Step 2 · Approve publication", "Choose what others on a board may see",
    "Publishing is separate from saving. You choose a board, an expiry and which optional sections to include, then approve one exact preview. " +
    "Peer-disclosure grants from introductions never count as permission to publish.", "panel-heading-publish");
  if (!workspace.brief) {
    return h("section", { className: "panel" }, heading,
      emptyState("Save a brief first", "A notice is built from your saved brief.",
        h("button", { type: "button", className: "button secondary", onclick: () => actions.selectTab("brief", true) }, "Go to your brief")));
  }
  const dirtyWarning = state.briefDirty
    ? h("p", { className: "callout caution", role: "note" }, "Your brief has unsaved edits. The preview uses the last saved version.")
    : null;
  const boards = workspace.boards;
  const form = state.publishForm;
  const boardId = form.boardId ?? boards[0]?.board_id ?? null;
  const settings = h("form", { className: "publish-form", novalidate: true },
    h("fieldset", { className: "field group" }, h("legend", {}, "Board"),
      boards.length === 0 ? h("p", {}, "You are not a member of any board.") : null,
      boards.map((board) => {
        const id = nextId("board");
        const radio = h("input", { type: "radio", name: "board", id, value: board.board_id, checked: board.board_id === boardId });
        radio.addEventListener("change", () => actions.setPublishForm({ boardId: board.board_id }));
        return h("label", { className: "choice-card", for: id }, radio,
          h("span", {}, h("strong", {}, board.name), h("span", { className: "muted small block" }, `${board.description} ${board.member_count} members.`)));
      })),
    h("div", { className: "field" },
      h("label", { for: "publish-days" }, "Expires after"),
      (() => {
        const control = h("select", { id: "publish-days" },
          session.publication_days.map((days) => h("option", { value: days, selected: days === form.days }, `${days} days`)));
        control.addEventListener("change", () => actions.setPublishForm({ days: Number(control.value) }));
        return control;
      })()),
    h("fieldset", { className: "field group" }, h("legend", {}, "Optional sections"),
      h("p", { className: "hint" }, "Required parts of every notice: title, outcome, stage, focus, offers, needs, availability, engagement basis and suggested first step. Your private notes and settings are never included."),
      h("div", { className: "checks" }, session.optional_sections.map((section) => {
        const id = nextId("section");
        const box = h("input", { type: "checkbox", id, checked: form.include.includes(section) });
        box.addEventListener("change", () => {
          const next = box.checked ? [...form.include, section] : form.include.filter((item) => item !== section);
          actions.setPublishForm({ include: next });
        });
        const info = SECTION_LABELS[section];
        return h("label", { className: "check", for: id }, box, h("span", {}, h("strong", {}, info?.label ?? section), h("span", { className: "muted small block" }, info?.detail ?? "")));
      }))),
    h("div", { className: "button-row" },
      h("button", { type: "submit", className: "button primary", disabled: state.busy !== null || !boardId },
        state.busy === "preview" ? "Preparing…" : "Preview exact notice")));
  settings.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!form.boardId && boardId) actions.setPublishForm({ boardId });
    actions.previewPublication();
  });
  return h("section", { className: "panel" }, heading, dirtyWarning,
    h("div", { className: "panel-grid" },
      h("div", {}, h("h3", { className: "subheading" }, state.preview ? "Preview" : "Publication settings"),
        state.preview ? previewView(state.preview, state, actions) : settings),
      h("aside", { className: "side", "aria-label": "Your published notices" },
        h("h3", { className: "subheading" }, "Your published notices"),
        workspace.publications.length === 0
          ? emptyState("Nothing published", "Your brief is private until you approve a notice.")
          : workspace.publications.map((notice) => ownNotice(notice, actions)))));
}
