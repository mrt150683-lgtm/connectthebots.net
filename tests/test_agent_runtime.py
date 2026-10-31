"""Fictional agent authority and restart regressions for the optional runtime.

Tests use generated keys and invented content only. The old offline core keeps
its zero-dependency installation; these checks run when the runtime extra is
installed. No network service, account, model or real-person fixture is used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from agentmatch_runtime.runtime import AgentRuntime, AgentRuntimeError, EXACT_OPERATIONS, OPERATIONS, command_digest
from agentmatch_runtime.serialization import decode, restore, snapshot
from agentmatch_runtime.vault import VaultError

CRYPTO_AVAILABLE = importlib.util.find_spec("cryptography") is not None
CANARY = "FICTIONAL-PRIVATE-RUNTIME-CANARY-27"


@unittest.skipUnless(CRYPTO_AVAILABLE, "optional agent runtime requires cryptography")
class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-runtime-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.db"
        self.key = secrets.token_bytes(32)
        self.now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        self.runtime = AgentRuntime.create(self.path, self.key, clock=lambda: self.now)
        self.agent = self.runtime.issue_credential("marlow", "agent", sorted(OPERATIONS))
        self.counter = 0

    def fresh(self):
        return AgentRuntime.open(self.path, self.key, clock=lambda: self.now)

    def command(self, operation, args, credential=None, *, authorize=True):
        credential = credential or self.agent
        version = self.runtime.capabilities(credential["token"])["version"]
        if operation in EXACT_OPERATIONS and authorize:
            self.runtime.authorize_command(credential["id"], operation, args)
        self.counter += 1
        return self.runtime.execute(credential["token"], operation, args,
                                    idempotency_key=f"test-{self.counter}", expected_version=version)

    def workspace(self, credential=None):
        return self.runtime.execute((credential or self.agent)["token"], "workspace", {})["result"]

    def edit(self, **changes):
        brief = dict(self.workspace()["brief"])
        version = brief.pop("version")
        brief.pop("brief_id")
        brief.pop("updated_at")
        brief.update(changes)
        return self.command("save_brief", {"expected_version": version, "brief": brief})

    def publish(self, credential=None):
        credential = credential or self.agent
        board = self.workspace(credential)["boards"][0]["board_id"]
        preview = self.command("preview_publication", {"board_id": board, "expires_in_days": 14,
                                "include": ["working_arrangement", "feedback", "schedule", "commercial_note"]}, credential)["result"]
        args = {"board_id": board, **{k: preview[k] for k in ("expires_at", "include", "approval_digest")}}
        return self.command("approve_publication", args, credential)

    def assert_code(self, code, call):
        with self.assertRaises(AgentRuntimeError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_owner_is_bound_to_token_and_cannot_be_selected_in_arguments(self):
        self.assertEqual(self.workspace()["owner"]["owner_id"], "marlow")
        self.assert_code("INVALID_ARGUMENTS", lambda: self.runtime.execute(self.agent["token"], "workspace", {"owner_id": "juniper"}))
        forged = self.agent["token"].replace("am1.", "am1.juniper", 1)
        self.assert_code("AUTH_REQUIRED", lambda: self.runtime.execute(forged, "workspace", {}))

    def test_viewer_cannot_acquire_write_scopes_or_execute_mutations(self):
        self.assert_code("INVALID_CREDENTIAL_POLICY", lambda: self.runtime.issue_credential("marlow", "viewer", ["workspace", "save_brief"]))
        viewer = self.runtime.issue_credential("marlow", "viewer", ["workspace"])
        self.assertEqual(self.workspace(viewer)["owner"]["owner_id"], "marlow")
        self.assert_code("SCOPE_REQUIRED", lambda: self.runtime.execute(viewer["token"], "withdraw_notice", {"notice_id": "notice-0001"}, "viewer-write", 0))

    def test_scope_is_upper_bound_even_with_trusted_command_grant_request(self):
        limited = self.runtime.issue_credential("marlow", "agent", ["workspace"])
        args = {"text": "", "limit": 5}
        self.assert_code("SCOPE_REQUIRED", lambda: self.runtime.authorize_command(limited["id"], "search", args))
        self.assert_code("SCOPE_REQUIRED", lambda: self.runtime.execute(limited["token"], "search", args, "search", 0))

    def test_outward_search_needs_exact_approval_and_edited_terms_do_not_inherit_it(self):
        args = {"text": "", "limit": 5}
        self.assert_code("APPROVAL_REQUIRED", lambda: self.command("search", args, authorize=False))
        grant = self.runtime.authorize_command(self.agent["id"], "search", args)
        self.assertEqual(grant["digest"], command_digest("search", args))
        changed = {"text": "FICTIONAL_QUERY_CANARY", "limit": 5}
        self.assert_code("APPROVAL_REQUIRED", lambda: self.command("search", changed, authorize=False))
        result = self.command("search", args, authorize=False)
        self.assertIn("results", result["result"])
        self.assert_code("APPROVAL_REQUIRED", lambda: self.command("search", args, authorize=False))

    def test_exact_grant_does_not_transfer_to_another_credential(self):
        second = self.runtime.issue_credential("marlow", "agent", ["workspace", "search"])
        args = {"text": "", "limit": 5}
        self.runtime.authorize_command(self.agent["id"], "search", args)
        self.assert_code("APPROVAL_REQUIRED", lambda: self.command("search", args, second, authorize=False))

    def test_preview_digest_does_not_itself_approve_publication(self):
        board = self.workspace()["boards"][0]["board_id"]
        preview = self.command("preview_publication", {"board_id": board, "include": [], "expires_in_days": 7})["result"]
        args = {"board_id": board, **{k: preview[k] for k in ("expires_at", "include", "approval_digest")}}
        before = self.workspace()["publications"]
        self.assert_code("APPROVAL_REQUIRED", lambda: self.command("approve_publication", args, authorize=False))
        self.assertEqual(self.workspace()["publications"], before)
        self.command("approve_publication", args)
        self.assertNotEqual(self.workspace()["publications"], before)

    def test_grant_rechecks_global_version_and_real_expiry(self):
        args = {"text": "", "limit": 5}
        self.runtime.authorize_command(self.agent["id"], "search", args, ttl_seconds=5)
        self.now += timedelta(seconds=6)
        self.assert_code("APPROVAL_STALE", lambda: self.command("search", args, authorize=False))
        self.runtime.authorize_command(self.agent["id"], "search", args)
        self.edit(private_notes=CANARY)
        self.assert_code("APPROVAL_STALE", lambda: self.command("search", args, authorize=False))

    def test_credentials_expire_by_real_clock_while_workflow_time_is_fixed(self):
        credential = self.runtime.issue_credential("marlow", "agent", ["workspace"], ttl_seconds=1)
        initial_time = self.runtime.capabilities(credential["token"])["result"]["workflow_time"]
        self.now += timedelta(seconds=2)
        self.assert_code("CREDENTIAL_INACTIVE", lambda: self.runtime.execute(credential["token"], "workspace", {}))
        self.assertEqual(self.runtime.capabilities(self.agent["token"])["result"]["workflow_time"], initial_time)

    def test_restart_retains_private_state_without_plaintext_or_token_in_database(self):
        self.edit(private_notes=CANARY)
        raw = self.path.read_bytes()
        self.assertNotIn(CANARY.encode(), raw)
        self.assertNotIn(self.agent["token"].encode(), raw)
        self.assertNotIn(b"marlow", raw)
        restarted = self.fresh()
        self.assertEqual(restarted.execute(self.agent["token"], "workspace", {})["result"]["brief"]["private_notes"], CANARY)
        with restarted.vault.transaction(write=False) as state:
            self.assertNotIn(self.agent["token"], json.dumps(state))

    def test_wrong_key_corruption_and_changed_epoch_fail_closed(self):
        with self.assertRaises(VaultError):
            AgentRuntime.open(self.path, secrets.token_bytes(32))
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("UPDATE vault SET epoch=epoch+1")
        with self.assertRaises(VaultError):
            self.fresh()

    def test_ciphertext_bit_flip_is_rejected_without_attempted_repair(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            value = bytearray(connection.execute("SELECT ciphertext FROM vault WHERE id=1").fetchone()[0])
            value[len(value) // 2] ^= 1
            connection.execute("UPDATE vault SET ciphertext=? WHERE id=1", (bytes(value),))
        changed = self.path.read_bytes()
        with self.assertRaises(VaultError):
            self.fresh()
        self.assertEqual(changed, self.path.read_bytes())

    def test_existing_vault_is_never_overwritten_during_create(self):
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            AgentRuntime.create(self.path, self.key)
        self.assertEqual(before, self.path.read_bytes())

    def test_revocation_persists_and_blocks_old_token_and_retry(self):
        args = {"text": "", "limit": 5}
        self.runtime.authorize_command(self.agent["id"], "search", args)
        self.runtime.execute(self.agent["token"], "search", args, "original", 0)
        self.runtime.revoke_credential(self.agent["id"])
        restarted = self.fresh()
        self.assert_code("CREDENTIAL_INACTIVE", lambda: restarted.execute(self.agent["token"], "search", args, "original", 0))

    def test_idempotent_retry_after_restart_has_one_effect_and_no_stale_payload(self):
        args = {"text": "", "limit": 5}
        self.runtime.authorize_command(self.agent["id"], "search", args)
        first = self.runtime.execute(self.agent["token"], "search", args, "retry-key", 0)
        uses = self.runtime.capabilities(self.agent["token"])["result"]["uses"]
        replay = self.fresh().execute(self.agent["token"], "search", args, "retry-key", 0)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["result"]["results"], first["result"]["results"])
        self.assertTrue(replay["result"]["coverage"]["recovered_original_candidates"])
        self.assertEqual(first["receipt"], replay["receipt"])
        self.assertEqual(uses, self.runtime.capabilities(self.agent["token"])["result"]["uses"])
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.runtime.execute(self.agent["token"], "search", {"text": "other", "limit": 5}, "retry-key", 0))

    def test_retry_never_restores_content_withdrawn_after_original_result(self):
        juniper = self.runtime.issue_credential("juniper", "agent", sorted(OPERATIONS))
        self.publish(juniper)
        args = {"text": "", "limit": 20}
        self.runtime.authorize_command(self.agent["id"], "search", args)
        version = self.runtime.capabilities(self.agent["token"])["version"]
        original = self.runtime.execute(self.agent["token"], "search", args, "search-before-withdrawal", version)
        card = next(item["card"] for item in original["result"]["results"] if item["card"]["publisher_alias"] == "Juniper")
        self.command("withdraw_notice", {"notice_id": card["notice_id"]}, juniper)
        replay = self.runtime.execute(self.agent["token"], "search", args, "search-before-withdrawal", version)
        self.assertTrue(replay["result"]["coverage"]["recovered_original_candidates"])
        self.assertNotIn("Juniper", json.dumps(replay))
        self.assert_code("NOTICE_UNAVAILABLE", lambda: self.command("explain", {"notice_id": card["notice_id"], "revision": card["revision"]}))

    def test_commit_failure_rolls_back_effect_budget_and_receipt(self):
        brief = dict(self.workspace()["brief"])
        brief_version = brief.pop("version")
        brief.pop("brief_id")
        brief.pop("updated_at")
        brief["private_notes"] = CANARY
        args = {"expected_version": brief_version, "brief": brief}
        before = self.runtime.capabilities(self.agent["token"])
        receipts = self.runtime.receipts(self.agent["token"])
        original_write = self.runtime.vault._write
        def fail_after_update(connection, epoch, state):
            original_write(connection, epoch, state)
            raise OSError("fictional disk failure after SQL update before commit")
        with patch.object(self.runtime.vault, "_write", side_effect=fail_after_update):
            with self.assertRaises(OSError):
                self.runtime.execute(self.agent["token"], "save_brief", args, "disk-failure", before["version"])
        restarted = self.fresh()
        self.assertEqual(before, restarted.capabilities(self.agent["token"]))
        self.assertEqual(receipts, restarted.receipts(self.agent["token"]))
        self.assertNotEqual(restarted.execute(self.agent["token"], "workspace", {})["result"]["brief"]["private_notes"], CANARY)

    def test_concurrent_instances_share_atomic_budget(self):
        credential = self.runtime.issue_credential("marlow", "agent", ["workspace"], max_uses=1)
        runtimes = [self.fresh(), self.fresh()]
        barrier = Barrier(2)
        def call(runtime):
            barrier.wait(timeout=5)
            try:
                runtime.execute(credential["token"], "workspace", {})
                return "completed"
            except AgentRuntimeError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(call, runtimes))
        self.assertEqual(sorted(results), ["BUDGET_EXHAUSTED", "completed"])
        self.assertEqual(self.runtime.capabilities(credential["token"])["result"]["uses"], 1)

    def test_concurrent_instances_same_retry_key_have_one_effect(self):
        args = {"text": "", "limit": 5}
        self.runtime.authorize_command(self.agent["id"], "search", args)
        runtimes = [self.fresh(), self.fresh()]
        barrier = Barrier(2)
        def call(runtime):
            barrier.wait(timeout=5)
            return runtime.execute(self.agent["token"], "search", args, "race", 0)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(call, runtimes))
        self.assertEqual(sorted(r["replayed"] for r in results), [False, True])
        self.assertEqual(results[0]["receipt"], results[1]["receipt"])
        self.assertEqual(self.runtime.capabilities(self.agent["token"])["result"]["uses"], 1)

    def test_denied_command_receipts_have_no_arguments_or_secret_values(self):
        self.assert_code("APPROVAL_REQUIRED", lambda: self.command("search", {"text": CANARY, "limit": 5}, authorize=False))
        entries = self.runtime.receipts(self.agent["token"])["result"]["items"]
        self.assertEqual(entries[-1]["code"], "APPROVAL_REQUIRED")
        self.assertNotIn(CANARY, json.dumps(entries))
        self.assertNotIn(self.agent["token"], json.dumps(entries))
        other = self.runtime.issue_credential("juniper", "viewer", ["workspace"])
        self.assertTrue(all(row["owner_id"] == "juniper" for row in self.runtime.receipts(other["token"])["result"]["items"]))

    def test_malformed_arguments_fail_without_domain_effect(self):
        before = self.runtime.capabilities(self.agent["token"])["version"]
        self.assert_code("INVALID_REQUEST", lambda: self.command("explain", {"notice_id": [], "revision": 1}))
        self.assertEqual(before, self.runtime.capabilities(self.agent["token"])["version"])

    def test_boolean_brief_revision_is_not_coerced_to_integer_one(self):
        brief = dict(self.workspace()["brief"])
        self.assertEqual(brief.pop("version"), 1)
        brief.pop("brief_id")
        brief.pop("updated_at")
        brief["private_notes"] = CANARY
        self.assert_code("INVALID_ARGUMENTS", lambda: self.command("save_brief", {"expected_version": True, "brief": brief}))
        self.assertNotEqual(self.workspace()["brief"]["private_notes"], CANARY)

    def test_narrow_mutation_scope_does_not_implicitly_read_private_workspace(self):
        self.publish()
        narrow = self.runtime.issue_credential("marlow", "agent", ["withdraw_notice"])
        notice = self.workspace()["publications"][0]["notice_id"]
        response = self.command("withdraw_notice", {"notice_id": notice}, narrow)
        self.assertEqual(response["result"], {"completed": True})

    def test_serialization_rejects_unknown_record_and_preserves_open_channel_after_restart(self):
        with self.assertRaises(ValueError):
            decode({"type": "record", "name": "subprocess.Popen", "value": {}})
        juniper = self.runtime.issue_credential("juniper", "agent", sorted(OPERATIONS))
        self.publish()
        self.publish(juniper)
        results = self.command("search", {"text": "", "limit": 20})["result"]["results"]
        card = next(item["card"] for item in results if item["card"]["publisher_alias"] == "Juniper")
        proposed = self.command("propose_introduction", {"notice_id": card["notice_id"], "revision": card["revision"], "first_step": "async-review", "note": "Fictional agent introduction"})
        intro = proposed["result"]["introductions"][-1]
        args = {"intro_id": intro["intro_id"], "data": {"proposal_digest": intro["proposal_digest"]}}
        self.command("accept", args)
        self.command("accept", args, juniper)
        self.runtime = self.fresh()
        restored = self.workspace()["introductions"][-1]
        self.assertEqual(restored["status"], "open")
        self.command("propose_step", {"intro_id": intro["intro_id"], "data": {"action": "scoping-call", "detail": "Invented review"}})
        self.command("agree_step", {"intro_id": intro["intro_id"], "data": {"version": 1}}, juniper)
        for credential in (self.agent, juniper):
            self.command("report_step", {"intro_id": intro["intro_id"], "data": {"version": 1, "outcome": "happened"}}, credential)
        self.command("close_introduction", {"intro_id": intro["intro_id"], "data": {"context_version": 1}})
        after = self.fresh().execute(self.agent["token"], "workspace", {})["result"]
        self.assertEqual(after["introductions"][-1]["status"], "closed")
        self.assertEqual(after["capacity"]["in_use"], 0)

    def mission_pair(self, *, max_uses=20, ttl_seconds=600):
        juniper = self.runtime.issue_credential("juniper", "agent", sorted(OPERATIONS))
        self.publish()
        self.publish(juniper)
        spaces = {c["id"]: self.workspace(c) for c in (self.agent, juniper)}
        missions = []
        for credential, target in ((self.agent, juniper), (juniper, self.agent)):
            own, candidate = spaces[credential["id"]]["publications"][0], spaces[target["id"]]["publications"][0]
            policy = {"own_notice": {k: own[k] for k in ("notice_id", "revision")},
                      "board_ids": [b["board_id"] for b in spaces[credential["id"]]["boards"]],
                      "candidate_notices": [{k: candidate[k] for k in ("notice_id", "revision")}],
                      "operations": ["search", "propose_introduction", "accept", "propose_step", "agree_step"],
                      "search_queries": [{"text": "", "limit": 20}], "first_steps": ["async-review", "scoping-call"],
                      "max_uses": max_uses, "ttl_seconds": ttl_seconds}
            missions.append(self.runtime.authorize_mission(credential["id"], policy))
        return juniper, missions

    def mission_command(self, operation, args, credential=None):
        return self.command(operation, args, credential, authorize=False)

    def change_authoritative_fixture(self, change):
        """Simulate board administration/source changes that have no public demo API."""
        with self.runtime.vault.transaction() as state:
            service = restore(state["workflow"])
            change(service)
            state["workflow"] = snapshot(service)
            state["version"] += 1

    def test_two_standing_missions_run_full_agreement_without_per_command_admin(self):
        juniper, missions = self.mission_pair()
        with patch.object(self.runtime, "authorize_command", side_effect=AssertionError("No per-command administrator allowed")):
            found = self.mission_command("search", {"text": "", "limit": 20})
            self.assertEqual([r["card"]["publisher_alias"] for r in found["result"]["results"]], ["Juniper"])
            self.assertEqual(found["result"]["coverage"]["mission_candidate_limit"], 1)
            self.assertEqual(found["receipt"]["authority"], "standing_mission")
            self.assertEqual(found["receipt"]["mission_id"], missions[0]["id"])
            card = found["result"]["results"][0]["card"]
            proposed = self.mission_command("propose_introduction", {"notice_id": card["notice_id"], "revision": card["revision"],
                                             "first_step": "async-review", "note": ""})
            intro = proposed["result"]["introductions"][-1]
            args = {"intro_id": intro["intro_id"], "data": {"proposal_digest": intro["proposal_digest"]}}
            self.mission_command("accept", args)
            self.mission_command("accept", args, juniper)
            self.mission_command("propose_step", {"intro_id": intro["intro_id"], "data": {"action": "scoping-call", "detail": ""}})
            self.mission_command("agree_step", {"intro_id": intro["intro_id"], "data": {"version": 1}}, juniper)
            self.assertEqual(self.workspace()["introductions"][-1]["first_step"]["state"], "agreed")
            self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("report_step", {
                "intro_id": intro["intro_id"], "data": {"version": 1, "outcome": "happened"}}))
        recovered = self.fresh()
        capabilities = recovered.capabilities(self.agent["token"])["result"]
        self.assertEqual(capabilities["missions"][0]["uses"], 4)
        self.assertNotIn("board_memberships", capabilities["missions"][0])

    def test_mission_blocks_query_candidate_first_step_and_free_text_substitutions(self):
        juniper, missions = self.mission_pair()
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("search", {"text": "unapproved", "limit": 20}))
        target = missions[0]["candidate_notices"][0]
        base = {"notice_id": target["notice_id"], "revision": target["revision"], "first_step": "async-review", "note": ""}
        for changes in ({"note": CANARY}, {"notice_id": "notice-0001"}, {"revision": 99}, {"first_step": "prototype-review"}):
            self.assert_code("APPROVAL_REQUIRED", lambda changes=changes: self.mission_command("propose_introduction", {**base, **changes}))
        self.assertEqual(self.runtime.capabilities(self.agent["token"])["result"]["missions"][0]["uses"], 0)

    def test_mission_requires_all_board_scopes_and_rejects_audience_expansion(self):
        juniper, missions = self.mission_pair()
        captured = missions[1]
        policy = {"own_notice": {k: captured["own_notice"][k] for k in ("notice_id", "revision")},
                  "candidate_notices": [{k: n[k] for k in ("notice_id", "revision")} for n in captured["candidate_notices"]],
                  "board_ids": ["community-tools"], "operations": ["search"], "search_queries": [{"text": "", "limit": 20}],
                  "first_steps": [], "max_uses": 5, "ttl_seconds": 600}
        self.assert_code("MISSION_BOARD_SCOPE_REQUIRED", lambda: self.runtime.authorize_mission(juniper["id"], policy))
        def expand(service):
            board = service.store.boards["community-tools"]
            service.store.boards[board.board_id] = replace(board, member_ids=(*board.member_ids, "fen"))
        self.change_authoritative_fixture(expand)
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("search", {"text": "", "limit": 20}))

    def test_mission_rejects_new_board_membership_not_only_changed_old_board(self):
        self.mission_pair()
        def admit(service):
            board = service.store.boards["makers"]
            service.store.boards[board.board_id] = replace(board, member_ids=(*board.member_ids, "marlow"))
        self.change_authoritative_fixture(admit)
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("search", {"text": "", "limit": 20}))

    def test_mission_stops_after_own_source_edit_and_target_withdrawal(self):
        juniper, missions = self.mission_pair()
        self.edit(private_notes=CANARY)
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("search", {"text": "", "limit": 20}))
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("search", {"text": "", "limit": 20}, juniper))
        # A newly approved owner notice does not silently replace its fixed ref.
        self.publish()
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("search", {"text": "", "limit": 20}))
        self.command("withdraw_notice", {"notice_id": missions[1]["own_notice"]["notice_id"]}, juniper)
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("search", {"text": "", "limit": 20}))

    def test_mission_budget_is_atomic_and_retry_does_not_debit_again(self):
        _, missions = self.mission_pair(max_uses=1)
        version = self.runtime.capabilities(self.agent["token"])["version"]
        args = {"text": "", "limit": 20}
        runtimes, barrier = [self.fresh(), self.fresh()], Barrier(2)
        def call(index):
            barrier.wait(timeout=5)
            try:
                return runtimes[index].execute(self.agent["token"], "search", args, f"mission-race-{index}", version)
            except AgentRuntimeError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(call, (0, 1)))
        successes = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(successes), 1)
        self.assertIn("APPROVAL_REQUIRED", results)
        winning_index = next(i for i, r in enumerate(results) if isinstance(r, dict))
        self.runtime.revoke_mission(self.agent["id"], missions[0]["id"])
        self.assert_code("SEARCH_RECOVERY_REVOKED", lambda: self.runtime.execute(
            self.agent["token"], "search", args, f"mission-race-{winning_index}", version))
        self.assertEqual(self.runtime.capabilities(self.agent["token"])["result"]["missions"][0]["uses"], 1)

    def test_mission_expiry_and_revocation_stop_new_actions(self):
        _, missions = self.mission_pair(ttl_seconds=1)
        self.now += timedelta(seconds=2)
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("search", {"text": "", "limit": 20}))
        self.runtime.revoke_mission(self.agent["id"], missions[0]["id"])
        self.assertTrue(self.fresh().capabilities(self.agent["token"])["result"]["missions"][0]["revoked"])

    def test_mission_does_not_accept_peer_added_text_or_wrong_proposal_digest(self):
        juniper, missions = self.mission_pair()
        target = missions[0]["candidate_notices"][0]
        intro = self.mission_command("propose_introduction", {"notice_id": target["notice_id"], "revision": target["revision"],
                                    "first_step": "async-review", "note": ""})["result"]["introductions"][-1]
        args = {"intro_id": intro["intro_id"], "data": {"proposal_digest": "0" * 64}}
        self.assert_code("CONTEXT_CHANGED", lambda: self.mission_command("accept", args, juniper))
        self.command("change", {"intro_id": intro["intro_id"], "data": {"first_step": "async-review", "note": CANARY}})
        changed = self.workspace()["introductions"][-1]
        args["data"]["proposal_digest"] = changed["proposal_digest"]
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("accept", args, juniper))

    def test_mission_checks_authority_after_channel_open_and_empty_step_detail(self):
        juniper, missions = self.mission_pair()
        target = missions[0]["candidate_notices"][0]
        intro = self.mission_command("propose_introduction", {"notice_id": target["notice_id"], "revision": target["revision"],
                                    "first_step": "async-review", "note": ""})["result"]["introductions"][-1]
        args = {"intro_id": intro["intro_id"], "data": {"proposal_digest": intro["proposal_digest"]}}
        self.mission_command("accept", args)
        self.mission_command("accept", args, juniper)
        step = {"intro_id": intro["intro_id"], "data": {"action": "scoping-call", "detail": CANARY}}
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("propose_step", step))
        self.command("propose_step", step)
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("agree_step", {"intro_id": intro["intro_id"], "data": {"version": 1}}, juniper))
        self.command("withdraw_notice", {"notice_id": target["notice_id"]}, juniper)
        step["data"]["detail"] = ""
        self.assert_code("APPROVAL_REQUIRED", lambda: self.mission_command("propose_step", step))


if __name__ == "__main__":
    unittest.main()
