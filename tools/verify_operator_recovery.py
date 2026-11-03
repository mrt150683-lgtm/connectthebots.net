"""Ubuntu-only real operator CLI recovery verification on disposable fictional data.

Encrypted backups, keys, cookies and private fixture content stay in a private
native temporary directory or process memory. Only checks/digests are exported.
No production path, SMTP connection or external identity provider is used.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
import tempfile

REPOSITORY = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("artifacts/operator-recovery"))
    parser.add_argument("--installed", action="store_true", help="Verify this interpreter's installed wheel without repository imports")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this private-file/lock verification on Ubuntu; it never weakens file permissions on another platform.")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.installed:
        # A caller's source PYTHONPATH must not turn a packaged verification into
        # another source run. The actual CLI also starts in isolated mode.
        source_paths = {REPOSITORY.resolve(), (REPOSITORY / "src").resolve()}
        sys.path[:] = [entry for entry in sys.path if Path(entry or os.getcwd()).resolve() not in source_paths]
    else:
        sys.path.insert(0, str(REPOSITORY / "src"))

    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.board import BoardError
    from connectthebots_server.clock import utc_now
    from connectthebots_server.config import Settings
    from connectthebots_server.db import Database
    from connectthebots_server.interactions import InteractionError
    from connectthebots_server.notifications import NotificationError
    from connectthebots_server.report_schema import ReportError
    from connectthebots_server.sessions import SESSION_COOKIE, random_token, storage_time, token_hash

    checks = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append({"check": name, "passed": True})

    if args.installed:
        import connectthebots_server
        package_path = Path(connectthebots_server.__file__).resolve()
        check("server imports come from this interpreter's installed wheel", package_path.is_relative_to(Path(sys.prefix).resolve()) and not package_path.is_relative_to(REPOSITORY / "src"))

    def environment(settings, key, backup_directory):
        result = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                "APP_ENV": "staging", "PUBLIC_BASE_URL": settings.public_base_url,
                "DATABASE_PATH": str(settings.database_path), "SESSION_SECRET": settings.session_secret,
                "SIGNUPS": "open", "EMAIL_MODE": "outbox", "ACCOUNT_SIGNIN_ENABLED": "true",
                "GOOGLE_CLIENT_ID": "fictional-recovery-client", "GOOGLE_CLIENT_SECRET": "fictional-recovery-secret",
                "OPERATOR_EMAIL": "operator@fictional.example.test", "BACKUP_DIRECTORY": str(backup_directory),
                "BACKUP_ENCRYPTION_KEY": key}
        if args.installed:
            result.pop("PYTHONPATH", None)
        else:
            result["PYTHONPATH"] = str(REPOSITORY / "src")
        return result

    def cli(env, *arguments, success=True):
        result = subprocess.run([sys.executable, *(["-I"] if args.installed else []), "-m", "connectthebots_server.admin", *arguments],
                                cwd=REPOSITORY, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        expected = 0 if success else 2
        if result.returncode != expected:
            raise AssertionError(f"Operator {arguments[:2]} exit {result.returncode}, expected {expected}: {result.stderr.strip()}")
        return json.loads(result.stdout) if success else result

    def fingerprint(db):
        with db.connect() as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()

    def copy_database(source, destination):
        with source.connect() as connection, sqlite3.connect(destination) as copied:
            connection.backup(copied)
        return Database(destination, utc_now)

    def seed(root):
        root.mkdir(mode=0o700)
        key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
        secret = secrets.token_urlsafe(48)
        env = {"APP_ENV": "staging", "PUBLIC_BASE_URL": "http://localhost", "DATABASE_PATH": str(root / "app.db"),
               "SESSION_SECRET": secret, "SIGNUPS": "open", "EMAIL_MODE": "outbox", "ACCOUNT_SIGNIN_ENABLED": "true",
               "GOOGLE_CLIENT_ID": "fictional-recovery-client", "GOOGLE_CLIENT_SECRET": "fictional-recovery-secret",
               "OPERATOR_EMAIL": "operator@fictional.example.test", "BACKUP_DIRECTORY": str(root / "backups"), "BACKUP_ENCRYPTION_KEY": key}
        settings = Settings.from_env(env)
        db = Database(settings.database_path, utc_now); db.migrate()
        app = create_app(settings)
        now = storage_time(utc_now())
        with db.transaction() as connection:
            for person in ("ama", "joon", "priya"):
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                   (person, "fictional-recovery-" + person, person + "@example.invalid", person.title() + " Fictional", now, now))
            session = app.state.auth.sessions.create(connection, "joon")
            removal = random_token()
            connection.execute("INSERT INTO waitlist(id,email,consent_text,created_at,removal_hash) VALUES(?,?,?,?,?)",
                               ("fictional-independent-waitlist", "ama@example.invalid", "Fictional independent removal authority", now, token_hash(removal)))
        postings = {}
        for person in ("ama", "joon", "priya"):
            draft = app.state.board.save_draft(person, {"text": f"I build fictional {person} lab tools. I can offer repair notes. I am looking for measurements.",
                                                       "tags": ["fictional"], "looking_for": "Measurements", "offering": "Repair notes"})
            postings[person] = app.state.board.publish(person, draft["revision"], draft["payload_hash"])
        report_key = app.state.reports.create_key("joon", "Fictional recovery key")
        report_body = json.dumps({"kind": "nothing", "checked_at": now, "places_checked": [], "posts_read": 0}).encode()
        app.state.reports.ingest("Bearer " + report_key["key"], report_body, "fictional-recovery-replay")
        app.state.reports.update_settings("joon", paused=False, cadence_minutes=1440)
        preview = app.state.interactions.prepare_request("ama", postings["joon"]["slug"], "Fictional private introduction canary.", "Fictional lab measurements could help.")
        accepted = app.state.interactions.request("ama", preview, random_token())
        conversation = app.state.interactions.accept("joon", accepted["id"], accepted["context_hash"])["conversation"]["id"]
        app.state.interactions.send_message("ama", conversation, "Fictional private message canary.", random_token())
        message_nonce = random_token()
        app.state.interactions.send_message("joon", conversation, "Fictional surviving message canary.", message_nonce)
        pending = app.state.interactions.request("priya", app.state.interactions.prepare_request("priya", postings["joon"]["slug"], "Fictional pending consent.", "Fictional repair knowledge."), random_token())
        unsubscribe = app.state.notifications.unsubscribe_token("joon", "new_message")
        env = environment(settings, key, root / "backups")
        invitation = cli(env, "invite", "create", "--days", "7")
        with TestClient(app, base_url="https://localhost", follow_redirects=False) as client:
            response = client.get("/auth/google/start")
            check("fictional pending OAuth state is created locally without following Google", response.status_code == 303)
        return {"root": root, "settings": settings, "db": db, "app": app, "env": env, "key": key,
                "session": session, "removal": removal, "postings": postings, "report_key": report_key,
                "report_body": report_body, "conversation": conversation, "message_nonce": message_nonce,
                "pending": pending, "unsubscribe": unsubscribe, "invite": invitation}

    previous_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="ctb-operator-recovery-") as temporary:
            root = Path(temporary).resolve()
            check("private fixture directory is owner-only", stat.S_IMODE(root.stat().st_mode) == 0o700)
            erased = seed(root / "erasures")
            first_backup = cli(erased["env"], "backup")
            first_path = Path(first_backup["path"]).resolve()
            check("backup remains inside its private configured directory", first_path.is_relative_to(root / "erasures" / "backups"))
            check("encrypted backup and directory have private permissions", stat.S_IMODE(first_path.stat().st_mode) == 0o600 and stat.S_IMODE(first_path.parent.stat().st_mode) == 0o700)
            encrypted = first_path.read_bytes()
            check("backup is encrypted and contains no fixture message plaintext", not encrypted.startswith(b"SQLite format 3") and b"Fictional private message canary" not in encrypted)
            invitation_list = cli(erased["env"], "invite", "list")
            check("invite secret is returned only once", erased["invite"]["code"] not in json.dumps(invitation_list) and erased["invite"]["id"] in json.dumps(invitation_list))
            cli(erased["env"], "invite", "revoke", "--", erased["invite"]["id"])
            check("real invite CLI revokes its selected invitation", any(item["id"] == erased["invite"]["id"] and item["revoked_at"] for item in cli(erased["env"], "invite", "list")["items"]))
            with TestClient(create_app(erased["settings"]), base_url="https://localhost", follow_redirects=False) as client:
                form = client.get("/auth/waitlist/remove")
                csrf = re.search(r'name="csrf" value="([^"]+)"', form.text).group(1)
                removed = client.post("/auth/waitlist/remove", data={"csrf": csrf, "removal": erased["removal"]}, headers={"Origin": "http://localhost"})
                check("waitlist removal uses its own native authority", removed.status_code == 200)
            with erased["db"].connect() as connection:
                check("waitlist removal advances erasure generation", connection.execute("SELECT erasure_generation FROM recovery_state WHERE singleton=1").fetchone()[0] > first_backup["generation"])
                check("separate waitlist row is removed without deleting same-email account", connection.execute("SELECT count(*) FROM waitlist").fetchone()[0] == 0 and connection.execute("SELECT 1 FROM users WHERE id='ama'").fetchone() is not None)
            current_copy = copy_database(erased["db"], root / "waitlist-target.db")
            copied_env = {**erased["env"], "DATABASE_PATH": str(current_copy.path)}
            before = fingerprint(current_copy); cli(copied_env, "restore", "--from", str(first_path), success=False)
            check("old backup cannot resurrect a removed waitlist request", fingerprint(current_copy) == before)
            after_waitlist = cli(erased["env"], "backup")
            cli(erased["env"], "user", "delete", "ama", "--confirm", "wrong", success=False)
            check("CLI deletion requires the exact account confirmation", erased["app"].state.board.get_public(erased["postings"]["ama"]["slug"])["slug"] == erased["postings"]["ama"]["slug"])
            cli(erased["env"], "user", "delete", "ama", "--confirm", "ama")
            deletion_copy = copy_database(erased["db"], root / "deletion-target.db")
            before = fingerprint(deletion_copy); cli({**erased["env"], "DATABASE_PATH": str(deletion_copy.path)}, "restore", "--from", after_waitlist["path"], success=False)
            check("old backup cannot resurrect a deleted account or its messages", fingerprint(deletion_copy) == before)
            with deletion_copy.connect() as connection:
                check("deleted identity and authored messages remain erased after refusal", connection.execute("SELECT 1 FROM users WHERE id='ama'").fetchone() is None and connection.execute("SELECT 1 FROM messages WHERE body='Fictional private message canary.'").fetchone() is None)
            missing = root / "untrusted-fresh-host.db"
            cli({**erased["env"], "DATABASE_PATH": str(missing)}, "restore", "--from", after_waitlist["path"], success=False)
            check("snapshot alone cannot initialize recovery on a host lacking current authority", not missing.exists())

            live = seed(root / "authority")
            backup = cli(live["env"], "backup"); source = Path(backup["path"]).resolve()
            check("backup source is confined to this fixture", source.is_relative_to(root))
            target = copy_database(live["db"], root / "authority-target.db")
            target_env = {**live["env"], "DATABASE_PATH": str(target.path)}
            target_settings = replace(live["settings"], database_path=target.path)
            before = fingerprint(target)
            wrong_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
            cli({**target_env, "BACKUP_ENCRYPTION_KEY": wrong_key}, "restore", "--from", str(source), success=False)
            check("wrong encryption key leaves the trusted target unchanged", fingerprint(target) == before)
            original = source.read_bytes()
            try:
                source.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                cli(target_env, "restore", "--from", str(source), success=False)
                check("authenticated corruption is refused without target mutation", fingerprint(target) == before)
            finally:
                source.write_bytes(original)
            foreign = Database(root / "foreign-lineage.db", utc_now); foreign.migrate()
            foreign_before = fingerprint(foreign)
            cli({**target_env, "DATABASE_PATH": str(foreign.path)}, "restore", "--from", str(source), success=False)
            check("foreign lineage cannot be overwritten by a valid encrypted backup", fingerprint(foreign) == foreign_before)
            with TestClient(create_app(target_settings), base_url="https://localhost", follow_redirects=False) as client:
                check("running fixture is healthy before restore refusal", client.get("/healthz").status_code == 200)
                cli(target_env, "restore", "--from", str(source), success=False)
                check("actual application lifespan lock prevents live restore", fingerprint(target) == before)

            live["app"].state.auth.sessions.revoke(live["session"])
            live["app"].state.reports.revoke_key("joon", live["report_key"]["id"])
            cli(live["env"], "invite", "revoke", "--", live["invite"]["id"])
            live["app"].state.interactions.block_posting("joon", live["postings"]["ama"]["slug"])
            live["app"].state.board.unpublish("ama")
            live["app"].state.interactions.withdraw("priya", live["pending"]["id"])
            live["app"].state.interactions.close_conversation("joon", live["conversation"])
            # Same erasure generation does not imply the same live identities
            # or block graph. These later changes remain trusted authority.
            with live["db"].transaction() as connection:
                current_time = storage_time(utc_now())
                connection.execute("UPDATE users SET email=?,display_name=? WHERE id='ama'", ("ama-current@example.invalid", "Ama Current Fictional"))
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                   ("nora", "fictional-recovery-nora", "nora@example.invalid", "Nora Fictional", current_time, current_time))
            cli(live["env"], "user", "suspend", "nora")
            current_target = copy_database(live["db"], root / "quarantined-target.db")
            restore_env = {**live["env"], "DATABASE_PATH": str(current_target.path)}
            restored_settings = replace(live["settings"], database_path=current_target.path)
            restored = cli(restore_env, "restore", "--from", str(source))
            check("same-generation restore is explicitly quarantined", restored["restored"] and restored["recovery_hold"])
            checkpoint = Path(restored["checkpoint"]).resolve()
            check("restore first preserves a private encrypted current-state checkpoint", checkpoint.is_relative_to(root) and checkpoint.exists() and stat.S_IMODE(checkpoint.stat().st_mode) == 0o600)
            with current_target.connect() as connection:
                check("restored database passes SQLite integrity and foreign-key checks", connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and not connection.execute("PRAGMA foreign_key_check").fetchall())
                check("every restored account remains suspended", connection.execute("SELECT count(*) FROM users WHERE suspended_at IS NULL").fetchone()[0] == 0)
                check("current live identities absent from the older snapshot remain quarantined", {row[0] for row in connection.execute("SELECT id FROM users")} == {"ama", "joon", "priya", "nora"} and connection.execute("SELECT suspended_at FROM users WHERE google_sub='fictional-recovery-nora'").fetchone()[0] is not None)
                current_profile = connection.execute("SELECT email,display_name FROM users WHERE id='ama'").fetchone()
                check("current trusted profile values replace older snapshot values", tuple(current_profile) == ("ama-current@example.invalid", "Ama Current Fictional"))
                check("a block added after the backup survives the restore", connection.execute("SELECT 1 FROM user_blocks WHERE blocker_id='joon' AND blocked_id='ama'").fetchone() is not None)
                check("session OAuth and unsubscribe capabilities are cleared", all(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in ("sessions", "oauth_attempts", "notification_subscriptions")))
                check("report keys and invitations stay revoked", connection.execute("SELECT count(*) FROM report_keys WHERE revoked_at IS NULL").fetchone()[0] == 0 and connection.execute("SELECT count(*) FROM invites WHERE revoked_at IS NULL").fetchone()[0] == 0)
                check("publication grants cannot reactivate automatically", connection.execute("SELECT count(*) FROM board_publication_grants WHERE revoked_at IS NULL").fetchone()[0] == 0 and connection.execute("SELECT count(*) FROM board_postings WHERE active_grant_id IS NOT NULL").fetchone()[0] == 0)
                check("pending introductions and open conversations remain closed", connection.execute("SELECT count(*) FROM introductions WHERE status='requested'").fetchone()[0] == 0 and connection.execute("SELECT count(*) FROM conversations WHERE closed_at IS NULL").fetchone()[0] == 0)
                check("restored outgoing email is cancelled", connection.execute("SELECT count(*) FROM email_outbox WHERE state IN ('pending','outbox','sending')").fetchone()[0] == 0)
                check("check-ins are paused and optional email is off", connection.execute("SELECT count(*) FROM report_settings WHERE paused=0").fetchone()[0] == 0 and connection.execute("SELECT count(*) FROM notification_preferences WHERE security_first_login OR introduction_request OR introduction_accepted OR new_message OR checkin_missed OR weekly_summary").fetchone()[0] == 0)
            with TestClient(create_app(restored_settings), base_url="https://localhost", follow_redirects=False) as client:
                check("health reports unavailable during recovery hold", client.get("/healthz").status_code == 503)
            cli(restore_env, "user", "unsuspend", "joon", success=False)
            cli(restore_env, "recovery", "release", success=False)
            check("unchanged session secret cannot release recovery hold", True)
            rotated = secrets.token_urlsafe(48)
            cli({**restore_env, "SESSION_SECRET": rotated}, "recovery", "release")
            with TestClient(create_app(restored_settings), base_url="https://localhost", follow_redirects=False) as stale:
                check("a stale process secret stays blocked after global release", stale.get("/healthz").status_code == 503)
            rotated_settings = replace(restored_settings, session_secret=rotated)
            with current_target.connect() as connection:
                check("global release does not review or unsuspend any account", connection.execute("SELECT count(*) FROM users WHERE suspended_at IS NULL").fetchone()[0] == 0)
                check("later suspended identity remains unavailable after global release", connection.execute("SELECT suspended_at FROM users WHERE google_sub='fictional-recovery-nora'").fetchone()[0] is not None)
            cli({**restore_env, "SESSION_SECRET": rotated}, "user", "unsuspend", "joon")
            reviewed = create_app(rotated_settings)
            check("old bearer key cannot recover even an old successful receipt", _refused(lambda: reviewed.state.reports.ingest("Bearer " + live["report_key"]["key"], live["report_body"], "fictional-recovery-replay"), ReportError, 401))
            check("old pending acceptance cannot reopen contact after account review", _refused(lambda: reviewed.state.interactions.accept("joon", live["pending"]["id"], live["pending"]["context_hash"]), InteractionError, 410))
            check("old message nonce cannot send or replay into a closed conversation", _refused(lambda: reviewed.state.interactions.send_message("joon", live["conversation"], "Fictional surviving message canary.", live["message_nonce"]), InteractionError, 409))
            check("old unsubscribe capability does not survive recovery", _refused(lambda: reviewed.state.notifications.token_info(live["unsubscribe"]), NotificationError, 400))
            with TestClient(reviewed, base_url="https://localhost", follow_redirects=False) as client:
                check("rotated reviewed application is healthy", client.get("/healthz").status_code == 200)
                check("old browser session stays invalid after review", client.get("/app", headers={"Cookie": SESSION_COOKIE + "=" + live["session"]}).status_code == 303)
                check("revoked old public posting is not republished", client.get("/board/" + live["postings"]["ama"]["slug"] + ".json").status_code == 410)
            reviewed_env = {**restore_env, "SESSION_SECRET": rotated}
            cli(reviewed_env, "user", "unsuspend", "ama")
            republished = {}
            for person in ("ama", "joon"):
                cli(reviewed_env, "posting", "unhide", live["postings"][person]["slug"])
                draft = reviewed.state.board.save_draft(person, {"text": f"I build fictional {person} recovery tools. I can offer repair notes. I am looking for measurements.",
                                                                "tags": ["fictional"], "looking_for": "Measurements", "offering": "Repair notes"})
                republished[person] = reviewed.state.board.publish(person, draft["revision"], draft["payload_hash"])
            check("reviewed owners can explicitly republish for anonymous readers", all(reviewed.state.board.get_public(republished[person]["slug"])["slug"] == republished[person]["slug"] for person in ("ama", "joon")))
            check("the later block still filters both reviewed owners after fresh publication", all(_refused(lambda owner=owner, peer=peer: reviewed.state.board.get_public(republished[peer]["slug"], viewer_id=owner), BoardError, 404) for owner, peer in (("ama", "joon"), ("joon", "ama"))))
            check("the preserved block prevents either reviewed owner requesting new contact", all(_refused(lambda owner=owner, peer=peer: reviewed.state.interactions.prepare_request(owner, republished[peer]["slug"], "Fictional renewed request.", "Fictional shared work."), InteractionError, 404) for owner, peer in (("ama", "joon"), ("joon", "ama"))))
            check("reviewing both owners cannot reopen the old conversation", _refused(lambda: reviewed.state.interactions.send_message("joon", live["conversation"], "Must remain closed after review.", random_token()), InteractionError, 409))
            bad = Database(root / "bad-history.db", utc_now); bad.migrate()
            with bad.transaction() as connection:
                connection.execute("UPDATE schema_migrations SET sha256=? WHERE name=(SELECT min(name) FROM schema_migrations)", ("0" * 64,))
            cli({**live["env"], "DATABASE_PATH": str(bad.path)}, "backup", success=False)
            check("operator backup refuses a mismatched migration history", True)
    finally:
        os.umask(previous_umask)
    (args.out / "verification.json").write_text(json.dumps({"checks": checks, "passed": len(checks), "fixture_only": True, "real_email_tested": False, "code_source": "installed-wheel" if args.installed else "repository"}, indent=2) + "\n")
    print(f"{len(checks)} operator/recovery checks passed. Evidence: {args.out}")
    return 0


def _refused(action, error_type, status):
    try:
        action()
    except error_type as error:
        return error.status == status
    return False


if __name__ == "__main__":
    raise SystemExit(main())
