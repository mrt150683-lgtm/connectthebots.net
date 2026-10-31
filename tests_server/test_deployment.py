"""Deployment safety checks: configuration, artifact integrity and dry runs.

These tests do not contact a server. Ubuntu staging separately exercises nginx,
systemd and installation of the wheel; source assertions alone are not that proof.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "site" / "deploy"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure_app = load("configure_app")
verify_bundle = load("verify_bundle")


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "app.env"

    def create(self):
        configure_app.configure(self.path, DEPLOY / "app.env.example", "staging", "http://localhost")

    def test_initial_secret_is_private_and_stable_on_reinstall(self):
        self.create()
        first = self.path.read_bytes()
        values = configure_app.read_env(self.path)
        self.assertGreaterEqual(len(values["SESSION_SECRET"]), 32)
        self.assertEqual(len(base64.urlsafe_b64decode(values["BACKUP_ENCRYPTION_KEY"])), 32)
        self.assertEqual(values["BACKUP_DIRECTORY"], "/var/backups/connectthebots")
        self.assertEqual(values["OPERATOR_EMAIL"], "")
        self.assertEqual(values["SIGNUPS"], "closed")
        self.assertEqual(values["EMAIL_MODE"], "outbox")
        self.assertEqual(values["ACCOUNT_SIGNIN_ENABLED"], "false")
        self.assertEqual(values["LEGAL_PAGES_APPROVED"], "false")
        self.create()
        self.assertEqual(first, self.path.read_bytes())
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_existing_target_mismatch_is_refused_without_rewriting(self):
        self.create()
        first = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "differs"):
            configure_app.configure(self.path, DEPLOY / "app.env.example", "production", "https://example.test")
        self.assertEqual(first, self.path.read_bytes())

    def test_existing_missing_or_invalid_backup_key_is_never_silently_replaced(self):
        self.create()
        original = self.path.read_text(encoding="utf-8")
        key = configure_app.read_env(self.path)["BACKUP_ENCRYPTION_KEY"]
        for replacement in ("", "not-base64", base64.urlsafe_b64encode(b"short").decode()):
            with self.subTest(replacement=replacement):
                self.path.write_text(original.replace("BACKUP_ENCRYPTION_KEY=" + key, "BACKUP_ENCRYPTION_KEY=" + replacement), encoding="utf-8")
                before = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "BACKUP_ENCRYPTION_KEY"):
                    self.create()
                self.assertEqual(self.path.read_bytes(), before)

    def test_backup_path_and_operator_recipient_are_bounded_without_values_in_errors(self):
        self.create()
        values = configure_app.read_env(self.path)
        for key, value in (("BACKUP_DIRECTORY", "/tmp/public-backups"),
                           ("OPERATOR_EMAIL", "first@example.test,second@example.test")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key) as failure:
                configure_app.validate(values | {key: value}, "staging", "http://localhost")
            self.assertNotIn(value, str(failure.exception))

    def offsite(self):
        return {"OFFSITE_B2_KEY_ID": "fictional-key", "OFFSITE_B2_APPLICATION_KEY": "fictional-private-value",
                "OFFSITE_B2_BUCKET_ID": "fictional-bucket", "OFFSITE_B2_PREFIX": "ctb-" + "a" * 32 + "/"}

    def test_offsite_preflight_requires_complete_scoped_configuration(self):
        self.create()
        values = configure_app.read_env(self.path)
        configure_app.validate(values | self.offsite(), "staging", "http://localhost")
        for key in self.offsite():
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "OFFSITE_B2"):
                configure_app.validate(values | self.offsite() | {key: ""}, "staging", "http://localhost")
        for key, value in (("OFFSITE_B2_PREFIX", "someone@example.test/"), ("OFFSITE_B2_BUCKET_ID", "invalid/bucket")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key) as failure:
                configure_app.validate(values | self.offsite() | {key: value}, "staging", "http://localhost")
            self.assertNotIn(value, str(failure.exception))

    def test_approved_offsite_preflight_requires_backup_notice(self):
        self.create()
        values = configure_app.read_env(self.path) | self.offsite() | {
            "LEGAL_PAGES_APPROVED": "true", "LEGAL_CONTROLLER_NAME": "Fictional Cooperative",
            "CONTACT_EMAIL": "privacy@example.test", "VPS_PROVIDER_NAME": "Fictional host",
            "EMAIL_PROVIDER_NAME": "Fictional sender", "LEGAL_TRANSFER_DETAILS": "Fictional reviewed details"}
        for notice in ("", " "):
            with self.subTest(notice=notice), self.assertRaisesRegex(ValueError, "OFFSITE_BACKUP_NOTICE"):
                configure_app.validate(values | {"OFFSITE_BACKUP_NOTICE": notice}, "staging", "http://localhost")
        configure_app.validate(values | {"OFFSITE_BACKUP_NOTICE": "Fictional reviewed backup details"}, "staging", "http://localhost")

    def test_backup_network_override_is_opt_in_credential_free_and_reversible(self):
        target = Path(self.temp.name) / "backup.service.d" / "40-ctb-offsite-network.conf"
        configure_app.configure_backup_network(target, {})
        self.assertFalse(target.exists())
        configure_app.configure_backup_network(target, self.offsite())
        content = target.read_text()
        self.assertEqual(content, configure_app.BACKUP_NETWORK_DROP_IN)
        self.assertIn("PrivateNetwork=no\nRestrictAddressFamilies=\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", content)
        for value in self.offsite().values():
            self.assertNotIn(value, content)
        if os.name != "nt":
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        unrelated = target.with_name("90-operator.conf")
        unrelated.write_text("# Separate operator settings\n")
        configure_app.configure_backup_network(target, self.offsite())
        configure_app.configure_backup_network(target, {})
        self.assertFalse(target.exists())
        self.assertEqual(unrelated.read_text(), "# Separate operator settings\n")

    def test_backup_network_override_refuses_foreign_files_and_partial_settings(self):
        target = Path(self.temp.name) / "40-ctb-offsite-network.conf"
        target.write_text("# Operator-owned override\n")
        for values in ({}, self.offsite()):
            with self.assertRaisesRegex(ValueError, "not the managed file"):
                configure_app.configure_backup_network(target, values)
            self.assertEqual(target.read_text(), "# Operator-owned override\n")
        target.unlink()
        configure_app.configure_backup_network(target, self.offsite())
        with self.assertRaisesRegex(ValueError, "required together"):
            configure_app.configure_backup_network(target, self.offsite() | {"OFFSITE_B2_APPLICATION_KEY": ""})
        self.assertEqual(target.read_text(), configure_app.BACKUP_NETWORK_DROP_IN)

    @unittest.skipIf(os.name == "nt", "Unix deployment path symlink checks")
    def test_backup_network_override_refuses_symlinks(self):
        original = Path(self.temp.name) / "original.conf"
        original.write_text(configure_app.BACKUP_NETWORK_DROP_IN)
        target = Path(self.temp.name) / "override.conf"
        target.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "symlink"):
            configure_app.configure_backup_network(target, {})
        self.assertTrue(original.exists())
        directory = Path(self.temp.name) / "linked"
        directory.symlink_to(self.temp.name, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            configure_app.configure_backup_network(directory / "new.conf", self.offsite())

    def test_cli_backup_network_override_validates_before_writing(self):
        import sys
        self.create()
        original = self.path.read_text()
        target = Path(self.temp.name) / "backup.service.d" / "40-ctb-offsite-network.conf"
        command = [sys.executable, str(DEPLOY / "configure_app.py"), "--env-file", str(self.path),
                   "--environment", "staging", "--base-url", "http://localhost", "--backup-network-drop-in", str(target)]
        partial = original.replace("OFFSITE_B2_KEY_ID=", "OFFSITE_B2_KEY_ID=fictional-key")
        self.path.write_text(partial)
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertFalse(target.exists())
        content = original
        for key, value in self.offsite().items():
            content = content.replace(key + "=", key + "=" + value)
        self.path.write_text(content)
        run = subprocess.run(command + ["--check-only"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertFalse(target.exists())
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(target.exists())
        for value in self.offsite().values():
            self.assertNotIn(value, run.stdout + run.stderr)
        self.path.write_text(original)
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(target.exists())

    def test_shell_substitution_duplicate_and_multiline_syntax_are_refused(self):
        for content in ("SESSION_SECRET=$(touch injected)\n", "SESSION_SECRET=`id`\n", "SIGNUPS=closed\nSIGNUPS=open\n", "X=one two\n", "X=one\x00two\n"):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    configure_app.read_env(self.path)

    def test_backup_timer_requires_both_heartbeat_and_offsite_and_is_reversible(self):
        target = Path(self.temp.name) / "backup.timer.d" / "40-ctb-heartbeat-schedule.conf"
        url = "https://sm.hetrixtools.net/hb/?s=" + "fictional" * 4
        for values in ({}, self.offsite()):
            configure_app.configure_backup_timer(target, values)
            self.assertFalse(target.exists())
        with self.assertRaisesRegex(ValueError, "OFFSITE_B2"):
            configure_app.configure_backup_timer(target, {"BACKUP_HEARTBEAT_URL": url})
        values = self.offsite() | {"BACKUP_HEARTBEAT_URL": url}
        configure_app.configure_backup_timer(target, values)
        self.assertEqual(target.read_text(), configure_app.BACKUP_TIMER_DROP_IN)
        self.assertIn("OnCalendar=\nOnCalendar=hourly\nRandomizedDelaySec=0", target.read_text())
        self.assertNotIn(url, target.read_text())
        if os.name != "nt":
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        for bad_url in (url + "&extra=x", url.replace(".net", ".net:443"), url.replace("https:", "http:")):
            with self.assertRaisesRegex(ValueError, "BACKUP_HEARTBEAT_URL") as caught:
                configure_app.configure_backup_timer(target, values | {"BACKUP_HEARTBEAT_URL": bad_url})
            self.assertNotIn(bad_url, str(caught.exception))
        configure_app.configure_backup_timer(target, self.offsite())
        self.assertFalse(target.exists())
        target.write_text("# Separate operator schedule\n")
        for values in ({}, values):
            with self.assertRaisesRegex(ValueError, "not the managed file"):
                configure_app.configure_backup_timer(target, values)
        self.assertEqual(target.read_text(), "# Separate operator schedule\n")

    def test_previous_owned_timer_migrates_atomically_and_disabled_removes_either_version(self):
        target = Path(self.temp.name) / "40-ctb-heartbeat-schedule.conf"
        values = self.offsite() | {"BACKUP_HEARTBEAT_URL": "https://sm.hetrixtools.net/hb/?s=" + "fictional" * 4}
        target.write_text(configure_app.LEGACY_BACKUP_TIMER_DROP_IN)
        configure_app.configure_backup_timer(target, values)
        self.assertEqual(target.read_text(), configure_app.BACKUP_TIMER_DROP_IN)
        modified = target.stat().st_mtime_ns
        configure_app.configure_backup_timer(target, values)
        self.assertEqual(target.stat().st_mtime_ns, modified)
        for owned in (configure_app.LEGACY_BACKUP_TIMER_DROP_IN, configure_app.BACKUP_TIMER_DROP_IN):
            target.write_text(owned)
            configure_app.configure_backup_timer(target, self.offsite())
            self.assertFalse(target.exists())
        for foreign in (configure_app.LEGACY_BACKUP_TIMER_DROP_IN + "# operator setting\n",
                        configure_app.LEGACY_BACKUP_TIMER_DROP_IN.replace("00/15", "00/30")):
            target.write_text(foreign)
            for settings in (values, {}):
                with self.assertRaisesRegex(ValueError, "not the managed file"):
                    configure_app.configure_backup_timer(target, settings)
                self.assertEqual(target.read_text(), foreign)

    def test_failed_timer_migration_preserves_previous_owned_file(self):
        from unittest.mock import patch
        target = Path(self.temp.name) / "40-ctb-heartbeat-schedule.conf"
        target.write_text(configure_app.LEGACY_BACKUP_TIMER_DROP_IN)
        values = self.offsite() | {"BACKUP_HEARTBEAT_URL": "https://sm.hetrixtools.net/hb/?s=" + "fictional" * 4}
        with patch.object(configure_app.os, "replace", side_effect=OSError("fictional failure")):
            with self.assertRaises(OSError):
                configure_app.configure_backup_timer(target, values)
        self.assertEqual(target.read_text(), configure_app.LEGACY_BACKUP_TIMER_DROP_IN)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [target])

    @unittest.skipIf(os.name == "nt", "Unix deployment path symlink checks")
    def test_backup_timer_refuses_symlink_without_removing_target(self):
        original = Path(self.temp.name) / "original.conf"
        original.write_text(configure_app.BACKUP_TIMER_DROP_IN)
        target = Path(self.temp.name) / "override.conf"
        target.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "symlink"):
            configure_app.configure_backup_timer(target, {})
        self.assertTrue(original.exists())

    def test_cli_backup_timer_validates_before_writing_or_check_only(self):
        import sys
        self.create()
        original = self.path.read_text()
        target = Path(self.temp.name) / "backup.timer.d" / "40-ctb-heartbeat-schedule.conf"
        command = [sys.executable, str(DEPLOY / "configure_app.py"), "--env-file", str(self.path),
                   "--environment", "staging", "--base-url", "http://localhost", "--backup-timer-drop-in", str(target)]
        url = "https://sm.hetrixtools.net/hb/?s=" + "fictional" * 4
        self.path.write_text(original.replace("BACKUP_HEARTBEAT_URL=", "BACKUP_HEARTBEAT_URL=" + url))
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertFalse(target.exists())
        content = self.path.read_text()
        for key, value in self.offsite().items():
            content = content.replace(key + "=", key + "=" + value)
        self.path.write_text(content)
        run = subprocess.run(command + ["--check-only"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertFalse(target.exists())
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(target.exists())
        self.assertNotIn(url, run.stdout + run.stderr)
        self.path.write_text(original)
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(target.exists())

    def test_quoted_spaces_and_literal_comments_parse(self):
        self.path.write_text('SMTP_PASSWORD="fictional value#here"\n', encoding="utf-8")
        self.assertEqual(configure_app.read_env(self.path)["SMTP_PASSWORD"], "fictional value#here")

    def test_staging_never_sends_email(self):
        self.create()
        values = configure_app.read_env(self.path)
        with self.assertRaisesRegex(ValueError, "Staging requires"):
            configure_app.validate(values | {"EMAIL_MODE": "smtp"}, "staging", "http://localhost")

    def test_staging_signin_requires_configured_google_credentials(self):
        self.create()
        values = configure_app.read_env(self.path) | {"ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open"}
        with self.assertRaisesRegex(ValueError, "GOOGLE_CLIENT_ID"):
            configure_app.validate(values, "staging", "http://localhost")
        values |= {"GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-test-only-secret"}
        configure_app.validate(values, "staging", "http://localhost")

    def test_signup_mode_alone_does_not_require_or_enable_signin(self):
        self.create()
        values = configure_app.read_env(self.path) | {"SIGNUPS": "open"}
        configure_app.validate(values, "staging", "http://localhost")

    def test_old_foundation_environment_keeps_both_gates_disabled(self):
        self.create()
        values = configure_app.read_env(self.path)
        values.pop("ACCOUNT_SIGNIN_ENABLED")
        values.pop("LEGAL_PAGES_APPROVED")
        configure_app.validate(values, "staging", "http://localhost")

    def test_security_contact_expiry_is_explicit_preserved_and_syntax_checked(self):
        self.create()
        values = configure_app.read_env(self.path)
        self.assertEqual(values["SECURITY_CONTACT_EXPIRES"], "")
        values.pop("SECURITY_CONTACT_EXPIRES")
        configure_app.validate(values, "staging", "http://localhost")
        for expiry in ("2027-04-30T12:00:00Z", "2020-01-01T00:00:00Z"):
            configure_app.validate(values | {"SECURITY_CONTACT_EXPIRES": expiry}, "staging", "http://localhost")
        for expiry in ("2027-02-30T12:00:00Z", "2027-04-30", "2027-04-30T12:00:00+00:00", "fictional-private-value"):
            with self.subTest(expiry=expiry), self.assertRaisesRegex(ValueError, "SECURITY_CONTACT_EXPIRES") as caught:
                configure_app.validate(values | {"SECURITY_CONTACT_EXPIRES": expiry}, "staging", "http://localhost")
            self.assertNotIn(expiry, str(caught.exception))
        content = self.path.read_text(encoding="utf-8").replace("SECURITY_CONTACT_EXPIRES=", "SECURITY_CONTACT_EXPIRES=2027-04-30T12:00:00Z")
        self.path.write_text(content, encoding="utf-8")
        self.create()
        self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_production_preflight_lists_only_missing_variable_names(self):
        self.create()
        values = configure_app.read_env(self.path) | {"APP_ENV": "production", "PUBLIC_BASE_URL": "https://example.test", "SIGNUPS": "open", "ACCOUNT_SIGNIN_ENABLED": "true"}
        with self.assertRaisesRegex(ValueError, "GOOGLE_CLIENT_ID") as failure:
            configure_app.validate(values, "production", "https://example.test")
        self.assertNotIn(values["SESSION_SECRET"], str(failure.exception))

    def test_production_signin_requires_approved_complete_legal_pages(self):
        self.create()
        values = configure_app.read_env(self.path) | {"APP_ENV": "production", "PUBLIC_BASE_URL": "https://example.test", "SIGNUPS": "open", "ACCOUNT_SIGNIN_ENABLED": "true", "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret"}
        with self.assertRaisesRegex(ValueError, "LEGAL_PAGES_APPROVED"):
            configure_app.validate(values, "production", "https://example.test")
        values["LEGAL_PAGES_APPROVED"] = "true"
        with self.assertRaisesRegex(ValueError, "LEGAL_CONTROLLER_NAME"):
            configure_app.validate(values, "production", "https://example.test")
        values |= {"LEGAL_CONTROLLER_NAME": "Fictional Example Cooperative", "LEGAL_CONTACT_ADDRESS": "Fictional correspondence address", "CONTACT_EMAIL": "privacy@example.test", "VPS_PROVIDER_NAME": "Fictional host", "EMAIL_PROVIDER_NAME": "Fictional mail service"}
        with self.assertRaisesRegex(ValueError, "LEGAL_TRANSFER_DETAILS"):
            configure_app.validate(values, "production", "https://example.test")
        values["LEGAL_TRANSFER_DETAILS"] = "Fictional test transfer disclosure; not a real provider statement."
        # Sign-in can be verified against the outbox before mail delivery exists.
        configure_app.validate(values, "production", "https://example.test")
        # Address is optional; accurate controller/contact and approval remain required.
        values["LEGAL_CONTACT_ADDRESS"] = ""
        configure_app.validate(values, "production", "https://example.test")
        values["CONTACT_EMAIL"] = "invalid"
        with self.assertRaisesRegex(ValueError, "CONTACT_EMAIL"):
            configure_app.validate(values, "production", "https://example.test")

    def test_production_still_requires_https_and_valid_gate_values(self):
        self.create()
        values = configure_app.read_env(self.path) | {"APP_ENV": "production"}
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            configure_app.validate(values, "production", "http://localhost")
        for key in ("ACCOUNT_SIGNIN_ENABLED", "LEGAL_PAGES_APPROVED"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                configure_app.validate(configure_app.read_env(self.path) | {key: "yes"}, "staging", "http://localhost")

    def test_cli_does_not_print_existing_secret_on_failure(self):
        self.create()
        values = configure_app.read_env(self.path)
        import sys
        run = subprocess.run([sys.executable, str(DEPLOY / "configure_app.py"), "--env-file", str(self.path), "--environment", "production", "--base-url", "https://example.test", "--check-only"], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn(values["SESSION_SECRET"], run.stdout + run.stderr)
        self.assertNotIn(values["BACKUP_ENCRYPTION_KEY"], run.stdout + run.stderr)

    def test_smtp_preflight_refuses_insecure_or_ambiguous_configuration(self):
        self.create()
        values = configure_app.read_env(self.path) | {
            "APP_ENV": "production", "PUBLIC_BASE_URL": "https://example.test", "EMAIL_MODE": "smtp",
            "SMTP_HOST": "mail.example.test", "SMTP_USERNAME": "fictional-user",
            "SMTP_PASSWORD": "fictional-password", "SMTP_FROM_ADDRESS": "hello@example.test",
            "OPERATOR_EMAIL": "operator@example.test",
        }
        configure_app.validate(values, "production", "https://example.test")
        with self.assertRaisesRegex(ValueError, "OPERATOR_EMAIL"):
            configure_app.validate(values | {"OPERATOR_EMAIL": ""}, "production", "https://example.test")
        for key, value in (("SMTP_STARTTLS", "false"), ("SMTP_HOST", "mail.example.test:587"),
                           ("SMTP_FROM_ADDRESS", "hello@example.test,other@example.test"),
                           ("SMTP_PORT", "65536")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                configure_app.validate(values | {key: value}, "production", "https://example.test")


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.wheel = self.path / "agentmatch-0.1.0-py3-none-any.whl"
        self.wheel.write_bytes(b"fictional test artifact")
        (self.path / "requirements-server.lock").write_text("example==1.0 --hash=sha256:" + "0" * 64)
        self.manifest = self.path / "SHA256SUMS"
        self.manifest.write_text("".join(hashlib.sha256(item.read_bytes()).hexdigest() + "  " + item.name + "\n" for item in (self.wheel, self.path / "requirements-server.lock")), encoding="utf-8")

    def test_verified_bundle_identity_binds_wheel_and_lock(self):
        wheel, release = verify_bundle.verify(self.path)
        self.assertEqual(wheel, self.wheel)
        self.assertEqual(len(release), 24)
        (self.path / "requirements-server.lock").write_text("changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            verify_bundle.verify(self.path)

    def test_manifest_cannot_read_a_path_outside_bundle(self):
        self.manifest.write_text("0" * 64 + "  ../secret\n")
        with self.assertRaisesRegex(ValueError, "Invalid bundle"):
            verify_bundle.verify(self.path)

    def test_missing_duplicate_or_unexpected_entries_are_refused(self):
        original = self.manifest.read_text()
        for text in (original.splitlines()[0] + "\n", original + original.splitlines()[0] + "\n", original + "0" * 64 + "  extra.whl\n"):
            self.manifest.write_text(text)
            with self.assertRaises(ValueError):
                verify_bundle.verify(self.path)

    def test_multiple_wheels_are_refused(self):
        (self.path / "agentmatch-0.2.0-py3-none-any.whl").write_bytes(b"other")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            verify_bundle.verify(self.path)


class ScriptTests(unittest.TestCase):
    def test_systemd_confines_writes_and_drops_privileges(self):
        unit = (DEPLOY / "connectthebots-app.service").read_text()
        for directive in ("User=ctb", "NoNewPrivileges=yes", "ProtectSystem=strict", "ReadWritePaths=/var/lib/connectthebots", "ProtectHome=yes", "PrivateTmp=yes", "CapabilityBoundingSet=", "EnvironmentFile=/etc/connectthebots/app.env"):
            self.assertIn(directive, unit)

    def test_dynamic_proxy_does_not_log_authorization_requests(self):
        script = (DEPLOY / "server-setup.sh").read_text()
        self.assertNotIn('"$request"', script)
        self.assertNotIn("$http_referer", script)
        self.assertIn("access_log off;", script)
        self.assertIn("client_max_body_size 64k;", script)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", script)
        self.assertIn("email(/|$)", script)
        # The public leaderboard and personal share links are app pages.
        self.assertIn("|leaderboard$|r/)", script)

    def test_certbot_only_obtains_certificates_and_never_edits_the_site(self):
        # certbot --nginx --redirect clashed with our own www redirect and rolled HTTPS back on a rerun.
        script = (DEPLOY / "server-setup.sh").read_text()
        self.assertIn("certbot certonly --nginx", script)
        self.assertNotIn("--redirect", script)
        self.assertIn("sed -i '/^installer *=/d'", script)
        tls = script.split("cat > /etc/nginx/sites-available/connectthebots <<NGINX", 2)[1]
        self.assertIn("listen 443 ssl;", tls)
        self.assertEqual(tls.count("return 308 https://$DOMAIN\\$request_uri;"), 2)
        self.assertIn('add_header Strict-Transport-Security "max-age=31536000" always;', tls)
        self.assertEqual(script.count("write_site\n"), 2)

    def test_security_contact_proxy_is_exact_and_preserves_request_boundary(self):
        script = (DEPLOY / "server-setup.sh").read_text()
        route = script.split("location = /.well-known/security.txt {", 1)[1].split("}", 1)[0]
        for directive in ("proxy_pass http://127.0.0.1:8100;", "proxy_set_header Host $host;",
                          "proxy_set_header X-Forwarded-Proto $scheme;", 'proxy_set_header Forwarded "";',
                          "access_log off;", "error_log /dev/null;", "client_max_body_size 64k;"):
            self.assertIn(directive, route)
        self.assertNotIn("location /.well-known", script)

    def test_notification_timer_uses_confined_sequential_worker(self):
        unit = (DEPLOY / "connectthebots-notifications.service").read_text()
        for directive in ("Type=oneshot", "User=ctb", "NoNewPrivileges=yes", "ProtectSystem=strict",
                          "ReadWritePaths=/var/lib/connectthebots", "EnvironmentFile=/etc/connectthebots/app.env",
                          "-m connectthebots_server.notifications tick --limit 20"):
            self.assertIn(directive, unit)
        timer = (DEPLOY / "connectthebots-notifications.timer").read_text()
        self.assertIn("OnUnitInactiveSec=1min", timer)
        self.assertIn("Unit=connectthebots-notifications.service", timer)

    def test_backup_and_certificate_workers_have_separate_privileges_and_schedules(self):
        backup = (DEPLOY / "connectthebots-backup.service").read_text()
        certificate = (DEPLOY / "connectthebots-certificate.service").read_text()
        for unit in (backup, certificate):
            for directive in ("Type=oneshot", "User=ctb", "UMask=0077", "NoNewPrivileges=yes",
                              "ProtectSystem=strict", "ProtectHome=yes", "CapabilityBoundingSet=",
                              "EnvironmentFile=/etc/connectthebots/app.env"):
                self.assertIn(directive, unit)
        self.assertIn("ReadWritePaths=/var/lib/connectthebots /var/backups/connectthebots", backup)
        self.assertIn("PrivateNetwork=yes", backup)
        self.assertIn("RestrictAddressFamilies=AF_UNIX\n", backup)
        setup = (DEPLOY / "app-setup.sh").read_text()
        self.assertIn("--backup-network-drop-in /etc/systemd/system/connectthebots-backup.service.d/40-ctb-offsite-network.conf", setup)
        self.assertLess(setup.index("--backup-network-drop-in"), setup.index("systemctl daemon-reload"))
        self.assertIn("--backup-timer-drop-in /etc/systemd/system/connectthebots-backup.timer.d/40-ctb-heartbeat-schedule.conf", setup)
        self.assertIn("-m connectthebots_server.admin backup", backup)
        self.assertNotIn("/var/backups/connectthebots", certificate)
        self.assertIn("ExecCondition=", certificate)
        self.assertIn("startswith('https://')", certificate)
        self.assertIn("-m connectthebots_server.admin certificate check", certificate)
        self.assertIn("OnCalendar=*-*-* 02:15:00", (DEPLOY / "connectthebots-backup.timer").read_text())
        self.assertIn("OnCalendar=weekly", (DEPLOY / "connectthebots-certificate.timer").read_text())

    @unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "Bash input execution runs on Linux")
    def test_bash_parse_dry_run_and_injection_rejection(self):
        for script in ("deploy.sh", "app-setup.sh", "server-setup.sh"):
            subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True, capture_output=True)
        run = subprocess.run(["bash", str(DEPLOY / "deploy.sh"), "operator@example.test", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("No files uploaded", run.stdout)
        for args in (("-oProxyCommand=id", "--dry-run"), ("operator@example.test", "--domain", "example.test;id", "--dry-run"), ("operator@example.test", "--email", "a@b;id", "--dry-run")):
            run = subprocess.run(["bash", str(DEPLOY / "deploy.sh"), *args], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)

    @unittest.skipUnless(os.name == "nt", "Windows uploader execution runs on Windows")
    def test_windows_dry_run_and_injection_rejection(self):
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            self.skipTest("PowerShell unavailable")
        command = [executable, "-NoProfile", "-File", str(DEPLOY / "deploy.ps1")]
        run = subprocess.run(command + ["-Server", "operator@example.test", "-DryRun"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("No files uploaded", run.stdout)
        for args in (("-Server", "operator@example.test;id"), ("-Server", "operator@example.test", "-Domain", "example.test;id")):
            run = subprocess.run(command + [*args, "-DryRun"], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)


if __name__ == "__main__":
    unittest.main()


class CaseInsensitiveCheckoutTests(unittest.TestCase):
    def test_no_two_paths_differ_only_by_case(self):
        # Windows clones collide on such pairs (DEPLOY.ps1 vs deploy.ps1).
        import subprocess
        root = Path(__file__).resolve().parent.parent
        try:
            names = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.split("\n")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        seen = {}
        for name in filter(None, names):
            self.assertNotIn(name.lower(), seen, f"{name} collides with {seen.get(name.lower())} on Windows")
            seen[name.lower()] = name
