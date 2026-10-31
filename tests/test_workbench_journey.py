"""Workbench matching, introduction and first-step invariants (fictional data only)."""
from __future__ import annotations

from datetime import timedelta
import unittest

from agentmatch.channels import open_channel
from agentmatch.topics import SimulationError
from agentmatch_workbench.adapters.seed import build_service, load_seed
from agentmatch_workbench.application.service import WorkbenchError

from .test_workbench_publication import edit_brief, publish


def find_card(service, viewer, alias):
    for item in service.search(viewer, {"text": "", "limit": 20})["results"]:
        if item["card"]["publisher_alias"] == alias:
            return item
    raise AssertionError(f"{alias} not visible to {viewer}")


def explain(service, viewer, alias):
    card = find_card(service, viewer, alias)["card"]
    return service.explain(viewer, {"notice_id": card["notice_id"], "revision": card["revision"]})


def intro_view(service, owner, intro_id):
    return next(i for i in service.workspace(owner)["introductions"] if i["intro_id"] == intro_id)


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.service = build_service()
        publish(self.service, "marlow")
        publish(self.service, "juniper")

    def test_unknown_evidence_remains_unknown(self):
        # Sable did not publish a feedback style. Marlow's feedback preference is
        # therefore unknown: not a mismatch, and not enough to call it a match.
        result = explain(self.service, "marlow", "Sable")["explanation"]
        forward = result["directions"][0]
        self.assertEqual(forward["status"], "insufficient_evidence")
        self.assertEqual(result["pair_status"], "needs_information")
        self.assertTrue(any("feedback style" in s["statement"] for s in forward["unknowns"]))
        self.assertFalse(any("feedback" in s["statement"].lower() for s in forward["mismatches"]))
        self.assertFalse(explain(self.service, "marlow", "Sable")["propose"]["allowed"])

    def test_undecided_required_basis_is_unknown_and_blocks(self):
        result = explain(self.service, "juniper", "Calloway")["explanation"]
        forward = result["directions"][0]
        self.assertEqual(forward["status"], "needs_information")
        self.assertEqual([r["requirement"] for r in forward["requirements_unknown"]], ["basis"])

    def test_paid_only_and_volunteer_only_is_a_known_incompatibility(self):
        forward = explain(self.service, "juniper", "Teasel")["explanation"]["directions"][0]
        self.assertEqual(forward["status"], "not_eligible")
        self.assertIn("basis", [r["requirement"] for r in forward["requirements_failed"]])

    def test_shared_topics_without_a_useful_contribution_do_not_qualify(self):
        result = explain(self.service, "juniper", "Brook")["explanation"]
        self.assertIn("Maintenance planning", result["reciprocal"]["shared_focus"])
        self.assertEqual(result["pair_status"], "not_a_fit_now")
        self.assertIn("primary-need", [r["requirement"] for r in result["directions"][0]["requirements_failed"]])

    def test_same_capability_label_is_not_rejected(self):
        # Sable and Juniper both offer prototyping; Marlow can still meet either.
        self.assertEqual(find_card(self.service, "marlow", "Juniper")["assessment"]["pair_status"], "both_meet")
        self.assertNotEqual(find_card(self.service, "marlow", "Sable")["assessment"]["pair_status"], "not_a_fit_now")

    def test_reciprocal_benefit_and_no_numbers_in_explanation(self):
        result = explain(self.service, "juniper", "Marlow")
        self.assertEqual(result["explanation"]["pair_status"], "both_meet")
        self.assertEqual(result["explanation"]["reciprocal"]["they_offer_you"], ["Domain knowledge", "Access to a pilot site"])
        self.assertEqual(result["explanation"]["reciprocal"]["you_offer_them"], ["Prototype building"])
        self.assertNotIn("%", str(result))
        self.assertTrue(result["propose"]["allowed"])

    def test_both_meet_results_come_first(self):
        statuses = [item["assessment"]["pair_status"] for item in self.service.search("juniper", {"text": "", "limit": 20})["results"]]
        order = {"both_meet": 0, "needs_information": 1, "not_a_fit_now": 2}
        self.assertEqual(statuses, sorted(statuses, key=order.__getitem__))
        self.assertEqual(statuses[0], "both_meet")


class IntroductionTests(unittest.TestCase):
    def setUp(self):
        self.service = build_service()
        publish(self.service, "marlow")
        publish(self.service, "juniper")
        card = find_card(self.service, "juniper", "Marlow")["card"]
        workspace = self.service.propose_introduction("juniper", {"notice_id": card["notice_id"], "revision": card["revision"],
                                                                  "first_step": "async-review", "note": "Hello"})
        self.intro_id = workspace["introductions"][0]["intro_id"]

    def digest(self, owner):
        return intro_view(self.service, owner, self.intro_id)["proposal_digest"]

    def accept(self, owner, digest=None):
        return self.service.accept(owner, self.intro_id, {"proposal_digest": digest or self.digest(owner)})

    def test_proposing_is_not_accepting_and_one_acceptance_cannot_open_a_channel(self):
        view = intro_view(self.service, "marlow", self.intro_id)
        self.assertEqual([a["accepted"] for a in view["acceptances"]], [False, False])
        self.accept("juniper")
        view = intro_view(self.service, "marlow", self.intro_id)
        self.assertEqual(view["status"], "pending")
        self.assertIsNone(view["channel"])
        intro = self.service.store.introductions[self.intro_id]
        with self.assertRaises(SimulationError):  # the R0 gate also refuses directly
            open_channel(self.service._proposal(intro), tuple(intro.acceptances.values()), as_of=self.service.now(),
                         session_expires_at=self.service.now() + timedelta(minutes=10))

    def test_both_owner_perspectives_open_the_channel(self):
        self.accept("juniper")
        self.accept("marlow")
        for owner in ("juniper", "marlow"):
            view = intro_view(self.service, owner, self.intro_id)
            self.assertEqual(view["status"], "open")
            self.assertEqual(view["channel"]["human_messages"], 0)  # nothing written for anyone

    def test_changing_context_invalidates_both_acceptances(self):
        intro = self.service.store.introductions[self.intro_id]
        old_digest = self.digest("marlow")
        self.accept("juniper")
        # Hold both acceptances of version 1 without letting the channel open,
        # then change the context: neither may authorize version 2.
        from agentmatch.channels import acceptance_for
        old_proposal = self.service._proposal(intro)
        held = {o: acceptance_for(old_proposal, o, intro.expires_at) for o in intro.participants}
        self.service.change("marlow", self.intro_id, {"first_step": "scoping-call", "note": "Hello"})
        self.assertEqual(intro.acceptances, {})
        self.assertEqual(intro.context_version, 2)
        self.assertTrue(intro_view(self.service, "juniper", self.intro_id)["acceptance_cleared_by_change"])
        with self.assertRaises(SimulationError):
            open_channel(self.service._proposal(intro), tuple(held[o] for o in intro.participants),
                         as_of=self.service.now(), session_expires_at=self.service.now() + timedelta(minutes=10))
        with self.assertRaises(WorkbenchError) as raised:  # a stale screen cannot accept the new version
            self.accept("marlow", old_digest)
        self.assertEqual(raised.exception.code, "CONTEXT_CHANGED")
        self.accept("marlow")
        self.accept("juniper")
        self.assertEqual(intro.status, "open")

    def test_decline_closes_for_both(self):
        self.accept("juniper")
        self.service.decline("marlow", self.intro_id, {})
        self.assertEqual(intro_view(self.service, "juniper", self.intro_id)["status"], "declined")
        with self.assertRaises(WorkbenchError):
            self.accept("marlow")

    def test_expiry_before_both_accept(self):
        self.accept("juniper")
        self.service.clock.advance(timedelta(days=8))
        self.assertEqual(intro_view(self.service, "marlow", self.intro_id)["status"], "expired")
        with self.assertRaises(WorkbenchError):
            self.service.accept("marlow", self.intro_id, {"proposal_digest": "x"})

    def test_withdrawn_acceptance_and_withdrawn_notice(self):
        self.accept("juniper")
        self.service.withdraw_acceptance("juniper", self.intro_id, {})
        self.assertEqual([a["accepted"] for a in intro_view(self.service, "juniper", self.intro_id)["acceptances"]], [False, False])
        notice = self.service.store.notices_by_owner("marlow")[0]
        self.service.withdraw_notice("marlow", {"notice_id": notice.notice_id})
        view = intro_view(self.service, "juniper", self.intro_id)
        self.assertEqual(view["status"], "closed")
        self.assertIsNone(view["status_detail"])  # the other person is not told why

    def test_brief_edit_by_recipient_closes_pending_introduction(self):
        digest = self.digest("marlow")
        edit_brief(self.service, "marlow", hours_per_week=9)
        with self.assertRaises(WorkbenchError):
            self.accept("juniper", digest)
        self.assertEqual(intro_view(self.service, "juniper", self.intro_id)["status"], "closed")

    def test_block_closes_open_channel_and_hides_notices(self):
        self.accept("juniper")
        self.accept("marlow")
        self.service.block_introduction("marlow", self.intro_id, {})
        self.assertEqual(intro_view(self.service, "juniper", self.intro_id)["status"], "closed")
        self.assertIsNone(intro_view(self.service, "juniper", self.intro_id)["status_detail"])
        self.assertEqual(intro_view(self.service, "marlow", self.intro_id)["status_detail"], "You blocked this person.")
        self.assertTrue(self.service.store.introductions[self.intro_id].channel.closed)
        with self.assertRaises(WorkbenchError):
            self.service.propose_step("juniper", self.intro_id, {"action": "scoping-call", "detail": ""})

    def test_first_step_is_proposed_then_agreed_then_reported_separately(self):
        with self.assertRaises(WorkbenchError):  # no channel yet
            self.service.propose_step("juniper", self.intro_id, {"action": "scoping-call", "detail": ""})
        self.accept("juniper")
        self.accept("marlow")
        self.service.propose_step("juniper", self.intro_id, {"action": "scoping-call", "detail": "Tuesday"})
        step = intro_view(self.service, "marlow", self.intro_id)["first_step"]
        self.assertEqual(step["state"], "proposed")
        with self.assertRaises(WorkbenchError):  # cannot report an unagreed step
            self.service.report_step("juniper", self.intro_id, {"version": step["version"], "outcome": "happened"})
        self.service.agree_step("marlow", self.intro_id, {"version": step["version"]})
        self.assertEqual(intro_view(self.service, "marlow", self.intro_id)["first_step"]["state"], "agreed")
        self.service.report_step("marlow", self.intro_id, {"version": step["version"], "outcome": "happened"})
        step = intro_view(self.service, "juniper", self.intro_id)["first_step"]
        self.assertEqual(step["state"], "reported")  # one-sided report is not mutual confirmation
        self.assertEqual([r["outcome"] for r in step["reports"]], [None, "happened"])
        self.service.report_step("juniper", self.intro_id, {"version": step["version"], "outcome": "happened"})
        step = intro_view(self.service, "juniper", self.intro_id)["first_step"]
        self.assertEqual(step["state"], "both_reported_happened")
        self.assertIn("not that the collaboration was useful", step["state_label"])

    def test_changed_step_needs_fresh_agreement(self):
        self.accept("juniper")
        self.accept("marlow")
        self.service.propose_step("juniper", self.intro_id, {"action": "scoping-call", "detail": ""})
        self.service.propose_step("marlow", self.intro_id, {"action": "bounded-trial", "detail": ""})
        with self.assertRaises(WorkbenchError) as raised:
            self.service.agree_step("juniper", self.intro_id, {"version": 1})
        self.assertEqual(raised.exception.code, "STEP_CHANGED")

    def test_capacity_and_duplicate_rules(self):
        card = find_card(self.service, "juniper", "Marlow")["card"]
        with self.assertRaises(WorkbenchError) as raised:
            self.service.propose_introduction("juniper", {"notice_id": card["notice_id"], "revision": card["revision"],
                                                          "first_step": "async-review", "note": ""})
        self.assertEqual(raised.exception.code, "INTRODUCTION_NOT_ALLOWED")

    def test_shared_context_uses_only_published_notice_content(self):
        context = intro_view(self.service, "marlow", self.intro_id)["context"]
        self.assertEqual(context["achieve"]["from_alias"], "Marlow")
        text = str(context)
        for private in ("steering group", "one new client", "selective"):
            self.assertNotIn(private, text)


class EntryPathTests(unittest.TestCase):
    def test_both_entry_paths_from_examples_reach_a_qualified_pair(self):
        service = build_service()
        examples = load_seed()["examples"]
        # Wren rewrites their brief from the project example; Juniper keeps the contributor path.
        project = {**examples["project"], "focus_areas": ["repair-cafes", "maintenance-planning"]}
        edit_brief(service, "wren", **project)
        publish(service, "wren")
        publish(service, "juniper")
        self.assertEqual(service.store.briefs["wren"].mode, "project")
        self.assertEqual(find_card(service, "juniper", "Wren")["assessment"]["pair_status"], "both_meet")
        self.assertEqual(find_card(service, "wren", "Juniper")["assessment"]["pair_status"], "both_meet")
        contributor = {**examples["contribute"]}
        edit_brief(service, "brook", **contributor)
        self.assertEqual(service.store.briefs["brook"].mode, "contribute")


if __name__ == "__main__":
    unittest.main()
