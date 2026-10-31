"""Allowlisted view records: the only shapes that reach the browser.

Every function here builds a fresh dict with named keys. None of them
serializes a domain object wholesale, so a new private field added to a brief,
grant, claim or R0 assessment cannot leak by default. The TypeScript contracts
in ``workbench/ui/src/contracts.ts`` mirror these shapes.

Privacy boundary: ``brief_view`` and ``own_notice_view`` are only returned to
the owner's own perspective. Anything describing another owner is built from
their approved notice payload (``notice_card``) or from the shared
introduction context, which is itself derived from approved payloads.
"""
from __future__ import annotations

from typing import Any, Mapping

from agentmatch.models import time_text
from ..domain import vocabulary as v
from ..domain.briefs import Brief
from ..domain.introductions import FirstStep, Introduction, channel_summary
from ..domain.information import INFORMATION_FIELDS, InformationRequest
from ..domain.publication import PublicationRequest, PublishedNotice
from ..domain.visibility import Board

UNTRUSTED_NOTE = "Written by the notice owner. Shown as plain text; it cannot instruct the workbench."


def option(registry: Mapping[str, str], key: str | None) -> dict[str, str] | None:
    return None if key is None else {"id": key, "label": registry.get(key, key)}


def options(registry: Mapping[str, str], keys: Any) -> list[dict[str, str]]:
    return [{"id": key, "label": registry.get(key, key)} for key in keys]


def board_view(board: Board, *, viewer_id: str) -> dict[str, Any]:
    return {"board_id": board.board_id, "name": board.name, "description": board.description,
            "member_count": len(board.member_ids), "is_member": viewer_id in board.member_ids}


def brief_view(brief: Brief) -> dict[str, Any]:
    """Owner-only. Includes private notes because it is the owner's own record."""
    return brief.to_private_dict()


def notice_card(notice: PublishedNotice, board: Board) -> dict[str, Any]:
    """What another member sees: the approved payload, labelled, nothing else."""
    p = notice.payload
    card: dict[str, Any] = {
        "notice_id": notice.notice_id, "revision": notice.revision,
        "board": {"board_id": board.board_id, "name": board.name},
        "publisher_alias": p["publisher_alias"], "mode": option(v.MODES, p["mode"]),
        "title": p["title"], "desired_outcome": p["desired_outcome"], "stage": option(v.STAGES, p["stage"]),
        "focus_areas": options(v.FOCUS_AREAS, p["focus_areas"]), "offers": options(v.CAPABILITIES, p["offers"]),
        "offers_complete": p["offers_complete"], "primary_need": option(v.CAPABILITIES, p["primary_need"]),
        "also_seeks": options(v.CAPABILITIES, p["also_seeks"]),
        "availability": option(v.AVAILABILITY, p["availability"]), "availability_required": p["availability_required"],
        "basis": option(v.BASES, p["basis"]), "accepted_bases": options(v.BASES, p["accepted_bases"]),
        "basis_required": p["basis_required"], "first_step": option(v.FIRST_STEPS, p["first_step"]),
        "working_arrangement": None, "feedback": None, "schedule": None, "commercial_note": None,
        "published_at": time_text(notice.published_at), "expires_at": time_text(notice.expires_at),
        "provenance": "The owner's own statements, approved for this board. Not verified.",
        "untrusted_text_note": UNTRUSTED_NOTE,
    }
    if "working_arrangement" in p:
        w = p["working_arrangement"]
        card["working_arrangement"] = {"venue": option(v.VENUES, w["venue"]), "accepted_venues": options(v.VENUES, w["accepted_venues"]),
                                       "rhythm": option(v.RHYTHMS, w["rhythm"]), "accepted_rhythms": options(v.RHYTHMS, w["accepted_rhythms"])}
    if "feedback" in p:
        card["feedback"] = {"style": option(v.FEEDBACK_STYLES, p["feedback"]["style"]),
                            "accepted": options(v.FEEDBACK_STYLES, p["feedback"]["accepted"])}
    if "schedule" in p:
        card["schedule"] = {"hours_per_week": p["schedule"]["hours_per_week"],
                            "start_window": option(v.START_WINDOWS, p["schedule"]["start_window"])}
    if "commercial_note" in p:
        card["commercial_note"] = p["commercial_note"]
    return card


OWN_STATUS_LABELS = {
    "active": "Published and discoverable by the approved audience",
    "suspended_brief_changed": "Paused: your brief changed after approval. Review and approve the new version to republish.",
    "expired": "Expired. It is no longer discoverable.",
    "withdrawn": "Withdrawn. It is no longer discoverable.",
}


def own_notice_view(notice: PublishedNotice, board: Board, status: str, audience_aliases: list[str]) -> dict[str, Any]:
    return {"notice_id": notice.notice_id, "revision": notice.revision, "status": status,
            "status_label": OWN_STATUS_LABELS[status], "brief_version": notice.brief_version,
            "card": notice_card(notice, board), "audience_aliases": audience_aliases,
            "payload_digest": notice.payload_digest}


def preview_view(request: PublicationRequest, board: Board, audience_aliases: list[str], card: dict[str, Any]) -> dict[str, Any]:
    return {"board": {"board_id": board.board_id, "name": board.name}, "purpose": request.purpose,
            "purpose_label": "Project and business collaboration",
            "audience": {"kind": "fixed_membership_snapshot", "aliases": audience_aliases,
                         "explanation": ("Only the members listed here can find this notice. People who join the board later "
                                         "are not included. Members can keep what they have already read.")},
            "expires_at": time_text(request.expires_at), "brief_version": request.brief_version,
            "include": list(request.include), "payload": dict(request.payload), "card": card,
            "approval_digest": request.approval_digest,
            "not_included": ["Your private notes", "Your selectivity setting", "Your introduction capacity",
                             *[label for section, label in (("working_arrangement", "Working arrangement"), ("feedback", "Feedback style"),
                                                            ("schedule", "Hours and start window"), ("commercial_note", "Commercial note"))
                               if section not in request.include]]}


STEP_LABELS = {
    "none": "No first step proposed yet.",
    "proposed": "Proposed, waiting for the other person to agree. Nothing is agreed yet.",
    "agreed": "Agreed by both people. Not yet reported as done.",
    "reported": "Agreed. Outcome reports so far are shown below; a missing report stays missing.",
    "both_reported_happened": ("Both people reported that the step happened. That records an action, "
                               "not that the collaboration was useful."),
}


def first_step_view(step: FirstStep, intro: Introduction, aliases: Mapping[str, str], viewer_id: str) -> dict[str, Any]:
    state = step.state(intro.participants)
    return {"state": state, "state_label": STEP_LABELS[state], "version": step.version,
            "action": option(v.FIRST_STEPS, step.action), "detail": step.detail or None,
            "proposed_by_alias": aliases[step.proposed_by] if step.proposed_by else None,
            "agreed_by_aliases": sorted(aliases[o] for o in step.agreed_by),
            "i_agreed": viewer_id in step.agreed_by,
            "reports": [{"alias": aliases[o], "outcome": step.reports.get(o)} for o in intro.participants]}


INTRO_STATUS_LABELS = {
    "pending": "Waiting for both people to accept the same context",
    "open": "Both accepted. A simulated channel is open.",
    "declined": "Declined. No channel was opened.",
    "expired": "Expired before both people accepted.",
    "closed": "No longer available.",
}


def introduction_view(intro: Introduction, *, viewer_id: str, aliases: Mapping[str, str], context: dict[str, Any] | None,
                      proposal_digest: str | None, actions: list[str]) -> dict[str, Any]:
    other = intro.other(viewer_id)
    # A blocked or withdrawn party is not told why; only the blocker sees the reason.
    reason = "You blocked this person." if intro.status == "closed" and intro.blocked_by == viewer_id else None
    normally_closed = intro.status == "closed" and intro.closed_reason == "OWNER_CLOSED"
    if normally_closed:
        reason = "Connection closed by a participant. Outcome reports are retained."
    return {"intro_id": intro.intro_id, "status": intro.status,
            "status_label": "Connection closed" if normally_closed else INTRO_STATUS_LABELS[intro.status],
            "status_detail": reason, "direction": "outgoing" if intro.proposer_id == viewer_id else "incoming",
            "counterpart_alias": aliases[other], "context_version": intro.context_version, "context": context,
            "proposal_digest": proposal_digest, "created_at": time_text(intro.created_at),
            "expires_at": time_text(intro.expires_at),
            "acceptances": [{"alias": aliases[o], "accepted": o in intro.acceptances, "is_you": o == viewer_id}
                            for o in intro.participants],
            "acceptance_cleared_by_change": viewer_id in intro.invalidated_acceptances and viewer_id not in intro.acceptances
            and intro.status == "pending",
            "channel": channel_summary(intro),
            "first_step": first_step_view(intro.first_step, intro, aliases, viewer_id) if intro.status == "open" or normally_closed else None,
            "actions": actions,
            "reminder": "Accepting opens a simulated channel only. It is not an agreement to work together."}


def information_request_view(request: InformationRequest, *, viewer_id: str, counterpart_alias: str | None,
                             notice: PublishedNotice | None, actions: list[str]) -> dict[str, Any]:
    """Return no notice data or identity after the request loses authority."""
    unavailable = request.state == "unavailable"
    labels = {
        "pending": "Waiting for the owner to review and approve an updated notice.",
        "answered": "An approved notice now states this field. Reassess it before deciding.",
        "declined": "The owner declined this information request.",
        "unavailable": "This information request is no longer available.",
    }
    return {"request_id": request.request_id,
            "direction": "outgoing" if viewer_id == request.requester_id else "incoming",
            "counterpart_alias": None if unavailable else counterpart_alias,
            "field": None if unavailable else option(INFORMATION_FIELDS, request.field),
            "status": request.state, "status_label": labels[request.state],
            "created_at": time_text(request.created_at), "expires_at": time_text(request.expires_at),
            "notice_id": notice.notice_id if notice is not None and not unavailable else None,
            "revision": notice.revision if notice is not None and not unavailable else None,
            "actions": [] if unavailable else actions}

