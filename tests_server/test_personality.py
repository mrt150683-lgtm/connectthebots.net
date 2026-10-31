"""Personality fingerprint: encoding, hand-worked matching rules and the owner-approved API.

All people and fingerprints here are fictional. The pure rules need only the
standard library; the API tests drive the real HTTP agent API.
"""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from connectthebots_server import personality
from connectthebots_server.agent_common import AgentError

SERVER = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if SERVER:
    from starlette.testclient import TestClient
    from connectthebots_server import agent_texts
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time
    from connectthebots_server.taste import TasteService, pair_match


def encode(**traits):
    """Build a fingerprint from trait values; every other trait is ff."""
    unknown = set(traits) - set(personality.KEYS)
    assert not unknown, unknown
    return "".join(f"{traits.get(key, 0xFF):02x}" for key in personality.KEYS)


# Hand-worked oracle pair (see test_hand_worked_pair for the arithmetic).
ROWAN = encode(outlook=0xC8, chronotype=0xDC, initiative=0xE6, project_role=0x28, scope=0x80,
               energy=0x7F, pace=0x80, risk=0x90, debate=0x30, order=0x20, travel=0xE0, patience=0x7F, focus=0x80)
WREN = encode(outlook=0xB4, chronotype=0x14, initiative=0x1E, project_role=0xD2, scope=0x10,
              energy=0x7F, pace=0x80, risk=0x98, debate=0x40, order=0xE0, travel=0xD0, patience=0x80, focus=0x7F)


class EncodingTests(unittest.TestCase):
    def test_schema_is_32_traits_of_two_hex_digits(self):
        self.assertEqual(len(personality.DIMENSIONS), 96)
        self.assertEqual(personality.LENGTH, 192)
        self.assertEqual(len(set(personality.KEYS)), 96)
        self.assertEqual({entry[3] for entry in personality.DIMENSIONS}, {"alike", "complement"})
        self.assertEqual(personality.catalogue()[29]["hex_offset"], 58)

    def test_parse_reads_values_and_unknowns(self):
        values = personality.parse(ROWAN.upper())
        self.assertEqual(values[0], 0xC8)
        self.assertIsNone(values[personality.KEYS.index("humour")])
        self.assertEqual(sum(value is not None for value in values), 13)

    def test_parse_refuses_bad_strings(self):
        for bad in (ROWAN[:-2], ROWAN + "00", "zz" + ROWAN[2:], None, 42, "ff" * 96,
                    encode(outlook=1, energy=2, pace=3, risk=4, debate=5, order=6, scope=7)):
            with self.subTest(bad=bad), self.assertRaises(AgentError) as refused:
                personality.parse(bad)
            self.assertEqual(refused.exception.status, 422)

    def test_readout_uses_plain_bands(self):
        lines = {line["key"]: line["reads_as"] for line in personality.readout(personality.parse(ROWAN))}
        self.assertEqual(lines["outlook"], "somewhat upbeat")
        self.assertEqual(lines["chronotype"], "strongly night owl")
        self.assertEqual(lines["scope"], "in between detail and big picture")
        self.assertEqual(lines["order"], "strongly relaxed about mess")
        self.assertEqual(lines["humour"], "not assessed")


class MatchingRuleTests(unittest.TestCase):
    def test_hand_worked_pair(self):
        # outlook c8/b4 = 200/180: both right of 127, leans 73/53 >= 40, gap 20 <= 70 -> alike "upbeat".
        # debate 30/40 = 48/64: both left, leans 79/63, gap 16 -> alike "peacekeepers" (stronger lean, listed first).
        # chronotype dc/14 = 220/20 and order 20/e0 = 32/224: gaps 200 and 192 >= 170 -> differences, -1 each.
        # initiative e6/1e = 230/30: opposite sides, leans 103/97 >= 30, gap 200 -> complement.
        # project_role 28/d2 = 40/210: leans 87/83, gap 170 -> complement.
        # scope 80/10: Rowan's 128 is middling (lean 1 < 30) -> nothing, despite a gap of 112.
        # travel e0/d0 = 224/208: leans 97/81, gap 16 -> alike "bitten by the travel bug" (strongest, listed first).
        # energy, pace, risk, patience, focus: middling or close to it -> nothing. Strength 3 + 4 - 2 = 5.
        fit = personality.pair_fit(personality.parse(ROWAN), personality.parse(WREN))
        self.assertEqual(fit["alike"], [{"trait": "travel", "both": "bitten by the travel bug"}, {"trait": "debate", "both": "peacekeepers"}, {"trait": "outlook", "both": "upbeat"}])
        self.assertEqual([entry["trait"] for entry in fit["complements"]], ["initiative", "project_role"])
        self.assertEqual(fit["complements"][0], {"trait": "initiative", "one_of_you": "likes to take the lead", "the_other": "is happy to follow a lead"})
        self.assertEqual(fit["differences"], ["order", "chronotype"])
        self.assertEqual(fit["strength"], 5)
        self.assertEqual(fit["explanation"],
                         "Personality-wise, you're both bitten by the travel bug, peacekeepers and upbeat. You could suit each other: "
                         "one of you likes to take the lead and the other is happy to follow a lead; "
                         "one of you loves starting things and the other loves finishing things.")

    def test_fit_is_symmetric_apart_from_wording(self):
        forward = personality.pair_fit(personality.parse(ROWAN), personality.parse(WREN))
        backward = personality.pair_fit(personality.parse(WREN), personality.parse(ROWAN))
        self.assertEqual(forward["strength"], backward["strength"])
        self.assertEqual(forward["explanation"], backward["explanation"])

    def test_middling_unknown_and_missing_never_count(self):
        middling = encode(**{key: 0x7F for key in personality.KEYS[:12]})
        self.assertIsNone(personality.pair_fit(personality.parse(middling), personality.parse(middling)))
        self.assertIsNone(personality.pair_fit(personality.parse(ROWAN), None))
        one = encode(outlook=0xE0, **{key: 0x7F for key in personality.KEYS[1:12]})
        other = encode(**{key: 0x7F for key in personality.KEYS[1:13]})
        self.assertIsNone(personality.pair_fit(personality.parse(one), personality.parse(other)))

    def test_edges_of_each_threshold(self):
        base = {key: 0x7F for key in personality.KEYS[1:12]}
        def fit(a, b, trait):
            return personality.pair_fit(personality.parse(encode(**base, **{trait: a})), personality.parse(encode(**base, **{trait: b})))
        self.assertIsNotNone(fit(127 + 40, 127 + 40 + 70, "outlook"))      # lean 40, gap 70: alike
        self.assertIsNone(fit(127 + 39, 200, "outlook"))                  # lean 39: no
        self.assertIsNone(fit(127 + 40, 127 + 40 + 71, "outlook"))        # gap 71: no
        self.assertIsNotNone(fit(127 - 50, 127 + 50, "initiative"))      # leans 50, gap 100: complement
        self.assertIsNone(fit(127 - 29, 127 + 90, "initiative"))         # lean 29: no
        self.assertIsNone(fit(127 - 49, 127 + 50, "initiative"))         # gap 99: no
        self.assertIsNone(fit(0x10, 0xF0, "outlook"))                    # opposite ends of an alike trait: a difference only

    def test_reasons_never_carry_values(self):
        fit = personality.pair_fit(personality.parse(ROWAN), personality.parse(WREN))
        text = json.dumps({key: fit[key] for key in ("alike", "complements", "differences", "explanation")})
        for value in ("c8", "b4", "200", "180", "0x"):
            self.assertNotIn(value, text)


@unittest.skipUnless(SERVER, "optional server extra not installed")
class PairMatchTests(unittest.TestCase):
    def setUp(self):
        self.fit = personality.pair_fit(personality.parse(ROWAN), personality.parse(WREN))

    @staticmethod
    def answer(items, tier="share"):
        return {"items": items, "tier": tier, "skipped": False}

    def test_personality_alone_needs_enough_signals(self):
        result = pair_match({}, {}, set(), set(), self.fit)
        self.assertTrue(result["explanation"].startswith("Personality-wise, you're both bitten by the travel bug, peacekeepers and upbeat."))
        self.assertEqual(result["personality"]["differences"], ["order", "chronotype"])
        weak = {**self.fit, "strength": personality.MIN_PERSONALITY_ONLY - 1}
        self.assertIsNone(pair_match({}, {}, set(), set(), weak))
        # A weak fit still adds its reasons to a taste match.
        taste = {"teams": self.answer(["Norwich City"])}
        both = pair_match(taste, taste, set(), set(), weak)
        self.assertEqual(both["explanation"].split(" Personality-wise")[0], "You both support Norwich City.")
        self.assertIn("peacekeepers", both["explanation"])

    def test_exclusions_still_apply(self):
        self.assertIsNone(pair_match({"looking_for": self.answer(["friends"])}, {"looking_for": self.answer(["club"])}, set(), set(), self.fit))
        result = pair_match({"looking_for": self.answer(["friends"])}, {"looking_for": self.answer(["friends", "club"])}, set(), set(), self.fit)
        self.assertTrue(result["explanation"].startswith("You are looking for friends. Personality-wise"))

    def test_taste_only_matches_are_unchanged(self):
        taste = {"films": self.answer(["90s horror"])}
        result = pair_match(taste, taste, set(), set())
        self.assertEqual(result["explanation"], "You both love 90s horror.")
        self.assertIsNone(result["personality"])


@unittest.skipUnless(SERVER, "optional server extra not installed")
class PersonalityApiTests(unittest.TestCase):
    PEOPLE = ("rowan", "wren", "ivo")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://agents.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-only-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings.public_base_url))
        self.db = self.app.state.database
        self.keys, self.sessions = {}, {}
        with self.db.transaction() as connection:
            for person in self.PEOPLE:
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                   (person, "fictional-" + person, person + "@example.test", person.title() + " Example",
                                    storage_time(self.now), storage_time(self.now)))
                self.sessions[person] = self.app.state.auth.sessions.create(connection, person)
        for person in self.PEOPLE:
            self.keys[person] = self.app.state.reports.create_key(person, "Fictional agent")["key"]

    def call(self, person, method, path, body=None):
        headers = {"Authorization": "Bearer " + self.keys[person]}
        if body is not None:
            return self.client.request(method, path, content=json.dumps(body), headers={**headers, "Content-Type": "application/json"})
        return self.client.request(method, path, headers=headers)

    def ok(self, person, method, path, body=None, status=200):
        response = self.call(person, method, path, body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def approved(self, fingerprint, tier="share"):
        return {"schema": personality.SCHEMA, "fingerprint": fingerprint, "tier": tier,
                "notice_version": agent_texts.PERSONALITY_CONSENT_VERSION, "approved_by_owner": True}

    def complete_interview(self, person, items=("climbing",)):
        self.ok(person, "PUT", "/api/v1/agent/interview/answers", {"answers": [
            {"topic": "hobbies", "items": list(items), "tier": "share"},
            {"topic": "looking_for", "items": ["friends"], "tier": "share"},
            {"topic": "food", "items": ["toast"], "tier": "never"},
            {"topic": "music", "items": ["sea shanties"], "tier": "never"},
            {"topic": "books", "items": ["atlases"], "tier": "never"}]})
        self.ok(person, "POST", "/api/v1/agent/interview/complete", {})

    def test_catalogue_lists_traits_encoding_and_consent(self):
        info = self.ok("rowan", "GET", "/api/v1/agent/personality")
        self.assertEqual(len(info["traits"]), 96)
        self.assertEqual(info["traits"][0]["key"], "outlook")
        self.assertEqual(info["consent"]["notice_version"], agent_texts.PERSONALITY_CONSENT_VERSION)
        self.assertIsNone(info["current"])
        self.assertIn("personality_fingerprint", self.client.get("/api/v1/agent/texts").json()["texts"])
        # The import prompt lets another assistant score every trait, with no private details.
        lines = [line for line in info["import_prompt"].splitlines() if ": 0 = " in line]
        self.assertEqual([line.split(":")[0] for line in lines], list(personality.KEYS))
        self.assertIn("unknown", info["import_prompt"])
        self.assertIn("personality_import_prompt", self.client.get("/api/v1/agent/texts").json()["texts"])

    def test_saving_needs_owner_approval_and_the_current_statement(self):
        for change in ({"approved_by_owner": False}, {"notice_version": "personality-fingerprint-v0"}):
            with self.subTest(change=change):
                body = {**self.approved(ROWAN), **change}
                response = self.call("rowan", "PUT", "/api/v1/agent/personality", body)
                self.assertEqual(response.status_code, 409)
                self.assertIn("readout", response.json()["error"])
        for change in ({"schema": "ctb-personality-v0"}, {"tier": "ask"}, {"fingerprint": "ff" * 96}, {"evidence": "diary"}):
            with self.subTest(change=change):
                self.assertEqual(self.call("rowan", "PUT", "/api/v1/agent/personality", {**self.approved(ROWAN), **change}).status_code, 422)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM personality_fingerprints").fetchone()[0], 0)
        saved = self.ok("rowan", "PUT", "/api/v1/agent/personality", self.approved(ROWAN.upper()))["current"]
        self.assertEqual(saved["fingerprint"], ROWAN)
        self.assertEqual(saved["assessed"], 13)
        self.assertTrue(saved["used_for_matching"])
        self.assertEqual(saved["readout"][0], {"key": "outlook", "scale": "wistful to upbeat", "reads_as": "somewhat upbeat"})

    def test_complementary_people_match_without_shared_taste(self):
        self.complete_interview("rowan", ["climbing"])
        self.complete_interview("wren", ["knitting"])
        self.assertEqual(self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"], [])
        self.ok("rowan", "PUT", "/api/v1/agent/personality", self.approved(ROWAN))
        self.assertEqual(self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"], [], "one-sided fingerprints are not compared")
        self.ok("wren", "PUT", "/api/v1/agent/personality", self.approved(WREN))
        match, = self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"]
        self.assertEqual(match["explanation"],
                         "You are looking for friends. Personality-wise, you're both bitten by the travel bug, peacekeepers and upbeat. You could suit each other: "
                         "one of you likes to take the lead and the other is happy to follow a lead; "
                         "one of you loves starting things and the other loves finishing things.")
        self.assertEqual(match["personality"]["differences"], ["order", "chronotype"])
        self.assertNotIn(WREN, json.dumps(match))
        self.assertNotIn("strength", match)
        checkin = self.ok("wren", "POST", "/api/v1/agent/checkin", {})
        self.assertIn("peacekeepers", checkin["matches"]["new"][0]["explanation"])

    def test_never_tier_delete_and_stale_consent_stop_matching(self):
        self.complete_interview("rowan", ["climbing"])
        self.complete_interview("wren", ["knitting"])
        self.ok("rowan", "PUT", "/api/v1/agent/personality", self.approved(ROWAN))
        self.ok("wren", "PUT", "/api/v1/agent/personality", self.approved(WREN, "never"))
        self.assertEqual(self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"], [])
        self.ok("wren", "PUT", "/api/v1/agent/personality", self.approved(WREN))
        self.assertEqual(len(self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"]), 1)
        with self.db.transaction() as connection:
            connection.execute("UPDATE personality_fingerprints SET notice_version='personality-fingerprint-v0' WHERE user_id='wren'")
        self.assertEqual(self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"], [])
        self.assertFalse(self.ok("wren", "GET", "/api/v1/agent/personality")["current"]["used_for_matching"])
        self.ok("wren", "PUT", "/api/v1/agent/personality", self.approved(WREN))
        self.assertTrue(self.ok("rowan", "DELETE", "/api/v1/agent/personality")["deleted"])
        self.assertEqual(self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"], [])
        self.assertFalse(self.ok("rowan", "DELETE", "/api/v1/agent/personality")["deleted"])

    def test_me_offers_it_once_the_interview_starts(self):
        self.assertNotIn("optional", self.ok("ivo", "GET", "/api/v1/agent/me")["next_step"])
        self.complete_interview("ivo")
        me = self.ok("ivo", "GET", "/api/v1/agent/me")
        self.assertEqual(me["next_step"]["call"], ["POST /api/v1/agent/checkin"])
        self.assertEqual(me["next_step"]["optional"]["call"], ["GET /api/v1/agent/personality"])
        self.assertEqual(me["personality"], {"saved": False, "used_for_matching": False})
        self.ok("ivo", "PUT", "/api/v1/agent/personality", self.approved(ROWAN))
        me = self.ok("ivo", "GET", "/api/v1/agent/me")
        self.assertNotIn("optional", me["next_step"])
        self.assertEqual(me["personality"], {"saved": True, "used_for_matching": True})

    def test_owner_sees_it_on_the_dashboard_and_in_the_export(self):
        self.client.cookies.set(SESSION_COOKIE, self.sessions["rowan"])
        self.assertIn('data-personality-state="none"', self.client.get("/app").text)
        self.ok("rowan", "PUT", "/api/v1/agent/personality", self.approved(ROWAN))
        page = self.client.get("/app").text
        self.assertIn('data-personality-state="shared"', page)
        self.assertIn("13 of 96 traits assessed", page)
        self.assertIn('<span class="topic">chronotype</span><span class="items">strongly night owl</span>', page)
        self.assertNotIn("humour</strong>", page)
        with self.db.connect() as connection:
            exported = TasteService(self.db, lambda: self.now).export_owner("rowan", connection)
        self.assertEqual(exported["personality_fingerprint"]["fingerprint"], ROWAN)

    def test_changes_and_deletion_advance_recovery_authority_and_account_deletion_cascades(self):
        def revision():
            with self.db.connect() as connection:
                return connection.execute("SELECT growth_revision FROM recovery_authority WHERE singleton=1").fetchone()[0]
        self.ok("rowan", "PUT", "/api/v1/agent/personality", self.approved(ROWAN))
        first = revision()
        self.ok("rowan", "PUT", "/api/v1/agent/personality", self.approved(ROWAN))
        self.assertEqual(revision(), first, "an identical save is not a protected change")
        self.ok("rowan", "PUT", "/api/v1/agent/personality", self.approved(WREN))
        self.assertGreater(revision(), first)
        changed = revision()
        self.ok("rowan", "DELETE", "/api/v1/agent/personality")
        self.assertGreater(revision(), changed)
        self.ok("wren", "PUT", "/api/v1/agent/personality", self.approved(WREN))
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM users WHERE id='wren'")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM personality_fingerprints").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
