"""Small, explicit introduction projections for the fictional R0 simulator.

These objects contain only permitted display/topic fields. They never accept a
private profile or source store. SHA-256 values are consistency identifiers in
the simulation, not proof of identity, encryption, or truthful claims.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import load_defaults


class SimulationError(ValueError):
    """Stable, non-sensitive refusal code for an unsupported simulator effect."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SimulationError("TIME_MUST_BE_AWARE")
    return value.astimezone(timezone.utc)


def timestamp(value: datetime) -> str:
    return require_time(value).isoformat().replace("+00:00", "Z")


def bounded_text(value: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SimulationError("INVALID_TEXT")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise SimulationError("INVALID_UNICODE")
    return value


def canonical_bytes(value: Any) -> bytes:
    """Deterministic restricted R0 representation, not a general JCS library.

    Known schemas use ASCII keys, strings, booleans, null and safe integers.
    Arbitrary floats, Decimal objects, surrogate text and extension fields do
    not cross this boundary. R2 requires separate RFC 8785 conformance.
    """
    def validate(item: Any) -> None:
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            if abs(item) > 2**53 - 1:
                raise SimulationError("INTEGER_OUT_OF_RANGE")
            return
        if isinstance(item, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise SimulationError("INVALID_UNICODE")
            return
        if isinstance(item, (tuple, list)):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict):
            if any(not isinstance(key, str) or not key.isascii() for key in item):
                raise SimulationError("INVALID_CANONICAL_KEY")
            for child in item.values():
                validate(child)
            return
        raise SimulationError("INVALID_CANONICAL_VALUE")

    validate(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class DisplayIdentity:
    owner_id: str
    alias: str
    version: int
    purpose: str = "collaboration"

    @property
    def value_digest(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class DisplayGrant:
    owner_id: str
    peer_id: str
    purpose: str
    version: int
    value_digest: str
    expires_at: datetime
    revoked: bool = False


@dataclass(frozen=True)
class PeerDisplayIdentity:
    alias: str
    version: int

    def to_dict(self) -> dict[str, Any]:
        return {"alias": self.alias, "version": self.version, "identity_verified": False}


def project_display(identity: DisplayIdentity, grant: DisplayGrant, peer_id: str,
                    purpose: str, as_of: datetime) -> PeerDisplayIdentity:
    bounded_text(identity.alias, maximum=80)
    if (grant.revoked or require_time(as_of) >= require_time(grant.expires_at)
            or identity.owner_id != grant.owner_id or peer_id != grant.peer_id
            or purpose != identity.purpose or purpose != grant.purpose
            or identity.version != grant.version or identity.version < 1
            or identity.value_digest != grant.value_digest):
        raise SimulationError("DISPLAY_NOT_AUTHORISED")
    return PeerDisplayIdentity(identity.alias, identity.version)


@dataclass(frozen=True)
class ApprovedTopicStatement:
    """Already projected, exact audience-scoped fictional statement."""

    alias: str
    version: int
    owner_id: str
    peer_id: str
    purpose: str
    topic_id: str
    topic_title: str
    statement: str
    expires_at: datetime
    approved: bool = True
    revoked: bool = False
    sensitive: bool = False

    def usable(self, *, peer_id: str, purpose: str, as_of: datetime) -> bool:
        return (self.approved and not self.revoked and not self.sensitive
                and self.peer_id == peer_id and self.purpose == purpose
                and require_time(as_of) < require_time(self.expires_at))


@dataclass(frozen=True)
class TopicBridge:
    topic_id: str
    version: int
    kind: str
    relationship: str
    title: str
    statements: tuple[str, ...]
    opening_question: str

    def to_dict(self) -> dict[str, Any]:
        return {"topic_id": self.topic_id, "version": self.version,
                "kind": self.kind, "relationship": self.relationship,
                "title": self.title, "statements": list(self.statements),
                "opening_question": self.opening_question}


def grounded_bridges(left: Iterable[ApprovedTopicStatement], right: Iterable[ApprovedTopicStatement],
                     *, left_id: str, right_id: str, purpose: str,
                     as_of: datetime) -> tuple[TopicBridge, ...]:
    """Intersect exact permitted topic IDs; do not infer intensity or synonyms."""
    first = {item.topic_id: item for item in left
             if item.owner_id == left_id and item.usable(peer_id=right_id, purpose=purpose, as_of=as_of)}
    second = {item.topic_id: item for item in right
              if item.owner_id == right_id and item.usable(peer_id=left_id, purpose=purpose, as_of=as_of)}
    result: list[TopicBridge] = []
    for topic_id in sorted(first.keys() & second.keys()):
        a, b = first[topic_id], second[topic_id]
        if a.topic_title != b.topic_title:
            continue  # Contradictory labels are not silently merged.
        title = bounded_text(a.topic_title, maximum=160)
        result.append(TopicBridge(topic_id, 1, "evidence_supported", "shared_topic",
                                  title, (bounded_text(a.statement), bounded_text(b.statement)),
                                  f"What would you test first about {title}?"))
    return tuple(result[:load_defaults()["social"]["max_suggested_topic_threads"]])


def neutral_invitation(topic_id: str = "owner-invitation") -> TopicBridge:
    return TopicBridge(topic_id, 1, "owner_proposed", "not_claimed",
                       "An invitation to compare current projects", (),
                       "What small project would you enjoy comparing notes on?")


@dataclass(frozen=True)
class SharedIntroductionContext:
    context_id: str
    version: int
    purpose: str
    participant_ids: tuple[str, str]
    display_identities: tuple[PeerDisplayIdentity, PeerDisplayIdentity]
    topic: TopicBridge

    def to_dict(self) -> dict[str, Any]:
        return {"context_id": self.context_id, "version": self.version,
                "purpose": self.purpose, "participant_ids": list(self.participant_ids),
                "display_identities": [item.to_dict() for item in self.display_identities],
                "topic": self.topic.to_dict(), "label": "Introduction context"}

    @property
    def value_digest(self) -> str:
        return digest(self.to_dict())


def introduction_card(context: SharedIntroductionContext) -> dict[str, Any]:
    """A bounded peer projection, without local scores, source IDs or contacts."""
    return {"release": "R0", "synthetic_only": True,
            "context": context.to_dict(), "shared_context_digest": context.value_digest,
            "supported_thread_count": int(context.topic.kind == "evidence_supported"),
            "topic_note": ("Approved shared topic; no intensity inferred."
                           if context.topic.kind == "evidence_supported"
                           else "No supported common thread found. Owner-proposed invitation."),
            "human_messages": []}
