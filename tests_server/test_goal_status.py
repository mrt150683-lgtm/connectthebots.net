"""Hand-worked M3 status oracles, independent of SQL and service writers."""
from copy import deepcopy
import unittest

from connectthebots_server.goal_findings import finding_status


def finding(status="visible"):
    return {"id": "original", "seq": 10, "status": status, "author": "c_author"}


def review(owner="c_first", verdict="supports", *, seq=11, independent=True, status="visible"):
    return {"id": "review_" + owner, "finding_id": "original", "seq": seq, "reviewer": owner,
            "verdict": verdict, "independent": independent, "status": status}


class FindingStatusTests(unittest.TestCase):
    def test_no_checks_and_nonchecking_verdicts_leave_new(self):
        self.assertEqual(finding_status(finding(), [], []), "new")
        self.assertEqual(finding_status(finding(), [review(verdict="could_not_check"), review("c_second", "needs_change")], []), "new")

    def test_one_independent_support_is_checked_two_distinct_owners_confirm(self):
        self.assertEqual(finding_status(finding(), [review()], []), "checked")
        self.assertEqual(finding_status(finding(), [review(), review("c_second")], []), "confirmed")
        self.assertEqual(finding_status(finding(), [review(), review(seq=12)], []), "checked")
        self.assertEqual(finding_status(finding(), [review("c_author")], []), "new")

    def test_referral_linked_support_never_counts_but_visible_dissent_does(self):
        self.assertEqual(finding_status(finding(), [review(independent=False)], []), "new")
        self.assertEqual(finding_status(finding(), [review(), review("c_second", independent=False)], []), "checked")
        self.assertEqual(finding_status(finding(), [review(), review("c_second", "does_not_support", independent=False)], []), "disputed")

    def test_any_open_dissent_overrides_support(self):
        reviews = [review(), review("c_second"), review("c_third", "does_not_support", seq=13)]
        self.assertEqual(finding_status(finding(), reviews, []), "disputed")

    def test_hidden_held_withdrawn_reviews_never_support_or_dispute(self):
        for status in ("hidden", "held", "withdrawn"):
            with self.subTest(status=status):
                self.assertEqual(finding_status(finding(), [review(status=status)], []), "new")
                self.assertEqual(finding_status(finding(), [review(), review("c_second", "does_not_support", status=status)], []), "checked")
        self.assertEqual(finding_status(finding(), [{**review(), "content": None}], []), "new")

    def test_finding_safety_and_erasure_states_always_override_checks(self):
        for status in ("held", "hidden", "withdrawn"):
            with self.subTest(status=status):
                self.assertEqual(finding_status(finding(status), [review(), review("c_second", "does_not_support")], []), status)

    def test_sensitive_pending_status_requires_qualifying_support(self):
        self.assertEqual(finding_status(finding("pending_peer_check"), [], []), "pending_peer_check")
        self.assertEqual(finding_status(finding("pending_peer_check"), [review(independent=False)], []), "pending_peer_check")
        self.assertEqual(finding_status(finding("pending_peer_check"), [review()], []), "checked")
        self.assertEqual(finding_status(finding("pending_peer_check"), [review(), review("c_second")], []), "confirmed")
        self.assertEqual(finding_status(finding("pending_peer_check"), [review(verdict="does_not_support")], []), "disputed")

    def test_only_checked_later_descendant_answers_dissent(self):
        reviews = [review(), review("c_second", "does_not_support", seq=13)]
        for status in ("new", "disputed", "pending_peer_check", "hidden", "held", "withdrawn"):
            descendant = {"id": "answer", "seq": 14, "builds_on": ["original"], "status": status}
            with self.subTest(status=status):
                self.assertEqual(finding_status(finding(), reviews, [descendant]), "disputed")
        answer = {"id": "answer", "seq": 14, "builds_on": ["original"], "status": "checked"}
        self.assertEqual(finding_status(finding(), reviews, [answer]), "checked")
        self.assertEqual(finding_status(finding(), reviews, [{**answer, "status": "confirmed"}]), "checked")
        self.assertEqual(finding_status(finding(), reviews, [{**answer, "seq": 12}]), "disputed")
        self.assertEqual(finding_status(finding(), reviews, [{**answer, "builds_on": ["unrelated"]}]), "disputed")

    def test_later_checked_descendant_can_be_transitive_or_cite_the_dissent(self):
        dissent = review("c_second", "does_not_support", seq=13)
        bridge = {"id": "bridge", "seq": 14, "builds_on": ["original"], "status": "new"}
        answer = {"id": "answer", "seq": 15, "builds_on": ["bridge"], "status": "checked"}
        self.assertEqual(finding_status(finding(), [review(), dissent], [answer, bridge]), "checked")
        answer["builds_on"] = [dissent["id"]]
        self.assertEqual(finding_status(finding(), [review(), dissent], [answer]), "checked")

    def test_dissent_after_the_answer_stays_open_and_inputs_are_unchanged(self):
        original = finding()
        reviews = [review(), review("c_second", "does_not_support", seq=13), review("c_third", "does_not_support", seq=16)]
        relations = [{"id": "answer", "seq": 14, "builds_on": ["original"], "status": "checked"}]
        saved = deepcopy((original, reviews, relations))
        self.assertEqual(finding_status(original, reviews, relations), "disputed")
        self.assertEqual((original, reviews, relations), saved)

    def test_reviews_of_another_finding_do_not_count_and_cycles_do_not_resolve(self):
        self.assertEqual(finding_status(finding(), [{**review(), "finding_id": "elsewhere"}], []), "new")
        cyclic = [{"id": "a", "seq": 15, "builds_on": ["b"], "status": "checked"},
                  {"id": "b", "seq": 14, "builds_on": ["a"], "status": "checked"}]
        self.assertEqual(finding_status(finding(), [review(verdict="does_not_support")], cyclic), "disputed")

    def test_checked_descendant_through_eleven_hundred_builds_on_edges_is_iterative(self):
        relations, previous = [], "original"
        for number in range(1101):
            node = {"id": "chain_" + str(number), "seq": 13 + number, "builds_on": [previous],
                    "status": "checked" if number == 1100 else "new"}
            relations.append(node)
            previous = node["id"]
        reviews = [review(), review("c_second", "does_not_support", seq=12)]
        self.assertEqual(finding_status(finding(), reviews, list(reversed(relations))), "checked")
        reviews.append(review("c_third", "does_not_support", seq=1114))
        self.assertEqual(finding_status(finding(), reviews, relations), "disputed")
