"""Independent closed-rule manifest and fictional M4 screening oracles."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from connectthebots_server.agent_common import AgentError
from connectthebots_server.agent_texts import GOAL_SAFETY_HOLD, GOAL_SAFETY_REFUSAL
from connectthebots_server.goal_safety import (RULE_MANIFEST, RULESET_SHA256, RULESET_VERSION,
                                             check_proposal, screen_content)


# This explicit test-side manifest is deliberately independent of production
# constants. A rule/list change must update it with a written rule rationale;
# changing code without this review makes both equality and fingerprint fail.
EXPECTED_MANIFEST = {
    "version": "goal-safety-v1",
    "prohibited_project": (
        r"\bweapons?\b", r"\bpathogens?\b", r"\btoxins?\b", r"\bgain[ -]of[ -]function\b",
        r"\bexplosives?\b", r"\bsurveillance\b", r"\bidentify(?:ing)? people\b",
        r"\bcyber[ -]attacks?\b", r"\battacks? on (?:computer )?systems\b", r"\belection persuasion\b",
    ),
    "contact": r"[^\s@]+@[^\s@]+\.[a-z]{2,}|https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|me|app|gg|co)\b|(?:\+?\d[\d\s().-]{7,}\d)"
               r"|\b(?:whatsapp|telegram|signal|snapchat|instagram|insta|discord|kik|wechat|text me|dm me|call me)\b",
    "contact_exempt_fields": ("sources", "artifact"),
    "payment": (
        r"\b(?:donate|paypal|gofundme|patreon)\b",
        r"\b(?:make|send|give|request|solicit|collect)\s+(?:(?:a|your|some)\s+)?donations?\b",
        r"\b(?:send|transfer)\s+(?:money|funds|cash|crypto|bitcoin|ethereum)\b",
        r"\b(?:send|pay|give)\s+(?:me|us)\s+(?:money|funds|cash)\b",
        r"\b[a-z]{2}\d{2}(?: ?[a-z0-9]){11,30}\b",
        r"\b[13][a-z1-9]{25,34}\b", r"\bbc1[ac-hj-np-z02-9]{11,87}\b", r"\b0x[0-9a-f]{40}\b",
    ),
    "recruitment": (
        r"\b(?:recruit(?:ing)?|looking for|seeking)\s+(?:patients?|trial participants)\b",
        r"\bvolunteers?\s+to\s+(?:try|take|receive)\b",
        r"\b(?:recruit(?:ing)?|looking for|seeking)\s+(?:people|participants|volunteers)\s+for\s+(?:(?:a|our|the)\s+)?(?:(?:clinical|medical|treatment)\s+)?trials?\b",
        r"\b(?:join|participate in|sign up for)\s+(?:(?:a|our|the)\s+)?(?:clinical|medical|treatment)\s+trials?\b",
    ),
    "personal_health": (
        r"\bmy\s+(?:diagnosis|medical records|blood\s*work|blood test results)\b",
        r"\bmy\s+(?:dog|cat|pet)(?:'s|s'|s)?\s+(?:blood\s*work|diagnosis|test results)\b",
    ),
    "named_health_templates": (
        r"\b[a-z][a-z'-]{1,29}(?:\s+[a-z][a-z'-]{1,29}){0,2}\s+(?:has|was diagnosed with|is living with|suffers from|is being treated for)\s+(?:(?:a|an)\s+)?(?:{conditions})\b",
        r"\b(?:patient|person|individual)\s+(?:named\s+)?[a-z][a-z'-]{1,29}(?:\s+[a-z][a-z'-]{1,29}){0,2}\s+with\s+(?:{conditions})\b",
    ),
    "drugs_supplements": (
        "aspirin", "ibuprofen", "paracetamol", "amoxicillin", "prednisone", "prednisolone", "metformin", "insulin",
        "ivermectin", "rapamycin", "melatonin", "magnesium", "vitamin d", "turmeric", "cbd", "resveratrol",
    ),
    "conditions": (
        "cancer", "diabetes", "arthritis", "anxiety", "depression", "dementia", "infection", "pain", "obesity", "insomnia",
        "kidney disease", "heart disease", "ageing", "aging", "longevity",
    ),
    "claims": ("cure", "cures", "cured", "guaranteed", "proven to", "miracle", "reverses"),
    "dose_units": ("mg", "mcg", "µg", "ml", "iu", "g/kg", "mg/kg"),
    "dose_template": r"(?<![\w.])(?:\d+(?:\.\d+)?|\.\d+)\s*(?:{units})\b",
    "instruction_verbs": ("take", "stop", "start", "give"),
    "instruction_templates": (r"\byou should\s+(?:{verbs})\b", r"\binstead of\s+(?:a|your)\s+(?:doctor|vet)\b"),
    "drug_condition_template": r"\b(?:{drugs})\s+for\s+(?:{conditions})\b",
    "hold_codes": ("sensitive_claim", "sensitive_dose", "sensitive_instruction", "sensitive_drug_condition"),
}


class GoalSafetyTests(unittest.TestCase):
    def refused(self, value, tier="standard"):
        with self.assertRaises(AgentError) as caught:
            screen_content(value, tier)
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.detail, GOAL_SAFETY_REFUSAL)

    def held(self, value, code):
        result = screen_content(value, "sensitive")
        self.assertTrue(result["held"])
        self.assertIn(code, result["reasons"])
        self.assertEqual(result["message"], "Held for review because it reads like a treatment instruction. Rewrite it as what a cited study reported.")

    def passed(self, value, tier="standard"):
        self.assertEqual(screen_content(value, tier), {"held": False, "reasons": [], "message": None})

    def test_manifest_version_and_hash_pin_every_closed_rule_set(self):
        self.assertEqual(RULESET_VERSION, "goal-safety-v1")
        self.assertEqual(RULE_MANIFEST, EXPECTED_MANIFEST)
        independent_hash = sha256(json.dumps(EXPECTED_MANIFEST, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()
        self.assertEqual(RULESET_SHA256, independent_hash)
        self.assertRegex(RULESET_SHA256, r"^[0-9a-f]{64}$")

    def test_all_original_prohibited_project_classes_stay_refused_in_both_tiers(self):
        examples = ("Fictional weapons research.", "Fictional pathogen work.", "Fictional toxins.", "Gain-of-function study.",
                    "Fictional explosives.", "Surveillance of people.", "Identify people.", "Identifying people.",
                    "Fictional cyber attack.", "Attacks on computer systems.", "Election persuasion.")
        for text in examples:
            for tier in ("standard", "sensitive"):
                with self.subTest(text=text, tier=tier):
                    self.refused(text, tier)
                    with self.assertRaises(AgentError) as caught:
                        check_proposal(text)
                    self.assertEqual(caught.exception.status, 422)
                    self.assertEqual(caught.exception.detail, "This goal falls outside the permitted research and building categories.")

    def test_contact_forms_refused_outside_sources_artifact(self):
        for text in ("fictional.person@example.test", "https://fictional.example.test", "www.fictional.example.test",
                     "fictional.net", "+44 0000 123456", "Use WhatsApp", "DM me", "Join Discord"):
            for tier in ("standard", "sensitive"):
                with self.subTest(text=text, tier=tier):
                    self.refused({"detail": text}, tier)
        self.refused({"source": {"url": "https://fictional.example.test"}})
        self.refused({"Sources": [{"url": "https://fictional.example.test"}]})

    def test_source_contact_exception_is_narrow_and_propagates_only_inside_fields(self):
        value = {"sources": [{"url": "https://fictional.example.test", "quote": "fictional.person@example.test"}],
                 "artifact": {"url": "https://fictional.example.test/code", "sha256": "a" * 64}}
        self.passed(value, "sensitive")
        check_proposal("https://fictional.example.test and fictional.person@example.test")
        self.refused({**value, "detail": "fictional.person@example.test"})
        shared = {"url": "https://fictional.example.test"}
        self.refused({"sources": [shared], "detail": shared})
        # Only contacts are exempt. Other refusal and hold rules apply there.
        self.refused({"sources": [{"quote": "Please donate."}]})
        self.refused({"artifact": {"url": "https://fictional.example.test/paypal"}})
        self.refused({"sources": [{"quote": "My diagnosis is fictional."}]})
        self.held({"sources": [{"quote": "The published dose was 5 mg/kg."}]}, "sensitive_dose")
        self.refused({"sources": [{"quote": "Fictional weapons."}]})

    def test_payment_donation_words_and_patterns_are_refused(self):
        texts = ("Please donate to the fictional project.", "PayPal", "GoFundMe", "Patreon", "Make a donation.",
                 "Collect donations.", "Send money.", "Transfer crypto.", "Pay us money.", "Give me funds.",
                 "ZZ00 FICT 0000 0000 0000 00", "ZZ00FICT00000000000000",
                 "1" + "A" * 25, "3" + "B" * 33, "bc1" + "q" * 39, "0x" + "a" * 40)
        for text in texts:
            for tier in ("standard", "sensitive"):
                with self.subTest(text=text, tier=tier):
                    self.refused({"sources": [{"quote": text}]}, tier)
        for text in ("Design the fictional charity platform interface.", "Payment algebra is out of scope.",
                     "Pay attention to the arithmetic.", "A donateable widget is a fictional identifier."):
            self.passed(text, "sensitive")

    def test_trial_recruiting_refused_but_reported_study_enrolment_passes(self):
        for text in ("Looking for patients.", "Recruiting patients.", "Seeking trial participants.",
                     "Volunteers to try a fictional treatment.", "Volunteers to receive a fictional treatment.",
                     "Recruit participants for a clinical trial.", "Seeking people for our trial.",
                     "Join our medical trial.", "Sign up for a treatment trial.", "Participate in a clinical trial."):
            self.refused(text)
        for text in ("The study enrolled 40 dogs.", "The fictional trial recruited 40 participants.",
                     "The paper describes volunteers in a published trial.", "Looking for collaborators to review arithmetic."):
            self.passed(text, "sensitive")

    def test_personal_health_templates_refuse_fictional_disclosures(self):
        texts = ("My diagnosis is fictional.", "My medical records say this.", "My bloodwork is private.",
                 "My dog's bloodwork is fictional.", "My dog’s blood work is fictional.", "My dogs' bloodwork is fictional.", "My dogs bloodwork is fictional.",
                 "My cat's diagnosis is fictional.", "Fictional Mira has diabetes.", "Mira was diagnosed with cancer.",
                 "Fictional Dara is living with arthritis.", "Mira suffers from anxiety.",
                 "Fictional Mira is being treated for kidney disease.", "A person named Mira with cancer.",
                 "Patient Mira with diabetes.")
        for text in texts:
            for tier in ("standard", "sensitive"):
                with self.subTest(text=text, tier=tier):
                    self.refused(text, tier)
        for text in ("The published study examined diabetes in an unnamed cohort.", "The study enrolled 40 dogs.",
                     "Reported group bloodwork appears in the published table.", "A diagnostic algorithm for fictional clock states."):
            self.passed(text, "sensitive")

    def test_every_claim_word_is_a_sensitive_hold_with_word_boundaries(self):
        for word in ("cure", "cures", "cured", "guaranteed", "proven to", "miracle", "reverses"):
            self.held("The fictional claim says " + word + ".", "sensitive_claim")
            self.passed("The fictional claim says " + word + ".", "standard")
        for text in ("The derivation is secure.", "Curvature is a fictional geometry term.", "A reversible mapping."):
            self.passed(text, "sensitive")

    def test_dose_units_integer_decimal_micro_sign_and_published_quotes_are_held(self):
        for unit in ("mg", "mcg", "µg", "μg", "ml", "iu", "g/kg", "mg/kg"):
            for amount in ("5", "5.25", ".5"):
                with self.subTest(unit=unit, amount=amount):
                    self.held("The fictional published study reports " + amount + " " + unit + ".", "sensitive_dose")
        self.held("A fictional dose was 5mg/kg.", "sensitive_dose")
        self.passed("The fictional sample weighed 5 g and the study dog weighed 12 kg.", "sensitive")
        self.passed("The group contained 40 dogs.", "sensitive")
        self.passed("The fictional comparison used 5 mg/kg.", "standard")

    def test_direct_instructions_and_doctor_replacement_hold_only_sensitive(self):
        texts = ["You should " + verb + " a fictional example." for verb in ("take", "stop", "start", "give")]
        texts += ["Use this instead of " + article + " " + professional + "."
                  for article in ("a", "your") for professional in ("doctor", "vet")]
        for text in texts:
            self.held(text, "sensitive_instruction")
            self.passed(text, "standard")
        self.passed("You could review the paper with a doctor or vet.", "sensitive")

    def test_closed_drug_and_condition_lists_are_only_lexical_hold_triggers(self):
        drugs = ("aspirin", "ibuprofen", "paracetamol", "amoxicillin", "prednisone", "prednisolone", "metformin", "insulin",
                 "ivermectin", "rapamycin", "melatonin", "magnesium", "vitamin d", "turmeric", "cbd", "resveratrol")
        conditions = ("cancer", "diabetes", "arthritis", "anxiety", "depression", "dementia", "infection", "pain", "obesity", "insomnia",
                      "kidney disease", "heart disease", "ageing", "aging", "longevity")
        for drug in drugs:
            self.held("The fictional paper discusses " + drug + " for pain.", "sensitive_drug_condition")
        for condition in conditions:
            self.held("The fictional paper discusses magnesium for " + condition + ".", "sensitive_drug_condition")
        self.passed("The fictional paper discusses magnesium for pain.", "standard")
        self.passed("The paper discusses magnesium as an element.", "sensitive")
        self.passed("The fictional text compares an unknown compound for an unknown condition.", "sensitive")
        self.passed("Aspirinase for painless arithmetic is a fictional name.", "sensitive")

    def test_hold_reasons_are_fixed_deduplicated_ordered_and_refusals_override(self):
        value = ["Magnesium for pain.", "You should take this.", "5 mg/kg.", "Miracle cure.", "Guaranteed cure."]
        self.assertEqual(screen_content(value, "sensitive"), {"held": True,
            "reasons": ["sensitive_claim", "sensitive_dose", "sensitive_instruction", "sensitive_drug_condition"], "message": GOAL_SAFETY_HOLD})
        self.refused([*value, "Please donate."], "sensitive")
        self.refused(["Please donate.", *value], "sensitive")

    def test_normalization_detects_compatibility_text_without_mutating_approved_input(self):
        value = {"detail": "Ｆｉｃｔｉｏｎａｌ ＭＩＲＡＣＬＥ", "sources": [{"quote": "5 μg"}], "revision": 2, "approved": True}
        saved = deepcopy(value)
        result = screen_content(value, "sensitive")
        self.assertEqual(result["reasons"], ["sensitive_claim", "sensitive_dose"])
        self.assertEqual(value, saved)
        self.refused("ＰａｙＰａｌ")
        self.refused("ｆｉｃｔｉｏｎａｌ＠ｅｘａｍｐｌｅ．ｔｅｓｔ")

    def test_control_and_invisible_characters_use_existing_validation(self):
        for value in ("bad\u200b text", "bad\x00 text", "bad\t text", "bad\ud800 text"):
            with self.assertRaises(AgentError) as caught:
                screen_content(value)
            self.assertEqual(caught.exception.status, 422)
        self.passed("A fictional line.\nA second fictional line.")

    def test_structured_walk_is_deep_bounded_by_input_and_rejects_cycles_non_json(self):
        value = "A fictional miracle claim."
        for _ in range(1100):
            value = [value]
        self.held(value, "sensitive_claim")
        cyclic = []
        cyclic.append(cyclic)
        for value in (cyclic, {1: "invalid key"}, {"text": {"not", "json"}}, b"binary", 1.5):
            self.refused(value)
        self.passed({"revision": 1, "permission": False, "optional": None, "texts": []})
        with self.assertRaises(AgentError) as caught:
            screen_content("Fictional text.", "unknown")
        self.assertEqual(caught.exception.status, 422)

    def test_screen_does_not_open_sources_or_artifacts_or_network_connections(self):
        value = {"sources": [{"url": "https://fictional.example.test/study", "quote": "The study enrolled 40 dogs."}],
                 "artifact": {"url": "https://fictional.example.test/code", "sha256": "f" * 64}}
        with patch("urllib.request.urlopen", side_effect=AssertionError("No URL fetching")), \
                patch("socket.socket", side_effect=AssertionError("No network")):
            self.passed(value, "sensitive")
