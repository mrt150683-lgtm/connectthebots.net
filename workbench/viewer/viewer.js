/**
 * Read-only human view of the local agent runtime. This module deliberately
 * contains no mutation requests and never sends an owner id supplied by the
 * viewer. The credential's server-enforced owner and scopes select the view.
 *
 * The viewer key lives only in this module's memory. It is never written to
 * a URL, web storage, DOM after connection, logs, or browser credentials.
 * Disconnect aborts pending reads and drops all displayed owner data.
 */
const root = document.getElementById("viewer");
const live = document.getElementById("announcements");
const state = { key: "", workspace: null, capabilities: null, receipts: [], receiptPage: null, receiptBefore: null,
  busy: false, error: null, renewalRequired: false, updatedAt: null };
const RECEIPT_PAGE_SIZE = 50;
const BUDGET_MESSAGE = "This viewer key has no workspace reads left. Obtain a renewed viewer key from your local runtime administrator, then connect it here. Refreshing with this key cannot renew its access.";
let generation = 0;
let pending = null;

function h(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name.startsWith("on") && typeof value === "function") element.addEventListener(name.slice(2), value);
    else if (name === "className") element.className = value;
    else element.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

const array = (value) => Array.isArray(value) ? value : [];
const text = (value, fallback = "Not recorded") => typeof value === "string" && value.length ? value : fallback;
const option = (value, fallback = "Not stated") => value && typeof value === "object" ? text(value.label, fallback) : text(value, fallback);
const humanize = (value) => text(value, "Unknown operation").replace(/[._-]+/g, " ");

function time(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.valueOf())) return "Not recorded";
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(date) + " UTC";
}

function announce(message) { live.textContent = message; }
function pill(label, tone = "neutral") { return h("span", { className: `pill ${tone}` }, label); }
function empty(message) { return h("p", { className: "empty" }, message); }
function chips(values) { return h("ul", { className: "chips" }, array(values).map((item) => h("li", {}, option(item)))); }
function facts(rows) { return h("dl", { className: "facts" }, rows.flatMap(([name, value]) => [h("dt", {}, name), h("dd", {}, value)])); }

class ReadError extends Error {
  constructor(message, revoke = false, renewalRequired = false) {
    super(message); this.revoke = revoke; this.renewalRequired = renewalRequired;
  }
}

async function read(path, key, signal) {
  const response = await fetch(path, {
    method: "GET", mode: "same-origin", credentials: "omit", redirect: "error", cache: "no-store", referrerPolicy: "no-referrer",
    headers: { Authorization: `Bearer ${key}`, Accept: "application/json" }, signal,
  });
  if (response.status === 401 || response.status === 403) {
    throw new ReadError("This viewer key is unavailable, expired, or does not allow this view. Connect with a current viewer key.", true);
  }
  const envelope = await response.json();
  if (!response.ok) {
    if (envelope?.error?.code === "BUDGET_EXHAUSTED") throw new ReadError(BUDGET_MESSAGE, false, true);
    throw new ReadError("The local runtime could not return this view. Try refreshing.");
  }
  if (!envelope || typeof envelope !== "object" || !("version" in envelope) || !("result" in envelope)) {
    throw new ReadError("The local runtime returned an unsupported response. Check that this viewer and runtime belong to the same version.");
  }
  return envelope.result;
}

function clearConnection() {
  generation += 1;
  pending?.abort();
  pending = null;
  state.key = "";
  state.workspace = null;
  state.capabilities = null;
  state.receipts = [];
  state.receiptPage = null;
  state.receiptBefore = null;
  state.renewalRequired = false;
  state.updatedAt = null;
  state.busy = false;
}

function disconnect() {
  clearConnection();
  state.error = null;
  render("viewer-key");
  announce("Disconnected. The key and displayed workspace have been cleared from this page.");
}

async function load(key, connecting = false) {
  if (state.busy || (!connecting && state.renewalRequired)) return;
  if (!["http:", "https:"].includes(location.protocol) || !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) {
    state.error = "Open this viewer at the loopback address printed by the local agent runtime.";
    render("viewer-key");
    return;
  }
  const myGeneration = ++generation;
  pending?.abort();
  pending = new AbortController();
  const signal = pending.signal;
  state.busy = true;
  state.error = null;
  render();
  try {
    const capabilities = await read("/v1/capabilities", key, signal);
    if (capabilities?.role !== "viewer") {
      throw new ReadError("Use a viewer key here. Agent credentials are for agent clients and cannot connect to the human viewer.", true);
    }
    if (generation !== myGeneration) return;
    if (capabilities.remaining_uses === 0) {
      state.capabilities = capabilities;
      throw new ReadError(BUDGET_MESSAGE, false, true);
    }
    const [workspace, receiptPage] = await Promise.all([
      read("/v1/workspace", key, signal),
      read(`/v1/receipts?order=desc&limit=${RECEIPT_PAGE_SIZE}`, key, signal),
    ]);
    // A workspace read consumes a use. Metadata fetched before it is stale;
    // obtain current authority afterwards, also detecting intervening revocation.
    const currentCapabilities = await read("/v1/capabilities", key, signal);
    if (currentCapabilities?.role !== "viewer") throw new ReadError("Connect with a current viewer key.", true);
    if (generation !== myGeneration) return;
    if (!workspace || typeof workspace !== "object" || !workspace.owner) throw new ReadError("No owner workspace was returned by the local runtime.");
    state.key = key;
    state.capabilities = currentCapabilities;
    state.workspace = workspace;
    state.receipts = array(receiptPage?.items).slice(0, RECEIPT_PAGE_SIZE);
    state.receiptPage = receiptPage;
    state.receiptBefore = null;
    state.renewalRequired = currentCapabilities.remaining_uses === 0;
    state.updatedAt = new Date().toISOString();
    state.busy = false;
    pending = null;
    render(connecting ? "workspace-title" : "refresh");
    announce(`${connecting ? "Connected" : "Refreshed"}. Viewing ${text(workspace.owner.alias, "the owner")}'s agent workspace. Read only.`);
  } catch (error) {
    if (generation !== myGeneration || error?.name === "AbortError") return;
    const message = error instanceof ReadError ? error.message : "The local runtime is not responding. Check that it is running, then refresh or reconnect.";
    if (connecting || error?.revoke) clearConnection();
    else { state.busy = false; pending = null; }
    state.renewalRequired = Boolean(error?.renewalRequired);
    state.error = message;
    render(state.workspace ? "refresh" : "viewer-key");
    announce(message);
  }
}

async function loadReceipts(before = null) {
  if (state.busy || !state.key) return;
  const myGeneration = ++generation;
  pending?.abort();
  pending = new AbortController();
  state.busy = true;
  state.error = null;
  render();
  try {
    const cursor = before === null ? "" : `&before=${before}`;
    const page = await read(`/v1/receipts?order=desc&limit=${RECEIPT_PAGE_SIZE}${cursor}`, state.key, pending.signal);
    if (generation !== myGeneration) return;
    state.receipts = array(page?.items).slice(0, RECEIPT_PAGE_SIZE);
    state.receiptPage = page;
    state.receiptBefore = before;
    state.busy = false;
    pending = null;
    render("receipts-title");
    announce(before === null ? "Showing the newest retained receipts." : "Showing an older page of retained receipts.");
  } catch (error) {
    if (generation !== myGeneration || error?.name === "AbortError") return;
    if (error?.revoke) clearConnection();
    else { state.busy = false; pending = null; }
    state.error = error instanceof ReadError ? error.message : "The local runtime is not responding. The previously loaded receipt page is still shown.";
    render(state.workspace ? "receipts-title" : "viewer-key");
    announce(state.error);
  }
}

function connection() {
  const input = h("input", { id: "viewer-key", type: "password", autocomplete: "off", spellcheck: "false", autocapitalize: "off",
    required: true, maxlength: 2048, disabled: state.busy, "aria-describedby": "key-help" });
  const form = h("form", { className: "connection-card" },
    h("div", {}, h("p", { className: "eyebrow" }, "Private local view"), h("h2", {}, "See your agent's progress")),
    h("p", {}, "Agents operate the system. This page lets you follow the work for the owner attached to your viewer key."),
    h("div", { className: "field" }, h("label", { for: "viewer-key" }, "Viewer key"), input,
      h("p", { id: "key-help", className: "small muted" }, "Use the read-only key from your local runtime. It stays in this page's memory and is cleared when you disconnect or reload.")),
    h("button", { className: "button primary", type: "submit", disabled: state.busy }, state.busy ? "Connecting…" : "Connect viewer"));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const key = input.value.trim();
    input.value = "";
    if (key && key.length <= 2048) void load(key, true);
  });
  return form;
}

function briefPanel(workspace) {
  const brief = workspace.brief;
  return h("section", { className: "card", "aria-labelledby": "brief-title" },
    h("div", { className: "section-top" }, h("h3", { id: "brief-title" }, "Current brief"), pill("Owner view")),
    brief ? [
      h("h4", { className: "brief-title" }, text(brief.title)),
      h("p", { className: "lead" }, text(brief.desired_outcome)),
      facts([["Purpose", brief.mode === "contribute" ? "Offering a contribution" : "Project or opportunity"],
        ["Offers", chips(brief.offers)], ["Needs", humanize(brief.primary_need)],
        ["Availability", humanize(brief.availability)], ["Engagement basis", humanize(brief.basis)],
        ["Version", Number.isInteger(brief.version) ? String(brief.version) : "Not recorded"],
        ["Updated", time(brief.updated_at)]]),
      h("p", { className: "footnote" }, "This is the owner's brief. Only a separately authorized publication can make notice content available to others."),
    ] : empty("The agent has not recorded a brief in this workspace."));
}

function publicationPanel(workspace) {
  const publications = array(workspace.publications);
  return h("section", { className: "card", "aria-labelledby": "publications-title" },
    h("div", { className: "section-top" }, h("h3", { id: "publications-title" }, "Published notices"), h("span", { className: "count" }, publications.length)),
    publications.length ? h("div", { className: "stack" }, publications.map((notice) => h("article", { className: "record" },
      h("div", { className: "section-top" }, h("h4", {}, text(notice.card?.title)), pill(text(notice.status_label, humanize(notice.status)), notice.status === "active" ? "positive" : "neutral")),
      h("p", { className: "muted small" }, `${text(notice.card?.board?.name, "Board not recorded")} · Revision ${Number.isInteger(notice.revision) ? notice.revision : "unknown"}`),
      h("p", {}, text(notice.card?.desired_outcome)),
      h("p", { className: "muted small" }, `Expires ${time(notice.card?.expires_at)}.`),
      h("details", {}, h("summary", {}, "Publication audience"), h("p", {}, array(notice.audience_aliases).map((alias) => text(alias, "")).join(", ") || "No audience recorded.")))))
      : empty("No notice has been published. A saved brief is not a publication."));
}

function outcome(step) {
  if (!step) return null;
  return h("div", { className: "outcome" },
    h("h5", {}, "First step and outcomes"),
    h("p", {}, text(step.state_label)),
    step.action ? h("p", {}, h("strong", {}, option(step.action)), step.detail ? ` · ${text(step.detail)}` : "") : null,
    h("ul", { className: "plain" }, array(step.reports).map((report) => h("li", {},
      `${text(report.alias, "Participant")}: ${report.outcome === "happened" ? "reported that it happened" : report.outcome === "did_not_happen" ? "reported that it did not happen" : "no report"}`))),
    h("p", { className: "small muted" }, "Occurrence reports do not establish that the collaboration was useful."));
}

function introductionsPanel(workspace) {
  const introductions = array(workspace.introductions);
  return h("section", { className: "card span-all", "aria-labelledby": "introductions-title" },
    h("div", { className: "section-top" }, h("div", {}, h("p", { className: "eyebrow" }, "Connection progress"), h("h3", { id: "introductions-title" }, "Introductions and first steps")), h("span", { className: "count" }, introductions.length)),
    introductions.length ? h("div", { className: "connection-grid" }, introductions.map((intro) => h("article", { className: "record" },
      h("div", { className: "section-top" }, h("h4", {}, `With ${text(intro.counterpart_alias, "another participant")}`),
        pill(text(intro.status_label, humanize(intro.status)), intro.status === "open" ? "positive" : intro.status === "pending" ? "waiting" : "neutral")),
      intro.status_detail ? h("p", { className: "small muted" }, text(intro.status_detail)) : null,
      intro.context?.achieve ? h("p", {}, text(intro.context.achieve.text)) : null,
      h("ul", { className: "plain acceptance-list" }, array(intro.acceptances).map((acceptance) => h("li", {},
        h("span", { "aria-hidden": "true" }, acceptance.accepted ? "✓ " : "○ "),
        `${text(acceptance.alias, "Participant")}: ${acceptance.accepted ? "acceptance recorded for this context" : "acceptance not recorded"}`))),
      h("p", { className: "small muted" }, `Context ${intro.context_version ?? "unknown"} · Proposed ${time(intro.created_at)}`),
      intro.channel ? h("p", { className: "channel-note" }, intro.channel.status === "closed"
        ? "Simulated channel closed. Earlier records remain below."
        : "A simulated channel is open. This fictional runtime does not provide encrypted peer messaging.") : null,
      outcome(intro.first_step)))) : empty("No introduction has been proposed. The viewer never creates or accepts one."));
}

function discoveryPanel(workspace) {
  // Some runtimes may include a saved search projection. Its absence is not a
  // claim that a search ran or that no suitable candidate exists.
  const candidates = array(workspace.candidates ?? workspace.discovery?.results);
  return h("section", { className: "card", "aria-labelledby": "discovery-title" },
    h("h3", { id: "discovery-title" }, "Discovery"),
    candidates.length ? h("ul", { className: "plain stack" }, candidates.map((candidate) => h("li", { className: "record" },
      h("h4", {}, text(candidate.card?.title, "Candidate notice")),
      h("p", {}, text(candidate.card?.publisher_alias, "Publisher not recorded")),
      pill(text(candidate.assessment?.pair_label, "Assessment not recorded")),
      h("p", { className: "small muted" }, "A candidate is not an accepted introduction."))))
      : empty("No saved candidate list is included in this workspace view. Agent search activity can appear in the receipts below."));
}

function requestsPanel(workspace) {
  const requests = array(workspace.information_requests);
  return h("section", { className: "card", "aria-labelledby": "requests-title" },
    h("div", { className: "section-top" }, h("h3", { id: "requests-title" }, "Information requests"), h("span", { className: "count" }, requests.length)),
    requests.length ? h("ul", { className: "plain stack" }, requests.map((request) => h("li", { className: "record" },
      h("div", { className: "section-top" }, h("h4", {}, request.counterpart_alias
        ? `${request.direction === "incoming" ? "From" : "To"} ${text(request.counterpart_alias)}` : "Request unavailable"),
      pill(text(request.status_label, humanize(request.status)), request.status === "pending" ? "waiting" : "neutral")),
      request.field ? h("p", {}, option(request.field)) : null,
      h("p", { className: "small muted" }, `Requested ${time(request.created_at)}. Expires ${time(request.expires_at)}.`))))
      : empty("No information requests are recorded. A request asks about a notice; it does not open a connection."));
}

function activityPanel(workspace) {
  const activity = array(workspace.activity).slice(0, 50);
  return h("section", { className: "card", "aria-labelledby": "activity-title" },
    h("h3", { id: "activity-title" }, "Workspace activity"),
    activity.length ? h("ol", { className: "timeline" }, activity.map((event) => h("li", {},
      h("time", { datetime: text(event.at, "") }, time(event.at)), h("p", {}, text(event.text)))))
      : empty("No workspace activity is recorded."));
}

function receiptsPanel() {
  const page = state.receiptPage;
  const nextBefore = page?.next_before;
  const canReadOlder = page?.has_more === true && Number.isInteger(nextBefore) && nextBefore > 0;
  return h("section", { className: "card", "aria-labelledby": "receipts-title" },
    h("div", { className: "section-top" }, h("h3", { id: "receipts-title", tabindex: -1 }, "Agent operation receipts"), pill("Recorded activity")),
    h("p", { className: "small muted" }, `${state.receiptBefore === null ? "Latest retained activity" : "Earlier retained activity"}, newest first. Up to ${RECEIPT_PAGE_SIZE} receipts per page. These show returned operation results, not proof of human benefit.`),
    page?.history_truncated ? h("p", { className: "retention-note", role: "note" }, "Older history has been removed under the runtime's retention limit. These pages cover only the receipts still retained.") : null,
    state.receipts.length ? h("ol", { className: "timeline" }, state.receipts.map((receipt) => h("li", { "data-receipt-sequence": receipt.sequence },
      h("div", { className: "section-top" }, h("strong", {}, humanize(receipt.operation)), pill(humanize(receipt.status))),
      h("time", { datetime: text(receipt.at, "") }, time(receipt.at)),
      receipt.code ? h("p", { className: "small muted" }, `Result: ${humanize(receipt.code)}`) : null)))
      : empty("No retained receipts are available on this page."),
    h("div", { className: "button-row receipt-controls" },
      h("button", { type: "button", id: "receipts-newest", className: "button secondary", disabled: state.busy,
        onclick: () => void loadReceipts() }, "Newest receipts"),
      h("button", { type: "button", id: "receipts-older", className: "button secondary", disabled: state.busy || !canReadOlder,
        onclick: () => void loadReceipts(nextBefore) }, "Older receipts")));
}

function accessPanel() {
  const capability = state.capabilities;
  return h("details", { className: "access" }, h("summary", {}, "About this viewer's access"),
    facts([["Role", "Read-only viewer"], ["Owner", text(capability.owner_id)], ["Expires", time(capability.expires_at)],
      ["Remaining uses", Number.isInteger(capability.remaining_uses) ? String(capability.remaining_uses) : "Not specified"],
      ["Scopes", array(capability.scopes).map(humanize).join(", ") || "No scopes listed"]]),
    h("p", { className: "small muted" }, "The runtime enforces this access. The key is held in this page's memory only. Disconnecting clears this page; it does not revoke the credential."));
}

function render(focusId = null) {
  const activeId = document.activeElement?.id;
  const workspace = state.workspace;
  root.replaceChildren(
    h("header", { className: "topbar" }, h("div", { className: "shell topbar-inner" },
      h("a", { className: "brand", href: "#main", "aria-label": "AgentMatch" }, h("span", { className: "brand-mark", "aria-hidden": "true" }), "agentmatch"),
      h("div", { className: "header-labels" }, pill("Human viewer"), pill("Fictional local agent runtime", "waiting")))),
    h("main", { id: "main", className: "shell", tabindex: -1 },
      h("section", { className: "hero" }, h("div", {}, h("p", { className: "eyebrow" }, "Agent activity, in view"),
        h("h1", {}, "Connecting", h("br"), "human intelligence"),
        h("p", { className: "hero-copy" }, "Agents do the work. Follow the brief, the introductions, and the steps that follow.")),
        h("aside", { className: "scope-note" }, h("strong", {}, "A view into a fictional local runtime."),
          h("p", {}, "Every person and notice is invented. This viewer reads existing records and never operates the collaboration workflow."),
          h("p", { className: "small muted" }, "It does not establish real-user security, private peer transport or useful collaboration."))),
      state.error || state.renewalRequired ? h("div", { className: "error", role: "alert" }, state.error || BUDGET_MESSAGE,
        workspace ? h("p", { className: "small" }, `Showing the snapshot read at ${time(state.updatedAt)}. ${state.renewalRequired ? "A renewed key is required for another workspace read." : "It may be older than the runtime's current state."}`) : null,
        state.renewalRequired && workspace ? h("button", { type: "button", id: "renew-viewer", className: "button secondary", onclick: disconnect }, "Connect a new viewer key") : null) : null,
      workspace ? [
        h("section", { className: "workspace-bar", "aria-busy": state.busy ? "true" : "false" },
          h("div", {}, h("p", { className: "eyebrow" }, "Your agent workspace"), h("h2", { id: "workspace-title", tabindex: -1 }, `For ${text(workspace.owner?.alias, "the owner")}`),
            h("p", { className: "small muted" }, `Read ${time(state.updatedAt)} · Runtime time ${time(workspace.now)}`)),
          h("div", { className: "button-row" },
            h("button", { type: "button", id: "refresh", className: "button primary", disabled: state.busy || state.renewalRequired, onclick: () => void load(state.key) }, state.busy ? "Loading…" : "Refresh view"),
            h("button", { type: "button", id: "disconnect", className: "button secondary", onclick: disconnect }, "Disconnect"))),
        h("p", { className: "capacity" }, `${Number.isInteger(workspace.capacity?.in_use) ? workspace.capacity.in_use : "—"} of ${Number.isInteger(workspace.capacity?.limit) ? workspace.capacity.limit : "—"} introduction slots in use. `,
          "Availability changes only through authorized agent operations."),
        h("div", { className: "content-grid", "aria-label": "Read-only agent workspace" }, briefPanel(workspace), publicationPanel(workspace),
          introductionsPanel(workspace), discoveryPanel(workspace), requestsPanel(workspace), activityPanel(workspace), receiptsPanel()),
        accessPanel(),
      ] : connection()),
    h("footer", { className: "shell footer" }, h("p", {}, "AgentMatch · Collaboration first."),
      h("p", {}, "This page sends only authenticated read requests to the local runtime. No external resources, tracking, or saved browser credentials.")));
  const target = document.getElementById(focusId ?? activeId);
  if (target && !target.disabled) target.focus({ preventScroll: focusId === null });
}

window.addEventListener("pagehide", () => {
  clearConnection();
  state.error = null;
  // Back/forward caches can retain the document itself. Clear its owner data,
  // not just the credential variable, before it can be restored from history.
  render();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    clearConnection();
    state.error = null;
    render();
    announce("The previous view was cleared. Connect again to read the current workspace.");
  }
});
render();
