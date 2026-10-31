/**
 * The operations components may request. Components never call the API
 * directly; the controller in `app.ts` performs the request, updates state and
 * re-renders, so loading, error and focus handling stay in one place.
 */
import type { BriefContent, InformationRequest, PublicationPreview, SearchQuery } from "./contracts.js";
import type { PublishForm, Tab } from "./state.js";

export interface Actions {
  selectOwner(ownerId: string): void;
  selectTab(tab: Tab, focusHeading?: boolean): void;
  advanceClock(days: number): void;
  resetDemo(): void;
  updateDraft(patch: Partial<BriefContent>): void;
  loadExample(mode: "project" | "contribute"): void;
  discardDraft(): void;
  saveBrief(): void;
  setPublishForm(patch: Partial<PublishForm>): void;
  previewPublication(): void;
  approvePublication(preview: PublicationPreview): void;
  clearPreview(): void;
  withdraw(noticeId: string): void;
  setQuery(patch: Partial<SearchQuery>): void;
  runSearch(): void;
  explain(noticeId: string, revision: number): void;
  closeExplanation(): void;
  requestInformation(noticeId: string, revision: number, field: string): void;
  reviewInformationRequest(request: InformationRequest): void;
  declineInformationRequest(requestId: string): void;
  reassessInformationRequest(request: InformationRequest): void;
  dismissRequestReview(): void;
  blockNotice(noticeId: string, revision: number, alias: string): void;
  propose(noticeId: string, revision: number, firstStep: string, note: string): void;
  introAction(introId: string, action: string, body?: Record<string, unknown>, confirm?: { title: string; body: string; label: string }): void;
}
