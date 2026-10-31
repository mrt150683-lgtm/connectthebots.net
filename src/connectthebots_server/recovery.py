"""Encrypted snapshots and deletion-aware, offline recovery for the Ubuntu host.

A backup alone is never authority to restore deleted data. Existing-host restore
requires current local authority; enrolled independent authority additionally
allows a fenced replacement host to restore an exactly current snapshot.
"""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import struct
import tempfile

from .clock import timestamp
from .db import Database, MigrationError, _statements
from .sessions import storage_time

MAGIC = b"CTB-BACKUP-1\n"
CHUNK = 512 * 1024
OWNED_NAME = re.compile(r"ctb-backup-(\d{8}T\d{12}Z)-[a-f0-9]{16}\.ctb\Z")


class RecoveryError(ValueError):
    """Fixed safe text, suitable for operator output; never exception internals."""

    @property
    def detail(self):
        return str(self)


def advance_erasure(connection):
    if connection.execute("UPDATE recovery_state SET erasure_generation=erasure_generation+1 WHERE singleton=1").rowcount != 1:
        raise RecoveryError("Erasure authority is unavailable; deletion was rolled back.")
    # Pending OAuth rows do not yet reveal which Google identity will return.
    # Invalidate all older attempts rather than retain erased identity markers.
    connection.execute("DELETE FROM oauth_attempts")


def connection_held(connection, settings):
    try:
        row = connection.execute("SELECT recovery_hold,restore_secret_hash FROM recovery_state WHERE singleton=1").fetchone()
        if row is None or row[0]:
            return True
        return bool(row[1] and hmac.compare_digest(row[1], sha256(settings.session_secret.encode()).hexdigest()))
    except sqlite3.Error:
        return True


def recovery_held(database, settings):
    try:
        with database.connect() as connection:
            return connection_held(connection, settings)
    except (sqlite3.Error, OSError):
        return True


@contextmanager
def runtime_lock(database_path, *, exclusive=False):
    """Cooperating app and CLI processes share one persistent lock inode.

    Linux restore takes a nonblocking exclusive lock. Never unlink this file:
    replacing its inode would let a new process bypass an existing holder.
    Windows supports local web tests, not backup/restore permission guarantees.
    """
    if os.name != "posix":
        if exclusive:
            raise RecoveryError("Restore must run on the Ubuntu host with services stopped.")
        yield
        return
    import fcntl
    path = Path(database_path).with_name(Path(database_path).name + ".runtime.lock")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RecoveryError("The runtime lock must be a private regular file owned by the service account.")
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecoveryError("Stop the app and workers before restoring; the database is in use.") from None
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _digest(path):
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _private_temp(directory):
    descriptor, name = tempfile.mkstemp(prefix=".ctb-recovery-", dir=directory)
    os.close(descriptor)  # mkstemp creates mode0600 before the first byte.
    path = Path(name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


class RecoveryService:
    def __init__(self, database, clock, settings):
        self.database, self.clock, self.settings = database, clock, settings

    def _independent(self):
        from .recovery_authority import AuthorityError
        if self.database.recovery_authority is None:
            raise AuthorityError("Independent recovery configuration is required; operation refused.")
        return self.database.recovery_authority

    def _key(self):
        if os.name != "posix":
            raise RecoveryError("Encrypted backups and restore require Ubuntu file permissions.")
        try:
            key = base64.b64decode(self.settings.backup_encryption_key, altchars=b"-_", validate=True)
        except (ValueError, TypeError):
            raise RecoveryError("Configure BACKUP_ENCRYPTION_KEY with 32 random bytes encoded as base64url.") from None
        if len(key) != 32:
            raise RecoveryError("Configure BACKUP_ENCRYPTION_KEY with 32 random bytes encoded as base64url.")
        return key

    def _directory(self):
        directory = self.settings.backup_directory or self.database.path.parent / "backups"
        directory = Path(directory)
        if not directory.is_absolute():
            raise RecoveryError("BACKUP_DIRECTORY must be an absolute path.")
        directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RecoveryError("Backup directory must be mode0700, owned by the service account, with no symlink.")
        return directory

    def _validate(self, path):
        """Read only; no migration of untrusted or stale recovery input."""
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise RecoveryError("Recovery needs an intact regular database file.")
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                history = self.database._history(connection)
                self.database._verify_history(history)
                if len(history) != len(self.database.migrations):
                    raise RecoveryError("Database schema must exactly match the installed application.")
                if [r[0] for r in connection.execute("PRAGMA integrity_check")] != ["ok"] or connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise RecoveryError("Database integrity validation failed.")
                # History alone cannot establish that tables/triggers are intact.
                expected = sqlite3.connect(":memory:")
                try:
                    for migration in self.database.migrations:
                        for statement in _statements(migration.sql):
                            expected.execute(statement)
                    query = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                    if [tuple(row) for row in connection.execute(query)] != list(expected.execute(query)):
                        raise RecoveryError("Database schema objects do not match the installed application.")
                finally:
                    expected.close()
                row = connection.execute("SELECT * FROM recovery_state WHERE singleton=1").fetchone()
                if row is None or not re.fullmatch(r"[a-f0-9]{32}", row["lineage_id"]) or type(row["erasure_generation"]) is not int or row["erasure_generation"] < 0:
                    raise RecoveryError("Trusted erasure authority is unavailable.")
                from .recovery_authority import local_state
                result = dict(row)
                authority = local_state(connection)
                result["authority"] = {name: authority[name] for name in ("enrolled", "lineage_id", "generation", "marker")}
                result["authority"]["growth_generation"] = authority.get("growth_generation", 0)
                return result
            finally:
                connection.close()
        except (sqlite3.Error, OSError, MigrationError):
            raise RecoveryError("Recovery database is unavailable, damaged or incompatible.") from None

    def _authority_matches(self, source, live):
        if source["lineage_id"] != live["lineage_id"] or source["erasure_generation"] != live["erasure_generation"]:
            raise RecoveryError("Backup predates an erasure or belongs to another database; restore refused.")
        if source.get("authority") != live.get("authority"):
            raise RecoveryError("Backup differs from current recovery authority; restore refused.")

    def backup(self):
        self._key()
        with runtime_lock(self.database.path):
            result = self._backup()
            if result["authority"]["enrolled"]:
                authority = self._independent()
                # A later protected mutation can invalidate this snapshot, but
                # upload never changes authority or makes an old snapshot valid.
                authority.require(result["authority"])
                result.update(authority.upload_backup(Path(result["path"])))
                authority.require(result["authority"])
            return result

    def _backup(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        key, directory = self._key(), self._directory()
        self._validate(self.database.path)
        with _private_temp(directory) as snapshot, _private_temp(directory) as encrypted:
            with self.database.connect() as source:
                destination = sqlite3.connect(snapshot)
                try:
                    source.backup(destination)
                    destination.execute("PRAGMA journal_mode=DELETE")
                finally:
                    destination.close()
            state = self._validate(snapshot)
            if state["authority"]["enrolled"]:
                self._independent().require(state["authority"])
            now = self.clock().astimezone(timezone.utc)
            metadata = {
                "version": 1, "created_at": timestamp(now), "lineage_id": state["lineage_id"],
                "erasure_generation": state["erasure_generation"], "sha256": _digest(snapshot),
                "authority": state["authority"],
                "migrations": [[m.name, m.checksum] for m in self.database.migrations],
            }
            header = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
            nonce = secrets.token_bytes(12)
            prefix = MAGIC + struct.pack(">I", len(header)) + header + nonce
            encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
            encryptor.authenticate_additional_data(prefix)
            with snapshot.open("rb") as source, encrypted.open("wb") as output:
                output.write(prefix)
                for chunk in iter(lambda: source.read(CHUNK), b""):
                    output.write(encryptor.update(chunk))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
            # An erasure racing the snapshot must never yield an eligible old file.
            with self.database.transaction() as connection:
                live = dict(connection.execute("SELECT * FROM recovery_state WHERE singleton=1").fetchone())
                from .recovery_authority import local_state
                authority_state = local_state(connection)
                live["authority"] = {name: authority_state[name] for name in ("enrolled", "lineage_id", "generation", "marker")}
                live["authority"]["growth_generation"] = authority_state.get("growth_generation", 0)
                self._authority_matches(state, live)
                if authority_state["enrolled"]:
                    self._independent().require(authority_state)
                filename = "ctb-backup-" + now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(8) + ".ctb"
                target = directory / filename
                os.replace(encrypted, target)
                _fsync_directory(directory)
            self._prune(directory, now, keep=target)
            return {"path": str(target), "created_at": metadata["created_at"],
                    "lineage_id": state["lineage_id"], "generation": state["erasure_generation"], "authority": state["authority"]}

    def _prune(self, directory, now, *, keep):
        cutoff = now - timedelta(days=14)
        for candidate in directory.iterdir():
            match = OWNED_NAME.fullmatch(candidate.name)
            if not match or candidate == keep:
                continue
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                continue
            try:
                created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if created < cutoff:
                candidate.unlink()
        _fsync_directory(directory)

    def _decrypt(self, source, destination, key):
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        try:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as encrypted:
                info = os.fstat(encrypted.fileno())
                if not stat.S_ISREG(info.st_mode) or encrypted.read(len(MAGIC)) != MAGIC:
                    raise RecoveryError("Select an authenticated Connect the Bots backup.")
                size = struct.unpack(">I", encrypted.read(4))[0]
                if not 1 <= size <= 65536:
                    raise RecoveryError("Backup header is invalid.")
                header, nonce = encrypted.read(size), encrypted.read(12)
                prefix = MAGIC + struct.pack(">I", size) + header + nonce
                remaining = info.st_size - len(prefix) - 16
                if len(nonce) != 12 or remaining <= 0:
                    raise RecoveryError("Backup is truncated.")
                encrypted.seek(-16, os.SEEK_END)
                tag = encrypted.read(16)
                encrypted.seek(len(prefix))
                decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
                decryptor.authenticate_additional_data(prefix)
                with destination.open("wb") as output:
                    while remaining:
                        chunk = encrypted.read(min(CHUNK, remaining))
                        if not chunk:
                            raise RecoveryError("Backup is truncated.")
                        output.write(decryptor.update(chunk))
                        remaining -= len(chunk)
                    output.write(decryptor.finalize())
                    output.flush()
                    os.fsync(output.fileno())
            metadata = json.loads(header)
            if not isinstance(metadata, dict) or metadata.get("version") != 1 or metadata.get("migrations") != [[m.name, m.checksum] for m in self.database.migrations]:
                raise RecoveryError("Backup format or schema is incompatible.")
            if not hmac.compare_digest(_digest(destination), str(metadata.get("sha256", ""))):
                raise RecoveryError("Backup content validation failed.")
            state = self._validate(destination)
            self._authority_matches(state, metadata)
            return state
        except (InvalidTag, ValueError, KeyError, OSError, struct.error, UnicodeError) as error:
            if isinstance(error, RecoveryError):
                raise
            raise RecoveryError("Backup authentication failed; check the key and file.") from None

    def _quarantine(self, path, *, authority_path=None):
        from .board import touch_board
        restored = Database(path, self.clock)
        now = storage_time(self.clock())
        # The trusted live database is also authority for current identities and
        # blocks. A snapshot can predate a later account (including suspension)
        # or block without advancing the erasure generation. Quarantining only
        # old rows would let a missing suspended identity sign up as new after
        # release, or allow an old block to disappear after account review.
        roster = self.database if authority_path is None else Database(authority_path, self.clock)
        with roster.connect() as live:
            live_users = [dict(row) for row in live.execute(
                "SELECT id,google_sub,email,display_name,created_at,last_login_at,suspended_at FROM users"
            )]
            live_blocks = [tuple(row) for row in live.execute(
                "SELECT blocker_id,blocked_id,created_at FROM user_blocks"
            )]
        identities = {row["id"]: row["google_sub"] for row in live_users}
        # This is a private, authenticated scratch database, never the live one.
        # Quarantine only removes permissions. It does not advance external
        # authority: a crashed replacement must still be recoverable from the
        # same snapshot. Release/unsuspension later advance authority normally.
        with restored.transaction(_guard_recovery=False) as connection:
            for row in connection.execute("SELECT id,google_sub FROM users"):
                if identities.get(row["id"]) != row["google_sub"]:
                    raise RecoveryError("Backup account identity is absent or conflicts with current authority; restore refused.")
            connection.executemany(
                "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at,suspended_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET email=excluded.email,display_name=excluded.display_name,"
                "created_at=excluded.created_at,last_login_at=excluded.last_login_at,suspended_at=excluded.suspended_at",
                [(row["id"], row["google_sub"], row["email"], row["display_name"], row["created_at"], row["last_login_at"], row["suspended_at"]) for row in live_users],
            )
            connection.execute("DELETE FROM user_blocks")
            connection.executemany("INSERT INTO user_blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)", live_blocks)
            connection.execute("UPDATE recovery_state SET recovery_hold=1,restore_secret_hash=? WHERE singleton=1",
                               (sha256(self.settings.session_secret.encode()).hexdigest(),))
            connection.execute("UPDATE users SET suspended_at=COALESCE(suspended_at,?)", (now,))
            connection.execute("DELETE FROM sessions")
            connection.execute("DELETE FROM oauth_attempts")
            connection.execute("DELETE FROM notification_subscriptions")
            connection.execute("DELETE FROM operator_subscriptions")
            connection.execute("DELETE FROM operator_certificate_state")
            connection.execute("UPDATE report_keys SET revoked_at=COALESCE(revoked_at,?)", (now,))
            connection.execute("UPDATE invites SET revoked_at=COALESCE(revoked_at,?)", (now,))
            connection.execute("UPDATE board_publication_grants SET revoked_at=COALESCE(revoked_at,?)", (now,))
            # Fresh draft revisions invalidate any preview signed before restore,
            # even if someone later configures an old application secret.
            connection.execute("INSERT INTO board_revisions(user_id,revision,payload_json,payload_hash,search_text,saved_at) SELECT r.user_id,r.revision+1,r.payload_json,r.payload_hash,r.search_text,? FROM board_revisions r JOIN board_postings p ON p.user_id=r.user_id AND p.draft_revision=r.revision", (now,))
            connection.execute("UPDATE board_postings SET active_grant_id=NULL,hidden=1,draft_revision=draft_revision+1,updated_at=?", (now,))
            # Growth sharing has independent consent and publication records.
            # Account release must not revive them or cached responses that
            # contained another person's data. Owners can make fresh choices.
            connection.execute("UPDATE board_posts SET hidden_at=COALESCE(hidden_at,?)", (now,))
            connection.execute("UPDATE board_replies SET hidden_at=COALESCE(hidden_at,?)", (now,))
            connection.execute("DELETE FROM taste_disclosures")
            connection.execute("UPDATE taste_answers SET tier='never',approval_nonce=lower(hex(randomblob(16)))")
            connection.execute("DELETE FROM taste_answers WHERE topic='politics'")
            # A restored fingerprint stays private to its owner until re-approved.
            connection.execute("UPDATE personality_fingerprints SET tier='never'")
            connection.execute("INSERT INTO politics_consent_decisions(user_id,notice_version,choice,recorded_at,source) "
                               "SELECT d.user_id,d.notice_version,'withdrawn',?,'recovery' FROM politics_consent_decisions d "
                               "WHERE d.id=(SELECT MAX(latest.id) FROM politics_consent_decisions latest WHERE latest.user_id=d.user_id) "
                               "AND d.choice='granted'", (now,))
            connection.execute("UPDATE member_profiles SET show_name_on_leaderboards=0,signature_opt_in=0,signature_includes_code=0")
            connection.execute("DELETE FROM share_permissions")
            connection.execute("DELETE FROM agent_receipts")
            connection.execute("DELETE FROM agent_connect_links")
            connection.execute("DELETE FROM taste_matches_seen")
            connection.execute("DELETE FROM topic_signals")
            connection.execute("DELETE FROM early_access_grants")
            connection.execute("UPDATE early_access_state SET calculated_at=NULL")
            connection.execute("UPDATE introductions SET status='withdrawn',resolved_at=? WHERE status='requested'", (now,))
            connection.execute("UPDATE conversations SET closed_at=COALESCE(closed_at,?),closed_reason=COALESCE(closed_reason,'recovery')", (now,))
            connection.execute("INSERT OR IGNORE INTO report_settings(user_id,paused) SELECT id,1 FROM users")
            connection.execute("UPDATE report_settings SET paused=1,next_due_at=NULL")
            connection.execute("INSERT OR IGNORE INTO notification_preferences(user_id) SELECT id FROM users")
            connection.execute("UPDATE notification_preferences SET security_first_login=0,introduction_request=0,introduction_accepted=0,new_message=0,checkin_missed=0,weekly_summary=0,weekly_next_at=NULL")
            connection.execute("DELETE FROM notification_mail_windows")
            connection.execute("UPDATE notification_events SET cancelled_at=COALESCE(cancelled_at,?)", (now,))
            connection.execute("UPDATE email_outbox SET state=CASE WHEN state='sending' THEN 'uncertain' ELSE 'cancelled' END,lease_id=NULL,lease_until=NULL WHERE state IN ('pending','sending','outbox')")
            touch_board(connection, self.clock)
        with restored.connect() as connection:
            connection.execute("PRAGMA journal_mode=DELETE")

    def restore(self, source, *, lost_host=False, old_host_fenced=False):
        key = self._key()
        with runtime_lock(self.database.path, exclusive=True):
            if lost_host:
                if not old_host_fenced:
                    raise RecoveryError("Stop the previous host and revoke its storage key before replacement-host recovery.")
                if os.path.lexists(self.database.path) or any(os.path.lexists(str(self.database.path) + suffix) for suffix in ("-wal", "-shm")):
                    raise RecoveryError("Replacement-host restore requires a missing target database and no WAL files.")
                self._independent()
                live = None
            else:
                live = self._validate(self.database.path)
            with _private_temp(self.database.path.parent) as restored:
                state = self._decrypt(Path(source), restored, key)
                if live is not None:
                    self._authority_matches(state, live)
                if lost_host or state["authority"]["enrolled"]:
                    self._independent().require(state["authority"])
                self._quarantine(restored, authority_path=restored if lost_host else None)
                self._validate(restored)
                checkpoint = self._backup() if live is not None else None
                if live is not None:
                    self._authority_matches(state, self._validate(self.database.path))
                if lost_host or state["authority"]["enrolled"]:
                    self._independent().require(state["authority"])
                # All managed writers are stopped by the exclusive runtime lock.
                # Clear old WAL state before replacing the complete main file.
                if live is not None:
                    with self.database.connect() as connection:
                        if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                            raise RecoveryError("Cannot checkpoint the target; restore refused.")
                with restored.open("rb") as verified:
                    os.fsync(verified.fileno())
                os.replace(restored, self.database.path)
                _fsync_directory(self.database.path.parent)
            return {"restored": True, "recovery_hold": True, "checkpoint": checkpoint["path"] if checkpoint else None}

    def release_hold(self):
        with runtime_lock(self.database.path):
            with self.database.transaction() as connection:
                row = connection.execute("SELECT recovery_hold,restore_secret_hash FROM recovery_state WHERE singleton=1").fetchone()
                if row is None:
                    raise RecoveryError("Recovery state is unavailable.")
                if row[1] and hmac.compare_digest(row[1], sha256(self.settings.session_secret.encode()).hexdigest()):
                    raise RecoveryError("Rotate SESSION_SECRET before releasing recovery hold.")
                connection.execute("UPDATE recovery_state SET recovery_hold=0 WHERE singleton=1")
                # Retain the old-secret fingerprint: still-running processes with
                # stale environment values remain held after this transaction.
                return {"recovery_hold": False, "accounts_require_review": True}
