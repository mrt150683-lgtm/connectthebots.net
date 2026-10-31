"""Independent examples and rejection cases for strict reports and paste v2."""

from copy import deepcopy
import json
import unittest

from connectthebots_server.report_schema import ReportError, parse_json_report, parse_paste, validate_report


def sample(kind="nothing"):
    payload = {"kind": kind, "checked_at": "2026-10-30T12:00:00Z", "places_checked": [{"name": "Fictional makers board", "url": "https://board.example.test"}], "posts_read": 4}
    if kind == "question":
        payload["question"] = {"text": "Would a short prototype fit your plans?", "why": "The next step depends on your availability."}
    elif kind == "connection":
        payload["connection"] = {
            "who": "Joon Fictional", "where": "https://board.example.test/joon", "why_now": "A small prototype is being planned.",
            "they_bring": "Workshop access.", "they_could_gain": "A useful working example.", "i_bring": "Interface design.",
            "i_could_gain": "A test of the interaction.", "evidence": [{"quote_or_link": "Seeking a prototype collaborator", "matches": "A small, concrete project."}],
            "still_unknown": "Whether the timing fits.", "first_step": "Ask about the prototype scope.", "board_slug": "joon-fictional",
        }
    return payload


PASTE_COMMON = "Kind: {kind}\nChecked at: 2026-10-30T12:00:00Z\nPlaces checked: Fictional makers board | https://board.example.test\nPosts read: 4\n"
PASTE_CONNECTION = PASTE_COMMON.format(kind="connection") + """Connection: Joon Fictional | https://board.example.test/joon
Why now: A small prototype is being planned.
They bring: Workshop access.
They could gain: A useful working example.
I bring: Interface design.
I could gain: A test of the interaction.
Evidence: Seeking a prototype collaborator | A small, concrete project.
Still unknown: Whether the timing fits.
Small first step: Ask about the prototype scope.
Board slug: joon-fictional
"""


class ReportSchemaTests(unittest.TestCase):
    def rejected(self, payload, status=422):
        with self.assertRaises(ReportError) as caught:
            validate_report(payload)
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_each_kind_parses_and_returns_detached_values(self):
        for kind in ("nothing", "question", "connection"):
            original = sample(kind)
            parsed = parse_json_report(json.dumps(original).encode())
            self.assertEqual(parsed["kind"], kind)
            self.assertEqual(parsed["checked_at"], "2026-10-30T12:00:00.000000Z")
            self.assertEqual(parsed, validate_report(original))
            original["places_checked"][0]["name"] = "Changed outside validator"
            self.assertEqual(parsed["places_checked"][0]["name"], "Fictional makers board")

    def test_unknown_fields_are_rejected_at_every_object_level(self):
        for path in ((), ("places_checked", 0), ("connection",), ("connection", "evidence", 0)):
            payload = sample("connection")
            target = payload
            for part in path:
                target = target[part]
            target["unexpected"] = "untrusted"
            with self.subTest(path=path):
                self.rejected(payload)
        payload = sample("question")
        payload["question"]["owner_id"] = "another-person"
        self.rejected(payload)

    def test_missing_required_fields_and_wrong_kind_details_are_rejected(self):
        for kind in ("nothing", "question", "connection"):
            original = sample(kind)
            for name in list(original):
                payload = deepcopy(original)
                del payload[name]
                with self.subTest(kind=kind, missing=name):
                    self.rejected(payload)
        for kind, extra in (("nothing", "question"), ("question", "connection"), ("connection", "question")):
            payload = sample(kind)
            payload[extra] = sample(extra)[extra]
            self.rejected(payload)
        for field in sample("connection")["connection"]:
            if field == "board_slug":
                continue
            payload = sample("connection")
            del payload["connection"][field]
            self.rejected(payload)

    def test_duplicate_json_keys_at_top_and_nested_levels_are_rejected(self):
        for raw in (
            b'{"kind":"nothing","kind":"nothing"}',
            b'{"question":{"text":"one","text":"two"}}',
            b'{"places_checked":[{"name":"one","name":"two"}]}',
        ):
            with self.subTest(raw=raw), self.assertRaisesRegex(ReportError, "duplicate"):
                parse_json_report(raw)

    def test_invalid_json_encoding_nonfinite_numbers_and_depth_are_safe_errors(self):
        for raw in (b"", b"{bad", b"\xff", b'{"posts_read":NaN}', b'{"posts_read":Infinity}', b"[" * 2000 + b"]" * 2000, b"null", b"[]", b'"string"'):
            with self.subTest(raw=raw[:30]), self.assertRaises(ReportError) as caught:
                parse_json_report(raw)
            self.assertEqual(caught.exception.status, 422)

    def test_raw_and_validated_payload_size_limits_are_enforced(self):
        with self.assertRaises(ReportError) as caught:
            parse_json_report(b" " * (32 * 1024 + 1))
        self.assertEqual(caught.exception.status, 413)
        payload = sample("connection")
        payload["connection"]["evidence"] = [{"quote_or_link": "q" * 2000, "matches": "m" * 1000} for _ in range(20)]
        self.rejected(payload, 413)

    def test_timestamp_requires_real_date_seconds_and_timezone(self):
        for value in ("2026-10-30", "2026-10-30T12:00:00", "2026-10-30T12:00Z", "2026-02-30T12:00:00Z", "2026-10-30T12:00:00+99:00", "2026-10-30T12:00:00+00:99", "2026-10-30T12:00:00-00:60", "2026-10-30T12:00:00+24:00", "0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59", 123, None):
            payload = sample()
            payload["checked_at"] = value
            self.rejected(payload)
        payload = sample()
        payload["checked_at"] = "2026-10-30T14:30:00+02:30"
        self.assertEqual(validate_report(payload)["checked_at"], "2026-10-30T12:00:00.000000Z")

    def test_counts_types_collections_and_lengths_are_bounded(self):
        for value in (True, False, -1, 1.0, "1", 1_000_001):
            payload = sample()
            payload["posts_read"] = value
            self.rejected(payload)
        for value in ({}, None, [{"name": "A"}] * 21):
            payload = sample()
            payload["places_checked"] = value
            self.rejected(payload)
        for value in ([], {}, [{"quote_or_link": "x", "matches": "y"}] * 21):
            payload = sample("connection")
            payload["connection"]["evidence"] = value
            self.rejected(payload)
        for value in (None, [], "", " " * 4, "x" * 1001, "bad\x00value", "spoof\u202evalue", "\ud800"):
            payload = sample("question")
            payload["question"]["text"] = value
            self.rejected(payload)

    def test_only_https_links_without_credentials_are_accepted(self):
        for value in ("http://example.test", "javascript:alert(1)", "//example.test", "https://user:password@example.test", "https://@example.test", "https://example.test:0", "https://example.test:99999", "https://exa mple.test", "https://example.test\\evil", "https://"):
            for field in ("place", "connection", "evidence"):
                payload = sample("connection")
                if field == "place":
                    payload["places_checked"][0]["url"] = value
                elif field == "connection":
                    payload["connection"]["where"] = value
                else:
                    payload["connection"]["evidence"][0]["quote_or_link"] = value
                with self.subTest(value=value, field=field):
                    if field == "evidence" and not value.startswith(("http:", "https:", "javascript:")):
                        continue  # Plain quotations may contain arbitrary inert text.
                    self.rejected(payload)

    def test_scores_and_percentages_are_rejected_in_every_narrative_field(self):
        for value in ("A 90% match", "A 90 % match", "confidence: 0.9", "score = 7", "confidence of 0.9", "8/10", "９０％", "90 percent", "8 out of 10"):
            payload = sample("question")
            payload["question"]["text"] = value
            self.assertIn("scores or percentages", self.rejected(payload).detail)
        for path in (("places_checked", 0, "name"), ("question", "why"), ("connection", "who"), ("connection", "why_now"), ("connection", "evidence", 0, "quote_or_link"), ("connection", "evidence", 0, "matches")):
            payload = sample("question" if "question" in path else "connection")
            target = payload
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = "90%"
            self.rejected(payload)

    def test_html_and_markdown_are_preserved_as_untrusted_plain_text(self):
        payload = sample("question")
        payload["question"]["text"] = '<script>alert(1)</script> [link](javascript:alert(1))'
        self.assertEqual(validate_report(payload)["question"]["text"], payload["question"]["text"])
        connection = sample("connection")
        connection["connection"]["evidence"][0]["quote_or_link"] = "Seeking: a small prototype collaborator"
        self.assertEqual(validate_report(connection)["connection"]["evidence"][0]["quote_or_link"], "Seeking: a small prototype collaborator")

    def test_paste_all_kinds_and_repeated_places_evidence_match_json(self):
        self.assertEqual(parse_paste(PASTE_COMMON.format(kind="nothing")), validate_report(sample()))
        question = PASTE_COMMON.format(kind="question") + "Question: Would a short prototype fit your plans?\nWhy: The next step depends on your availability.\n"
        self.assertEqual(parse_paste(question), validate_report(sample("question")))
        self.assertEqual(parse_paste(PASTE_CONNECTION), validate_report(sample("connection")))
        extra = PASTE_CONNECTION + "Places checked: Another fictional board\nEvidence: https://example.test/source | The project is explicit.\n"
        result = parse_paste(extra)
        self.assertEqual(len(result["places_checked"]), 2)
        self.assertEqual(len(result["connection"]["evidence"]), 2)
        empty = PASTE_COMMON.format(kind="nothing").replace("Fictional makers board | https://board.example.test", "none")
        self.assertEqual(parse_paste(empty)["places_checked"], [])

    def test_paste_never_invents_legacy_metadata_or_ignores_malformed_lines(self):
        for value in (
            "Connection: Joon | https://example.test\nWhy now: A project",
            PASTE_CONNECTION.replace("Posts read: 4\n", ""),
            PASTE_CONNECTION + "Unrecognized: hidden data\n",
            PASTE_CONNECTION + "Why now: repeated\n",
            PASTE_CONNECTION + " continuation without a label\n",
            PASTE_CONNECTION.replace(" | https://board.example.test/joon", ""),
            PASTE_CONNECTION.replace(" | A small, concrete project.", ""),
            PASTE_CONNECTION.replace("Kind: connection", "Kind: nothing"),
            PASTE_CONNECTION.replace("Posts read: 4", "Posts read: -1"),
            PASTE_CONNECTION + "Places checked: none\n",
        ):
            with self.subTest(value=value[-60:]), self.assertRaises(ReportError):
                parse_paste(value)


if __name__ == "__main__":
    unittest.main()
