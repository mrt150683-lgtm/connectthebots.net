"""Machine actions over already-authorized views; never a permission grant.

The manual workbench retains its presentation labels. Runtime clients receive
canonical commands and current resource references, with missing choices named
as JSON pointers into ``arguments``. Execution still checks all authority and
resource state inside its transaction.
"""
from __future__ import annotations

from copy import deepcopy

from .catalogue import authorization_for


def _action(credential, operation, arguments, required=(), *, description=None):
    if credential["role"] != "agent" or operation not in credential["scopes"]:
        return None
    action = {"operation": operation, "arguments": arguments,
              "required_arguments": list(required), "scope": operation,
              "authorization": authorization_for(operation),
              "authorization_checked_at_execution": True,
              "requires_idempotency_key": operation not in {"workspace", "preview_publication", "explain"},
              "requires_expected_version": operation not in {"workspace", "preview_publication", "explain"}}
    if description:
        action["description"] = description
    return action


def _introduction_actions(intro, credential):
    result = []
    for label in intro["actions"]:
        operation = {"block": "block_introduction", "close": "close_introduction"}.get(label, label)
        data, required = {}, []
        if operation == "accept":
            data["proposal_digest"] = intro["proposal_digest"]
        elif operation == "close_introduction":
            data["context_version"] = intro["context_version"]
        elif operation in {"agree_step", "report_step"}:
            data["version"] = intro["first_step"]["version"]
            if operation == "report_step":
                required = ["/data/outcome"]
        elif operation == "change":
            required = ["/data/first_step", "/data/note"]
        elif operation == "propose_step":
            required = ["/data/action", "/data/detail"]
        action = _action(credential, operation, {"intro_id": intro["intro_id"], "data": data}, required)
        if action:
            result.append(action)
    return result


def project_result(operation, result, credential):
    """Return a fresh runtime projection without changing domain/manual records."""
    if not isinstance(result, dict):
        return result
    projected = deepcopy(result)
    if "introductions" in projected and "information_requests" in projected:
        for intro in projected["introductions"]:
            intro["actions"] = _introduction_actions(intro, credential)
        for request in projected["information_requests"]:
            actions = []
            for label in request["actions"]:
                if label == "review_brief":
                    action = _action(credential, "workspace", {}, description=(
                        "Review your private brief. Any answer requires saving your chosen changes, "
                        "previewing publication, and separately authorizing approval; reading does not answer the request."))
                elif label == "decline":
                    action = _action(credential, "decline_information", {"request_id": request["request_id"], "data": {}})
                elif label == "reassess":
                    action = _action(credential, "explain", {"notice_id": request["notice_id"], "revision": request["revision"]})
                else:
                    continue
                if action:
                    actions.append(action)
            request["actions"] = actions
    return projected
