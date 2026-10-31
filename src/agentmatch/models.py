"""Immutable, explicit private and peer records for the fictional R0 reference."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
import re
from typing import Any, Mapping

from .config import FAMILIES, selectivity
from .numeric import exact, decimal_text, fraction_record, MAX_ATOMS_PER_FAMILY

SCHEMA_VERSION = "0.1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_DIM = re.compile(r"^(?:offer|intent|workflow|communication|topic|identity|politics|religion|sexuality|health|emotional|social_need|diagnosis|intelligence|human_worth)\.[a-z][a-z0-9_-]{0,79}$")
ORIGINS = frozenset({"current_owner_statement", "reported_event", "model_inferred"})
APPROVALS = frozenset({"unreviewed", "approved_for_local_use", "rejected"})
LIFECYCLES = frozenset({"active", "superseded", "revoked"})
SENSITIVE_FAMILIES = frozenset({"politics", "religion", "sexuality", "health", "emotional", "social_need"})
FORBIDDEN_FAMILIES = frozenset({"diagnosis", "intelligence", "human_worth"})


def require_fields(data: Mapping[str, Any], required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(data, dict):
        raise ValueError("INVALID_SCHEMA: object required")
    missing, extra = required - data.keys(), data.keys() - required - optional
    if missing:
        raise ValueError("MISSING_FIELD: " + ",".join(sorted(missing)))
    if extra:
        raise ValueError("UNKNOWN_FIELD: " + ",".join(sorted(extra)))


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("INVALID_IDENTIFIER: expected bounded canonical identifier")
    return value


def text_value(value: Any, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("INVALID_TEXT: expected bounded text")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ValueError("INVALID_UNICODE: unpaired surrogate") from exc
    return value


def dimension(value: Any) -> str:
    if not isinstance(value, str) or not _DIM.fullmatch(value):
        raise ValueError("UNKNOWN_DIMENSION: field outside R0 registry namespaces")
    return value


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("INVALID_TIME: expected ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("INVALID_TIME: expected ISO-8601 timestamp") from exc
    return checked_time(parsed)


def checked_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("INVALID_TIME: timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def time_text(value: datetime) -> str:
    return checked_time(value).isoformat().replace("+00:00", "Z")


def bounded_int(value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("INTEGER_OUT_OF_RANGE: invalid bounded integer")
    return value


def ids(value: Any, *, maximum: int = 256) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("INVALID_LIST: expected bounded identifier list")
    output = tuple(identifier(item) for item in value)
    if len(output) != len(set(output)):
        raise ValueError("DUPLICATE_IDENTIFIER: duplicate list entry")
    return output


@dataclass(frozen=True)
class Measure:
    amount: Fraction
    unit: str

    def to_dict(self) -> dict[str, str]:
        return {"amount": decimal_text(self.amount), "unit": self.unit}


ClaimValue = str | bool | tuple[str, ...] | Measure


def parse_value(value: Any) -> ClaimValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return text_value(value)
    if isinstance(value, list):
        if len(value) > 128:
            raise ValueError("VALUE_TOO_LARGE: set exceeds 128 entries")
        output = tuple(sorted(text_value(item, maximum=160) for item in value))
        if len(set(output)) != len(output):
            raise ValueError("DUPLICATE_VALUE: set has duplicate entries")
        return output
    if isinstance(value, dict):
        require_fields(value, {"amount", "unit"})
        return Measure(exact(value["amount"]), identifier(value["unit"]))
    raise ValueError("INVALID_CLAIM_VALUE: scalar, set or typed measure required")


def value_json(value: ClaimValue) -> Any:
    if isinstance(value, Measure):
        return value.to_dict()
    if isinstance(value, tuple):
        return list(value)
    return value


@dataclass(frozen=True)
class EvidenceClaim:
    claim_id: str
    version: int
    dimension: str
    value: ClaimValue
    origin: str
    approval: str
    lifecycle: str
    purpose: str
    valid_from: datetime
    expires_at: datetime
    source_refs: tuple[str, ...]
    context: str
    contradiction_refs: tuple[str, ...] = ()
    complete: bool | None = None
    sensitive_family: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"claim_id": self.claim_id, "version": self.version, "dimension": self.dimension,
                  "value": value_json(self.value), "origin": self.origin, "approval": self.approval,
                  "lifecycle": self.lifecycle, "purpose": self.purpose, "valid_from": time_text(self.valid_from),
                  "expires_at": time_text(self.expires_at), "source_refs": list(self.source_refs),
                  "context": self.context, "contradiction_refs": list(self.contradiction_refs)}
        if self.complete is not None:
            result["complete"] = self.complete
        if self.sensitive_family is not None:
            result["sensitive_family"] = self.sensitive_family
        return result


def load_claim(data: dict[str, Any]) -> EvidenceClaim:
    required = {"claim_id", "version", "dimension", "value", "origin", "approval", "lifecycle", "purpose", "valid_from", "expires_at", "source_refs", "context", "contradiction_refs"}
    require_fields(data, required, {"complete", "sensitive_family"})
    if (not all(isinstance(data[key], str) for key in ("origin", "approval", "lifecycle"))
            or data["origin"] not in ORIGINS or data["approval"] not in APPROVALS or data["lifecycle"] not in LIFECYCLES):
        raise ValueError("INVALID_CLAIM_STATE: unknown origin, approval or lifecycle")
    complete = data.get("complete")
    if complete is not None and not isinstance(complete, bool):
        raise ValueError("INVALID_COMPLETENESS: complete must be boolean")
    value = parse_value(data["value"])
    if complete is not None and not isinstance(value, tuple):
        raise ValueError("INVALID_COMPLETENESS: only set claims have completeness")
    sensitive = data.get("sensitive_family")
    if sensitive is not None and (not isinstance(sensitive, str) or sensitive not in SENSITIVE_FAMILIES | FORBIDDEN_FAMILIES):
        raise ValueError("UNKNOWN_SENSITIVE_FAMILY: unsupported family")
    start, end = parse_time(data["valid_from"]), parse_time(data["expires_at"])
    if end <= start:
        raise ValueError("INVALID_VALIDITY: expiry must follow start")
    return EvidenceClaim(identifier(data["claim_id"]), bounded_int(data["version"], 1, 1000000), dimension(data["dimension"]), value,
                         data["origin"], data["approval"], data["lifecycle"], identifier(data["purpose"]), start, end,
                         ids(data["source_refs"]), text_value(data["context"]), ids(data["contradiction_refs"]), complete, sensitive)


@dataclass(frozen=True)
class PreferenceAtom:
    atom_id: str
    field: str
    kind: str
    wanted: str | None = None
    accepted: tuple[str | bool, ...] = ()
    minimum: Fraction | None = None
    maximum: Fraction | None = None
    unit: str | None = None
    weight: Fraction = Fraction(1)
    source_claim_ids: tuple[str, ...] = ()

    def to_dict(self, *, hard: bool = False) -> dict[str, Any]:
        output: dict[str, Any] = {"constraint_id" if hard else "atom_id": self.atom_id, "field": self.field, "kind": self.kind}
        if self.kind == "membership":
            output["wanted"] = self.wanted
        elif self.kind == "accepted":
            output["accepted"] = list(self.accepted)
        else:
            output.update(minimum=decimal_text(self.minimum or Fraction(0)), maximum=decimal_text(self.maximum or Fraction(0)), unit=self.unit)
        if not hard:
            output["weight"] = decimal_text(self.weight)
        if self.source_claim_ids:
            output["source_claim_ids"] = list(self.source_claim_ids)
        return output


def load_atom(data: dict[str, Any], *, hard: bool = False) -> PreferenceAtom:
    id_key = "constraint_id" if hard else "atom_id"
    if not isinstance(data, dict):
        raise ValueError("INVALID_ATOM: object required")
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in {"membership", "accepted", "range"}:
        raise ValueError("UNKNOWN_FIT_FUNCTION: unsupported atom kind")
    specific = {"membership": {"wanted"}, "accepted": {"accepted"}, "range": {"minimum", "maximum", "unit"}}[kind]
    require_fields(data, {id_key, "field", "kind"} | specific, {"source_claim_ids"} | (set() if hard else {"weight"}))
    wanted, accepted, low, high, unit = None, (), None, None, None
    if kind == "membership":
        wanted = text_value(data["wanted"], maximum=160)
    elif kind == "accepted":
        options = data["accepted"]
        if not isinstance(options, list) or not 1 <= len(options) <= 64 or any(not isinstance(item, (str, bool)) for item in options):
            raise ValueError("INVALID_ACCEPTED_VALUES: bounded nonempty scalar list required")
        accepted = tuple(text_value(item) if isinstance(item, str) else item for item in options)
        if len(accepted) != len(set(accepted)):
            raise ValueError("DUPLICATE_VALUE: accepted values repeat")
    else:
        low, high, unit = exact(data["minimum"]), exact(data["maximum"]), identifier(data["unit"])
        if low > high:
            raise ValueError("INVALID_RANGE: minimum exceeds maximum")
    return PreferenceAtom(identifier(data[id_key]), dimension(data["field"]), kind, wanted, accepted, low, high, unit,
                          exact(data.get("weight", "1"), low=0, high=10**9), ids(data.get("source_claim_ids", [])))


@dataclass(frozen=True)
class MatchIntent:
    intent_id: str
    version: int
    purpose: str
    current: bool
    valid_from: datetime
    expires_at: datetime
    capacity: int
    threshold: Fraction
    atoms: tuple[tuple[str, tuple[PreferenceAtom, ...]], ...]
    hard_constraints: tuple[PreferenceAtom, ...]
    applicable_families: tuple[str, ...] = FAMILIES

    def family_atoms(self, family: str) -> tuple[PreferenceAtom, ...]:
        return dict(self.atoms).get(family, ())

    def to_dict(self) -> dict[str, Any]:
        return {"intent_id": self.intent_id, "version": self.version, "purpose": self.purpose, "current": self.current,
                "valid_from": time_text(self.valid_from), "expires_at": time_text(self.expires_at), "capacity": self.capacity,
                "threshold": decimal_text(self.threshold), "atoms": {family: [atom.to_dict() for atom in atoms] for family, atoms in self.atoms},
                "hard_constraints": [atom.to_dict(hard=True) for atom in self.hard_constraints], "applicable_families": list(self.applicable_families)}


def load_intent(data: dict[str, Any]) -> MatchIntent:
    require_fields(data, {"intent_id", "version", "purpose", "current", "valid_from", "expires_at", "capacity", "threshold", "atoms", "hard_constraints"}, {"applicable_families"})
    if not isinstance(data["current"], bool):
        raise ValueError("INVALID_INTENT: current must be boolean")
    if not isinstance(data["atoms"], dict) or data["atoms"].keys() - set(FAMILIES):
        raise ValueError("UNKNOWN_FAMILY: unsupported atom family")
    atoms = []
    for family in FAMILIES:
        records = data["atoms"].get(family, [])
        if not isinstance(records, list) or len(records) > MAX_ATOMS_PER_FAMILY:
            raise ValueError("ATOM_LIMIT: family exceeds bounded atom count")
        parsed = tuple(sorted((load_atom(item) for item in records), key=lambda atom: atom.atom_id))
        if len({item.atom_id for item in parsed}) != len(parsed):
            raise ValueError("DUPLICATE_ATOM: atom identifiers repeat")
        atoms.append((family, parsed))
    hard = data["hard_constraints"]
    if not isinstance(hard, list) or len(hard) > 64:
        raise ValueError("CONSTRAINT_LIMIT: too many hard constraints")
    hard_parsed = tuple(sorted((load_atom(item, hard=True) for item in hard), key=lambda atom: atom.atom_id))
    if len({item.atom_id for item in hard_parsed}) != len(hard_parsed):
        raise ValueError("DUPLICATE_CONSTRAINT: hard identifiers repeat")
    applicable = ids(data.get("applicable_families", list(FAMILIES)), maximum=4)
    if not set(applicable) <= set(FAMILIES):
        raise ValueError("UNKNOWN_FAMILY: unsupported applicable family")
    start, end = parse_time(data["valid_from"]), parse_time(data["expires_at"])
    if end <= start:
        raise ValueError("INVALID_VALIDITY: intent expiry must follow start")
    return MatchIntent(identifier(data["intent_id"]), bounded_int(data["version"], 1, 1000000), identifier(data["purpose"]),
                       data["current"], start, end, bounded_int(data["capacity"], 0, 1000000), exact(data["threshold"], low=0, high=1),
                       tuple(atoms), hard_parsed, tuple(f for f in FAMILIES if f in applicable))


@dataclass(frozen=True)
class Permission:
    family: str
    purpose: str
    operation: str

    def to_dict(self) -> dict[str, str]:
        return {"family": self.family, "purpose": self.purpose, "operation": self.operation}


@dataclass(frozen=True)
class Owner:
    owner_id: str
    alias: str
    intent: MatchIntent
    claims: tuple[EvidenceClaim, ...]
    permissions: tuple[Permission, ...] = ()
    blocked_owner_ids: tuple[str, ...] = ()
    existing_offer_owner_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "owner_id": self.owner_id, "alias": self.alias, "synthetic": True,
                "intent": self.intent.to_dict(), "claims": [claim.to_dict() for claim in self.claims],
                "permissions": [permission.to_dict() for permission in self.permissions], "blocked_owner_ids": list(self.blocked_owner_ids),
                "existing_offer_owner_ids": list(self.existing_offer_owner_ids)}


def load_owner(data: dict[str, Any]) -> Owner:
    require_fields(data, {"schema_version", "owner_id", "alias", "synthetic", "intent", "claims"}, {"permissions", "blocked_owner_ids", "existing_offer_owner_ids"})
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("UNSUPPORTED_SCHEMA: expected owner schema 0.1")
    if data["synthetic"] is not True:
        raise ValueError("SYNTHETIC_ONLY: R0 does not process real owners")
    records = data["claims"]
    if not isinstance(records, list) or len(records) > 256:
        raise ValueError("CLAIM_LIMIT: expected bounded claim list")
    claims = tuple(sorted((load_claim(item) for item in records), key=lambda claim: (claim.claim_id, claim.version)))
    if len({(claim.claim_id, claim.version) for claim in claims}) != len(claims):
        raise ValueError("DUPLICATE_CLAIM: immutable claim version repeats")
    permissions = []
    raw_permissions = data.get("permissions", [])
    if not isinstance(raw_permissions, list) or len(raw_permissions) > 32:
        raise ValueError("PERMISSION_LIMIT: invalid permission list")
    for item in raw_permissions:
        require_fields(item, {"family", "purpose", "operation"})
        if (not isinstance(item["family"], str) or not isinstance(item["operation"], str)
                or item["family"] not in SENSITIVE_FAMILIES or item["operation"] not in {"local_use", "disclosure"}):
            raise ValueError("UNSUPPORTED_PERMISSION: unsupported operation or family")
        permission = Permission(item["family"], identifier(item["purpose"]), item["operation"])
        if permission in permissions:
            raise ValueError("DUPLICATE_PERMISSION: permission repeats")
        permissions.append(permission)
    return Owner(identifier(data["owner_id"]), text_value(data["alias"], maximum=120), load_intent(data["intent"]), claims, tuple(permissions),
                 ids(data.get("blocked_owner_ids", [])), ids(data.get("existing_offer_owner_ids", [])))


@dataclass(frozen=True)
class ApprovedLocalView:
    owner_id: str
    purpose: str
    claims: tuple[EvidenceClaim, ...]
    exclusions: tuple[tuple[str, str], ...]

    def by_dimension(self, field: str) -> EvidenceClaim | None:
        found = [claim for claim in self.claims if claim.dimension == field]
        return found[0] if len(found) == 1 else None

    def by_id(self, claim_id: str) -> EvidenceClaim | None:
        found = [claim for claim in self.claims if claim.claim_id == claim_id]
        return found[0] if len(found) == 1 else None


@dataclass(frozen=True)
class AtomContribution:
    family: str
    atom_id: str
    field: str
    weight: Fraction
    known: bool
    fit: Fraction
    evidence_factor: Fraction
    reason: str
    local_claim_refs: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"family": self.family, "atom_id": self.atom_id, "field": self.field, "weight": fraction_record(self.weight),
                "known": self.known, "fit": fraction_record(self.fit), "evidence_factor": fraction_record(self.evidence_factor),
                "coverage_contribution": fraction_record(self.weight if self.known else Fraction(0)),
                "preference_contribution": fraction_record(self.weight * self.fit if self.known else Fraction(0)),
                "supported_contribution": fraction_record(self.weight * self.fit * self.evidence_factor if self.known else Fraction(0)),
                "reason": self.reason, "local_claim_refs": [{"claim_id": cid, "version": version} for cid, version in self.local_claim_refs]}


@dataclass(frozen=True)
class DirectionalAssessment:
    seeker_id: str
    candidate_id: str
    status: str
    reasons: tuple[str, ...]
    coverage: Fraction
    preference_fit: Fraction
    supported_fit: Fraction
    preference_fit_upper_bound: Fraction
    threshold: Fraction
    atoms: tuple[AtomContribution, ...]
    as_of: datetime
    intent_versions: tuple[int, int]

    @property
    def qualifies(self) -> bool:
        return self.status == "qualified_candidate"

    def to_dict(self) -> dict[str, Any]:
        return {"seeker_id": self.seeker_id, "candidate_id": self.candidate_id, "status": self.status, "reasons": list(self.reasons),
                "coverage": fraction_record(self.coverage), "preference_fit": fraction_record(self.preference_fit),
                "supported_fit": fraction_record(self.supported_fit), "preference_fit_upper_bound": fraction_record(self.preference_fit_upper_bound),
                "threshold": fraction_record(self.threshold), "atoms": [atom.to_dict() for atom in self.atoms], "as_of": time_text(self.as_of),
                "intent_versions": list(self.intent_versions), "contract_version": "0.4.0", "mode": "offline_synthetic_reference",
                "score_note": "Experimental policy quantities, not compatibility probabilities.", "visibility": "local_private_assessment"}


@dataclass(frozen=True)
class PairAssessment:
    a_to_b: DirectionalAssessment
    b_to_a: DirectionalAssessment

    @property
    def qualifies(self) -> bool:
        return self.a_to_b.qualifies and self.b_to_a.qualifies

    @property
    def reciprocal_supported_fit(self) -> Fraction | None:
        return min(self.a_to_b.supported_fit, self.b_to_a.supported_fit) if self.qualifies else None

    def to_dict(self) -> dict[str, Any]:
        score = self.reciprocal_supported_fit
        return {"qualified_pair": self.qualifies, "a_to_b": self.a_to_b.to_dict(), "b_to_a": self.b_to_a.to_dict(),
                "reciprocal_supported_fit": fraction_record(score) if score is not None else None,
                "scope": "offline_reference_only_not_a_verified_network_joint_score"}
