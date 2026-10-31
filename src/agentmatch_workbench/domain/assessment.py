"""Map briefs and published notices onto the unchanged R0 assessment schema.

Only supported structured fields become R0 claims, atoms or hard constraints.
Everything else (title, outcome text, stage, hours, start window, commercial
note, first step) is notice metadata and is never scored.

Two sources, one mapping:

- ``facts_from_brief``: the viewer's own private brief. Used locally only.
- ``facts_from_notice``: another owner's *published* notice. Fields that the
  publisher left out are ``None`` and therefore produce no claim, so the R0
  scorer reports them as unknown instead of as a mismatch. This is how notice
  data that is insufficient keeps its uncertainty.

The viewer's assessment of a candidate therefore depends on exactly two
inputs: the viewer's own brief and the candidate's approved notice. No other
owner's private brief is ever read when building a view for the viewer.

Mapping policy (a product decision, not a calibrated model):

- capability_complementarity: one membership atom per needed capability.
- goal_problem_alignment: one membership atom per focus area.
- practical_workflow_fit: accepted-value atoms for venue and rhythm, if set.
- communication_fit: accepted-value atom for feedback style, if set.
- hard constraints: the primary need must be offered (reciprocal value comes
  before fit, so shared topics alone never qualify); engagement basis and
  availability when the owner marks them as requirements.
- the R0 weights, coverage floor and selectivity presets are reused unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from agentmatch.config import selectivity as selectivity_threshold
from agentmatch.models import Owner, load_owner, time_text
from agentmatch.numeric import decimal_text
from .briefs import Brief

AVAILABLE_VALUES = ("available", "limited")
UNPUBLISHED_SELECTIVITY = "selective"


@dataclass(frozen=True)
class MatchFacts:
    """Scorable facts from one source. ``None`` means not stated."""
    owner_id: str
    alias: str
    source: str  # private_brief | published_notice
    source_ref: str
    version: int
    valid_from: datetime
    expires_at: datetime
    offers: tuple[str, ...]
    offers_complete: bool
    focus_areas: tuple[str, ...]
    availability: str | None
    venue: str | None
    rhythm: str | None
    feedback_style: str | None
    basis: str | None
    primary_need: str
    also_seeks: tuple[str, ...]
    accepted_venues: tuple[str, ...]
    accepted_rhythms: tuple[str, ...]
    accepted_feedback: tuple[str, ...]
    accepted_bases: tuple[str, ...]
    basis_required: bool
    availability_required: bool
    capacity: int
    selectivity: str
    selectivity_published: bool

    @property
    def needs(self) -> tuple[str, ...]:
        return (self.primary_need, *self.also_seeks)


def facts_from_brief(brief: Brief, *, alias: str, expires_at: datetime) -> MatchFacts:
    return MatchFacts(brief.owner_id, alias, "private_brief", f"{brief.brief_id}-v{brief.version}", brief.version,
                      brief.updated_at, expires_at, brief.offers, brief.offers_complete, brief.focus_areas,
                      brief.availability, brief.venue, brief.rhythm, brief.feedback_style,
                      None if brief.basis == "undecided" else brief.basis, brief.primary_need, brief.also_seeks,
                      brief.accepted_venues, brief.accepted_rhythms, brief.accepted_feedback, brief.accepted_bases,
                      brief.basis_required, brief.availability_required, brief.capacity, brief.selectivity, True)


def facts_from_notice(*, owner_id: str, notice_id: str, revision: int, payload: Mapping[str, Any],
                      published_at: datetime, expires_at: datetime) -> MatchFacts:
    """Only published sections contribute; absent sections stay unknown."""
    work = payload.get("working_arrangement") or {}
    feedback = payload.get("feedback") or {}
    basis = payload["basis"]
    return MatchFacts(owner_id, payload["publisher_alias"], "published_notice", f"{notice_id}-r{revision}", revision,
                      published_at, expires_at, tuple(payload["offers"]), bool(payload["offers_complete"]),
                      tuple(payload["focus_areas"]), payload["availability"], work.get("venue"), work.get("rhythm"),
                      feedback.get("style"), None if basis == "undecided" else basis, payload["primary_need"],
                      tuple(payload["also_seeks"]), tuple(work.get("accepted_venues", ())),
                      tuple(work.get("accepted_rhythms", ())), tuple(feedback.get("accepted", ())),
                      tuple(payload["accepted_bases"]), bool(payload["basis_required"]),
                      bool(payload["availability_required"]), 1, UNPUBLISHED_SELECTIVITY, False)


def owner_from_facts(facts: MatchFacts, *, blocked_owner_ids: tuple[str, ...] = ()) -> Owner:
    """Build a strict R0 owner record; ``load_owner`` re-validates everything."""
    prefix = facts.source_ref
    start, end = time_text(facts.valid_from), time_text(facts.expires_at)
    context = ("Owner-entered structured brief field (fictional prototype)." if facts.source == "private_brief"
               else "Field from an approved published notice (fictional prototype).")

    def claim(name: str, field: str, value: Any, *, complete: bool | None = None) -> dict[str, Any]:
        record = {"claim_id": f"{prefix}-{name}", "version": 1, "dimension": field, "value": value,
                  "origin": "current_owner_statement", "approval": "approved_for_local_use", "lifecycle": "active",
                  "purpose": "collaboration", "valid_from": start, "expires_at": end, "source_refs": [prefix],
                  "context": context, "contradiction_refs": []}
        if complete is not None:
            record["complete"] = complete
        return record

    claims = [claim("offers", "offer.capabilities", list(facts.offers), complete=facts.offers_complete),
              claim("focus", "intent.problem_tags", list(facts.focus_areas), complete=True)]
    for name, field, value in (("availability", "workflow.availability", facts.availability),
                               ("venue", "workflow.venue", facts.venue), ("rhythm", "workflow.rhythm", facts.rhythm),
                               ("feedback", "communication.feedback", facts.feedback_style),
                               ("basis", "workflow.compensation", facts.basis)):
        if value is not None:
            claims.append(claim(name, field, value))

    atoms: dict[str, list[dict[str, Any]]] = {
        "capability_complementarity": [{"atom_id": f"need-{tag}", "field": "offer.capabilities", "kind": "membership", "wanted": tag}
                                       for tag in facts.needs],
        "goal_problem_alignment": [{"atom_id": f"focus-{tag}", "field": "intent.problem_tags", "kind": "membership", "wanted": tag}
                                   for tag in facts.focus_areas],
        "practical_workflow_fit": [], "communication_fit": []}
    if facts.accepted_venues:
        atoms["practical_workflow_fit"].append({"atom_id": "venue", "field": "workflow.venue", "kind": "accepted",
                                                "accepted": list(facts.accepted_venues)})
    if facts.accepted_rhythms:
        atoms["practical_workflow_fit"].append({"atom_id": "rhythm", "field": "workflow.rhythm", "kind": "accepted",
                                                "accepted": list(facts.accepted_rhythms)})
    if facts.accepted_feedback:
        atoms["communication_fit"].append({"atom_id": "feedback", "field": "communication.feedback", "kind": "accepted",
                                           "accepted": list(facts.accepted_feedback)})
    hard = [{"constraint_id": "primary-need", "field": "offer.capabilities", "kind": "membership", "wanted": facts.primary_need}]
    if facts.basis_required:
        hard.append({"constraint_id": "basis", "field": "workflow.compensation", "kind": "accepted",
                     "accepted": list(facts.accepted_bases)})
    if facts.availability_required:
        hard.append({"constraint_id": "availability", "field": "workflow.availability", "kind": "accepted",
                     "accepted": list(AVAILABLE_VALUES)})
    applicable = [family for family, items in atoms.items() if items]
    intent = {"intent_id": f"{prefix}-intent", "version": facts.version, "purpose": "collaboration", "current": True,
              "valid_from": start, "expires_at": end, "capacity": facts.capacity,
              "threshold": decimal_text(selectivity_threshold(facts.selectivity)),
              "atoms": {family: items for family, items in atoms.items() if items},
              "hard_constraints": hard, "applicable_families": applicable}
    return load_owner({"schema_version": "0.1", "owner_id": facts.owner_id, "alias": facts.alias, "synthetic": True,
                       "intent": intent, "claims": claims, "blocked_owner_ids": sorted(set(blocked_owner_ids))})
