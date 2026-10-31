/**
 * Typed contracts for the local workbench API.
 *
 * These mirror the allowlisted view records built in
 * `src/agentmatch_workbench/application/views.py` and the service in
 * `application/service.py`. The browser never receives domain objects, claim
 * ids, source references or numeric ranking values; if a field is not listed
 * here, the server does not send it.
 *
 * Privacy note: `Brief` is only ever returned to the owner's own perspective.
 * Everything about another owner arrives as a `NoticeCard` (their approved
 * notice) or as an `IntroductionContext` (derived from both approved notices).
 */

export interface Option {
  id: string;
  label: string;
}

export interface Vocabularies {
  modes: Option[];
  capabilities: Option[];
  focus_areas: Option[];
  stages: Option[];
  availability: Option[];
  start_windows: Option[];
  venues: Option[];
  rhythms: Option[];
  feedback_styles: Option[];
  bases: Option[];
  first_steps: Option[];
  selectivity: Option[];
}

/** Editable brief fields, exactly as the server validates them. */
export interface BriefContent {
  mode: "project" | "contribute";
  title: string;
  desired_outcome: string;
  stage: string;
  focus_areas: string[];
  offers: string[];
  offers_complete: boolean;
  primary_need: string;
  also_seeks: string[];
  availability: string;
  availability_required: boolean;
  hours_per_week: number | null;
  start_window: string;
  venue: string | null;
  accepted_venues: string[];
  rhythm: string | null;
  accepted_rhythms: string[];
  feedback_style: string | null;
  accepted_feedback: string[];
  basis: string;
  accepted_bases: string[];
  basis_required: boolean;
  commercial_note: string;
  first_step: string;
  capacity: number;
  selectivity: string;
  private_notes: string;
}

export interface Brief extends BriefContent {
  brief_id: string;
  version: number;
  updated_at: string;
}

export interface Session {
  fictional_prototype: true;
  now: string;
  owners: { owner_id: string; alias: string; mode: Option | null }[];
  vocabularies: Vocabularies;
  examples: { project: BriefContent; contribute: BriefContent };
  publication_days: number[];
  optional_sections: string[];
  result_limits: number[];
  notices: { prototype: string; perspective: string; scope: string };
}

export interface BoardView {
  board_id: string;
  name: string;
  description: string;
  member_count: number;
  is_member: boolean;
}

export interface NoticeCard {
  notice_id: string;
  revision: number;
  board: { board_id: string; name: string };
  publisher_alias: string;
  mode: Option;
  title: string;
  desired_outcome: string;
  stage: Option;
  focus_areas: Option[];
  offers: Option[];
  offers_complete: boolean;
  primary_need: Option;
  also_seeks: Option[];
  availability: Option;
  availability_required: boolean;
  basis: Option;
  accepted_bases: Option[];
  basis_required: boolean;
  first_step: Option;
  working_arrangement: { venue: Option | null; accepted_venues: Option[]; rhythm: Option | null; accepted_rhythms: Option[] } | null;
  feedback: { style: Option | null; accepted: Option[] } | null;
  schedule: { hours_per_week: number | null; start_window: Option } | null;
  commercial_note: string | null;
  published_at: string;
  expires_at: string;
  provenance: string;
  untrusted_text_note: string;
}

export type OwnNoticeStatus = "active" | "suspended_brief_changed" | "expired" | "withdrawn";

export interface OwnNotice {
  notice_id: string;
  revision: number;
  status: OwnNoticeStatus;
  status_label: string;
  brief_version: number;
  card: NoticeCard;
  audience_aliases: string[];
  payload_digest: string;
}

export interface PublicationPreview {
  board: { board_id: string; name: string };
  purpose: string;
  purpose_label: string;
  audience: { kind: "fixed_membership_snapshot"; aliases: string[]; explanation: string };
  expires_at: string;
  brief_version: number;
  include: string[];
  payload: Record<string, unknown>;
  card: NoticeCard;
  approval_digest: string;
  not_included: string[];
}

export type PairStatus = "both_meet" | "needs_information" | "not_a_fit_now";

export interface SearchResult {
  card: NoticeCard;
  assessment: { pair_status: PairStatus; pair_label: string };
}

export interface SearchResponse {
  results: SearchResult[];
  empty_reason: "not_a_member" | "no_results" | null;
  limit: number;
  coverage: { boards_searched: string[]; statement: string; order: string };
}

export interface SearchQuery {
  text: string;
  mode: string | null;
  focus_area: string | null;
  offers: string | null;
  basis: string | null;
  venue: string | null;
  limit: number;
}

export interface Statement {
  aspect?: string;
  requirement?: string;
  statement: string;
  source?: string;
}

export interface Direction {
  heading: string;
  status: string;
  status_label: string;
  summary: string;
  supported: Statement[];
  mismatches: Statement[];
  unknowns: Statement[];
  requirements_met: Statement[];
  requirements_failed: Statement[];
  requirements_unknown: Statement[];
  notes: string[];
  threshold_note: string | null;
}

export interface Explanation {
  card: NoticeCard;
  explanation: {
    pair_status: PairStatus;
    pair_label: string;
    directions: [Direction, Direction];
    reciprocal: { they_offer_you: string[]; you_offer_them: string[]; shared_focus: string[] };
    method_note: string;
  };
  propose: { allowed: boolean; blockers: string[]; default_first_step: Option };
  information_request: {
    allowed: boolean;
    blockers: string[];
    fields: Option[];
    disclosure: string;
    requester_notice_id: string | null;
    requester_revision: number | null;
  };
}

/** A bounded request for a public notice field, never a private message. */
export interface InformationRequest {
  request_id: string;
  direction: "incoming" | "outgoing";
  counterpart_alias: string | null;
  field: Option | null;
  status: "pending" | "answered" | "declined" | "unavailable";
  status_label: string;
  created_at: string;
  expires_at: string;
  notice_id: string | null;
  revision: number | null;
  actions: ("review_brief" | "decline" | "reassess")[];
}

export interface IntroductionContext {
  achieve: { text: string; from_alias: string; title: string };
  contributions: { alias: string; offers: string[] }[];
  gains: { alias: string; gains: string[] }[];
  to_check: string[];
  suggested_first_step: Option;
  note: string | null;
  note_author_alias: string | null;
}

export type IntroAction =
  | "accept" | "withdraw_acceptance" | "change" | "decline" | "block" | "close"
  | "propose_step" | "agree_step" | "report_step";

export interface FirstStepView {
  state: "none" | "proposed" | "agreed" | "reported" | "both_reported_happened";
  state_label: string;
  version: number;
  action: Option | null;
  detail: string | null;
  proposed_by_alias: string | null;
  agreed_by_aliases: string[];
  i_agreed: boolean;
  reports: { alias: string; outcome: "happened" | "did_not_happen" | null }[];
}

export interface IntroductionView {
  intro_id: string;
  status: "pending" | "open" | "declined" | "expired" | "closed";
  status_label: string;
  status_detail: string | null;
  direction: "incoming" | "outgoing";
  counterpart_alias: string;
  context_version: number;
  context: IntroductionContext | null;
  proposal_digest: string | null;
  created_at: string;
  expires_at: string;
  acceptances: { alias: string; accepted: boolean; is_you: boolean }[];
  acceptance_cleared_by_change: boolean;
  channel: { status: string; opened_at: string; human_messages: number; encrypted: boolean; persistent: boolean; simulation_notice: string } | null;
  first_step: FirstStepView | null;
  actions: IntroAction[];
  reminder: string;
}

export interface Workspace {
  owner: { owner_id: string; alias: string };
  now: string;
  brief: Brief | null;
  boards: BoardView[];
  publications: OwnNotice[];
  introductions: IntroductionView[];
  information_requests: InformationRequest[];
  capacity: { limit: number; in_use: number };
  blocked_aliases: string[];
  activity: { at: string; text: string }[];
}

export interface FieldError {
  field: string;
  code: string;
  message: string;
}

export interface ErrorBody {
  error: { code: string; message: string; fields: FieldError[] };
}
