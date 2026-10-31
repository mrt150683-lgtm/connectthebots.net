"""Bounded questions about missing published fields, never free-text messages.

A request binds two approved notices and one listed field. It is not consent
to an introduction or permission to disclose a private draft. An answer only
exists when the publisher explicitly approves a fresh notice containing the
field and the requester may still read it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .introductions import NoticeRef

INFORMATION_FIELDS = {
    "feedback_style": "Feedback style",
    "working_arrangement": "Working arrangement",
    "basis": "Engagement basis",
}
REQUEST_DAYS = 7
MAX_PENDING_REQUESTS = 5
REQUEST_WINDOW_SECONDS = 24 * 60 * 60
MAX_WINDOW_REQUESTS = 20
# Kept for source compatibility with the original fictional workbench tests.
# The bound now applies to a rolling window, never all retained history.
MAX_SESSION_REQUESTS = MAX_WINDOW_REQUESTS


def missing_fields(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Only approved notice values determine what can be requested."""
    result = []
    if not (payload.get("feedback") or {}).get("style"):
        result.append("feedback_style")
    work = payload.get("working_arrangement") or {}
    if not work.get("venue") or not work.get("rhythm"):
        result.append("working_arrangement")
    if payload.get("basis") == "undecided":
        result.append("basis")
    return tuple(result)


@dataclass
class InformationRequest:
    request_id: str
    requester_id: str
    recipient_id: str
    requester_ref: NoticeRef
    recipient_ref: NoticeRef
    field: str
    created_at: datetime
    expires_at: datetime
    state: str = "pending"  # pending | answered | declined | unavailable
    # This independent accounting clock is real UTC in the persistent runtime
    # and the simulated clock in the old workbench. It is never an input from
    # the requesting agent. None identifies a legacy snapshot awaiting a
    # conservative first-seen migration; history is retained.
    accounted_at: datetime | None = None

    @property
    def participants(self) -> tuple[str, str]:
        return (self.requester_id, self.recipient_id)
