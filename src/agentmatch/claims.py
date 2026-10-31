"""Resolve current, reviewed evidence without rewriting its provenance."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from fractions import Fraction
from typing import Any

from .config import load_defaults
from .models import (ApprovedLocalView, EvidenceClaim, Owner, Permission, SENSITIVE_FAMILIES,
                     FORBIDDEN_FAMILIES, checked_time, parse_value, ids, text_value)
from .numeric import exact

# Explicit supported sensitive fields, independent of any owner permission.
# A matching family permission alone cannot enable arbitrary new medical traits.
SENSITIVE_REGISTRY = {
    "politics.affiliation": frozenset({"local_use", "disclosure"}),
    "religion.practice": frozenset({"local_use", "disclosure"}),
    "sexuality.preference": frozenset({"local_use", "disclosure"}),
    "health.accessibility": frozenset({"local_use", "disclosure"}),
    "emotional.processing": frozenset({"local_use", "disclosure"}),
    "social_need.current": frozenset({"local_use", "disclosure"}),
}


def sensitivity(claim: EvidenceClaim) -> str | None:
    prefix = claim.dimension.partition(".")[0]
    if prefix in SENSITIVE_FAMILIES | FORBIDDEN_FAMILIES:
        return prefix
    return claim.sensitive_family


def permitted(owner: Owner, claim: EvidenceClaim, operation: str) -> bool:
    family = sensitivity(claim)
    if family in FORBIDDEN_FAMILIES:
        return False
    if family is None:
        return True
    # No guessed sexuality, health or affiliation in the R0 registry. These
    # narrow fields are current explicit statements only, even after opt-in.
    if claim.origin != "current_owner_statement":
        return False
    return (operation in SENSITIVE_REGISTRY.get(claim.dimension, frozenset())
            and Permission(family, owner.intent.purpose, operation) in owner.permissions)


def claim_unusable_reason(owner: Owner, claim: EvidenceClaim, as_of: datetime) -> str | None:
    as_of = checked_time(as_of)
    if claim.lifecycle != "active":
        return "CLAIM_" + claim.lifecycle.upper()
    if claim.approval != "approved_for_local_use":
        return "CLAIM_NOT_APPROVED"
    if claim.purpose != owner.intent.purpose:
        return "CLAIM_WRONG_PURPOSE"
    if not claim.valid_from <= as_of < claim.expires_at:
        return "CLAIM_OUTSIDE_VALIDITY"
    if claim.contradiction_refs:
        return "CLAIM_UNRESOLVED_CONTRADICTION"
    if claim.origin == "reported_event":
        return "PAST_EVENT_NOT_CURRENT_OFFER"
    if not permitted(owner, claim, "local_use"):
        return "CLAIM_OPERATION_NOT_PERMITTED"
    return None


def resolve_usable(owner: Owner, as_of: datetime) -> ApprovedLocalView:
    """Only the newest version can be current. Ambiguous dimensions abstain."""
    latest: dict[str, EvidenceClaim] = {}
    excluded: list[tuple[str, str]] = []
    for claim in owner.claims:
        if claim.claim_id not in latest or latest[claim.claim_id].version < claim.version:
            latest[claim.claim_id] = claim
    candidates = []
    for claim in owner.claims:
        if latest[claim.claim_id].version != claim.version:
            excluded.append((claim.claim_id + ":" + str(claim.version), "CLAIM_SUPERSEDED_VERSION"))
            continue
        reason = claim_unusable_reason(owner, claim, as_of)
        if reason:
            excluded.append((claim.claim_id + ":" + str(claim.version), reason))
        else:
            candidates.append(claim)
    counts: dict[str, int] = {}
    for claim in candidates:
        counts[claim.dimension] = counts.get(claim.dimension, 0) + 1
    usable = []
    for claim in candidates:
        if counts[claim.dimension] != 1:
            excluded.append((claim.claim_id + ":" + str(claim.version), "AMBIGUOUS_CURRENT_DIMENSION"))
        else:
            usable.append(claim)
    return ApprovedLocalView(owner.owner_id, owner.intent.purpose,
                             tuple(sorted(usable, key=lambda claim: claim.claim_id)), tuple(sorted(excluded)))


def evidence_factor(claim: EvidenceClaim) -> Fraction:
    policy = load_defaults()["scoring_fixture_policy"]["evidence_factors"]
    if claim.origin == "current_owner_statement":
        return exact(policy["current_owner_statement"])
    if claim.origin == "model_inferred":
        return exact(policy["approved_contextual_inference"])
    return Fraction(0)


def revise_claim(owner: Owner, claim_id: str, value: Any, source_refs: list[str], as_of: datetime,
                 context: str = "Owner-reviewed correction proposed", *, origin: str | None = None) -> Owner:
    records = [claim for claim in owner.claims if claim.claim_id == claim_id]
    if not records:
        raise ValueError("UNKNOWN_CLAIM: cannot revise missing claim")
    old = max(records, key=lambda claim: claim.version)
    clock = checked_time(as_of)
    if clock >= old.expires_at:
        raise ValueError("CLAIM_EXPIRED: new validity interval required for expired evidence")
    if origin is not None and origin not in {"current_owner_statement", "model_inferred", "reported_event"}:
        raise ValueError("INVALID_ORIGIN: unsupported correction origin")
    if old.version >= 1000000:
        raise ValueError("VERSION_LIMIT: claim revision exceeds bound")
    new = replace(old, version=old.version + 1, value=parse_value(value), approval="unreviewed", lifecycle="active",
                  valid_from=clock, source_refs=ids(source_refs), context=text_value(context), contradiction_refs=(),
                  origin=origin if origin is not None else old.origin)
    if new.complete is not None and not isinstance(new.value, tuple):
        new = replace(new, complete=None)
    historical = tuple(replace(claim, lifecycle="superseded") if claim.claim_id == claim_id and claim.lifecycle == "active" else claim for claim in owner.claims)
    return replace(owner, claims=tuple(sorted((*historical, new), key=lambda claim: (claim.claim_id, claim.version))))


def approve_claim(owner: Owner, claim_id: str, version: int) -> Owner:
    matches = [claim for claim in owner.claims if claim.claim_id == claim_id]
    if not matches or max(claim.version for claim in matches) != version:
        raise ValueError("STALE_REVIEW: only exact latest claim version may be approved")
    selected = next(claim for claim in matches if claim.version == version)
    if selected.lifecycle != "active":
        raise ValueError("CLAIM_INACTIVE: inactive evidence cannot be approved")
    return replace(owner, claims=tuple(replace(claim, approval="approved_for_local_use") if claim.claim_id == claim_id and claim.version == version else claim for claim in owner.claims))


def revoke_claim(owner: Owner, claim_id: str) -> Owner:
    if not any(claim.claim_id == claim_id for claim in owner.claims):
        raise ValueError("UNKNOWN_CLAIM: cannot revoke missing claim")
    return replace(owner, claims=tuple(replace(claim, lifecycle="revoked") if claim.claim_id == claim_id else claim for claim in owner.claims))


def invalidate_source(owner: Owner, source_ref: str) -> Owner:
    """R0 dependency invalidation; this does not erase a file or remote copy."""
    return replace(owner, claims=tuple(replace(claim, lifecycle="revoked") if source_ref in claim.source_refs else claim for claim in owner.claims))
