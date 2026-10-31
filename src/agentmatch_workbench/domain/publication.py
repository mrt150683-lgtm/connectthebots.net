"""Exact-payload board publication: preview, approval, grant and notice.

Invariants (each has a test in ``tests/test_workbench_publication.py``):

1. A notice payload is built only by ``project_notice``, an allowlist copy of
   brief fields. ``private_notes`` and unselected optional sections can never
   appear in it, whatever the brief contains.
2. The owner approves an ``approval_digest`` that covers the exact payload,
   board, fixed audience snapshot, purpose, expiry and brief version. Approval
   recomputes the request from the *current* brief; any edit changes the
   version (and usually the payload), so a stale preview cannot be approved.
3. Publication needs a ``PublicationGrant``. It is a distinct type from the R0
   ``consent.DisclosureGrant`` and ``topics.DisplayGrant``: a peer-disclosure
   grant names one peer and one claim, so it cannot authorize putting content
   on a board. ``authorize_publication`` rejects any other grant type.
4. Audiences are fixed membership snapshots. Members admitted later are not in
   the audience; a removed member is excluded by the visibility check.

Digests here are local consistency identifiers. They do not authenticate the
owner, the board or the content to anyone else.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Any, Mapping

from agentmatch.models import checked_time, time_text
from agentmatch.numeric import canonical_json
from .briefs import Brief

NOTICE_SCHEMA = "workbench-notice-0.1"
PURPOSE = "collaboration"
OPTIONAL_SECTIONS = ("working_arrangement", "feedback", "schedule", "commercial_note")
MAX_PUBLICATION_DAYS = 30


class PublicationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def sha256_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def project_notice(brief: Brief, *, publisher_alias: str, include: tuple[str, ...]) -> dict[str, Any]:
    """Allowlisted outward projection of a brief. New brief fields stay private by default."""
    unknown = set(include) - set(OPTIONAL_SECTIONS)
    if unknown or len(set(include)) != len(include):
        raise PublicationError("UNKNOWN_NOTICE_SECTION")
    payload: dict[str, Any] = {
        "schema": NOTICE_SCHEMA, "purpose": PURPOSE, "publisher_alias": publisher_alias,
        "mode": brief.mode, "title": brief.title, "desired_outcome": brief.desired_outcome, "stage": brief.stage,
        "focus_areas": list(brief.focus_areas), "offers": list(brief.offers), "offers_complete": brief.offers_complete,
        "primary_need": brief.primary_need, "also_seeks": list(brief.also_seeks),
        "availability": brief.availability, "availability_required": brief.availability_required,
        "basis": brief.basis, "accepted_bases": list(brief.accepted_bases), "basis_required": brief.basis_required,
        "first_step": brief.first_step,
    }
    if "working_arrangement" in include:
        payload["working_arrangement"] = {"venue": brief.venue, "accepted_venues": list(brief.accepted_venues),
                                          "rhythm": brief.rhythm, "accepted_rhythms": list(brief.accepted_rhythms)}
    if "feedback" in include:
        payload["feedback"] = {"style": brief.feedback_style, "accepted": list(brief.accepted_feedback)}
    if "schedule" in include:
        payload["schedule"] = {"hours_per_week": brief.hours_per_week, "start_window": brief.start_window}
    if "commercial_note" in include and brief.commercial_note:
        payload["commercial_note"] = brief.commercial_note
    return payload


@dataclass(frozen=True)
class PublicationRequest:
    """What the owner is shown before approving. Nothing is published yet."""
    owner_id: str
    brief_id: str
    brief_version: int
    board_id: str
    audience_member_ids: tuple[str, ...]
    purpose: str
    expires_at: datetime
    include: tuple[str, ...]
    payload: Mapping[str, Any]
    payload_digest: str
    approval_digest: str


def prepare_publication(brief: Brief, *, publisher_alias: str, board_id: str, board_member_ids: tuple[str, ...],
                        include: tuple[str, ...], expires_at: datetime, now: datetime) -> PublicationRequest:
    now, expires_at = checked_time(now), checked_time(expires_at)
    if not now < expires_at <= now + timedelta(days=MAX_PUBLICATION_DAYS):
        raise PublicationError("EXPIRY_OUT_OF_RANGE")
    if brief.owner_id not in board_member_ids:
        raise PublicationError("NOT_A_BOARD_MEMBER")
    if set(include) - set(OPTIONAL_SECTIONS) or len(set(include)) != len(include):
        raise PublicationError("UNKNOWN_NOTICE_SECTION")
    include = tuple(section for section in OPTIONAL_SECTIONS if section in include)  # canonical order
    payload = project_notice(brief, publisher_alias=publisher_alias, include=include)
    audience = tuple(sorted(member for member in board_member_ids if member != brief.owner_id))
    payload_digest = sha256_digest(payload)
    approval_digest = sha256_digest({
        "kind": "exact_payload_board_publication", "owner_id": brief.owner_id, "brief_id": brief.brief_id,
        "brief_version": brief.version, "board_id": board_id, "audience_member_ids": list(audience),
        "purpose": PURPOSE, "expires_at": time_text(expires_at), "payload_digest": payload_digest})
    return PublicationRequest(brief.owner_id, brief.brief_id, brief.version, board_id, audience, PURPOSE,
                              expires_at, include, payload, payload_digest, approval_digest)


@dataclass(frozen=True)
class PublicationGrant:
    """Board publication permission. Separate from every peer-disclosure grant."""
    grant_id: str
    owner_id: str
    board_id: str
    audience_member_ids: tuple[str, ...]
    purpose: str
    brief_id: str
    brief_version: int
    payload_digest: str
    approval_digest: str
    approved_at: datetime
    expires_at: datetime
    kind: str = "exact_payload"
    revoked: bool = False


def approve_publication(request: PublicationRequest, *, presented_digest: str, current_brief: Brief,
                        grant_id: str, now: datetime) -> PublicationGrant:
    """Turn an explicit owner approval of one exact preview into a grant.

    ``request`` must be rebuilt from the current brief by the caller. If the
    owner edited the brief after previewing, the presented digest no longer
    matches and approval fails: the edit invalidated the approval.
    """
    now = checked_time(now)
    if current_brief.version != request.brief_version or current_brief.brief_id != request.brief_id:
        raise PublicationError("APPROVAL_STALE")
    if presented_digest != request.approval_digest:
        raise PublicationError("APPROVAL_STALE")
    if not now < request.expires_at:
        raise PublicationError("EXPIRY_OUT_OF_RANGE")
    return PublicationGrant(grant_id, request.owner_id, request.board_id, request.audience_member_ids, request.purpose,
                            request.brief_id, request.brief_version, request.payload_digest, request.approval_digest,
                            now, request.expires_at)


@dataclass(frozen=True)
class PublishedNotice:
    """Board record. Holds only the approved payload, never brief content."""
    notice_id: str
    revision: int
    owner_id: str
    board_id: str
    payload: Mapping[str, Any]
    payload_digest: str
    grant_id: str
    brief_version: int
    published_at: datetime
    expires_at: datetime
    state: str = "active"  # active | withdrawn


def authorize_publication(grant: Any, *, payload: Mapping[str, Any], owner_id: str, board_id: str,
                          now: datetime) -> None:
    """Fail closed unless this exact payload is covered by a current board grant."""
    if not isinstance(grant, PublicationGrant) or grant.kind != "exact_payload":
        # Includes consent.DisclosureGrant and topics.DisplayGrant: wrong contract.
        raise PublicationError("PUBLICATION_GRANT_REQUIRED")
    now = checked_time(now)
    if grant.revoked:
        raise PublicationError("PUBLICATION_REVOKED")
    if not grant.approved_at <= now < grant.expires_at:
        raise PublicationError("PUBLICATION_GRANT_INACTIVE")
    if grant.owner_id != owner_id or grant.board_id != board_id or grant.purpose != PURPOSE:
        raise PublicationError("PUBLICATION_SCOPE_MISMATCH")
    if sha256_digest(dict(payload)) != grant.payload_digest:
        raise PublicationError("PAYLOAD_NOT_APPROVED")


def publication_preview_record(request: PublicationRequest) -> dict[str, Any]:
    return {"board_id": request.board_id, "purpose": request.purpose, "expires_at": time_text(request.expires_at),
            "include": list(request.include), "brief_version": request.brief_version,
            "payload": dict(request.payload), "payload_digest": request.payload_digest,
            "approval_digest": request.approval_digest}
