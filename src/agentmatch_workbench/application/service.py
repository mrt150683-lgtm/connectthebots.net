"""Workbench use cases. The only entry point the HTTP adapter calls.

Every public method takes the acting ``owner_id`` first. In this prototype that
id comes from the demo perspective switch, which is **not authentication**:
anyone who can reach the local server can act as any fictional owner. The
service still enforces everything that does not depend on identity proof:
membership, audience, purpose, expiry, revocation, blocks, versions, digests
and capacity.

Re-check at use: methods that act on a notice or introduction take the id and
revision the browser last saw, reload authoritative state, and run the
visibility gate again with the current clock. A stale card therefore cannot
bypass a later withdrawal, expiry, block or brief edit.

Every method returns allowlisted records from ``views``. Errors are
``WorkbenchError`` with a stable code and a message safe to show the viewer.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from functools import wraps
from threading import RLock
from typing import Any

from agentmatch.models import parse_time, time_text
from agentmatch.ranking import rank_qualified
from ..domain import vocabulary as v
from ..domain.assessment import MatchFacts, facts_from_brief, facts_from_notice
from ..domain.briefs import BriefValidationError, parse_brief, revise
from ..domain.explanations import PAIR_LABELS, assess, explain_pair, pair_status
from ..domain.introductions import (Introduction, IntroductionError, NoticeRef, accept as accept_intro, agree_step,
                                    block as block_intro, build_proposal, change_context, clean_note, close, decline,
                                    close_connection, expire_if_due, introduction_ttl, propose_step, report_step, withdraw_acceptance)
from ..domain.information import (INFORMATION_FIELDS, MAX_PENDING_REQUESTS, MAX_WINDOW_REQUESTS, REQUEST_DAYS, REQUEST_WINDOW_SECONDS,
                                  InformationRequest, missing_fields)
from ..domain.publication import (OPTIONAL_SECTIONS, PublicationError, PublishedNotice, approve_publication,
                                  authorize_publication, prepare_publication)
from ..domain.visibility import notice_unavailable_reason, own_notice_status
from . import views
from .ports import Clock, QueryError, SearchIndex, WorkbenchStore, index_document, parse_query

BRIEF_VALIDITY = timedelta(days=180)
PUBLICATION_DAYS = (7, 14, 30)
ACTIVITY_LIMIT = 40

MESSAGES = {
    "UNKNOWN_OWNER": "That fictional owner does not exist.",
    "BRIEF_INVALID": "Some brief fields need attention.",
    "VERSION_CONFLICT": "Your brief changed in another tab. Reload it before saving.",
    "BOARD_UNAVAILABLE": "You are not a member of that board.",
    "EXPIRY_OUT_OF_RANGE": "Choose an expiry within the allowed range.",
    "APPROVAL_STALE": "This preview no longer matches your current brief or settings. Review the new preview before approving.",
    "NOTICE_UNAVAILABLE": "This notice is no longer available to you.",
    "NOT_YOUR_NOTICE": "You can only withdraw your own notices.",
    "QUERY_INVALID": "Some search fields need attention.",
    "NO_BRIEF": "Save a brief before using discovery.",
    "INTRODUCTION_NOT_ALLOWED": "An introduction is not possible yet.",
    "INTRODUCTION_UNAVAILABLE": "This introduction is no longer available.",
    "INTRODUCTION_NOT_PENDING": "This introduction is no longer waiting for acceptance.",
    "CONTEXT_CHANGED": "The introduction changed after you opened it. Review the current version before accepting.",
    "NO_ACCEPTANCE_TO_WITHDRAW": "You have not accepted this introduction.",
    "CAPACITY_FULL": "Your introduction capacity is in use. Close or finish another introduction first.",
    "NO_CHANGE": "Nothing changed.",
    "CHANNEL_NOT_OPEN": "The simulated channel is not open.",
    "STEP_CHANGED": "The proposed first step changed. Review the current proposal.",
    "STEP_NOT_AGREED": "Both people must agree a step before reporting an outcome.",
    "STEP_ALREADY_REPORTED": "An outcome was already reported for this step.",
    "INVALID_REQUEST": "The request was not in the expected form.",
    "INFORMATION_NOT_ALLOWED": "This information request is not available. Refresh the notice and try again.",
    "INFORMATION_UNAVAILABLE": "This information request is no longer available.",
    "INFORMATION_LIMIT": "Your information-request limit is in use: at most 20 in 24 hours and 5 pending. You can review existing requests without sending another.",
}
STATUS = {"UNKNOWN_OWNER": 404, "NOTICE_UNAVAILABLE": 404, "INTRODUCTION_UNAVAILABLE": 404, "BRIEF_INVALID": 422,
          "QUERY_INVALID": 422, "VERSION_CONFLICT": 409, "APPROVAL_STALE": 409, "CONTEXT_CHANGED": 409,
          "STEP_CHANGED": 409, "BOARD_UNAVAILABLE": 403, "NOT_YOUR_NOTICE": 403}


class WorkbenchError(ValueError):
    def __init__(self, code: str, *, errors: list[dict[str, str]] | None = None, detail: str | None = None):
        self.code, self.errors, self.detail = code, errors or [], detail
        self.status = STATUS.get(code, 400 if code == "INVALID_REQUEST" else 409)
        super().__init__(code)

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.detail or MESSAGES.get(self.code, "The action was refused."),
                          "fields": self.errors}}


def _fields(data: Any, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - set(optional):
        raise WorkbenchError("INVALID_REQUEST")
    return data


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80 or not all(c.isalnum() or c in "-_" for c in value):
        raise WorkbenchError("INVALID_REQUEST")
    return value


def _revision(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise WorkbenchError("INVALID_REQUEST")
    return value


def transaction(method):
    """Serialize complete service operations, including derived-state refreshes.

    Pending introductions reserve both participants' capacity in this same
    critical section. The lock is reentrant because commands return a newly
    read workspace. A future persistent store must replace it with a database
    transaction; it is not a distributed lock or durable reservation.
    """
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


class WorkbenchService:
    def __init__(self, store: WorkbenchStore, index: SearchIndex, clock: Clock, *, examples: dict[str, dict[str, Any]],
                 information_clock: Clock | None = None):
        self.store, self.index, self.clock, self.examples = store, index, clock, examples
        self.information_clock = information_clock
        self._lock = RLock()

    # ----- shared helpers -------------------------------------------------

    def now(self) -> datetime:
        return self.clock.now()

    def information_now(self) -> datetime:
        """Explicit accounting time; persistent adapters inject their UTC clock."""
        return (self.information_clock or self.clock).now()

    def migrate_information_accounting(self) -> bool:
        """Anchor legacy records once instead of silently replenishing their cap.

        A migrated record is conservatively recent for one full window. The
        persistent adapter commits this first-seen time during open/recovery.
        Neither a restart nor a replacement credential resets the timestamps.
        """
        changed = False
        for request in self.store.information_requests.values():
            if request.accounted_at is None:
                request.accounted_at = self.information_now()
                changed = True
        return changed

    def information_budget(self, owner_id: str) -> dict[str, Any]:
        """Owner-only rolling counts; never reveal another owner's queue size."""
        self.migrate_information_accounting()
        now = self.information_now()
        requests = list(self.store.information_requests.values())
        for request in requests:
            self._refresh_information(request)
        sent = [r for r in requests if r.requester_id == owner_id]
        received = [r for r in requests if r.recipient_id == owner_id]
        cutoff = now - timedelta(seconds=REQUEST_WINDOW_SECONDS)
        recent_sent = [r for r in sent if r.accounted_at is not None and r.accounted_at > cutoff]
        recent_received = [r for r in received if r.accounted_at is not None and r.accounted_at > cutoff]
        def reset_at(records):
            return min((r.accounted_at + timedelta(seconds=REQUEST_WINDOW_SECONDS) for r in records), default=None)
        next_sent, next_received = reset_at(recent_sent), reset_at(recent_received)
        return {"policy": "rolling_24h", "window_seconds": REQUEST_WINDOW_SECONDS,
                "sent_limit": MAX_WINDOW_REQUESTS, "received_limit": MAX_WINDOW_REQUESTS,
                "pending_limit": MAX_PENDING_REQUESTS,
                "sent_in_window": len(recent_sent), "received_in_window": len(recent_received),
                "sent_pending": sum(r.state == "pending" for r in sent),
                "received_pending": sum(r.state == "pending" for r in received),
                "next_sent_reset_at": time_text(next_sent) if next_sent else None,
                "next_received_reset_at": time_text(next_received) if next_received else None}

    def _owner(self, owner_id: Any) -> str:
        if not isinstance(owner_id, str) or owner_id not in self.store.owners:
            raise WorkbenchError("UNKNOWN_OWNER")
        return owner_id

    def _alias(self, owner_id: str) -> str:
        return self.store.owners[owner_id].alias

    def _aliases(self) -> dict[str, str]:
        return {owner_id: profile.alias for owner_id, profile in self.store.owners.items()}

    def _brief_version(self, owner_id: str) -> int | None:
        brief = self.store.briefs.get(owner_id)
        return None if brief is None else brief.version

    def _blocked_with(self, owner_id: str) -> tuple[str, ...]:
        return tuple(sorted({b if a == owner_id else a for a, b in self.store.blocked_pairs if owner_id in (a, b)}))

    def _unavailable(self, viewer_id: str, notice: PublishedNotice | None, expected_revision: int | None = None) -> str | None:
        grant = self.store.grants.get(notice.grant_id) if notice else None
        board = self.store.boards.get(notice.board_id) if notice else None
        return notice_unavailable_reason(notice, grant, viewer_id=viewer_id, board=board,
                                         blocked_pairs=frozenset(self.store.blocked_pairs),
                                         current_brief_version=self._brief_version(notice.owner_id) if notice else None,
                                         now=self.now(), expected_revision=expected_revision)

    def _visible_notice(self, viewer_id: str, notice_id: Any, revision: Any) -> PublishedNotice:
        """Authoritative re-check of a notice reference that came from a card."""
        notice = self.store.notices.get(_identifier(notice_id))
        if self._unavailable(viewer_id, notice, _revision(revision)) is not None:
            raise WorkbenchError("NOTICE_UNAVAILABLE")
        assert notice is not None
        return notice

    def _viewer_facts(self, owner_id: str) -> MatchFacts:
        brief = self.store.briefs.get(owner_id)
        if brief is None:
            raise WorkbenchError("NO_BRIEF")
        return facts_from_brief(brief, alias=self._alias(owner_id), expires_at=brief.updated_at + BRIEF_VALIDITY)

    @staticmethod
    def _notice_facts(notice: PublishedNotice) -> MatchFacts:
        return facts_from_notice(owner_id=notice.owner_id, notice_id=notice.notice_id, revision=notice.revision,
                                 payload=notice.payload, published_at=notice.published_at, expires_at=notice.expires_at)

    def _log_both(self, intro: Introduction, actor: str, mine: str, theirs: str) -> None:
        self.store.log(actor, self.now(), mine)
        self.store.log(intro.other(actor), self.now(), theirs)

    def _active_introductions(self, owner_id: str, *, excluding: str | None = None) -> int:
        count = 0
        for intro in self.store.introductions_for(owner_id):
            self._refresh(intro)
            if intro.status in {"pending", "open"} and intro.intro_id != excluding:
                count += 1
        return count

    # ----- session and workspace -----------------------------------------

    @transaction
    def session(self) -> dict[str, Any]:
        owners = []
        for owner_id, profile in sorted(self.store.owners.items()):
            brief = self.store.briefs.get(owner_id)
            owners.append({"owner_id": owner_id, "alias": profile.alias,
                           "mode": views.option(v.MODES, brief.mode) if brief else None})
        return {"fictional_prototype": True, "now": time_text(self.now()), "owners": owners,
                "vocabularies": v.all_vocabularies(), "examples": self.examples,
                "publication_days": list(PUBLICATION_DAYS), "optional_sections": list(OPTIONAL_SECTIONS),
                "result_limits": [5, 10, 20],
                "notices": {"prototype": "Fictional prototype. Every person, board and notice here is invented.",
                            "perspective": "The perspective switch is a demo control, not sign-in or authentication.",
                            "scope": ("Local simulation only: no accounts, no encryption, no network transport. "
                                      "This does not complete the R1 or R2 release gates.")}}

    @transaction
    def workspace(self, owner_id: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        brief = self.store.briefs.get(owner_id)
        publications = []
        for notice in self.store.notices_by_owner(owner_id):
            board = self.store.boards[notice.board_id]
            grant = self.store.grants.get(notice.grant_id)
            status = own_notice_status(notice, grant, current_brief_version=brief.version if brief else -1, now=self.now())
            audience = sorted(self._alias(o) for o in (grant.audience_member_ids if grant else ()))
            publications.append(views.own_notice_view(notice, board, status, audience))
        intros = [self._intro_view(intro, owner_id) for intro in self.store.introductions_for(owner_id)]
        activity = list(reversed(self.store.activity.get(owner_id, [])))[:ACTIVITY_LIMIT]
        return {"owner": {"owner_id": owner_id, "alias": self._alias(owner_id)}, "now": time_text(self.now()),
                "brief": views.brief_view(brief) if brief else None,
                "boards": [views.board_view(b, viewer_id=owner_id) for b in self.store.boards_for(owner_id)],
                "publications": publications, "introductions": intros,
                "information_requests": [self._information_view(request, owner_id)
                                         for request in self.store.information_requests.values()
                                         if owner_id in request.participants],
                "capacity": {"limit": brief.capacity if brief else 0, "in_use": self._active_introductions(owner_id)},
                "blocked_aliases": sorted(self._alias(o) for a, o in self.store.blocked_pairs if a == owner_id),
                "activity": activity}

    # ----- briefs ----------------------------------------------------------

    @transaction
    def save_brief(self, owner_id: Any, data: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        data = _fields(data, {"expected_version", "brief"})
        current = self.store.briefs.get(owner_id)
        expected = data["expected_version"]
        if (current.version if current else 0) != expected:
            raise WorkbenchError("VERSION_CONFLICT")
        try:
            if current is None:
                brief = parse_brief(data["brief"], owner_id=owner_id, version=1, updated_at=self.now())
            else:
                candidate = parse_brief(data["brief"], owner_id=owner_id, version=current.version, updated_at=self.now())
                if candidate.content() == current.content():
                    return self.workspace(owner_id)  # identical resubmission: no new version, nothing invalidated
                brief = revise(current, data["brief"], updated_at=self.now())
        except BriefValidationError as exc:
            raise WorkbenchError("BRIEF_INVALID", errors=exc.errors) from exc
        self.store.briefs[owner_id] = brief
        self.store.log(owner_id, self.now(), f"You saved your private brief (version {brief.version}).")
        if current is not None and any(n.state == "active" for n in self.store.notices_by_owner(owner_id)):
            self.store.log(owner_id, self.now(), "Your published notices are paused until you approve the new version.")
        for intro in self.store.introductions_for(owner_id):
            self._refresh(intro)
        return self.workspace(owner_id)

    # ----- publication -----------------------------------------------------

    def _publication_request(self, owner_id: str, data: dict[str, Any], *, expires_at: datetime):
        brief = self.store.briefs.get(owner_id)
        if brief is None:
            raise WorkbenchError("NO_BRIEF")
        board = self.store.boards.get(_identifier(data["board_id"]))
        if board is None or owner_id not in board.member_ids:
            raise WorkbenchError("BOARD_UNAVAILABLE")
        include = data["include"]
        if not isinstance(include, list) or any(not isinstance(s, str) for s in include):
            raise WorkbenchError("INVALID_REQUEST")
        try:
            request = prepare_publication(brief, publisher_alias=self._alias(owner_id), board_id=board.board_id,
                                          board_member_ids=board.member_ids, include=tuple(include),
                                          expires_at=expires_at, now=self.now())
        except PublicationError as exc:
            raise WorkbenchError(exc.code if exc.code in MESSAGES else "INVALID_REQUEST") from exc
        return brief, board, request

    @transaction
    def preview_publication(self, owner_id: Any, data: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        data = _fields(data, {"board_id", "expires_in_days", "include"})
        if data["expires_in_days"] not in PUBLICATION_DAYS or type(data["expires_in_days"]) is not int:
            raise WorkbenchError("EXPIRY_OUT_OF_RANGE")
        _, board, request = self._publication_request(owner_id, data, expires_at=self.now() + timedelta(days=data["expires_in_days"]))
        card = views.notice_card(PublishedNotice("preview", 0, owner_id, board.board_id, request.payload, request.payload_digest,
                                                 "preview", request.brief_version, self.now(), request.expires_at), board)
        return views.preview_view(request, board, sorted(self._alias(o) for o in request.audience_member_ids), card)

    @transaction
    def approve_publication(self, owner_id: Any, data: Any) -> dict[str, Any]:
        """Explicit approval of one previewed payload; recomputed from current state."""
        owner_id = self._owner(owner_id)
        data = _fields(data, {"board_id", "expires_at", "include", "approval_digest"})
        try:
            expires_at = parse_time(data["expires_at"])
        except ValueError as exc:
            raise WorkbenchError("INVALID_REQUEST") from exc
        if not isinstance(data["approval_digest"], str):
            raise WorkbenchError("INVALID_REQUEST")
        brief, board, request = self._publication_request(owner_id, data, expires_at=expires_at)
        try:
            grant = approve_publication(request, presented_digest=data["approval_digest"], current_brief=brief,
                                        grant_id=self.store.next_id("grant"), now=self.now())
            authorize_publication(grant, payload=request.payload, owner_id=owner_id, board_id=board.board_id, now=self.now())
        except PublicationError as exc:
            raise WorkbenchError(exc.code if exc.code in MESSAGES else "APPROVAL_STALE") from exc
        previous = self.store.notice_for(owner_id, board.board_id)
        if previous is not None and previous.grant_id in self.store.grants:
            old = self.store.grants[previous.grant_id]
            self.store.grants[old.grant_id] = replace(old, revoked=True)
        notice = PublishedNotice(previous.notice_id if previous else self.store.next_id("notice"),
                                 previous.revision + 1 if previous else 1, owner_id, board.board_id, dict(request.payload),
                                 request.payload_digest, grant.grant_id, brief.version, self.now(), request.expires_at)
        self.store.grants[grant.grant_id] = grant
        self.store.notices[notice.notice_id] = notice
        self.index.upsert(index_document(notice_id=notice.notice_id, revision=notice.revision, board_id=notice.board_id,
                                         payload=notice.payload, published_at=notice.published_at))
        self.store.log(owner_id, self.now(), f"You approved and published your notice to {board.name} (revision {notice.revision}).")
        return self.workspace(owner_id)

    @transaction
    def withdraw_notice(self, owner_id: Any, data: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        data = _fields(data, {"notice_id"})
        notice = self.store.notices.get(_identifier(data["notice_id"]))
        if notice is None or notice.owner_id != owner_id:
            raise WorkbenchError("NOT_YOUR_NOTICE")
        self.store.notices[notice.notice_id] = replace(notice, state="withdrawn")
        grant = self.store.grants.get(notice.grant_id)
        if grant is not None:
            self.store.grants[grant.grant_id] = replace(grant, revoked=True)
        self.index.remove(notice.notice_id)
        self.store.log(owner_id, self.now(), f"You withdrew your notice from {self.store.boards[notice.board_id].name}.")
        for intro in self.store.introductions_for(owner_id):
            self._refresh(intro)
        return self.workspace(owner_id)

    # ----- discovery -------------------------------------------------------

    @transaction
    def search(self, owner_id: Any, data: Any) -> dict[str, Any]:
        """Bounded retrieval over boards the viewer belongs to, then local assessment.

        This is not the R0 full-population scan. Notices that were not retrieved
        were not assessed, so the result never claims to hold the best matches.
        """
        owner_id = self._owner(owner_id)
        boards = self.store.boards_for(owner_id)
        try:
            query = parse_query(data, board_ids=tuple(b.board_id for b in boards))
        except QueryError as exc:
            raise WorkbenchError("QUERY_INVALID", errors=exc.errors) from exc
        viewer = self._viewer_facts(owner_id)
        blocked = self._blocked_with(owner_id)
        shown = []
        for hit in self.index.query(query):
            notice = self.store.notices.get(hit.notice_id)
            # Re-check every hit: the index is a hint, the store is the authority.
            if notice is None or self._unavailable(owner_id, notice, hit.revision) is not None:
                continue
            candidate = self._notice_facts(notice)
            shown.append((notice, assess(viewer, candidate, as_of=self.now(), blocked_owner_ids=blocked)))
        groups = {"both_meet": [], "needs_information": [], "not_a_fit_now": []}
        for notice, pair in shown:
            groups[pair_status(pair)].append((notice, pair))
        by_owner = {notice.owner_id: (notice, pair) for notice, pair in groups["both_meet"]}
        ordered = [by_owner[p.a_to_b.candidate_id] for p in rank_qualified([pair for _, pair in groups["both_meet"]])]
        ordered += groups["needs_information"] + groups["not_a_fit_now"]
        results = [{"card": views.notice_card(n, self.store.boards[n.board_id]),
                    "assessment": {"pair_status": pair_status(p), "pair_label": PAIR_LABELS[pair_status(p)]}}
                   for n, p in ordered]
        self.store.log(owner_id, self.now(), f"You searched {len(boards)} board{'s' if len(boards) != 1 else ''} and saw {len(results)} notice{'s' if len(results) != 1 else ''}.")
        empty = None
        if not boards:
            empty = "not_a_member"
        elif not results:
            empty = "no_results"
        return {"results": results, "empty_reason": empty, "limit": query.limit,
                "coverage": {"boards_searched": [b.name for b in boards],
                             "statement": (f"Searched {', '.join(b.name for b in boards) or 'no boards'} for up to {query.limit} "
                                           "current notices matching your words and filters, then re-checked each one "
                                           "against current permissions. Notices that were not retrieved were not assessed, "
                                           "so this is not a list of the best possible matches."),
                             "order": ("Notices where both sides' stated preferences are met come first, ordered among the "
                                       "retrieved notices by an experimental policy quantity. The order is not a score of anyone.")}}

    @transaction
    def explain(self, owner_id: Any, data: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        data = _fields(data, {"notice_id", "revision"})
        notice = self._visible_notice(owner_id, data["notice_id"], data["revision"])
        viewer, candidate = self._viewer_facts(owner_id), self._notice_facts(notice)
        pair = assess(viewer, candidate, as_of=self.now(), blocked_owner_ids=self._blocked_with(owner_id))
        explanation = explain_pair(viewer, candidate, pair)
        blockers = self._proposal_blockers(owner_id, notice, pair_status(pair))
        brief = self.store.briefs[owner_id]
        return {"card": views.notice_card(notice, self.store.boards[notice.board_id]), "explanation": explanation,
                "information_request": self._information_options(owner_id, notice),
                "propose": {"allowed": not blockers, "blockers": blockers,
                            "default_first_step": views.option(v.FIRST_STEPS, brief.first_step)}}

    def _my_notice_for(self, owner_id: str, other_id: str) -> PublishedNotice | None:
        """One of my active notices that the other person may currently see."""
        for notice in self.store.notices_by_owner(owner_id):
            if self._unavailable(other_id, notice) is None:
                return notice
        return None

    def _proposal_blockers(self, owner_id: str, notice: PublishedNotice, status: str) -> list[str]:
        blockers = []
        if status != "both_meet":
            blockers.append("Both sides' stated preferences must be met before an introduction.")
        if self._my_notice_for(owner_id, notice.owner_id) is None:
            blockers.append("Publish your own notice on a board you share, so they can review who is proposing.")
        brief = self.store.briefs[owner_id]
        if self._active_introductions(owner_id) >= brief.capacity:
            blockers.append(f"Your introduction capacity ({brief.capacity}) is in use.")
        recipient_brief = self.store.briefs.get(notice.owner_id)
        if recipient_brief is None or self._active_introductions(notice.owner_id) >= recipient_brief.capacity:
            # Neither the limit nor the number/identity of other connections
            # is published. Expose only whether this action is available.
            blockers.append("This introduction is not available right now.")
        for intro in self.store.introductions_for(owner_id):
            if notice.owner_id in intro.participants and intro.status in {"pending", "open"}:
                blockers.append(f"You already have an introduction with {self._alias(notice.owner_id)}.")
                break
        return blockers

    # ----- introductions ---------------------------------------------------

    def _authority_ok(self, intro: Introduction) -> bool:
        proposer_notice = self.store.notices.get(intro.proposer_ref.notice_id)
        recipient_notice = self.store.notices.get(intro.recipient_ref.notice_id)
        return (self._unavailable(intro.recipient_id, proposer_notice, intro.proposer_ref.revision) is None
                and self._unavailable(intro.proposer_id, recipient_notice, intro.recipient_ref.revision) is None)

    def _refresh(self, intro: Introduction) -> None:
        expire_if_due(intro, self.now())
        if intro.status == "pending" and not self._authority_ok(intro):
            close(intro, "AUTHORITY_LOST")

    def _snapshot(self, intro: Introduction) -> None:
        proposer = self.store.notices[intro.proposer_ref.notice_id].payload
        recipient = self.store.notices[intro.recipient_ref.notice_id].payload
        context, proposal = build_proposal(intro, proposer, recipient)
        intro.context_snapshot, intro.proposal_digest = context, proposal.value_digest

    def _proposal(self, intro: Introduction):
        proposer = self.store.notices[intro.proposer_ref.notice_id].payload
        recipient = self.store.notices[intro.recipient_ref.notice_id].payload
        return build_proposal(intro, proposer, recipient)[1]

    def _intro(self, owner_id: Any, intro_id: Any) -> tuple[str, Introduction]:
        owner_id = self._owner(owner_id)
        intro = self.store.introductions.get(_identifier(intro_id))
        if intro is None or owner_id not in intro.participants:
            raise WorkbenchError("INTRODUCTION_UNAVAILABLE")
        self._refresh(intro)
        return owner_id, intro

    def _actions(self, intro: Introduction, owner_id: str) -> list[str]:
        if intro.status == "pending":
            return ["withdraw_acceptance" if owner_id in intro.acceptances else "accept", "change", "decline", "block"]
        if intro.status == "open":
            step = intro.first_step
            actions = [] if step.reports else ["propose_step"]
            state = step.state(intro.participants)
            if state == "proposed" and owner_id not in step.agreed_by:
                actions.append("agree_step")
            if state in {"agreed", "reported", "both_reported_happened"} and owner_id not in step.reports:
                actions.append("report_step")
            return actions + ["close", "block"]
        return []

    def _intro_view(self, intro: Introduction, owner_id: str) -> dict[str, Any]:
        self._refresh(intro)
        return views.introduction_view(intro, viewer_id=owner_id, aliases=self._aliases(), context=intro.context_snapshot,
                                       proposal_digest=intro.proposal_digest if intro.status == "pending" else None,
                                       actions=self._actions(intro, owner_id))

    @transaction
    def propose_introduction(self, owner_id: Any, data: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        data = _fields(data, {"notice_id", "revision", "first_step", "note"})
        notice = self._visible_notice(owner_id, data["notice_id"], data["revision"])
        if data["first_step"] not in v.FIRST_STEPS:
            raise WorkbenchError("INVALID_REQUEST")
        try:
            note = clean_note(data["note"])
        except IntroductionError as exc:
            raise WorkbenchError("INVALID_REQUEST") from exc
        pair = assess(self._viewer_facts(owner_id), self._notice_facts(notice), as_of=self.now(),
                      blocked_owner_ids=self._blocked_with(owner_id))
        blockers = self._proposal_blockers(owner_id, notice, pair_status(pair))
        if blockers:
            raise WorkbenchError("INTRODUCTION_NOT_ALLOWED", detail=" ".join(blockers))
        mine = self._my_notice_for(owner_id, notice.owner_id)
        assert mine is not None
        intro = Introduction(self.store.next_id("intro"), owner_id, notice.owner_id,
                             NoticeRef(mine.notice_id, mine.revision, owner_id, mine.expires_at),
                             NoticeRef(notice.notice_id, notice.revision, notice.owner_id, notice.expires_at),
                             self.now(), self.now() + introduction_ttl(), data["first_step"], note)
        self._snapshot(intro)
        self.store.introductions[intro.intro_id] = intro
        self._log_both(intro, owner_id, f"You proposed an introduction to {self._alias(notice.owner_id)}. Nothing is accepted yet.",
                       f"{self._alias(owner_id)} proposed an introduction to you.")
        return self.workspace(owner_id)

    def _intro_error(self, exc: IntroductionError) -> WorkbenchError:
        code = exc.code.split(":", 1)[0]
        return WorkbenchError(code if code in MESSAGES else "INTRODUCTION_NOT_PENDING")

    @transaction
    def accept(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        data = _fields(data, {"proposal_digest"})
        if intro.status != "pending":
            raise WorkbenchError("INTRODUCTION_NOT_PENDING")
        if self._active_introductions(owner_id, excluding=intro.intro_id) >= self.store.briefs[owner_id].capacity:
            raise WorkbenchError("CAPACITY_FULL")
        try:
            opened = accept_intro(intro, owner_id, presented_digest=data["proposal_digest"], proposal=self._proposal(intro),
                                  now=self.now())
        except IntroductionError as exc:
            raise self._intro_error(exc) from exc
        other = self._alias(intro.other(owner_id))
        self._log_both(intro, owner_id, f"You accepted the introduction with {other} (version {intro.context_version}).",
                       f"{self._alias(owner_id)} accepted the introduction (version {intro.context_version}).")
        if opened:
            for participant in intro.participants:
                self.store.log(participant, self.now(), "Both people accepted the same context. A simulated channel opened; no messages were sent.")
        return self.workspace(owner_id)

    @transaction
    def withdraw_acceptance(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        _fields(data, set())
        try:
            withdraw_acceptance(intro, owner_id, self.now())
        except IntroductionError as exc:
            raise self._intro_error(exc) from exc
        self._log_both(intro, owner_id, "You withdrew your acceptance.", f"{self._alias(owner_id)} withdrew their acceptance.")
        return self.workspace(owner_id)

    @transaction
    def decline(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        _fields(data, set())
        try:
            decline(intro, owner_id, self.now())
        except IntroductionError as exc:
            raise self._intro_error(exc) from exc
        self._log_both(intro, owner_id, "You declined the introduction.", f"{self._alias(owner_id)} declined the introduction.")
        return self.workspace(owner_id)

    @transaction
    def change(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        data = _fields(data, {"first_step", "note"})
        if not isinstance(data["first_step"], str):
            raise WorkbenchError("INVALID_REQUEST")
        try:
            cleared = change_context(intro, owner_id, suggested_first_step=data["first_step"], note=data["note"], now=self.now())
        except IntroductionError as exc:
            raise WorkbenchError("INVALID_REQUEST" if exc.code in {"UNKNOWN_FIRST_STEP", "INVALID_TEXT"} else exc.code
                                 if exc.code in MESSAGES else "INTRODUCTION_NOT_PENDING") from exc
        self._snapshot(intro)
        suffix = " Earlier acceptances were cleared." if cleared else ""
        self._log_both(intro, owner_id, f"You changed the introduction (now version {intro.context_version}).{suffix}",
                       f"{self._alias(owner_id)} changed the introduction (now version {intro.context_version}).{suffix}")
        return self.workspace(owner_id)

    def _block(self, owner_id: str, other_id: str) -> None:
        self.store.blocked_pairs.add((owner_id, other_id))
        for intro in self.store.introductions_for(owner_id):
            if other_id in intro.participants:
                block_intro(intro, owner_id, self.now())
        self.store.log(owner_id, self.now(), f"You blocked {self._alias(other_id)}. Their notices are hidden and any introduction is closed.")

    @transaction
    def block_introduction(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        _fields(data, set())
        self._block(owner_id, intro.other(owner_id))
        return self.workspace(owner_id)

    @transaction
    def block_notice(self, owner_id: Any, data: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        data = _fields(data, {"notice_id", "revision"})
        notice = self._visible_notice(owner_id, data["notice_id"], data["revision"])
        self._block(owner_id, notice.owner_id)
        return self.workspace(owner_id)

    @transaction
    def close_introduction(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        data = _fields(data, {"context_version"})
        try:
            changed = close_connection(intro, owner_id, context_version=_revision(data["context_version"]))
        except IntroductionError as exc:
            raise self._intro_error(exc) from exc
        if changed:
            self._log_both(intro, owner_id, "You closed the connection. Its outcome reports are retained.",
                           f"{self._alias(owner_id)} closed the connection. Its outcome reports are retained.")
        return self.workspace(owner_id)

    # ----- bounded information requests -----------------------------------

    def _refresh_information(self, request: InformationRequest) -> None:
        if request.state == "unavailable":
            return
        if request.accounted_at is None:
            request.accounted_at = self.information_now()
        if self.information_now() >= request.accounted_at + timedelta(days=REQUEST_DAYS):
            request.state = "unavailable"
            return
        requester_notice = self.store.notices.get(request.requester_ref.notice_id)
        target = self.store.notices.get(request.recipient_ref.notice_id)
        if (self.now() >= request.expires_at or
                self._unavailable(request.recipient_id, requester_notice, request.requester_ref.revision) is not None):
            request.state = "unavailable"
            return
        target_reason = self._unavailable(request.requester_id, target)
        # The recipient must be able to edit their answer privately before
        # explicitly approving it. Only that temporary draft suspension is
        # allowed; withdrawal, scope loss, expiry and blocks still stop it.
        editing = request.state == "pending" and target_reason == "SOURCE_BRIEF_CHANGED"
        if editing and target is not None:
            # Ignore only the private draft version mismatch, never later
            # digest/scope checks in the normal visibility function. A stale
            # notice tampered with during editing must still fail closed.
            editing = notice_unavailable_reason(
                target, self.store.grants.get(target.grant_id), viewer_id=request.requester_id,
                board=self.store.boards.get(target.board_id), blocked_pairs=frozenset(self.store.blocked_pairs),
                current_brief_version=target.brief_version, now=self.now()) is None
        if target_reason is not None and not editing:
            request.state = "unavailable"
            return
        if target is None or target.revision < request.recipient_ref.revision:
            request.state = "unavailable"
            return
        if request.state == "answered" and request.field in missing_fields(target.payload):
            request.state = "unavailable"
        if (request.state == "pending" and target_reason is None and target.revision > request.recipient_ref.revision
                and request.field not in missing_fields(target.payload)):
            request.state = "answered"

    def _information_options(self, owner_id: str, notice: PublishedNotice) -> dict[str, Any]:
        mine = self._my_notice_for(owner_id, notice.owner_id)
        fields = missing_fields(notice.payload)
        requests = list(self.store.information_requests.values())
        for request in requests:
            self._refresh_information(request)
        sent = [r for r in requests if r.requester_id == owner_id]
        received = [r for r in requests if r.recipient_id == notice.owner_id]
        requested = {r.field for r in sent if r.recipient_id == notice.owner_id
                     and r.recipient_ref.notice_id == notice.notice_id
                     and r.recipient_ref.revision == notice.revision}
        available = [field for field in fields if field not in requested]
        blockers = []
        if mine is None:
            blockers.append("Publish a notice this owner can see before asking for information.")
        if not available:
            blockers.append("No unrequested missing fields are available on this notice.")
        cutoff = self.information_now() - timedelta(seconds=REQUEST_WINDOW_SECONDS)
        recent_sent = [r for r in sent if r.accounted_at is not None and r.accounted_at > cutoff]
        recent_received = [r for r in received if r.accounted_at is not None and r.accounted_at > cutoff]
        if len(recent_sent) >= MAX_WINDOW_REQUESTS or sum(r.state == "pending" for r in sent) >= MAX_PENDING_REQUESTS:
            blockers.append(MESSAGES["INFORMATION_LIMIT"])
        if len(recent_received) >= MAX_WINDOW_REQUESTS or sum(r.state == "pending" for r in received) >= MAX_PENDING_REQUESTS:
            blockers.append(MESSAGES["INFORMATION_NOT_ALLOWED"])
        alias = mine.payload["publisher_alias"] if mine else "Your approved alias"
        return {"allowed": not blockers, "blockers": blockers,
                "fields": [views.option(INFORMATION_FIELDS, field) for field in available],
                "requester_notice_id": mine.notice_id if mine else None,
                "requester_revision": mine.revision if mine else None,
                "disclosure": (f"This will tell {notice.payload['publisher_alias']} that {alias} is asking about the selected "
                               "missing field. They can already see your approved notice. No private brief, note or "
                               "introduction acceptance is shared. They may decline or choose what to publish.")}

    def _information_view(self, request: InformationRequest, owner_id: str) -> dict[str, Any]:
        self._refresh_information(request)
        incoming = owner_id == request.recipient_id
        counterpart_ref = request.requester_ref if incoming else request.recipient_ref
        counterpart = self.store.notices.get(counterpart_ref.notice_id)
        alias = counterpart.payload["publisher_alias"] if counterpart is not None else None
        target = self.store.notices.get(request.recipient_ref.notice_id)
        # Never hand an outbound request a reference to unpublished changes.
        visible_target = target if target is not None and self._unavailable(request.requester_id, target) is None else None
        actions = []
        if incoming and request.state == "pending":
            actions = ["review_brief", "decline"]
        elif not incoming and request.state == "answered" and visible_target is not None:
            actions = ["reassess"]
        return views.information_request_view(request, viewer_id=owner_id, counterpart_alias=alias,
                                              notice=target if incoming else visible_target, actions=actions)

    @transaction
    def request_information(self, owner_id: Any, data: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        data = _fields(data, {"notice_id", "revision", "field", "requester_notice_id", "requester_revision"})
        notice = self._visible_notice(owner_id, data["notice_id"], data["revision"])
        field = data["field"]
        if not isinstance(field, str) or field not in INFORMATION_FIELDS or field not in missing_fields(notice.payload):
            raise WorkbenchError("INFORMATION_NOT_ALLOWED")
        mine = self._visible_notice(notice.owner_id, data["requester_notice_id"], data["requester_revision"])
        if mine.owner_id != owner_id:
            raise WorkbenchError("INFORMATION_NOT_ALLOWED")
        # Identical retries do not consume a second budget slot or send twice.
        for existing in self.store.information_requests.values():
            if (existing.requester_id == owner_id and existing.recipient_ref.notice_id == notice.notice_id
                    and existing.recipient_ref.revision == notice.revision and existing.field == field):
                self._refresh_information(existing)
                if (existing.state == "pending" and existing.requester_ref.notice_id == mine.notice_id
                        and existing.requester_ref.revision == mine.revision):
                    return self.workspace(owner_id)
                raise WorkbenchError("INFORMATION_NOT_ALLOWED")
        options = self._information_options(owner_id, notice)
        if not options["allowed"]:
            raise WorkbenchError("INFORMATION_NOT_ALLOWED")
        request = InformationRequest(self.store.next_id("information"), owner_id, notice.owner_id,
                                     NoticeRef(mine.notice_id, mine.revision, owner_id, mine.expires_at),
                                     NoticeRef(notice.notice_id, notice.revision, notice.owner_id, notice.expires_at),
                                     field, self.now(), min(self.now() + timedelta(days=REQUEST_DAYS),
                                                            mine.expires_at, notice.expires_at),
                                     accounted_at=self.information_now())
        self.store.information_requests[request.request_id] = request
        self.store.log(owner_id, self.now(), "You sent an information request about an approved notice.")
        self.store.log(notice.owner_id, self.now(), "You received an information request. Review it before deciding what to publish.")
        return self.workspace(owner_id)

    @transaction
    def decline_information(self, owner_id: Any, request_id: Any, data: Any) -> dict[str, Any]:
        owner_id = self._owner(owner_id)
        _fields(data, set())
        request = self.store.information_requests.get(_identifier(request_id))
        if request is None or request.recipient_id != owner_id:
            raise WorkbenchError("INFORMATION_UNAVAILABLE")
        self._refresh_information(request)
        if request.state == "declined":
            return self.workspace(owner_id)
        if request.state != "pending":
            raise WorkbenchError("INFORMATION_UNAVAILABLE")
        request.state = "declined"
        self.store.log(owner_id, self.now(), "You declined an information request. Nothing new was published.")
        return self.workspace(owner_id)

    # ----- first step ------------------------------------------------------

    @transaction
    def propose_step(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        data = _fields(data, {"action", "detail"})
        try:
            propose_step(intro, owner_id, action=data["action"], detail=data["detail"])
        except IntroductionError as exc:
            raise WorkbenchError(exc.code if exc.code in MESSAGES else "INVALID_REQUEST") from exc
        label = v.FIRST_STEPS[intro.first_step.action or ""]
        self._log_both(intro, owner_id, f"You proposed a first step: {label}. It is not agreed yet.",
                       f"{self._alias(owner_id)} proposed a first step: {label}.")
        return self.workspace(owner_id)

    @transaction
    def agree_step(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        data = _fields(data, {"version"})
        try:
            agree_step(intro, owner_id, version=_revision(data["version"]))
        except IntroductionError as exc:
            raise WorkbenchError(exc.code if exc.code in MESSAGES else "INVALID_REQUEST") from exc
        self._log_both(intro, owner_id, "You agreed the proposed first step. It has not happened yet.",
                       f"{self._alias(owner_id)} agreed the proposed first step.")
        return self.workspace(owner_id)

    @transaction
    def report_step(self, owner_id: Any, intro_id: Any, data: Any) -> dict[str, Any]:
        owner_id, intro = self._intro(owner_id, intro_id)
        data = _fields(data, {"version", "outcome"})
        try:
            report_step(intro, owner_id, version=_revision(data["version"]), outcome=data["outcome"])
        except IntroductionError as exc:
            raise WorkbenchError(exc.code if exc.code in MESSAGES else "INVALID_REQUEST") from exc
        text = "happened" if data["outcome"] == "happened" else "did not happen"
        self._log_both(intro, owner_id, f"You reported that the first step {text}.",
                       f"{self._alias(owner_id)} reported that the first step {text}.")
        return self.workspace(owner_id)
