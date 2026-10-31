"""Regressions for operational QA-004 read recovery and QA-005 rolling budgets.

All people and content are fictional. Wall-clock changes are injected; tests
do not claim that a day elapsed or that a real public service was contacted.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import unittest

from agentmatch_runtime.runtime import AgentRuntime, AgentRuntimeError, OPERATIONS
from agentmatch_runtime.serialization import restore, snapshot
from agentmatch_workbench.domain.information import InformationRequest


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "optional encrypted runtime dependency required")
class RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "vault.sqlite"
        self.key = secrets.token_bytes(32)
        self.now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        self.runtime = AgentRuntime.create(self.path, self.key, clock=lambda: self.now)
        self.credentials = {}
        self.sequence = 0

    def credential(self, owner="marlow", *, uses=1000, scopes=None):
        credential = self.runtime.issue_credential(owner, "agent", scopes or sorted(OPERATIONS),
                                                  ttl_seconds=30 * 86400, max_uses=uses)
        self.credentials[owner] = credential
        return credential

    def fresh(self):
        return AgentRuntime.open(self.path, self.key, clock=lambda: self.now)

    def execute(self, owner, operation, args, *, grant=False):
        credential = self.credentials[owner]
        if grant:
            self.runtime.authorize_command(credential["id"], operation, args)
        version = self.runtime.capabilities(credential["token"])["version"]
        self.sequence += 1
        return self.runtime.execute(credential["token"], operation, args, f"op-{self.sequence}", version)

    def publish(self, owner, *, include=None):
        preview = self.execute(owner, "preview_publication", {"board_id": "community-tools", "expires_in_days": 30,
                                                              "include": include or []})["result"]
        args = {"board_id": "community-tools", **{k: preview[k] for k in ("expires_at", "include", "approval_digest")}}
        return self.execute(owner, "approve_publication", args, grant=True)

    def setup_search(self, *, uses=1, ttl=300):
        credential = self.credential(uses=uses, scopes=["search", "workspace"])
        query = {"text": "", "limit": 20}
        self.runtime.authorize_command(credential["id"], "search", query, ttl_seconds=ttl)
        version = self.runtime.capabilities(credential["token"])["version"]
        first = self.runtime.execute(credential["token"], "search", query, "lost-search", version)
        return credential, query, version, first

    def assert_code(self, code, call):
        with self.assertRaises(AgentRuntimeError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_one_use_lost_search_recovers_after_reopen_three_times_without_second_effect(self):
        credential, query, version, first = self.setup_search()
        self.assertGreater(len(first["result"]["results"]), 0)
        with self.runtime.vault.transaction(write=False) as state:
            original_log = restore(state["workflow"]).store.activity["marlow"]
            recovery = next(iter(state["idempotency"][credential["id"]].values()))["search_recovery"]
            self.assertEqual(set(recovery), {"schema", "expires_at", "recoveries", "mission_id", "board_memberships", "references"})
            self.assertTrue(all(set(ref) == {"notice_id", "revision"} for ref in recovery["references"]))
        reopened = self.fresh()
        for count in (1, 2, 3):
            replay = reopened.execute(credential["token"], "search", query, "lost-search", version)
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["receipt"], first["receipt"])
            self.assertEqual(replay["recovery"]["count"], count)
            self.assertEqual(replay["result"]["results"], first["result"]["results"])
        self.assertEqual(reopened.capabilities(credential["token"])["result"]["uses"], 1)
        self.assert_code("SEARCH_RECOVERY_LIMIT", lambda: reopened.execute(credential["token"], "search", query, "lost-search", version))
        with reopened.vault.transaction(write=False) as state:
            self.assertEqual(original_log, restore(state["workflow"]).store.activity["marlow"])
            self.assertEqual(sum(r["operation"] == "search" and r["status"] == "completed" for r in state["receipts"]), 1)

    def test_recovery_omits_withdrawn_and_revised_notices_and_never_adds_new_candidates(self):
        credential, query, version, first = self.setup_search()
        self.credential("sable")
        self.credential("brook")
        self.credential("juniper")
        sable = next(row["card"] for row in first["result"]["results"] if row["card"]["publisher_alias"] == "Sable")
        self.execute("sable", "withdraw_notice", {"notice_id": sable["notice_id"]})
        self.publish("brook")  # new revision of an original candidate is outside recovery's old refs
        self.publish("juniper")  # newly available candidate is outside the original returned set
        replay = self.fresh().execute(credential["token"], "search", query, "lost-search", version)
        aliases = [row["card"]["publisher_alias"] for row in replay["result"]["results"]]
        self.assertNotIn("Sable", aliases)
        self.assertNotIn("Brook", aliases)
        self.assertNotIn("Juniper", aliases)
        original_aliases = {row["card"]["publisher_alias"] for row in first["result"]["results"]}
        self.assertEqual(set(aliases), original_aliases - {"Sable", "Brook"})

    def test_search_recovery_expiry_revocation_scope_and_audience_are_current(self):
        credential, query, version, first = self.setup_search(ttl=1)
        self.now += timedelta(seconds=2)
        self.assert_code("SEARCH_RECOVERY_EXPIRED", lambda: self.runtime.execute(credential["token"], "search", query, "lost-search", version))
        self.runtime.revoke_credential(credential["id"])
        self.assert_code("CREDENTIAL_INACTIVE", lambda: self.runtime.execute(credential["token"], "search", query, "lost-search", version))

    def test_search_recovery_refuses_expanded_board_and_reduced_scope(self):
        credential, query, version, _ = self.setup_search()
        with self.runtime.vault.transaction() as state:
            service = restore(state["workflow"])
            board = service.store.boards["community-tools"]
            service.store.boards[board.board_id] = replace(board, member_ids=(*board.member_ids, "fen"))
            state["workflow"] = snapshot(service)
        self.assert_code("SEARCH_RECOVERY_SCOPE_CHANGED", lambda: self.runtime.execute(credential["token"], "search", query, "lost-search", version))
        with self.runtime.vault.transaction() as state:
            state["credentials"][credential["id"]]["scopes"] = ["workspace"]
        self.assert_code("SCOPE_REQUIRED", lambda: self.runtime.execute(credential["token"], "search", query, "lost-search", version))

    def test_old_retry_without_provenance_cannot_invent_recovery_permission(self):
        credential, query, version, _ = self.setup_search()
        with self.runtime.vault.transaction() as state:
            next(iter(state["idempotency"][credential["id"]].values())).pop("search_recovery")
        self.assert_code("SEARCH_RECOVERY_UNAVAILABLE", lambda: self.fresh().execute(credential["token"], "search", query, "lost-search", version))

    def test_concurrent_search_recoveries_share_the_three_read_limit(self):
        credential, query, version, _ = self.setup_search()
        runtimes = [self.fresh() for _ in range(6)]
        def recover(runtime):
            try:
                return runtime.execute(credential["token"], "search", query, "lost-search", version)["recovery"]["count"]
            except AgentRuntimeError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(recover, runtimes))
        self.assertEqual(sorted(r for r in results if isinstance(r, int)), [1, 2, 3])
        self.assertEqual(results.count("SEARCH_RECOVERY_LIMIT"), 3)
        self.assertEqual(self.runtime.capabilities(credential["token"])["result"]["uses"], 1)

    def prepare_information(self):
        self.credential("marlow")
        self.credential("sable")
        self.publish("marlow", include=["working_arrangement", "feedback"])

    def information_args(self, owner="marlow"):
        with self.runtime.vault.transaction(write=False) as state:
            service = restore(state["workflow"])
            target = service.store.notice_for("sable", "community-tools")
            mine = service.store.notice_for(owner, "community-tools")
        return {"notice_id": target.notice_id, "revision": target.revision, "field": "feedback_style",
                "requester_notice_id": mine.notice_id, "requester_revision": mine.revision}

    def fill_declined_window(self):
        self.prepare_information()
        for _ in range(20):
            response = self.execute("marlow", "request_information", self.information_args(), grant=True)
            request_id = response["result"]["information_requests"][-1]["request_id"]
            self.execute("sable", "decline_information", {"request_id": request_id, "data": {}})
            self.publish("sable")

    def test_clarification_window_replenishes_after_24h_without_erasing_history_or_reissuing_authority(self):
        self.fill_declined_window()
        for owner in ("marlow", "sable"):
            budget = self.runtime.capabilities(self.credentials[owner]["token"])["result"]["information_budget"]
            self.assertEqual(budget["sent_in_window" if owner == "marlow" else "received_in_window"], 20)
            self.assertEqual(budget["sent_pending" if owner == "marlow" else "received_pending"], 0)
        args = self.information_args()
        self.assert_code("INFORMATION_NOT_ALLOWED", lambda: self.execute("marlow", "request_information", args, grant=True))
        self.runtime = self.fresh()
        self.credential("marlow")  # a new token cannot reset an owner's budget
        self.assert_code("INFORMATION_NOT_ALLOWED", lambda: self.execute("marlow", "request_information", args, grant=True))
        self.now += timedelta(hours=24)
        self.assertEqual(self.runtime.capabilities(self.credentials["marlow"]["token"])["result"]["information_budget"]["sent_in_window"], 0)
        self.execute("marlow", "request_information", args, grant=True)
        with self.runtime.vault.transaction(write=False) as state:
            requests = restore(state["workflow"]).store.information_requests
            self.assertEqual(len(requests), 21)
            self.assertEqual(sum(r.state == "declined" for r in requests.values()), 20)

    def test_recipient_budget_cannot_be_bypassed_by_a_different_requester(self):
        self.fill_declined_window()
        self.credential("juniper")
        self.publish("juniper", include=["working_arrangement", "feedback"])
        args = self.information_args("juniper")
        self.assert_code("INFORMATION_NOT_ALLOWED", lambda: self.execute("juniper", "request_information", args, grant=True))
        self.now += timedelta(hours=24)
        self.execute("juniper", "request_information", args, grant=True)
        budget = self.runtime.capabilities(self.credentials["sable"]["token"])["result"]["information_budget"]
        self.assertEqual(budget["received_in_window"], 1)

    def test_legacy_accounting_migration_anchors_once_and_keeps_history(self):
        self.fill_declined_window()
        with self.runtime.vault.transaction() as state:
            def remove_old_field(value):
                if isinstance(value, dict):
                    if value.get("type") == "record" and value.get("name") == "InformationRequest":
                        value["value"].pop("accounted_at")
                    for item in value.values():
                        remove_old_field(item)
                elif isinstance(value, list):
                    for item in value:
                        remove_old_field(item)
            remove_old_field(state["workflow"])
        self.now += timedelta(days=2)
        self.runtime = self.fresh()
        before = self.runtime.capabilities(self.credentials["marlow"]["token"])["result"]["information_budget"]
        self.assertEqual(before["sent_in_window"], 20)
        self.now += timedelta(hours=1)
        self.runtime = self.fresh()
        after = self.runtime.capabilities(self.credentials["marlow"]["token"])["result"]["information_budget"]
        self.assertEqual(after["next_sent_reset_at"], before["next_sent_reset_at"])
        self.now += timedelta(hours=23)
        self.assertEqual(self.runtime.capabilities(self.credentials["marlow"]["token"])["result"]["information_budget"]["sent_in_window"], 0)
        with self.runtime.vault.transaction(write=False) as state:
            self.assertEqual(len(restore(state["workflow"]).store.information_requests), 20)

    def test_pending_limit_does_not_reset_at_24h_and_expires_after_seven_real_days(self):
        self.prepare_information()
        for _ in range(5):
            # A new target revision permits a new question, but does not erase
            # earlier pending requests or the receiver's finite queue.
            self.execute("marlow", "request_information", self.information_args(), grant=True)
            self.publish("sable")
        self.now += timedelta(hours=24)
        budget = self.runtime.capabilities(self.credentials["marlow"]["token"])["result"]["information_budget"]
        self.assertEqual(budget["sent_in_window"], 0)
        self.assertEqual(budget["sent_pending"], 5)
        self.assert_code("INFORMATION_NOT_ALLOWED", lambda: self.execute("marlow", "request_information", self.information_args(), grant=True))
        self.now += timedelta(days=6)
        budget = self.runtime.capabilities(self.credentials["marlow"]["token"])["result"]["information_budget"]
        self.assertEqual(budget["sent_pending"], 0)
        self.execute("marlow", "request_information", self.information_args(), grant=True)
        with self.runtime.vault.transaction(write=False) as state:
            requests = restore(state["workflow"]).store.information_requests
            self.assertEqual(len(requests), 6)
            self.assertEqual(sum(r.state == "unavailable" for r in requests.values()), 5)


if __name__ == "__main__":
    unittest.main()
