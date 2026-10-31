"""Local SMTP protocol doubles; no socket or email delivery is permitted."""
from email import message_from_bytes
from types import SimpleNamespace
import smtplib
import ssl
import unittest
from unittest.mock import MagicMock, patch

from connectthebots_server.notification_transport import SMTPTransport


class SMTPTransportTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(smtp_starttls=True, smtp_host="smtp-relay.brevo.com", smtp_port=587, smtp_username="fictional-login", smtp_password="never-print-fictional-key")
        self.envelope = {"sender": "support@fictional.example.test", "recipient": "reader@fictional.example.test", "subject": "Your notification", "text": "Open your dashboard.\nhttps://fictional.example.test/app\n", "headers": {
            "List-Unsubscribe": "<https://fictional.example.test/email/unsubscribe/fictional>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click", "Message-ID": "<fictional@fictional.example.test>", "Date": "Fri, 30 Oct 2026 12:00:00 +0000"}}
        self.smtp = MagicMock()
        self.smtp.ehlo.return_value = (250, b"OK")
        self.smtp.mail.return_value = (250, b"OK")
        self.smtp.rcpt.return_value = (250, b"OK")
        self.smtp.data.return_value = (250, b"OK")
        self.transport = SMTPTransport(self.settings)

    def send(self):
        with patch("connectthebots_server.notification_transport.smtplib.SMTP", return_value=self.smtp) as constructor:
            result = self.transport.send(self.envelope)
        return result, constructor

    def test_verified_starttls_before_auth_and_plain_email_with_exact_headers(self):
        result, constructor = self.send()
        self.assertEqual(result["status"], "accepted")
        constructor.assert_called_once_with("smtp-relay.brevo.com", 587, timeout=10)
        context = self.smtp.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        methods = [call[0] for call in self.smtp.method_calls]
        self.assertEqual(methods[:4], ["ehlo", "starttls", "ehlo", "login"])
        parsed = message_from_bytes(self.smtp.data.call_args.args[0])
        self.assertEqual(parsed["List-Unsubscribe"], self.envelope["headers"]["List-Unsubscribe"])
        self.assertEqual(parsed["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertEqual(parsed.get_content_type(), "text/plain")
        self.assertEqual(len(parsed.get_all("List-Unsubscribe")), 1)

    def test_plaintext_disabled_never_connects(self):
        self.settings.smtp_starttls = False
        result, constructor = self.send()
        self.assertEqual(result, {"status": "failed", "code": "tls_required"})
        constructor.assert_not_called()

    def test_unsupported_tls_never_authenticates(self):
        self.smtp.starttls.side_effect = smtplib.SMTPNotSupportedError("server has no TLS")
        result, _ = self.send()
        self.assertEqual(result["status"], "failed")
        self.smtp.login.assert_not_called()
        self.smtp.data.assert_not_called()

    def test_failed_certificate_never_authenticates(self):
        self.smtp.starttls.side_effect = ssl.SSLCertVerificationError("certificate failure")
        result, _ = self.send()
        self.assertEqual(result["code"], "tls_unavailable")
        self.smtp.login.assert_not_called()

    def test_bad_auth_fixed_error_never_discloses_reply(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"fictional secret provider response")
        result, _ = self.send()
        self.assertEqual(result, {"status": "failed", "code": "smtp_authentication"})
        self.smtp.data.assert_not_called()

    def test_connection_failure_before_data_is_retryable(self):
        with patch("connectthebots_server.notification_transport.smtplib.SMTP", side_effect=TimeoutError("private server data")):
            result = self.transport.send(self.envelope)
        self.assertEqual(result, {"status": "retry", "code": "connection_unavailable"})

    def test_disconnect_during_data_is_uncertain(self):
        self.smtp.data.side_effect = smtplib.SMTPServerDisconnected("could have accepted")
        result, _ = self.send()
        self.assertEqual(result, {"status": "uncertain", "code": "acceptance_unknown"})

    def test_data_timeout_is_uncertain(self):
        self.smtp.data.side_effect = TimeoutError("reply lost")
        result, _ = self.send()
        self.assertEqual(result["status"], "uncertain")

    def test_explicit_data_rejection_has_known_outcome(self):
        for code, expected in ((451, "retry"), (550, "failed")):
            with self.subTest(code=code):
                self.smtp.data.side_effect = smtplib.SMTPDataError(code, b"private response")
                result, _ = self.send()
                self.assertEqual(result["status"], expected)
                self.assertNotIn("private", str(result))

    def test_recipient_refusal_does_not_transmit_body(self):
        self.smtp.rcpt.return_value = (550, b"private recipient rejected")
        result, _ = self.send()
        self.assertEqual(result["status"], "failed")
        self.smtp.data.assert_not_called()

    def test_final_250_then_failed_quit_stays_accepted(self):
        self.smtp.quit.side_effect = smtplib.SMTPServerDisconnected("quit reply lost")
        result, _ = self.send()
        self.assertEqual(result["status"], "accepted")
        self.smtp.data.assert_called_once()

    def test_unsafe_and_nonascii_envelopes_fail_before_network(self):
        for recipient in ("a@example.test\r\nBcc: victim@example.test", "a@example.test,b@example.test", "Display <a@example.test>", "λ@example.test", "a@éxample.test", "a@example.test\x00", "missing-at"):
            with self.subTest(recipient=repr(recipient)):
                self.envelope["recipient"] = recipient
                result, constructor = self.send()
                self.assertEqual(result, {"status": "failed", "code": "unsupported_address"})
                constructor.assert_not_called()

    def test_header_injection_fails_without_exposing_value(self):
        self.envelope["headers"]["List-Unsubscribe"] = "private\r\nBcc: victim@example.test"
        result, constructor = self.send()
        self.assertEqual(result, {"status": "failed", "code": "invalid_message"})
        constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
