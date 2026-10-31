"""Bounded SMTP hand-off with mandatory TLS and conservative uncertainty."""
from email.message import EmailMessage
from email.policy import SMTP
import re
import smtplib
import ssl

_ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")


def safe_address(value):
    return isinstance(value, str) and value.isascii() and len(value) <= 254 and _ADDRESS.fullmatch(value) is not None


class SMTPTransport:
    """A final 250 means accepted by the relay, never confirmed delivery.

    A disconnect after DATA begins can mean accepted mail with a lost reply.
    Such a hand-off is uncertain and must never be automatically repeated.
    """
    def __init__(self, settings):
        self.settings = settings

    def send(self, envelope):
        if not self.settings.smtp_starttls:
            return {"status": "failed", "code": "tls_required"}
        if not safe_address(envelope.get("recipient")) or not safe_address(envelope.get("sender")):
            return {"status": "failed", "code": "unsupported_address"}
        try:
            message = EmailMessage(policy=SMTP)
            message["From"] = envelope["sender"]
            message["To"] = envelope["recipient"]
            message["Subject"] = envelope["subject"]
            headers = envelope["headers"]
            allowed = {"List-Unsubscribe", "List-Unsubscribe-Post", "Message-ID", "Date"}
            if type(headers) is not dict or headers.keys() != allowed:
                raise ValueError
            for name, value in headers.items():
                if not isinstance(value, str) or not value.isascii() or len(value) > 2048 or any(ord(char) < 32 for char in value):
                    raise ValueError
                message[name] = value
            message.set_content(envelope["text"], charset="utf-8", cte="quoted-printable")
            wire = message.as_bytes()
        except (KeyError, TypeError, ValueError, UnicodeError):
            return {"status": "failed", "code": "invalid_message"}
        smtp, data_started, accepted = None, False, False
        try:
            smtp = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10)
            code, _ = smtp.ehlo()
            if code != 250:
                return self._refused(code)
            smtp.starttls(context=ssl.create_default_context())
            code, _ = smtp.ehlo()
            if code != 250:
                return self._refused(code)
            smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            code, _ = smtp.mail(envelope["sender"])
            if code != 250:
                return self._refused(code)
            code, _ = smtp.rcpt(envelope["recipient"])
            if code not in (250, 251):
                return self._refused(code)
            # Persisted queue state is already sending before entering here.
            # Treat even a partial DATA write with no final answer as uncertain.
            data_started = True
            code, _ = smtp.data(wire)
            if code != 250:
                return self._refused(code)
            accepted = True
            return {"status": "accepted", "code": "accepted"}
        except smtplib.SMTPAuthenticationError:
            return {"status": "failed", "code": "smtp_authentication"}
        except (ssl.SSLError, smtplib.SMTPNotSupportedError):
            return {"status": "failed", "code": "tls_unavailable"}
        except smtplib.SMTPResponseException as error:
            return self._refused(error.smtp_code)
        except (OSError, smtplib.SMTPException):
            return {"status": "uncertain" if data_started else "retry", "code": "acceptance_unknown" if data_started else "connection_unavailable"}
        finally:
            if smtp is not None:
                try:
                    if accepted:
                        smtp.quit()
                    else:
                        smtp.close()
                except (OSError, smtplib.SMTPException):
                    # Losing QUIT after an acknowledged DATA must not resend.
                    pass

    @staticmethod
    def _refused(code):
        return {"status": "retry" if 400 <= code < 500 else "failed", "code": "smtp_temporary" if 400 <= code < 500 else "smtp_rejected"}
