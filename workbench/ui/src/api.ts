/**
 * Thin client for the loopback adapter.
 *
 * Every request carries the per-launch token from the served page's meta tag.
 * The token stops other websites from driving the local server; it is not a
 * login and does not identify a person. The owner id in each path is the demo
 * perspective, not an authenticated identity.
 *
 * Failure behaviour: network failures and non-JSON responses become an
 * `ApiError` with code `SERVER_UNREACHABLE`; server refusals keep their code,
 * message and field errors so the interface can show them next to the field.
 */
import type {
  ErrorBody, Explanation, FieldError, PublicationPreview, SearchQuery, SearchResponse, Session, Workspace,
  BriefContent,
} from "./contracts.js";

export class ApiError extends Error {
  constructor(readonly code: string, message: string, readonly status: number, readonly fields: FieldError[] = []) {
    super(message);
  }
}

function readToken(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="workbench-token"]');
  return meta?.content ?? "";
}

export class WorkbenchApi {
  private readonly token = readToken();

  private async request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await fetch(path, {
        method,
        credentials: "same-origin",
        cache: "no-store",
        headers: method === "POST"
          ? { "Content-Type": "application/json", "X-Workbench-Token": this.token }
          : { "X-Workbench-Token": this.token },
        ...(method === "POST" ? { body: JSON.stringify(body ?? {}) } : {}),
      });
    } catch {
      throw new ApiError("SERVER_UNREACHABLE", "The local workbench server is not responding. Is run_workbench.py still running?", 0);
    }
    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch {
      throw new ApiError("SERVER_UNREACHABLE", "The local server sent an unexpected response.", response.status);
    }
    if (!response.ok) {
      const error = (parsed as ErrorBody).error;
      throw new ApiError(error?.code ?? "UNKNOWN", error?.message ?? "The action was refused.", response.status, error?.fields ?? []);
    }
    return parsed as T;
  }

  session(): Promise<Session> {
    return this.request("GET", "/api/session");
  }

  advance(days: number): Promise<Session> {
    return this.request("POST", "/api/simulation/advance", { days });
  }

  reset(): Promise<Session> {
    return this.request("POST", "/api/simulation/reset", {});
  }

  workspace(owner: string): Promise<Workspace> {
    return this.request("GET", `/api/owners/${encodeURIComponent(owner)}/workspace`);
  }

  saveBrief(owner: string, expectedVersion: number, brief: BriefContent): Promise<Workspace> {
    return this.request("POST", this.ownerPath(owner, "brief"), { expected_version: expectedVersion, brief });
  }

  preview(owner: string, boardId: string, days: number, include: string[]): Promise<PublicationPreview> {
    return this.request("POST", this.ownerPath(owner, "publication/preview"), { board_id: boardId, expires_in_days: days, include });
  }

  approve(owner: string, preview: PublicationPreview): Promise<Workspace> {
    return this.request("POST", this.ownerPath(owner, "publication/approve"), {
      board_id: preview.board.board_id, expires_at: preview.expires_at, include: preview.include,
      approval_digest: preview.approval_digest,
    });
  }

  withdraw(owner: string, noticeId: string): Promise<Workspace> {
    return this.request("POST", this.ownerPath(owner, "publication/withdraw"), { notice_id: noticeId });
  }

  search(owner: string, query: SearchQuery): Promise<SearchResponse> {
    const body: Record<string, unknown> = { text: query.text, limit: query.limit };
    for (const key of ["mode", "focus_area", "offers", "basis", "venue"] as const) {
      if (query[key]) body[key] = query[key];
    }
    return this.request("POST", this.ownerPath(owner, "search"), body);
  }

  explain(owner: string, noticeId: string, revision: number): Promise<Explanation> {
    return this.request("POST", this.ownerPath(owner, "explain"), { notice_id: noticeId, revision });
  }

  blockNotice(owner: string, noticeId: string, revision: number): Promise<Workspace> {
    return this.request("POST", this.ownerPath(owner, "block-notice"), { notice_id: noticeId, revision });
  }

  propose(owner: string, noticeId: string, revision: number, firstStep: string, note: string): Promise<Workspace> {
    return this.request("POST", this.ownerPath(owner, "introductions"), { notice_id: noticeId, revision, first_step: firstStep, note });
  }

  requestInformation(owner: string, noticeId: string, revision: number, field: string, requesterNoticeId: string,
                     requesterRevision: number): Promise<Workspace> {
    return this.request("POST", this.ownerPath(owner, "information-requests"), {
      notice_id: noticeId, revision, field, requester_notice_id: requesterNoticeId, requester_revision: requesterRevision,
    });
  }

  declineInformationRequest(owner: string, requestId: string): Promise<Workspace> {
    return this.request("POST", this.ownerPath(owner, `information-requests/${encodeURIComponent(requestId)}/decline`), {});
  }

  introAction(owner: string, introId: string, action: string, body: Record<string, unknown> = {}): Promise<Workspace> {
    return this.request("POST", this.ownerPath(owner, `introductions/${encodeURIComponent(introId)}/${action}`), body);
  }

  private ownerPath(owner: string, rest: string): string {
    return `/api/owners/${encodeURIComponent(owner)}/${rest}`;
  }
}
