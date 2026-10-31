"""Validated environment configuration. Never include secret values in errors."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import os
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """A configuration variable is missing or invalid; messages name keys only."""


def _text(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(name, default)
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ConfigurationError(f"{name} must be a single-line value")
    return value


@dataclass(frozen=True)
class Settings:
    app_env: str
    public_base_url: str
    database_path: Path
    session_secret: str = field(repr=False)
    signups: str = "closed"
    email_mode: str = "outbox"
    google_client_id: str = ""
    google_client_secret: str = field(default="", repr=False)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = field(default="", repr=False)
    smtp_password: str = field(default="", repr=False)
    smtp_from_address: str = ""
    smtp_starttls: bool = True
    account_signin_enabled: bool = False
    legal_pages_approved: bool = False
    legal_controller_name: str = ""
    legal_contact_address: str = ""
    contact_email: str = ""
    security_contact_expires: datetime | None = None
    vps_provider_name: str = ""
    email_provider_name: str = ""
    email_processing_notice: str = ""
    legal_transfer_details: str = ""
    operator_email: str = ""
    backup_directory: Path | None = None
    backup_encryption_key: str = field(default="", repr=False)
    offsite_b2_key_id: str = field(default="", repr=False)
    offsite_b2_application_key: str = field(default="", repr=False)
    offsite_b2_bucket_id: str = ""
    offsite_b2_prefix: str = ""
    offsite_backup_notice: str = ""
    backup_heartbeat_url: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Use only environment values; never discover or load a dotenv file.

        The optional mapping is an explicit test seam. Production entry points
        call this without arguments, after systemd loads the protected env file.
        Missing Google/SMTP settings do not prevent the closed foundation from
        starting. Launch configuration is checked separately before activation.
        """
        env = os.environ if env is None else env
        app_env = _text(env, "APP_ENV", "production")
        if app_env not in {"production", "staging", "development"}:
            raise ConfigurationError("APP_ENV must be production, staging or development")
        base_url = _text(env, "PUBLIC_BASE_URL", "https://connectthebots.net")
        try:
            url = urlsplit(base_url)
            port = url.port
        except ValueError:
            raise ConfigurationError("PUBLIC_BASE_URL must be an absolute HTTP(S) origin") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or any(c.isspace() for c in base_url)
            or "\\" in base_url
            or not re.fullmatch(r"[a-zA-Z0-9.:-]+", url.hostname)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ConfigurationError("PUBLIC_BASE_URL must be an absolute HTTP(S) origin")
        if app_env == "production" and url.scheme != "https":
            raise ConfigurationError("PUBLIC_BASE_URL must use HTTPS in production")
        db_path = Path(_text(env, "DATABASE_PATH", "/var/lib/connectthebots/app.db"))
        if not db_path.is_absolute():
            raise ConfigurationError("DATABASE_PATH must be an absolute filesystem path")
        secret = _text(env, "SESSION_SECRET")
        if len(secret) < 32 or secret != secret.strip():
            raise ConfigurationError("SESSION_SECRET must contain at least 32 characters with no surrounding whitespace")
        signups = _text(env, "SIGNUPS", "closed")
        if signups not in {"open", "invite", "closed"}:
            raise ConfigurationError("SIGNUPS must be open, invite or closed")
        email_mode = _text(env, "EMAIL_MODE", "outbox")
        if email_mode not in {"outbox", "smtp"}:
            raise ConfigurationError("EMAIL_MODE must be outbox or smtp")
        if app_env != "production" and email_mode != "outbox":
            raise ConfigurationError("EMAIL_MODE must be outbox outside production")
        raw_port = _text(env, "SMTP_PORT", "587")
        if not raw_port.isascii() or not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
            raise ConfigurationError("SMTP_PORT must be an integer from 1 to 65535")
        starttls = _text(env, "SMTP_STARTTLS", "true")
        if starttls not in {"true", "false"}:
            raise ConfigurationError("SMTP_STARTTLS must be true or false")
        enabled = _text(env, "ACCOUNT_SIGNIN_ENABLED", "false")
        approved = _text(env, "LEGAL_PAGES_APPROVED", "false")
        if enabled not in {"true", "false"} or approved not in {"true", "false"}:
            raise ConfigurationError("ACCOUNT_SIGNIN_ENABLED and LEGAL_PAGES_APPROVED must be true or false")
        if app_env == "production" and enabled == "true" and approved != "true":
            raise ConfigurationError("LEGAL_PAGES_APPROVED must be true before production sign-in is enabled")
        raw_expiry = _text(env, "SECURITY_CONTACT_EXPIRES")
        security_expiry = None
        if raw_expiry:
            try:
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", raw_expiry):
                    raise ValueError
                security_expiry = datetime.strptime(raw_expiry, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                raise ConfigurationError("SECURITY_CONTACT_EXPIRES must be a UTC date in YYYY-MM-DDTHH:MM:SSZ format") from None
        result = cls(
            app_env=app_env,
            public_base_url=base_url.rstrip("/"),
            database_path=db_path,
            session_secret=secret,
            signups=signups,
            email_mode=email_mode,
            google_client_id=_text(env, "GOOGLE_CLIENT_ID"),
            google_client_secret=_text(env, "GOOGLE_CLIENT_SECRET"),
            smtp_host=_text(env, "SMTP_HOST"),
            smtp_port=int(raw_port),
            smtp_username=_text(env, "SMTP_USERNAME"),
            smtp_password=_text(env, "SMTP_PASSWORD"),
            smtp_from_address=_text(env, "SMTP_FROM_ADDRESS"),
            smtp_starttls=starttls == "true",
            account_signin_enabled=enabled == "true",
            legal_pages_approved=approved == "true",
            legal_controller_name=_text(env, "LEGAL_CONTROLLER_NAME"),
            legal_contact_address=_text(env, "LEGAL_CONTACT_ADDRESS"),
            contact_email=_text(env, "CONTACT_EMAIL"),
            security_contact_expires=security_expiry,
            vps_provider_name=_text(env, "VPS_PROVIDER_NAME"),
            email_provider_name=_text(env, "EMAIL_PROVIDER_NAME"),
            email_processing_notice=_text(env, "EMAIL_PROCESSING_NOTICE"),
            legal_transfer_details=_text(env, "LEGAL_TRANSFER_DETAILS"),
            operator_email=_text(env, "OPERATOR_EMAIL"),
            backup_directory=Path(_text(env, "BACKUP_DIRECTORY", str(db_path.parent / "backups"))),
            backup_encryption_key=_text(env, "BACKUP_ENCRYPTION_KEY"),
            offsite_b2_key_id=_text(env, "OFFSITE_B2_KEY_ID"),
            offsite_b2_application_key=_text(env, "OFFSITE_B2_APPLICATION_KEY"),
            offsite_b2_bucket_id=_text(env, "OFFSITE_B2_BUCKET_ID"),
            offsite_b2_prefix=_text(env, "OFFSITE_B2_PREFIX"),
            offsite_backup_notice=_text(env, "OFFSITE_BACKUP_NOTICE"),
            backup_heartbeat_url=_text(env, "BACKUP_HEARTBEAT_URL"),
        )
        offsite = (result.offsite_b2_key_id, result.offsite_b2_application_key, result.offsite_b2_bucket_id, result.offsite_b2_prefix)
        if result.backup_heartbeat_url:
            from .backup_monitor import validate_heartbeat_url
            try:
                validate_heartbeat_url(result.backup_heartbeat_url)
            except ValueError:
                raise ConfigurationError("BACKUP_HEARTBEAT_URL must be the private HTTPS HetrixTools heartbeat URL") from None
            if not all(offsite):
                raise ConfigurationError("BACKUP_HEARTBEAT_URL requires all OFFSITE_B2 settings")
        if any(offsite):
            if not all(offsite) or not result.backup_encryption_key:
                raise ConfigurationError("Offsite recovery requires all OFFSITE_B2 settings and BACKUP_ENCRYPTION_KEY")
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", result.offsite_b2_bucket_id):
                raise ConfigurationError("OFFSITE_B2_BUCKET_ID must be a provider bucket identifier")
            if not re.fullmatch(r"ctb-[a-f0-9]{32}/", result.offsite_b2_prefix):
                raise ConfigurationError("OFFSITE_B2_PREFIX must be ctb- followed by 32 random hex characters and a slash")
        if not result.backup_directory.is_absolute():
            raise ConfigurationError("BACKUP_DIRECTORY must be an absolute filesystem path")
        if result.operator_email and (not result.operator_email.isascii() or not re.fullmatch(
            r"[^\s@<>(),;:\"\\]+@[^\s@<>(),;:\"\\]+\.[^\s@<>(),;:\"\\]+", result.operator_email
        )):
            raise ConfigurationError("OPERATOR_EMAIL must be a single email address")
        if result.backup_encryption_key:
            try:
                key = base64.b64decode(result.backup_encryption_key, altchars=b"-_", validate=True)
            except (ValueError, TypeError):
                raise ConfigurationError("BACKUP_ENCRYPTION_KEY must be base64url-encoded 32 random bytes") from None
            if len(key) != 32:
                raise ConfigurationError("BACKUP_ENCRYPTION_KEY must be base64url-encoded 32 random bytes")
        if result.legal_pages_approved:
            missing = [name for name, value in (
                ("LEGAL_CONTROLLER_NAME", result.legal_controller_name),
                ("CONTACT_EMAIL", result.contact_email),
                ("VPS_PROVIDER_NAME", result.vps_provider_name),
                ("EMAIL_PROVIDER_NAME", result.email_provider_name),
                ("LEGAL_TRANSFER_DETAILS", result.legal_transfer_details),
            ) if not value.strip()]
            if any(offsite) and not result.offsite_backup_notice.strip():
                missing.append("OFFSITE_BACKUP_NOTICE")
            if missing:
                raise ConfigurationError("Missing legal configuration: " + ", ".join(missing))
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", result.contact_email):
                raise ConfigurationError("CONTACT_EMAIL must be a valid email address")
        if email_mode == "smtp":
            missing = [name for name, value in (
                ("SMTP_HOST", result.smtp_host),
                ("SMTP_USERNAME", result.smtp_username),
                ("SMTP_PASSWORD", result.smtp_password),
                ("SMTP_FROM_ADDRESS", result.smtp_from_address),
            ) if not value]
            if missing:
                raise ConfigurationError("Missing configuration: " + ", ".join(missing))
            if not result.smtp_starttls:
                raise ConfigurationError("SMTP_STARTTLS must be true for SMTP delivery")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", result.smtp_host):
                raise ConfigurationError("SMTP_HOST must be a hostname")
            if not result.smtp_from_address.isascii() or not re.fullmatch(
                r"[^\s@<>(),;:\"\\]+@[^\s@<>(),;:\"\\]+\.[^\s@<>(),;:\"\\]+", result.smtp_from_address
            ):
                raise ConfigurationError("SMTP_FROM_ADDRESS must be a single email address")
        return result

    def missing_launch_configuration(self) -> tuple[str, ...]:
        """Names only, suitable for a trusted operator's preflight output."""
        return tuple(name for name, value in (
            ("GOOGLE_CLIENT_ID", self.google_client_id),
            ("GOOGLE_CLIENT_SECRET", self.google_client_secret),
            ("SMTP_HOST", self.smtp_host),
            ("SMTP_USERNAME", self.smtp_username),
            ("SMTP_PASSWORD", self.smtp_password),
            ("SMTP_FROM_ADDRESS", self.smtp_from_address),
        ) if not value)
