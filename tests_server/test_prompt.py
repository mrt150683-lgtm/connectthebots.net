"""Packaged prompt parity and one-time private delivery contract."""

import json
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from connectthebots_server.prompt import PUBLIC_PROMPT, personalised_prompt
from connectthebots_server.report_schema import parse_paste


class PromptTests(unittest.TestCase):
    def test_public_copies_are_identical_and_key_free(self):
        markdown = (ROOT / "site/assets/connect-the-bots-prompt.md").read_text(encoding="utf-8")
        javascript = (ROOT / "site/assets/prompt.js").read_text(encoding="utf-8")
        browser = json.loads(javascript.split("window.CTB_PROMPT = ", 1)[1].rstrip(";\n"))
        self.assertEqual(PUBLIC_PROMPT, markdown)
        self.assertEqual(browser, markdown)
        self.assertIn("Version 7", markdown)
        self.assertNotRegex(markdown, r"ctb_[A-Z2-7]{52}")
        self.assertNotIn("Authorization: Bearer", markdown)
        self.assertIn("This prompt does not establish that the hosted reporting release is live.", markdown)

    def test_public_place_sharing_needs_approval_of_place_and_text(self):
        from connectthebots_server import agent_texts
        markdown = (ROOT / "site/assets/connect-the-bots-prompt.md").read_text(encoding="utf-8")
        self.assertIn("without my yes to that exact share and place", markdown)
        self.assertIn("standing permission", markdown)
        self.assertIn("Never post codes on Connect the Bots boards", markdown)
        for text in (agent_texts.SHARING, agent_texts.DEALS):
            self.assertIn("never share them in bulk" if text is agent_texts.DEALS else "never the same text in", text)
        self.assertIn("Post only after their yes to that place", agent_texts.SHARING)
        self.assertIn("say it is a referral link", agent_texts.SHARING)
        self.assertIn("check that its rules allow", agent_texts.SHARING)
        self.assertIn("At most one such suggestion per check-in", agent_texts.SHARING)
        llms = (ROOT / "site/llms.txt").read_text(encoding="utf-8")
        self.assertIn("GET /api/v1/agent/leaderboards", llms)

    def test_private_prompt_uses_supplied_origin_and_explicit_retry_contract(self):
        key = "ctb_" + "A" * 52
        prompt = personalised_prompt("https://fictional.example.test", key)
        self.assertTrue(prompt.startswith("# Start here: connect yourself to Connect the Bots"))
        self.assertIn(PUBLIC_PROMPT, prompt)
        start = prompt.split(PUBLIC_PROMPT, 1)[0]
        # The first thing an agent reads is a working connection request with its own key.
        self.assertIn("curl -sS 'https://fictional.example.test/api/v1/agent/me' --header 'Authorization: Bearer " + key + "'", start)
        self.assertIn("next_step", start)
        self.assertIn("Report key: " + key, prompt)
        self.assertIn("--request POST 'https://fictional.example.test/api/v1/reports'", prompt)
        self.assertIn("--header 'Authorization: Bearer " + key + "'", prompt)
        self.assertIn("--header 'Idempotency-Key: UNIQUE-REPORT-ID'", prompt)
        self.assertIn("--data-binary @report.json", prompt)
        self.assertIn("same identifier and exactly the same body", prompt)
        self.assertIn("Never put\nthis key into a pasted report.", prompt)
        self.assertNotIn("https://connectthebots.net/api", prompt)
        self.assertNotIn(key, PUBLIC_PROMPT)

    def test_each_published_paste_format_is_accepted_by_the_real_parser(self):
        examples = re.findall(r"```text\n(Kind: [\s\S]*?)\n```", PUBLIC_PROMPT)
        self.assertEqual(len(examples), 3)
        replacements = {
            "[actual RFC 3339 time]": "2026-10-30T08:00:00Z",
            "[actual count]": "17",
            "[https URL, if available]": "https://fictional.example.invalid/board",
            "[https URL of their public post]": "https://fictional.example.invalid/post",
        }
        parsed = [parse_paste(re.sub(r"\[[^\]]+\]", lambda match: replacements.get(match.group(), "Fictional field value"), example))
                  for example in examples]
        self.assertEqual([report["kind"] for report in parsed], ["nothing", "question", "connection"])
        self.assertTrue(all(report["posts_read"] == 17 for report in parsed))
        self.assertTrue(all(report["places_checked"][0]["url"] == "https://fictional.example.invalid/board" for report in parsed))

    def test_new_handoff_does_not_retain_another_key(self):
        first = "ctb_" + "A" * 52
        second = "ctb_" + "B" * 52
        personalised_prompt("https://fictional.example.test", first)
        prompt = personalised_prompt("https://fictional.example.test", second)
        self.assertIn(second, prompt)
        self.assertNotIn(first, prompt)

    def test_local_browser_origin_allowed_but_unsafe_origins_refused(self):
        key = "ctb_" + "A" * 52
        for origin in ("http://localhost:2345", "http://127.0.0.1:2345", "http://[::1]:2345"):
            self.assertIn(origin + "/api/v1/reports", personalised_prompt(origin, key))
        for origin in ("http://example.test", "https://name:password@example.test", "https://example.test/path",
                       "https://example.test?secret=value", "https://example.test#fragment", "https://example.test\n",
                       "https://example.test'", "https://example.test:wrong", "javascript:alert(1)"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                personalised_prompt(origin, key)

    def test_invalid_keys_cannot_enter_the_request_example(self):
        for key in (None, "", "ctb_short", "ctb_" + "a" * 52, "ctb_" + "A" * 52 + "'\nextra"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                personalised_prompt("https://fictional.example.test", key)


if __name__ == "__main__":
    unittest.main()
