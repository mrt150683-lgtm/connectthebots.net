/**
 * Controller: owns state, performs API calls, renders, and manages focus.
 *
 * Rendering rebuilds the page from state. Text inputs update state without a
 * re-render, so typing never loses focus; after a render the element that had
 * focus (by id) is focused again, and after navigation focus moves to the new
 * panel heading so keyboard and screen-reader users land in the right place.
 */
import type { Actions } from "./actions.js";
import { ApiError, WorkbenchApi } from "./api.js";
import type { BriefContent, InformationRequest, PublicationPreview, Workspace } from "./contracts.js";
import { confirmAction, h } from "./dom.js";
import { briefEditor } from "./components/briefEditor.js";
import { discoveryPanel } from "./components/discovery.js";
import { introductionsPanel } from "./components/introductions.js";
import { informationRequestReview } from "./components/informationRequests.js";
import { publicationPanel } from "./components/publication.js";
import { activity, footer, header, hero, tabs } from "./components/shell.js";
import { type AppState, type Tab, initialState, perspectiveReset } from "./state.js";

const FALLBACK_OWNER = "marlow";

export class App implements Actions {
  private state: AppState = initialState();
  private readonly api = new WorkbenchApi();
  private pendingFocus: string | null = null;

  constructor(private readonly root: HTMLElement, private readonly live: HTMLElement) {}

  async start(): Promise<void> {
    this.render();
    try {
      const session = await this.api.session();
      const owner = new URLSearchParams(location.hash.slice(1)).get("as") ?? FALLBACK_OWNER;
      const known = session.owners.some((o) => o.owner_id === owner) ? owner : session.owners[0]?.owner_id ?? null;
      this.patch({ session, owner: known });
      if (known) await this.loadWorkspace(known);
    } catch (error) {
      this.fail(error);
    }
  }

  // ----- state plumbing ----------------------------------------------------

  private patch(patch: Partial<AppState>, render = true): void {
    this.state = { ...this.state, ...patch };
    if (render) this.render();
  }

  private announce(message: string): void {
    this.state.status = message;
    this.live.textContent = "";
    // A fresh text node makes screen readers announce repeated messages too.
    window.setTimeout(() => { this.live.textContent = message; }, 30);
  }

  private fail(error: unknown, fallback = "Something went wrong."): void {
    const message = error instanceof ApiError ? error.message : fallback;
    this.patch({ busy: null, error: message });
    this.announce(message);
  }

  private applyWorkspace(workspace: Workspace, extra: Partial<AppState> = {}): void {
    const keepDraft = this.state.briefDirty && this.state.briefDraft !== null && !("briefDraft" in extra);
    this.patch({
      workspace, busy: null, error: null,
      session: this.state.session ? { ...this.state.session, now: workspace.now } : null,
      ...(keepDraft ? {} : { briefDraft: workspace.brief ? strip(workspace.brief) : null, briefDirty: false }),
      ...extra,
    });
  }

  private async loadWorkspace(owner: string): Promise<void> {
    this.patch({ busy: "load" });
    try {
      this.applyWorkspace(await this.api.workspace(owner), { briefDraft: null });
      const ws = this.state.workspace;
      if (ws?.brief) this.patch({ briefDraft: strip(ws.brief), briefDirty: false });
    } catch (error) {
      this.fail(error);
    }
  }

  private async run(busy: string, task: () => Promise<void>): Promise<void> {
    if (this.state.busy) return;
    this.patch({ busy, error: null });
    try {
      await task();
    } catch (error) {
      if (error instanceof ApiError && ["NOTICE_UNAVAILABLE", "INTRODUCTION_UNAVAILABLE", "INFORMATION_UNAVAILABLE", "INFORMATION_NOT_ALLOWED"].includes(error.code)) {
        // A stale card was used. Refresh so it disappears rather than lingering.
        this.patch({ explanation: null, search: null, reviewingRequestId: null });
        if (this.state.owner) this.applyWorkspace(await this.api.workspace(this.state.owner).catch(() => this.state.workspace!), {});
      }
      this.fail(error);
    }
  }

  private owner(): string {
    if (!this.state.owner) throw new ApiError("NO_OWNER", "Choose a perspective first.", 0);
    return this.state.owner;
  }

  // ----- actions -------------------------------------------------------------

  selectOwner(ownerId: string): void {
    if (ownerId === this.state.owner || this.state.busy) return;
    const alias = this.state.session?.owners.find((o) => o.owner_id === ownerId)?.alias ?? ownerId;
    this.patch({ owner: ownerId, ...perspectiveReset(this.state) });
    history.replaceState(null, "", `#as=${encodeURIComponent(ownerId)}`);
    this.announce(`Now viewing as ${alias}. This is a demo perspective, not a sign-in.`);
    this.pendingFocus = "perspective";
    void this.loadWorkspace(ownerId);
  }

  selectTab(tab: Tab, focusHeading = false): void {
    this.patch({ tab });
    if (focusHeading) this.pendingFocus = `panel-heading-${tab}`;
    this.render();
  }

  advanceClock(days: number): void {
    void this.run("clock", async () => {
      const session = await this.api.advance(days);
      this.patch({ session, search: null, explanation: null, preview: null });
      await this.refresh();
      this.announce(`Simulated time moved forward ${days} day${days === 1 ? "" : "s"}.`);
    });
  }

  resetDemo(): void {
    void confirmAction("Reset the demo?", "This discards every change in this local session and restores the fictional starting data.", "Reset").then((ok) => {
      if (!ok) return;
      void this.run("reset", async () => {
        const session = await this.api.reset();
        this.patch({ session, ...perspectiveReset(this.state) });
        this.pendingFocus = "main";
        await this.refresh();
        this.announce("Demo reset to the fictional starting data.");
      });
    });
  }

  updateDraft(patch: Partial<BriefContent>): void {
    if (!this.state.briefDraft) return;
    const wasDirty = this.state.briefDirty;
    this.patch({ briefDraft: { ...this.state.briefDraft, ...patch }, briefDirty: true }, false);
    if (!wasDirty) this.render();  // show the "unsaved" marker once
  }

  loadExample(mode: "project" | "contribute"): void {
    const example = this.state.session?.examples[mode];
    if (!example) return;
    this.patch({ briefDraft: structuredClone(example), briefDirty: true, briefErrors: [] });
    this.announce(`Loaded the fictional ${mode === "project" ? "project" : "contributor"} example. Edit it, then save.`);
  }

  discardDraft(): void {
    const brief = this.state.workspace?.brief;
    this.patch({ briefDraft: brief ? strip(brief) : null, briefDirty: false, briefErrors: [] });
    this.announce("Unsaved changes discarded.");
  }

  saveBrief(): void {
    const draft = this.state.briefDraft;
    if (!draft) return;
    void this.run("save", async () => {
      try {
        const ws = await this.api.saveBrief(this.owner(), this.state.workspace?.brief?.version ?? 0, draft);
        this.applyWorkspace(ws, { briefDraft: ws.brief ? strip(ws.brief) : null, briefDirty: false, briefErrors: [], preview: null });
        this.announce(`Private brief saved as version ${ws.brief?.version}.`);
      } catch (error) {
        if (error instanceof ApiError && error.fields.length) {
          this.patch({ busy: null, briefErrors: error.fields, error: null });
          this.pendingFocus = "brief-error-summary";
          this.render();
          this.announce(`${error.fields.length} field${error.fields.length === 1 ? "" : "s"} need attention.`);
          return;
        }
        throw error;
      }
    });
  }

  setPublishForm(patch: Partial<AppState["publishForm"]>): void {
    this.patch({ publishForm: { ...this.state.publishForm, ...patch } }, false);
  }

  previewPublication(): void {
    const ws = this.state.workspace;
    const form = this.state.publishForm;
    const boardId = form.boardId ?? ws?.boards[0]?.board_id;
    if (!boardId) return;
    void this.run("preview", async () => {
      const preview = await this.api.preview(this.owner(), boardId, form.days, form.include);
      this.pendingFocus = "publication-preview-title";
      this.patch({ preview, busy: null });
      this.announce("Preview ready. Nothing has been published.");
    });
  }

  approvePublication(preview: PublicationPreview): void {
    void this.run("approve", async () => {
      try {
        const ws = await this.api.approve(this.owner(), preview);
        this.applyWorkspace(ws, { preview: null });
        this.announce(`Published to ${preview.board.name}.`);
      } catch (error) {
        if (error instanceof ApiError && error.code === "APPROVAL_STALE") this.patch({ preview: null });
        throw error;
      }
    });
  }

  clearPreview(): void {
    this.patch({ preview: null });
  }

  withdraw(noticeId: string): void {
    void confirmAction("Withdraw this notice?", "It stops being discoverable immediately. People who already read it may remember it.", "Withdraw").then((ok) => {
      if (!ok) return;
      void this.run("withdraw", async () => {
        this.applyWorkspace(await this.api.withdraw(this.owner(), noticeId));
        this.announce("Notice withdrawn.");
      });
    });
  }

  setQuery(patch: Partial<AppState["query"]>): void {
    this.patch({ query: { ...this.state.query, ...patch } }, false);
  }

  runSearch(): void {
    void this.run("search", async () => {
      try {
        const search = await this.api.search(this.owner(), this.state.query);
        // Keep the perspective locked until both reads finish; an old owner's
        // workspace response must never overwrite a newly selected owner.
        this.patch({ search, searchErrors: [], explanation: null });
        await this.refresh();
        const count = search.results.length;
        this.announce(count ? `${count} notice${count === 1 ? "" : "s"} shown.` : "No current notices matched.");
      } catch (error) {
        if (error instanceof ApiError && error.fields.length) {
          this.patch({ busy: null, searchErrors: error.fields });
          this.announce("Some search fields need attention.");
          return;
        }
        throw error;
      }
    });
  }

  explain(noticeId: string, revision: number): void {
    void this.run("explain", async () => {
      const explanation = await this.api.explain(this.owner(), noticeId, revision);
      this.pendingFocus = "explanation-title";
      this.patch({ explanation, busy: null });
    });
  }

  closeExplanation(): void {
    const id = this.state.explanation?.card.notice_id;
    this.pendingFocus = id ? `explain-${id}` : "panel-heading-discover";
    this.patch({ explanation: null });
  }

  requestInformation(noticeId: string, revision: number, fieldId: string): void {
    const explanation = this.state.explanation;
    const request = explanation?.information_request;
    const field = request?.fields.find((item) => item.id === fieldId);
    const owner = this.owner();
    if (!explanation || explanation.card.notice_id !== noticeId || explanation.card.revision !== revision ||
        !request?.allowed || !field || request.requester_notice_id === null || request.requester_revision === null) return;
    const requesterNoticeId = request.requester_notice_id;
    const requesterRevision = request.requester_revision;
    void confirmAction(`Request ${field.label} from ${explanation.card.publisher_alias}?`,
      `${request.disclosure} Requested detail: ${field.label}. The notice owner can decline or approve a revised notice; this does not open a connection.`, "Send request", "primary").then((ok) => {
      if (!ok || this.state.owner !== owner) return;
      void this.run("request-information", async () => {
        const workspace = await this.api.requestInformation(owner, noticeId, revision, field.id, requesterNoticeId, requesterRevision);
        this.pendingFocus = "information-requests-title";
        this.applyWorkspace(workspace, { explanation: null, tab: "discover" });
        this.announce("Information request sent. The owner decides whether to publish an answer.");
      });
    });
  }

  reviewInformationRequest(request: InformationRequest): void {
    if (!request.actions.includes("review_brief") || request.direction !== "incoming") return;
    const notice = this.state.workspace?.publications.find((item) => item.notice_id === request.notice_id);
    this.pendingFocus = "panel-heading-brief";
    this.patch({ tab: "brief", reviewingRequestId: request.request_id, preview: null,
      publishForm: { ...this.state.publishForm, boardId: notice?.card.board.board_id ?? this.state.publishForm.boardId } });
    this.announce(`Review ${request.field?.label ?? "the requested detail"} in your private brief, then choose whether to approve a revised publication.`);
  }

  declineInformationRequest(requestId: string): void {
    void this.run("decline-information", async () => {
      this.applyWorkspace(await this.api.declineInformationRequest(this.owner(), requestId));
      this.announce("Information request declined. Nothing was added to your notice.");
    });
  }

  reassessInformationRequest(request: InformationRequest): void {
    if (!request.actions.includes("reassess") || request.notice_id === null || request.revision === null) return;
    const noticeId = request.notice_id;
    const revision = request.revision;
    void this.run("reassess", async () => {
      // Re-read the notice through the normal authority gate. A stored request
      // never authorizes a stale revision, nor turns the answer into consent.
      const search = await this.api.search(this.owner(), this.state.query);
      const explanation = await this.api.explain(this.owner(), noticeId, revision);
      const workspace = await this.api.workspace(this.owner());
      this.pendingFocus = "explanation-title";
      this.applyWorkspace(workspace, { tab: "discover", explanation, search });
      this.announce("Updated notice reassessed. Review the current explanation before deciding on an introduction.");
    });
  }

  dismissRequestReview(): void {
    this.patch({ reviewingRequestId: null });
  }

  blockNotice(noticeId: string, revision: number, alias: string): void {
    void confirmAction(`Block ${alias}?`, "Their notices are hidden from you and yours from them, and any introduction between you closes.", "Block").then((ok) => {
      if (!ok) return;
      void this.run("block", async () => {
        const ws = await this.api.blockNotice(this.owner(), noticeId, revision);
        this.applyWorkspace(ws, { explanation: null, search: null });
        this.announce(`${alias} is blocked. Search again to see current results.`);
      });
    });
  }

  propose(noticeId: string, revision: number, firstStep: string, note: string): void {
    void this.run("propose", async () => {
      const ws = await this.api.propose(this.owner(), noticeId, revision, firstStep, note);
      this.applyWorkspace(ws, { explanation: null, tab: "introductions" });
      this.pendingFocus = "panel-heading-introductions";
      this.render();
      this.announce("Introduction proposed. Nothing is accepted yet, including by you.");
    });
  }

  introAction(introId: string, action: string, body: Record<string, unknown> = {}, confirm?: { title: string; body: string; label: string }): void {
    const go = () => void this.run(action, async () => {
      const ws = await this.api.introAction(this.owner(), introId, action, body);
      this.applyWorkspace(ws);
      const intro = ws.introductions.find((i) => i.intro_id === introId);
      this.announce(intro ? intro.status_label : "Done.");
    });
    if (confirm) {
      void confirmAction(confirm.title, confirm.body, confirm.label).then((ok) => { if (ok) go(); });
    } else {
      go();
    }
  }

  private async refresh(): Promise<void> {
    if (this.state.owner) this.applyWorkspace(await this.api.workspace(this.state.owner));
  }

  // ----- rendering -----------------------------------------------------------

  private render(): void {
    // Remember focus by id, or by role and visible label for controls without an id,
    // so a re-render never drops keyboard focus back to the top of the page.
    const active = document.activeElement as HTMLElement | null;
    const activeId = active?.id || null;
    const activeLabel = !activeId && active && active !== document.body ? `${active.tagName}|${active.textContent?.trim() ?? ""}` : null;
    const s = this.state;
    const panel = s.tab === "brief" ? briefEditor(s, this) : s.tab === "publish" ? publicationPanel(s, this)
      : s.tab === "discover" ? discoveryPanel(s, this) : introductionsPanel(s, this);
    const main = h("main", { id: "main", tabindex: -1 },
      hero(s),
      tabs(s, this),
      h("div", { className: "shell" },
        s.error ? h("div", { className: "error-banner", role: "alert" }, h("p", {}, s.error),
          h("button", { type: "button", className: "button ghost small", onclick: () => this.patch({ error: null }) }, "Dismiss")) : null,
        h("div", { id: "panel", role: "tabpanel", "aria-labelledby": `tab-${s.tab}`, "aria-busy": s.busy ? "true" : "false" },
          s.tab === "brief" || s.tab === "publish" ? informationRequestReview(s, this) : null, panel)),
      activity(s));
    this.root.replaceChildren(h("a", { className: "skip", href: "#main" }, "Skip to content"), header(s, this), main, footer());
    const target = this.pendingFocus ?? activeId;
    const fromAction = this.pendingFocus !== null;
    this.pendingFocus = null;
    if (target) {
      const element = document.getElementById(target);
      element?.focus({ preventScroll: !fromAction });
      // A control disabled while busy cannot take focus; try again on the next render.
      if (element instanceof HTMLButtonElement && element.disabled) this.pendingFocus = target;
    } else if (activeLabel) {
      const [tag, label] = activeLabel.split("|");
      const match = Array.from(this.root.querySelectorAll<HTMLElement>(tag ?? "BUTTON")).find((el) => el.textContent?.trim() === label);
      (match ?? this.root.querySelector<HTMLElement>("#panel h2"))?.focus({ preventScroll: true });
    }
  }
}

function strip(brief: Workspace["brief"]): BriefContent {
  const { brief_id: _id, version: _version, updated_at: _updated, ...content } = brief!;
  return content;
}
