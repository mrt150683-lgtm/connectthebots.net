"""Load the bundled fictional seed and build a ready service.

Seed notices are published through the same preview -> approve path as the
browser uses, at their recorded fictional times, so they pass every check a
live publication passes. Each such approval is a scripted fixture event that
stands in for the fictional owner's own approval; it is labelled that way in
their activity log.
"""
from __future__ import annotations

from datetime import datetime
from importlib.resources import files
from typing import Any

from agentmatch.models import parse_time
from agentmatch.numeric import strict_json_loads
from ..application.ports import SimulatedClock
from ..application.service import WorkbenchService
from ..domain.briefs import parse_brief
from ..domain.visibility import Board, OwnerProfile
from .lexical_index import InMemoryLexicalIndex
from .memory_store import InMemoryStore

SEED_SCHEMA = "workbench-seed-0.1"


class _SeedClock:
    """Settable clock used only while replaying the seed's recorded times."""

    def __init__(self) -> None:
        self.current: datetime | None = None

    def now(self) -> datetime:
        assert self.current is not None
        return self.current


def load_seed() -> dict[str, Any]:
    data = strict_json_loads(files("agentmatch_workbench").joinpath("data/seed.json").read_bytes())
    if not isinstance(data, dict) or data.get("schema_version") != SEED_SCHEMA or data.get("fictional") is not True:
        raise ValueError("INVALID_SEED: fictional workbench seed required")
    return data


def build_service(seed: dict[str, Any] | None = None, *, start: datetime | None = None) -> WorkbenchService:
    seed = seed or load_seed()
    store = InMemoryStore()
    for record in seed["boards"]:
        store.boards[record["board_id"]] = Board(record["board_id"], record["name"], record["description"],
                                                 tuple(sorted(record["member_ids"])))
    seeding = _SeedClock()
    service = WorkbenchService(store, InMemoryLexicalIndex(), seeding, examples=seed["examples"])
    events = []
    for owner in seed["owners"]:
        store.owners[owner["owner_id"]] = OwnerProfile(owner["owner_id"], owner["alias"])
        updated = parse_time(owner["brief_updated_at"])
        store.briefs[owner["owner_id"]] = parse_brief(owner["brief"], owner_id=owner["owner_id"], version=1, updated_at=updated)
        store.log(owner["owner_id"], updated, "Fictional seed: brief created (version 1).")
        for publication in owner["publications"]:
            events.append((parse_time(publication["approved_at"]), owner["owner_id"], publication))
    for approved_at, owner_id, publication in sorted(events, key=lambda item: (item[0], item[1])):
        seeding.current = approved_at
        preview = service.preview_publication(owner_id, {"board_id": publication["board_id"],
                                                         "expires_in_days": publication["expires_in_days"],
                                                         "include": publication["include"]})
        service.approve_publication(owner_id, {"board_id": publication["board_id"], "expires_at": preview["expires_at"],
                                               "include": publication["include"], "approval_digest": preview["approval_digest"]})
        store.log(owner_id, approved_at, "Fictional seed: the approval above is a scripted fixture event, not a person's action.")
    service.clock = SimulatedClock(start or parse_time(seed["as_of"]))
    return service
