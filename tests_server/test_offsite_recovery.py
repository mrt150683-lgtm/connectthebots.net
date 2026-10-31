"""Independent authority, real protected mutations and lost-host restoration.

The storage seam is an append-only in-memory provider. It never contacts B2.
The separate transport tests exercise the actual HTTP protocol.
"""
import base64
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from connectthebots_server.b2_storage import B2StorageError
from connectthebots_server.db import Database
from connectthebots_server.config import ConfigurationError, Settings
from connectthebots_server.lifecycle import AccountLifecycle
from connectthebots_server.recovery import RecoveryError, RecoveryService, advance_erasure, recovery_held
from connectthebots_server.recovery_authority import AuthorityError, RecoveryAuthority, canonical, local_state
from connectthebots_server.sessions import storage_time
from tests_server.test_operator import OperatorFixture


class MemoryStorage:
    def __init__(self):
        self.files = []
        self.unavailable = False
        self.timeout_after_write = False

    def list_versions(self, prefix):
        if self.unavailable:
            raise B2StorageError("Storage request failed.")
        return [{k: v for k, v in item.items() if k != "data"} for item in self.files if item["name"].startswith(prefix)]

    def read(self, file_id, max_bytes):
        if self.unavailable:
            raise B2StorageError("Storage request failed.")
        data = next(item["data"] for item in self.files if item["id"] == file_id)
        if len(data) > max_bytes:
            raise B2StorageError("Storage request failed.")
        return data

    def write(self, name, data, *, metadata=None):
        if self.unavailable:
            raise B2StorageError("Storage request failed.")
        file_id = str(len(self.files) + 1)
        self.files.append({"name": name, "data": data, "id": file_id, "action": "upload", "length": len(data), "metadata": metadata or {}})
        if self.timeout_after_write:
            raise B2StorageError("Storage request failed.")
        return file_id


class FileStorage(MemoryStorage):
    """Private fictional provider persisted across a deliberately killed process."""
    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self.files = [{**item, "data": base64.b64decode(item["data"])} for item in json.loads(self.path.read_text())]

    def write(self, name, data, *, metadata=None):
        file_id = super().write(name, data, metadata=metadata)
        encoded = [{**item, "data": base64.b64encode(item["data"]).decode()} for item in self.files]
        with self.path.open("w") as output:
            json.dump(encoded, output)
            output.flush()
            os.fsync(output.fileno())
        return file_id


def crash_process():
    database_path, storage_path, point = sys.argv[1:]
    storage = FileStorage(storage_path)
    authority = RecoveryAuthority(storage, b"f" * 32, "ctb-" + "a" * 32 + "/")
    database = Database(database_path, lambda: datetime(2026, 10, 30, 12, tzinfo=timezone.utc), recovery_authority=authority)
    append = authority._append
    def stop(value):
        if point == "before_remote":
            os._exit(17)
        append(value)
        if point == "after_remote":
            os._exit(18)
    authority._append = stop
    with database.transaction() as connection:
        connection.execute("UPDATE users SET suspended_at=? WHERE id='one'", ("2026-10-30T12:00:00Z",))
    os._exit(19)


class OffsiteFixture(OperatorFixture):
    def setUp(self):
        super().setUp()
        self.settings = replace(self.settings, backup_encryption_key=base64.urlsafe_b64encode(b"f" * 32).decode())
        self.storage = MemoryStorage()
        self.authority = RecoveryAuthority(self.storage, b"f" * 32, "ctb-" + "a" * 32 + "/")
        self.authority.enroll(self.db)
        self.recovery = RecoveryService(self.db, self.clock, self.settings)
        self.lifecycle = AccountLifecycle(self.db, self.clock, self.board, self.interactions, self.notifications, self.moderation)

    def authority_state(self):
        with self.db.connect() as connection:
            return local_state(connection)

    def generation(self):
        return self.authority_state()["generation"]


class IndependentAuthorityTests(OffsiteFixture):
    def test_partial_offsite_configuration_and_nonrandom_prefix_refused(self):
        env = {"APP_ENV": "staging", "DATABASE_PATH": str(self.db.path), "SESSION_SECRET": "fictional-secret-" + "s" * 32,
               "BACKUP_ENCRYPTION_KEY": self.settings.backup_encryption_key,
               "OFFSITE_B2_KEY_ID": "fictional-key", "OFFSITE_B2_APPLICATION_KEY": "fictional-application-secret",
               "OFFSITE_B2_BUCKET_ID": "fictional-bucket", "OFFSITE_B2_PREFIX": "ctb-" + "a" * 32 + "/"}
        settings = Settings.from_env(env)
        self.assertNotIn(env["OFFSITE_B2_APPLICATION_KEY"], repr(settings))
        for field in ("OFFSITE_B2_KEY_ID", "OFFSITE_B2_APPLICATION_KEY", "OFFSITE_B2_BUCKET_ID", "OFFSITE_B2_PREFIX", "BACKUP_ENCRYPTION_KEY"):
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                Settings.from_env({**env, field: ""})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({**env, "OFFSITE_B2_PREFIX": "person@example.test/"})

    def test_enrollment_is_explicit_and_identity_free(self):
        self.assertEqual(self.generation(), 0)
        with self.assertRaises(AuthorityError):
            self.authority.enroll(self.db)
        serialized = repr(self.storage.files)
        for private in ("private-sub", "Private Google", "@private.example.test"):
            self.assertNotIn(private, serialized)
        self.assertEqual(self.authority.latest()["payload"]["generation"], 0)

    def test_protected_mutations_advance_once_per_commit_and_login_timestamp_does_not(self):
        before = self.generation()
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET last_login_at=? WHERE id='one'", ("2026-10-31T12:00:00Z",))
        self.assertEqual(self.generation(), before)
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET email=?,display_name=? WHERE id='one'", ("new@fictional.example.test", "New fictional name"))
            connection.execute("UPDATE users SET display_name=? WHERE id='two'", ("Another fictional name",))
        self.assertEqual(self.generation(), before + 1)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                               ("later", "fictional-later", "later@fictional.example.test", "Later", storage_time(self.now), storage_time(self.now)))
        self.assertEqual(self.generation(), before + 2)
        self.operator.user_suspend("later")
        self.assertEqual(self.generation(), before + 3)
        self.operator.user_unsuspend("later")
        self.assertEqual(self.generation(), before + 4)
        self.interactions.block_posting("one", self.postings["two"]["slug"])
        self.assertEqual(self.generation(), before + 5)

    def test_account_and_waitlist_erasure_advance_independent_and_erasure_generations(self):
        self.lifecycle.delete_account("three")
        self.assertEqual(self.generation(), 1)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO waitlist VALUES(?,?,?,?,?)", ("wait", "wait@fictional.example.test", "Agreed", storage_time(self.now), "hash"))
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM waitlist WHERE id='wait'")
            advance_erasure(connection)
        self.assertEqual(self.generation(), 2)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT erasure_generation FROM recovery_state").fetchone()[0], 2)

    def test_remote_unavailable_rolls_back_actual_block(self):
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.interactions.block_posting("one", self.postings["two"]["slug"])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM user_blocks").fetchone()[0], 0)
        self.assertEqual(self.generation(), 0)

    def test_timeout_after_remote_acceptance_never_retries_or_rolls_back_remote(self):
        self.storage.timeout_after_write = True
        with self.assertRaises(AuthorityError):
            self.operator.user_suspend("one")
        self.storage.timeout_after_write = False
        self.assertEqual(self.generation(), 0)
        self.assertEqual(self.authority.latest()["payload"]["generation"], 1)
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT suspended_at FROM users WHERE id='one'").fetchone()[0])
        with self.assertRaises(AuthorityError):
            self.operator.user_suspend("one")
        self.assertEqual(len(self.storage.files), 2)

    def test_local_failure_after_remote_advance_never_reauthorizes_old_state(self):
        original = self.authority.advance
        def fail(connection, before):
            original(connection, before)
            raise sqlite3.OperationalError("Fictional local commit failure")
        with patch.object(self.authority, "advance", side_effect=fail), self.assertRaises(sqlite3.OperationalError):
            self.operator.user_suspend("one")
        self.assertEqual(self.generation(), 0)
        with self.assertRaises(AuthorityError):
            self.authority.require(self.authority_state())

    def test_enrolled_database_cannot_bypass_authority_by_omitting_configuration(self):
        disconnected = Database(self.db.path, self.clock)
        with self.assertRaises(AuthorityError), disconnected.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='one'", (storage_time(self.now),))
        self.assertEqual(self.generation(), 0)

    def test_forks_duplicate_versions_hides_gaps_and_bad_signatures_refused(self):
        original = list(self.storage.files)
        root = self.authority.latest()
        choices = [
            {**original[0], "id": "2"},
            {**original[0], "id": "2", "action": "hide"},
        ]
        fork = self.authority._signed(root["payload"]["lineage"], 0, "b" * 32, "")
        gap = self.authority._signed(root["payload"]["lineage"], 2, "c" * 32, root["mac"])
        for value in (fork, gap):
            raw = canonical(value)
            choices.append({"name": self.authority._name(value), "id": "2", "action": "upload", "data": raw, "length": len(raw), "metadata": {"ctb-authority": raw.decode()}})
        for extra in choices:
            with self.subTest(action=extra["action"], name=extra["name"]):
                self.storage.files = original + [extra]
                with self.assertRaises(AuthorityError):
                    self.authority.latest()
        self.storage.files = original
        original[0]["metadata"] = {"ctb-authority": original[0]["metadata"]["ctb-authority"].replace(root["mac"], "0" * 64)}
        with self.assertRaises(AuthorityError):
            self.authority.latest()

    def test_latest_metadata_must_match_download_and_whole_lineage(self):
        self.storage.files[-1]["data"] = b"changed"
        with self.assertRaises(AuthorityError):
            self.authority.latest()
        self.storage.files[-1]["data"] = self.storage.files[-1]["metadata"]["ctb-authority"].encode()
        with self.assertRaises(AuthorityError):
            self.authority.require({**self.authority_state(), "lineage_id": "f" * 32})

    @unittest.skipUnless(os.name == "posix", "Ubuntu process-crash and fsync rehearsal")
    def test_process_death_at_each_remote_local_commit_boundary(self):
        for point, expected_local, expected_remote in (("before_remote", 0, 0), ("after_remote", 0, 1), ("after_commit", 1, 1)):
            with self.subTest(point=point):
                target = self.db.path.parent / (point + ".db")
                with self.db.connect() as source:
                    destination = sqlite3.connect(target)
                    try:
                        source.backup(destination)
                    finally:
                        destination.close()
                storage_path = self.db.path.parent / (point + ".json")
                storage = FileStorage(storage_path)
                item = self.storage.files[0]
                storage.write(item["name"], item["data"], metadata=item["metadata"])
                result = subprocess.run([sys.executable, "-c", "from tests_server.test_offsite_recovery import crash_process; crash_process()", str(target), str(storage_path), point],
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, {"before_remote": 17, "after_remote": 18, "after_commit": 19}[point], result.stderr.decode())
                database = Database(target, self.clock)
                with database.connect() as connection:
                    state = local_state(connection)
                    self.assertEqual(state["generation"], expected_local)
                    self.assertEqual(connection.execute("SELECT suspended_at IS NOT NULL FROM users WHERE id='one'").fetchone()[0], expected_local)
                remote = RecoveryAuthority(FileStorage(storage_path), b"f" * 32, "ctb-" + "a" * 32 + "/")
                self.assertEqual(remote.latest()["payload"]["generation"], expected_remote)
                if expected_local != expected_remote:
                    with self.assertRaises(AuthorityError):
                        remote.require(state)


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("cryptography"), "Ubuntu permissions and encryption required")
class LostHostRecoveryTests(OffsiteFixture):
    def replacement(self):
        target = self.db.path.parent / "replacement.db"
        settings = replace(self.settings, database_path=target)
        database = Database(target, self.clock, recovery_authority=self.authority)
        return database, RecoveryService(database, self.clock, settings), settings

    def test_current_offsite_snapshot_restores_missing_host_quarantined(self):
        self.interactions.block_posting("one", self.postings["two"]["slug"])
        self.operator.user_suspend("three")
        backup = self.recovery.backup()
        self.assertTrue(backup["offsite_verified"])
        database, recovery, settings = self.replacement()
        result = recovery.restore(backup["path"], lost_host=True, old_host_fenced=True)
        self.assertTrue(result["recovery_hold"])
        self.assertIsNone(result["checkpoint"])
        self.assertTrue(recovery_held(database, settings))
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM users WHERE suspended_at IS NOT NULL").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT count(*) FROM user_blocks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM board_publication_grants WHERE revoked_at IS NULL").fetchone()[0], 0)
        with self.assertRaises(RecoveryError):
            recovery.release_hold()
        rotated = replace(settings, session_secret="rotated-fictional-" + "r" * 32)
        before = self.generation()
        RecoveryService(database, self.clock, rotated).release_hold()
        with database.connect() as connection:
            self.assertEqual(local_state(connection)["generation"], before + 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM users WHERE suspended_at IS NOT NULL").fetchone()[0], 3)

    def test_older_snapshot_refused_after_erasure_block_identity_or_suspension(self):
        def profile():
            with self.db.transaction() as connection:
                connection.execute("UPDATE users SET email=? WHERE id='one'", ("current@fictional.example.test",))
        for action in (profile, lambda: self.interactions.block_posting("one", self.postings["two"]["slug"]),
                       lambda: self.operator.user_suspend("three"), lambda: self.lifecycle.delete_account("three")):
            backup = self.recovery.backup()
            action()
            database, recovery, _ = self.replacement()
            with self.assertRaises(AuthorityError):
                recovery.restore(backup["path"], lost_host=True, old_host_fenced=True)
            self.assertFalse(database.path.exists())

    def test_fencing_missing_authority_and_existing_target_refused(self):
        backup = self.recovery.backup()
        database, recovery, _ = self.replacement()
        with self.assertRaises(RecoveryError):
            recovery.restore(backup["path"], lost_host=True)
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            recovery.restore(backup["path"], lost_host=True, old_host_fenced=True)
        self.assertFalse(database.path.exists())
        self.storage.unavailable = False
        database.migrate()
        with self.assertRaises(RecoveryError):
            recovery.restore(backup["path"], lost_host=True, old_host_fenced=True)

    def test_backup_upload_does_not_make_a_stale_generation_current(self):
        backup = self.recovery.backup()
        self.operator.user_suspend("three")
        self.assertTrue(self.authority.upload_backup(Path(backup["path"]))["offsite_verified"])
        _, recovery, _ = self.replacement()
        with self.assertRaises(AuthorityError):
            recovery.restore(backup["path"], lost_host=True, old_host_fenced=True)
