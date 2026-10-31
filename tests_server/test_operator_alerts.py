"""Operator-only alert capabilities and existing safe delivery state machine."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
import json
import ssl
import unittest
from unittest.mock import MagicMock, patch

from connectthebots_server.notifications import NotificationError
from connectthebots_server.operator_alerts import OperatorAlertError
from connectthebots_server.sessions import token_hash
from tests_server.test_operator import OperatorFixture
from tests_server.test_notifications import FakeTransport


class OperatorAlertTests(OperatorFixture):
    def smtp(self):
        self.settings = replace(self.settings, email_mode="smtp", smtp_from_address="support@fictional.example.test")
        self.notifications.settings = self.settings
        self.alerts.settings = self.settings

    def test_outbox_separate_preferences_generic_payload_and_zero_network(self):
        receipt = self.submit()
        rows = self.rows("email_outbox")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["user_id"])
        self.assertIsNone(rows[0]["event_id"])
        self.assertEqual(rows[0]["state"], "outbox")
        payload = json.loads(rows[0]["payload_json"])
        self.assertIn(receipt["id"], payload["text"])
        for forbidden in ("Fictional private report detail", "private.example.test", "Private Google", self.postings["two"]["slug"]):
            self.assertNotIn(forbidden, json.dumps(payload))
        self.assertEqual(payload["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertEqual(self.notifications.preferences("one")["security_first_login"], True)
        fake = FakeTransport()
        with patch("smtplib.SMTP", side_effect=AssertionError("outbox must not connect")):
            self.notifications.run_due(transport=fake)
        self.assertEqual(fake.messages, [])

    def test_unsubscribe_scope_readonly_lookup_tamper_and_account_independence(self):
        self.submit()
        token = self.alerts.unsubscribe_token("abuse_report")
        before = self.rows("operator_subscriptions")
        with patch.object(self.db, "transaction", side_effect=AssertionError("GET must not mutate")):
            self.assertEqual(self.notifications.token_info(token)["kind"], "abuse_report")
        self.assertEqual(self.rows("operator_subscriptions"), before)
        for bad in (token.replace("abuse_report", "certificate_expiry"), token[:-1] + ("a" if token[-1] != "a" else "b"), "operator." + "λ" * 100):
            with self.assertRaises(NotificationError):
                self.notifications.unsubscribe(bad)
        first = self.notifications.unsubscribe(token)
        self.assertEqual(self.notifications.unsubscribe(token), first)
        self.assertEqual(self.rows("email_outbox")[0]["state"], "cancelled")
        self.submit()
        self.assertEqual(len(self.rows("operator_alerts")), 2)
        self.assertEqual(len(self.rows("email_outbox")), 1)
        self.assertTrue(self.notifications.preferences("one")["security_first_login"])
        self.alerts.set_enabled("abuse_report", True)
        self.submit()
        self.assertEqual(len(self.rows("email_outbox")), 2)

    def test_recipient_change_invalidates_old_unsubscribe_authority(self):
        token = self.alerts.unsubscribe_token("abuse_report")
        self.alerts.settings = replace(self.settings, operator_email="newoperator@fictional.example.test")
        with self.assertRaises(OperatorAlertError):
            self.alerts.token_info(token)
        new = self.alerts.unsubscribe_token("abuse_report")
        self.assertNotEqual(new, token)
        self.assertEqual(self.alerts.token_info(new)["kind"], "abuse_report")

    def test_operator_delivery_uses_shared_retry_ids_and_uncertainty_policy(self):
        self.smtp()
        self.submit()
        transport = FakeTransport([{"status": "retry", "code": "smtp_temporary"}, {"status": "accepted", "code": "accepted"}])
        self.assertEqual(self.notifications.run_due(transport=transport)["retried"], 1)
        self.now += timedelta(minutes=5)
        self.assertEqual(self.notifications.run_due(transport=transport)["accepted"], 1)
        self.assertEqual(transport.messages[0]["recipient"], self.settings.operator_email)
        self.assertEqual(transport.messages[0]["headers"]["Message-ID"], transport.messages[1]["headers"]["Message-ID"])
        self.submit()
        uncertain = FakeTransport([{"status": "uncertain", "code": "acceptance_unknown"}])
        self.assertEqual(self.notifications.run_due(transport=uncertain)["uncertain"], 1)
        self.now += timedelta(days=1)
        self.notifications.run_due(transport=uncertain)
        self.assertEqual(len(uncertain.messages), 1)

    def test_deletion_or_unsubscribe_between_claim_and_handoff_prevents_send(self):
        self.smtp()
        self.submit()
        original = self.db.transaction
        @contextmanager
        def erase_after_claim():
            with original() as connection:
                yield connection
            with self.db.connect() as connection:
                connection.execute("DELETE FROM abuse_reports WHERE EXISTS (SELECT 1 FROM email_outbox WHERE state='sending')")
        transport = FakeTransport()
        with patch.object(self.db, "transaction", erase_after_claim):
            self.notifications.run_due(transport=transport)
        self.assertEqual(transport.messages, [])
        self.assertEqual(self.rows("email_outbox"), [])

    def test_concurrent_dispatch_sends_one_operator_alert(self):
        self.smtp()
        self.submit()
        transport = FakeTransport()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.notifications.run_due(transport=transport), range(2)))
        self.assertEqual(sum(row["accepted"] for row in results), 1)
        self.assertEqual(len(transport.messages), 1)

    def test_recovery_hold_and_old_secret_suppress_operator_and_account_mail(self):
        self.smtp()
        self.submit()
        with self.db.transaction() as connection:
            self.notifications.record_event(connection, "one", "security_first_login")
            connection.execute("UPDATE recovery_state SET recovery_hold=1 WHERE singleton=1")
        transport = FakeTransport()
        self.notifications.run_due(transport=transport)
        self.assertEqual(transport.messages, [])
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=0,restore_secret_hash=? WHERE singleton=1", (token_hash(self.settings.session_secret),))
        self.notifications.run_due(transport=transport)
        self.assertEqual(transport.messages, [])

    def test_certificate_exact_threshold_weekly_dedupe_and_unknown_failure(self):
        expiry = self.now + timedelta(days=14)
        with patch.object(self.alerts, "_certificate", return_value=(expiry, "f" * 64)) as probe:
            first = self.alerts.check_certificate()
            self.assertTrue(first["alert_recorded"])
            self.alerts.check_certificate()
            probe.assert_called_with("fictional.example.test", 443)
            self.assertEqual(len(self.rows("operator_alerts")), 1)
            self.now += timedelta(days=7)
            self.alerts.check_certificate()
            self.assertEqual(len(self.rows("operator_alerts")), 2)
        with patch.object(self.alerts, "_certificate", return_value=(self.now + timedelta(days=15), "a" * 64)):
            self.assertFalse(self.alerts.check_certificate()["alert_recorded"])
        with patch.object(self.alerts, "_certificate", side_effect=ssl.SSLError("private diagnostic")):
            with self.assertRaises(OperatorAlertError) as caught:
                self.alerts.check_certificate()
            self.assertNotIn("private diagnostic", str(caught.exception))
            self.assertEqual(caught.exception.status, 503)

    def test_certificate_probe_uses_verified_context_sni_and_timeout(self):
        raw, secured, context = MagicMock(), MagicMock(), MagicMock()
        raw.__enter__.return_value = raw
        secured.__enter__.return_value = secured
        context.wrap_socket.return_value = secured
        secured.getpeercert.side_effect = [{"notAfter": "Nov 13 12:00:00 2026 GMT"}, b"fictional certificate DER"]
        with patch("socket.create_connection", return_value=raw) as connect, patch("ssl.create_default_context", return_value=context) as verified:
            self.alerts.check_certificate()
        verified.assert_called_once_with()
        connect.assert_called_once_with(("fictional.example.test", 443), timeout=10)
        context.wrap_socket.assert_called_once_with(raw, server_hostname="fictional.example.test")

    def test_later_verified_renewal_cancels_old_queued_certificate_warning(self):
        self.smtp()
        with patch.object(self.alerts, "_certificate", return_value=(self.now + timedelta(days=7), "a" * 64)):
            self.alerts.check_certificate()
        with patch.object(self.alerts, "_certificate", return_value=(self.now + timedelta(days=90), "b" * 64)):
            self.alerts.check_certificate()
        transport = FakeTransport()
        self.assertEqual(self.notifications.run_due(transport=transport)["cancelled"], 1)
        self.assertEqual(transport.messages, [])

    def test_unconfigured_operator_keeps_report_but_never_queues_delivery(self):
        self.alerts.settings = replace(self.settings, operator_email="")
        self.submit()
        self.assertEqual(len(self.rows("abuse_reports")), 1)
        self.assertEqual(len(self.rows("operator_alerts")), 1)
        self.assertEqual(self.rows("email_outbox"), [])


if __name__ == "__main__":
    unittest.main()
