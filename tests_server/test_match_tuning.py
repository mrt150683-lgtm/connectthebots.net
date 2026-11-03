"""Match tuning: per-area strictness sliders and stated preferences.

Every expected value below is worked by hand from the fictional fingerprints in
test_personality (Rowan and Wren) and the thresholds in personality.THRESHOLDS,
not copied from scorer output. All people here are fictional.
"""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from connectthebots_server import match_tuning, personality
from connectthebots_server.agent_common import AgentError

from test_personality import ROWAN, WREN, encode

SERVER = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if SERVER:
    from starlette.testclient import TestClient
    from connectthebots_server import agent_texts
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time
    from connectthebots_server.taste import pair_match

R, W = personality.parse(ROWAN), personality.parse(WREN)


def levels(**areas):
    return {area: personality.LEVELS.index(name) for area, name in areas.items()}


def settings(preferences=(), **areas):
    return match_tuning.validate({"areas": levels(**areas), "preferences": list(preferences)})


class AreaTests(unittest.TestCase):
    def test_every_trait_is_in_exactly_one_area(self):
        keys = [key for _, _, area_keys in personality.AREAS.values() for key in area_keys]
        self.assertEqual(sorted(keys), sorted(personality.KEYS))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(match_tuning.AREAS, ("temperament", "social", "thinking", "communication", "working", "lifestyle", "interests"))

    def test_balanced_is_exactly_the_original_rules(self):
        self.assertEqual(personality.THRESHOLDS[personality.DEFAULT_LEVEL],
                         (personality.ALIKE_LEAN, personality.ALIKE_CLOSE, personality.COMPLEMENT_LEAN,
                          personality.COMPLEMENT_GAP, personality.CLASH_GAP, 1))
        everything_balanced = {area: 2 for area in personality.AREAS}
        self.assertEqual(personality.pair_fit(R, W, everything_balanced), personality.pair_fit(R, W))
        self.assertEqual(personality.pair_fit(R, W)["strength"], 5)

    def test_each_level_is_tighter_than_the_last(self):
        rows = [personality.THRESHOLDS[level] for level in (1, 2, 3, 4)]
        for looser, tighter in zip(rows, rows[1:]):
            self.assertLess(looser[0], tighter[0])     # alike needs a clearer lean
            self.assertGreater(looser[1], tighter[1])  # and a smaller gap
            self.assertLess(looser[2], tighter[2])     # complement needs a clearer lean
            self.assertLess(looser[3], tighter[3])     # and a bigger gap
            self.assertGreater(looser[4], tighter[4])  # a smaller gap already counts as a clash
            self.assertLessEqual(looser[5], tighter[5])


class SliderTests(unittest.TestCase):
    """Rowan and Wren at balanced: alike travel, debate, outlook (+3); complements
    initiative and project_role (+4); differences order, chronotype (-2). Strength 5."""

    def test_tight_temperament_drops_a_borderline_similarity(self):
        # outlook 200/180: leans 73/53. Tight needs 55 on both sides, so it no
        # longer counts. Every other temperament trait is middling. 5 - 1 = 4.
        fit = personality.pair_fit(R, W, levels(temperament="tight"))
        self.assertEqual([entry["trait"] for entry in fit["alike"]], ["travel", "debate"])
        self.assertEqual(fit["strength"], 4)

    def test_off_removes_an_area_entirely(self):
        # working holds initiative (+2), project_role (+2) and order (-1).
        # 5 - 2 - 2 + 1 = 2, and no complements remain.
        fit = personality.pair_fit(R, W, levels(working="off"))
        self.assertEqual(fit["complements"], [])
        self.assertEqual(fit["differences"], ["chronotype"])
        self.assertEqual(fit["strength"], 2)
        self.assertNotIn("one of you", fit["explanation"])

    def test_loose_lifestyle_forgives_a_clash(self):
        # chronotype 220/20: gap 200 < loose clash gap 210, so no longer a
        # difference. travel (leans 97/81, gap 16) still alike at weight 1. 5 + 1 = 6.
        fit = personality.pair_fit(R, W, levels(lifestyle="loose"))
        self.assertEqual(fit["differences"], ["order"])
        self.assertEqual(fit["strength"], 6)

    def test_strict_lifestyle_weighs_more_and_rules_out_the_clash(self):
        # travel: leans 97/81 >= 70, gap 16 <= 30 -> alike, weight 3.
        # chronotype: gap 200 >= 110 -> difference, weight -3.
        # 1 (outlook) + 1 (debate) + 3 (travel) + 2 + 2 (complements) - 1 (order) - 3 = 5.
        fit = personality.pair_fit(R, W, levels(lifestyle="strict"))
        self.assertEqual(fit["strength"], 5)
        self.assertEqual(personality.strict_clashes(R, W, levels(lifestyle="strict")), ["chronotype"])
        self.assertTrue(match_tuning.rules_out(settings(lifestyle="strict"), R, W, {}))
        self.assertFalse(match_tuning.rules_out(settings(lifestyle="tight"), R, W, {}))

    def test_loose_finds_a_similarity_balanced_misses(self):
        base = {key: 0x7F for key in personality.KEYS[1:12]}
        one = personality.parse(encode(**base, outlook=127 + 30))
        two = personality.parse(encode(**base, outlook=127 + 120))
        self.assertIsNone(personality.pair_fit(one, two))                         # lean 30 < 40, gap 90 > 70
        loose = personality.pair_fit(one, two, levels(temperament="loose"))      # lean 30 >= 25, gap 90 <= 100
        self.assertEqual(loose["alike"], [{"trait": "outlook", "both": "upbeat"}])


class PreferenceTests(unittest.TestCase):
    def test_trait_preference_reads_the_lean(self):
        early = {"kind": "trait", "trait": "chronotype", "want": "left", "importance": "must"}
        owl = {**early, "want": "right"}
        # Wren's chronotype is 20: 107 left of the middle. Rowan's is 220: 93 right.
        self.assertEqual(match_tuning.met_preferences(settings([early]), R, W, {}), [early])
        self.assertTrue(match_tuning.rules_out(settings([owl]), R, W, {}))
        self.assertFalse(match_tuning.rules_out(settings([owl]), W, R, {}))
        self.assertEqual(match_tuning.describe(early), "early bird")

    def test_trait_preference_needs_a_clear_lean_and_both_fingerprints(self):
        want = {"kind": "trait", "trait": "humour", "want": "right", "importance": "must"}
        base = {key: 0x7F for key in personality.KEYS[20:32]}  # twelve middling traits, not humour
        unknown = personality.parse(encode(**base))
        barely = personality.parse(encode(**base, humour=127 + 39))
        clearly = personality.parse(encode(**base, humour=127 + 40))
        self.assertFalse(match_tuning.rules_out(settings([want]), R, unknown, {}), "not assessed is not a no")
        self.assertTrue(match_tuning.rules_out(settings([want]), R, barely, {}))
        self.assertFalse(match_tuning.rules_out(settings([want]), R, clearly, {}))
        # An owner who shares no fingerprint cannot filter by other people's traits.
        self.assertFalse(match_tuning.rules_out(settings([want]), None, barely, {}))
        self.assertEqual(match_tuning.met_preferences(settings([want]), None, clearly, {}), [])

    def test_interest_preference_uses_only_visible_items(self):
        jazz = {"kind": "interest", "topic": "music", "item": "Jazz", "importance": "must"}
        self.assertTrue(match_tuning.rules_out(settings([jazz]), None, None, {}))
        self.assertTrue(match_tuning.rules_out(settings([jazz]), None, None, {"films": ["jazz"]}))
        self.assertFalse(match_tuning.rules_out(settings([jazz]), None, None, {"music": ["  JAZZ "]}))

    def test_validation(self):
        good = match_tuning.validate({"areas": {"social": "tight", "interests": 0}, "approved_by_owner": True})
        self.assertEqual(good["areas"]["social"], 3)
        self.assertEqual(good["areas"]["interests"], 0)
        self.assertEqual(good["areas"]["working"], 2)
        kept = match_tuning.validate({"preferences": []}, good)
        self.assertEqual(kept["areas"]["social"], 3, "omitted parts keep their current value")
        for bad in ({"areas": {"politics": "strict"}}, {"areas": {"social": 5}}, {"areas": {"social": "extreme"}},
                    {"areas": {"social": True}}, {"preferences": [{"kind": "trait", "trait": "nope", "want": "left"}]},
                    {"preferences": [{"kind": "trait", "trait": "humour", "want": "up"}]},
                    {"preferences": [{"kind": "interest", "topic": "politics", "item": "left"}]},
                    {"preferences": [{"kind": "interest", "topic": "looking_for", "item": "friends"}]},
                    {"preferences": [{"kind": "interest", "topic": "music", "item": "jazz", "importance": "maybe"}]},
                    {"preferences": [{"kind": "interest", "topic": "music", "item": "jazz"}, {"kind": "interest", "topic": "music", "item": "JAZZ"}]},
                    {"preferences": [{"kind": "interest", "topic": "music", "item": f"x{n}"} for n in range(13)]},
                    {"preferences": [{"kind": "mood"}]}, {"anything": 1}):
            with self.subTest(bad=bad), self.assertRaises(AgentError) as refused:
                match_tuning.validate(bad)
            self.assertEqual(refused.exception.status, 422)


@unittest.skipUnless(SERVER, "optional server extra not installed")
class PairMatchTuningTests(unittest.TestCase):
    @staticmethod
    def answer(items, tier="share"):
        return {"items": items, "tier": tier, "skipped": False}

    def test_no_settings_and_default_settings_are_identical(self):
        taste = {"films": self.answer(["90s horror"])}
        fit = personality.pair_fit(R, W)
        plain = pair_match(taste, taste, set(), set(), fit)
        tuned = pair_match(taste, taste, set(), set(), fit, tuning=match_tuning.default_settings(),
                           their_tuning=match_tuning.default_settings(), prints=(R, W))
        self.assertEqual(plain, tuned)

    def test_interest_slider(self):
        rowan = {"hobbies": self.answer(["climbing"])}
        wren = {"hobbies": self.answer(["climbing"])}
        # Balanced: hobbies weight 2 x 1 item = 2.
        self.assertEqual(pair_match(rowan, wren, set(), set())["strength"], 2)
        # Tight doubles shared favourites: 4. Strict triples them: 6.
        self.assertEqual(pair_match(rowan, wren, set(), set(), tuning=settings(interests="tight"))["strength"], 4)
        self.assertEqual(pair_match(rowan, wren, set(), set(), tuning=settings(interests="strict"))["strength"], 6)
        # Off: a taste-only match disappears.
        self.assertIsNone(pair_match(rowan, wren, set(), set(), tuning=settings(interests="off")))
        # Strict: a personality-only match (strength 5) disappears; loose keeps a weaker one (4 >= 3).
        fit = personality.pair_fit(R, W)
        self.assertIsNotNone(pair_match({}, {}, set(), set(), fit))
        self.assertIsNone(pair_match({}, {}, set(), set(), fit, tuning=settings(interests="strict")))
        weak = personality.pair_fit(R, W, levels(temperament="tight"))
        self.assertIsNone(pair_match({}, {}, set(), set(), weak))
        self.assertIsNotNone(pair_match({}, {}, set(), set(), weak, tuning=settings(interests="loose")))

    def test_preferences_explain_rank_and_rule_out_in_both_directions(self):
        rowan = {"hobbies": self.answer(["climbing"])}
        wren = {"hobbies": self.answer(["climbing"]), "music": self.answer(["jazz", "folk"])}
        nice = {"kind": "interest", "topic": "music", "item": "jazz", "importance": "nice"}
        result = pair_match(rowan, wren, set(), set(), tuning=settings([nice]))
        self.assertEqual(result["explanation"], "You both are into climbing. Fits what you asked for: jazz (music).")
        self.assertEqual(result["strength"], 2 + match_tuning.PREFERENCE_BONUS)
        self.assertEqual(result["asked_for"], [{"kind": "interest", "topic": "music", "item": "jazz", "reads_as": "jazz (music)"}])
        # A met preference alone is a reason to meet, with nothing else shared.
        alone = pair_match({}, wren, set(), set(), tuning=settings([nice]))
        self.assertEqual(alone["explanation"], "Fits what you asked for: jazz (music).")
        # Wren's must-have rules Rowan out of Wren's list and Wren out of Rowan's.
        must = {"kind": "interest", "topic": "books", "item": "atlases", "importance": "must"}
        self.assertIsNone(pair_match(rowan, wren, set(), set(), their_tuning=settings([must])))
        self.assertIsNone(pair_match(wren, rowan, set(), set(), tuning=settings([must])))
        # A hidden answer never satisfies or reveals anything.
        hidden = {"hobbies": self.answer(["climbing"]), "books": self.answer(["atlases"], "ask")}
        self.assertIsNone(pair_match(hidden, wren, set(), set(), their_tuning=settings([must])))
        self.assertIsNotNone(pair_match(hidden, wren, {"books"}, set(), their_tuning=settings([must])))

    def test_strict_area_clash_rules_out_both_ways(self):
        taste = {"films": self.answer(["90s horror"])}
        strict = settings(lifestyle="strict")
        self.assertIsNotNone(pair_match(taste, taste, set(), set(), personality.pair_fit(R, W), prints=(R, W)))
        self.assertIsNone(pair_match(taste, taste, set(), set(), personality.pair_fit(R, W, levels(lifestyle="strict")), tuning=strict, prints=(R, W)))
        self.assertIsNone(pair_match(taste, taste, set(), set(), personality.pair_fit(W, R), their_tuning=strict, prints=(W, R)))


@unittest.skipUnless(SERVER, "optional server extra not installed")
class MatchSettingsApiTests(unittest.TestCase):
    PEOPLE = ("rowan", "wren", "ivo")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        settings_ = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://agents.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-only-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings_, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings_.public_base_url))
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

    def interview(self, person, answers):
        self.ok(person, "PUT", "/api/v1/agent/interview/answers", {"answers": [
            *({"topic": topic, "items": items, "tier": "share"} for topic, items in answers.items()),
            {"topic": "looking_for", "items": ["friends"], "tier": "share"},
            {"topic": "food", "items": ["toast"], "tier": "never"},
            {"topic": "books", "items": ["atlases"], "tier": "never"},
            {"topic": "tv", "items": ["quiz shows"], "tier": "never"}]})
        self.ok(person, "POST", "/api/v1/agent/interview/complete", {})

    def names(self, person):
        return [match["member"]["first_name"] for match in self.ok(person, "GET", "/api/v1/agent/matches")["matches"]]

    def setup_three(self):
        # Rowan shares climbing and chess with Wren (2 x 2 = 4) and only
        # climbing with Ivo (2). Ivo also lists jazz.
        for person in self.PEOPLE:
            self.ok(person, "PATCH", "/api/v1/agent/profile", {"first_name": person.title()})
        self.interview("rowan", {"hobbies": ["climbing", "chess"]})
        self.interview("wren", {"hobbies": ["climbing", "chess"]})
        self.interview("ivo", {"hobbies": ["climbing"], "music": ["jazz"]})

    def test_catalogue_and_defaults(self):
        info = self.ok("rowan", "GET", "/api/v1/agent/match-settings")
        self.assertEqual([area["area"] for area in info["areas"]], list(match_tuning.AREAS))
        self.assertEqual(info["levels"], ["off", "loose", "balanced", "tight", "strict"])
        self.assertEqual(set(info["current"]["areas"].values()), {"balanced"})
        self.assertFalse(info["current"]["customised"])
        self.assertEqual(info["current"]["preferences"], [])

    def test_saving_needs_the_owners_yes_and_valid_values(self):
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"social": "tight"}}, status=409)
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"social": "max"}, "approved_by_owner": True}, status=422)
        saved = self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"social": "tight"}, "approved_by_owner": True})
        self.assertEqual(saved["current"]["areas"]["social"], "tight")
        self.assertTrue(saved["current"]["customised"])
        self.assertTrue(self.ok("rowan", "DELETE", "/api/v1/agent/match-settings")["reset"])
        self.assertFalse(self.ok("rowan", "GET", "/api/v1/agent/match-settings")["current"]["customised"])

    def test_settings_really_change_the_match_list(self):
        self.setup_three()
        self.assertEqual(self.names("rowan"), ["Wren", "Ivo"])                     # 4 beats 2
        jazz = {"kind": "interest", "topic": "music", "item": "jazz", "importance": "nice"}
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"preferences": [jazz], "approved_by_owner": True})
        matches = self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"]
        self.assertEqual([match["member"]["first_name"] for match in matches], ["Ivo", "Wren"])   # 2 + 3 = 5 beats 4
        self.assertEqual(matches[0]["explanation"], "You both are into climbing and are both looking for friends. Fits what you asked for: jazz (music).")
        self.assertEqual(matches[0]["asked_for"][0]["reads_as"], "jazz (music)")
        self.assertEqual(matches[1]["asked_for"], [])
        # Tight interests double shared favourites: Wren 8, Ivo 4 + 3 = 7.
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"interests": "tight"}, "approved_by_owner": True})
        self.assertEqual(self.names("rowan"), ["Wren", "Ivo"])
        # Must-have jazz: only Ivo. A must-have rules out in both directions,
        # so Rowan also leaves Wren's list; Ivo, who lists jazz, still sees Rowan.
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"preferences": [{**jazz, "importance": "must"}], "approved_by_owner": True})
        self.assertEqual(self.names("rowan"), ["Ivo"])
        self.assertEqual(self.names("wren"), ["Ivo"])
        self.assertCountEqual(self.names("ivo"), ["Rowan", "Wren"])
        # Interests off: shared favourites no longer count. Ivo still meets the must-have.
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"interests": "off"}, "approved_by_owner": True})
        only = self.ok("rowan", "GET", "/api/v1/agent/matches")["matches"]
        self.assertEqual([match["shared"] for match in only], [[]])
        self.assertNotIn("climbing", only[0]["explanation"])
        # Reset returns to the original order.
        self.ok("rowan", "DELETE", "/api/v1/agent/match-settings")
        self.assertEqual(self.names("rowan"), ["Wren", "Ivo"])

    def test_others_never_see_your_settings(self):
        self.setup_three()
        jazz = {"kind": "interest", "topic": "music", "item": "jazz", "importance": "nice"}
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"preferences": [jazz], "areas": {"social": "strict"}, "approved_by_owner": True})
        seen_by_ivo = self.ok("ivo", "GET", "/api/v1/agent/matches")["matches"]
        self.assertEqual([match["asked_for"] for match in seen_by_ivo], [[], []])
        self.assertNotIn("asked for", json.dumps(seen_by_ivo))
        self.assertNotIn("strict", json.dumps(seen_by_ivo))

    def test_dashboard_sliders_save_remove_and_reset(self):
        self.client.cookies.set(SESSION_COOKIE, self.sessions["rowan"])
        page = self.client.get("/app").text
        self.assertIn('data-match-settings="default"', page)
        self.assertIn('<input type="range" id="tune-social" name="social" min="0" max="4" step="1" value="2" aria-valuetext="balanced"', page)
        self.assertIn('<script src="/assets/tuning.js" defer></script>', page)
        self.assertIn('data-preferences="none"', page)
        csrf = self.app.state.auth.sessions.current(self.sessions["rowan"], refresh=False).csrf
        form = {area: "2" for area in match_tuning.AREAS} | {"social": "4", "interests": "0"}
        self.assertEqual(self.client.post("/app/match-settings", data={**form, "csrf": "wrong"}).status_code, 403)
        self.assertEqual(self.client.post("/app/match-settings", data={**form, "social": "9", "csrf": csrf}).status_code, 422)
        self.assertEqual(self.client.post("/app/match-settings", data={**{k: v for k, v in form.items() if k != "social"}, "csrf": csrf}).status_code, 422)
        saved = self.client.post("/app/match-settings", data={**form, "csrf": csrf}, follow_redirects=False)
        self.assertEqual(saved.status_code, 303)
        current = self.ok("rowan", "GET", "/api/v1/agent/match-settings")["current"]
        self.assertEqual(current["areas"]["social"], "strict")
        self.assertEqual(current["areas"]["interests"], "off")
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"preferences": [
            {"kind": "trait", "trait": "chronotype", "want": "right", "importance": "must"},
            {"kind": "interest", "topic": "music", "item": "jazz"}], "approved_by_owner": True})
        page = self.client.get("/app").text
        self.assertIn('data-match-settings="custom"', page)
        self.assertIn("must have</span><span class=\"items\">night owl", page)
        self.assertIn("nice to have</span><span class=\"items\">jazz (music)", page)
        self.assertEqual(self.client.post("/app/match-settings/preferences/5/remove", data={"csrf": csrf}).status_code, 404)
        self.client.post("/app/match-settings/preferences/0/remove", data={"csrf": csrf}, follow_redirects=False)
        remaining = self.ok("rowan", "GET", "/api/v1/agent/match-settings")["current"]["preferences"]
        self.assertEqual([entry["reads_as"] for entry in remaining], ["jazz (music)"])
        self.assertEqual(self.ok("rowan", "GET", "/api/v1/agent/match-settings")["current"]["areas"]["social"], "strict")
        self.client.post("/app/match-settings/reset", data={"csrf": csrf}, follow_redirects=False)
        self.assertFalse(self.ok("rowan", "GET", "/api/v1/agent/match-settings")["current"]["customised"])

    def test_export_recovery_revision_and_account_deletion(self):
        def revision():
            with self.db.connect() as connection:
                return connection.execute("SELECT growth_revision FROM recovery_authority WHERE singleton=1").fetchone()[0]
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"social": "tight"}, "approved_by_owner": True})
        first = revision()
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"social": "tight"}, "approved_by_owner": True})
        self.assertEqual(revision(), first, "an identical save is not a protected change")
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"social": "loose"}, "approved_by_owner": True})
        self.assertGreater(revision(), first)
        changed = revision()
        self.ok("rowan", "DELETE", "/api/v1/agent/match-settings")
        self.assertGreater(revision(), changed)
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"areas": {"social": "tight"}, "approved_by_owner": True})
        self.client.cookies.set(SESSION_COOKIE, self.sessions["rowan"])
        csrf = self.app.state.auth.sessions.current(self.sessions["rowan"], refresh=False).csrf
        exported = self.client.post("/app/account/export", data={"csrf": csrf}).json()
        self.assertEqual(exported["agent_network"]["match_settings"]["areas"]["social"], "tight")
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM users WHERE id='rowan'")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM match_settings").fetchone()[0], 0)

    def test_agent_guide_describes_it(self):
        texts = self.client.get("/api/v1/agent/texts").json()["texts"]
        self.assertIn("match_settings", texts)
        self.assertIn("/api/v1/agent/match-settings", texts["match_settings"]["text"])


if __name__ == "__main__":
    unittest.main()
