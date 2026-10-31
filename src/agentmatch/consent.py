"""Exact local authority and allowlisted projections. No transport effects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Any

from .claims import resolve_usable, permitted
from .models import EvidenceClaim, Owner, ClaimValue, value_json, checked_time, time_text, identifier, load_claim, bounded_int
from .numeric import canonical_json


def claim_digest(claim: EvidenceClaim) -> str:
    """Local-only identifier; includes source/context lineage, never sent."""
    payload = claim.to_dict()
    del payload["approval"]
    del payload["lifecycle"]
    return hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class DisclosureGrant:
    grant_id: str
    claim_id: str
    claim_version: int
    value_digest: str
    purpose: str
    peer_id: str
    audience_alias: str
    permitted_fields: tuple[str, ...]
    question_ids: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    owner_approval_event: str
    revoked: bool = False


@dataclass(frozen=True)
class PeerClaim:
    """There is deliberately no private claim/source ID or digest field."""
    alias: str
    version: int
    dimension: str
    value: ClaimValue
    purpose: str
    complete: bool | None
    provenance: str | None
    expires_at: datetime

    def to_dict(self) -> dict[str, Any]:
        record = {"alias": self.alias, "version": self.version, "dimension": self.dimension,
                  "value": value_json(self.value), "purpose": self.purpose, "expires_at": time_text(self.expires_at)}
        if self.complete is not None:
            record["complete"] = self.complete
        if self.provenance is not None:
            record["provenance"] = self.provenance
        return record


def project_for_peer(owner: Owner, grant: DisclosureGrant, peer: str, purpose: str, as_of: datetime,
                     *, question_id: str | None = None) -> PeerClaim:
    """Validate authority against the exact current version before each effect."""
    clock = checked_time(as_of)
    if not isinstance(grant, DisclosureGrant):
        raise ValueError("INVALID_GRANT: typed exact disclosure grant required")
    if grant.revoked or not checked_time(grant.issued_at) <= clock < checked_time(grant.expires_at):
        raise ValueError("GRANT_INACTIVE: expired, future or revoked authority")
    if peer != grant.peer_id or purpose != grant.purpose or purpose != owner.intent.purpose:
        raise ValueError("GRANT_SCOPE_MISMATCH: wrong audience or purpose")
    if peer in owner.blocked_owner_ids:
        raise ValueError("PEER_BLOCKED: no outgoing disclosure")
    identifier(grant.grant_id)
    identifier(grant.owner_approval_event)
    identifier(grant.audience_alias)
    bounded_int(grant.claim_version, 1, 1000000)
    if not isinstance(grant.revoked, bool):
        raise ValueError("INVALID_GRANT: revoked must be boolean")
    if grant.audience_alias == grant.claim_id:
        raise ValueError("PRIVATE_IDENTIFIER: audience alias must not reuse private claim ID")
    required = {"dimension", "value"}
    allowed = required | {"complete", "provenance"}
    fields = set(grant.permitted_fields)
    if not required <= fields or fields - allowed or len(fields) != len(grant.permitted_fields):
        raise ValueError("INVALID_PROJECTION_FIELDS: unsupported or missing disclosure fields")
    if question_id is not None and question_id not in grant.question_ids:
        raise ValueError("QUESTION_NOT_AUTHORISED: exact question not allowed")
    if not owner.intent.current or not owner.intent.valid_from <= clock < owner.intent.expires_at:
        raise ValueError("INTENT_INACTIVE: expired or withdrawn intent")
    claim = resolve_usable(owner, clock).by_id(grant.claim_id)
    if claim is None or claim.version != grant.claim_version or claim_digest(claim) != grant.value_digest:
        raise ValueError("STALE_DISCLOSURE: exact usable claim version/value required")
    # Even a directly constructed Python dataclass must pass the same bounded
    # value contract before it can cross the projection boundary.
    claim = load_claim(claim.to_dict())
    if grant.audience_alias in claim.source_refs or grant.audience_alias in claim.contradiction_refs:
        raise ValueError("PRIVATE_IDENTIFIER: audience alias cannot expose source references")
    if not permitted(owner, claim, "disclosure"):
        raise ValueError("DISCLOSURE_NOT_PERMITTED: registry and exact family permission required")
    if isinstance(claim.value, tuple) and "complete" not in fields:
        # A set without its completeness flag is an incomplete assertion to the recipient.
        complete = None
    else:
        complete = claim.complete if "complete" in fields else None
    provenance = None
    if "provenance" in fields:
        provenance = "owner_stated" if claim.origin == "current_owner_statement" else "owner_reviewed_inference"
    # Construct only allowed wire fields; never serialize the private claim.
    return PeerClaim(grant.audience_alias, claim.version, claim.dimension, claim.value, purpose, complete, provenance,
                     min(grant.expires_at, claim.expires_at, owner.intent.expires_at))
