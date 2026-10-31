/**
 * Application state for one browser tab. Nothing here is persisted: reloading
 * the page starts again from the server's in-memory state.
 *
 * Per-perspective state (draft, preview, search, explanation) is cleared when
 * the demo perspective changes, so one fictional owner's private draft is never
 * left on screen while acting as another.
 */
import type {
  BriefContent, Explanation, FieldError, PublicationPreview, SearchQuery, SearchResponse, Session, Workspace,
} from "./contracts.js";

export type Tab = "brief" | "publish" | "discover" | "introductions";

export interface PublishForm {
  boardId: string | null;
  days: number;
  include: string[];
}

export interface AppState {
  session: Session | null;
  owner: string | null;
  workspace: Workspace | null;
  tab: Tab;
  briefDraft: BriefContent | null;
  briefDirty: boolean;
  briefErrors: FieldError[];
  publishForm: PublishForm;
  preview: PublicationPreview | null;
  query: SearchQuery;
  search: SearchResponse | null;
  searchErrors: FieldError[];
  explanation: Explanation | null;
  reviewingRequestId: string | null;
  busy: string | null;
  error: string | null;
  status: string | null;
}

export const emptyQuery = (): SearchQuery => ({ text: "", mode: null, focus_area: null, offers: null, basis: null, venue: null, limit: 10 });

export function initialState(): AppState {
  return {
    session: null, owner: null, workspace: null, tab: "brief",
    briefDraft: null, briefDirty: false, briefErrors: [],
    publishForm: { boardId: null, days: 14, include: ["working_arrangement", "feedback", "schedule", "commercial_note"] },
    preview: null, query: emptyQuery(), search: null, searchErrors: [], explanation: null, reviewingRequestId: null,
    busy: null, error: null, status: null,
  };
}

export function perspectiveReset(state: AppState): Partial<AppState> {
  return {
    workspace: null, briefDraft: null, briefDirty: false, briefErrors: [], preview: null,
    publishForm: { ...state.publishForm, boardId: null }, query: emptyQuery(), search: null, searchErrors: [],
    explanation: null, reviewingRequestId: null, error: null,
  };
}
