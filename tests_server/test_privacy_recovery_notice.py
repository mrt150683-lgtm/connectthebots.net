"""Public privacy copy describes configured recovery without claiming live proof."""
import base64
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from connectthebots_server.config import Settings


OPTIONAL_SERVER = all(importlib.util.find_spec(name) for name in
                      ("starlette", "httpx", "uvicorn", "jwt", "jinja2", "cryptography"))
if OPTIONAL_SERVER:
    from connectthebots_server.auth import AuthService
    from connectthebots_server.db import Database


@unittest.skipUnless(OPTIONAL_SERVER, "optional server dependencies are not installed")
class PrivacyRecoveryNoticeTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.gettempdir()) / ("ctb-privacy-" + uuid4().hex) / "fictional.db"
        self.settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://service.example.test",
            "DATABASE_PATH": str(self.path), "SESSION_SECRET": "fictional-session-secret-only-for-tests",
            "CONTACT_EMAIL": "support@example.test",
        })

    def privacy(self, settings):
        # Exercise the real autoescaping renderer without creating a database,
        # Google client or storage client. Provider effects are not render inputs.
        clock = lambda: datetime(2026, 10, 30, tzinfo=timezone.utc)
        auth = AuthService(settings, Database(self.path, clock), clock, provider=object())
        response = auth.render("privacy.html", settings=settings)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("set-cookie", response.headers)
        self.assertFalse(self.path.exists())
        return response.body.decode()

    def configured(self, notice=""):
        return replace(self.settings, offsite_b2_key_id="fictional-key-id",
                       offsite_b2_application_key="fictional-" + "storage-secret", offsite_b2_bucket_id="fictionalbucket",
                       offsite_b2_prefix="ctb-" + "a" * 32 + "/",
                       backup_encryption_key=base64.urlsafe_b64encode(b"b" * 32).decode(),
                       offsite_backup_notice=notice)

    def test_local_only_notice_retains_intact_database_requirement(self):
        text = self.privacy(self.settings)
        self.assertIn("No off-server backup service is currently configured", text)
        self.assertIn("using the trusted current database to check this", text)
        self.assertNotIn("send encrypted backup snapshots to Backblaze B2", text)
        self.assertNotIn("Off-server storage location, retention", text)

    def test_configured_storage_stays_draft_and_does_not_claim_success(self):
        settings = self.configured()
        text = self.privacy(settings)
        self.assertIn("Draft for review", text)
        self.assertIn("configured to send encrypted backup snapshots to Backblaze B2", text)
        self.assertIn("does not by itself confirm that a backup or recovery test has succeeded", text)
        self.assertIn("[Off-server storage location, retention and verified recovery arrangements", text)
        self.assertIn("checks independently stored current recovery records", text)
        self.assertNotIn("No off-server backup service is currently configured", text)
        self.assertNotIn("using the trusted current database to check this", text)
        self.assertIn("[controller identity to be confirmed]", text)
        self.assertNotIn("Our correspondence address is", text)
        for private in (settings.offsite_b2_key_id, settings.offsite_b2_application_key,
                        settings.offsite_b2_bucket_id, settings.offsite_b2_prefix, settings.backup_encryption_key):
            self.assertNotIn(private, text)

    def test_explicit_notice_is_plain_text_and_cannot_inject_markup(self):
        text = self.privacy(self.configured('Fictional retention review: <script>alert("fixture")</script> & reviewed.'))
        self.assertIn("Fictional retention review:", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("&amp; reviewed.", text)
        self.assertNotIn('<script>alert("fixture")</script>', text)
        self.assertNotIn("[Off-server storage location, retention", text)

    def test_unused_notice_does_not_claim_an_unconfigured_provider(self):
        text = self.privacy(replace(self.settings, offsite_backup_notice="Fictional dormant provider notice."))
        self.assertIn("No off-server backup service is currently configured", text)
        self.assertNotIn("Fictional dormant provider notice", text)

    def test_email_notice_is_escaped_and_requires_a_named_provider(self):
        notice = 'Fictional delivery review: <script>fixture</script> & aggregate counts.'
        settings = Settings.from_env({
            "APP_ENV": "staging", "DATABASE_PATH": str(self.path),
            "SESSION_SECRET": "fictional-session-secret-only-for-tests",
            "EMAIL_PROVIDER_NAME": "Fictional email service",
            "EMAIL_PROCESSING_NOTICE": notice,
        })
        text = self.privacy(settings)
        self.assertIn('Fictional delivery review: &lt;script&gt;fixture&lt;/script&gt; &amp; aggregate counts.', text)
        self.assertNotIn('<script>fixture</script>', text)
        self.assertIn('Email delivery is currently off', text)
        self.assertIn('Draft for review', text)
        self.assertNotIn('Fictional delivery review:', self.privacy(replace(settings, email_provider_name="")))
