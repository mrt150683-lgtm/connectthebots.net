"""Independent structural/provenance oracles for the authored fictional corpus."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from importlib import resources
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentmatch import fixtures


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = resources.files("agentmatch").joinpath("data", "synthetic")
        cls.raw = json.loads(cls.directory.joinpath("corpus.json").read_text(encoding="utf-8"))
        cls.manifest = json.loads(cls.directory.joinpath("manifest.json").read_text(encoding="utf-8"))

    def test_T_EVAL_001_exact_fictional_counts(self) -> None:
        report = fixtures.verify()
        self.assertEqual(report["owner_count"], 40)
        self.assertEqual(report["conversation_count"], 200)
        self.assertEqual(report["source_message_count"], 800)
        self.assertFalse(report["real_owner_data_used"])
        self.assertFalse(report["reference_corpus_used"])
        self.assertFalse(report["numeric_oracles_executed_by_this_command"])
        counts: dict[str, int] = {}
        for conversation in self.raw["conversations"]:
            counts[conversation["owner_id"]] = counts.get(conversation["owner_id"], 0) + 1
        self.assertEqual(set(counts.values()), {5})

    def test_T_EVAL_001_duplicate_owner_rejected(self) -> None:
        raw = deepcopy(self.raw)
        raw["owners"][1]["owner_id"] = raw["owners"][0]["owner_id"]
        with self.assertRaisesRegex(fixtures.FixtureError, "DUPLICATE_OWNER_ID"):
            fixtures._validate_corpus(raw, self.manifest)

    def test_T_EVAL_001_missing_source_rejected(self) -> None:
        raw = deepcopy(self.raw)
        raw["owners"][0]["claims"][0]["source_refs"] = ["absent-conversation:owner-1"]
        with self.assertRaisesRegex(fixtures.FixtureError, "UNSAFE_OR_MISSING_SOURCE_REFERENCE"):
            fixtures._validate_corpus(raw, self.manifest)
        raw["owners"][0]["claims"][0]["source_refs"] = []
        with self.assertRaisesRegex(fixtures.FixtureError, "MISSING_SOURCE_REFERENCE"):
            fixtures._validate_corpus(raw, self.manifest)

    def test_invalid_source_unicode_is_rejected(self) -> None:
        raw = deepcopy(self.raw)
        raw["conversations"][0]["messages"][1]["text"] = "unpaired\ud800"
        with self.assertRaisesRegex(fixtures.FixtureError, "INVALID_SOURCE_UNICODE"):
            fixtures._validate_corpus(raw, self.manifest)

    def test_T_MATCH_009_agent_praise_cannot_be_owner_evidence(self) -> None:
        raw = deepcopy(self.raw)
        raw["owners"][0]["claims"][0]["source_refs"] = ["lumen-conv-1:agent-1"]
        with self.assertRaisesRegex(fixtures.FixtureError, "NON_OWNER_EVIDENCE_REFERENCE"):
            fixtures._validate_corpus(raw, self.manifest)

    def test_T_MATCH_009_quoted_statement_is_not_direct_owner_evidence(self) -> None:
        raw = deepcopy(self.raw)
        raw["conversations"][0]["messages"][1]["quoted"] = True
        with self.assertRaisesRegex(fixtures.FixtureError, "NON_OWNER_EVIDENCE_REFERENCE"):
            fixtures._validate_corpus(raw, self.manifest)

    def test_T_EVAL_001_cross_owner_source_rejected(self) -> None:
        raw = deepcopy(self.raw)
        raw["owners"][0]["claims"][0]["source_refs"] = ["harbor-conv-1:owner-1"]
        with self.assertRaisesRegex(fixtures.FixtureError, "UNSAFE_OR_MISSING_SOURCE_REFERENCE"):
            fixtures._validate_corpus(raw, self.manifest)

    def test_T_EVAL_001_unknown_schema_rejected(self) -> None:
        raw = deepcopy(self.raw)
        raw["schema_version"] = "9000"
        with self.assertRaisesRegex(fixtures.FixtureError, "UNSUPPORTED_CORPUS_PROVENANCE_OR_VERSION"):
            fixtures._validate_corpus(raw, self.manifest)

    def test_T_EVAL_001_manifest_cannot_address_arbitrary_path(self) -> None:
        manifest = deepcopy(self.manifest)
        manifest["files"]["../../private.json"] = manifest["files"].pop("corpus.json")
        with self.assertRaisesRegex(fixtures.FixtureError, "UNSAFE_FIXTURE_FILE_REFERENCE"):
            fixtures._validate_manifest(manifest)

    def test_T_EVAL_001_tamper_rejected_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "data" / "synthetic"
            target.mkdir(parents=True)
            for file in ("manifest.json", "corpus.json", "expected_outcomes.json"):
                (target / file).write_bytes(self.directory.joinpath(file).read_bytes())
            with (target / "corpus.json").open("ab") as handle:
                handle.write(b" ")
            with patch("agentmatch.fixtures.resources.files", return_value=root):
                with self.assertRaisesRegex(fixtures.FixtureError, "FIXTURE_INTEGRITY_MISMATCH"):
                    fixtures.load_corpus()

    def test_T_EVAL_001_duplicate_and_nonfinite_json_rejected(self) -> None:
        with self.assertRaisesRegex(fixtures.FixtureError, "DUPLICATE_JSON_KEY"):
            fixtures._decode(b'{"x":1,"x":2}')
        with self.assertRaisesRegex(fixtures.FixtureError, "NONFINITE_FIXTURE_NUMBER"):
            fixtures._decode(b'{"x":NaN}')
        with self.assertRaisesRegex(fixtures.FixtureError, "FIXTURE_FILE_TOO_LARGE"):
            fixtures._decode(b" " * 2_000_001)

    def test_T_EVAL_003_resolve_only_shipped_owner(self) -> None:
        corpus = fixtures.load_corpus()
        self.assertEqual(corpus.owner("lumen").intent.intent_id, "lumen-collaboration")
        self.assertEqual(len(fixtures.list_owners()), 40)
        with self.assertRaisesRegex(fixtures.FixtureError, "UNKNOWN_SYNTHETIC_OWNER"):
            corpus.owner("../../private")

    def test_T_EVAL_007_oracles_are_separate_and_hand_authored(self) -> None:
        corpus = fixtures.load_corpus()
        cases = {case["test_id"]: case for case in corpus.expected_outcomes}
        self.assertGreaterEqual(len(cases), 20)
        worked = cases["T-MATCH-001"]
        self.assertEqual(worked["expected"]["F"], "17/20")
        self.assertEqual(worked["expected"]["S"], "41/50")
        self.assertEqual(worked["expected"]["reverse_S"], "77/100")
        self.assertEqual(cases["T-MATCH-020"]["expected"]["C"], "3/4")
        self.assertEqual(cases["T-MATCH-020"]["expected"]["communication_unknown_C"], "7/10")
        self.assertTrue(all(case["authorship"] == "hand_authored_from_contract_not_scorer_output"
                            for case in cases.values()))

    def test_all_hand_authored_matching_oracles_execute(self) -> None:
        results = fixtures.run_oracles(datetime(2026, 10, 25, 12, tzinfo=timezone.utc))
        self.assertEqual(len(results), 22)
        for result in results:
            with self.subTest(case=result["case_id"]):
                self.assertTrue(result["passed"], result["details"])


if __name__ == "__main__":
    unittest.main()
