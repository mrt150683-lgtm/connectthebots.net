"""Introductions and first steps, built on the R0 topic and channel state machines.

The R0 modules do the consent work; this module only assembles their inputs
from published notices and keeps the workbench-level state around them.

- ``topics.project_display`` / ``grounded_bridges`` build the shared context
  from aliases and focus areas that *both* notices published.
- ``channels.acceptance_for`` records one explicit owner acceptance, bound to
  the exact proposal digest, version and kind.
- ``channels.open_channel`` refuses anything but two current acceptances of the
  same proposal digest. The workbench never calls it with fewer.

Workbench additions:

- The shared context also carries "what could we achieve", each person's
  contribution and gain, open questions and the suggested first step. All of it
  is derived from the two published notices, never from either private brief.
  It is folded into the R0 context id through a digest, so changing any of it
  changes the R0 proposal digest and invalidates earlier acceptances.
- ``change_context`` bumps the version and clears both acceptances.
- Decline, expiry, block and loss of authority are terminal for a pending
  introduction. A new introduction is needed afterwards.
- A first step is *proposed*, then *agreed* by both, then each person may
  *report* what happened. Accepting an introduction, agreeing a step and
  reporting it are separate records; none implies collaboration was useful.

Nothing here sends a message, and no state changes without an explicit call
that represents one owner's own action in their own perspective.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping

from agentmatch.channels import Acceptance, ChannelProposal, HumanChannel, acceptance_for, open_channel
from agentmatch.config import load_defaults
from agentmatch.models import checked_time, time_text
from agentmatch.topics import (ApprovedTopicStatement, DisplayGrant, DisplayIdentity, SharedIntroductionContext,
                       SimulationError, grounded_bridges, neutral_invitation, project_display)
from . import vocabulary as v
from .publication import sha256_digest

NOTE_LIMIT = 280
STEP_DETAIL_LIMIT = 200
CHANNEL_SESSION_SECONDS = 600


class IntroductionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def introduction_ttl() -> timedelta:
    return timedelta(seconds=load_defaults()["protocol_pilot"]["proposal_ttl_seconds"])


@dataclass(frozen=True)
class NoticeRef:
    notice_id: str
    revision: int
    owner_id: str
    expires_at: datetime


def clean_note(text: Any, *, limit: int = NOTE_LIMIT) -> str:
    if not isinstance(text, str):
        raise IntroductionError("INVALID_TEXT")
    text = text.strip()
    if len(text) > limit or any(ord(c) < 32 and c not in "\n" for c in text) or any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise IntroductionError("INVALID_TEXT")
    return text


@dataclass
class FirstStep:
    """Proposed -> agreed by both -> each person's own outcome report."""
    version: int = 0
    action: str | None = None
    detail: str = ""
    proposed_by: str | None = None
    agreed_by: set[str] = field(default_factory=set)
    reports: dict[str, str] = field(default_factory=dict)

    def state(self, participants: tuple[str, str]) -> str:
        if self.action is None:
            return "none"
        if self.agreed_by != set(participants):
            return "proposed"
        if not self.reports:
            return "agreed"
        if set(self.reports) == set(participants) and all(value == "happened" for value in self.reports.values()):
            return "both_reported_happened"
        return "reported"


@dataclass
class Introduction:
    intro_id: str
    proposer_id: str
    recipient_id: str
    proposer_ref: NoticeRef
    recipient_ref: NoticeRef
    created_at: datetime
    expires_at: datetime
    suggested_first_step: str
    note: str
    context_version: int = 1
    status: str = "pending"  # pending | open | declined | expired | closed
    closed_reason: str | None = None
    blocked_by: str | None = None
    # Snapshot of the exact shared context and R0 proposal digest for the
    # current version, as shown to both people. Rebuilt on every change.
    context_snapshot: dict[str, Any] | None = None
    proposal_digest: str | None = None
    acceptances: dict[str, Acceptance] = field(default_factory=dict)
    invalidated_acceptances: tuple[str, ...] = ()
    channel: HumanChannel | None = None
    first_step: FirstStep = field(default_factory=FirstStep)

    @property
    def participants(self) -> tuple[str, str]:
        return (self.proposer_id, self.recipient_id)

    def other(self, owner_id: str) -> str:
        if owner_id not in self.participants:
            raise IntroductionError("NOT_A_PARTICIPANT")
        return self.recipient_id if owner_id == self.proposer_id else self.proposer_id


def _labels(registry: Mapping[str, str], keys: list[str]) -> list[str]:
    return [registry[key] for key in keys if key in registry]


def shared_context(intro: Introduction, proposer: Mapping[str, Any], recipient: Mapping[str, Any]) -> dict[str, Any]:
    """The five answers, from published notice payloads only."""
    def gains(taker: Mapping[str, Any], giver: Mapping[str, Any]) -> list[str]:
        needs = [taker["primary_need"], *taker["also_seeks"]]
        return _labels(v.CAPABILITIES, [tag for tag in needs if tag in giver["offers"]])

    project = next((item for item in (proposer, recipient) if item["mode"] == "project"), proposer)
    checks: list[str] = []
    for payload in (proposer, recipient):
        alias = payload["publisher_alias"]
        if payload["basis"] == "undecided":
            checks.append(f"{alias}'s engagement basis is undecided.")
        if "working_arrangement" not in payload:
            checks.append(f"{alias}'s notice does not state a working arrangement.")
        if "feedback" not in payload:
            checks.append(f"{alias}'s notice does not state a feedback style.")
        if "schedule" not in payload:
            checks.append(f"{alias}'s notice does not state hours or a start window.")
        if not payload["offers_complete"]:
            checks.append(f"{alias}'s list of offers is not marked complete.")
    checks.append("Neither notice is verified; both are the owners' own statements.")
    return {
        "achieve": {"text": project["desired_outcome"], "from_alias": project["publisher_alias"], "title": project["title"]},
        "contributions": [{"alias": proposer["publisher_alias"], "offers": gains(recipient, proposer)},
                          {"alias": recipient["publisher_alias"], "offers": gains(proposer, recipient)}],
        "gains": [{"alias": proposer["publisher_alias"], "gains": gains(proposer, recipient)},
                  {"alias": recipient["publisher_alias"], "gains": gains(recipient, proposer)}],
        "to_check": checks,
        "suggested_first_step": {"id": intro.suggested_first_step, "label": v.FIRST_STEPS[intro.suggested_first_step]},
        "note": intro.note or None,
        "note_author_alias": proposer["publisher_alias"] if intro.note else None,
    }


def build_proposal(intro: Introduction, proposer: Mapping[str, Any], recipient: Mapping[str, Any]) -> tuple[dict[str, Any], ChannelProposal]:
    """Assemble the R0 proposal; its digest covers the full workbench context."""
    workbench_context = shared_context(intro, proposer, recipient)
    refs = [{"notice_id": ref.notice_id, "revision": ref.revision} for ref in (intro.proposer_ref, intro.recipient_ref)]
    binding = sha256_digest({"intro_id": intro.intro_id, "version": intro.context_version, "notices": refs,
                             "context": workbench_context})
    displays = []
    for owner_id, payload, ref, peer in ((intro.proposer_id, proposer, intro.proposer_ref, intro.recipient_id),
                                         (intro.recipient_id, recipient, intro.recipient_ref, intro.proposer_id)):
        identity = DisplayIdentity(owner_id, payload["publisher_alias"], 1)
        grant = DisplayGrant(owner_id, peer, "collaboration", 1, identity.value_digest, ref.expires_at)
        displays.append(project_display(identity, grant, peer, "collaboration", intro.created_at))
    statements = []
    for owner_id, payload, ref, peer in ((intro.proposer_id, proposer, intro.proposer_ref, intro.recipient_id),
                                         (intro.recipient_id, recipient, intro.recipient_ref, intro.proposer_id)):
        statements.append(tuple(ApprovedTopicStatement(f"{ref.notice_id}-{tag}", ref.revision, owner_id, peer, "collaboration",
                                                        tag, v.FOCUS_AREAS[tag],
                                                        f"{payload['publisher_alias']}'s notice lists {v.FOCUS_AREAS[tag].lower()} as a current focus.",
                                                        ref.expires_at)
                                for tag in payload["focus_areas"]))
    bridges = grounded_bridges(statements[0], statements[1], left_id=intro.proposer_id, right_id=intro.recipient_id,
                               purpose="collaboration", as_of=intro.created_at)
    context = SharedIntroductionContext(f"{intro.intro_id}-v{intro.context_version}-{binding[:24]}", intro.context_version,
                                        "collaboration", intro.participants, (displays[0], displays[1]),
                                        bridges[0] if bridges else neutral_invitation())
    authority = min(intro.expires_at, intro.proposer_ref.expires_at, intro.recipient_ref.expires_at)
    proposal = ChannelProposal(intro.intro_id, intro.context_version, context, intro.expires_at, authority)
    return workbench_context, proposal


def expire_if_due(intro: Introduction, now: datetime) -> None:
    if intro.status == "pending" and checked_time(now) >= intro.expires_at:
        intro.status, intro.closed_reason = "expired", "INTRODUCTION_EXPIRED"
        intro.acceptances.clear()


def close(intro: Introduction, reason: str) -> None:
    if intro.status == "pending":
        intro.status, intro.closed_reason = "closed", reason
        intro.acceptances.clear()


def _require_pending(intro: Introduction, owner_id: str, now: datetime) -> None:
    intro.other(owner_id)
    expire_if_due(intro, now)
    if intro.status != "pending":
        raise IntroductionError("INTRODUCTION_NOT_PENDING")


def accept(intro: Introduction, owner_id: str, *, presented_digest: str, proposal: ChannelProposal,
           now: datetime) -> bool:
    """Record one owner's own acceptance. Opens the channel only on the second."""
    now = checked_time(now)
    _require_pending(intro, owner_id, now)
    if proposal.version != intro.context_version or presented_digest != proposal.value_digest:
        raise IntroductionError("CONTEXT_CHANGED")
    intro.acceptances[owner_id] = acceptance_for(proposal, owner_id, intro.expires_at)
    if set(intro.acceptances) != set(intro.participants):
        return False
    try:
        intro.channel = open_channel(proposal, tuple(intro.acceptances[o] for o in intro.participants), as_of=now,
                                     session_expires_at=now + timedelta(seconds=CHANNEL_SESSION_SECONDS))
    except SimulationError as exc:
        # The R0 gate refused (stale digest, expired authority). Keep the
        # acceptance that was just given out of the record: fail closed.
        del intro.acceptances[owner_id]
        raise IntroductionError("CHANNEL_REFUSED:" + exc.code) from exc
    intro.status = "open"
    return True


def withdraw_acceptance(intro: Introduction, owner_id: str, now: datetime) -> None:
    _require_pending(intro, owner_id, now)
    if owner_id not in intro.acceptances:
        raise IntroductionError("NO_ACCEPTANCE_TO_WITHDRAW")
    del intro.acceptances[owner_id]


def decline(intro: Introduction, owner_id: str, now: datetime) -> None:
    _require_pending(intro, owner_id, now)
    intro.status, intro.closed_reason = "declined", "DECLINED"
    intro.acceptances.clear()


def change_context(intro: Introduction, owner_id: str, *, suggested_first_step: str, note: str, now: datetime) -> tuple[str, ...]:
    """Any change makes a new context version; every earlier acceptance is void."""
    _require_pending(intro, owner_id, now)
    if suggested_first_step not in v.FIRST_STEPS:
        raise IntroductionError("UNKNOWN_FIRST_STEP")
    note = clean_note(note)
    if (suggested_first_step, note) == (intro.suggested_first_step, intro.note):
        raise IntroductionError("NO_CHANGE")
    invalidated = tuple(sorted(intro.acceptances))
    intro.suggested_first_step, intro.note = suggested_first_step, note
    intro.context_version += 1
    intro.acceptances.clear()
    intro.invalidated_acceptances = invalidated
    return invalidated


def block(intro: Introduction, owner_id: str, now: datetime) -> None:
    intro.other(owner_id)
    if intro.channel is not None and not intro.channel.closed:
        intro.channel.block(owner_id, checked_time(now))
    intro.acceptances.clear()
    if intro.status in {"pending", "open"}:
        intro.status, intro.closed_reason, intro.blocked_by = "closed", "BLOCKED", owner_id


def _require_open(intro: Introduction, owner_id: str) -> None:
    intro.other(owner_id)
    if intro.status != "open" or intro.channel is None or intro.channel.closed:
        raise IntroductionError("CHANNEL_NOT_OPEN")


def close_connection(intro: Introduction, owner_id: str, *, context_version: int) -> bool:
    """End an open connection without blocking or claiming a successful outcome.

    The same version may be closed again safely after a retry. Reports and the
    original mutual acceptance remain as history, while the channel's live
    membership and coaching authority are ended.
    """
    intro.other(owner_id)
    if context_version != intro.context_version:
        raise IntroductionError("CONTEXT_CHANGED")
    if intro.status == "closed" and intro.closed_reason == "OWNER_CLOSED":
        return False
    _require_open(intro, owner_id)
    intro.status, intro.closed_reason = "closed", "OWNER_CLOSED"
    assert intro.channel is not None
    intro.channel.closed = True
    intro.channel.connected = False
    intro.channel.membership_epoch += 1
    intro.channel.withdraw_context_authority()
    for entry in intro.channel.outbox.values():
        if entry.state in {"queued_local", "sent_unacknowledged"}:
            entry.state = "cancelled"
    return True


def propose_step(intro: Introduction, owner_id: str, *, action: str, detail: Any) -> None:
    _require_open(intro, owner_id)
    if action not in v.FIRST_STEPS:
        raise IntroductionError("UNKNOWN_FIRST_STEP")
    if intro.first_step.reports:
        raise IntroductionError("STEP_ALREADY_REPORTED")
    step = intro.first_step
    step.version += 1
    step.action, step.detail, step.proposed_by = action, clean_note(detail, limit=STEP_DETAIL_LIMIT), owner_id
    step.agreed_by = {owner_id}  # proposing is the proposer's own agreement; the other person must agree separately


def agree_step(intro: Introduction, owner_id: str, *, version: int) -> None:
    _require_open(intro, owner_id)
    step = intro.first_step
    if step.action is None or version != step.version:
        raise IntroductionError("STEP_CHANGED")
    step.agreed_by.add(owner_id)


def report_step(intro: Introduction, owner_id: str, *, version: int, outcome: str) -> None:
    _require_open(intro, owner_id)
    step = intro.first_step
    if step.state(intro.participants) not in {"agreed", "reported", "both_reported_happened"} or version != step.version:
        raise IntroductionError("STEP_NOT_AGREED")
    if outcome not in {"happened", "did_not_happen"}:
        raise IntroductionError("UNKNOWN_OUTCOME")
    step.reports[owner_id] = outcome


def channel_summary(intro: Introduction) -> dict[str, Any] | None:
    if intro.channel is None:
        return None
    summary = intro.channel.summary()
    return {"status": summary["status"], "opened_at": time_text(intro.channel.opened_at),
            "human_messages": len(summary["human_messages"]), "encrypted": summary["encrypted"],
            "persistent": summary["persistent"], "simulation_notice": summary["simulation_notice"]}
