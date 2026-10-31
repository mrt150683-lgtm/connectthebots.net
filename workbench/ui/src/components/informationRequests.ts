/**
 * Missing-information requests are a bounded nudge to review a public notice.
 * There is no free-text reply or direct disclosure from the private brief.
 * A notice owner answers only by separately approving a revised publication;
 * the requester must then explicitly reassess that current notice.
 */
import type { Actions } from "../actions.js";
import type { Explanation, InformationRequest } from "../contracts.js";
import { formatTime, h } from "../dom.js";
import type { AppState } from "../state.js";
import { statusPill } from "./common.js";

export function informationRequestForm(explanation: Explanation, state: AppState, actions: Actions): HTMLElement | null {
  const request = explanation.information_request;
  if (!request || request.fields.length === 0) return null;
  const card = explanation.card;
  const select = h("select", { id: "information-request-field", disabled: !request.allowed || state.busy !== null },
    request.fields.map((field) => h("option", { value: field.id }, field.label)));
  const form = h("form", { className: "information-request-form", "aria-label": `Request information from ${card.publisher_alias}` },
    h("h4", {}, "Ask about a missing detail"),
    h("p", { className: "muted small" }, "The notice owner can choose to add this detail to an approved publication or decline. A request does not open a connection."),
    h("div", { className: "field" }, h("label", { for: select.id }, "Missing field to ask about"), select),
    h("p", { className: "request-disclosure" }, request.disclosure),
    request.blockers.length ? h("ul", { className: "blockers", "aria-label": "Why information cannot be requested yet" },
      request.blockers.map((blocker) => h("li", {}, blocker))) : null,
    h("button", { type: "submit", className: "button secondary", disabled: !request.allowed || state.busy !== null },
      state.busy === "request-information" ? "Sending request…" : `Ask ${card.publisher_alias} for information`));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    actions.requestInformation(card.notice_id, card.revision, select.value);
  });
  return form;
}

function requestCard(request: InformationRequest, state: AppState, actions: Actions): HTMLElement {
  const incoming = request.direction === "incoming";
  const title = request.counterpart_alias
    ? `Information request ${incoming ? "from" : "to"} ${request.counterpart_alias}`
    : "Information request unavailable";
  const tone = request.status === "answered" ? "positive" : request.status === "pending" ? "caution" : "neutral";
  return h("article", { className: "information-request-card", "aria-label": title, "data-request-id": request.request_id },
    h("div", { className: "own-notice-head" },
      h("h5", {}, title), statusPill(request.status_label, tone)),
    request.field ? h("p", {}, h("strong", {}, "Requested detail: "), request.field.label) : null,
    h("p", { className: "muted small" }, `Requested ${formatTime(request.created_at)}. Expires ${formatTime(request.expires_at)}.`),
    request.status === "pending" ? h("p", { className: "muted small" }, incoming
      ? "Review the detail in your private brief. You decide whether to include it when approving a revised notice."
      : "Waiting for an approved notice update. No private answer is shared with you.") : null,
    request.status === "answered" ? h("p", { className: "muted small" }, incoming
      ? "Your approved notice now includes the requested detail. This does not accept an introduction."
      : "An approved notice now includes the detail. Reassess it to see whether it changes the fit; no introduction is accepted automatically.") : null,
    h("div", { className: "button-row" },
      request.actions.includes("review_brief") ? h("button", { type: "button", className: "button secondary", disabled: state.busy !== null,
        onclick: () => actions.reviewInformationRequest(request) }, "Review my brief") : null,
      request.actions.includes("decline") ? h("button", { type: "button", className: "button ghost", disabled: state.busy !== null,
        onclick: () => actions.declineInformationRequest(request.request_id) }, "Decline request") : null,
      request.actions.includes("reassess") && request.notice_id !== null && request.revision !== null
        ? h("button", { type: "button", className: "button primary", disabled: state.busy !== null,
          onclick: () => actions.reassessInformationRequest(request) }, "Reassess updated notice") : null));
}

export function informationRequestsPanel(state: AppState, actions: Actions): HTMLElement {
  const requests = state.workspace?.information_requests ?? [];
  const incoming = requests.filter((request) => request.direction === "incoming");
  const outgoing = requests.filter((request) => request.direction === "outgoing");
  const group = (title: string, items: InformationRequest[]) => h("section", { className: "request-group" },
    h("h4", {}, title), items.length ? items.map((request) => requestCard(request, state, actions))
      : h("p", { className: "muted small" }, "None yet."));
  return h("section", { className: "information-requests", "aria-labelledby": "information-requests-title" },
    h("h3", { id: "information-requests-title", tabindex: -1 }, "Information requests"),
    h("p", { className: "muted small" }, "Ask for a missing public detail from a notice's explanation. Replies happen through a fresh publication approval, never through private messages."),
    requests.length ? h("div", { className: "request-groups" }, group("Requests to you", incoming), group("Your requests", outgoing))
      : h("p", { className: "muted" }, "No requests yet. A notice marked ‘needs information’ may offer a detail you can ask about."));
}

/** Guidance survives draft edits, but is discarded on a perspective switch. */
export function informationRequestReview(state: AppState, actions: Actions): HTMLElement | null {
  const request = state.workspace?.information_requests.find((item) => item.request_id === state.reviewingRequestId);
  if (!request || request.status !== "pending" || request.direction !== "incoming" || !request.field) return null;
  const section = request.field.id === "feedback_style" ? "Feedback style"
    : request.field.id === "working_arrangement" ? "Working arrangement" : null;
  return h("aside", { className: "request-review callout", "aria-label": "Responding to an information request" },
    h("p", {}, h("strong", {}, `${request.counterpart_alias} asked about ${request.field.label.toLowerCase()}.`)),
    h("p", {}, state.tab === "brief"
      ? "Review your choices and save your private draft. The request is answered only if you then approve a notice containing the detail. You can also keep it private."
      : `Review the exact notice and its audience before approving.${section ? ` Include the ${section} optional section only if you choose to share it.` : ""} The request does not grant permission to publish.`),
    h("div", { className: "button-row" },
      state.tab === "brief" ? h("button", { type: "button", className: "button secondary", disabled: state.briefDirty || state.busy !== null,
        onclick: () => actions.selectTab("publish", true) }, "Review publication") : null,
      h("button", { type: "button", className: "button ghost", onclick: () => actions.dismissRequestReview() }, "Dismiss guidance")));
}
