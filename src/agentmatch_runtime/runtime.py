"""Owner-bound agent commands, exact grants, atomic effects and inspectable receipts.

The credential, not a request argument, selects the acting fictional owner.
Scopes are an upper bound; every outward or consent action additionally needs
an exact command authorization or a bounded standing mission. Agent
tokens cannot create credentials or approve their own requests. The viewer
role can only read its owner's workspace and receipt metadata.

All authority checks happen inside the same database writer transaction as
the effect, budget debit and retry record. A rejected domain command discards
its cloned workflow state, while an authenticated denial leaves only a bounded
metadata receipt. Frozen R0 code and its fixture-only identity assumptions are
unchanged; this runtime is not a real-owner release or a network board.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Any, Callable

from agentmatch_workbench.adapters.seed import build_service
from agentmatch_workbench.adapters.lexical_index import InMemoryLexicalIndex
from agentmatch_workbench.application.ports import SimulatedClock, index_document
from agentmatch_workbench.application.service import WorkbenchError
from .serialization import restore, snapshot
from .vault import EncryptedVault
from .policies import MAX_MISSIONS, MISSION_OPERATIONS, PolicyError, authorize_use, build_mission

SCHEMA = "agentmatch-agent-runtime-1"
READ_OPERATIONS = frozenset({"workspace", "preview_publication", "search", "explain"})
EXACT_OPERATIONS = frozenset({
    "search", "approve_publication", "propose_introduction", "accept", "change",
    "request_information", "propose_step", "agree_step", "report_step",
})
INTRO_OPERATIONS = frozenset({
    "accept", "decline", "withdraw_acceptance", "block_introduction", "change",
    "close_introduction", "propose_step", "agree_step", "report_step",
})
OPERATIONS = frozenset({
    "workspace", "save_brief", "preview_publication", "approve_publication",
    "withdraw_notice", "search", "explain", "block_notice", "propose_introduction",
    "request_information", "decline_information", *INTRO_OPERATIONS,
})
MAX_CREDENTIALS = 128
MAX_GRANTS = 128
MAX_RECEIPTS = 2000
MAX_USES = 10000
MAX_SEARCH_RECOVERIES = 3


class AgentRuntimeError(ValueError):
    def __init__(self, code: str, *, status: int = 400, details: dict[str, Any] | None = None):
        self.code, self.status, self.details = code, status, details or {}
        super().__init__(code)

    def to_dict(self) -> dict[str, Any]:
        messages = {
            "AUTH_REQUIRED": "Supply a current owner-bound credential.",
            "CREDENTIAL_INACTIVE": "This credential is expired or revoked.",
            "SCOPE_REQUIRED": "This credential cannot perform that operation.",
            "APPROVAL_REQUIRED": "This command needs an exact authorization or a matching active mission.",
            "APPROVAL_STALE": "The approved command is stale or expired; review current state and authorize again.",
            "VERSION_CONFLICT": "Read the current workspace and reconsider the command against its current version.",
            "IDEMPOTENCY_CONFLICT": "This retry key already identifies a different command.",
            "BUDGET_EXHAUSTED": "This credential has used its command budget.",
            "IDEMPOTENCY_REQUIRED": "Supply a unique retry key and retain it if the response is lost.",
            "SEARCH_RECOVERY_UNAVAILABLE": "This earlier search has no remaining recovery authority. Obtain fresh search permission.",
            "SEARCH_RECOVERY_EXPIRED": "The earlier search authority expired. Obtain fresh search permission.",
            "SEARCH_RECOVERY_REVOKED": "The earlier mission no longer permits this search recovery.",
            "SEARCH_RECOVERY_SCOPE_CHANGED": "The search audience changed. Obtain fresh search permission.",
            "SEARCH_RECOVERY_LIMIT": "The three permitted recoveries were used. Obtain fresh search permission.",
        }
        return {"error": {"code": self.code,
                          "message": messages.get(self.code, "The command was refused."),
                          "details": self.details}}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 30:
        raise AgentRuntimeError("INVALID_ARGUMENTS")
    if value is None or type(value) in (bool, int, str):
        if isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise AgentRuntimeError("INVALID_ARGUMENTS")
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict) and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise AgentRuntimeError("INVALID_ARGUMENTS")


def _canonical(value: Any) -> bytes:
    _json_value(value)
    data = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > 65536:
        raise AgentRuntimeError("COMMAND_TOO_LARGE", status=413)
    return data


def command_digest(operation: str, arguments: dict[str, Any]) -> str:
    """Consistency digest only. Possessing this digest is never an authorization."""
    return hashlib.sha256(_canonical({"operation": operation, "arguments": arguments})).hexdigest()


def _validate_state(state: Any) -> None:
    required = {"schema", "version", "workflow", "credentials", "idempotency", "receipts", "receipt_sequence"}
    if (not isinstance(state, dict) or set(state) != required or state["schema"] != SCHEMA
            or type(state["version"]) is not int or state["version"] < 0
            or not isinstance(state["credentials"], dict) or not isinstance(state["idempotency"], dict)
            or not isinstance(state["receipts"], list) or type(state["receipt_sequence"]) is not int):
        raise AgentRuntimeError("VAULT_SCHEMA_UNSUPPORTED", status=503)


class AgentRuntime:
    """Local administrative methods require possession of the external vault key.

    Only ``execute``, ``capabilities`` and ``receipts`` belong on an agent
    transport. Never expose create/issue/authorize/revoke/list methods remotely.
    """

    def __init__(self, vault: EncryptedVault, *, clock: Callable[[], datetime] = _now):
        self.vault, self._clock = vault, clock

    @classmethod
    def create(cls, db_path: Path | str, key: bytes, *, clock: Callable[[], datetime] = _now) -> AgentRuntime:
        vault = EncryptedVault(db_path, key)
        vault.create({"schema": SCHEMA, "version": 0, "workflow": snapshot(build_service()),
                      "credentials": {}, "idempotency": {}, "receipts": [], "receipt_sequence": 0})
        return cls(vault, clock=clock)

    @classmethod
    def open(cls, db_path: Path | str, key: bytes, *, clock: Callable[[], datetime] = _now) -> AgentRuntime:
        runtime = cls(EncryptedVault(db_path, key), clock=clock)
        with runtime.vault.transaction() as state:
            _validate_state(state)
            service = runtime._service(state, runtime._time())
            if service.migrate_information_accounting():
                state["workflow"] = snapshot(service)
        return runtime

    @staticmethod
    def _service(state, now):
        service = restore(state["workflow"])
        service.information_clock = SimulatedClock(now)
        return service

    def _time(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AgentRuntimeError("CLOCK_INVALID", status=503)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _receipt(state, credential, operation, status, now, *, code=None):
        state["receipt_sequence"] += 1
        receipt = {"sequence": state["receipt_sequence"], "at": now.isoformat(),
                   "owner_id": credential["owner_id"], "credential_id": credential["id"],
                   "role": credential["role"], "operation": operation,
                   "status": status, "version": state["version"]}
        if code:
            receipt["code"] = code
        state["receipts"].append(receipt)
        del state["receipts"][:-MAX_RECEIPTS]
        return dict(receipt)

    @staticmethod
    def _active(credential, now):
        if credential["revoked"] or not datetime.fromisoformat(credential["issued_at"]) <= now < datetime.fromisoformat(credential["expires_at"]):
            raise AgentRuntimeError("CREDENTIAL_INACTIVE", status=401)

    def _authenticate(self, state, token, now):
        if not isinstance(token, str) or len(token) > 200:
            raise AgentRuntimeError("AUTH_REQUIRED", status=401)
        parts = token.split(".")
        credential = state["credentials"].get(parts[1]) if len(parts) == 3 and parts[0] == "am1" else None
        if credential is None or not hmac.compare_digest(credential["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
            raise AgentRuntimeError("AUTH_REQUIRED", status=401)
        self._active(credential, now)
        return credential

    @staticmethod
    def _scope(credential, operation):
        if operation not in credential["scopes"] or (credential["role"] == "viewer" and operation != "workspace"):
            raise AgentRuntimeError("SCOPE_REQUIRED", status=403)

    @staticmethod
    def _credential_view(credential):
        return {key: credential[key] for key in ("id", "owner_id", "role", "scopes", "issued_at", "expires_at", "max_uses", "uses", "revoked")}

    def issue_credential(self, owner_id: str, role: str, scopes, ttl_seconds: int = 86400, max_uses: int = 1000) -> dict[str, Any]:
        if (role not in {"agent", "viewer"} or not isinstance(scopes, (list, tuple, set, frozenset))
                or not scopes or any(type(s) is not str or s not in OPERATIONS for s in scopes)
                or len(set(scopes)) != len(scopes)
                or (role == "viewer" and set(scopes) != {"workspace"})
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 30 * 86400
                or type(max_uses) is not int or not 1 <= max_uses <= MAX_USES):
            raise AgentRuntimeError("INVALID_CREDENTIAL_POLICY")
        now = self._time()
        credential_id = secrets.token_hex(16)
        token = "am1." + credential_id + "." + secrets.token_urlsafe(32)
        credential = {"id": credential_id, "owner_id": owner_id, "role": role, "scopes": sorted(scopes),
                      "issued_at": now.isoformat(), "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
                      "max_uses": max_uses, "uses": 0, "revoked": False,
                      "token_hash": hashlib.sha256(token.encode()).hexdigest(), "grants": {}, "missions": {}}
        with self.vault.transaction() as state:
            _validate_state(state)
            if owner_id not in restore(state["workflow"]).store.owners:
                raise AgentRuntimeError("UNKNOWN_OWNER", status=404)
            if len(state["credentials"]) >= MAX_CREDENTIALS:
                raise AgentRuntimeError("CREDENTIAL_LIMIT_REACHED", status=409)
            state["credentials"][credential_id] = credential
            state["idempotency"][credential_id] = {}
            self._receipt(state, credential, "credential_issued", "administrative", now)
        return {**self._credential_view(credential), "token": token}

    def list_credentials(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        with self.vault.transaction(write=False) as state:
            _validate_state(state)
            return [self._credential_view(c) for c in state["credentials"].values()
                    if owner_id is None or c["owner_id"] == owner_id]

    def revoke_credential(self, credential_id: str) -> dict[str, Any]:
        with self.vault.transaction() as state:
            _validate_state(state)
            credential = state["credentials"].get(credential_id)
            if credential is None:
                raise AgentRuntimeError("CREDENTIAL_NOT_FOUND", status=404)
            credential["revoked"] = True
            credential["grants"].clear()
            for mission in credential.get("missions", {}).values():
                mission["revoked"] = True
            receipt = self._receipt(state, credential, "credential_revoked", "administrative", self._time())
            return {"revoked": True, "id": credential_id, "receipt": receipt}

    @staticmethod
    def _invoke(service, owner_id, operation, arguments):
        if not isinstance(arguments, dict):
            raise AgentRuntimeError("INVALID_ARGUMENTS")
        if operation == "save_brief" and (type(arguments.get("expected_version")) is not int or arguments["expected_version"] < 0):
            # bool is a subclass of int in Python and True == 1. The agent
            # contract requires an actual integer revision, not equal coercion.
            raise AgentRuntimeError("INVALID_ARGUMENTS")
        if operation == "workspace":
            if arguments:
                raise AgentRuntimeError("INVALID_ARGUMENTS")
            return service.workspace(owner_id)
        if operation in INTRO_OPERATIONS or operation == "decline_information":
            identifier = "request_id" if operation == "decline_information" else "intro_id"
            if set(arguments) != {identifier, "data"} or not isinstance(arguments[identifier], str) or not isinstance(arguments["data"], dict):
                raise AgentRuntimeError("INVALID_ARGUMENTS")
            return getattr(service, operation)(owner_id, arguments[identifier], arguments["data"])
        return getattr(service, operation)(owner_id, arguments)

    def authorize_command(self, credential_id: str, operation: str, arguments: dict[str, Any], *, ttl_seconds: int = 300) -> dict[str, Any]:
        """Approve one concrete command after trusted-local review, never via API.

        The digest, current workflow version, credential identity and short real
        expiry all bind the grant. A dry run validates the command in a disposable
        service; no publication, acceptance or other effect occurs here.
        """
        if operation not in EXACT_OPERATIONS or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise AgentRuntimeError("INVALID_AUTHORIZATION")
        digest = command_digest(operation, arguments)
        now = self._time()
        with self.vault.transaction() as state:
            _validate_state(state)
            credential = state["credentials"].get(credential_id)
            if credential is None:
                raise AgentRuntimeError("CREDENTIAL_NOT_FOUND", status=404)
            self._active(credential, now)
            self._scope(credential, operation)
            if credential["uses"] >= credential["max_uses"]:
                raise AgentRuntimeError("BUDGET_EXHAUSTED", status=429)
            try:
                self._invoke(self._service(state, now), credential["owner_id"], operation, arguments)
            except WorkbenchError as exc:
                raise AgentRuntimeError(exc.code, status=exc.status) from exc
            except AgentRuntimeError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise AgentRuntimeError("INVALID_ARGUMENTS") from exc
            grants = credential["grants"]
            for existing in list(grants):
                if datetime.fromisoformat(grants[existing]["expires_at"]) <= now or grants[existing]["version"] != state["version"]:
                    del grants[existing]
            if digest not in grants and len(grants) >= MAX_GRANTS:
                raise AgentRuntimeError("AUTHORIZATION_LIMIT_REACHED", status=409)
            grant = {"digest": digest, "operation": operation, "version": state["version"],
                     "credential_id": credential_id,
                     "expires_at": min(now + timedelta(seconds=ttl_seconds), datetime.fromisoformat(credential["expires_at"])).isoformat()}
            grants[digest] = grant
            self._receipt(state, credential, "command_authorized", "administrative", now)
            return dict(grant)

    def authorize_mission(self, credential_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        """Delegate a finite reviewed mission; agents cannot call this method.

        Captured board membership and notice bindings are returned for local
        review. Existing vaults from the exact-grant slice acquire the optional
        missions map on first use, with no changes to their old authority.
        """
        _canonical(policy)
        now = self._time()
        with self.vault.transaction() as state:
            _validate_state(state)
            credential = state["credentials"].get(credential_id)
            if credential is None:
                raise AgentRuntimeError("CREDENTIAL_NOT_FOUND", status=404)
            self._active(credential, now)
            if credential["uses"] >= credential["max_uses"]:
                raise AgentRuntimeError("BUDGET_EXHAUSTED", status=429)
            missions = credential.setdefault("missions", {})
            if len(missions) >= MAX_MISSIONS:
                raise AgentRuntimeError("MISSION_LIMIT_REACHED", status=409)
            try:
                mission = build_mission(self._service(state, now), credential, policy,
                                        mission_id="mission-" + secrets.token_hex(12), now=now)
            except (PolicyError, ValueError, TypeError, KeyError) as exc:
                code = str(exc) if isinstance(exc, PolicyError) else "MISSION_INVALID"
                raise AgentRuntimeError(code, status=403 if code == "SCOPE_REQUIRED" else 400) from exc
            missions[mission["id"]] = mission
            receipt = self._receipt(state, credential, "mission_authorized", "administrative", now)
            receipt["mission_id"] = mission["id"]
            state["receipts"][-1]["mission_id"] = mission["id"]
            return dict(mission)

    def revoke_mission(self, credential_id: str, mission_id: str) -> dict[str, Any]:
        with self.vault.transaction() as state:
            _validate_state(state)
            credential = state["credentials"].get(credential_id)
            mission = credential.get("missions", {}).get(mission_id) if credential else None
            if mission is None:
                raise AgentRuntimeError("MISSION_NOT_FOUND", status=404)
            mission["revoked"] = True
            receipt = self._receipt(state, credential, "mission_revoked", "administrative", self._time())
            receipt["mission_id"] = mission_id
            state["receipts"][-1]["mission_id"] = mission_id
            return {"id": mission_id, "revoked": True, "receipt": receipt}

    @staticmethod
    def _mission_view(mission):
        return {**{k: mission[k] for k in ("id", "operations", "issued_at", "expires_at", "max_uses", "uses", "revoked", "first_steps", "search_queries")},
                "own_notice": {k: mission["own_notice"][k] for k in ("notice_id", "revision")},
                "candidate_notices": [{k: n[k] for k in ("notice_id", "revision")} for n in mission["candidate_notices"]],
                "board_ids": sorted(mission["board_memberships"]),
                "board_member_counts": {k: len(v) for k, v in mission["board_memberships"].items()},
                "remaining_uses": max(0, mission["max_uses"] - mission["uses"])}

    def capabilities(self, token: str) -> dict[str, Any]:
        with self.vault.transaction(write=False) as state:
            _validate_state(state)
            now = self._time()
            credential = self._authenticate(state, token, now)
            return {"version": state["version"], "result": {
                **self._credential_view(credential), "remaining_uses": max(0, credential["max_uses"] - credential["uses"]),
                "information_budget": self._service(state, now).information_budget(credential["owner_id"]),
                "fictional_only": True, "workflow_time": state["workflow"]["now"],
                "additional_authorization_required": sorted(EXACT_OPERATIONS.intersection(credential["scopes"])),
                "standing_mission_supported": sorted(MISSION_OPERATIONS.intersection(credential["scopes"])),
                "missions": [self._mission_view(m) for m in credential.get("missions", {}).values()],
                "grants": [dict(g) for g in credential["grants"].values()
                           if datetime.fromisoformat(g["expires_at"]) > self._time() and g["version"] == state["version"]]}}

    @staticmethod
    def _search_recovery_authority(service, credential, result, grant, mission):
        """Persist references/authority, never old peer text or a cached result.

        This derives at most three recovery reads from the original permission.
        An exact search is bound to its original query fingerprint, audience,
        returned revisions and expiry. A mission additionally stays revocable
        independently of the credential. New candidates need a new search use.
        """
        source = mission if mission is not None else grant
        return {"schema": 1, "expires_at": source["expires_at"], "recoveries": 0,
                "mission_id": mission["id"] if mission is not None else None,
                "board_memberships": {b.board_id: sorted(b.member_ids) for b in service.store.boards_for(credential["owner_id"])},
                "references": [{"notice_id": row["card"]["notice_id"], "revision": row["card"]["revision"]}
                               for row in result["results"]]}

    def _recover_search(self, state, credential, previous, arguments, now):
        recovery = previous.get("search_recovery")
        if not isinstance(recovery, dict) or recovery.get("schema") != 1:
            raise AgentRuntimeError("SEARCH_RECOVERY_UNAVAILABLE", status=409)
        if now >= datetime.fromisoformat(recovery["expires_at"]):
            raise AgentRuntimeError("SEARCH_RECOVERY_EXPIRED", status=403)
        if recovery["recoveries"] >= MAX_SEARCH_RECOVERIES:
            raise AgentRuntimeError("SEARCH_RECOVERY_LIMIT", status=409)
        service = self._service(state, now)
        current_boards = {b.board_id: sorted(b.member_ids) for b in service.store.boards_for(credential["owner_id"])}
        if current_boards != recovery["board_memberships"]:
            raise AgentRuntimeError("SEARCH_RECOVERY_SCOPE_CHANGED", status=403)
        mission = None
        if recovery["mission_id"] is not None:
            mission = credential.get("missions", {}).get(recovery["mission_id"])
            if mission is None:
                raise AgentRuntimeError("SEARCH_RECOVERY_REVOKED", status=403)
            try:
                authorize_use(mission, service, credential["owner_id"], "search", arguments, now, recovery=True)
            except PolicyError as exc:
                raise AgentRuntimeError("SEARCH_RECOVERY_REVOKED", status=403, details={"mission_reason": str(exc)}) from exc
        # Rebuild from current records and the original returned revision set.
        # Withdrawing, blocking, editing or expiring a notice removes it; a
        # newly published candidate/version never expands a recovery read.
        index = InMemoryLexicalIndex()
        for ref in recovery["references"]:
            notice = service.store.notices.get(ref["notice_id"])
            if notice is not None and notice.revision == ref["revision"]:
                index.upsert(index_document(notice_id=notice.notice_id, revision=notice.revision,
                                           board_id=notice.board_id, payload=notice.payload, published_at=notice.published_at))
        service.index = index
        result = service.search(credential["owner_id"], arguments)
        result["coverage"]["recovered_original_candidates"] = True
        result["coverage"]["statement"] += " Recovery rechecks only the originally returned notice revisions against current permissions."
        if mission is not None:
            result["coverage"]["mission_candidate_limit"] = len(mission["candidate_notices"])
        recovery["recoveries"] += 1
        receipt = self._receipt(state, credential, "search_recovered", "completed", now)
        state["receipts"][-1]["original_receipt_sequence"] = previous["receipt"]["sequence"]
        # The cloned service's search log and derived refreshes are deliberately
        # not saved: recovery creates no second original effect or budget use.
        return result, {"count": recovery["recoveries"], "limit": MAX_SEARCH_RECOVERIES,
                        "expires_at": recovery["expires_at"], "returned_current": True}

    def receipts(self, token: str, after: int = 0, limit: int = 50, *,
                 order: str = "asc", before: int | None = None) -> dict[str, Any]:
        if (type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100
                or order not in ("asc", "desc")
                or (before is not None and (type(before) is not int or before < 1))
                or (order == "asc" and before is not None) or (order == "desc" and after != 0)):
            raise AgentRuntimeError("INVALID_CURSOR")
        with self.vault.transaction(write=False) as state:
            _validate_state(state)
            credential = self._authenticate(state, token, self._time())
            self._scope(credential, "workspace")
            retained = state["receipts"]
            visible = [item for item in retained if item["owner_id"] == credential["owner_id"]]
            if order == "desc":
                eligible = [item for item in reversed(visible)
                            if before is None or item["sequence"] < before]
            else:
                eligible = [item for item in visible if item["sequence"] > after]
            items = [dict(item) for item in eligible[:limit]]
            has_more = len(eligible) > limit
            return {"version": state["version"], "result": {
                "items": items, "next_cursor": max((item["sequence"] for item in items), default=after),
                "next_before": items[-1]["sequence"] if order == "desc" and has_more else None,
                "has_more": has_more, "order": order,
                "retention_limit": MAX_RECEIPTS,
                "history_truncated": bool(retained and retained[0]["sequence"] > 1
                                          and (order == "desc" or after < retained[0]["sequence"] - 1))}}

    def execute(self, token: str, operation: str, arguments: dict[str, Any],
                idempotency_key: str | None = None, expected_version: int | None = None) -> dict[str, Any]:
        if type(operation) is not str or operation not in OPERATIONS:
            raise AgentRuntimeError("UNKNOWN_OPERATION", status=404)
        digest = command_digest(operation, arguments)
        error = None
        response = None
        with self.vault.transaction() as state:
            _validate_state(state)
            now = self._time()
            credential = self._authenticate(state, token, now)
            try:
                self._scope(credential, operation)
                requires_key = operation not in READ_OPERATIONS or operation in EXACT_OPERATIONS
                if requires_key and idempotency_key is None:
                    raise AgentRuntimeError("IDEMPOTENCY_REQUIRED")
                if idempotency_key is not None and (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128
                                                   or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)):
                    raise AgentRuntimeError("INVALID_IDEMPOTENCY_KEY")
                if requires_key and (type(expected_version) is not int or expected_version < 0):
                    raise AgentRuntimeError("VERSION_REQUIRED")
                if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
                    raise AgentRuntimeError("INVALID_VERSION")
                key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key is not None else None
                fingerprint = hashlib.sha256(_canonical({"digest": digest, "expected_version": expected_version})).hexdigest()
                previous = state["idempotency"][credential["id"]].get(key_hash) if key_hash is not None else None
                if previous is not None:
                    if not hmac.compare_digest(previous["fingerprint"], fingerprint):
                        raise AgentRuntimeError("IDEMPOTENCY_CONFLICT", status=409)
                    # Mutation retries recover only their receipt. Search retries
                    # regenerate an authorized subset of original candidates.
                    response = {"version": state["version"], "receipt": previous["receipt"], "result": None, "replayed": True}
                    if operation == "search":
                        response["result"], response["recovery"] = self._recover_search(state, credential, previous, arguments, now)
                else:
                    if expected_version is not None and state["version"] != expected_version:
                        raise AgentRuntimeError("VERSION_CONFLICT", status=409, details={"current_version": state["version"]})
                    if credential["uses"] >= credential["max_uses"]:
                        raise AgentRuntimeError("BUDGET_EXHAUSTED", status=429)
                    service = self._service(state, now)
                    mission_used = None
                    exact_used = False
                    if operation in EXACT_OPERATIONS:
                        grant = credential["grants"].get(digest)
                        exact_used = bool(grant is not None and grant["version"] == state["version"]
                                          and now < datetime.fromisoformat(grant["expires_at"]))
                        policy_reason = None
                        if not exact_used:
                            for mission in credential.get("missions", {}).values():
                                if operation not in mission["operations"]:
                                    continue
                                try:
                                    authorize_use(mission, service, credential["owner_id"], operation, arguments, now)
                                except PolicyError as exc:
                                    policy_reason = str(exc)
                                    continue
                                mission_used = mission
                                break
                            if mission_used is None:
                                code = "APPROVAL_STALE" if grant is not None else "APPROVAL_REQUIRED"
                                details = {"command_digest": digest}
                                if policy_reason:
                                    details["mission_reason"] = policy_reason
                                raise AgentRuntimeError(code, status=409 if grant is not None else 403, details=details)
                    try:
                        result = self._invoke(service, credential["owner_id"], operation, arguments)
                    except WorkbenchError as exc:
                        raise AgentRuntimeError(exc.code, status=exc.status, details={"fields": exc.errors}) from exc
                    except AgentRuntimeError:
                        raise
                    except (KeyError, TypeError, ValueError) as exc:
                        raise AgentRuntimeError("INVALID_ARGUMENTS") from exc
                    if operation not in READ_OPERATIONS:
                        state["version"] += 1
                    state["workflow"] = snapshot(service)
                    credential["uses"] += 1
                    if exact_used:
                        del credential["grants"][digest]
                    if mission_used is not None:
                        mission_used["uses"] += 1
                        if operation == "search":
                            result["coverage"]["mission_candidate_limit"] = len(mission_used["candidate_notices"])
                            result["coverage"]["statement"] += " This mission searches only its fixed approved candidate notice revisions."
                    receipt = self._receipt(state, credential, operation, "completed", now)
                    receipt["authority"] = "standing_mission" if mission_used else "exact_command" if exact_used else "scope"
                    state["receipts"][-1]["authority"] = receipt["authority"]
                    if mission_used:
                        receipt["mission_id"] = mission_used["id"]
                        state["receipts"][-1]["mission_id"] = mission_used["id"]
                    if key_hash is not None:
                        state["idempotency"][credential["id"]][key_hash] = {"fingerprint": fingerprint, "receipt": receipt}
                        if operation == "search":
                            state["idempotency"][credential["id"]][key_hash]["search_recovery"] = self._search_recovery_authority(
                                service, credential, result, grant if exact_used else None, mission_used)
                    # Mutating use cases return a whole workspace internally. That
                    # must not implicitly grant private-read access to a narrower token.
                    if operation not in READ_OPERATIONS and "workspace" not in credential["scopes"]:
                        result = {"completed": True}
                    response = {"version": state["version"], "receipt": receipt, "result": result, "replayed": False}
                if response["result"] is not None:
                    # A separate adapter owns canonical agent commands while the
                    # old manual workbench retains its presentation vocabulary.
                    from .machine_views import project_result
                    response["result"] = project_result(operation, response["result"], credential)
            except AgentRuntimeError as exc:
                self._receipt(state, credential, operation, "refused", now, code=exc.code)
                error = exc
        # Successful effects are not returned before ciphertext and receipt commit.
        if error is not None:
            raise error
        assert response is not None
        return response
