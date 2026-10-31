"""Versioned data-only encoding for the fictional workflow snapshot.

There is no pickle, import-by-name, evaluation, or general object hook. The
closed registry is deliberately explicit: adding a domain type requires a
reviewable storage-format change. Search indexes are derived after loading.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

from agentmatch.channels import Acceptance, HumanChannel, HumanMessage, OutboxEntry
from agentmatch.models import checked_time
from agentmatch.topics import PeerDisplayIdentity, SharedIntroductionContext, TopicBridge
from agentmatch_workbench.adapters.lexical_index import InMemoryLexicalIndex
from agentmatch_workbench.adapters.memory_store import InMemoryStore
from agentmatch_workbench.application.ports import SimulatedClock, index_document
from agentmatch_workbench.application.service import WorkbenchService
from agentmatch_workbench.domain.briefs import Brief
from agentmatch_workbench.domain.information import InformationRequest
from agentmatch_workbench.domain.introductions import FirstStep, Introduction, NoticeRef
from agentmatch_workbench.domain.publication import PublicationGrant, PublishedNotice
from agentmatch_workbench.domain.visibility import Board, OwnerProfile

SCHEMA = "agentmatch-workflow-snapshot-1"
_TYPES = {cls.__name__: cls for cls in (
    InMemoryStore, Brief, InformationRequest, FirstStep, Introduction, NoticeRef,
    PublicationGrant, PublishedNotice, Board, OwnerProfile, Acceptance,
    HumanChannel, HumanMessage, OutboxEntry, PeerDisplayIdentity,
    SharedIntroductionContext, TopicBridge,
)}


def encode(value: Any) -> Any:
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, datetime):
        return {"type": "datetime", "value": checked_time(value).isoformat()}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, Fraction):
        return {"type": "fraction", "value": [value.numerator, value.denominator]}
    if is_dataclass(value) and type(value).__name__ in _TYPES and _TYPES[type(value).__name__] is type(value):
        return {"type": "record", "name": type(value).__name__,
                "value": {item.name: encode(getattr(value, item.name)) for item in fields(value)}}
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("SNAPSHOT_NON_STRING_KEY")
        return {"type": "mapping", "value": {key: encode(item) for key, item in value.items()}}
    if type(value) in (list, tuple, set, frozenset):
        return {"type": type(value).__name__, "value": [encode(item) for item in value]}
    raise ValueError("SNAPSHOT_UNSUPPORTED_TYPE")


def decode(value: Any, depth: int = 0) -> Any:
    if depth > 80:
        raise ValueError("SNAPSHOT_TOO_DEEP")
    if value is None or type(value) in (str, int, bool):
        return value
    if not isinstance(value, dict) or set(value) not in ({"type", "value"}, {"type", "name", "value"}):
        raise ValueError("SNAPSHOT_INVALID")
    kind, item = value["type"], value["value"]
    if kind == "record":
        cls = _TYPES.get(value.get("name"))
        if cls is InformationRequest and isinstance(item, dict) and set(item) == {f.name for f in fields(cls)} - {"accounted_at"}:
            # Schema-1 snapshots before rolling budgets had no accounting time.
            # Only this known additive field may be absent; runtime.open anchors
            # it to real first-seen UTC and commits the migration atomically.
            item = {**item, "accounted_at": None}
        if cls is None or not isinstance(item, dict) or set(item) != {f.name for f in fields(cls)}:
            raise ValueError("SNAPSHOT_UNKNOWN_RECORD")
        return cls(**{key: decode(child, depth + 1) for key, child in item.items()})
    if set(value) != {"type", "value"}:
        raise ValueError("SNAPSHOT_INVALID")
    if kind == "mapping" and isinstance(item, dict):
        return {key: decode(child, depth + 1) for key, child in item.items()}
    if kind in ("list", "tuple", "set", "frozenset") and isinstance(item, list):
        return {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}[kind](
            decode(child, depth + 1) for child in item)
    if kind == "datetime" and isinstance(item, str):
        return checked_time(datetime.fromisoformat(item))
    if kind == "decimal" and isinstance(item, str):
        result = Decimal(item)
        if not result.is_finite():
            raise ValueError("SNAPSHOT_INVALID_NUMBER")
        return result
    if kind == "fraction" and isinstance(item, list) and len(item) == 2 and all(type(n) is int for n in item):
        return Fraction(*item)
    raise ValueError("SNAPSHOT_INVALID")


def snapshot(service: WorkbenchService) -> dict[str, Any]:
    return {"schema": SCHEMA, "now": service.now().isoformat(), "store": encode(service.store)}


def restore(data: dict[str, Any]) -> WorkbenchService:
    if not isinstance(data, dict) or set(data) != {"schema", "now", "store"} or data["schema"] != SCHEMA:
        raise ValueError("SNAPSHOT_VERSION_UNSUPPORTED")
    store = decode(data["store"])
    if not isinstance(store, InMemoryStore):
        raise ValueError("SNAPSHOT_STORE_REQUIRED")
    index = InMemoryLexicalIndex()
    for notice in store.notices.values():
        index.upsert(index_document(notice_id=notice.notice_id, revision=notice.revision,
                                   board_id=notice.board_id, payload=notice.payload,
                                   published_at=notice.published_at))
    return WorkbenchService(store, index, SimulatedClock(checked_time(datetime.fromisoformat(data["now"]))), examples={})
