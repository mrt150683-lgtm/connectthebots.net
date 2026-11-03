"""Portable verifier checks, without network or optional server dependencies."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "tools" / "verify_goal_record.py"
SPEC = importlib.util.spec_from_file_location("goal_verify_tool", TOOL)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def fixture():
    # Independent JSON/hash encoding, not a second call to the writer.
    digest = lambda value: sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()
    entries, previous = [], "0" * 64
    for seq in range(1, 4):
        content = {"fictional arithmetic": seq}
        entry = {"goal": "fictional-verifier", "seq": seq, "kind": "note", "author": "c_1234abcd",
                 "created_at": "2026-11-01T12:00:00.000000Z", "refs": [],
                 "content_sha256": digest(content), "previous": previous}
        previous = digest(entry)
        entries.append({**entry, "entry_sha256": previous, "id": "e_" + previous,
                        "status": "visible", "content": content})
    return entries


def encoded(entries):
    return ("".join(json.dumps(entry) + "\n" for entry in entries)).encode("utf-8")


class Response(io.BytesIO):
    def __init__(self, url, entries, **headers):
        super().__init__(encoded(entries))
        self.url, self.headers = url, headers

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, pages):
        self.pages, self.urls = iter(pages), []

    def open(self, request, **options):
        self.urls.append(request.full_url)
        entries, headers = next(self.pages)
        return Response(request.full_url, entries, **headers)


class GoalVerifierTests(unittest.TestCase):
    def test_file_cli_runs_without_site_packages_and_reports_first_broken_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fictional.jsonl"
            for change, expected, text in ((None, 0, "Verified 3 entries"), ("content", 1, "BROKEN at entry 2")):
                entries = fixture()
                if change:
                    entries[1][change] = {"fictional tampering": True}
                path.write_bytes(encoded(entries))
                result = subprocess.run([sys.executable, "-S", str(TOOL), str(path)], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
                self.assertIn(text, result.stdout)
                if not change:
                    self.assertIn("Head: goal=fictional-verifier seq=3 entry_sha256=" + entries[-1]["entry_sha256"], result.stdout)

    def test_erased_content_verifies_without_claiming_its_cleartext_was_checked(self):
        entries = fixture()
        entries[1]["content"], entries[1]["status"] = None, "withdrawn"
        self.assertTrue(verifier.verify_record(verifier.decode_page(encoded(entries)))["valid"])
        with patch.object(verifier, "read_record", return_value=entries), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(verifier.main(["fictional.jsonl"]), 0)
        self.assertIn("Missing content was not rechecked", output.getvalue())

    def test_https_pagination_checks_entire_record_and_never_follows_link_target(self):
        entries = fixture()
        opener = Opener([(entries[:2], {"X-Record-Next-After": "2", "Link": '<https://record.example.test/goals/fictional/record.jsonl?after=2&limit=2>; rel="next"'}),
                         (entries[2:], {"X-Record-Next-After": "3"})])
        actual = verifier.read_url("https://record.example.test/goals/fictional/record.jsonl?limit=2", opener=opener)
        self.assertEqual(actual, entries)
        self.assertTrue(verifier.verify_record(actual)["valid"])
        self.assertEqual([url.rsplit("?", 1)[1] for url in opener.urls], ["after=0&limit=2", "after=2&limit=2"])

    def test_page_gap_or_duplicate_reports_the_first_broken_sequence(self):
        entries = fixture()
        for second in (entries[2:], entries[:1]):
            opener = Opener([(entries[:1], {}), (second, {})])
            actual = verifier.read_url("https://record.example.test/record.jsonl?limit=1", opener=opener)
            report = verifier.verify_record(actual)
            self.assertFalse(report["valid"])
            self.assertEqual(report["first_broken"], 2)
            self.assertEqual(len(opener.urls), 2)

    def test_unsafe_url_and_untrusted_pagination_are_refused(self):
        for url in ("http://record.example.test/record", "https://user:secret@record.example.test/record",
                    "https://record.example.test/record#fragment", "https://record.example.test/record?after=2",
                    "https://record.example.test/record?limit=101", "https://record.example.test/record?after=0&after=0",
                    "https://record.example.test/record?next=https://other.example.test"):
            with self.subTest(url=url), self.assertRaises(verifier.RecordInputError):
                verifier.read_url(url, opener=Opener([]))
        for headers in ({"Link": '<https://other.example.test/record?after=1&limit=1>; rel="next"'},
                        {"Link": '<https://record.example.test/record?after=0&limit=1>; rel="next"'},
                        {"X-Record-Next-After": "0"}):
            with self.subTest(headers=headers), self.assertRaises(verifier.RecordInputError):
                verifier.read_url("https://record.example.test/record?limit=1", opener=Opener([(fixture()[:1], headers)]))

    def test_page_bounds_and_malformed_json_fail_closed(self):
        for data in (b'{"seq":1,"seq":2}\n', b'[]\n', b'{"value":NaN}\n', b'{broken}\n', b'{"huge":' + b'1' * 5000 + b'}\n'):
            with self.subTest(data=data), self.assertRaises(verifier.RecordInputError):
                verifier.decode_page(data)
        with patch.object(verifier, "MAX_PAGES", 1), self.assertRaisesRegex(verifier.RecordInputError, "page limit"):
            verifier.read_url("https://record.example.test/record?limit=1", opener=Opener([(fixture()[:1], {})]))
        with patch.object(verifier, "MAX_PAGE_BYTES", 8), self.assertRaisesRegex(verifier.RecordInputError, "byte limit"):
            verifier.read_url("https://record.example.test/record", opener=Opener([(fixture(), {})]))


if __name__ == "__main__":
    unittest.main()
