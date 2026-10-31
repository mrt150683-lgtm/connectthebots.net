"""Bounded standing mission permissions for deterministic agent collaboration.

A mission delegates a finite set of queries, approved notice revisions and
first-step choices. It cannot publish claims, invent an outcome report, change
an introduction, add free text, or expand its candidate/community audience.
All policy checks use authoritative current records, not retrieved cards.

An administrator reviews the input and captured membership/notice bindings.
Agents can then perform allowed steps without another administrator call.
The runtime owns credential checks, transactional budgets and receipts; this
module owns only mission construction and current-use constraints.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any

from agentmatch_workbench.adapters.lexical_index import InMemoryLexicalIndex
from agentmatch_workbench.application.ports import index_document, parse_query
from agentmatch_workbench.domain.vocabulary import FIRST_STEPS

MISSION_OPERATIONS = frozenset({"search", "propose_introduction", "accept", "propose_step", "agree_step"})
MAX_MISSIONS = 16
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


class PolicyError(ValueError):
    pass


def _ref(value):
    if (not isinstance(value, dict) or set(value) != {"notice_id", "revision"}
            or not isinstance(value["notice_id"], str) or not _IDENTIFIER.fullmatch(value["notice_id"])
            or type(value["revision"]) is not int or value["revision"] < 1):
        raise PolicyError("MISSION_INVALID")
    return dict(value)


def _unique_strings(value, *, allowed=None, minimum=0, maximum=20):
    if (not isinstance(value, list) or not minimum <= len(value) <= maximum
            or any(type(v) is not str for v in value) or len(set(value)) != len(value)
            or (allowed is not None and not set(value) <= set(allowed))):
        raise PolicyError("MISSION_INVALID")
    return list(value)


def _binding(service, ref):
    notice = service.store.notices.get(ref["notice_id"])
    if notice is None or notice.revision != ref["revision"]:
        raise PolicyError("MISSION_NOTICE_UNAVAILABLE")
    grant = service.store.grants.get(notice.grant_id)
    if grant is None:
        raise PolicyError("MISSION_NOTICE_UNAVAILABLE")
    return {**ref, "owner_id": notice.owner_id, "board_id": notice.board_id,
            "grant_id": notice.grant_id, "brief_version": notice.brief_version,
            "payload_digest": notice.payload_digest, "approval_digest": grant.approval_digest}


def _check_binding(service, binding):
    current = _binding(service, {k: binding[k] for k in ("notice_id", "revision")})
    if current != binding:
        raise PolicyError("MISSION_NOTICE_CHANGED")
    return service.store.notices[binding["notice_id"]]


def check_authority(mission, service, owner_id, now, *, check_budget=True):
    """Fail closed after any audience expansion, source edit or lost notice access.

    Even open introductions are checked again here: the original workbench
    retains their historical context after some notices lose authority, which
    does not authorize an agent to make a new standing-policy commitment.
    """
    if (mission["revoked"] or not datetime.fromisoformat(mission["issued_at"]) <= now < datetime.fromisoformat(mission["expires_at"])
            or (check_budget and mission["uses"] >= mission["max_uses"])):
        raise PolicyError("MISSION_INACTIVE")
    memberships = {b.board_id: sorted(b.member_ids) for b in service.store.boards_for(owner_id)}
    if memberships != mission["board_memberships"]:
        raise PolicyError("MISSION_AUDIENCE_CHANGED")
    own = _check_binding(service, mission["own_notice"])
    if own.owner_id != owner_id:
        raise PolicyError("MISSION_NOTICE_UNAVAILABLE")
    candidates = []
    for binding in mission["candidate_notices"]:
        candidate = _check_binding(service, binding)
        if (candidate.owner_id == owner_id
                or service._unavailable(owner_id, candidate, candidate.revision) is not None
                or service._unavailable(candidate.owner_id, own, own.revision) is not None):
            raise PolicyError("MISSION_NOTICE_UNAVAILABLE")
        candidates.append(candidate)
    return own, candidates


def build_mission(service, credential, policy: dict[str, Any], *, mission_id: str, now: datetime):
    expected = {"own_notice", "board_ids", "candidate_notices", "operations", "search_queries", "first_steps", "max_uses", "ttl_seconds"}
    if not isinstance(policy, dict) or set(policy) != expected:
        raise PolicyError("MISSION_INVALID")
    own_ref = _ref(policy["own_notice"])
    boards = _unique_strings(policy["board_ids"], minimum=1, maximum=20)
    operations = _unique_strings(policy["operations"], allowed=MISSION_OPERATIONS, minimum=1, maximum=len(MISSION_OPERATIONS))
    if credential["role"] != "agent" or not set(operations) <= set(credential["scopes"]):
        raise PolicyError("SCOPE_REQUIRED")
    first_steps = _unique_strings(policy["first_steps"], allowed=FIRST_STEPS, maximum=len(FIRST_STEPS))
    if any(op != "search" for op in operations) and not first_steps:
        raise PolicyError("MISSION_INVALID")
    if type(policy["max_uses"]) is not int or not 1 <= policy["max_uses"] <= 100:
        raise PolicyError("MISSION_INVALID")
    if type(policy["ttl_seconds"]) is not int or not 1 <= policy["ttl_seconds"] <= 86400:
        raise PolicyError("MISSION_INVALID")
    refs = policy["candidate_notices"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
        raise PolicyError("MISSION_INVALID")
    refs = [_ref(ref) for ref in refs]
    if len({ref["notice_id"] for ref in refs}) != len(refs):
        raise PolicyError("MISSION_INVALID")
    memberships = {b.board_id: sorted(b.member_ids) for b in service.store.boards_for(credential["owner_id"])}
    if set(boards) != set(memberships):
        raise PolicyError("MISSION_BOARD_SCOPE_REQUIRED")
    queries = policy["search_queries"]
    if not isinstance(queries, list) or len(queries) > 10 or ("search" in operations and not queries):
        raise PolicyError("MISSION_INVALID")
    for query in queries:
        parse_query(query, board_ids=tuple(sorted(boards)))
    own = _binding(service, own_ref)
    targets = [_binding(service, ref) for ref in refs]
    if own["board_id"] not in boards or any(target["board_id"] != own["board_id"] for target in targets):
        raise PolicyError("MISSION_BOARD_SCOPE_REQUIRED")
    mission = {"id": mission_id, "credential_id": credential["id"], "owner_id": credential["owner_id"],
               "issued_at": now.isoformat(),
               "expires_at": min(now + timedelta(seconds=policy["ttl_seconds"]), datetime.fromisoformat(credential["expires_at"])).isoformat(),
               "max_uses": policy["max_uses"], "uses": 0, "revoked": False,
               "operations": sorted(operations), "own_notice": own, "candidate_notices": targets,
               "board_memberships": memberships, "search_queries": queries, "first_steps": first_steps}
    check_authority(mission, service, credential["owner_id"], now)
    return mission


def authorize_use(mission, service, owner_id, operation, arguments, now, *, recovery=False):
    """Authorize one use; a successful runtime transaction debits the budget.

    The no-free-text rule covers both submitted arguments AND existing peer
    context. A peer's separately approved edit cannot silently enlarge another
    owner's delegated mission terms.
    """
    if operation not in mission["operations"] or not isinstance(arguments, dict):
        raise PolicyError("MISSION_OPERATION_NOT_ALLOWED")
    # Recovery only re-reads an already spent search, bounded separately by the
    # runtime. It still checks expiry/revocation/all current scope constraints.
    if recovery and operation != "search":
        raise PolicyError("MISSION_OPERATION_NOT_ALLOWED")
    own, candidates = check_authority(mission, service, owner_id, now, check_budget=not recovery)
    targets = {(n.notice_id, n.revision): n for n in candidates}
    if operation == "search":
        if arguments not in mission["search_queries"]:
            raise PolicyError("MISSION_QUERY_NOT_ALLOWED")
        # Bound the actual retrieval set before service.search; filtering its
        # finished response could leave incorrect counts or consume a page with
        # undelegated candidates. The normal visibility gate still runs per hit.
        index = InMemoryLexicalIndex()
        for notice in candidates:
            index.upsert(index_document(notice_id=notice.notice_id, revision=notice.revision,
                                       board_id=notice.board_id, payload=notice.payload, published_at=notice.published_at))
        service.index = index
        return
    if operation == "propose_introduction":
        if (set(arguments) != {"notice_id", "revision", "first_step", "note"}
                or arguments["note"] != "" or arguments["first_step"] not in mission["first_steps"]):
            raise PolicyError("MISSION_TERMS_NOT_ALLOWED")
        if type(arguments["notice_id"]) is not str or type(arguments["revision"]) is not int:
            raise PolicyError("MISSION_CANDIDATE_NOT_ALLOWED")
        target = targets.get((arguments["notice_id"], arguments["revision"]))
        if target is None:
            raise PolicyError("MISSION_CANDIDATE_NOT_ALLOWED")
        selected = service._my_notice_for(owner_id, target.owner_id)
        if selected is None or (selected.notice_id, selected.revision) != (own.notice_id, own.revision):
            raise PolicyError("MISSION_NOTICE_CHANGED")
        return
    if (set(arguments) != {"intro_id", "data"} or not isinstance(arguments["intro_id"], str)
            or not isinstance(arguments["data"], dict)):
        raise PolicyError("MISSION_TERMS_NOT_ALLOWED")
    intro = service.store.introductions.get(arguments["intro_id"])
    if intro is None or owner_id not in intro.participants:
        raise PolicyError("MISSION_INTRODUCTION_NOT_ALLOWED")
    mine = intro.proposer_ref if intro.proposer_id == owner_id else intro.recipient_ref
    other = intro.recipient_ref if intro.proposer_id == owner_id else intro.proposer_ref
    if ((mine.notice_id, mine.revision) != (own.notice_id, own.revision)
            or (other.notice_id, other.revision) not in targets):
        raise PolicyError("MISSION_CANDIDATE_NOT_ALLOWED")
    if (intro.note != "" or intro.suggested_first_step not in mission["first_steps"]
            or intro.first_step.detail != ""
            or (intro.first_step.action is not None and intro.first_step.action not in mission["first_steps"])):
        raise PolicyError("MISSION_TERMS_NOT_ALLOWED")
    data = arguments["data"]
    if operation == "propose_step":
        if set(data) != {"action", "detail"} or data["detail"] != "" or data["action"] not in mission["first_steps"]:
            raise PolicyError("MISSION_TERMS_NOT_ALLOWED")
    elif operation == "agree_step":
        if set(data) != {"version"}:
            raise PolicyError("MISSION_TERMS_NOT_ALLOWED")
    elif operation == "accept":
        if set(data) != {"proposal_digest"}:
            raise PolicyError("MISSION_TERMS_NOT_ALLOWED")
    else:
        raise PolicyError("MISSION_OPERATION_NOT_ALLOWED")
