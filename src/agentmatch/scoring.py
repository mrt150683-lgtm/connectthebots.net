"""Exact, directional, inspectable matching over approved synthetic claims."""
from __future__ import annotations

from datetime import datetime
from fractions import Fraction

from .claims import resolve_usable, evidence_factor
from .config import load_defaults
from .models import (Owner, PreferenceAtom, ApprovedLocalView, EvidenceClaim, Measure,
                     AtomContribution, DirectionalAssessment, PairAssessment, checked_time)
from .numeric import exact


def _input_fit(atom: PreferenceAtom, claim: EvidenceClaim | None) -> tuple[bool, Fraction, str]:
    if claim is None:
        return False, Fraction(0), "UNKNOWN_OR_UNUSABLE_INPUT"
    value = claim.value
    if atom.kind == "membership":
        if not isinstance(value, tuple):
            return False, Fraction(0), "INPUT_TYPE_MISMATCH"
        if atom.wanted in value:
            return True, Fraction(1), "CURRENT_OFFER_OR_GOAL_PRESENT"
        if claim.complete is True:
            return True, Fraction(0), "ABSENT_FROM_COMPLETE_CURRENT_SET"
        return False, Fraction(0), "INCOMPLETE_SET_NO_NEGATIVE_INFERENCE"
    if atom.kind == "accepted":
        if not isinstance(value, (str, bool)):
            return False, Fraction(0), "INPUT_TYPE_MISMATCH"
        # bool and string remain distinct; no integer truthiness coercion.
        fits = any(type(value) is type(accepted) and value == accepted for accepted in atom.accepted)
        return True, Fraction(int(fits)), "ACCEPTED_VALUE" if fits else "KNOWN_VALUE_OUTSIDE_PREFERENCE"
    if not isinstance(value, Measure) or value.unit != atom.unit or atom.minimum is None or atom.maximum is None:
        return False, Fraction(0), "INPUT_TYPE_OR_UNIT_MISMATCH"
    fits = atom.minimum <= value.amount <= atom.maximum
    return True, Fraction(int(fits)), "ACCEPTED_RANGE" if fits else "KNOWN_VALUE_OUTSIDE_RANGE"


def _resolve_atom(atom: PreferenceAtom, seeker_view: ApprovedLocalView, candidate_view: ApprovedLocalView) -> tuple[bool, Fraction, Fraction, str, tuple[tuple[str, int], ...]]:
    dependencies = []
    for claim_id in atom.source_claim_ids:
        claim = seeker_view.by_id(claim_id)
        if claim is None:
            return False, Fraction(0), Fraction(0), "OWNER_PREFERENCE_EVIDENCE_UNUSABLE", ()
        dependencies.append(claim)
    candidate_claim = candidate_view.by_dimension(atom.field)
    known, fit, reason = _input_fit(atom, candidate_claim)
    if not known or candidate_claim is None:
        return False, Fraction(0), Fraction(0), reason, ()
    dependencies.append(candidate_claim)
    q = min(evidence_factor(claim) for claim in dependencies)
    return True, fit, q, reason, tuple((claim.claim_id, claim.version) for claim in dependencies)


def _intent_errors(owner: Owner, clock: datetime) -> tuple[str, ...]:
    intent = owner.intent
    problems = []
    if intent.purpose != "collaboration":
        problems.append("UNSUPPORTED_R0_PURPOSE")
    if not intent.current:
        problems.append("NO_EXPLICIT_CURRENT_INTENT")
    if not intent.valid_from <= clock < intent.expires_at:
        problems.append("INTENT_OUTSIDE_VALIDITY")
    if not intent.applicable_families:
        problems.append("NO_APPLICABLE_FAMILIES")
    for family in intent.applicable_families:
        atoms = intent.family_atoms(family)
        if not atoms or sum(atom.weight for atom in atoms) <= 0:
            problems.append("MISSING_VALID_ATOMS:" + family)
    return tuple(problems)


def assess_direction(seeker: Owner, candidate: Owner, as_of: datetime,
                     threshold: Fraction | str | None = None) -> DirectionalAssessment:
    """Qualification is based on F/C and hard facts; S only affects ranking."""
    clock = checked_time(as_of)
    tau = seeker.intent.threshold if threshold is None else exact(threshold, low=0, high=1)
    def outcome(status: str, reasons: tuple[str, ...], atoms: tuple[AtomContribution, ...] = (),
                coverage: Fraction = Fraction(0), preference: Fraction = Fraction(0), supported: Fraction = Fraction(0)) -> DirectionalAssessment:
        return DirectionalAssessment(seeker.owner_id, candidate.owner_id, status, reasons, coverage, preference, supported,
                                     preference + 1 - coverage, tau, atoms, clock, (seeker.intent.version, candidate.intent.version))
    own_errors = _intent_errors(seeker, clock)
    if own_errors:
        return outcome("invalid_intent", own_errors)
    candidate_errors = _intent_errors(candidate, clock)
    failures = []
    unknown = ["CANDIDATE_INTENT_UNAVAILABLE"] if candidate_errors else []
    if seeker.owner_id == candidate.owner_id:
        failures.append("SELF_MATCH")
    if seeker.intent.purpose != candidate.intent.purpose:
        failures.append("PURPOSE_MISMATCH")
    if candidate.owner_id in seeker.blocked_owner_ids or seeker.owner_id in candidate.blocked_owner_ids:
        failures.append("BLOCKED_PAIR")
    if seeker.intent.capacity <= len(seeker.existing_offer_owner_ids) or candidate.intent.capacity <= len(candidate.existing_offer_owner_ids):
        failures.append("CAPACITY_EXHAUSTED")
    seeker_view, candidate_view = resolve_usable(seeker, clock), resolve_usable(candidate, clock)
    for constraint in seeker.intent.hard_constraints:
        known, fit, _, _, _ = _resolve_atom(constraint, seeker_view, candidate_view)
        if not known:
            unknown.append("HARD_CONSTRAINT_UNKNOWN:" + constraint.atom_id)
        elif fit != 1:
            failures.append("HARD_CONSTRAINT_FAILED:" + constraint.atom_id)
    if failures:
        return outcome("not_eligible", tuple(failures + unknown))
    if unknown:
        return outcome("needs_information", tuple(unknown))
    policy = load_defaults()["scoring_fixture_policy"]
    family_weights = {family: exact(policy["weights"][family]) for family in seeker.intent.applicable_families}
    total_weight = sum(family_weights.values())
    atoms = []
    for family, raw_weight in family_weights.items():
        family_atoms = seeker.intent.family_atoms(family)
        atom_sum = sum(atom.weight for atom in family_atoms)
        for atom in family_atoms:
            weight = raw_weight / total_weight * atom.weight / atom_sum
            known, fit, q, reason, references = _resolve_atom(atom, seeker_view, candidate_view)
            atoms.append(AtomContribution(family, atom.atom_id, atom.field, weight, known, fit, q, reason, references))
    contributions = tuple(atoms)
    coverage = sum((atom.weight for atom in atoms if atom.known), Fraction(0))
    preference = sum((atom.weight * atom.fit for atom in atoms if atom.known), Fraction(0))
    supported = sum((atom.weight * atom.fit * atom.evidence_factor for atom in atoms if atom.known), Fraction(0))
    upper = preference + 1 - coverage
    if not 0 <= supported <= preference <= coverage <= 1 or not preference <= upper <= 1:
        raise ValueError("SCORING_INVARIANT: bounded exact score invariant violated")
    if coverage < exact(policy["minimum_coverage_each_direction"]):
        return outcome("insufficient_evidence", ("COVERAGE_BELOW_MINIMUM",), contributions, coverage, preference, supported)
    if preference >= tau:
        reason = "NO_SOFT_PREFERENCE_FILTER" if tau == 0 else "PREFERENCE_THRESHOLD_MET"
        return outcome("qualified_candidate", (reason,), contributions, coverage, preference, supported)
    if upper >= tau:
        return outcome("insufficient_evidence", ("SELECTIVITY_UNRESOLVED",), contributions, coverage, preference, supported)
    return outcome("not_recommended", ("PREFERENCE_THRESHOLD_UNREACHABLE",), contributions, coverage, preference, supported)


def assess_pair_offline(a: Owner, b: Owner, as_of: datetime,
                        threshold_a: Fraction | str | None = None,
                        threshold_b: Fraction | str | None = None) -> PairAssessment:
    """Offline fixture comparator. It is not an exchange of private peer scores."""
    return PairAssessment(assess_direction(a, b, as_of, threshold_a), assess_direction(b, a, as_of, threshold_b))
