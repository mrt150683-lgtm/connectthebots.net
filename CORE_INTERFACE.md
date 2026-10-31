# R0 core interface

Python 3.11+; standard library only. This is a synthetic, in-memory reference.

- `models.load_owner(mapping) -> Owner`: strict schema version `0.1`.
- `scoring.assess_direction(seeker, candidate, as_of, threshold=None) -> DirectionalAssessment`.
- `scoring.assess_pair_offline(a, b, as_of, threshold_a=None, threshold_b=None) -> PairAssessment`.
- Both assessment records expose `.to_dict()` with exact reduced fractions and separate display strings.
- `ranking.evaluate_owner(owner, population, as_of, threshold=None) -> dict`: full population scan, reciprocal ranking, capacity/offer limits and private local reasons.
- `config.load_defaults() -> dict`: authoritative packaged defaults, numeric JSON read as Decimal.
- `config.selectivity(preset=None, custom=None) -> Fraction`: mutually exclusive, default selective.
- `claims.resolve_usable(owner, as_of) -> ApprovedLocalView`; `claims.revise_claim(...)` creates a fresh, unapproved version; `claims.revoke_claim(...)` makes dependent evaluations unusable.
- `consent.project_for_peer(owner, grant, peer, purpose, as_of) -> PeerClaim`: exact, scoped projection, no local source fields.

All clocks are explicit timezone-aware datetime objects. Unsupported real-person input is rejected by the R0 CLI. Claim and intent values use canonical tags and bounded decimal text, never float scores.

Owner JSON: schema_version, owner_id, alias, synthetic=true, intent, claims; optional permissions, blocked_owner_ids and existing_offer_owner_ids. Intent JSON: intent_id, version, purpose, current, valid_from, expires_at, capacity, threshold, atoms, hard_constraints; optional applicable_families. The four atom families are capability_complementarity, goal_problem_alignment, practical_workflow_fit and communication_fit. Each atom has atom_id, field, kind (membership/accepted/range) and wanted/accepted/minimum+maximum+unit respectively; optional weight and source_claim_ids. A hard constraint uses constraint_id instead of atom_id and no weight.

Claim JSON: claim_id, version, dimension, value, origin, approval, lifecycle, purpose, valid_from, expires_at, source_refs, context, contradiction_refs; optional complete, sensitive_family. A claim value is a string, boolean, string set (JSON array), or a bounded measure {amount: decimal text, unit: string}. Source references are private opaque strings. Source bodies are not claim fields. Only current approved active noncontradictory claims matching purpose can contribute. The complete flag applies to set claims: absent members in incomplete sets are unknown. Reported events are provenance, not current intent/offer evidence.

No unknown JSON keys are silently retained. Every peer object is separately constructed. Digests are local consistency identifiers and do not authenticate content or peers.
