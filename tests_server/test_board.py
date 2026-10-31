"""Exact publication approval, private drafts, use-time visibility and schemas."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from connectthebots_server.board import BoardError, BoardService, touch_board
from connectthebots_server.board_schema import canonical_payload, payload_hash, sentence_count, validate_posting
from connectthebots_server.db import Database
from connectthebots_server.sessions import storage_time


def posting(label="Prototype"):
    return {"text": f"{label} is a small fictional project. We can offer interface design. We would like to test a useful prototype together.",
            "tags": ["Design", "Prototypes"], "looking_for": "A small workshop collaboration", "offering": "Interface design and testing"}


class BoardSchemaTests(unittest.TestCase):
    def rejected(self, payload):
        with self.assertRaises(BoardError) as caught:
            validate_posting(payload)
        self.assertEqual(caught.exception.status, 422)

    def test_typed_payload_is_complete_detached_and_deterministically_hashed(self):
        source = posting()
        clean = validate_posting(source)
        self.assertEqual(clean, source)
        self.assertEqual(payload_hash(clean), payload_hash(dict(reversed(list(clean.items())))))
        source["tags"].append("External edit")
        self.assertEqual(len(clean["tags"]), 2)
        self.assertEqual(json.loads(canonical_payload(clean)), clean)

    def test_every_extra_and_missing_field_is_rejected(self):
        for field in ("text", "tags", "looking_for", "offering"):
            value = posting()
            del value[field]
            self.rejected(value)
        for field in ("user_id", "email", "display_name", "title", "grant", "published"):
            value = posting()
            value[field] = "Unapproved extra"
            self.rejected(value)
        for value in (None, [], "text"):
            self.rejected(value)

    def test_three_to_five_sentences_ignore_urls_decimal_points_and_titles(self):
        for text, expected in (
            ("We build prototypes. We share tools. We seek a collaborator", 3),
            ("We work with Dr. Fictional. We meet at https://example.test/project. We use version 1.5.", 3),
            ('"Could this work?" We can test it! We offer design.', 3),
            ("One. Two. Three. Four. Five.", 5),
        ):
            self.assertEqual(sentence_count(text), expected)
            validate_posting({**posting(), "text": text})
        for text in ("Only one sentence.", "One. Two.", "One. Two. Three. Four. Five. Six.", ". . ."):
            self.rejected({**posting(), "text": text})

    def test_text_short_fields_tags_and_controls_have_fixed_bounds(self):
        boundary = "A" * 793 + ". B. C."
        self.assertEqual(len(boundary), 800)
        validate_posting({**posting(), "text": boundary})
        self.rejected({**posting(), "text": boundary + " "})
        for field in ("looking_for", "offering"):
            validate_posting({**posting(), field: "x" * 160})
            for value in ("", None, "x" * 161, "bad\x00control", "bad\u202econtrol", "\ud800"):
                self.rejected({**posting(), field: value})
        validate_posting({**posting(), "tags": []})
        validate_posting({**posting(), "tags": ["one", "two", "three", "four", "x" * 30]})
        for tags in (None, "not a list", ["one"] * 6, ["x" * 31], [""], ["Design", "design"], ["Design", "Ｄｅｓｉｇｎ"], [123]):
            self.rejected({**posting(), "tags": tags})

    def test_plain_text_preserves_explicit_names_and_html_but_rejects_scores(self):
        text = '<script>alert(1)</script> is literal text. Ama typed her own name here. Contact ama@example.test if appropriate.'
        self.assertEqual(validate_posting({**posting(), "text": text})["text"], text)
        for field, value in (("text", "This is 90% suitable. We offer design. We seek a partner."), ("looking_for", "score: 9"), ("offering", "8/10"), ("tags", ["90 percent"])):
            self.rejected({**posting(), field: value})


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        self.db = Database(Path(self.temp.name) / "board.db", lambda: self.now)
        self.db.migrate()
        self.board = BoardService(self.db, lambda: self.now)
        with self.db.transaction() as connection:
            for owner in ("ama", "joon"):
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                   (owner, "private-google-" + owner, owner + "-private@example.test", "Private profile " + owner, storage_time(self.now), storage_time(self.now)))

    def status(self, expected, operation, *args, **kwargs):
        with self.assertRaises(BoardError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.status, expected)
        return caught.exception

    def publish(self, owner="ama", label="Prototype"):
        draft = self.board.save_draft(owner, posting(label))
        return self.board.publish(owner, draft["revision"], draft["payload_hash"])

    def count(self, table):
        with self.db.connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_empty_timestamp_is_actual_migration_time_and_drafts_do_not_change_public_state(self):
        initial = self.board.public_list()
        self.assertEqual(initial, {"schema_version": 1, "updated_at": storage_time(self.now), "page": 1, "page_size": 20, "total": 0, "has_next": False, "items": []})
        self.now += timedelta(days=1)
        self.assertEqual(self.board.public_list(), initial)
        self.assertIsNone(self.board.draft("ama"))
        self.assertIsNone(self.board.owner_posting("ama")["slug"])
        draft = self.board.save_draft("ama", posting())
        self.assertEqual(draft["revision"], 1)
        slug = self.board.owner_posting("ama")["slug"]
        self.assertRegex(slug, r"^[0-9a-f]{32}$")
        self.status(404, self.board.get_public, slug)
        self.assertEqual(self.board.public_list(), initial)
        self.assertEqual(self.count("board_publication_grants"), 0)

    def test_publishing_releases_only_exact_typed_fields_never_profile_data(self):
        public = self.publish()
        self.assertEqual(set(public), {"slug", "text", "tags", "looking_for", "offering", "published_at", "updated_at"})
        self.assertEqual({key: public[key] for key in posting()}, posting())
        self.assertEqual(self.board.get_public(public["slug"]), public)
        self.assertEqual(self.board.public_list()["items"], [public])
        encoded = json.dumps(self.board.public_list())
        for private in ("ama-private@example.test", "Private profile ama", "private-google-ama", "user_id", "payload_hash", "grant_id"):
            self.assertNotIn(private, encoded)
        with self.db.connect() as connection:
            grant = connection.execute("SELECT * FROM board_publication_grants").fetchone()
            self.assertEqual(grant["user_id"], "ama")
            self.assertEqual(grant["revision"], 1)
            self.assertEqual(grant["payload_hash"], payload_hash(posting()))

    def test_private_edit_keeps_last_public_version_until_fresh_approval(self):
        old = self.publish()
        changed = posting("Private unapproved plan")
        draft = self.board.save_draft("ama", changed)
        self.assertEqual(self.board.get_public(old["slug"]), old)
        self.assertEqual(self.board.public_list(query="unapproved")["total"], 0)
        self.assertTrue(self.board.owner_posting("ama")["has_unpublished_changes"])
        new = self.board.publish("ama", draft["revision"], draft["payload_hash"])
        self.assertEqual(new["slug"], old["slug"])
        self.assertEqual(new["text"], changed["text"])
        self.assertGreater(new["updated_at"], old["updated_at"])
        self.assertFalse(self.board.owner_posting("ama")["has_unpublished_changes"])
        self.assertEqual(self.count("board_publication_grants"), 2)

    def test_stale_wrong_hash_wrong_owner_and_same_payload_new_revision_refuse_old_preview(self):
        first = self.board.save_draft("ama", posting())
        self.board.save_draft("ama", posting())
        self.status(409, self.board.publish, "ama", first["revision"], first["payload_hash"])
        current = self.board.draft("ama")
        self.status(409, self.board.publish, "ama", current["revision"], "0" * 64)
        self.board.save_draft("joon", posting("Different owner"))
        self.status(409, self.board.publish, "joon", current["revision"], current["payload_hash"])
        self.assertEqual(self.board.public_list()["total"], 0)
        self.assertEqual(self.count("board_publication_grants"), 0)
        self.status(401, self.board.save_draft, "' OR 1=1 --", posting())

    def test_publish_retry_and_concurrent_retries_have_one_grant_and_stable_receipt(self):
        draft = self.board.save_draft("ama", posting())
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.board.publish("ama", draft["revision"], draft["payload_hash"]), range(5)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.count("board_publication_grants"), 1)
        updated = self.board.public_list()["updated_at"]
        self.now += timedelta(days=3)
        self.assertEqual(self.board.publish("ama", draft["revision"], draft["payload_hash"]), results[0])
        self.assertEqual(self.board.public_list()["updated_at"], updated)

    def test_failed_grant_insert_rolls_back_revocation_pointer_and_global_timestamp(self):
        old = self.publish()
        before = self.board.public_list()
        draft = self.board.save_draft("ama", posting("Another draft"))
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_board_grant BEFORE INSERT ON board_publication_grants BEGIN SELECT RAISE(ABORT,'fictional grant failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.board.publish("ama", draft["revision"], draft["payload_hash"])
        self.assertEqual(self.board.public_list(), before)
        self.assertEqual(self.board.get_public(old["slug"]), old)
        self.assertEqual(self.count("board_publication_grants"), 1)

    def test_unpublish_removes_immediately_returns_gone_and_prevents_old_preview_resurrection(self):
        public = self.publish()
        preview = self.board.draft("ama")
        before = self.board.public_list()["updated_at"]
        state = self.board.unpublish("ama")
        self.assertFalse(state["visible"])
        self.assertIsNone(state["published"])
        self.status(410, self.board.get_public, public["slug"])
        empty = self.board.public_list()
        self.assertEqual(empty["items"], [])
        self.assertGreater(empty["updated_at"], before)
        self.status(409, self.board.publish, "ama", preview["revision"], preview["payload_hash"])
        fresh = state["draft"]
        republished = self.board.publish("ama", fresh["revision"], fresh["payload_hash"])
        self.assertEqual(republished["slug"], public["slug"])

    def test_withdrawal_global_timestamp_advances_when_other_rows_remain_and_clock_regresses(self):
        first = self.publish("ama", "First")
        self.now += timedelta(days=1)
        other = self.publish("joon", "Second")
        before = self.board.public_list()["updated_at"]
        self.now -= timedelta(days=10)
        self.board.unpublish("ama")
        feed = self.board.public_list()
        self.assertGreater(feed["updated_at"], before)
        self.assertEqual(feed["items"], [other])
        self.status(410, self.board.get_public, first["slug"])

    def test_hidden_or_suspended_publications_are_filtered_at_use_and_cannot_be_republished(self):
        public = self.publish()
        draft = self.board.draft("ama")
        with self.db.transaction() as connection:
            connection.execute("UPDATE board_postings SET hidden=1 WHERE user_id=?", ("ama",))
        self.status(410, self.board.get_public, public["slug"])
        self.assertEqual(self.board.public_list()["total"], 0)
        self.status(403, self.board.publish, "ama", draft["revision"], draft["payload_hash"])
        self.assertTrue(self.board.owner_posting("ama")["hidden"])
        with self.db.transaction() as connection:
            connection.execute("UPDATE board_postings SET hidden=0 WHERE user_id=?", ("ama",))
            connection.execute("UPDATE users SET suspended_at=? WHERE id=?", (storage_time(self.now), "ama"))
        self.status(410, self.board.get_public, public["slug"])
        self.assertEqual(self.board.public_list()["total"], 0)
        self.status(403, self.board.publish, "ama", draft["revision"], draft["payload_hash"])
        self.status(403, self.board.save_draft, "ama", posting())

    def test_changed_stored_payload_and_wrong_owner_grant_fail_closed(self):
        first = self.publish("ama", "First")
        self.publish("joon", "Second")
        with self.db.transaction() as connection:
            connection.execute("UPDATE board_revisions SET payload_json=? WHERE user_id=?", (canonical_payload(posting("Unapproved replacement")).decode(), "ama"))
        self.status(410, self.board.get_public, first["slug"])
        self.assertEqual(self.board.public_list()["total"], 1)
        with self.db.transaction() as connection:
            other_grant = connection.execute("SELECT active_grant_id FROM board_postings WHERE user_id=?", ("joon",)).fetchone()[0]
            connection.execute("UPDATE board_postings SET active_grant_id=? WHERE user_id=?", (other_grant, "ama"))
        self.status(410, self.board.get_public, first["slug"])
        self.assertEqual(self.board.public_list()["total"], 1)

    def test_owner_exports_include_only_their_private_history_and_grants(self):
        self.publish("ama", "Ama approved text")
        self.board.save_draft("ama", posting("Ama private revision"))
        self.publish("joon", "Joon secret owner history")
        export = self.board.export_owner("ama")
        self.assertEqual(len(export["revisions"]), 2)
        self.assertEqual(len(export["publication_grants"]), 1)
        self.assertIn("Ama private revision", json.dumps(export))
        self.assertNotIn("Joon secret owner history", json.dumps(export))
        self.assertEqual(self.board.draft("joon")["payload"]["text"], posting("Joon secret owner history")["text"])

    def test_public_search_is_literal_casefolded_and_covers_only_published_payload(self):
        first = self.publish("ama", "Café makers")
        second = self.publish("joon", "A_literal marker")
        self.assertEqual(self.board.public_list(query="CAFÉ")["items"], [first])
        self.assertEqual(self.board.public_list(query="_")["items"], [second])
        self.assertEqual(self.board.public_list(query="% ")["total"], 0)
        self.assertEqual(self.board.public_list(query="' OR 1=1 --")["total"], 0)
        self.assertEqual(self.board.public_list(query="workshop")["total"], 2)
        self.assertEqual(self.board.public_list(query="Design")["total"], 2)

    def test_export_reuses_callers_snapshot_without_committing_it(self):
        self.publish()
        original = self.board.export_owner("ama")
        changed = storage_time(self.now + timedelta(days=1))
        with self.assertRaisesRegex(RuntimeError, "caller rollback"):
            with self.db.transaction() as connection:
                connection.execute("UPDATE board_revisions SET saved_at=? WHERE user_id=?", (changed, "ama"))
                exported = self.board.export_owner("ama", connection=connection)
                self.assertEqual(exported["revisions"][0]["saved_at"], changed)
                self.assertTrue(connection.in_transaction)
                raise RuntimeError("caller rollback")
        self.assertEqual(self.board.export_owner("ama"), original)

    def test_newest_first_pagination_is_bounded_and_deterministic(self):
        first = self.publish("ama", "First")
        second = self.publish("joon", "Second")
        page1 = self.board.public_list(page_size=1)
        self.assertEqual(page1["items"], [second])
        self.assertTrue(page1["has_next"])
        self.assertEqual(self.board.public_list(page=2, page_size=1)["items"], [first])
        self.assertFalse(self.board.public_list(page=2, page_size=1)["has_next"])
        self.assertEqual(self.board.public_list(page=3, page_size=1)["items"], [])
        with self.db.transaction() as connection:
            connection.execute("UPDATE board_postings SET updated_at=?", (storage_time(self.now),))
        self.assertEqual([item["slug"] for item in self.board.public_list()["items"]], sorted([first["slug"], second["slug"]]))
        for kwargs in ({"query": "x" * 101}, {"query": "bad\x00"}, {"page": 0}, {"page": True}, {"page": 10001}, {"page_size": 0}, {"page_size": 51}, {"page_size": 1.5}):
            self.status(422, self.board.public_list, **kwargs)

    def test_account_cascade_removes_owner_content_and_slug_without_tombstone(self):
        first = self.publish("ama", "Delete this owner")
        other = self.publish("joon", "Keep this owner")
        before = self.board.public_list()["updated_at"]
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM users WHERE id=?", ("ama",))
            touch_board(connection, lambda: self.now)
        self.status(404, self.board.get_public, first["slug"])
        feed = self.board.public_list()
        self.assertEqual(feed["items"], [other])
        self.assertGreater(feed["updated_at"], before)
        for table in ("board_postings", "board_revisions", "board_publication_grants"):
            self.assertEqual(self.count(table), 1)
        with self.db.connect() as connection:
            dump = "\n".join(connection.iterdump())
            self.assertNotIn("Delete this owner", dump)
            self.assertNotIn(first["slug"], dump)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
