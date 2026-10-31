from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from fractions import Fraction
import io
import json
from pathlib import Path
import tempfile
import unittest

from agentmatch.cli import main
from agentmatch.demo import build_demo, interview_journey, introduction_journey, render_html, write_demo

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def rational(record):
    return Fraction(int(record["numerator"]), int(record["denominator"]))


class IntegrationTests(unittest.TestCase):
    def command(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(args)
            except SystemExit as error:
                code = error.code
        return code, out.getvalue(), err.getvalue()

    def test_T_INTERVIEW_006_review_changes_one_coherent_matching_snapshot(self):
        report, _, _ = interview_journey(NOW)
        before = report["before_review"]
        self.assertEqual(rational(before["coverage"]), Fraction(9, 10))
        self.assertEqual(rational(before["preference_fit"]), Fraction(3, 4))
        self.assertEqual(before, report["after_summary_only"])
        after = report["after_exact_review"]
        self.assertEqual(rational(after["coverage"]), 1)
        self.assertEqual(rational(after["preference_fit"]), Fraction(17, 20))
        self.assertEqual(rational(after["supported_fit"]), Fraction(41, 50))
        self.assertEqual(report["reviewed_revision"]["disclosures_granted"], 0)

    def test_T_EVAL_002_demo_is_reproducible_and_preserves_real_human_authorship(self):
        first = build_demo(as_of=NOW)
        second = build_demo(as_of=NOW)
        self.assertEqual(first, second)
        self.assertEqual(first["evaluation"]["scanned_candidates"], 39)
        self.assertLessEqual(len(first["evaluation"]["offered_owner_ids"]), 3)
        journey = first["introduction_journey"]
        self.assertEqual(journey["opened_channel_before_first_human_message"]["human_messages"], [])
        self.assertEqual(len(journey["channel_after_retry"]["human_messages"]), 1)
        self.assertTrue(journey["retry_receipt"]["duplicate"])
        self.assertFalse(journey["retry_receipt"]["read_confirmation"])
        self.assertEqual(journey["private_draft"]["visibility"], "owner_only")
        self.assertFalse(journey["private_draft"]["may_send"])
        self.assertNotIn("source_refs", json.dumps(journey["card"]))

    def test_T_EVAL_003_cli_validates_ids_selectivity_and_clock(self):
        args = ["evaluate", "--owner", "lumen", "--intent", "lumen-collaboration", "--json"]
        code, output, err = self.command(args)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(output)["scanned_candidates"], 39)
        for extra in (("--fit-threshold", "nan"), ("--fit-threshold", "1.1"),
                      ("--fit-threshold", "0.4", "--selectivity", "open"),
                      ("--as-of", "2026-10-25T12:00:00")):
            self.assertEqual(self.command(args + list(extra))[0], 2)
        self.assertEqual(self.command(["evaluate", "--owner", "unknown", "--intent", "unknown"])[0], 2)
        self.assertEqual(self.command(["evaluate", "--owner", "lumen", "--intent", "wrong"])[0], 2)

    def test_T_EVAL_006_unsupported_input_is_not_opened(self):
        for command in ("import", "interview", "contact", "dating", "connect", "serve"):
            code, output, error = self.command([command, "/no/such/private-archive.json"])
            self.assertEqual(code, 2)
            self.assertIn("UNSUPPORTED_R0_CAPABILITY", error)
            self.assertNotIn("No such file", error)

    def test_report_has_no_external_assets_and_escapes_scenario_text(self):
        report = build_demo(as_of=NOW)
        report["social_scenarios"].append({"scenario_id": "<script>alert(1)</script>", "text": "</pre><img src=x>"})
        html = render_html(report)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<img", html.lower())
        self.assertIn("default-src 'none'", html)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            write_demo(report, path)
            self.assertTrue((path / "demo.html").is_file())
            self.assertTrue((path / "decisions.jsonl").is_file())
            self.assertEqual(json.loads((path / "run_manifest.json").read_text())["release"], "R0")

    def test_all_cli_scenarios_and_fixture_commands_succeed(self):
        code, text, _ = self.command(["simulate", "--list"])
        self.assertEqual(code, 0)
        for name in json.loads(text)["scenarios"]:
            with self.subTest(scenario=name):
                code, output, error = self.command(["simulate", "--scenario", name])
                self.assertEqual(code, 0, error)
                json.loads(output)
        for args in (["fixtures", "list"], ["fixtures", "verify"], ["status"]):
            self.assertEqual(self.command(args)[0], 0)

    def test_zero_grounded_topics_use_labelled_owner_invitation(self):
        from agentmatch.fixtures import load_corpus
        from agentmatch.models import load_owner
        corpus = load_corpus()
        people = []
        for owner_id in ("lumen", "harbor"):
            record = corpus.owner(owner_id).to_dict()
            record["intent"]["threshold"] = "0"
            for claim in record["claims"]:
                if claim["dimension"] == "intent.problem_tags":
                    claim["value"] = []
            people.append(load_owner(record))
        result = introduction_journey(*people, NOW)
        self.assertEqual(result["card"]["context"]["topic"]["kind"], "owner_proposed")
        self.assertEqual(result["card"]["supported_thread_count"], 0)


if __name__ == "__main__":
    unittest.main()
