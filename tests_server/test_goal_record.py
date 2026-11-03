"""Independent record oracles, tamper detection and concurrent sequence checks."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
import threading
import unittest

from connectthebots_server import goal_record
from connectthebots_server.agent_common import AgentError
from connectthebots_server.recovery_authority import local_state
from tests_server.test_goals import GoalFixture, fictional_charter


def hand_worked_record():
    """Encode the documented envelope by hand, independently of production JSON.

    The literal JSON key order, separators and ASCII content below are the
    oracle. No production canonicalizer/digest or captured output is used.
    """
    goal, now = "fictional-tidal-clock", "2026-11-01T12:00:00.000000Z"
    contents = (
        ({"title": "Fictional: tidal clock puzzle", "aim": "Fictional clock puzzle."},
         '{"aim":"Fictional clock puzzle.","title":"Fictional: tidal clock puzzle"}'),
        ({"task": "t_fictional", "change": "add task"},
         '{"change":"add task","task":"t_fictional"}'),
        ({"reason": "Fictional review complete.", "action": "approve"},
         '{"action":"approve","reason":"Fictional review complete."}'),
    )
    entries, previous = [], "0" * 64
    for seq, (kind, author, item) in enumerate(zip(
            ("charter", "step", "operator"), ("c_1234abcd", "c_1234abcd", "operator"),
            contents), 1):
        content, content_json = item
        content_hash = sha256(content_json.encode("ascii")).hexdigest()
        refs = [entries[0]["id"]] if seq == 2 else []
        refs_json = '["' + entries[0]["id"] + '"]' if seq == 2 else "[]"
        envelope_json = (
            '{"author":"' + author + '","content_sha256":"' + content_hash
            + '","created_at":"' + now + '","goal":"' + goal
            + '","kind":"' + kind + '","previous":"' + previous
            + '","refs":' + refs_json + ',"seq":' + str(seq) + '}')
        entry_hash = sha256(envelope_json.encode("ascii")).hexdigest()
        entries.append({"id": "e_" + entry_hash, "goal": goal, "seq": seq,
                        "kind": kind, "author": author, "created_at": now, "refs": refs,
                        "content": content, "content_sha256": content_hash,
                        "previous": previous, "entry_sha256": entry_hash, "status": "visible"})
        previous = entry_hash
    return entries


class RecordOracleTests(unittest.TestCase):
    def test_fixed_three_entry_chain_matches_independent_literal_json_oracle(self):
        entries = hand_worked_record()
        result = goal_record.verify_record(entries, expected_goal="fictional-tidal-clock")
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["entries"], 3)
        self.assertIsNone(result["first_broken"])
        for entry in entries:
            # Compare the production digest against a hand-encoded envelope,
            # not against the production encoder applied twice.
            envelope = {key: entry[key] for key in
                        ("goal", "seq", "kind", "author", "created_at", "refs", "content_sha256", "previous")}
            self.assertEqual(goal_record.digest(envelope), entry["entry_sha256"])

    def test_writer_matches_all_three_hand_encoded_fixture_envelopes(self):
        # A narrow independent table isolates the record writer from charter
        # normalization. The service/concurrency tests use the real migration.
        expected = hand_worked_record()
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE goal_entries(id TEXT,goal_id INTEGER,seq INTEGER,kind TEXT,"
            "author_user_id TEXT,author_handle TEXT,session_id TEXT,refs_json TEXT,content_json TEXT,"
            "content_sha256 TEXT,previous_sha256 TEXT,entry_sha256 TEXT,status TEXT,created_at TEXT)")
        from datetime import datetime, timezone
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        connection.execute("BEGIN IMMEDIATE")
        actual = [goal_record.append_entry(connection, {"id": 1, "slug": entry["goal"]},
                    entry["kind"], entry["author"], entry["content"], now, refs=entry["refs"])
                  for entry in expected]
        connection.commit()
        self.assertEqual(actual, expected)

    def test_every_immutable_envelope_field_and_content_are_authenticated(self):
        changes = {"id": "e_" + "a" * 64, "goal": "different-fictional-goal", "seq": 9, "kind": "finding",
                   "author": "c_ffffffff", "created_at": "2026-11-02T12:00:00.000000Z",
                   "refs": ["e_different"], "content_sha256": "1" * 64,
                   "previous": "2" * 64, "entry_sha256": "3" * 64,
                   "content": {"change": "silently altered task", "task": "t_fictional"}}
        for field, replacement in changes.items():
            with self.subTest(field=field):
                entries = hand_worked_record()
                entries[1][field] = replacement
                result = goal_record.verify_record(entries)
                self.assertFalse(result["valid"], field)
                self.assertEqual(result["first_broken"], 2, result)

    def test_missing_reordered_and_duplicate_sequences_and_wrong_goal_fail(self):
        original = hand_worked_record()
        for entries in (original[1:], [original[0], original[2]],
                        [original[0], original[1], original[1]],
                        [original[1], original[0], original[2]]):
            with self.subTest(sequences=[row["seq"] for row in entries]):
                self.assertFalse(goal_record.verify_record(entries)["valid"])
        self.assertFalse(goal_record.verify_record(original, expected_goal="wrong-fictional-goal")["valid"])

    def test_tombstone_preserves_verifiable_envelope_without_recovering_content(self):
        entries = hand_worked_record()
        removed_hash, removed_entry_hash = entries[1]["content_sha256"], entries[1]["entry_sha256"]
        entries[1]["content"], entries[1]["status"] = None, "withdrawn"
        result = goal_record.verify_record(entries)
        self.assertTrue(result["valid"], result)
        self.assertEqual(entries[1]["content_sha256"], removed_hash)
        self.assertEqual(entries[1]["entry_sha256"], removed_entry_hash)

    def test_ascii_canonicalization_does_not_silently_change_unicode_or_key_order(self):
        value = {"z": "fictional caf\u00e9", "a": [1, True, None]}
        expected = '{"a":[1,true,null],"z":"fictional caf\\u00e9"}'
        self.assertEqual(goal_record.canonical_json(value), expected)
        self.assertEqual(goal_record.digest(value), sha256(expected.encode("ascii")).hexdigest())


class StoredRecordTests(GoalFixture):
    def test_accepted_charter_revision_is_appended_and_existing_entries_unchanged(self):
        goal = self.open_goal()
        before = self.goals.record(None, goal["slug"])["entries"]
        revision = fictional_charter()
        revision["aim"] = "A revised reproducible solution for the fictional tidal clock puzzle."
        self.goals.revise_charter("lead", goal["slug"], revision, True)
        after = self.goals.record(None, goal["slug"])["entries"]
        self.assertEqual(after[:len(before)], before)
        self.assertGreater(len(after), len(before))
        self.assertTrue(goal_record.verify_record(after, expected_goal=goal["slug"])["valid"])
        self.assertEqual(self.goals.detail(None, goal["slug"])["goal"]["charter"]["aim"], revision["aim"])

    def test_two_concurrent_writers_cannot_create_sequence_gaps_or_forks(self):
        goal = self.open_goal()
        barrier = threading.Barrier(2)
        def write(label):
            barrier.wait(timeout=10)
            for index in range(8):
                with self.db.transaction() as connection:
                    row = connection.execute("SELECT * FROM goals WHERE slug=?", (goal["slug"],)).fetchone()
                    goal_record.append_entry(connection, row, "step", "c_1234abcd",
                        {"change": "Fictional concurrent note", "writer": label, "item": index}, self.now,
                        author_user_id="lead")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(write, label) for label in ("one", "two")]
            for future in futures:
                future.result(timeout=30)
        entries = self.goals.record(None, goal["slug"])["entries"]
        self.assertEqual([row["seq"] for row in entries], list(range(1, len(entries) + 1)))
        self.assertEqual(len(entries), len({row["entry_sha256"] for row in entries}))
        self.assertEqual(sum(row["content"].get("change") == "Fictional concurrent note"
                             for row in entries if row["content"]), 16)
        self.assertTrue(goal_record.verify_record(entries, expected_goal=goal["slug"])["valid"])

    def test_record_pagination_and_input_validation_are_bounded(self):
        goal = self.open_goal()
        entries = self.goals.record(None, goal["slug"])["entries"]
        self.assertGreaterEqual(len(entries), 2)
        one = self.goals.record(None, goal["slug"], after=0, limit=1)["entries"]
        two = self.goals.record(None, goal["slug"], after=one[-1]["seq"], limit=1)["entries"]
        self.assertEqual(one + two, entries[:2])
        for options in ({"after": -1}, {"after": True}, {"after": 2**63}, {"after": 2**10000},
                        {"limit": 0}, {"limit": 1001}, {"limit": True}):
            with self.subTest(options=options), self.assertRaises(AgentError):
                self.goals.record(None, goal["slug"], **options)
        self.assertEqual(self.goals.record(None, goal["slug"], after=2**63 - 1)["entries"], [])

    def test_append_only_history_rejects_envelope_changes_but_allows_content_tombstone(self):
        goal = self.open_goal()
        entry = self.goals.record(None, goal["slug"])["entries"][0]
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as connection:
                connection.execute("UPDATE goal_entries SET author_handle='c_ffffffff' WHERE id=?", (entry["id"],))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as connection:
                connection.execute("DELETE FROM goal_entries WHERE id=?", (entry["id"],))
        # Erasure retains the original digest; it does not rewrite the envelope.
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET content_json=NULL,status='withdrawn' WHERE id=?", (entry["id"],))
        entries = self.goals.record(None, goal["slug"])["entries"]
        self.assertIsNone(entries[0]["content"])
        self.assertEqual(entries[0]["entry_sha256"], entry["entry_sha256"])
        self.assertTrue(goal_record.verify_record(entries)["valid"])

    def test_whole_ordinary_record_write_transaction_has_no_recovery_advance(self):
        goal = self.open_goal()
        with self.db.connect() as connection:
            before = local_state(connection)
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM goals WHERE slug=?", (goal["slug"],)).fetchone()
            for index in range(3):
                goal_record.append_entry(connection, row, "step", "c_1234abcd",
                    {"change": "Fictional ordinary work", "item": index}, self.now, author_user_id="lead")
        with self.db.connect() as connection:
            after = local_state(connection)
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["growth_generation"], before["growth_generation"])


if __name__ == "__main__":
    unittest.main()
