"""In-memory authoritative state for one local session. Nothing is persisted.

This is the storage adapter for the prototype. It is a plain container: every
rule lives in the domain and the service. Closing the process discards it, and
nothing here is encrypted. A real store is an R1 deliverable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from agentmatch.models import time_text
from ..domain.briefs import Brief
from ..domain.introductions import Introduction
from ..domain.information import InformationRequest
from ..domain.publication import PublicationGrant, PublishedNotice
from ..domain.visibility import Board, OwnerProfile


@dataclass
class InMemoryStore:
    """Implements ``application.ports.WorkbenchStore``."""

    owners: dict[str, OwnerProfile] = field(default_factory=dict)
    briefs: dict[str, Brief] = field(default_factory=dict)
    boards: dict[str, Board] = field(default_factory=dict)
    grants: dict[str, PublicationGrant] = field(default_factory=dict)
    notices: dict[str, PublishedNotice] = field(default_factory=dict)
    introductions: dict[str, Introduction] = field(default_factory=dict)
    information_requests: dict[str, InformationRequest] = field(default_factory=dict)
    blocked_pairs: set[tuple[str, str]] = field(default_factory=set)
    activity: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def next_id(self, kind: str) -> str:
        """Deterministic, opaque, local identifiers."""
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f"{kind}-{self.counters[kind]:04d}"

    def boards_for(self, owner_id: str) -> tuple[Board, ...]:
        return tuple(board for board in self.boards.values() if owner_id in board.member_ids)

    def notices_by_owner(self, owner_id: str) -> tuple[PublishedNotice, ...]:
        return tuple(sorted((n for n in self.notices.values() if n.owner_id == owner_id), key=lambda n: n.notice_id))

    def notice_for(self, owner_id: str, board_id: str) -> PublishedNotice | None:
        return next((n for n in self.notices.values() if n.owner_id == owner_id and n.board_id == board_id), None)

    def introductions_for(self, owner_id: str) -> tuple[Introduction, ...]:
        return tuple(sorted((i for i in self.introductions.values() if owner_id in i.participants),
                            key=lambda i: i.intro_id))

    def log(self, owner_id: str, at: datetime, text: str) -> None:
        self.activity.setdefault(owner_id, []).append({"at": time_text(at), "text": text})
