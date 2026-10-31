"""Who may see or use a published notice, decided from authoritative state.

``notice_unavailable_reason`` is the single gate for every read *and* every use
of a notice: search results, explanations, introduction proposals,
acceptances and channel opening all call it with the current clock. Search
indexes and browser cards are treated as possibly stale hints, never as
authority. If any input is missing or ambiguous the answer is "unavailable".

The detailed reason is for logs and tests. Callers that answer the viewer map
every reason to one code (``NOTICE_UNAVAILABLE``) so that a viewer cannot learn
whether a notice was withdrawn, expired, blocked or simply outside their
audience.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentmatch.models import checked_time
from .publication import PURPOSE, PublicationGrant, PublishedNotice, sha256_digest


@dataclass(frozen=True)
class OwnerProfile:
    """A fictional owner. ``alias`` is the only identity shown to others."""
    owner_id: str
    alias: str


@dataclass(frozen=True)
class Board:
    board_id: str
    name: str
    description: str
    member_ids: tuple[str, ...]


def notice_unavailable_reason(notice: PublishedNotice | None, grant: PublicationGrant | None, *, viewer_id: str,
                              board: Board | None, blocked_pairs: frozenset[tuple[str, str]],
                              current_brief_version: int | None, now: datetime,
                              expected_revision: int | None = None) -> str | None:
    """Return why ``viewer_id`` may not see or act on ``notice`` now, or ``None``."""
    now = checked_time(now)
    if notice is None:
        return "NOTICE_MISSING"
    if expected_revision is not None and expected_revision != notice.revision:
        return "NOTICE_REVISION_CHANGED"
    if notice.owner_id == viewer_id:
        return "OWN_NOTICE"
    if notice.state != "active":
        return "NOTICE_WITHDRAWN"
    if grant is None or grant.grant_id != notice.grant_id or grant.board_id != notice.board_id:
        return "NO_PUBLICATION_GRANT"
    if grant.revoked:
        return "PUBLICATION_REVOKED"
    if not (grant.approved_at <= now < grant.expires_at and notice.published_at <= now < notice.expires_at):
        return "NOTICE_EXPIRED"
    if grant.purpose != PURPOSE or notice.payload.get("purpose") != PURPOSE:
        return "PURPOSE_MISMATCH"
    if board is None or board.board_id != notice.board_id:
        return "BOARD_MISSING"
    # Fixed audience: in the approved snapshot AND still a member now.
    if viewer_id not in grant.audience_member_ids or viewer_id not in board.member_ids:
        return "OUTSIDE_AUDIENCE"
    if notice.owner_id not in board.member_ids:
        return "PUBLISHER_NOT_MEMBER"
    if (viewer_id, notice.owner_id) in blocked_pairs or (notice.owner_id, viewer_id) in blocked_pairs:
        return "BLOCKED"
    # Editing the brief suspends the live notice until the new version is approved.
    if current_brief_version is None or current_brief_version != grant.brief_version or notice.brief_version != grant.brief_version:
        return "SOURCE_BRIEF_CHANGED"
    if sha256_digest(dict(notice.payload)) != grant.payload_digest or notice.payload_digest != grant.payload_digest:
        return "PAYLOAD_NOT_APPROVED"
    return None


def own_notice_status(notice: PublishedNotice, grant: PublicationGrant | None, *, current_brief_version: int,
                      now: datetime) -> str:
    """Status shown to the publisher about their own notice."""
    now = checked_time(now)
    if notice.state == "withdrawn" or grant is None or grant.revoked:
        return "withdrawn"
    if not now < min(notice.expires_at, grant.expires_at):
        return "expired"
    if current_brief_version != notice.brief_version:
        return "suspended_brief_changed"
    return "active"
