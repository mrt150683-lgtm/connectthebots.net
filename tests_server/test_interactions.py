"""Hosted consent transitions, use-time authority, quotas and shared-data erasure."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from connectthebots_server.board import BoardError, BoardService
from connectthebots_server.db import Database
from connectthebots_server.interactions import InteractionError, InteractionService
from connectthebots_server.sessions import random_token, storage_time


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        self.db = Database(Path(self.temp.name) / "interactions.db", lambda: self.now)
        self.db.migrate()
        self.board = BoardService(self.db, lambda: self.now)
        self.service = InteractionService(self.db, lambda: self.now, self.board)
        self.postings = {}
        for owner in ("ama", "joon", "cora"):
            self.add_owner(owner)

    def add_owner(self, owner):
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                (owner, "private-sub-" + owner, owner + "@private.example.test", "Private Profile " + owner, storage_time(self.now), storage_time(self.now)))
        payload = {"text": f"This is the fictional {owner} project. We can offer a small prototype. We seek thoughtful testing.", "tags": ["prototypes"], "looking_for": "Thoughtful testing", "offering": "A small prototype"}
        draft = self.board.save_draft(owner, payload)
        self.postings[owner] = self.board.publish(owner, draft["revision"], draft["payload_hash"])

    def status(self, expected, operation, *args, **kwargs):
        with self.assertRaises(InteractionError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.status, expected)
        return caught.exception

    def count(self, table):
        with self.db.connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def prepare(self, requester="ama", recipient="joon", note="Could we discuss a small prototype?", reason="Our published projects could share a test."):
        return self.service.prepare_request(requester, self.postings[recipient]["slug"], note, reason)

    def request(self, requester="ama", recipient="joon"):
        return self.service.request(requester, self.prepare(requester, recipient), random_token())

    def conversation(self, requester="ama", recipient="joon"):
        request = self.request(requester, recipient)
        result = self.service.accept(recipient, request["id"], request["context_hash"])
        return request, result["conversation"]

    def suspend(self, owner, active=True):
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id=?", (storage_time(self.now) if active else None, owner))

    def erase(self, owner):
        with self.db.transaction() as connection:
            self.service.erase_owner(owner, connection=connection)
            connection.execute("DELETE FROM users WHERE id=?", (owner,))

    def test_preview_is_read_only_contains_only_explicit_postings_note_reason(self):
        preview = self.prepare()
        self.assertEqual(set(preview), {"target_slug", "requester_posting", "recipient_posting", "note", "reason", "approval_hash"})
        self.assertEqual(preview["requester_posting"], self.postings["ama"])
        self.assertEqual(preview["recipient_posting"], self.postings["joon"])
        self.assertEqual(self.count("introductions"), 0)
        self.assertEqual(self.count("conversations"), 0)
        for private in ("@private.example.test", "Private Profile", "private-sub-", "requester_id", "recipient_id", "grant_id"):
            self.assertNotIn(private, json.dumps(preview))

    def test_self_unpublished_suspended_and_blocked_requests_are_refused(self):
        self.status(404, self.prepare, "ama", "ama")
        self.board.unpublish("ama")
        self.status(409, self.prepare)
        draft = self.board.draft("ama")
        self.board.publish("ama", draft["revision"], draft["payload_hash"])
        self.suspend("joon")
        self.status(404, self.prepare)
        self.suspend("joon", False)
        self.service.block_posting("joon", self.postings["ama"]["slug"])
        self.status(404, self.prepare)

    def test_request_is_requester_consent_but_never_creates_conversation(self):
        request = self.request()
        self.assertEqual(request["state"], "requested")
        self.assertEqual(request["direction"], "outgoing")
        self.assertIsNone(request["conversation_id"])
        self.assertEqual(self.count("conversations"), 0)
        incoming = self.service.get_introduction("joon", request["id"])
        self.assertEqual(incoming["direction"], "incoming")
        self.assertIn("accept", incoming["actions"])
        self.assertEqual(self.service.list_introductions("joon")["totals"]["pending_incoming"], 1)
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM introductions").fetchone()
            self.assertIsNotNone(row["requester_accepted_at"])
            self.assertIsNone(row["recipient_accepted_at"])

    def test_only_recipient_acceptance_opens_one_conversation_with_same_context(self):
        request = self.request()
        self.status(403, self.service.accept, "ama", request["id"], request["context_hash"])
        self.status(404, self.service.accept, "cora", request["id"], request["context_hash"])
        self.status(409, self.service.accept, "joon", request["id"], "0" * 64)
        self.assertEqual(self.count("conversations"), 0)
        accepted = self.service.accept("joon", request["id"], request["context_hash"])
        repeated = self.service.accept("joon", request["id"], request["context_hash"])
        self.assertEqual(accepted, repeated)
        self.assertEqual(accepted["introduction"]["state"], "accepted")
        self.assertEqual(self.count("conversations"), 1)

    def test_database_guard_refuses_conversation_without_mutual_acceptance(self):
        request = self.request()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as connection:
                connection.execute("INSERT INTO conversations(id,introduction_id,requester_id,recipient_id,opened_at) VALUES(?,?,?,?,?)", (random_token(), request["id"], "ama", "joon", storage_time(self.now)))
        self.assertEqual(self.count("conversations"), 0)

    def test_expiry_at_exact_fourteen_days_commits_even_when_accept_is_refused(self):
        request = self.request()
        self.now += timedelta(days=14)
        self.status(410, self.service.accept, "joon", request["id"], request["context_hash"])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT status FROM introductions").fetchone()[0], "expired")
        expired = self.service.get_introduction("ama", request["id"])
        self.assertEqual(expired["state"], "expired")
        self.assertIsNone(expired["recipient_posting"])
        self.assertIsNone(expired["note"])
        self.assertEqual(self.count("conversations"), 0)

    def test_current_publication_change_invalidates_pending_context_read_retry_and_accept(self):
        preview, nonce = self.prepare(), random_token()
        request = self.service.request("ama", preview, nonce)
        draft = self.board.save_draft("joon", {**self.board.draft("joon")["payload"], "offering": "A changed public offer"})
        # Private edits alone do not change the actual shared publication.
        self.assertEqual(self.service.get_introduction("ama", request["id"])["state"], "requested")
        self.board.publish("joon", draft["revision"], draft["payload_hash"])
        replay = self.service.request("ama", preview, nonce)
        self.assertEqual(replay["state"], "withdrawn")
        for field in ("requester_posting", "recipient_posting", "note", "reason", "context_hash"):
            self.assertIsNone(replay[field])
        self.assertIsNone(self.service.list_introductions("ama")["history"][0]["recipient_posting"])
        self.status(410, self.service.accept, "joon", request["id"], request["context_hash"])
        self.assertEqual(self.count("conversations"), 0)

    def test_revoked_then_republished_same_words_do_not_reactivate_old_preview(self):
        preview = self.prepare()
        self.board.unpublish("joon")
        fresh = self.board.draft("joon")
        self.board.publish("joon", fresh["revision"], fresh["payload_hash"])
        self.status(409, self.service.request, "ama", preview, random_token())
        self.assertEqual(self.count("introductions"), 0)

    def test_request_nonce_replays_once_survives_restart_and_conflicts_on_changed_input(self):
        preview, nonce = self.prepare(), random_token()
        first = self.service.request("ama", preview, nonce)
        restarted = InteractionService(self.db, lambda: self.now, self.board)
        self.assertEqual(restarted.request("ama", preview, nonce), first)
        changed = {**preview, "note": "A different note"}
        self.status(409, self.service.request, "ama", changed, nonce)
        self.assertEqual(self.count("introductions"), 1)
        self.assertEqual(self.count("intro_receipts"), 1)

    def test_duplicate_and_reciprocal_pending_requests_never_auto_accept(self):
        request = self.request()
        self.status(409, self.request)
        self.status(409, self.request, "joon", "ama")
        self.assertEqual(self.count("introductions"), 1)
        self.assertEqual(self.count("conversations"), 0)
        self.assertEqual(self.service.get_introduction("ama", request["id"])["state"], "requested")

    def test_decline_withdraw_and_invalid_actor_transitions(self):
        first = self.request()
        self.status(403, self.service.decline, "ama", first["id"])
        self.status(403, self.service.withdraw, "joon", first["id"])
        self.assertEqual(self.service.decline("joon", first["id"])["state"], "declined")
        self.assertEqual(self.service.decline("joon", first["id"])["state"], "declined")
        self.status(410, self.service.accept, "joon", first["id"], first["context_hash"])
        second = self.request()
        self.assertEqual(self.service.withdraw("ama", second["id"])["state"], "withdrawn")
        self.status(410, self.service.accept, "joon", second["id"], second["context_hash"])

    def test_concurrent_request_and_accept_retries_create_one_of_each(self):
        preview, nonce = self.prepare(), random_token()
        with ThreadPoolExecutor(max_workers=5) as pool:
            requested = list(pool.map(lambda _: self.service.request("ama", preview, nonce), range(5)))
        self.assertTrue(all(row == requested[0] for row in requested))
        row = requested[0]
        with ThreadPoolExecutor(max_workers=5) as pool:
            accepted = list(pool.map(lambda _: self.service.accept("joon", row["id"], row["context_hash"]), range(5)))
        self.assertTrue(all(item == accepted[0] for item in accepted))
        self.assertEqual((self.count("introductions"), self.count("conversations")), (1, 1))

    def test_failed_conversation_insert_rolls_back_recipient_acceptance(self):
        request = self.request()
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_conversation BEFORE INSERT ON conversations BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.accept("joon", request["id"], request["context_hash"])
        self.assertEqual(self.service.get_introduction("ama", request["id"])["state"], "requested")
        self.assertEqual(self.count("conversations"), 0)
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT recipient_accepted_at FROM introductions").fetchone()[0])

    def test_five_pending_limit_and_withdrawal_frees_a_slot(self):
        requests = []
        for number in range(6):
            peer = "extra" + str(number)
            self.add_owner(peer)
            if number < 5:
                requests.append(self.request("ama", peer))
        self.status(429, self.request, "ama", "extra5")
        self.service.withdraw("ama", requests[0]["id"])
        self.request("ama", "extra5")
        self.assertEqual(self.service.list_introductions("ama")["totals"]["pending_outgoing"], 5)

    def test_twenty_request_week_is_rolling_and_declining_does_not_reset_it(self):
        origin = self.now
        for number in range(20):
            self.now = origin + timedelta(minutes=number)
            request = self.request()
            self.service.decline("joon", request["id"])
        self.now = origin + timedelta(days=6)
        error = self.status(429, self.request)
        self.assertEqual(error.retry_after, 86400)
        self.now = origin + timedelta(days=7)
        self.request()
        self.assertEqual(self.count("introductions"), 21)

    def test_blocks_are_symmetric_for_board_private_freshness_and_silent_to_peer(self):
        request = self.request()
        public_before = self.board.public_list()
        private_before = self.board.public_list(viewer_id="ama")
        blocked = self.service.block_introduction("joon", request["id"])
        self.assertEqual(blocked["state"], "blocked")
        peer = self.service.get_introduction("ama", request["id"])
        self.assertEqual(peer["state"], "unavailable")
        self.assertIsNone(peer["recipient_posting"])
        self.assertNotIn("block", peer["actions"])
        self.assertEqual(self.board.public_list(), public_before)
        self.assertGreater(self.board.public_list(viewer_id="ama")["updated_at"], private_before["updated_at"])
        for viewer, target in (("ama", "joon"), ("joon", "ama")):
            self.assertEqual(self.board.public_list(viewer_id=viewer)["total"], 2)
            with self.assertRaises(BoardError) as caught:
                self.board.get_public(self.postings[target]["slug"], viewer_id=viewer)
            self.assertEqual(caught.exception.status, 404)
            self.status(404, self.prepare, viewer, target)
        self.assertEqual(self.count("email_outbox"), 0)

    def test_authorized_object_can_block_temporarily_suspended_peer(self):
        request, conversation = self.conversation()
        self.suspend("joon")
        self.assertEqual(self.service.block_conversation("ama", conversation["id"])["state"], "closed")
        self.suspend("joon", False)
        self.status(404, self.prepare, "joon", "ama")
        self.assertEqual(self.count("user_blocks"), 1)

    def test_conversation_consent_is_separate_from_later_publication_changes(self):
        request, conversation = self.conversation()
        self.board.unpublish("ama")
        self.board.unpublish("joon")
        self.assertEqual(self.service.get_conversation("ama", conversation["id"])["state"], "open")
        self.service.send_message("ama", conversation["id"], "Our agreed conversation can continue.", random_token())
        self.assertIsNotNone(self.service.get_introduction("joon", request["id"])["requester_posting"])

    def test_nonparticipant_cannot_view_send_close_block_or_use_message_cursor(self):
        _, conversation = self.conversation()
        for operation, args in ((self.service.get_conversation, (conversation["id"],)),
                                (self.service.send_message, (conversation["id"], "Forged message", random_token())),
                                (self.service.close_conversation, (conversation["id"],)),
                                (self.service.block_conversation, (conversation["id"],))):
            self.status(404, operation, "cora", *args)
        self.assertEqual(self.count("messages"), 0)

    def test_valid_message_whitespace_stored_as_entered_and_scores_are_rejected(self):
        _, conversation = self.conversation()
        text = "  Hello <script>alert(1)</script>.\nA second line.\t  "
        message = self.service.send_message("ama", conversation["id"], text, random_token())
        self.assertEqual(message["text"], text)
        self.assertEqual(message["author"], "you")
        peer = self.service.get_conversation("joon", conversation["id"])["messages"][0]
        self.assertEqual((peer["text"], peer["author"]), (text, "other"))
        for text in ("90% compatible", "score: 9", "", " " * 5, "x" * 4001, "bad\x00", "\ud800"):
            self.status(422, self.service.send_message, "ama", conversation["id"], text, random_token())
        for note, reason in (("90%", "Reason"), ("Note", "score: 9"), ("n" * 601, "Reason"), ("Note", "r" * 301)):
            self.status(422, self.prepare, "ama", "cora", note, reason)
        self.assertEqual(self.count("messages"), 1)

    def test_messages_exact_replay_conflict_and_concurrent_once(self):
        _, conversation = self.conversation()
        nonce = random_token()
        with ThreadPoolExecutor(max_workers=5) as pool:
            sent = list(pool.map(lambda _: self.service.send_message("ama", conversation["id"], "One exact message", nonce), range(5)))
        self.assertTrue(all(message == sent[0] for message in sent))
        self.status(409, self.service.send_message, "ama", conversation["id"], "Different message", nonce)
        self.assertEqual((self.count("messages"), self.count("message_receipts")), (1, 1))

    def test_message_receipt_failure_rolls_back_message(self):
        _, conversation = self.conversation()
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_message_receipt BEFORE INSERT ON message_receipts BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.send_message("ama", conversation["id"], "A message", random_token())
        self.assertEqual(self.count("messages"), 0)

    def test_message_limit_is_rolling_per_sender_across_conversations(self):
        _, first = self.conversation()
        _, second = self.conversation("ama", "cora")
        nonce = random_token()
        initial = self.service.send_message("ama", first["id"], "First message", nonce)
        for number in range(99):
            self.service.send_message("ama", first["id"] if number % 2 else second["id"], "Message " + str(number), random_token())
        self.assertEqual(self.service.send_message("ama", first["id"], "First message", nonce), initial)
        self.assertEqual(self.status(429, self.service.send_message, "ama", first["id"], "Next message", random_token()).retry_after, 86400)
        self.service.send_message("joon", first["id"], "Separate sender allowance", random_token())
        self.now += timedelta(days=1)
        self.service.send_message("ama", first["id"], "Next day", random_token())

    def test_close_block_or_suspension_prevents_further_send_and_closed_never_reopens(self):
        request, conversation = self.conversation()
        self.service.close_conversation("joon", conversation["id"])
        self.status(409, self.service.send_message, "ama", conversation["id"], "After closure", random_token())
        self.assertEqual(self.service.accept("joon", request["id"], request["context_hash"])["conversation"]["state"], "closed")
        _, next_conversation = self.conversation()
        self.suspend("joon")
        self.status(409, self.service.send_message, "ama", next_conversation["id"], "After suspension", random_token())
        self.assertEqual(self.service.get_conversation("ama", next_conversation["id"])["state"], "closed")
        self.suspend("joon", False)
        self.assertEqual(self.service.get_conversation("ama", next_conversation["id"])["state"], "closed")

    def test_message_cursor_and_lists_are_bounded_ordered_and_owner_checked(self):
        _, conversation = self.conversation()
        sent = [self.service.send_message("ama", conversation["id"], "Message " + str(index), random_token()) for index in range(51)]
        newest = self.service.get_conversation("joon", conversation["id"])
        self.assertEqual([row["id"] for row in newest["messages"]], [row["id"] for row in sent[1:]])
        self.assertTrue(newest["has_more"])
        older = self.service.get_conversation("joon", conversation["id"], before=newest["messages"][0]["id"])
        self.assertEqual([row["id"] for row in older["messages"]], [sent[0]["id"]])
        self.assertFalse(older["has_more"])
        _, other = self.conversation("ama", "cora")
        self.status(404, self.service.get_conversation, "ama", other["id"], before=sent[0]["id"])
        listing = self.service.list_conversations("ama", limit=1)
        self.assertEqual((listing["total"], len(listing["items"]), listing["has_next"]), (2, 1, True))
        self.assertEqual(self.service.list_conversations("ama", limit=1, page=2)["items"][0]["id"], conversation["id"])
        self.status(422, self.service.list_conversations, "ama", limit=51)
        self.status(422, self.service.list_introductions, "ama", page=0)

    def test_account_erasure_scrubs_all_intro_context_and_only_deleted_author_messages(self):
        request, conversation = self.conversation()
        self.service.send_message("ama", conversation["id"], "Deleted author content", random_token())
        self.service.send_message("joon", conversation["id"], "Survivor own content", random_token())
        deleted_slug = self.postings["ama"]["slug"]
        self.erase("ama")
        view = self.service.get_conversation("joon", conversation["id"])
        self.assertEqual(view["state"], "closed")
        self.assertEqual([item["text"] for item in view["messages"]], ["[deleted]", "Survivor own content"])
        self.assertEqual(view["messages"][0]["author"], "deleted")
        intro = self.service.get_introduction("joon", request["id"])
        self.assertIsNone(intro["requester_posting"])
        self.assertIsNone(intro["recipient_posting"])
        exported = self.service.export_owner("joon")
        self.assertEqual([row["text"] for row in exported["messages"]], ["Survivor own content"])
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM introductions").fetchone()
            for field in ("requester_id", "requester_grant_id", "recipient_grant_id", "requester_snapshot_json", "recipient_snapshot_json", "note", "reason", "context_hash"):
                self.assertIsNone(row[field])
            dump = "\n".join(connection.iterdump())
            self.assertNotIn(deleted_slug, dump)
            self.assertNotIn("Deleted author content", dump)
            self.assertNotIn("ama@private.example.test", dump)
        self.assertEqual(self.count("intro_receipts"), 0)
        self.erase("joon")
        for table in ("introductions", "conversations", "messages", "message_receipts"):
            self.assertEqual(self.count(table), 0)

    def test_erase_and_snapshot_export_share_callers_transaction_and_can_rollback(self):
        _, conversation = self.conversation()
        self.service.send_message("ama", conversation["id"], "Must survive caller rollback", random_token())
        original = self.service.export_owner("ama")
        with self.assertRaisesRegex(RuntimeError, "caller rollback"):
            with self.db.transaction() as connection:
                self.service.erase_owner("ama", connection=connection)
                connection.execute("DELETE FROM users WHERE id=?", ("ama",))
                survivor = self.service.export_owner("joon", connection=connection)
                self.assertEqual(survivor["conversations"][0]["state"], "closed")
                raise RuntimeError("caller rollback")
        self.assertEqual(self.service.export_owner("ama"), original)

    def test_terminal_export_preserves_only_owners_authored_context(self):
        request = self.request()
        self.service.decline("joon", request["id"])
        outgoing = self.service.export_owner("ama")["introductions"][0]
        incoming = self.service.export_owner("joon")["introductions"][0]
        self.assertEqual(outgoing["own_posting_snapshot"], self.postings["ama"])
        self.assertEqual(outgoing["own_note"], request["note"])
        self.assertEqual(outgoing["own_reason"], request["reason"])
        self.assertEqual(incoming["own_posting_snapshot"], self.postings["joon"])
        self.assertIsNone(incoming["own_note"])
        self.assertIsNone(incoming["own_reason"])
        for exported in (outgoing, incoming):
            for key in ("requester_posting", "recipient_posting", "note", "reason", "context_hash"):
                self.assertIsNone(exported[key])
        self.assertNotIn(self.postings["joon"]["slug"], json.dumps(outgoing))
        self.assertNotIn(self.postings["ama"]["slug"], json.dumps(incoming))
        self.assertNotIn("own_posting_snapshot", self.service.get_introduction("ama", request["id"]))
        self.erase("ama")
        survivor = self.service.export_owner("joon")["introductions"][0]
        self.assertIsNone(survivor["own_posting_snapshot"])
        self.assertIsNone(survivor["own_note"])

    def test_export_only_own_blocks_without_profile_or_private_identity(self):
        self.service.block_posting("ama", self.postings["joon"]["slug"])
        own = self.service.export_owner("ama")["blocks"]
        self.assertEqual(own, [{"created_at": storage_time(self.now), "blocked_posting_slug": self.postings["joon"]["slug"]}])
        self.assertEqual(self.service.export_owner("joon")["blocks"], [])
        self.assertEqual(self.service.export_owner("cora")["blocks"], [])
        self.board.unpublish("joon")
        self.assertEqual(self.service.export_owner("ama")["blocks"], own)
        self.erase("joon")
        self.assertEqual(self.service.export_owner("ama")["blocks"], [])

    def test_concurrent_new_requests_cannot_overrun_pending_quota(self):
        for number in range(6):
            self.add_owner("extra" + str(number))
            if number < 4:
                self.request("ama", "extra" + str(number))
        def attempt(peer):
            try:
                self.request("ama", peer)
                return 201
            except InteractionError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ("extra4", "extra5")))
        self.assertEqual(sorted(outcomes), [201, 429])
        self.assertEqual(self.service.list_introductions("ama")["totals"]["pending_outgoing"], 5)
        self.assertEqual(self.count("intro_receipts"), 5)


if __name__ == "__main__":
    unittest.main()
