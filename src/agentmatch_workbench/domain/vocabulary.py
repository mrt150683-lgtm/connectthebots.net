"""Closed vocabularies for structured brief inputs.

Every scored brief field takes values from one of these registries. Free text is
allowed only in fields that are never scored (title, desired outcome, notes), so
nothing is "extracted" from prose and no evidence is invented from it. Adding a
term here is a product decision: it changes which values can reach the R0
assessment, so it needs a fixture case like any other matching change.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


def _registry(pairs: tuple[tuple[str, str], ...]) -> Mapping[str, str]:
    return MappingProxyType(dict(pairs))


MODES = _registry((
    ("project", "I have a project or opportunity"),
    ("contribute", "I want to contribute"),
))

CAPABILITIES = _registry((
    ("prototyping", "Prototype building"),
    ("frontend-development", "Front-end development"),
    ("ux-research", "User research"),
    ("accessibility-review", "Accessibility review"),
    ("domain-knowledge", "Domain knowledge"),
    ("pilot-site-access", "Access to a pilot site"),
    ("operations", "Operations know-how"),
    ("data-modelling", "Data modelling"),
    ("fabrication", "Fabrication"),
    ("grant-writing", "Grant writing"),
    ("copyediting", "Copyediting"),
    ("project-coordination", "Project coordination"),
))

FOCUS_AREAS = _registry((
    ("maintenance-planning", "Maintenance planning"),
    ("tool-libraries", "Community tool libraries"),
    ("repair-cafes", "Repair cafés"),
    ("open-civic-data", "Open civic data"),
    ("accessible-wayfinding", "Accessible wayfinding"),
    ("learning-exhibits", "Portable learning exhibits"),
))

STAGES = _registry((
    ("exploring", "Exploring"),
    ("validating", "Validating"),
    ("prototyping", "Prototyping"),
    ("delivering", "Delivering"),
    ("operating", "Operating"),
))

AVAILABILITY = _registry((
    ("available", "Available now"),
    ("limited", "Limited capacity"),
    ("unavailable", "Not available at the moment"),
))

START_WINDOWS = _registry((
    ("now", "Can start now"),
    ("within-month", "Within a month"),
    ("next-quarter", "Next quarter"),
))

VENUES = _registry((
    ("remote", "Remote"),
    ("hybrid", "Hybrid"),
    ("in-person", "In person"),
))

RHYTHMS = _registry((
    ("async", "Mostly asynchronous"),
    ("mixed", "Mixed"),
    ("sync", "Mostly live sessions"),
))

FEEDBACK_STYLES = _registry((
    ("direct", "Direct comments"),
    ("written", "Written, considered notes"),
    ("gentle", "Gentle, conversational"),
))

# "undecided" is a real choice, but it is never turned into a claim: an
# undecided basis stays unknown to the matcher instead of matching anything.
BASES = _registry((
    ("paid", "Paid work"),
    ("volunteer", "Volunteer"),
    ("reciprocal", "Reciprocal exchange"),
    ("equity-exploration", "Exploring a cofounder or equity arrangement"),
    ("undecided", "Undecided"),
))

FIRST_STEPS = _registry((
    ("scoping-call", "A 30-minute scoping call"),
    ("async-review", "Review one document or prototype asynchronously"),
    ("bounded-trial", "A bounded trial task of up to two hours"),
    ("site-walkthrough", "A short walkthrough of the pilot site or tools"),
    ("exchange-examples", "Exchange one example of past work"),
))

SELECTIVITY = _registry((
    ("open", "Open: show more possibilities"),
    ("selective", "Selective (default)"),
    ("close_fit", "Close fit only"),
))


def as_options(registry: Mapping[str, str]) -> list[dict[str, str]]:
    """Ordered id/label pairs for the browser; order is the registry order."""
    return [{"id": key, "label": label} for key, label in registry.items()]


def all_vocabularies() -> dict[str, list[dict[str, str]]]:
    return {"modes": as_options(MODES), "capabilities": as_options(CAPABILITIES),
            "focus_areas": as_options(FOCUS_AREAS), "stages": as_options(STAGES),
            "availability": as_options(AVAILABILITY), "start_windows": as_options(START_WINDOWS),
            "venues": as_options(VENUES), "rhythms": as_options(RHYTHMS),
            "feedback_styles": as_options(FEEDBACK_STYLES), "bases": as_options(BASES),
            "first_steps": as_options(FIRST_STEPS), "selectivity": as_options(SELECTIVITY)}
