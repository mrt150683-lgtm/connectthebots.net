"""Transport-free negotiation and social scenarios with fictional inputs only.

``transition`` is pure. ``ProtocolSimulator`` supplies an in-memory adapter and
owner quota ledger. Neither simulates cryptographic proof or durable storage.
All scenario IDs below are bundled; there is no transcript-path interpreter.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from .channels import (Acceptance, ChannelProposal, HumanChannel, acceptance_for,
                       open_channel)
from .coach import (CoachRequest, enable_private_coach, private_draft,
                    publish_private_draft)
from .config import load_defaults
from .topics import (ApprovedTopicStatement, DisplayGrant, DisplayIdentity,
                     SharedIntroductionContext, SimulationError, canonical_bytes,
                     digest, grounded_bridges, introduction_card,
                     neutral_invitation, project_display, require_time, timestamp)


REGISTERED_QUESTIONS = frozenset({"current_goal", "current_offered_capabilities", "availability", "communication_preference"})
TERMINAL_STATES = frozenset({"DECLINED", "EXPIRED", "WITHDRAWN", "BLOCKED", "ABORTED", "COMPLETE"})


@dataclass(frozen=True)
class ProtocolEvent:
    event_id: str
    event_type: str
    actor_id: str
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "r0.1", "event_id": self.event_id,
                "event_type": self.event_type, "actor_id": self.actor_id,
                "sequence": self.sequence, "payload": self.payload}


@dataclass(frozen=True)
class ProtocolState:
    participant_ids: tuple[str, str]
    purpose: str = "collaboration"
    status: str = "UNPAIRED"
    session_id: str | None = None
    setup_id: str | None = None
    deadline: datetime | None = None
    paired: bool = False
    hello_nonces: tuple[tuple[str, str], ...] = ()
    acknowledged_by: tuple[str, ...] = ()
    seen: tuple[tuple[str, str], ...] = ()
    sequences: tuple[tuple[str, int], ...] = ()
    question_count: int = 0
    round_number: int = 1
    turn_index: int = 0
    pending_question_id: str | None = None
    pending_sender: str | None = None
    pending_questions: tuple[str, ...] = ()
    policy_offered_by: tuple[str, ...] = ()
    permitted_questions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    peer_claims: tuple[tuple[str, str, int, str, datetime], ...] = ()
    proposal_kind: str | None = None
    proposal_digest: str | None = None
    proposal_expires_at: datetime | None = None
    proposal_acknowledged_by: tuple[str, ...] = ()
    accepted_by: tuple[str, ...] = ()
    acceptance_deadlines: tuple[tuple[str, datetime], ...] = ()
    contact_permissions: tuple[tuple[str, str, datetime], ...] = ()
    release_id: str | None = None
    release_payload_digest: str | None = None
    local_stop_reason: str | None = None

    @property
    def coordinator(self) -> str:
        return min(self.participant_ids)


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SimulationError("INVALID_EVENT_TIME")
    try:
        return require_time(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise SimulationError("INVALID_EVENT_TIME") from exc


def _fields(payload: dict[str, Any], required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - required - (optional or set()):
        raise SimulationError("INVALID_EVENT_FIELDS")


def _effect(kind: str, **data: Any) -> dict[str, Any]:
    return {"kind": kind, **data}


def transition(state: ProtocolState, event: ProtocolEvent, as_of: datetime) -> tuple[ProtocolState, tuple[dict[str, Any], ...]]:
    """Return next state and proposed fictional effects without doing I/O."""
    now = require_time(as_of)
    limits = load_defaults()["protocol_pilot"]
    if (not isinstance(event.event_id, str) or not isinstance(event.event_type, str)
            or not isinstance(event.actor_id, str) or not isinstance(event.payload, dict)):
        raise SimulationError("INVALID_EVENT_SCHEMA")
    if len(set(state.participant_ids)) != 2 or event.actor_id not in state.participant_ids:
        raise SimulationError("PEER_BINDING_MISMATCH")
    encoded = canonical_bytes(event.to_dict())
    if len(encoded) > limits["max_encoded_message_bytes"]:
        raise SimulationError("PROTOCOL_ENVELOPE_TOO_LARGE")
    fingerprint = digest(event.to_dict())
    seen = dict(state.seen)
    if event.event_id in seen:
        if seen[event.event_id] != fingerprint:
            return replace(state, status="ABORTED", local_stop_reason="CHANGED_DUPLICATE"), (_effect("generic_stop"),)
        return state, (_effect("duplicate_ignored"),)
    if state.status in TERMINAL_STATES:
        raise SimulationError("TERMINAL_SESSION")
    if type(event.sequence) is not int or event.sequence != dict(state.sequences).get(event.actor_id, 0) + 1:
        raise SimulationError("OUT_OF_ORDER_EVENT")
    if not event.event_id or len(event.event_id) > 128:
        raise SimulationError("INVALID_EVENT_ID")

    # Acceptance may be recorded after evaluation ends. No new disclosure or
    # opening/release effect can use that expired session.
    if state.deadline is not None and now >= state.deadline and event.event_type not in {"OWNER_ACCEPT", "DECLINE", "WITHDRAW", "BLOCK", "ABORT", "REFRESH_SESSION"}:
        return replace(state, status="EXPIRED", local_stop_reason="SESSION_EXPIRED"), (_effect("generic_stop"),)
    if event.event_type in {"PROPOSAL", "OWNER_ACCEPT", "CONTACT_RELEASE"} and any(now >= record[4] for record in state.peer_claims):
        raise SimulationError("PEER_CLAIMS_REQUIRE_REFRESH")

    kind, payload = event.event_type, event.payload
    updated = state
    effects: tuple[dict[str, Any], ...] = ()
    if kind == "PAIR":
        _fields(payload, {"fixture_invitation_verified"})
        if state.status != "UNPAIRED" or payload["fixture_invitation_verified"] is not True:
            raise SimulationError("INVITATION_REQUIRED")
        updated = replace(state, status="PAIRED_IDLE", paired=True)
    elif kind == "HELLO":
        _fields(payload, {"setup_id", "nonce"})
        if state.status not in {"PAIRED_IDLE", "OPENING"}:
            raise SimulationError("INVALID_TRANSITION")
        if not all(isinstance(payload[k], str) and 0 < len(payload[k]) <= 128 for k in ("setup_id", "nonce")):
            raise SimulationError("INVALID_HELLO")
        if state.setup_id is not None and state.setup_id != payload["setup_id"]:
            effects = (_effect("duplicate_setup_closed"),)
        else:
            nonces = dict(state.hello_nonces)
            if event.actor_id in nonces and nonces[event.actor_id] != payload["nonce"]:
                raise SimulationError("NONCE_CHANGED")
            nonces[event.actor_id] = payload["nonce"]
            updated = replace(state, status="OPENING", setup_id=payload["setup_id"], hello_nonces=tuple(sorted(nonces.items())))
    elif kind == "SESSION_OPEN":
        _fields(payload, {"session_id", "setup_id", "nonces"})
        if (state.status != "OPENING" or event.actor_id != state.coordinator
                or len(state.hello_nonces) != 2 or state.session_id is not None
                or not isinstance(payload["session_id"], str) or not 0 < len(payload["session_id"]) <= 128
                or payload["setup_id"] != state.setup_id
                or payload["nonces"] != dict(state.hello_nonces)):
            raise SimulationError("SESSION_OPEN_NOT_AUTHORISED")
        updated = replace(state, session_id=payload["session_id"], deadline=now + timedelta(seconds=limits["evaluation_session_ttl_seconds"]))
        effects = (_effect("reserve_session", session_id=payload["session_id"]),)
    elif kind == "SESSION_ACK":
        _fields(payload, {"session_id"})
        if state.status != "OPENING" or state.session_id is None or payload["session_id"] != state.session_id:
            raise SimulationError("SESSION_ACK_MISMATCH")
        owners = tuple(sorted(set(state.acknowledged_by) | {event.actor_id}))
        updated = replace(state, acknowledged_by=owners, status="EVALUATING" if len(owners) == 2 else "OPENING")
    elif kind == "POLICY_OFFER":
        _fields(payload, {"question_ids"})
        if state.status != "EVALUATING" or not isinstance(payload["question_ids"], list) or not set(payload["question_ids"]).issubset(REGISTERED_QUESTIONS):
            raise SimulationError("POLICY_NOT_SUPPORTED")
        # R0 uses a fixed nonsensitive registry; this is not an arbitrary predicate API.
        policies = dict(state.permitted_questions)
        policies[event.actor_id] = tuple(payload["question_ids"])
        updated = replace(state, policy_offered_by=tuple(sorted(set(state.policy_offered_by) | {event.actor_id})),
                          permitted_questions=tuple(sorted(policies.items())))
    elif kind == "CLAIM_EXCHANGE":
        _fields(payload, {"claims"})
        if state.status != "EVALUATING" or len(state.policy_offered_by) != 2 or not isinstance(payload["claims"], list):
            raise SimulationError("CLAIM_EXCHANGE_NOT_PERMITTED")
        records = list(state.peer_claims)
        for claim in payload["claims"]:
            _fields(claim, {"alias", "version", "dimension", "value", "provenance", "expires_at"})
            if (not isinstance(claim["alias"], str) or len(claim["alias"]) > 128
                    or type(claim["version"]) is not int or claim["version"] < 1
                    or claim["dimension"] not in REGISTERED_QUESTIONS
                    or claim["provenance"] not in {"owner_stated", "owner_reviewed_inference"}
                    or _parse_time(claim["expires_at"]) <= now):
                raise SimulationError("INVALID_PEER_CLAIM")
            value = claim["value"]
            if not isinstance(value, (str, bool, list)) or (isinstance(value, list) and not all(isinstance(v, str) for v in value)):
                raise SimulationError("INVALID_PEER_CLAIM")
            record = (event.actor_id, claim["alias"], claim["version"], digest(claim), _parse_time(claim["expires_at"]))
            same = next((r for r in records if r[:3] == record[:3]), None)
            if same is not None and same != record:
                raise SimulationError("PEER_CLAIM_VERSION_CHANGED")
            if same is None:
                records.append(record)
        updated = replace(state, peer_claims=tuple(records))
        effects = (_effect("fictional_claims_received", count=len(payload["claims"])),)
    elif kind == "CLAIM_WITHDRAW":
        _fields(payload, {"aliases"})
        if state.status not in {"EVALUATING", "PROPOSAL_PENDING", "LOCAL_ACCEPTED", "PEER_ACCEPTED", "MUTUAL_ACCEPTED"} or not isinstance(payload["aliases"], list):
            raise SimulationError("CLAIM_WITHDRAWAL_NOT_PERMITTED")
        remove: set[tuple[str, int]] = set()
        for item in payload["aliases"]:
            _fields(item, {"alias", "version"})
            if not any(record[:3] == (event.actor_id, item["alias"], item["version"]) for record in state.peer_claims):
                raise SimulationError("UNKNOWN_PEER_CLAIM_WITHDRAWAL")
            remove.add((item["alias"], item["version"]))
        records = tuple(record for record in state.peer_claims if not (record[0] == event.actor_id and (record[1], record[2]) in remove))
        updated = replace(state, peer_claims=records, accepted_by=(), acceptance_deadlines=(),
                          status="WITHDRAWN" if state.proposal_digest is not None else state.status)
        effects = (_effect("pending_claim_dependencies_invalidated"),)
    elif kind == "QUESTION":
        _fields(payload, {"question_ids", "ordinal", "round"})
        questions = payload["question_ids"]
        order = tuple(sorted(state.participant_ids))
        receiver = next(p for p in state.participant_ids if p != event.actor_id)
        if (state.status != "EVALUATING" or len(state.policy_offered_by) != 2
                or state.pending_question_id is not None or event.actor_id != order[state.turn_index]
                or type(payload["ordinal"]) is not int or type(payload["round"]) is not int
                or payload["ordinal"] != state.question_count + 1 or payload["round"] != state.round_number
                or state.round_number > limits["max_clarification_rounds_per_pair"]
                or not isinstance(questions, list) or not all(isinstance(q, str) for q in questions)
                or not set(questions).issubset(REGISTERED_QUESTIONS)
                or not set(questions).issubset(dict(state.permitted_questions).get(receiver, ()))):
            raise SimulationError("QUESTION_NOT_PERMITTED")
        if state.question_count + len(questions) > limits["max_questions_total_per_pair"]:
            raise SimulationError("PAIR_QUESTION_BUDGET_EXHAUSTED")
        updated = replace(state, question_count=state.question_count + len(questions),
                          pending_question_id=event.event_id, pending_sender=event.actor_id,
                          pending_questions=tuple(questions))
        effects = (_effect("reserve_questions", sender=event.actor_id,
                           receiver=next(p for p in state.participant_ids if p != event.actor_id), count=len(questions)),)
    elif kind == "ANSWER":
        _fields(payload, {"question_event_id", "items"})
        if (state.status != "EVALUATING" or state.pending_question_id != payload["question_event_id"]
                or state.pending_sender == event.actor_id or not isinstance(payload["items"], list)
                or len(payload["items"]) != len(state.pending_questions)):
            raise SimulationError("ANSWER_NOT_CORRELATED")
        for question, item in zip(state.pending_questions, payload["items"]):
            if not isinstance(item, dict) or item.get("kind") not in {"value", "unavailable"}:
                raise SimulationError("INVALID_ANSWER")
            if item["kind"] == "unavailable":
                _fields(item, {"kind"})
            else:
                _fields(item, {"kind", "value"})
                value = item["value"]
                if question == "current_offered_capabilities":
                    valid = isinstance(value, list) and all(isinstance(v, str) and len(v) <= 128 for v in value)
                elif question == "availability":
                    valid = isinstance(value, (str, bool))
                else:
                    valid = isinstance(value, str)
                if not valid:
                    raise SimulationError("INVALID_ANSWER")
        next_turn = 1 - state.turn_index
        updated = replace(state, pending_question_id=None, pending_sender=None, pending_questions=(), turn_index=next_turn,
                          round_number=state.round_number + int(next_turn == 0))
    elif kind == "PROPOSAL":
        _fields(payload, {"proposal_kind", "proposal_digest", "expires_at", "both_locally_willing"})
        if (state.status != "EVALUATING" or state.pending_question_id is not None
                or payload["proposal_kind"] not in {"channel_open", "contact_release"}
                or payload["both_locally_willing"] is not True
                or _parse_time(payload["expires_at"]) <= now):
            raise SimulationError("PROPOSAL_NOT_AUTHORISED")
        updated = replace(state, status="PROPOSAL_PENDING", proposal_kind=payload["proposal_kind"],
                          proposal_digest=payload["proposal_digest"], proposal_expires_at=_parse_time(payload["expires_at"]),
                          proposal_acknowledged_by=(event.actor_id,))
    elif kind == "PROPOSAL_ACK":
        _fields(payload, {"proposal_kind", "proposal_digest"})
        if (state.status not in {"PROPOSAL_PENDING", "LOCAL_ACCEPTED", "PEER_ACCEPTED", "MUTUAL_ACCEPTED"}
                or payload["proposal_kind"] != state.proposal_kind or payload["proposal_digest"] != state.proposal_digest):
            raise SimulationError("PROPOSAL_ACK_MISMATCH")
        updated = replace(state, proposal_acknowledged_by=tuple(sorted(set(state.proposal_acknowledged_by) | {event.actor_id})))
    elif kind == "OWNER_ACCEPT":
        _fields(payload, {"proposal_kind", "proposal_digest", "authorization_ref", "expires_at"})
        if (state.status not in {"PROPOSAL_PENDING", "LOCAL_ACCEPTED", "PEER_ACCEPTED", "MUTUAL_ACCEPTED"}
                or state.proposal_expires_at is None or now >= state.proposal_expires_at
                or payload["proposal_kind"] != state.proposal_kind or payload["proposal_digest"] != state.proposal_digest
                or now >= _parse_time(payload["expires_at"])):
            raise SimulationError("ACCEPTANCE_NOT_CURRENT_OR_EXACT")
        owners = tuple(sorted(set(state.accepted_by) | {event.actor_id}))
        deadlines = dict(state.acceptance_deadlines)
        deadlines[event.actor_id] = _parse_time(payload["expires_at"])
        updated = replace(state, accepted_by=owners, acceptance_deadlines=tuple(sorted(deadlines.items())),
                          status="MUTUAL_ACCEPTED" if len(owners) == 2 else "LOCAL_ACCEPTED")
    elif kind == "REFRESH_SESSION":
        _fields(payload, {"fixture_same_peer_binding", "new_setup_id"})
        if not state.paired or payload["fixture_same_peer_binding"] is not True or payload["new_setup_id"] == state.setup_id:
            raise SimulationError("FRESH_BINDING_REQUIRED")
        updated = replace(state, setup_id=payload["new_setup_id"], deadline=now + timedelta(seconds=limits["evaluation_session_ttl_seconds"]))
        effects = (_effect("fixture_session_refreshed"),)
    elif kind == "CONTACT_RELEASE":
        _fields(payload, {"release_id", "proposal_digest", "fictional_contact", "authority_current"})
        if (state.status not in {"MUTUAL_ACCEPTED", "SENT_UNACKNOWLEDGED"}
                or state.proposal_kind != "contact_release" or payload["proposal_digest"] != state.proposal_digest
                or state.proposal_expires_at is None or now >= state.proposal_expires_at
                or payload["authority_current"] is not True
                or len(state.acceptance_deadlines) != 2
                or any(now >= deadline for _, deadline in state.acceptance_deadlines)
                or not isinstance(payload["fictional_contact"], str) or not payload["fictional_contact"].endswith("@example.invalid")):
            raise SimulationError("CONTACT_RELEASE_NOT_AUTHORISED")
        current_digest = digest({"value": payload["fictional_contact"], "proposal": state.proposal_digest})
        permissions = {owner: (value_digest, deadline) for owner, value_digest, deadline in state.contact_permissions}
        if event.actor_id not in permissions or permissions[event.actor_id][0] != current_digest or now >= permissions[event.actor_id][1]:
            raise SimulationError("EXACT_CONTACT_PERMISSION_REQUIRED")
        if state.release_id is not None and (state.release_id != payload["release_id"] or state.release_payload_digest != current_digest):
            raise SimulationError("CONTACT_VALUE_CHANGED")
        fresh = state.release_id is None
        updated = replace(state, status="SENT_UNACKNOWLEDGED", release_id=payload["release_id"], release_payload_digest=current_digest)
        effects = (_effect("would_release", owner=event.actor_id, release_id=payload["release_id"], reserve=fresh,
                           transmission_performed=False),)
    elif kind == "RELEASE_ACK":
        _fields(payload, {"release_id"})
        if state.status != "SENT_UNACKNOWLEDGED" or payload["release_id"] != state.release_id:
            raise SimulationError("RELEASE_ACK_MISMATCH")
        updated = replace(state, status="COMPLETE")
        effects = (_effect("fictional_receipt", read_confirmation=False),)
    elif kind in {"DECLINE", "ABORT", "WITHDRAW", "BLOCK"}:
        _fields(payload, set(), {"local_reason"})
        status = {"DECLINE": "DECLINED", "ABORT": "ABORTED", "WITHDRAW": "WITHDRAWN", "BLOCK": "BLOCKED"}[kind]
        updated = replace(state, status=status, local_stop_reason=payload.get("local_reason"))
        effects = (_effect("generic_stop"),)
    else:
        raise SimulationError("UNKNOWN_EVENT_TYPE")

    sequences = dict(updated.sequences)
    sequences[event.actor_id] = event.sequence
    updated = replace(updated, seen=updated.seen + ((event.event_id, fingerprint),), sequences=tuple(sorted(sequences.items())))
    return updated, effects


class BudgetLedger:
    """Owner-wide UTC-day reservations, atomic only inside this R0 process."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.daily: dict[tuple[str, str, str], int] = {}
        self.active: dict[tuple[str, str, str], str] = {}
        self.release_ids: set[tuple[str, str]] = set()

    def _key(self, owner: str, counter: str, as_of: datetime) -> tuple[str, str, str]:
        return owner, require_time(as_of).date().isoformat(), counter

    def reserve_session(self, owners: tuple[str, str], purpose: str, session_id: str, as_of: datetime) -> None:
        limits = load_defaults()["protocol_pilot"]
        with self._lock:
            pair_key = (*sorted(owners), purpose)
            if pair_key in self.active:
                if self.active[pair_key] == session_id:
                    return
                raise SimulationError("PAIR_SESSION_ALREADY_ACTIVE")
            for owner in owners:
                if sum(owner in key[:2] for key in self.active) >= limits["max_active_sessions_per_owner"]:
                    raise SimulationError("OWNER_ACTIVE_SESSION_LIMIT")
                if self.daily.get(self._key(owner, "sessions", as_of), 0) >= limits["max_new_sessions_per_owner_per_utc_day"]:
                    raise SimulationError("OWNER_DAILY_SESSION_LIMIT")
            self.active[pair_key] = session_id
            for owner in owners:
                key = self._key(owner, "sessions", as_of)
                self.daily[key] = self.daily.get(key, 0) + 1

    def close_session(self, owners: tuple[str, str], purpose: str) -> None:
        with self._lock:
            self.active.pop((*sorted(owners), purpose), None)

    def reserve_questions(self, sender: str, receiver: str, count: int, as_of: datetime) -> None:
        limits = load_defaults()["protocol_pilot"]
        if type(count) is not int or count < 0:
            raise SimulationError("INVALID_QUESTION_COUNT")
        with self._lock:
            a, b = self._key(sender, "outbound_questions", as_of), self._key(receiver, "inbound_answers", as_of)
            if (self.daily.get(a, 0) + count > limits["max_outbound_questions_per_owner_per_utc_day"]
                    or self.daily.get(b, 0) + count > limits["max_inbound_questions_answered_per_owner_per_utc_day"]):
                raise SimulationError("OWNER_DAILY_QUESTION_LIMIT")
            self.daily[a], self.daily[b] = self.daily.get(a, 0) + count, self.daily.get(b, 0) + count

    def reserve_release(self, owner: str, release_id: str, as_of: datetime) -> None:
        with self._lock:
            if (owner, release_id) in self.release_ids:
                return
            key = self._key(owner, "contact_releases", as_of)
            if self.daily.get(key, 0) >= load_defaults()["protocol_pilot"]["max_contact_releases_per_owner_per_utc_day"]:
                raise SimulationError("OWNER_DAILY_RELEASE_LIMIT")
            self.daily[key] = self.daily.get(key, 0) + 1
            self.release_ids.add((owner, release_id))

    def summary(self) -> list[dict[str, Any]]:
        return [{"owner_id": key[0], "utc_day": key[1], "counter": key[2], "value": value}
                for key, value in sorted(self.daily.items())]


@dataclass
class RunBudget:
    """The per-run shortlist/probe caps are separate from daily reservations."""

    shortlisted: tuple[str, ...] = ()
    probed: set[str] = field(default_factory=set)

    def set_shortlist(self, candidate_ids: tuple[str, ...]) -> None:
        if len(candidate_ids) != len(set(candidate_ids)):
            raise SimulationError("DUPLICATE_SHORTLIST_CANDIDATE")
        if len(candidate_ids) > load_defaults()["protocol_pilot"]["max_candidates_shortlisted_per_run"]:
            raise SimulationError("SHORTLIST_LIMIT")
        self.shortlisted = candidate_ids

    def reserve_probe(self, peer_id: str) -> None:
        if peer_id not in self.shortlisted:
            raise SimulationError("PEER_NOT_SHORTLISTED")
        if peer_id in self.probed:
            return
        if len(self.probed) >= load_defaults()["protocol_pilot"]["max_peers_probed_per_run"]:
            raise SimulationError("PROBE_LIMIT")
        self.probed.add(peer_id)


class ProtocolSimulator:
    def __init__(self, participant_ids: tuple[str, str] = ("fixture-key-a", "fixture-key-b"),
                 ledger: BudgetLedger | None = None) -> None:
        self.state = ProtocolState(participant_ids)
        self.ledger = ledger or BudgetLedger()
        self.trace: list[dict[str, Any]] = []
        self._last_time: datetime | None = None
        self._lock = RLock()

    def apply(self, event: ProtocolEvent, as_of: datetime) -> tuple[dict[str, Any], ...]:
        with self._lock:
            now = require_time(as_of)
            if self._last_time is not None and now < self._last_time:
                raise SimulationError("CLOCK_ROLLBACK")
            next_state, effects = transition(self.state, event, now)
            for effect in effects:
                if effect["kind"] == "reserve_session":
                    self.ledger.reserve_session(self.state.participant_ids, self.state.purpose, effect["session_id"], now)
                elif effect["kind"] == "reserve_questions":
                    self.ledger.reserve_questions(effect["sender"], effect["receiver"], effect["count"], now)
                elif effect["kind"] == "would_release" and effect["reserve"]:
                    self.ledger.reserve_release(effect["owner"], effect["release_id"], now)
            self.state, self._last_time = next_state, now
            if next_state.status in TERMINAL_STATES:
                self.ledger.close_session(next_state.participant_ids, next_state.purpose)
            self.trace.append({"event_id": event.event_id, "event_type": event.event_type,
                               "status": next_state.status, "effects": list(effects)})
            return effects

    def event(self, kind: str, actor: str, payload: dict[str, Any], as_of: datetime) -> ProtocolEvent:
        event = ProtocolEvent(f"event-{len(self.state.seen) + 1}", kind, actor,
                              dict(self.state.sequences).get(actor, 0) + 1, payload)
        self.apply(event, as_of)
        return event

    def authorise_fictional_contact(self, owner_id: str, contact: str, *, expires_at: datetime) -> None:
        """Trusted scripted owner action, never a wire message or prompt effect."""
        with self._lock:
            if (owner_id not in self.state.participant_ids or self.state.proposal_kind != "contact_release"
                    or self.state.proposal_digest is None or not contact.endswith("@example.invalid")):
                raise SimulationError("INVALID_CONTACT_PERMISSION")
            permission = (owner_id, digest({"value": contact, "proposal": self.state.proposal_digest}), require_time(expires_at))
            others = tuple(item for item in self.state.contact_permissions if item[0] != owner_id)
            self.state = replace(self.state, contact_permissions=others + (permission,))

    def revoke_fictional_contact(self, owner_id: str) -> None:
        with self._lock:
            self.state = replace(self.state, contact_permissions=tuple(item for item in self.state.contact_permissions if item[0] != owner_id))


@dataclass(frozen=True)
class FictionalContactEnvelope:
    release_id: str
    sender_id: str
    recipient_id: str
    proposal_digest: str
    fictional_contact: str

    def to_dict(self) -> dict[str, Any]:
        return {"release_id": self.release_id, "sender_id": self.sender_id,
                "recipient_id": self.recipient_id, "proposal_digest": self.proposal_digest,
                "fictional_contact": self.fictional_contact}

    @property
    def value_digest(self) -> str:
        return digest(self.to_dict())


@dataclass
class ContactReceiver:
    """Separate fictional receiver ledger; an ACK is a later independent step."""

    owner_id: str
    received: dict[str, str] = field(default_factory=dict)

    def receive(self, envelope: FictionalContactEnvelope, state: ProtocolState, *,
                authenticated_sender_id: str, as_of: datetime) -> dict[str, Any]:
        now = require_time(as_of)
        if len(canonical_bytes(envelope.to_dict())) > load_defaults()["protocol_pilot"]["max_encoded_message_bytes"]:
            raise SimulationError("PROTOCOL_ENVELOPE_TOO_LARGE")
        if (self.owner_id not in state.participant_ids or envelope.recipient_id != self.owner_id
                or envelope.sender_id == self.owner_id or envelope.sender_id != authenticated_sender_id
                or authenticated_sender_id not in state.participant_ids):
            raise SimulationError("CONTACT_RECEIVER_BINDING_MISMATCH")
        if (state.status not in {"MUTUAL_ACCEPTED", "SENT_UNACKNOWLEDGED"}
                or state.proposal_kind != "contact_release" or state.proposal_digest != envelope.proposal_digest
                or state.deadline is None or now >= state.deadline
                or state.proposal_expires_at is None or now >= state.proposal_expires_at
                or set(state.accepted_by) != set(state.participant_ids)
                or len(state.acceptance_deadlines) != 2
                or any(now >= deadline for _, deadline in state.acceptance_deadlines)
                or any(now >= record[4] for record in state.peer_claims)):
            raise SimulationError("CONTACT_RECEIPT_NOT_AUTHORISED")
        if not isinstance(envelope.fictional_contact, str) or not envelope.fictional_contact.endswith("@example.invalid"):
            raise SimulationError("FICTIONAL_CONTACT_REQUIRED")
        if not isinstance(envelope.release_id, str) or not 0 < len(envelope.release_id) <= 128:
            raise SimulationError("INVALID_RELEASE_ID")
        previous = self.received.get(envelope.release_id)
        if previous is not None and previous != envelope.value_digest:
            raise SimulationError("CONTACT_RELEASE_ID_REUSED")
        duplicate = previous is not None
        if not duplicate:
            self.received[envelope.release_id] = envelope.value_digest
        return {"receiver_id": self.owner_id, "release_id": envelope.release_id,
                "application_received": True, "duplicate": duplicate,
                "read_confirmation": False, "durable": False}


def _negotiation(as_of: datetime) -> ProtocolSimulator:
    sim = ProtocolSimulator()
    a, b = sim.state.participant_ids
    sim.event("PAIR", a, {"fixture_invitation_verified": True}, as_of)
    sim.event("HELLO", a, {"setup_id": "setup-1", "nonce": "nonce-a"}, as_of)
    sim.event("HELLO", b, {"setup_id": "setup-1", "nonce": "nonce-b"}, as_of)
    sim.event("SESSION_OPEN", a, {"session_id": "episode-1", "setup_id": "setup-1", "nonces": dict(sim.state.hello_nonces)}, as_of)
    sim.event("SESSION_ACK", a, {"session_id": "episode-1"}, as_of)
    sim.event("SESSION_ACK", b, {"session_id": "episode-1"}, as_of)
    for owner in (a, b):
        sim.event("POLICY_OFFER", owner, {"question_ids": sorted(REGISTERED_QUESTIONS)}, as_of)
    return sim


def usable_peer_claims(state: ProtocolState, as_of: datetime) -> tuple[tuple[str, str, int, str, datetime], ...]:
    """Known expiry/withdrawal excludes new automated use; no remote recall."""
    now = require_time(as_of)
    return tuple(record for record in state.peer_claims if now < record[4])


def example_context(as_of: datetime, *, neutral: bool = False) -> SharedIntroductionContext:
    expiry = require_time(as_of) + timedelta(days=7)
    identities = (DisplayIdentity("synthetic-lumen", "Lumen", 1), DisplayIdentity("synthetic-harbor", "Harbor", 1))
    peers = (identities[1].owner_id, identities[0].owner_id)
    projected = tuple(project_display(identity, DisplayGrant(identity.owner_id, peer, "collaboration", 1, identity.value_digest, expiry),
                                      peer, "collaboration", as_of) for identity, peer in zip(identities, peers))
    title = "Making a portable learning exhibit usable without reading small print"
    left = ApprovedTopicStatement("a-topic-1", 1, identities[0].owner_id, identities[1].owner_id, "collaboration", "portable-learning-exhibit", title,
                                  "I want to test a portable learning exhibit with visitors who use different reading strategies.", expiry)
    right = ApprovedTopicStatement("b-topic-1", 1, identities[1].owner_id, identities[0].owner_id, "collaboration", "portable-learning-exhibit", title,
                                   "I want to review the tactile labels and installation instructions for a portable learning exhibit.", expiry)
    bridges = grounded_bridges((left,), (right,), left_id=identities[0].owner_id, right_id=identities[1].owner_id,
                                purpose="collaboration", as_of=as_of)
    return SharedIntroductionContext("synthetic-context-1", 1, "collaboration",
                                      (identities[0].owner_id, identities[1].owner_id), projected,
                                      neutral_invitation() if neutral else bridges[0])  # type: ignore[arg-type]


def example_proposal(as_of: datetime, *, neutral: bool = False, chat_coaching: bool = False) -> ChannelProposal:
    context = example_context(as_of, neutral=neutral)
    return ChannelProposal("synthetic-proposal-1", 1, context, as_of + timedelta(days=7), as_of + timedelta(days=7),
                           context.participant_ids if chat_coaching else ())


def example_channel(as_of: datetime, *, neutral: bool = False, chat_coaching: bool = False) -> HumanChannel:
    proposal = example_proposal(as_of, neutral=neutral, chat_coaching=chat_coaching)
    acceptances = tuple(acceptance_for(proposal, owner, as_of + timedelta(days=1)) for owner in proposal.context.participant_ids)
    return open_channel(proposal, acceptances, as_of=as_of, session_expires_at=as_of + timedelta(seconds=600))


def _denied(operation: Any) -> str:
    try:
        operation()
    except SimulationError as exc:
        return exc.code
    raise AssertionError("scenario expected an operation to be denied")


_SCENARIOS = (
    "mutual_acceptance", "one_sided_acceptance", "changed_context", "withdrawn_acceptance",
    "neutral_invitation", "ungranted_display", "offline_queue_resume", "lost_ack_deduplication",
    "expired_outbox", "block_then_unblock", "private_coach", "chat_coach_policy",
    "finite_repeating_decimal", "simultaneous_start", "protocol_replay", "protocol_budgets",
    "generic_private_stop", "contact_release", "contact_asymmetric_delivery", "contact_kind_separation", "session_expiry",
)


def scenario_names() -> tuple[str, ...]:
    return _SCENARIOS


def run_scenario(scenario_id: str, as_of: datetime) -> dict[str, Any]:
    if scenario_id not in _SCENARIOS:
        raise SimulationError("UNKNOWN_SYNTHETIC_SCENARIO")
    now = require_time(as_of)
    result: dict[str, Any] = {"scenario_id": scenario_id, "release": "R0", "synthetic_only": True,
                              "as_of": timestamp(now), "execution_kind": "in_memory_simulation",
                              "network_used": False, "cryptography_verified": False,
                              "persistent_storage_verified": False}
    if scenario_id in {"mutual_acceptance", "one_sided_acceptance", "changed_context", "withdrawn_acceptance", "neutral_invitation", "contact_kind_separation"}:
        proposal = example_proposal(now, neutral=scenario_id == "neutral_invitation")
        acceptances = tuple(acceptance_for(proposal, owner, now + timedelta(days=1)) for owner in proposal.context.participant_ids)
        if scenario_id == "one_sided_acceptance":
            result["denied"] = _denied(lambda: open_channel(proposal, acceptances[:1], as_of=now, session_expires_at=now + timedelta(minutes=10)))
        elif scenario_id == "changed_context":
            changed = replace(proposal, context=replace(proposal.context, version=2, topic=neutral_invitation()), version=2)
            result["denied"] = _denied(lambda: open_channel(changed, acceptances, as_of=now, session_expires_at=now + timedelta(minutes=10)))
        elif scenario_id == "withdrawn_acceptance":
            changed_acceptance = (replace(acceptances[0], revoked=True), acceptances[1])
            result["denied"] = _denied(lambda: open_channel(proposal, changed_acceptance, as_of=now, session_expires_at=now + timedelta(minutes=10)))
        elif scenario_id == "contact_kind_separation":
            wrong = (replace(acceptances[0], proposal_kind="contact_release"), acceptances[1])
            result["denied"] = _denied(lambda: open_channel(proposal, wrong, as_of=now, session_expires_at=now + timedelta(minutes=10)))
        else:
            channel = open_channel(proposal, acceptances, as_of=now, session_expires_at=now + timedelta(minutes=10))
            result["card"] = introduction_card(proposal.context)
            result["channel"] = channel.summary()
    elif scenario_id == "ungranted_display":
        identity = DisplayIdentity("synthetic-lumen", "Lumen", 1)
        grant = DisplayGrant("synthetic-lumen", "synthetic-other", "collaboration", 1, identity.value_digest, now + timedelta(days=1))
        result["denied"] = _denied(lambda: project_display(identity, grant, "synthetic-harbor", "collaboration", now))
    elif scenario_id in {"offline_queue_resume", "lost_ack_deduplication", "expired_outbox", "block_then_unblock", "session_expiry"}:
        channel = example_channel(now)
        owner = channel.context.participant_ids[0]
        entry = channel.queue_human_message(owner, "Could we begin by testing a tactile label on the smallest exhibit panel?", "synthetic-message-1", now, explicit_human_send=True)
        result["initial_delivery_state"] = entry.state
        if scenario_id == "expired_outbox":
            later = now + timedelta(days=8)
            channel.connect(channel.context.participant_ids, 1, later)
            result["denied"] = _denied(lambda: channel.deliver(entry.message.message_id, later))
        elif scenario_id == "block_then_unblock":
            channel.block(owner, now)
            channel.unblock(owner)
            result["denied"] = _denied(lambda: channel.connect(channel.context.participant_ids, channel.membership_epoch, now))
        else:
            later = now + timedelta(seconds=601 if scenario_id == "session_expiry" else 1)
            channel.connect(channel.context.participant_ids, 1, later)
            result["receipt"] = channel.deliver(entry.message.message_id, later, acknowledge=scenario_id != "lost_ack_deduplication")
            if scenario_id == "lost_ack_deduplication":
                result["state_after_lost_ack"] = entry.state
                result["retry_receipt"] = channel.deliver(entry.message.message_id, later + timedelta(seconds=1))
        result["channel"] = channel.summary()
    elif scenario_id in {"private_coach", "chat_coach_policy"}:
        channel = example_channel(now)
        owner = channel.context.participant_ids[0]
        request = CoachRequest("request-1", owner, True, channel.context.value_digest)
        result["disabled_result"] = _denied(lambda: private_draft(channel, request, as_of=now))
        enable_private_coach(channel, owner, explicit_owner_choice=True)
        draft = private_draft(channel, request, as_of=now)
        result["draft"] = draft.to_dict()
        result["publish_result"] = _denied(lambda: publish_private_draft(draft))
        if scenario_id == "chat_coach_policy":
            request = replace(request, selected_message_ids=("unselected-chat-message",))
            result["chat_result"] = _denied(lambda: private_draft(channel, request, as_of=now))
        result["human_message_count"] = len(channel.human_messages)
    elif scenario_id == "finite_repeating_decimal":
        result["topic"] = {"kind": "owner_proposed", "title": "Finite and repeating decimals",
                           "opening_question": "How would you explain why finite 0.999 is below 1, while repeating 0.999… equals 1?",
                           "claims_a_shared_disagreement": False}
    else:
        sim = _negotiation(now)
        a, b = sim.state.participant_ids
        if scenario_id == "simultaneous_start":
            result["coordinator"] = sim.state.coordinator
            result["logical_sessions"] = len(sim.ledger.active)
            result["second_session_result"] = _denied(lambda: sim.ledger.reserve_session((a, b), "collaboration", "different-session", now))
        elif scenario_id == "protocol_replay":
            event = sim.event("QUESTION", a, {"question_ids": ["current_goal"], "ordinal": 1, "round": 1}, now)
            result["duplicate_effects"] = list(sim.apply(event, now))
            result["changed_duplicate_effects"] = list(sim.apply(replace(event, payload={"question_ids": ["availability"], "ordinal": 1, "round": 1}), now))
        elif scenario_id == "protocol_budgets":
            sim.ledger.reserve_questions(a, b, 30, now)
            result["denied"] = _denied(lambda: sim.ledger.reserve_questions(a, "new-peer", 1, now))
        elif scenario_id == "generic_private_stop":
            sim.event("DECLINE", a, {"local_reason": "fixture-private-hard-constraint-unknown"}, now)
            result["wire_stop"] = sim.trace[-1]["effects"]
            result["private_reason_not_in_wire_stop"] = True
        elif scenario_id in {"contact_release", "contact_asymmetric_delivery"}:
            proposal_hash = digest({"fictional_proposal": "contact-1"})
            sim.event("PROPOSAL", a, {"proposal_kind": "contact_release", "proposal_digest": proposal_hash,
                                      "expires_at": timestamp(now + timedelta(days=7)), "both_locally_willing": True}, now)
            for owner in (a, b):
                sim.event("OWNER_ACCEPT", owner, {"proposal_kind": "contact_release", "proposal_digest": proposal_hash,
                                                  "authorization_ref": f"fictional-accept-{owner}", "expires_at": timestamp(now + timedelta(days=1))}, now)
            sim.authorise_fictional_contact(a, "fictional-lumen@example.invalid", expires_at=now + timedelta(days=1))
            receiver_state = sim.state  # Independent endpoint snapshot before either send.
            sim.event("CONTACT_RELEASE", a, {"release_id": "synthetic-release-1", "proposal_digest": proposal_hash,
                                             "fictional_contact": "fictional-lumen@example.invalid", "authority_current": True}, now)
            result["delivery_before_ack"] = sim.state.status
            if scenario_id == "contact_asymmetric_delivery":
                receiver = ContactReceiver(b)
                envelope = FictionalContactEnvelope("synthetic-release-1", a, b, proposal_hash,
                                                    "fictional-lumen@example.invalid")
                result["receiver_before_ack"] = receiver.receive(envelope, receiver_state, authenticated_sender_id=a, as_of=now)
                result["receiver_own_contact_release"] = "not_sent"
                result["sender_before_lost_ack"] = sim.state.status
                # Dropping the fictional ACK changes no sender state. A retry is
                # a distinct attempt with the same immutable release identity.
                sim.event("CONTACT_RELEASE", a, {"release_id": envelope.release_id, "proposal_digest": proposal_hash,
                                                 "fictional_contact": envelope.fictional_contact, "authority_current": True}, now)
                result["retry_receipt"] = receiver.receive(envelope, receiver_state, authenticated_sender_id=a, as_of=now)
                result["receiver_distinct_release_count"] = len(receiver.received)
                result["bilateral_delivery_atomic"] = False
                result["delivery_note"] = "Fictional separate endpoint states; no process crash, durable storage or real send is asserted."
            sim.event("RELEASE_ACK", b, {"release_id": "synthetic-release-1"}, now)
        result["protocol_status"] = sim.state.status
        result["trace"] = sim.trace
        result["owner_budget_ledger"] = sim.ledger.summary()
    result["script_completed"] = True
    return result
