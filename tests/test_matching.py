"""Independent arithmetic and decision oracles; fictional inputs only."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction as F
import unittest

from agentmatch.claims import approve_claim, resolve_usable, revise_claim, revoke_claim, invalidate_source
from agentmatch.config import selectivity, load_defaults, validate_contract
from agentmatch.fixtures import load_corpus
from agentmatch.models import load_owner, load_claim, parse_time, EvidenceClaim, PreferenceAtom, PairAssessment
from agentmatch.numeric import exact, strict_json_loads, canonical_json
from agentmatch.ranking import evaluate_owner, rank_qualified
from agentmatch.scoring import assess_direction, assess_pair_offline

NOW = parse_time("2026-10-25T12:00:00Z")


def altered(owner, field, **changes):
    data = owner.to_dict()
    for claim in data["claims"]:
        if claim["dimension"] == field:
            claim.update(changes)
    return load_owner(data)


class MatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = load_corpus()
        cls.a = cls.corpus.owner("lumen")
        cls.b = cls.corpus.owner("harbor")

    def test_T_MATCH_001_worked_reciprocal_exact(self):
        pair = assess_pair_offline(self.a, self.b, NOW)
        self.assertEqual((pair.a_to_b.preference_fit, pair.a_to_b.supported_fit, pair.a_to_b.coverage), (F(17,20), F(41,50), F(1)))
        self.assertEqual((pair.b_to_a.preference_fit, pair.b_to_a.supported_fit, pair.b_to_a.coverage), (F(4,5), F(77,100), F(1)))
        self.assertTrue(pair.qualifies)
        self.assertEqual(pair.reciprocal_supported_fit, F(77,100))

    def test_T_MATCH_002_hard_failure_overrides_score(self):
        result = assess_direction(self.corpus.owner("birch"), self.corpus.owner("aster"), NOW)
        self.assertEqual(result.status, "not_eligible")
        self.assertEqual(result.atoms, ())

    def test_T_MATCH_003_unknown_mandatory_abstains(self):
        result = assess_direction(self.corpus.owner("cobalt"), self.corpus.owner("dune"), NOW)
        self.assertEqual(result.status, "needs_information")

    def test_known_hard_failure_precedes_unknown_candidate_intent(self):
        wrong_purpose = replace(self.b, intent=replace(self.b.intent, purpose="friendship"))
        self.assertEqual(assess_direction(self.a, wrong_purpose, NOW).status, "not_eligible")
        expired_full = replace(self.b, intent=replace(self.b.intent, capacity=0, expires_at=NOW))
        self.assertEqual(assess_direction(self.a, expired_full, NOW).status, "not_eligible")

    def test_T_MATCH_004_sparse_not_renormalized(self):
        b = self.b
        for claim in b.claims:
            if claim.dimension not in {"offer.capabilities", "workflow.availability", "workflow.compensation"}:
                b = altered(b, claim.dimension, approval="unreviewed")
        result = assess_direction(self.a, b, NOW)
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertEqual(result.coverage, F(2,5))
        self.assertEqual(result.preference_fit, F(3,10))

    def test_T_MATCH_005_known_negative_counts_as_covered(self):
        b = self.b
        fields = {atom.field for _, atoms in self.a.intent.atoms for atom in atoms}
        for claim in b.claims:
            if claim.dimension in fields:
                if isinstance(claim.value, tuple):
                    b = altered(b, claim.dimension, value=[], complete=True)
                else:
                    b = altered(b, claim.dimension, value="mismatch")
        result = assess_direction(self.a, b, NOW)
        self.assertEqual((result.coverage, result.preference_fit, result.supported_fit), (F(1), F(0), F(0)))
        self.assertEqual(result.status, "not_recommended")
        self.assertTrue(assess_direction(self.a, b, NOW, F(0)).qualifies)

    def test_T_MATCH_006_all_inferences_qualify_without_hidden_s_floor(self):
        a, b = self.corpus.owner("aerolith"), self.corpus.owner("bramble")
        b = replace(b, claims=tuple(replace(claim, origin="model_inferred") for claim in b.claims))
        result = assess_direction(a, b, NOW, F(85,100))
        self.assertEqual((result.coverage, result.preference_fit, result.supported_fit), (F(1), F(1), F(7,10)))
        self.assertTrue(result.qualifies)

    def test_T_MATCH_007_and_SELECTIVITY_003_bilateral_policies(self):
        strict = assess_pair_offline(self.a, self.b, NOW, F(85,100), F(85,100))
        self.assertTrue(strict.a_to_b.qualifies)
        self.assertFalse(strict.b_to_a.qualifies)
        self.assertFalse(strict.qualifies)
        self.assertIsNone(strict.reciprocal_supported_fit)
        independent = assess_pair_offline(self.a, self.b, NOW, F(85,100), F(80,100))
        self.assertTrue(independent.qualifies)

    def test_T_MATCH_008_canonical_tags_do_not_match_synonyms(self):
        b = altered(self.b, "intent.problem_tags", value=["portable-learning-exhibit-with-extra-words"], complete=True)
        result = assess_direction(self.a, b, NOW)
        goal = [atom for atom in result.atoms if atom.family == "goal_problem_alignment"]
        self.assertTrue(all(atom.known and atom.fit == 0 for atom in goal))

    def test_T_MATCH_009_past_event_cannot_be_current_offer(self):
        b = altered(self.b, "offer.capabilities", origin="reported_event")
        result = assess_direction(self.a, b, NOW)
        self.assertEqual(result.coverage, F(3,5))
        self.assertEqual(result.status, "insufficient_evidence")

    def test_T_MATCH_010_contradiction_is_unknown(self):
        result = assess_direction(self.corpus.owner("zephyr"), self.corpus.owner("yarrow"), NOW)
        self.assertEqual(result.coverage, F(7,10))
        self.assertEqual(result.status, "insufficient_evidence")

    def test_T_MATCH_011_expired_intent_not_renewed_by_history(self):
        result = assess_direction(self.corpus.owner("xenon"), self.corpus.owner("willow"), NOW)
        self.assertEqual(result.status, "invalid_intent")

    def test_T_MATCH_012_alias_prestige_invariance(self):
        before = assess_direction(self.a, self.b, NOW).to_dict()
        after = assess_direction(self.a, replace(self.b, alias="World-famous founder"), NOW).to_dict()
        self.assertEqual(before, after)

    def test_T_MATCH_013_rarity_cannot_rescue_failure(self):
        pair = assess_pair_offline(self.corpus.owner("loam"), self.corpus.owner("kelp"), NOW)
        self.assertEqual(pair.a_to_b.status, "not_recommended")
        self.assertEqual(rank_qualified([pair], {"kelp": F(1)}), [])

    def test_T_MATCH_014_sensitive_defaults_and_inference_exclusions(self):
        owner = self.corpus.owner("grove")
        view = resolve_usable(owner, NOW)
        self.assertFalse(any(claim.dimension.startswith("politics.") for claim in view.claims))
        data = self.b.to_dict()
        exemplar = dict(data["claims"][0])
        exemplar.update(claim_id="secret-health", dimension="health.accessibility", value="sensitive", complete=None)
        data["claims"].append(exemplar)
        candidate = load_owner(data)
        self.assertEqual(assess_direction(self.a, candidate, NOW).to_dict(), assess_direction(self.a, self.b, NOW).to_dict())

    def test_T_MATCH_015_no_fill_when_capacity_zero(self):
        result = evaluate_owner(self.corpus.owner("flint"), self.corpus.owners, NOW)
        self.assertEqual(result["offered_owner_ids"], [])
        self.assertEqual(result["offer_limit"], 0)

    def test_T_MATCH_016_offline_scope_honesty(self):
        report = assess_pair_offline(self.a, self.b, NOW).to_dict()
        self.assertIn("not_a_verified_network_joint_score", report["scope"])
        self.assertEqual(report["a_to_b"]["visibility"], "local_private_assessment")

    def test_T_MATCH_017_population_and_atom_permutation(self):
        first = evaluate_owner(self.a, self.corpus.owners, NOW)
        second = evaluate_owner(self.a, reversed(self.corpus.owners), NOW)
        self.assertEqual(first, second)
        data = self.a.to_dict()
        for records in data["intent"]["atoms"].values():
            records.reverse()
        self.assertEqual(assess_direction(self.a, self.b, NOW).to_dict(), assess_direction(load_owner(data), self.b, NOW).to_dict())

    def test_T_MATCH_018_missing_optional_does_not_shrink_denominator(self):
        missing = altered(self.b, "communication.feedback", approval="unreviewed")
        result = assess_direction(self.a, missing, NOW)
        self.assertEqual((result.preference_fit, result.supported_fit, result.coverage, result.preference_fit_upper_bound), (F(3,4), F(3,4), F(9,10), F(17,20)))
        self.assertTrue(result.qualifies)
        strict = assess_direction(self.a, missing, NOW, F(85,100))
        self.assertEqual(strict.reasons, ("SELECTIVITY_UNRESOLVED",))
        self.assertEqual(assess_direction(self.a, missing, NOW, F(86,100)).status, "not_recommended")

    def test_T_MATCH_019_self_and_blocked(self):
        self.assertEqual(assess_direction(self.a, self.a, NOW).status, "not_eligible")
        blocked = replace(self.a, blocked_owner_ids=(self.b.owner_id,))
        self.assertEqual(assess_direction(blocked, self.b, NOW).status, "not_eligible")
        self.assertNotIn(self.a.owner_id, evaluate_owner(self.a, self.corpus.owners, NOW)["offered_owner_ids"])

    def test_T_MATCH_020_recurring_weights_exact_at_coverage_gate(self):
        data_a, data_b = self.a.to_dict(), self.b.to_dict()
        data_a["intent"]["hard_constraints"] = []
        data_a["intent"]["atoms"] = {}
        data_b["claims"] = []
        base = self.b.claims[0].to_dict()
        base.update(origin="current_owner_statement", contradiction_refs=[], approval="approved_for_local_use", lifecycle="active")
        families = [("capability_complementarity", "workflow.cap", 3, 2),
                    ("goal_problem_alignment", "workflow.goal", 1, 1),
                    ("practical_workflow_fit", "workflow.flow", 3, 2),
                    ("communication_fit", "communication.comm", 2, 1)]
        for family, prefix, total, known in families:
            atoms = []
            for index in range(total):
                field = prefix + str(index)
                atoms.append({"atom_id": prefix.replace(".", "-") + str(index), "field": field, "kind": "accepted", "accepted": ["yes"]})
                if index < known:
                    claim = dict(base)
                    claim.pop("complete", None)
                    claim.update(claim_id="recurring-" + str(len(data_b["claims"])), dimension=field, value="yes")
                    data_b["claims"].append(claim)
            data_a["intent"]["atoms"][family] = atoms
        a, b = load_owner(data_a), load_owner(data_b)
        result = assess_direction(a, b, NOW, F(3,4))
        self.assertEqual((result.coverage, result.preference_fit, result.supported_fit), (F(3,4), F(3,4), F(3,4)))
        self.assertTrue(result.qualifies)
        without_comm = replace(b, claims=tuple(claim for claim in b.claims if not claim.dimension.startswith("communication.")))
        result = assess_direction(a, without_comm, NOW, F(0))
        self.assertEqual(result.coverage, F(7,10))
        self.assertFalse(result.qualifies)

    def test_T_SELECTIVITY_002_monotonic_and_independent(self):
        sets = []
        for threshold in (F(0), F(2,5), F(3,5), F(85,100), F(1)):
            sets.append({owner.owner_id for owner in self.corpus.owners if assess_direction(self.a, owner, NOW, threshold).qualifies})
        for earlier, later in zip(sets, sets[1:]):
            self.assertTrue(later <= earlier)

    def test_incomplete_set_absence_is_unknown(self):
        missing = altered(self.b, "offer.capabilities", value=[], complete=False)
        result = assess_direction(self.a, missing, NOW)
        self.assertEqual(result.coverage, F(3,5))
        self.assertEqual(result.status, "insufficient_evidence")

    def test_revisions_require_specific_review_and_preserve_inference(self):
        claim = next(claim for claim in self.b.claims if claim.dimension == "communication.feedback")
        revised = revise_claim(self.b, claim.claim_id, "direct", ["new-fictional-source"], NOW)
        self.assertEqual(resolve_usable(revised, NOW).by_dimension("communication.feedback"), None)
        approved = approve_claim(revised, claim.claim_id, claim.version + 1)
        self.assertEqual(resolve_usable(approved, NOW).by_dimension("communication.feedback").origin, "model_inferred")
        self.assertEqual(assess_direction(self.a, approved, NOW).supported_fit, F(41,50))
        with self.assertRaisesRegex(ValueError, "STALE_REVIEW"):
            approve_claim(revised, claim.claim_id, claim.version)

    def test_revocation_source_deletion_and_expiry_are_immediate(self):
        claim = next(claim for claim in self.b.claims if claim.dimension == "intent.problem_tags")
        for changed in (revoke_claim(self.b, claim.claim_id), invalidate_source(self.b, claim.source_refs[0])):
            self.assertIsNone(resolve_usable(changed, NOW).by_dimension("intent.problem_tags"))
        expired = altered(self.b, "intent.problem_tags", expires_at="2026-10-25T12:00:00Z")
        self.assertEqual(assess_direction(self.a, expired, NOW).coverage, F(7,10))

    def test_frozen_applicability_and_invalid_empty_family(self):
        data = self.a.to_dict()
        data["intent"]["applicable_families"] = ["capability_complementarity"]
        reduced = load_owner(data)
        result = assess_direction(reduced, self.b, NOW)
        self.assertEqual((result.preference_fit, result.coverage), (F(3,4), F(1)))
        data = self.a.to_dict()
        data["intent"]["atoms"]["communication_fit"] = []
        self.assertEqual(assess_direction(load_owner(data), self.b, NOW).status, "invalid_intent")

    def test_ties_are_anchored_not_transitively_chained(self):
        pair = assess_pair_offline(self.a, self.b, NOW)
        def variant(candidate, score):
            return PairAssessment(replace(pair.a_to_b, candidate_id=candidate, supported_fit=score), replace(pair.b_to_a, seeker_id=candidate, supported_fit=score))
        high = variant("high", F(8,10))
        middle = variant("middle", F(8,10) - F(8,10000000))
        low = variant("low", F(8,10) - F(16,10000000))
        ordered = rank_qualified([low, middle, high], {"low": F(1), "middle": F(1,2)})
        self.assertEqual([item.a_to_b.candidate_id for item in ordered], ["middle", "high", "low"])

    def test_full_scan_offer_limit_and_duplicate_population(self):
        result = evaluate_owner(self.a, self.corpus.owners, NOW)
        self.assertEqual(result["scanned_candidates"], 39)
        self.assertLessEqual(len(result["offered_owner_ids"]), load_defaults()["r0"]["synthetic_offer_limit_per_run"])
        with self.assertRaisesRegex(ValueError, "DUPLICATE_OWNER"):
            evaluate_owner(self.a, [self.a, self.a], NOW)


class ParsingTests(unittest.TestCase):
    def test_decimal_exactness_bounds_and_float_rejection(self):
        self.assertEqual(exact("0.85"), F(17,20))
        self.assertEqual(exact(Decimal("1e-6")), F(1,1000000))
        for value in (0.85, True, "NaN", "Infinity", "1e999", "0." + "1" * 100, "10000000000"):
            with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                exact(value)

    def test_json_duplicate_unicode_nonfinite_and_nesting(self):
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '"\\ud800"', '[' * 30 + '0' + ']' * 30):
            with self.subTest(value=value[:20]), self.assertRaises(ValueError):
                strict_json_loads(value)
        self.assertIsInstance(strict_json_loads('{"a":0.85}')["a"], Decimal)

    def test_unknown_fields_versions_and_large_atom_inputs(self):
        base = load_corpus().owner("lumen").to_dict()
        for changes in ({"schema_version": "999"}, {"synthetic": False}, {"private_transcript": "forbidden"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load_owner({**base, **changes})
        base["intent"]["atoms"]["communication_fit"] *= 65
        with self.assertRaisesRegex(ValueError, "ATOM_LIMIT"):
            load_owner(base)

    def test_custom_selectivity_and_defensive_configuration(self):
        self.assertEqual(selectivity("close_fit"), F(17,20))
        self.assertEqual(selectivity(custom="0"), F(0))
        for kwargs in ({"preset": "open", "custom": "0.5"}, {"custom": "1.01"}, {"custom": "-0.01"}, {"preset": "imagined"}):
            with self.assertRaises(ValueError):
                selectivity(**kwargs)
        data = load_defaults()
        data["r0"]["allow_real_history_import"] = True
        self.assertFalse(load_defaults()["r0"]["allow_real_history_import"])

    def test_invalid_configuration_limits_fail_closed_before_runtime(self):
        mutations = [("protocol_pilot", "max_questions_total_per_pair", -1),
                     ("protocol_pilot", "proposal_ttl_seconds", "600"),
                     ("social", "max_encoded_chat_message_bytes", True),
                     ("offline", "max_queued_messages_per_channel", 0),
                     ("interview", "extended_max_questions_total", 2),
                     ("interview", "auto_approve_extracted_claims", True),
                     ("r0", "default_seed", -1)]
        for section, key, value in mutations:
            data = load_defaults()
            data[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaisesRegex(ValueError, "INVALID_CONFIGURATION"):
                validate_contract(data)
        for section, key in (("offline", "default_unsent_expiry_seconds"), ("interview", "review_generated_tokens_max")):
            data = load_defaults()
            del data[section][key]
            with self.assertRaisesRegex(ValueError, "INVALID_CONFIGURATION"):
                validate_contract(data)
        for name in ("selectivity_presets", "evidence_factors"):
            data = load_defaults()
            data["scoring_fixture_policy"][name]["unknown"] = "0.5"
            with self.assertRaisesRegex(ValueError, "INVALID_CONFIGURATION"):
                validate_contract(data)

    def test_malformed_enums_fail_with_stable_value_errors(self):
        owner = load_corpus().owner("harbor").to_dict()
        for key in ("origin", "approval", "lifecycle", "sensitive_family"):
            data = dict(owner["claims"][0])
            data[key] = ["not-a-string"]
            with self.subTest(key=key), self.assertRaises(ValueError):
                load_claim(data)


if __name__ == "__main__":
    unittest.main()
