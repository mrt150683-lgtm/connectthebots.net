"""Full-scan reciprocal ranking, stable anchored ties and explicit capacity."""
from __future__ import annotations

from datetime import datetime
from fractions import Fraction
from typing import Any, Iterable

from .claims import resolve_usable, sensitivity
from .config import load_defaults
from .models import Owner, PairAssessment, checked_time, time_text
from .numeric import exact, fraction_record
from .scoring import assess_pair_offline


def problem_tags(owner: Owner, as_of: datetime) -> frozenset[str]:
    claim = resolve_usable(owner, as_of).by_dimension("intent.problem_tags")
    if claim is None or sensitivity(claim) is not None or not isinstance(claim.value, tuple):
        return frozenset()
    return frozenset(claim.value)


def novelty_for_pair(owner: Owner, candidate: Owner, frozen_pool: tuple[Owner, ...], as_of: datetime) -> Fraction:
    signature = problem_tags(owner, as_of) & problem_tags(candidate, as_of)
    if not signature:
        return Fraction(0)
    count = sum(signature <= problem_tags(other, as_of) for other in frozen_pool)
    return Fraction(1, 1 + count)


def rank_qualified(assessments: Iterable[PairAssessment], novelty: dict[str, Fraction] | None = None) -> list[PairAssessment]:
    """Tie groups are anchored to their highest score, not chained fuzziness."""
    novelty = novelty or {}
    pairs = [pair for pair in assessments if pair.qualifies]
    pairs.sort(key=lambda pair: (-(pair.reciprocal_supported_fit or Fraction(0)), pair.a_to_b.candidate_id))
    epsilon = exact(load_defaults()["scoring_fixture_policy"]["score_tie_epsilon"])
    result = []
    cursor = 0
    while cursor < len(pairs):
        anchor = pairs[cursor].reciprocal_supported_fit
        assert anchor is not None
        end = cursor + 1
        while end < len(pairs):
            score = pairs[end].reciprocal_supported_fit
            assert score is not None
            if anchor - score > epsilon:
                break
            end += 1
        group = pairs[cursor:end]
        group.sort(key=lambda pair: (-novelty.get(pair.a_to_b.candidate_id, Fraction(0)), pair.a_to_b.candidate_id))
        result.extend(group)
        cursor = end
    return result


def evaluate_owner(owner: Owner, population: Iterable[Owner], as_of: datetime,
                   threshold: Fraction | str | None = None) -> dict[str, Any]:
    clock = checked_time(as_of)
    owners = tuple(sorted(population, key=lambda item: item.owner_id))
    if len({item.owner_id for item in owners}) != len(owners):
        raise ValueError("DUPLICATE_OWNER: population identifiers repeat")
    # No pre-sampling or top-k retrieval is allowed before all decisions.
    candidates = tuple(item for item in owners if item.owner_id != owner.owner_id)
    pairs = [assess_pair_offline(owner, candidate, clock, threshold_a=threshold) for candidate in candidates]
    pool = tuple(candidate for candidate, pair in zip(candidates, pairs) if pair.a_to_b.status not in {"invalid_intent", "not_eligible", "needs_information"})
    novelty = {candidate.owner_id: novelty_for_pair(owner, candidate, pool, clock) for candidate in candidates}
    ranked = rank_qualified(pairs, novelty)
    contract = load_defaults()
    shortlist_limit = contract["protocol_pilot"]["max_candidates_shortlisted_per_run"]
    offer_limit = min(contract["r0"]["synthetic_offer_limit_per_run"], max(0, owner.intent.capacity - len(owner.existing_offer_owner_ids)))
    offered: list[str] = []
    records = []
    for rank, pair in enumerate(ranked, 1):
        candidate_id = pair.a_to_b.candidate_id
        if candidate_id in owner.existing_offer_owner_ids:
            outcome = "already_proposed"
        elif rank > shortlist_limit:
            outcome = "outside_shortlist"
        elif len(offered) >= offer_limit:
            outcome = "offer_capacity_exhausted"
        else:
            offered.append(candidate_id)
            outcome = "synthetic_offer_selected"
        records.append({"candidate_id": candidate_id, "rank": rank, "selection": outcome,
                        "reciprocal_supported_fit": fraction_record(pair.reciprocal_supported_fit or Fraction(0)),
                        "pool_novelty_tiebreak": fraction_record(novelty[candidate_id])})
    return {"schema_version": "0.1", "mode": "offline_synthetic_reference", "owner_id": owner.owner_id,
            "intent_id": owner.intent.intent_id, "as_of": time_text(clock), "contract_version": contract["contract_version"],
            "effective_threshold": fraction_record(owner.intent.threshold if threshold is None else exact(threshold, low=0, high=1)),
            "scanned_candidates": len(candidates), "qualified_pairs": len(ranked), "offer_limit": offer_limit,
            "offered_owner_ids": offered, "ranked_candidates": records, "assessments": [pair.to_dict() for pair in pairs],
            "visibility": "local_private_assessment", "ranking_note": "Reciprocal supported fit; novelty only within anchored score ties. Capacity is not person quality.",
            "claim": "No compatibility probability or human usefulness has been established."}
