"""Fictional-only backup freshness checks; no provider requests or credentials."""
import base64
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from connectthebots_server import backup_monitor as monitor
from connectthebots_server.config import ConfigurationError, Settings
from connectthebots_server.db import Database
from connectthebots_server.recovery import RecoveryError
from connectthebots_server.recovery_authority import AuthorityError, local_state


def heartbeat(token=None):
    return "https://sm.hetrixtools.net/hb/?s=" + (token if token is not None else "fictional" * 4)


class ConfigurationTests(unittest.TestCase):
    def test_runtime_validation_is_exact_and_secret_safe(self):
        env = {"APP_ENV": "staging", "PUBLIC_BASE_URL": "http://localhost", "SESSION_SECRET": "f" * 40,
               "DATABASE_PATH": str(Path.cwd() / "fictional.db"), "BACKUP_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"f" * 32).decode(),
               "OFFSITE_B2_KEY_ID": "fictional", "OFFSITE_B2_APPLICATION_KEY": "fictional",
               "OFFSITE_B2_BUCKET_ID": "fictional", "OFFSITE_B2_PREFIX": "ctb-" + "a" * 32 + "/"}
        for token in ("a" * 16, "a" * 256, "a_A-" * 8):
            settings = Settings.from_env(env | {"BACKUP_HEARTBEAT_URL": heartbeat(token)})
            self.assertEqual(settings.backup_heartbeat_url, heartbeat(token))
            self.assertNotIn(heartbeat(token), repr(settings))
        for url in (heartbeat("a" * 15), heartbeat("a" * 257), heartbeat() + "&x=y", heartbeat() + "#x",
                    heartbeat().replace("https:", "http:"), heartbeat().replace(".net", ".net.attacker.test"),
                    heartbeat().replace(".net", ".net:443"), heartbeat().replace("sm.", "user@sm."),
                    heartbeat() + "%41", heartbeat() + "\n", " " + heartbeat()):
            with self.subTest(kind=url[:25]), self.assertRaises(ConfigurationError) as caught:
                Settings.from_env(env | {"BACKUP_HEARTBEAT_URL": url})
            self.assertNotIn(url, str(caught.exception))
        for name in ("OFFSITE_B2_KEY_ID", "OFFSITE_B2_APPLICATION_KEY", "OFFSITE_B2_BUCKET_ID", "OFFSITE_B2_PREFIX"):
            with self.assertRaisesRegex(ConfigurationError, "OFFSITE_B2"):
                Settings.from_env(env | {name: "", "BACKUP_HEARTBEAT_URL": heartbeat()})

    def test_redactor_removes_capability_in_messages_and_environment(self):
        path = Path(__file__).resolve().parents[1] / "tools/verify_server_staging.py"
        spec = importlib.util.spec_from_file_location("monitor_staging_verifier", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.redact("request " + heartbeat() + "\nBACKUP_HEARTBEAT_URL=" + heartbeat())
        self.assertNotIn(heartbeat(), result)
        self.assertNotIn("fictional" * 4, result)


class TransportTests(unittest.TestCase):
    def connection(self, status=200):
        connection = Mock()
        connection.getresponse.return_value.status = status
        return connection, Mock(return_value=connection)

    def test_verified_head_without_body_retry_redirect_proxy_or_response_read(self):
        connection, factory = self.connection()
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://invalid.example.test"}):
            monitor._send_direct(heartbeat(), connection_factory=factory)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.args, ("sm.hetrixtools.net",))
        self.assertEqual(factory.call_args.kwargs["timeout"], 5)
        context = factory.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        connection.set_debuglevel.assert_called_once_with(0)
        self.assertEqual(connection.request.call_args.args, ("HEAD", "/hb/?s=" + "fictional" * 4))
        self.assertEqual(set(connection.request.call_args.kwargs), {"headers"})
        connection.getresponse.return_value.read.assert_not_called()
        connection.close.assert_called_once()

    def test_redirect_and_error_status_never_follow_or_expose_response(self):
        for status in (201, 301, 302, 307, 400, 500):
            connection, factory = self.connection(status)
            connection.getresponse.return_value.reason = heartbeat()
            with self.assertRaises(monitor.BackupMonitorError) as caught:
                monitor._send_direct(heartbeat(), connection_factory=factory)
            self.assertNotIn(heartbeat(), str(caught.exception))
            self.assertEqual(connection.request.call_count, 1)
            connection.getresponse.return_value.read.assert_not_called()

    def test_request_and_cleanup_exceptions_cannot_disclose_url(self):
        for failure_at in ("factory", "request", "getresponse"):
            connection, factory = self.connection()
            failure = RuntimeError(heartbeat())
            if failure_at == "factory":
                factory.side_effect = failure
            else:
                getattr(connection, failure_at).side_effect = failure
            connection.close.side_effect = failure
            with self.assertRaises(monitor.BackupMonitorError) as caught:
                monitor._send_direct(heartbeat(), connection_factory=factory)
            self.assertNotIn(heartbeat(), str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)
        connection, factory = self.connection()
        connection.close.side_effect = RuntimeError(heartbeat())
        monitor._send_direct(heartbeat(), connection_factory=factory)

    def test_child_receives_capability_only_on_stdin_and_output_is_discarded(self):
        with patch.dict("os.environ", {"BACKUP_HEARTBEAT_URL": heartbeat(), "SESSION_SECRET": "fictional-private"}), \
             patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            monitor._send(heartbeat())
        args, kwargs = run.call_args
        self.assertNotIn(heartbeat(), repr(args))
        self.assertNotIn(heartbeat(), repr(kwargs["env"]))
        self.assertNotIn("SESSION_SECRET", kwargs["env"])
        self.assertEqual(kwargs["input"], heartbeat())
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 7)
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 2)):
            with self.assertRaises(monitor.BackupMonitorError):
                monitor._send(heartbeat())

    def test_stalled_child_is_killed_and_reaped_before_return_without_secret_error(self):
        children = []
        original = subprocess.Popen
        def start(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        command = [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(30)"]
        begin = time.monotonic()
        with patch("subprocess.Popen", side_effect=start), self.assertRaises(monitor.BackupMonitorError) as caught:
            monitor._send(heartbeat(), command=command, timeout=0.2)
        self.assertLess(time.monotonic() - begin, 3)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertNotIn(heartbeat(), str(caught.exception))


class SuccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = lambda: datetime(2026, 10, 30, tzinfo=timezone.utc)
        self.authority = Mock()
        self.db = Database(Path(self.temp.name) / "fictional.db", self.now, recovery_authority=self.authority)
        self.db.migrate()
        with self.db.transaction(_guard_recovery=False) as connection:
            connection.execute("UPDATE recovery_authority SET enrolled=1,generation=2,marker=?", ("a" * 32,))
        with self.db.connect() as connection:
            self.state = local_state(connection)
        self.expected = {key: self.state[key] for key in ("enrolled", "lineage_id", "generation", "marker", "growth_generation")}
        self.result = {"authority": self.expected, "offsite_verified": True}

    def test_exact_current_authority_is_rechecked_before_head_under_writer_fence(self):
        order = []
        def require(state):
            self.assertEqual(state, self.expected)
            order.append("fresh remote authority")
        def send(url):
            self.assertEqual(url, heartbeat())
            order.append("HEAD")
            start = time.monotonic()
            with self.assertRaises(sqlite3.OperationalError):
                with self.db.transaction():
                    self.fail("A second writer must not pass the current-snapshot fence")
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertTrue(self.db.is_current())  # Independent WAL readers remain available.
        self.authority.require.side_effect = require
        self.assertTrue(monitor.signal_backup_success(self.db, heartbeat(), self.result, sender=send))
        self.assertEqual(order, ["fresh remote authority", "HEAD"])
        self.authority.advance.assert_not_called()
        with self.db.connect() as connection:
            self.assertEqual(local_state(connection), self.state)

    def test_disabled_local_unverified_unenrolled_or_stale_snapshot_never_signals(self):
        sender = Mock()
        self.assertFalse(monitor.signal_backup_success(self.db, "", {}, sender=sender))
        bad = [{}, self.result | {"offsite_verified": False}, self.result | {"offsite_verified": 1},
               self.result | {"authority": self.expected | {"enrolled": 0}}]
        for key, value in (("generation", 1), ("marker", "b" * 32), ("lineage_id", "c" * 32), ("growth_generation", 1)):
            bad.append(self.result | {"authority": self.expected | {key: value}})
        for result in bad:
            with self.assertRaises(monitor.BackupMonitorError):
                monitor.signal_backup_success(self.db, heartbeat(), result, sender=sender)
        self.authority.require.assert_not_called()
        sender.assert_not_called()
        self.db.recovery_authority = None
        with self.assertRaises(monitor.BackupMonitorError):
            monitor.signal_backup_success(self.db, heartbeat(), self.result, sender=sender)

    def test_failed_fresh_remote_check_never_signals(self):
        sender = Mock()
        self.authority.require.side_effect = AuthorityError("Independent recovery authority is unavailable.")
        with self.assertRaises(AuthorityError):
            monitor.signal_backup_success(self.db, heartbeat(), self.result, sender=sender)
        sender.assert_not_called()

    def test_child_timeout_releases_writer_fence_after_process_is_reaped(self):
        command = [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(30)"]
        def stalled_send(url):
            monitor._send(url, command=command, timeout=0.2)
        with self.assertRaises(monitor.BackupMonitorError):
            monitor.signal_backup_success(self.db, heartbeat(), self.result, sender=stalled_send)
        with self.db.transaction() as connection:
            self.assertEqual(local_state(connection), self.state)

    @unittest.skipUnless(os.name != "nt" and importlib.util.find_spec("cryptography"), "Encrypted backup requires Unix permissions and crypto extra")
    def test_real_encrypted_offsite_readback_result_reaches_fresh_authority_gate(self):
        from connectthebots_server.recovery import RecoveryService
        from connectthebots_server.recovery_authority import RecoveryAuthority
        from tests_server.test_offsite_recovery import MemoryStorage
        storage = MemoryStorage()
        authority = RecoveryAuthority(storage, b"f" * 32, "ctb-" + "a" * 32 + "/")
        db = Database(Path(self.temp.name) / "enrolled.db", self.now, recovery_authority=authority)
        db.migrate()
        authority.enroll(db)
        settings = Settings("staging", "http://localhost", db.path, "f" * 40,
                            backup_directory=Path(self.temp.name) / "backups",
                            backup_encryption_key=base64.urlsafe_b64encode(b"f" * 32).decode())
        result = RecoveryService(db, self.now, settings).backup()
        self.assertIs(result["offsite_verified"], True)
        sender = Mock()
        self.assertTrue(monitor.signal_backup_success(db, heartbeat(), result, sender=sender))
        sender.assert_called_once_with(heartbeat())
        storage.unavailable = True
        sender.reset_mock()
        with self.assertRaises(AuthorityError):
            monitor.signal_backup_success(db, heartbeat(), result, sender=sender)
        sender.assert_not_called()

    def test_admin_only_signals_after_completed_backup_and_output_is_safe(self):
        from connectthebots_server.admin import main
        settings = Settings("staging", "http://localhost", self.db.path, "f" * 40,
                            backup_heartbeat_url=heartbeat())
        for failure in (None, RecoveryError("Backup failed."), RuntimeError(heartbeat())):
            out, err = io.StringIO(), io.StringIO()
            with patch("connectthebots_server.config.Settings.from_env", return_value=settings), \
                 patch("connectthebots_server.recovery_authority.from_settings", return_value=self.authority), \
                 patch("connectthebots_server.recovery.RecoveryService.backup", return_value=dict(self.result), side_effect=failure), \
                 patch("connectthebots_server.backup_monitor.signal_backup_success") as signal, \
                 redirect_stdout(out), redirect_stderr(err):
                code = main(["backup"])
            self.assertEqual(code, 0 if failure is None else 2)
            self.assertNotIn(heartbeat(), out.getvalue() + err.getvalue())
            if failure is None:
                signal.assert_called_once()
                self.assertTrue(json.loads(out.getvalue())["heartbeat_sent"])
            else:
                signal.assert_not_called()
        for failure in (monitor.BackupMonitorError("Backup monitoring could not be reached."), RuntimeError(heartbeat())):
            out, err = io.StringIO(), io.StringIO()
            with patch("connectthebots_server.config.Settings.from_env", return_value=settings), \
                 patch("connectthebots_server.recovery_authority.from_settings", return_value=self.authority), \
                 patch("connectthebots_server.recovery.RecoveryService.backup", return_value=dict(self.result)), \
                 patch("connectthebots_server.backup_monitor.signal_backup_success", side_effect=failure), \
                 redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(["backup"]), 2)
            self.assertEqual(out.getvalue(), "")
            self.assertNotIn(heartbeat(), err.getvalue())


if __name__ == "__main__":
    unittest.main()
