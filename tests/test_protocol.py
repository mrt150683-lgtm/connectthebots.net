"""Protocol policy tests. Peer authentication is a fictional input assumption."""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from agentmatch.simulator import (BudgetLedger, ContactReceiver, FictionalContactEnvelope, ProtocolEvent, ProtocolSimulator,
                                 ProtocolState, RunBudget, _negotiation, transition,
                                 usable_peer_claims, run_scenario)
from agentmatch.topics import SimulationError, canonical_bytes, digest, timestamp


NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def contact_pending() -> ProtocolSimulator:
    sim = _negotiation(NOW)
    a, b = sim.state.participant_ids
    sim.event("PROPOSAL", a, {"proposal_kind": "contact_release", "proposal_digest": "fixture-proposal",
                              "expires_at": timestamp(NOW + timedelta(days=7)), "both_locally_willing": True}, NOW)
    return sim


def accept_contact(sim: ProtocolSimulator, owner: str, *, expiry: datetime | None = None) -> None:
    sim.event("OWNER_ACCEPT", owner, {"proposal_kind": "contact_release", "proposal_digest": "fixture-proposal",
                                      "authorization_ref": "fixture-acceptance", "expires_at": timestamp(expiry or NOW + timedelta(days=1))}, NOW)


def release(sim: ProtocolSimulator, *, as_of: datetime = NOW, value: str = "fictional@example.invalid") -> ProtocolEvent:
    return sim.event("CONTACT_RELEASE", sim.state.participant_ids[0],
                     {"release_id": "release-1", "proposal_digest": "fixture-proposal",
                      "fictional_contact": value, "authority_current": True}, as_of)


class NegotiationTests(unittest.TestCase):
    def test_T_PROTO_001_one_session_and_coordinator(self) -> None:
        sim = _negotiation(NOW)
        self.assertEqual(sim.state.coordinator, "fixture-key-a")
        self.assertEqual(sim.state.status, "EVALUATING")
        self.assertEqual(len(sim.ledger.active), 1)
        with self.assertRaisesRegex(SimulationError, "PAIR_SESSION"):
            sim.ledger.reserve_session(sim.state.participant_ids, sim.state.purpose, "second-id", NOW)
        self.assertEqual(len(sim.ledger.active), 1)

    def test_T_PROTO_001_duplicate_setup_has_no_disclosure_effect(self) -> None:
        sim = ProtocolSimulator()
        a, b = sim.state.participant_ids
        sim.event("PAIR", a, {"fixture_invitation_verified": True}, NOW)
        sim.event("HELLO", a, {"setup_id": "setup-a", "nonce": "nonce-a"}, NOW)
        event = sim.event("HELLO", b, {"setup_id": "setup-b", "nonce": "nonce-b"}, NOW)
        self.assertEqual(sim.trace[-1]["effects"], [{"kind": "duplicate_setup_closed"}])
        self.assertEqual(sim.state.setup_id, "setup-a")
        self.assertIsNone(sim.state.session_id)

    def test_T_PROTO_002_fixture_binding_rejects_unknown_actor(self) -> None:
        state = ProtocolState(("a", "b"))
        event = ProtocolEvent("event-1", "PAIR", "unknown-key", 1, {"fixture_invitation_verified": True})
        with self.assertRaisesRegex(SimulationError, "PEER_BINDING"):
            transition(state, event, NOW)
        # This exercises the policy boundary only, not a signature or DTLS check.

    def test_T_PROTO_003_duplicate_idempotence_and_changed_payload_abort(self) -> None:
        sim = _negotiation(NOW)
        a, b = sim.state.participant_ids
        event = sim.event("QUESTION", a, {"question_ids": ["availability"], "ordinal": 1, "round": 1}, NOW)
        before = sim.ledger.summary()
        effects = sim.apply(event, NOW)
        self.assertEqual(effects, ({"kind": "duplicate_ignored"},))
        self.assertEqual(before, sim.ledger.summary())
        altered = replace(event, payload={"question_ids": ["current_goal"], "ordinal": 1, "round": 1})
        sim.apply(altered, NOW)
        self.assertEqual(sim.state.status, "ABORTED")
        self.assertEqual(sim.trace[-1]["effects"], [{"kind": "generic_stop"}])

    def test_T_PROTO_003_out_of_order_and_unrecognised_fields_reject(self) -> None:
        sim = _negotiation(NOW)
        a = sim.state.participant_ids[0]
        event = ProtocolEvent("bad", "QUESTION", a, 999, {"question_ids": ["availability"], "ordinal": 1, "round": 1})
        with self.assertRaisesRegex(SimulationError, "OUT_OF_ORDER"):
            sim.apply(event, NOW)
        with self.assertRaisesRegex(SimulationError, "INVALID_EVENT_FIELDS"):
            sim.event("QUESTION", a, {"question_ids": ["availability"], "ordinal": 1, "round": 1, "private_score": True}, NOW)
        self.assertEqual(sim.state.question_count, 0)

    def test_T_PROTO_003_state_event_matrix_rejects_out_of_context_effects(self) -> None:
        payloads = {
            "QUESTION": {"question_ids": ["current_goal"], "ordinal": 1, "round": 1},
            "ANSWER": {"question_event_id": "missing", "items": []},
            "OWNER_ACCEPT": {"proposal_kind": "channel_open", "proposal_digest": "missing", "authorization_ref": "x", "expires_at": timestamp(NOW + timedelta(days=1))},
            "CONTACT_RELEASE": {"release_id": "r", "proposal_digest": "missing", "fictional_contact": "a@example.invalid", "authority_current": True},
            "RELEASE_ACK": {"release_id": "r"},
        }
        for status in ("UNPAIRED", "PAIRED_IDLE", "OPENING", "DECLINED", "EXPIRED", "BLOCKED", "COMPLETE"):
            for kind, payload in payloads.items():
                with self.subTest(status=status, kind=kind), self.assertRaises(SimulationError):
                    transition(ProtocolState(("a", "b"), status=status), ProtocolEvent("m", kind, "a", 1, payload), NOW)

    def test_T_PROTO_003_ordered_question_turns_and_boolean_false(self) -> None:
        sim = _negotiation(NOW)
        a, b = sim.state.participant_ids
        with self.assertRaisesRegex(SimulationError, "QUESTION_NOT_PERMITTED"):
            sim.event("QUESTION", b, {"question_ids": ["availability"], "ordinal": 1, "round": 1}, NOW)
        question = sim.event("QUESTION", a, {"question_ids": ["availability"], "ordinal": 1, "round": 1}, NOW)
        with self.assertRaisesRegex(SimulationError, "ANSWER_NOT_CORRELATED"):
            sim.event("ANSWER", b, {"question_event_id": question.event_id, "items": []}, NOW)
        sim.event("ANSWER", b, {"question_event_id": question.event_id, "items": [{"kind": "value", "value": False}]}, NOW)
        self.assertEqual(sim.state.turn_index, 1)
        question = sim.event("QUESTION", b, {"question_ids": ["current_goal"], "ordinal": 2, "round": 1}, NOW)
        with self.assertRaisesRegex(SimulationError, "INVALID_EVENT_FIELDS"):
            sim.event("ANSWER", a, {"question_event_id": question.event_id, "items": [{"kind": "unavailable", "reason": "private refusal"}]}, NOW)
        sim.event("ANSWER", a, {"question_event_id": question.event_id, "items": [{"kind": "unavailable"}]}, NOW)
        self.assertEqual(sim.state.round_number, 2)
        self.assertEqual(sim.state.question_count, 2)

    def test_T_PROTO_004_total_questions_and_round_caps(self) -> None:
        sim = _negotiation(NOW)
        a, b = sim.state.participant_ids
        question = sim.event("QUESTION", a, {"question_ids": ["availability"] * 6, "ordinal": 1, "round": 1}, NOW)
        sim.event("ANSWER", b, {"question_event_id": question.event_id, "items": [{"kind": "unavailable"}] * 6}, NOW)
        with self.assertRaisesRegex(SimulationError, "PAIR_QUESTION_BUDGET"):
            sim.event("QUESTION", b, {"question_ids": ["availability"], "ordinal": 7, "round": 1}, NOW)
        self.assertEqual(sim.state.question_count, 6)
        for sender, receiver, round_number in ((b, a, 1), (a, b, 2), (b, a, 2)):
            question = sim.event("QUESTION", sender, {"question_ids": [], "ordinal": 7, "round": round_number}, NOW)
            sim.event("ANSWER", receiver, {"question_event_id": question.event_id, "items": []}, NOW)
        with self.assertRaisesRegex(SimulationError, "QUESTION_NOT_PERMITTED"):
            sim.event("QUESTION", a, {"question_ids": [], "ordinal": 7, "round": 3}, NOW)

    def test_T_PROTO_004_per_peer_policy_is_not_registry_wide_permission(self) -> None:
        sim = _negotiation(NOW)
        a, b = sim.state.participant_ids
        sim.event("POLICY_OFFER", b, {"question_ids": ["current_goal"]}, NOW)
        with self.assertRaisesRegex(SimulationError, "QUESTION_NOT_PERMITTED"):
            sim.event("QUESTION", a, {"question_ids": ["availability"], "ordinal": 1, "round": 1}, NOW)

    def test_T_PROTO_005_private_failures_collapse_to_same_stop(self) -> None:
        results = []
        for reason in ("unknown", "refused", "failed", "insufficient_evidence", "not_recommended"):
            sim = _negotiation(NOW)
            sim.event("DECLINE", sim.state.participant_ids[0], {"local_reason": reason}, NOW)
            results.append(sim.trace[-1]["effects"])
            self.assertEqual(sim.state.local_stop_reason, reason)
        self.assertEqual(results, [[{"kind": "generic_stop"}]] * 5)
        sim = _negotiation(NOW)
        with self.assertRaisesRegex(SimulationError, "QUESTION_NOT_PERMITTED"):
            sim.event("QUESTION", sim.state.participant_ids[0], {"question_ids": ["private_score_above_0.7"], "ordinal": 1, "round": 1}, NOW)

    def test_protocol_full_envelope_limit_and_clock_rollback(self) -> None:
        sim = _negotiation(NOW)
        with self.assertRaisesRegex(SimulationError, "ENVELOPE_TOO_LARGE"):
            sim.event("ABORT", sim.state.participant_ids[0], {"local_reason": "x" * 16384}, NOW)
        with self.assertRaisesRegex(SimulationError, "CLOCK_ROLLBACK"):
            sim.event("ABORT", sim.state.participant_ids[0], {}, NOW - timedelta(seconds=1))

    def test_T_PRIV_007_peer_claims_expire_and_learned_withdrawals_cancel_pending_use(self) -> None:
        sim = _negotiation(NOW)
        a, b = sim.state.participant_ids
        claim = {"alias": "peer-a-claim-1", "version": 1, "dimension": "current_goal", "value": "portable exhibit",
                 "provenance": "owner_stated", "expires_at": timestamp(NOW + timedelta(seconds=30))}
        sim.event("CLAIM_EXCHANGE", a, {"claims": [claim]}, NOW)
        self.assertEqual(len(usable_peer_claims(sim.state, NOW)), 1)
        self.assertEqual(len(usable_peer_claims(sim.state, NOW + timedelta(seconds=30))), 0)
        with self.assertRaisesRegex(SimulationError, "INVALID_EVENT_FIELDS"):
            sim.event("CLAIM_EXCHANGE", a, {"claims": [{**claim, "source_id": "PRIVATE_CANARY"}]}, NOW)
        with self.assertRaisesRegex(SimulationError, "VERSION_CHANGED"):
            sim.event("CLAIM_EXCHANGE", a, {"claims": [{**claim, "value": "altered claim"}]}, NOW)
        with self.assertRaisesRegex(SimulationError, "UNKNOWN_PEER_CLAIM_WITHDRAWAL"):
            sim.event("CLAIM_WITHDRAW", b, {"aliases": [{"alias": claim["alias"], "version": 1}]}, NOW)
        sim.event("PROPOSAL", a, {"proposal_kind": "channel_open", "proposal_digest": "fixture-context",
                                  "expires_at": timestamp(NOW + timedelta(days=1)), "both_locally_willing": True}, NOW)
        sim.event("CLAIM_WITHDRAW", a, {"aliases": [{"alias": claim["alias"], "version": 1}]}, NOW)
        self.assertEqual(sim.state.status, "WITHDRAWN")
        self.assertEqual(usable_peer_claims(sim.state, NOW), ())
        self.assertFalse(sim.state.accepted_by)
        self.assertNotIn("PRIVATE_CANARY", canonical_bytes(sim.trace).decode())

    def test_proposal_ack_is_not_owner_acceptance(self) -> None:
        sim = contact_pending()
        a, b = sim.state.participant_ids
        sim.event("PROPOSAL_ACK", b, {"proposal_kind": "contact_release", "proposal_digest": "fixture-proposal"}, NOW)
        self.assertEqual(set(sim.state.proposal_acknowledged_by), {a, b})
        self.assertEqual(sim.state.accepted_by, ())
        self.assertEqual(sim.state.status, "PROPOSAL_PENDING")
        with self.assertRaises(SimulationError):
            release(sim)


class BudgetTests(unittest.TestCase):
    def test_T_PROTO_004_concurrent_owner_counter_cannot_overspend(self) -> None:
        ledger = BudgetLedger()
        def attempt(index: int) -> bool:
            try:
                ledger.reserve_questions("same-owner", f"peer-{index}", 1, NOW)
                return True
            except SimulationError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(60)))
        self.assertEqual(sum(outcomes), 30)
        value = next(item["value"] for item in ledger.summary() if item["owner_id"] == "same-owner")
        self.assertEqual(value, 30)
        ledger.reserve_questions("same-owner", "peer-next-day", 1, NOW + timedelta(days=1))

    def test_T_PROTO_004_inbound_active_daily_and_release_limits(self) -> None:
        ledger = BudgetLedger()
        ledger.reserve_questions("sender", "receiver", 30, NOW)
        with self.assertRaises(SimulationError):
            ledger.reserve_questions("new-sender", "receiver", 1, NOW)
        for index in range(5):
            ledger.reserve_session(("owner", f"peer-{index}"), "collaboration", f"s-{index}", NOW)
        with self.assertRaisesRegex(SimulationError, "ACTIVE_SESSION"):
            ledger.reserve_session(("owner", "peer-5"), "collaboration", "s-5", NOW)
        for index in range(5):
            ledger.close_session(("owner", f"peer-{index}"), "collaboration")
        for index in range(5, 10):
            ledger.reserve_session(("owner", f"peer-{index}"), "collaboration", f"s-{index}", NOW)
            ledger.close_session(("owner", f"peer-{index}"), "collaboration")
        with self.assertRaisesRegex(SimulationError, "DAILY_SESSION"):
            ledger.reserve_session(("owner", "new-key"), "collaboration", "s-10", NOW)
        for index in range(3):
            ledger.reserve_release("owner", f"r-{index}", NOW)
            ledger.reserve_release("owner", f"r-{index}", NOW)
        with self.assertRaisesRegex(SimulationError, "DAILY_RELEASE"):
            ledger.reserve_release("owner", "r-3", NOW)

    def test_run_limits_are_separate_from_owner_budgets(self) -> None:
        budget = RunBudget()
        with self.assertRaisesRegex(SimulationError, "SHORTLIST_LIMIT"):
            budget.set_shortlist(tuple(f"p-{i}" for i in range(21)))
        budget.set_shortlist(tuple(f"p-{i}" for i in range(20)))
        for index in range(5):
            budget.reserve_probe(f"p-{index}")
            budget.reserve_probe(f"p-{index}")
        with self.assertRaisesRegex(SimulationError, "PROBE_LIMIT"):
            budget.reserve_probe("p-5")
        self.assertEqual(len(budget.probed), 5)


class ContactTests(unittest.TestCase):
    def test_T_PROTO_006_one_acceptance_wrong_kind_and_exact_value_gate(self) -> None:
        sim = contact_pending()
        a, b = sim.state.participant_ids
        accept_contact(sim, a)
        with self.assertRaisesRegex(SimulationError, "CONTACT_RELEASE_NOT_AUTHORISED"):
            release(sim)
        accept_contact(sim, b)
        with self.assertRaisesRegex(SimulationError, "EXACT_CONTACT_PERMISSION"):
            release(sim)
        sim.authorise_fictional_contact(a, "fictional@example.invalid", expires_at=NOW + timedelta(days=1))
        with self.assertRaisesRegex(SimulationError, "EXACT_CONTACT_PERMISSION"):
            release(sim, value="different@example.invalid")
        with self.assertRaisesRegex(SimulationError, "CONTACT_RELEASE_NOT_AUTHORISED"):
            release(sim, value="someone@example.com")
        sim.revoke_fictional_contact(a)
        with self.assertRaisesRegex(SimulationError, "EXACT_CONTACT_PERMISSION"):
            release(sim)

    def test_T_PROTO_006_acceptance_expiry_blocks_even_fresh_session(self) -> None:
        sim = contact_pending()
        a, b = sim.state.participant_ids
        accept_contact(sim, a, expiry=NOW + timedelta(seconds=10))
        accept_contact(sim, b)
        sim.authorise_fictional_contact(a, "fictional@example.invalid", expires_at=NOW + timedelta(days=1))
        with self.assertRaisesRegex(SimulationError, "CONTACT_RELEASE_NOT_AUTHORISED"):
            release(sim, as_of=NOW + timedelta(seconds=10))

    def test_T_PROTO_007_attempt_receipt_retry_are_separate(self) -> None:
        sim = contact_pending()
        a, b = sim.state.participant_ids
        accept_contact(sim, a)
        accept_contact(sim, b)
        sim.authorise_fictional_contact(a, "fictional@example.invalid", expires_at=NOW + timedelta(days=1))
        event = release(sim)
        self.assertEqual(sim.state.status, "SENT_UNACKNOWLEDGED")
        self.assertFalse(sim.trace[-1]["effects"][0]["transmission_performed"])
        first = sim.ledger.summary()
        sim.apply(event, NOW)
        release(sim)
        self.assertEqual(sim.ledger.summary(), first)
        self.assertEqual(sim.state.release_id, "release-1")
        sim.event("RELEASE_ACK", b, {"release_id": "release-1"}, NOW)
        self.assertEqual(sim.state.status, "COMPLETE")
        self.assertFalse(sim.trace[-1]["effects"][0]["read_confirmation"])

    def test_T_PROTO_007_separate_receiver_can_have_contact_before_sender_knows(self) -> None:
        sim = contact_pending()
        a, b = sim.state.participant_ids
        accept_contact(sim, a)
        accept_contact(sim, b)
        receiver_state = replace(sim.state)
        receiver = ContactReceiver(b)
        sim.authorise_fictional_contact(a, "fictional@example.invalid", expires_at=NOW + timedelta(days=1))
        release(sim)
        envelope = FictionalContactEnvelope("release-1", a, b, "fixture-proposal", "fictional@example.invalid")
        first = receiver.receive(envelope, receiver_state, authenticated_sender_id=a, as_of=NOW)
        self.assertTrue(first["application_received"])
        self.assertFalse(first["duplicate"])
        self.assertEqual(sim.state.status, "SENT_UNACKNOWLEDGED")
        self.assertIsNone(receiver_state.release_id)
        self.assertEqual(receiver_state.status, "MUTUAL_ACCEPTED")
        release(sim)
        retry = receiver.receive(envelope, receiver_state, authenticated_sender_id=a, as_of=NOW)
        self.assertTrue(retry["duplicate"])
        self.assertEqual(len(receiver.received), 1)
        with self.assertRaisesRegex(SimulationError, "RELEASE_ID_REUSED"):
            receiver.receive(replace(envelope, fictional_contact="changed@example.invalid"), receiver_state,
                             authenticated_sender_id=a, as_of=NOW)
        with self.assertRaisesRegex(SimulationError, "BINDING_MISMATCH"):
            receiver.receive(envelope, receiver_state, authenticated_sender_id=b, as_of=NOW)
        with self.assertRaisesRegex(SimulationError, "RECEIPT_NOT_AUTHORISED"):
            receiver.receive(envelope, replace(receiver_state, status="WITHDRAWN"), authenticated_sender_id=a, as_of=NOW)
        sim.event("RELEASE_ACK", b, {"release_id": "release-1"}, NOW)
        self.assertEqual(sim.state.status, "COMPLETE")
        self.assertIsNone(receiver_state.release_id)
        result = run_scenario("contact_asymmetric_delivery", NOW)
        self.assertFalse(result["bilateral_delivery_atomic"])
        self.assertEqual(result["receiver_own_contact_release"], "not_sent")
        self.assertEqual(result["receiver_distinct_release_count"], 1)

    def test_proposal_review_after_evaluation_needs_fresh_session_for_release(self) -> None:
        sim = contact_pending()
        a, b = sim.state.participant_ids
        later = NOW + timedelta(seconds=601)
        for owner in (a, b):
            sim.event("OWNER_ACCEPT", owner, {"proposal_kind": "contact_release", "proposal_digest": "fixture-proposal",
                                              "authorization_ref": "fixture-acceptance", "expires_at": timestamp(NOW + timedelta(days=1))}, later)
        self.assertEqual(sim.state.status, "MUTUAL_ACCEPTED")
        sim.event("REFRESH_SESSION", a, {"fixture_same_peer_binding": True, "new_setup_id": "fresh-setup"}, later)
        sim.authorise_fictional_contact(a, "fictional@example.invalid", expires_at=NOW + timedelta(days=1))
        release(sim, as_of=later)
        self.assertEqual(sim.state.status, "SENT_UNACKNOWLEDGED")


if __name__ == "__main__":
    unittest.main()
