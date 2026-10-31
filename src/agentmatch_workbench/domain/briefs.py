"""Private collaboration briefs: strict structured input, both entry paths.

A brief is the owner's private draft. It never leaves the owner's perspective
as-is: publication goes through ``publication.project_notice``, which copies an
allowlisted subset, and matching goes through ``assessment.facts_from_brief``,
which maps only the supported structured fields into the R0 schema.

Field placement (shown to the owner in the interface):

- *scored*: offers, primary need, also-useful needs, focus areas, availability,
  working arrangement, feedback style, engagement basis and their accepted
  counterpart values. These map to R0 claims, atoms or hard constraints.
- *notice only*: title, desired outcome, stage, hours, start window,
  commercial note and suggested first step. Published if chosen, never scored.
- *private*: ``private_notes``. Never published, never scored, never shown to
  another perspective. Tests plant canary values here.

Validation returns every field error at once so the interface can place each
message next to its control. Nothing here reads a clock; callers pass times in.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping

from agentmatch.models import checked_time, time_text
from . import vocabulary as v

TEXT_LIMITS = {"title": (4, 90), "desired_outcome": (12, 400), "commercial_note": (0, 200), "private_notes": (0, 500)}
MAX_OFFERS, MAX_ALSO_SEEKS, MAX_FOCUS = 6, 3, 3
MAX_CAPACITY = 5

BRIEF_FIELDS = frozenset({
    "mode", "title", "desired_outcome", "stage", "focus_areas", "offers", "offers_complete",
    "primary_need", "also_seeks", "availability", "availability_required", "hours_per_week",
    "start_window", "venue", "accepted_venues", "rhythm", "accepted_rhythms", "feedback_style",
    "accepted_feedback", "basis", "accepted_bases", "basis_required", "commercial_note",
    "first_step", "capacity", "selectivity", "private_notes",
})


class BriefValidationError(ValueError):
    """All field problems for one submission; codes are stable and non-sensitive."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("BRIEF_INVALID: " + ",".join(sorted({item["field"] for item in errors})))


@dataclass(frozen=True)
class Brief:
    brief_id: str
    owner_id: str
    version: int
    updated_at: datetime
    mode: str
    title: str
    desired_outcome: str
    stage: str
    focus_areas: tuple[str, ...]
    offers: tuple[str, ...]
    offers_complete: bool
    primary_need: str
    also_seeks: tuple[str, ...]
    availability: str
    availability_required: bool
    hours_per_week: int | None
    start_window: str
    venue: str | None
    accepted_venues: tuple[str, ...]
    rhythm: str | None
    accepted_rhythms: tuple[str, ...]
    feedback_style: str | None
    accepted_feedback: tuple[str, ...]
    basis: str
    accepted_bases: tuple[str, ...]
    basis_required: bool
    commercial_note: str
    first_step: str
    capacity: int
    selectivity: str
    private_notes: str

    def content(self) -> dict[str, Any]:
        """The editable fields only, in the same shape the browser submits."""
        return {"mode": self.mode, "title": self.title, "desired_outcome": self.desired_outcome,
                "stage": self.stage, "focus_areas": list(self.focus_areas), "offers": list(self.offers),
                "offers_complete": self.offers_complete, "primary_need": self.primary_need,
                "also_seeks": list(self.also_seeks), "availability": self.availability,
                "availability_required": self.availability_required, "hours_per_week": self.hours_per_week,
                "start_window": self.start_window, "venue": self.venue, "accepted_venues": list(self.accepted_venues),
                "rhythm": self.rhythm, "accepted_rhythms": list(self.accepted_rhythms),
                "feedback_style": self.feedback_style, "accepted_feedback": list(self.accepted_feedback),
                "basis": self.basis, "accepted_bases": list(self.accepted_bases), "basis_required": self.basis_required,
                "commercial_note": self.commercial_note, "first_step": self.first_step, "capacity": self.capacity,
                "selectivity": self.selectivity, "private_notes": self.private_notes}

    def to_private_dict(self) -> dict[str, Any]:
        """Owner-only record. Never place this in another perspective's response."""
        return {"brief_id": self.brief_id, "version": self.version, "updated_at": time_text(self.updated_at),
                **self.content()}


class _Collector:
    def __init__(self, data: Mapping[str, Any]):
        self.data = data
        self.errors: list[dict[str, str]] = []

    def fail(self, field: str, code: str, message: str) -> None:
        self.errors.append({"field": field, "code": code, "message": message})

    def text(self, field: str) -> str:
        low, high = TEXT_LIMITS[field]
        value = self.data.get(field)
        if not isinstance(value, str):
            self.fail(field, "TEXT_REQUIRED", "Enter text.")
            return ""
        value = value.strip()
        if any(ord(c) < 32 and c not in "\n\t" for c in value) or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            self.fail(field, "INVALID_CHARACTERS", "Remove control characters.")
            return ""
        if not low <= len(value) <= high:
            self.fail(field, "TEXT_LENGTH", f"Use between {low} and {high} characters." if low else f"Use at most {high} characters.")
        return value

    def choice(self, field: str, registry: Mapping[str, str], *, optional: bool = False) -> str | None:
        value = self.data.get(field)
        if value is None and optional:
            return None
        if not isinstance(value, str) or value not in registry:
            self.fail(field, "CHOICE_REQUIRED", "Choose one of the listed options.")
            return None
        return value

    def choices(self, field: str, registry: Mapping[str, str], *, low: int, high: int) -> tuple[str, ...]:
        value = self.data.get(field)
        if not isinstance(value, list) or any(not isinstance(item, str) or item not in registry for item in value):
            self.fail(field, "CHOICES_INVALID", "Choose from the listed options.")
            return ()
        if len(set(value)) != len(value):
            self.fail(field, "CHOICES_DUPLICATE", "Each option can be chosen once.")
            return ()
        if not low <= len(value) <= high:
            self.fail(field, "CHOICES_COUNT", f"Choose between {low} and {high}." if low else f"Choose at most {high}.")
        # Registry order, so equal selections always produce identical payloads.
        return tuple(key for key in registry if key in value)

    def flag(self, field: str) -> bool:
        value = self.data.get(field)
        if type(value) is not bool:
            self.fail(field, "BOOLEAN_REQUIRED", "Choose yes or no.")
            return False
        return value

    def integer(self, field: str, low: int, high: int, *, optional: bool = False) -> int | None:
        value = self.data.get(field)
        if value is None and optional:
            return None
        if type(value) is not int or not low <= value <= high:
            self.fail(field, "NUMBER_RANGE", f"Enter a whole number from {low} to {high}.")
            return None
        return value


def parse_brief(data: Any, *, owner_id: str, version: int, updated_at: datetime) -> Brief:
    """Validate a complete submitted brief. Unknown keys are refused, not ignored."""
    if not isinstance(data, dict):
        raise BriefValidationError([{"field": "brief", "code": "OBJECT_REQUIRED", "message": "Submit the brief as an object."}])
    unknown, missing = set(data) - BRIEF_FIELDS, BRIEF_FIELDS - set(data)
    if unknown or missing:
        raise BriefValidationError([{"field": name, "code": "UNKNOWN_FIELD" if name in unknown else "MISSING_FIELD",
                                     "message": "Field not accepted." if name in unknown else "Field missing."}
                                    for name in sorted(unknown | missing)])
    c = _Collector(data)
    mode = c.choice("mode", v.MODES)
    title, outcome = c.text("title"), c.text("desired_outcome")
    stage = c.choice("stage", v.STAGES)
    focus = c.choices("focus_areas", v.FOCUS_AREAS, low=1, high=MAX_FOCUS)
    offers = c.choices("offers", v.CAPABILITIES, low=1, high=MAX_OFFERS)
    offers_complete = c.flag("offers_complete")
    primary = c.choice("primary_need", v.CAPABILITIES)
    also = c.choices("also_seeks", v.CAPABILITIES, low=0, high=MAX_ALSO_SEEKS)
    if primary is not None and primary in also:
        c.fail("also_seeks", "DUPLICATE_NEED", "Your primary need is already included; choose other needs here.")
    availability = c.choice("availability", v.AVAILABILITY)
    availability_required = c.flag("availability_required")
    hours = c.integer("hours_per_week", 1, 60, optional=True)
    start = c.choice("start_window", v.START_WINDOWS)
    venue = c.choice("venue", v.VENUES, optional=True)
    accepted_venues = c.choices("accepted_venues", v.VENUES, low=0, high=len(v.VENUES))
    rhythm = c.choice("rhythm", v.RHYTHMS, optional=True)
    accepted_rhythms = c.choices("accepted_rhythms", v.RHYTHMS, low=0, high=len(v.RHYTHMS))
    feedback = c.choice("feedback_style", v.FEEDBACK_STYLES, optional=True)
    accepted_feedback = c.choices("accepted_feedback", v.FEEDBACK_STYLES, low=0, high=len(v.FEEDBACK_STYLES))
    basis = c.choice("basis", v.BASES)
    accepted_bases = c.choices("accepted_bases", {k: l for k, l in v.BASES.items() if k != "undecided"}, low=1, high=len(v.BASES) - 1)
    basis_required = c.flag("basis_required")
    note = c.text("commercial_note")
    first_step = c.choice("first_step", v.FIRST_STEPS)
    capacity = c.integer("capacity", 1, MAX_CAPACITY)
    selectivity = c.choice("selectivity", v.SELECTIVITY)
    private_notes = c.text("private_notes")
    if c.errors:
        raise BriefValidationError(c.errors)
    return Brief(f"{owner_id}-brief", owner_id, version, checked_time(updated_at), mode or "", title, outcome,
                 stage or "", focus, offers, offers_complete, primary or "", also, availability or "",
                 availability_required, hours, start or "", venue, accepted_venues, rhythm, accepted_rhythms,
                 feedback, accepted_feedback, basis or "", accepted_bases, basis_required, note, first_step or "",
                 capacity or 1, selectivity or "selective", private_notes)


def revise(brief: Brief, data: Any, *, updated_at: datetime) -> Brief:
    """Every accepted edit creates the next version; approvals bind to versions."""
    if checked_time(updated_at) < brief.updated_at:
        raise ValueError("CLOCK_ROLLBACK: brief edits must move forward in time")
    revised = parse_brief(data, owner_id=brief.owner_id, version=brief.version + 1, updated_at=updated_at)
    return replace(revised, brief_id=brief.brief_id)
