"""Independent regressions for capacity, normal closure and bounded questions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from threading import Barrier
import unittest

from agentmatch_workbench.adapters.http_server import WorkbenchApp
from agentmatch_workbench.adapters.seed import build_service
from agentmatch_workbench.application.service import WorkbenchError
from agentmatch_workbench.domain.information import MAX_SESSION_REQUESTS

from .test_workbench_journey import explain, find_card, intro_view
from .test_workbench_publication import ALL_SECTIONS, CANARY, edit_brief, publish


def propose(service, owner, alias):
    card = find_card(service, owner, alias)["card"]
    workspace = service.propose_introduction(owner, {"notice_id": card["notice_id"], "revision": card["revision"],
                                                    "first_step": "async-review", "note": ""})
    return workspace["introductions"][-1]["intro_id"]


def accept_both(service, intro_id):
    for owner in service.store.introductions[intro_id].participants:
        digest = intro_view(service, owner, intro_id)["proposal_digest"]
        service.accept(owner, intro_id, {"proposal_digest": digest})


def information_payload(service, owner="marlow", alias="Sable", field="feedback_style"):
    response = explain(service, owner, alias)
    card, options = response["card"], response["information_request"]
    return {"notice_id": card["notice_id"], "revision": card["revision"], "field": field,
            "requester_notice_id": options["requester_notice_id"], "requester_revision": options["requester_revision"]}


class CapacityLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.service = build_service()
        edit_brief(self.service, "marlow", capacity=1)
        contributor = self.service.store.briefs["juniper"].content()
        edit_brief(self.service, "sable", **contributor)
        for owner in ("marlow", "juniper", "sable"):
            publish(self.service, owner)

    def test_distinct_proposers_cannot_overbook_recipient_and_first_can_accept(self):
        intro_id = propose(self.service, "juniper", "Marlow")
        self.assertEqual(self.service.workspace("marlow")["capacity"], {"limit": 1, "in_use": 1})
        response = explain(self.service, "sable", "Marlow")
        self.assertFalse(response["propose"]["allowed"])
        self.assertIn("This introduction is not available right now.", response["propose"]["blockers"])
        self.assertNotIn("capacity (1)", str(response["propose"]))
        with self.assertRaises(WorkbenchError):
            propose(self.service, "sable", "Marlow")
        accept_both(self.service, intro_id)
        self.assertEqual(intro_view(self.service, "marlow", intro_id)["status"], "open")

    def test_concurrent_distinct_proposals_reserve_one_recipient_slot(self):
        barrier = Barrier(2)
        def submit(owner):
            barrier.wait(timeout=5)
            try:
                propose(self.service, owner, "Marlow")
                return True
            except WorkbenchError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ("juniper", "sable")))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.service.workspace("marlow")["capacity"]["in_use"], 1)

    def test_decline_releases_both_reservations_for_another_pair(self):
        intro_id = propose(self.service, "juniper", "Marlow")
        self.service.decline("marlow", intro_id, {})
        self.assertEqual(self.service.workspace("juniper")["capacity"]["in_use"], 0)
        other = propose(self.service, "sable", "Marlow")
        self.assertNotEqual(other, intro_id)

    def test_normal_close_preserves_reports_releases_capacity_and_is_not_blocking(self):
        intro_id = propose(self.service, "juniper", "Marlow")
        accept_both(self.service, intro_id)
        self.service.propose_step("juniper", intro_id, {"action": "scoping-call", "detail": "Fictional trial"})
        self.service.agree_step("marlow", intro_id, {"version": 1})
        for owner in ("juniper", "marlow"):
            self.service.report_step(owner, intro_id, {"version": 1, "outcome": "happened"})
        before = intro_view(self.service, "marlow", intro_id)
        self.assertNotIn("propose_step", before["actions"])
        self.assertIn("close", before["actions"])
        self.service.close_introduction("marlow", intro_id, {"context_version": before["context_version"]})
        after = intro_view(self.service, "juniper", intro_id)
        self.assertEqual(after["status"], "closed")
        self.assertEqual(after["first_step"]["reports"], before["first_step"]["reports"])
        self.assertIn("not that the collaboration was useful", after["first_step"]["state_label"])
        self.assertEqual(after["actions"], [])
        self.assertEqual(after["channel"]["status"], "closed")
        self.assertEqual(self.service.store.blocked_pairs, set())
        for owner in ("juniper", "marlow"):
            self.assertEqual(self.service.workspace(owner)["capacity"]["in_use"], 0)
        propose(self.service, "sable", "Marlow")

    def test_normal_close_does_not_invent_an_action_or_require_both_to_leave(self):
        intro_id = propose(self.service, "juniper", "Marlow")
        accept_both(self.service, intro_id)
        self.service.close_introduction("marlow", intro_id, {"context_version": 1})
        view = intro_view(self.service, "juniper", intro_id)
        self.assertEqual(view["first_step"]["state"], "none")
        self.assertTrue(all(r["outcome"] is None for r in view["first_step"]["reports"]))
        log_length = len(self.service.store.activity["marlow"])
        self.service.close_introduction("marlow", intro_id, {"context_version": 1})
        self.assertEqual(len(self.service.store.activity["marlow"]), log_length)
        with self.assertRaises(WorkbenchError):
            self.service.close_introduction("marlow", intro_id, {"context_version": 2})
        with self.assertRaises(WorkbenchError):
            self.service.close_introduction("sable", intro_id, {"context_version": 1})

    def test_pending_introduction_uses_decline_not_normal_channel_close(self):
        intro_id = propose(self.service, "juniper", "Marlow")
        with self.assertRaises(WorkbenchError):
            self.service.close_introduction("marlow", intro_id, {"context_version": 1})
        self.assertEqual(intro_view(self.service, "marlow", intro_id)["status"], "pending")


class InformationRequestTests(unittest.TestCase):
    def setUp(self):
        self.service = build_service()
        edit_brief(self.service, "marlow", private_notes=CANARY)
        publish(self.service, "marlow")

    def request(self, **changes):
        payload = {**information_payload(self.service), **changes}
        self.service.request_information("marlow", payload)
        return self.service.workspace("marlow")["information_requests"][-1]

    def test_explicit_request_edit_reapproval_and_fresh_reassessment(self):
        before = explain(self.service, "marlow", "Sable")
        self.assertEqual(before["explanation"]["pair_status"], "needs_information")
        self.assertEqual(before["information_request"]["fields"], [{"id": "feedback_style", "label": "Feedback style"}])
        request = self.request()
        incoming = self.service.workspace("sable")["information_requests"][0]
        self.assertEqual(incoming["counterpart_alias"], "Marlow")
        self.assertEqual(incoming["actions"], ["review_brief", "decline"])
        edit_brief(self.service, "sable", private_notes="ANSWER-DRAFT-CANARY-238")
        waiting = self.service.workspace("marlow")["information_requests"][0]
        self.assertEqual(waiting["status"], "pending")
        self.assertIsNone(waiting["notice_id"])
        self.assertNotIn("ANSWER-DRAFT-CANARY-238", str(waiting))
        publish(self.service, "sable")
        answered = self.service.workspace("marlow")["information_requests"][0]
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["actions"], ["reassess"])
        self.assertGreater(answered["revision"], request["revision"])
        result = self.service.explain("marlow", {"notice_id": answered["notice_id"], "revision": answered["revision"]})
        # Sable's real fictional answer is gentle feedback, which does not
        # match Marlow's direct preference. Answered is not auto-qualified.
        self.assertEqual(result["explanation"]["pair_status"], "not_a_fit_now")
        self.assertEqual(self.service.store.introductions, {})

    def test_repeat_click_is_idempotent_and_cannot_request_private_or_known_fields(self):
        payload = information_payload(self.service)
        self.service.request_information("marlow", payload)
        activity = len(self.service.store.activity["sable"])
        self.service.request_information("marlow", payload)
        self.assertEqual(len(self.service.store.information_requests), 1)
        self.assertEqual(len(self.service.store.activity["sable"]), activity)
        for field in ("private_notes", "capacity", "basis", "arbitrary", [], None):
            with self.subTest(field=field), self.assertRaises(WorkbenchError):
                self.service.request_information("marlow", {**payload, "field": field})

    def test_request_requires_a_current_approved_requester_notice_and_exact_source_preview(self):
        payload = information_payload(self.service)
        edit_brief(self.service, "marlow", title="A different approved project title")
        with self.assertRaises(WorkbenchError):
            self.service.request_information("marlow", payload)
        publish(self.service, "marlow")
        with self.assertRaises(WorkbenchError):
            self.service.request_information("marlow", payload)
        self.assertEqual(self.service.store.information_requests, {})

    def test_requester_cannot_borrow_another_persons_published_identity(self):
        payload = information_payload(self.service)
        other = self.service.store.notices_by_owner("wren")[0]
        with self.assertRaises(WorkbenchError):
            self.service.request_information("marlow", {**payload, "requester_notice_id": other.notice_id,
                                                        "requester_revision": other.revision})

    def test_target_revision_and_visibility_are_rechecked_at_send(self):
        payload = information_payload(self.service)
        publish(self.service, "sable", include=["working_arrangement"])
        with self.assertRaises(WorkbenchError):
            self.service.request_information("marlow", payload)
        target = self.service.store.notices_by_owner("sable")[0]
        self.service.withdraw_notice("sable", {"notice_id": target.notice_id})
        with self.assertRaises(WorkbenchError):
            self.service.request_information("marlow", {**payload, "revision": target.revision})

    def test_reapproval_with_requested_field_still_omitted_stays_pending(self):
        self.request()
        publish(self.service, "sable", include=["working_arrangement"])
        view = self.service.workspace("marlow")["information_requests"][0]
        self.assertEqual(view["status"], "pending")
        self.assertEqual(view["actions"], [])

    def test_recipient_edit_exception_still_checks_approved_payload_digest(self):
        self.request()
        edit_brief(self.service, "sable", private_notes="STILL-PRIVATE")
        target = self.service.store.notices_by_owner("sable")[0]
        self.service.store.notices[target.notice_id] = replace(
            target, payload={**target.payload, "publisher_alias": "UNAPPROVED-ALIAS-CANARY"})
        self.assert_unavailable()
        for owner in ("marlow", "sable"):
            self.assertNotIn("UNAPPROVED-ALIAS-CANARY", str(self.service.workspace(owner)["information_requests"]))

    def test_undecided_basis_stays_unknown_until_explicitly_published_known(self):
        publish(self.service, "juniper")
        payload = information_payload(self.service, "juniper", "Calloway", "basis")
        self.service.request_information("juniper", payload)
        publish(self.service, "calloway")
        self.assertEqual(self.service.workspace("juniper")["information_requests"][0]["status"], "pending")
        edit_brief(self.service, "calloway", basis="paid")
        self.assertEqual(self.service.workspace("juniper")["information_requests"][0]["status"], "pending")
        publish(self.service, "calloway")
        self.assertEqual(self.service.workspace("juniper")["information_requests"][0]["status"], "answered")

    def test_decline_is_explicit_idempotent_and_cannot_be_bypassed_by_retry(self):
        payload = information_payload(self.service)
        request = self.request()
        with self.assertRaises(WorkbenchError):
            self.service.decline_information("marlow", request["request_id"], {})
        self.service.decline_information("sable", request["request_id"], {})
        self.service.decline_information("sable", request["request_id"], {})
        self.assertEqual(self.service.workspace("marlow")["information_requests"][0]["status"], "declined")
        with self.assertRaises(WorkbenchError):
            self.service.request_information("marlow", payload)
        publish(self.service, "sable")
        self.assertEqual(self.service.workspace("marlow")["information_requests"][0]["status"], "declined")

    def assert_unavailable(self, service=None):
        service = service or self.service
        for owner in ("marlow", "sable"):
            view = service.workspace(owner)["information_requests"][0]
            self.assertEqual(view["status"], "unavailable")
            for key in ("counterpart_alias", "field", "notice_id", "revision"):
                self.assertIsNone(view[key])
            self.assertEqual(view["actions"], [])

    def test_request_expiry_and_requester_edit_stop_future_disclosure(self):
        self.request()
        self.service.clock.advance(timedelta(days=8))
        self.assert_unavailable()
        self.setUp()
        self.request()
        edit_brief(self.service, "marlow", title="New requester context")
        self.assert_unavailable()

    def test_requester_withdrawal_and_block_stop_future_disclosure(self):
        self.request()
        source = self.service.store.notices_by_owner("marlow")[0]
        self.service.withdraw_notice("marlow", {"notice_id": source.notice_id})
        self.assert_unavailable()
        self.setUp()
        self.request()
        target = self.service.store.notices_by_owner("sable")[0]
        self.service.block_notice("marlow", {"notice_id": target.notice_id, "revision": target.revision})
        self.assert_unavailable()

    def test_answer_loses_access_after_membership_revocation(self):
        self.request()
        publish(self.service, "sable")
        self.assertEqual(self.service.workspace("marlow")["information_requests"][0]["status"], "answered")
        board = self.service.store.boards["community-tools"]
        self.service.store.boards[board.board_id] = replace(board, member_ids=tuple(o for o in board.member_ids if o != "marlow"))
        self.assert_unavailable()

    def test_private_values_never_enter_request_preview_or_peer_request_records(self):
        self.request()
        public = self.service.workspace("sable")["information_requests"]
        public += self.service.workspace("marlow")["information_requests"]
        public += [explain(self.service, "marlow", "Sable")["information_request"]]
        self.assertNotIn(CANARY, json.dumps(public))
        self.assertNotIn("private_notes", json.dumps(public))

    def test_declined_request_history_cannot_bypass_session_budget(self):
        # Small limits are a local anti-spam/memory bound, not production abuse defence.
        for _ in range(MAX_SESSION_REQUESTS):
            request = self.request()
            self.service.decline_information("sable", request["request_id"], {})
            publish(self.service, "sable", include=["working_arrangement"])
        with self.assertRaises(WorkbenchError):
            self.request()
        self.assertEqual(len(self.service.store.information_requests), MAX_SESSION_REQUESTS)

    def test_routes_preserve_request_guards_and_decline_ownership(self):
        app = WorkbenchApp(port=8765, ui_dir=None, token="test")
        app.service = self.service
        headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
                   "X-Workbench-Token": "test", "Content-Type": "application/json"}
        path = "/api/owners/marlow/information-requests"
        payload = json.dumps(information_payload(self.service)).encode()
        refused = app.handle("POST", path, {k: v for k, v in headers.items() if k != "Origin"}, payload)
        self.assertEqual(refused.status, 403)
        created = app.handle("POST", path, headers, payload)
        self.assertEqual(created.status, 200)
        request_id = json.loads(created.body)["information_requests"][0]["request_id"]
        rejected = app.handle("POST", f"/api/owners/juniper/information-requests/{request_id}/decline", headers, b"{}")
        self.assertGreaterEqual(rejected.status, 400)
        declined = app.handle("POST", f"/api/owners/sable/information-requests/{request_id}/decline", headers, b"{}")
        self.assertEqual(declined.status, 200)


if __name__ == "__main__":
    unittest.main()
