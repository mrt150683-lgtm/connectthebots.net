"""Ports between the use cases and replaceable adapters.

``SearchIndex`` is the discovery seam. The bundled adapter is an in-memory
lexical index; a future indexed board service can implement the same protocol.
Whatever implements it receives only ``IndexDocument`` values, which are built
from approved notice payloads, and returns only ``SearchHit`` references. The
service re-reads each hit from the authoritative store and re-runs the
visibility gate, so an index that is stale, wrong or hostile can hide results
but cannot reveal a notice the viewer may not see.

``Clock`` is injected so every decision uses an explicit time. The local
server uses a simulated clock that only moves forward when asked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Protocol

from agentmatch.models import checked_time
from ..domain import vocabulary as v
from ..domain.briefs import Brief
from ..domain.introductions import Introduction
from ..domain.information import InformationRequest
from ..domain.publication import PublicationGrant, PublishedNotice
from ..domain.visibility import Board, OwnerProfile

MAX_RESULTS = 20
RESULT_LIMITS = (5, 10, 20)
MAX_QUERY_CHARS = 80


class Clock(Protocol):
    def now(self) -> datetime: ...


class SimulatedClock:
    """Deterministic demo clock. It never reads the wall clock and never goes back."""

    def __init__(self, start: datetime):
        self._now = checked_time(start)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta <= timedelta(0):
            raise ValueError("CLOCK_ROLLBACK: simulated time only moves forward")
        self._now = self._now + delta
        return self._now


class WorkbenchStore(Protocol):
    """Authoritative state. The bundled adapter keeps it in memory only."""
    owners: dict[str, OwnerProfile]
    briefs: dict[str, Brief]
    boards: dict[str, Board]
    grants: dict[str, PublicationGrant]
    notices: dict[str, PublishedNotice]
    introductions: dict[str, Introduction]
    information_requests: dict[str, InformationRequest]
    blocked_pairs: set[tuple[str, str]]
    activity: dict[str, list[dict[str, str]]]

    def next_id(self, kind: str) -> str: ...

    def boards_for(self, owner_id: str) -> tuple[Board, ...]: ...

    def notices_by_owner(self, owner_id: str) -> tuple[PublishedNotice, ...]: ...

    def notice_for(self, owner_id: str, board_id: str) -> PublishedNotice | None: ...

    def introductions_for(self, owner_id: str) -> tuple[Introduction, ...]: ...

    def log(self, owner_id: str, at: datetime, text: str) -> None: ...


@dataclass(frozen=True)
class IndexDocument:
    """Searchable derivative of one approved notice revision. No private fields."""
    notice_id: str
    revision: int
    board_id: str
    mode: str
    focus_areas: tuple[str, ...]
    offers: tuple[str, ...]
    basis: str
    venue: str | None
    text: str
    published_at: datetime


def index_document(*, notice_id: str, revision: int, board_id: str, payload: Mapping[str, Any],
                   published_at: datetime) -> IndexDocument:
    """Build the index entry from the approved payload alone."""
    labels = [v.CAPABILITIES[t] for t in payload["offers"]] + [v.FOCUS_AREAS[t] for t in payload["focus_areas"]]
    labels += [v.CAPABILITIES[payload["primary_need"]]] + [v.CAPABILITIES[t] for t in payload["also_seeks"]]
    text = " ".join([payload["title"], payload["desired_outcome"], payload["publisher_alias"], *labels])
    work = payload.get("working_arrangement") or {}
    return IndexDocument(notice_id, revision, board_id, payload["mode"], tuple(payload["focus_areas"]),
                         tuple(payload["offers"]), payload["basis"], work.get("venue"), text, published_at)


@dataclass(frozen=True)
class SearchQuery:
    """A bounded query. ``board_ids`` is the viewer's current board membership."""
    text: str
    board_ids: tuple[str, ...]
    mode: str | None = None
    focus_area: str | None = None
    offers: str | None = None
    basis: str | None = None
    venue: str | None = None
    limit: int = 10


@dataclass(frozen=True)
class SearchHit:
    notice_id: str
    revision: int


class SearchIndex(Protocol):
    def upsert(self, document: IndexDocument) -> None: ...

    def remove(self, notice_id: str) -> None: ...

    def query(self, query: SearchQuery) -> list[SearchHit]: ...


QUERY_FIELDS = frozenset({"text", "mode", "focus_area", "offers", "basis", "venue", "limit"})


class QueryError(ValueError):
    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("QUERY_INVALID")


def parse_query(data: Any, *, board_ids: tuple[str, ...]) -> SearchQuery:
    """Validate browser search input. Board scope comes from membership, never the request."""
    if not isinstance(data, dict) or set(data) - QUERY_FIELDS or not {"text", "limit"} <= set(data):
        raise QueryError([{"field": "query", "code": "INVALID_QUERY", "message": "Unsupported search request."}])
    errors = []
    text = data["text"]
    if not isinstance(text, str) or len(text.strip()) > MAX_QUERY_CHARS or any(ord(c) < 32 for c in text):
        errors.append({"field": "text", "code": "QUERY_TEXT", "message": f"Use at most {MAX_QUERY_CHARS} plain characters."})
        text = ""
    filters = {}
    for name, registry in (("mode", v.MODES), ("focus_area", v.FOCUS_AREAS), ("offers", v.CAPABILITIES),
                           ("basis", v.BASES), ("venue", v.VENUES)):
        value = data.get(name)
        if value is not None and (not isinstance(value, str) or value not in registry):
            errors.append({"field": name, "code": "FILTER_INVALID", "message": "Choose a listed option."})
            value = None
        filters[name] = value
    limit = data["limit"]
    if type(limit) is not int or limit not in RESULT_LIMITS:
        errors.append({"field": "limit", "code": "LIMIT_INVALID", "message": "Choose 5, 10 or 20 results."})
        limit = 10
    if errors:
        raise QueryError(errors)
    return SearchQuery(text.strip(), board_ids, limit=limit, **filters)
