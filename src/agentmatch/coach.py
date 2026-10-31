"""On-demand private draft routing, without a model or channel-write capability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .channels import HumanChannel
from .topics import SimulationError


@dataclass(frozen=True)
class CoachRequest:
    request_id: str
    owner_id: str
    explicitly_invoked: bool
    context_digest: str
    selected_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PrivateDraft:
    draft_id: str
    owner_id: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"draft_id": self.draft_id, "owner_id": self.owner_id,
                "text": self.text, "visibility": "owner_only", "may_send": False,
                "generation": "scripted_R0_template"}


def enable_private_coach(channel: HumanChannel, owner_id: str, *, explicit_owner_choice: bool) -> None:
    channel._membership(owner_id)
    if not explicit_owner_choice:
        raise SimulationError("EXPLICIT_COACH_CHOICE_REQUIRED")
    channel.coaching_enabled_by.add(owner_id)


def private_draft(channel: HumanChannel, request: CoachRequest, *, as_of: datetime) -> PrivateDraft:
    now = channel._clock(as_of)
    channel._membership(request.owner_id)
    if request.owner_id not in channel.coaching_enabled_by or not request.explicitly_invoked:
        raise SimulationError("COACH_DISABLED_OR_NOT_REQUESTED")
    if (request.context_digest != channel.context.value_digest
            or not channel.context_available_for_coaching
            or channel.context_reuse_expires_at is None
            or now >= channel.context_reuse_expires_at):
        raise SimulationError("COACH_CONTEXT_NOT_AUTHORISED")
    if request.selected_message_ids:
        if channel.chat_coaching_allowed_by != frozenset(channel.context.participant_ids):
            raise SimulationError("MUTUAL_CHAT_COACHING_POLICY_REQUIRED")
        available = {item.message_id for item in channel.human_messages}
        if not set(request.selected_message_ids).issubset(available):
            raise SimulationError("COACH_MESSAGE_SELECTION_INVALID")
    # The deterministic demo deliberately does not interpret chat instructions.
    # Selected messages merely exercise authorization; no archive is available.
    text = channel.context.topic.opening_question
    return PrivateDraft(f"synthetic-draft-{request.request_id}", request.owner_id, text)


def publish_private_draft(draft: PrivateDraft) -> None:
    del draft
    raise SimulationError("PRIVATE_DRAFT_HAS_NO_SEND_CAPABILITY")
