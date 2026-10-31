"""Consent and delivery state for fictional, in-memory human channels.

No object here opens a socket or encrypts/persists a message. A simulated
binding is an input assumption. R2 must implement and verify that binding,
encrypted storage, transaction boundaries and crash recovery separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import load_defaults
from .topics import (SharedIntroductionContext, SimulationError, bounded_text,
                     canonical_bytes, digest, require_time, timestamp)


@dataclass(frozen=True)
class ChannelProposal:
    proposal_id: str
    version: int
    context: SharedIntroductionContext
    expires_at: datetime
    authority_expires_at: datetime
    chat_coaching_allowed_by: tuple[str, ...] = ()
    proposal_kind: str = "channel_open"
    revoked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, "version": self.version,
                "proposal_kind": self.proposal_kind,
                "participant_ids": list(self.context.participant_ids),
                "purpose": self.context.purpose,
                "context_digest": self.context.value_digest,
                "chat_coaching_allowed_by": sorted(self.chat_coaching_allowed_by),
                "shared_agent_turns": False,
                "expires_at": timestamp(self.expires_at),
                "authority_expires_at": timestamp(self.authority_expires_at)}

    @property
    def value_digest(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class Acceptance:
    owner_id: str
    proposal_id: str
    proposal_version: int
    proposal_digest: str
    proposal_kind: str
    expires_at: datetime
    authorization_id: str
    revoked: bool = False


def acceptance_for(proposal: ChannelProposal, owner_id: str, expires_at: datetime) -> Acceptance:
    """Fixture factory: the call represents an explicit scripted owner event."""
    if owner_id not in proposal.context.participant_ids:
        raise SimulationError("OWNER_NOT_IN_PROPOSAL")
    return Acceptance(owner_id, proposal.proposal_id, proposal.version,
                      proposal.value_digest, proposal.proposal_kind, expires_at,
                      f"synthetic-accept-{proposal.proposal_id}-{owner_id}")


@dataclass(frozen=True)
class HumanMessage:
    message_id: str
    channel_id: str
    membership_epoch: int
    author_id: str
    sequence: int
    text: str
    created_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "r0.1", "message_type": "HUMAN_MESSAGE",
                "message_id": self.message_id, "channel_id": self.channel_id,
                "membership_epoch": self.membership_epoch, "author_id": self.author_id,
                "sequence": self.sequence, "text": self.text,
                "created_at": timestamp(self.created_at), "expires_at": timestamp(self.expires_at)}

    @property
    def value_digest(self) -> str:
        return digest(self.to_dict())


@dataclass
class OutboxEntry:
    message: HumanMessage
    state: str = "queued_local"
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"message_id": self.message.message_id, "state": self.state,
                "attempts": self.attempts, "receipt_is_read_confirmation": False,
                "expires_at": timestamp(self.message.expires_at)}


@dataclass
class HumanChannel:
    channel_id: str
    context: SharedIntroductionContext
    opened_at: datetime
    membership_epoch: int = 1
    chat_coaching_allowed_by: frozenset[str] = frozenset()
    connected: bool = False
    closed: bool = False
    blocked_by: set[str] = field(default_factory=set)
    outbox: dict[str, OutboxEntry] = field(default_factory=dict)
    received_digests: dict[str, str] = field(default_factory=dict)
    received_sequences: dict[str, int] = field(default_factory=dict)
    human_messages: list[HumanMessage] = field(default_factory=list)
    next_sequence: dict[str, int] = field(default_factory=dict)
    coaching_enabled_by: set[str] = field(default_factory=set)
    context_available_for_coaching: bool = True
    context_reuse_expires_at: datetime | None = None
    last_time: datetime | None = None

    def _clock(self, as_of: datetime) -> datetime:
        now = require_time(as_of)
        if self.last_time is not None and now < self.last_time:
            self.connected = False
            raise SimulationError("CLOCK_ROLLBACK")
        self.last_time = now
        return now

    def _membership(self, author_id: str, epoch: int | None = None) -> None:
        if self.closed or self.blocked_by:
            raise SimulationError("CHANNEL_CLOSED_OR_BLOCKED")
        if author_id not in self.context.participant_ids:
            raise SimulationError("MEMBERSHIP_REQUIRED")
        if epoch is not None and epoch != self.membership_epoch:
            raise SimulationError("STALE_MEMBERSHIP")

    def connect(self, authenticated_peer_ids: tuple[str, str], membership_epoch: int,
                as_of: datetime) -> None:
        self._clock(as_of)
        if set(authenticated_peer_ids) != set(self.context.participant_ids) or len(set(authenticated_peer_ids)) != 2:
            raise SimulationError("PEER_BINDING_MISMATCH")
        self._membership(authenticated_peer_ids[0], membership_epoch)
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def expire(self, as_of: datetime) -> None:
        now = self._clock(as_of)
        for entry in self.outbox.values():
            if entry.state in {"queued_local", "sent_unacknowledged"} and now >= entry.message.expires_at:
                entry.state = "expired"

    def queue_human_message(self, author_id: str, text: str, message_id: str,
                            as_of: datetime, *, explicit_human_send: bool,
                            author_kind: str = "human", expires_at: datetime | None = None) -> OutboxEntry:
        now = self._clock(as_of)
        self._membership(author_id)
        if author_kind != "human" or not explicit_human_send:
            raise SimulationError("EXPLICIT_HUMAN_SEND_REQUIRED")
        bounded_text(message_id, maximum=128)
        bounded_text(text, maximum=65536)
        if message_id in self.outbox:
            existing = self.outbox[message_id]
            if existing.message.text != text or existing.message.author_id != author_id:
                raise SimulationError("MESSAGE_ID_REUSED_WITH_CHANGED_CONTENT")
            if expires_at is not None and min(existing.message.created_at + timedelta(seconds=load_defaults()["offline"]["default_unsent_expiry_seconds"]), require_time(expires_at)) != existing.message.expires_at:
                raise SimulationError("MESSAGE_ID_REUSED_WITH_CHANGED_CONTENT")
            return existing
        self.expire(now)
        active = sum(entry.state in {"queued_local", "sent_unacknowledged"} for entry in self.outbox.values())
        if active >= load_defaults()["offline"]["max_queued_messages_per_channel"]:
            raise SimulationError("OUTBOX_FULL")
        sequence = self.next_sequence.get(author_id, 1)
        deadline = now + timedelta(seconds=load_defaults()["offline"]["default_unsent_expiry_seconds"])
        if expires_at is not None:
            deadline = min(deadline, require_time(expires_at))
        if deadline <= now:
            raise SimulationError("MESSAGE_EXPIRY_NOT_FUTURE")
        message = HumanMessage(message_id, self.channel_id, self.membership_epoch,
                               author_id, sequence, text, now, deadline)
        if len(canonical_bytes(message.to_dict())) > load_defaults()["social"]["max_encoded_chat_message_bytes"]:
            raise SimulationError("CHAT_ENVELOPE_TOO_LARGE")
        entry = OutboxEntry(message)
        self.outbox[message_id] = entry
        self.next_sequence[author_id] = sequence + 1
        return entry

    def receive(self, message: HumanMessage, authenticated_author_id: str,
                as_of: datetime) -> dict[str, Any]:
        now = self._clock(as_of)
        self._membership(authenticated_author_id, message.membership_epoch)
        if not self.connected:
            raise SimulationError("BOTH_PEERS_MUST_BE_ONLINE")
        if message.channel_id != self.channel_id or message.author_id != authenticated_author_id:
            raise SimulationError("MESSAGE_BINDING_MISMATCH")
        bounded_text(message.message_id, maximum=128)
        bounded_text(message.text, maximum=65536)
        if type(message.sequence) is not int or message.sequence < 1:
            raise SimulationError("INVALID_MESSAGE_SEQUENCE")
        if now >= message.expires_at or message.created_at > now:
            raise SimulationError("MESSAGE_NOT_CURRENT")
        if len(canonical_bytes(message.to_dict())) > load_defaults()["social"]["max_encoded_chat_message_bytes"]:
            raise SimulationError("CHAT_ENVELOPE_TOO_LARGE")
        previous = self.received_digests.get(message.message_id)
        if previous is not None and previous != message.value_digest:
            raise SimulationError("MESSAGE_ID_REUSED_WITH_CHANGED_CONTENT")
        duplicate = previous is not None
        if not duplicate:
            if message.sequence <= self.received_sequences.get(authenticated_author_id, 0):
                raise SimulationError("OUT_OF_ORDER_MESSAGE")
            # R0 models the transaction order. This ledger is NOT durable.
            self.received_digests[message.message_id] = message.value_digest
            self.received_sequences[authenticated_author_id] = message.sequence
            self.human_messages.append(message)
        return {"message_id": message.message_id, "application_received": True,
                "duplicate": duplicate, "read_confirmation": False}

    def deliver(self, message_id: str, as_of: datetime, *, acknowledge: bool = True) -> dict[str, Any]:
        now = self._clock(as_of)
        self.expire(now)
        entry = self.outbox.get(message_id)
        if entry is None:
            raise SimulationError("UNKNOWN_MESSAGE")
        self._membership(entry.message.author_id, entry.message.membership_epoch)
        if not self.connected:
            raise SimulationError("BOTH_PEERS_MUST_BE_ONLINE")
        if entry.state == "acknowledged":
            return {"message_id": message_id, "application_received": True,
                    "duplicate": True, "read_confirmation": False}
        if entry.state not in {"queued_local", "sent_unacknowledged"}:
            raise SimulationError("MESSAGE_NO_LONGER_SENDABLE")
        entry.state = "sent_unacknowledged"
        entry.attempts += 1
        receipt = self.receive(entry.message, entry.message.author_id, now)
        if acknowledge:
            entry.state = "acknowledged"
        return receipt

    def block(self, owner_id: str, as_of: datetime) -> None:
        self._clock(as_of)
        if owner_id not in self.context.participant_ids:
            raise SimulationError("MEMBERSHIP_REQUIRED")
        self.blocked_by.add(owner_id)
        self.closed = True
        self.connected = False
        self.membership_epoch += 1
        for entry in self.outbox.values():
            if entry.state in {"queued_local", "sent_unacknowledged"}:
                entry.state = "cancelled"

    def unblock(self, owner_id: str) -> None:
        self.blocked_by.discard(owner_id)
        # Closed membership and cancelled work deliberately stay closed.

    def withdraw_context_authority(self) -> None:
        self.context_available_for_coaching = False

    def summary(self) -> dict[str, Any]:
        return {"channel_id": self.channel_id, "status": "closed" if self.closed else "ready",
                "membership_epoch": self.membership_epoch, "connected": self.connected,
                "context_panel": self.context.to_dict(),
                "human_messages": [message.to_dict() for message in self.human_messages],
                "outbox": [self.outbox[key].to_dict() for key in sorted(self.outbox)],
                "encrypted": False, "persistent": False,
                "simulation_notice": "Fictional in-memory events; no transport or durable receipt guarantee."}


def open_channel(proposal: ChannelProposal, acceptances: tuple[Acceptance, ...], *,
                 as_of: datetime, session_expires_at: datetime,
                 current_authority: bool = True) -> HumanChannel:
    now = require_time(as_of)
    participants = proposal.context.participant_ids
    if proposal.proposal_kind != "channel_open":
        raise SimulationError("WRONG_PROPOSAL_KIND")
    if len(set(participants)) != 2 or proposal.version < 1:
        raise SimulationError("INVALID_CHANNEL_PROPOSAL")
    if len(canonical_bytes(proposal.context.to_dict())) > load_defaults()["protocol_pilot"]["max_encoded_message_bytes"]:
        raise SimulationError("CONTEXT_ENVELOPE_TOO_LARGE")
    if (proposal.revoked or not current_authority
            or now >= min(require_time(proposal.expires_at), require_time(proposal.authority_expires_at), require_time(session_expires_at))):
        raise SimulationError("CHANNEL_AUTHORITY_NOT_CURRENT")
    if not set(proposal.chat_coaching_allowed_by).issubset(participants):
        raise SimulationError("INVALID_CAPABILITY_PARTICIPANT")
    if len(acceptances) != 2 or {item.owner_id for item in acceptances} != set(participants):
        raise SimulationError("EXACT_DUAL_ACCEPTANCE_REQUIRED")
    for item in acceptances:
        if (item.revoked or now >= require_time(item.expires_at)
                or item.proposal_kind != "channel_open" or item.proposal_id != proposal.proposal_id
                or item.proposal_version != proposal.version or item.proposal_digest != proposal.value_digest):
            raise SimulationError("EXACT_DUAL_ACCEPTANCE_REQUIRED")
    return HumanChannel(f"synthetic-channel-{proposal.proposal_id}-{proposal.version}", proposal.context,
                        now, chat_coaching_allowed_by=frozenset(proposal.chat_coaching_allowed_by),
                        context_reuse_expires_at=proposal.authority_expires_at, last_time=now)
