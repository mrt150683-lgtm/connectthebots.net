"""Plain-language shortlist explanations built from the R0 explainer.

``explain_pair`` runs the unchanged R0 ``assess_pair_offline`` and
``render_pair_local`` and then translates the private local cards into
statements. Translation is an allowlist: it reads atom ids, ``known``, ``fit``
and reason codes, and never forwards ``local_claim_refs``, claim ids, source
references or exact fractions to the browser.

Rules the wording keeps:

- an unknown atom is described as "not stated", never as a mismatch;
- a hard requirement that is unknown is reported as blocking, not as passed;
- no percentage, score or rank number is produced. The R0 quantities decide
  qualification and the order among *retrieved* notices only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from agentmatch.explain import render_pair_local
from agentmatch.models import PairAssessment
from agentmatch.scoring import assess_pair_offline
from . import vocabulary as v
from .assessment import MatchFacts, owner_from_facts

STATUS_LABELS = {
    "qualified_candidate": "Meets the stated preferences",
    "insufficient_evidence": "Not enough information to decide",
    "needs_information": "A required fact is not known",
    "not_recommended": "The stated preferences point elsewhere",
    "not_eligible": "A stated requirement is not met",
    "invalid_intent": "This brief cannot be used for matching right now",
}

PAIR_LABELS = {
    "both_meet": "Both sides' stated preferences are met",
    "needs_information": "Needs information before an introduction",
    "not_a_fit_now": "Not a fit for the current briefs",
}


def _label(registry: dict[str, str] | Any, key: str | None) -> str:
    return registry.get(key, key) if key is not None else "not stated"


def _labels(registry: Any, keys: tuple[str, ...]) -> str:
    return ", ".join(registry[key] for key in keys) if keys else "none"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


class _Voice:
    """Grammar for 'you' versus a named alias, so both directions read naturally."""

    def __init__(self, tested: str, chooser: str, *, tested_is_viewer: bool):
        self.tested, self.chooser, self.viewer = tested, chooser, tested_is_viewer

    def of(self, verb_you: str, verb_alias: str) -> str:
        return f"{self.tested} {verb_you if self.viewer else verb_alias}"

    @property
    def source(self) -> str:
        return "your private brief" if self.viewer else f"{self.tested}'s published notice"

    @property
    def chooser_wants(self) -> str:
        return f"{self.chooser} wants" if self.viewer else "you want"


def _atom_statement(atom_id: str, known: bool, fit: bool, reason: str, tested: MatchFacts, chooser: MatchFacts,
                    voice: _Voice) -> str:
    if atom_id.startswith("need-"):
        cap = v.CAPABILITIES.get(atom_id[5:], atom_id[5:])
        if not known:
            return (f"{_cap(voice.source)} does not say whether {voice.tested if not voice.viewer else 'you'} "
                    f"offer{'s' if not voice.viewer else ''} {cap.lower()} (the list of offers is not marked complete).")
        return (f"{voice.of('offer', 'offers')} {cap.lower()}, which {voice.chooser_wants}." if fit else
                f"{voice.of('do not list', 'does not list')} {cap.lower()} in a complete list of offers.")
    if atom_id.startswith("focus-"):
        area = v.FOCUS_AREAS.get(atom_id[6:], atom_id[6:])
        if not known:
            return f"{_cap(voice.source)} does not state whether {area.lower()} is a current focus."
        return (f"{voice.of('share', 'shares')} a current focus on {area.lower()}." if fit else
                f"{area} is not among {'your' if voice.viewer else voice.tested + chr(39) + 's'} stated focus areas.")
    names = {"venue": ("working arrangement", v.VENUES, tested.venue, chooser.accepted_venues),
             "rhythm": ("working rhythm", v.RHYTHMS, tested.rhythm, chooser.accepted_rhythms),
             "feedback": ("feedback style", v.FEEDBACK_STYLES, tested.feedback_style, chooser.accepted_feedback)}
    if atom_id in names:
        title, registry, value, accepted = names[atom_id]
        if not known:
            return f"{_cap(voice.source)} does not state a {title}; it stays unknown rather than counting against."
        accepted_text = _labels(registry, accepted).lower()
        return (f"{title.capitalize()}: {_label(registry, value).lower()}, within what {voice.chooser_wants} ({accepted_text})." if fit else
                f"{title.capitalize()}: {_label(registry, value).lower()}, outside what {voice.chooser_wants} ({accepted_text}).")
    return f"{atom_id}: {reason}"


def _hard_statements(reasons: tuple[str, ...] | list[str], tested: MatchFacts, chooser: MatchFacts, voice: _Voice,
                     status: str) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Split hard requirements into met, failed and unknown with plain wording."""
    constraints = ["primary-need"] + (["basis"] if chooser.basis_required else []) + (["availability"] if chooser.availability_required else [])
    failed = {r.split(":", 1)[1] for r in reasons if r.startswith("HARD_CONSTRAINT_FAILED:")}
    unknown = {r.split(":", 1)[1] for r in reasons if r.startswith("HARD_CONSTRAINT_UNKNOWN:")}
    evaluated = status not in {"invalid_intent"}
    met_list, failed_list, unknown_list = [], [], []
    for name in constraints:
        if name == "primary-need":
            cap = v.CAPABILITIES[chooser.primary_need].lower()
            text = {"met": f"Primary need met: {voice.of('offer', 'offers')} {cap}.",
                    "failed": f"Primary need not met: {voice.of('do not offer', 'does not offer')} {cap}.",
                    "unknown": f"Primary need unknown: {voice.source} does not say whether {cap} is offered."}
        elif name == "basis":
            text = {"met": f"Engagement basis is acceptable: {_label(v.BASES, tested.basis).lower()}.",
                    "failed": (f"Engagement basis does not fit: {_label(v.BASES, tested.basis).lower()}, but "
                               f"{voice.chooser_wants} {_labels(v.BASES, chooser.accepted_bases).lower()} as a requirement."),
                    "unknown": "Engagement basis is undecided or not stated, and it is marked as a requirement."}
        else:
            text = {"met": f"Availability requirement met: {_label(v.AVAILABILITY, tested.availability).lower()}.",
                    "failed": f"Availability requirement not met: {_label(v.AVAILABILITY, tested.availability).lower()}.",
                    "unknown": "Availability is not stated, and it is marked as a requirement."}
        if name in failed:
            failed_list.append({"requirement": name, "statement": text["failed"]})
        elif name in unknown:
            unknown_list.append({"requirement": name, "statement": text["unknown"]})
        elif evaluated:  # R0 checks every hard constraint unless the seeker's own intent is invalid
            met_list.append({"requirement": name, "statement": text["met"]})
    return met_list, failed_list, unknown_list


def _direction(card: dict[str, Any], status: str, reasons: list[str], tested: MatchFacts, chooser: MatchFacts,
               voice: _Voice, heading: str) -> dict[str, Any]:
    def statements(items: list[dict[str, Any]], *, is_known: bool) -> list[dict[str, str]]:
        output = []
        for atom in items:
            fit = atom["fit"]["numerator"] != "0"
            output.append({"aspect": atom["family"], "statement": _atom_statement(atom["atom_id"], is_known, fit, atom["reason"], tested, chooser, voice),
                           "source": voice.source})
        return output

    met, failed, unknown = _hard_statements(reasons, tested, chooser, voice, status)
    other_reasons = [r for r in reasons if not r.startswith("HARD_CONSTRAINT_") and r not in {"PREFERENCE_THRESHOLD_MET", "NO_SOFT_PREFERENCE_FILTER"}]
    notes = {"SELECTIVITY_UNRESOLVED": "Unknown fields could still decide this either way, so it is not presented as a match.",
             "COVERAGE_BELOW_MINIMUM": "Too much of the preference is unknown to decide.",
             "PREFERENCE_THRESHOLD_UNREACHABLE": "Even if every unknown turned out well, the stated preferences would not be met.",
             "BLOCKED_PAIR": "This pair is blocked.", "CAPACITY_EXHAUSTED": "Introduction capacity is full.",
             "CANDIDATE_INTENT_UNAVAILABLE": "The other brief is not currently usable."}
    return {"heading": heading, "status": status, "status_label": STATUS_LABELS.get(status, status),
            "summary": card["summary"],
            "supported": statements(card["supported_atoms"], is_known=True),
            "mismatches": statements(card["known_mismatches"], is_known=True),
            "unknowns": statements(card["unknown_atoms"], is_known=False),
            "requirements_met": met, "requirements_failed": failed, "requirements_unknown": unknown,
            "notes": [notes[r] for r in other_reasons if r in notes],
            "threshold_note": None if chooser.selectivity_published else
            f"{chooser.alias}'s selectivity is not published; this side uses the default. {chooser.alias} decides for themselves when reviewing an introduction."}


def pair_status(pair: PairAssessment) -> str:
    statuses = {pair.a_to_b.status, pair.b_to_a.status}
    if pair.qualifies:
        return "both_meet"
    if statuses & {"not_eligible", "not_recommended", "invalid_intent"}:
        return "not_a_fit_now"
    return "needs_information"


def assess(viewer: MatchFacts, candidate: MatchFacts, *, as_of: datetime,
           blocked_owner_ids: tuple[str, ...] = ()) -> PairAssessment:
    return assess_pair_offline(owner_from_facts(viewer, blocked_owner_ids=blocked_owner_ids),
                               owner_from_facts(candidate), as_of)


def explain_pair(viewer: MatchFacts, candidate: MatchFacts, pair: PairAssessment) -> dict[str, Any]:
    """Allowlisted explanation for the viewer. Contains no claim ids or numbers."""
    rendered = render_pair_local(pair)
    forward = _direction(rendered["a_to_b"], pair.a_to_b.status, list(pair.a_to_b.reasons), candidate, viewer,
                         _Voice(candidate.alias, "you", tested_is_viewer=False),
                         f"Your brief's preferences, checked against {candidate.alias}'s notice")
    reverse = _direction(rendered["b_to_a"], pair.b_to_a.status, list(pair.b_to_a.reasons), viewer, candidate,
                         _Voice("You", candidate.alias, tested_is_viewer=True),
                         f"{candidate.alias}'s published preferences, checked against your brief")
    status = pair_status(pair)
    return {"pair_status": status, "pair_label": PAIR_LABELS[status], "directions": [forward, reverse],
            "reciprocal": {
                "they_offer_you": [v.CAPABILITIES[t] for t in viewer.needs if t in candidate.offers],
                "you_offer_them": [v.CAPABILITIES[t] for t in candidate.needs if t in viewer.offers],
                "shared_focus": [v.FOCUS_AREAS[t] for t in viewer.focus_areas if t in candidate.focus_areas]},
            "method_note": ("Checked locally with the R0 rules: both directions must pass on their own. "
                            "Unknown fields stay unknown. Nothing here is a compatibility percentage or a score of a person.")}
