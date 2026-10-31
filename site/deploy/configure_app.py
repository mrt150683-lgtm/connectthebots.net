"""Create/check a root-only environment file without executing or printing it.

The supported format is one literal KEY=value per line, optionally quoted.
It is the common subset of systemd EnvironmentFile and Python's shlex parser;
shell expansion, multiline values, and embedded control characters are refused.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import tempfile
from urllib.parse import urlsplit


OFFSITE_FIELDS = ("OFFSITE_B2_KEY_ID", "OFFSITE_B2_APPLICATION_KEY", "OFFSITE_B2_BUCKET_ID", "OFFSITE_B2_PREFIX")
BACKUP_NETWORK_DROP_IN = """# Managed by Connect the Bots configure_app.py; contains no credentials.
[Service]
PrivateNetwork=no
RestrictAddressFamilies=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
"""
BACKUP_TIMER_DROP_IN = """# Managed by Connect the Bots configure_app.py; contains no credentials.
[Timer]
OnCalendar=
OnCalendar=hourly
RandomizedDelaySec=0
AccuracySec=1s
"""
# Only this exact previously shipped file may be migrated automatically.
LEGACY_BACKUP_TIMER_DROP_IN = """# Managed by Connect the Bots configure_app.py; contains no credentials.
[Timer]
OnCalendar=
OnCalendar=*-*-* *:00/15:00
RandomizedDelaySec=0
AccuracySec=1s
"""


def offsite_enabled(values: dict[str, str]) -> bool:
    configured = [values.get(key, "") for key in OFFSITE_FIELDS]
    if not any(configured):
        return False
    if not all(value.strip() for value in configured):
        raise ValueError("All four OFFSITE_B2 variables are required together")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", values["OFFSITE_B2_BUCKET_ID"]):
        raise ValueError("OFFSITE_B2_BUCKET_ID has an invalid format")
    if not re.fullmatch(r"ctb-[a-f0-9]{32}/", values["OFFSITE_B2_PREFIX"]):
        raise ValueError("OFFSITE_B2_PREFIX must be a unique ctb-<32 random hex characters>/ prefix")
    return True


def configure_backup_network(path: Path, values: dict[str, str]) -> None:
    """Manage only this exact credential-free override after configuration checks."""
    _configure_backup_override(path, offsite_enabled(values), BACKUP_NETWORK_DROP_IN)


def heartbeat_enabled(values: dict[str, str]) -> bool:
    value = values.get("BACKUP_HEARTBEAT_URL", "")
    if not value:
        return False
    if not re.fullmatch(r"https://sm\.hetrixtools\.net/hb/\?s=[A-Za-z0-9_-]{16,256}", value):
        raise ValueError("BACKUP_HEARTBEAT_URL must be the private HTTPS HetrixTools heartbeat URL")
    if not offsite_enabled(values):
        raise ValueError("BACKUP_HEARTBEAT_URL requires all OFFSITE_B2 settings")
    return True


def configure_backup_timer(path: Path, values: dict[str, str]) -> None:
    _configure_backup_override(path, heartbeat_enabled(values), BACKUP_TIMER_DROP_IN,
                               previous=(LEGACY_BACKUP_TIMER_DROP_IN,))


def _configure_backup_override(path: Path, enabled: bool, content: str, *, previous: tuple[str, ...] = ()) -> None:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Refusing a backup override path symlink")
    if path.exists():
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        if existing not in (content, *previous):
            raise ValueError("Backup override is not the managed file; review it first")
        if not enabled:
            path.unlink()
            return
        if existing == content:
            return
    if not enabled:
        return
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in result:
            raise ValueError("Invalid or duplicate environment variable name")
        if any(ord(character) < 32 for character in value) or "`" in value or "$" in value:
            raise ValueError("Unsupported environment value syntax")
        # Bare spaces are not supported: quote the whole value instead.
        pieces = shlex.split(value, comments=False, posix=True)
        if len(pieces) > 1:
            raise ValueError("Environment values containing spaces must be quoted")
        result[key] = pieces[0] if pieces else ""
    return result


def validate(values: dict[str, str], environment: str, base_url: str) -> None:
    missing = [key for key in ("APP_ENV", "PUBLIC_BASE_URL", "DATABASE_PATH", "SESSION_SECRET", "SIGNUPS", "EMAIL_MODE", "BACKUP_DIRECTORY", "BACKUP_ENCRYPTION_KEY") if not values.get(key)]
    if missing:
        raise ValueError("Missing variables: " + ", ".join(missing))
    if values["APP_ENV"] != environment or values["PUBLIC_BASE_URL"] != base_url:
        raise ValueError("APP_ENV or PUBLIC_BASE_URL differs from the deployment target; review app.env")
    if len(values["SESSION_SECRET"]) < 32 or values["SESSION_SECRET"] != values["SESSION_SECRET"].strip():
        raise ValueError("SESSION_SECRET must contain at least 32 characters with no surrounding whitespace")
    if values["DATABASE_PATH"] != "/var/lib/connectthebots/app.db":
        raise ValueError("DATABASE_PATH must stay inside the configured service data directory")
    if values["BACKUP_DIRECTORY"] != "/var/backups/connectthebots":
        raise ValueError("BACKUP_DIRECTORY must match the protected deployment backup directory")
    try:
        key = base64.b64decode(values["BACKUP_ENCRYPTION_KEY"], altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise ValueError("BACKUP_ENCRYPTION_KEY must encode 32 random bytes as base64url") from None
    if len(key) != 32:
        raise ValueError("BACKUP_ENCRYPTION_KEY must encode 32 random bytes as base64url")
    offsite_enabled(values)
    heartbeat_enabled(values)
    if values["SIGNUPS"] not in {"closed", "invite", "open"}:
        raise ValueError("Invalid SIGNUPS value")
    if values["EMAIL_MODE"] not in {"outbox", "smtp"}:
        raise ValueError("Invalid EMAIL_MODE value")
    if environment == "staging" and values["EMAIL_MODE"] != "outbox":
        raise ValueError("Staging requires EMAIL_MODE=outbox")
    if environment == "production" and urlsplit(base_url).scheme != "https":
        raise ValueError("Production requires HTTPS")

    # Old foundation environment files keep working with both gates disabled.
    # SIGNUPS alone cannot turn account collection on.
    for key in ("ACCOUNT_SIGNIN_ENABLED", "LEGAL_PAGES_APPROVED"):
        if values.get(key, "false") not in {"true", "false"}:
            raise ValueError(key + " must be true or false")
    signin_enabled = values.get("ACCOUNT_SIGNIN_ENABLED", "false") == "true"
    legal_approved = values.get("LEGAL_PAGES_APPROVED", "false") == "true"
    security_expiry = values.get("SECURITY_CONTACT_EXPIRES", "")
    if security_expiry:
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", security_expiry):
                raise ValueError
            datetime.strptime(security_expiry, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise ValueError("SECURITY_CONTACT_EXPIRES must be a UTC date in YYYY-MM-DDTHH:MM:SSZ format") from None
    if legal_approved:
        required = ("LEGAL_CONTROLLER_NAME", "CONTACT_EMAIL", "VPS_PROVIDER_NAME", "EMAIL_PROVIDER_NAME", "LEGAL_TRANSFER_DETAILS")
        missing = [key for key in required if not values.get(key, "").strip()]
        if offsite_enabled(values) and not values.get("OFFSITE_BACKUP_NOTICE", "").strip():
            missing.append("OFFSITE_BACKUP_NOTICE")
        if missing:
            raise ValueError("Missing legal variables: " + ", ".join(missing))
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["CONTACT_EMAIL"]):
            raise ValueError("CONTACT_EMAIL must be a valid contact email")
    if signin_enabled:
        missing = [key for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET") if not values.get(key)]
        if environment == "production" and not legal_approved:
            missing.append("LEGAL_PAGES_APPROVED=true")
        if missing:
            raise ValueError("Missing sign-in variables: " + ", ".join(missing))
    operator = values.get("OPERATOR_EMAIL", "")
    if operator and (not operator.isascii() or not re.fullmatch(r"[^\s@<>(),;:\"\\]+@[^\s@<>(),;:\"\\]+\.[^\s@<>(),;:\"\\]+", operator)):
        raise ValueError("OPERATOR_EMAIL must be a single email address")
    if values["EMAIL_MODE"] == "smtp":
        missing = [key for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_ADDRESS", "OPERATOR_EMAIL") if not values.get(key)]
        if missing:
            raise ValueError("Missing mail variables: " + ", ".join(missing))
        if values.get("SMTP_STARTTLS", "true") != "true":
            raise ValueError("SMTP_STARTTLS must be true for SMTP delivery")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", values["SMTP_HOST"]):
            raise ValueError("SMTP_HOST must be a hostname")
        sender = values["SMTP_FROM_ADDRESS"]
        if not sender.isascii() or not re.fullmatch(r"[^\s@<>(),;:\"\\]+@[^\s@<>(),;:\"\\]+\.[^\s@<>(),;:\"\\]+", sender):
            raise ValueError("SMTP_FROM_ADDRESS must be a single email address")
        port = values.get("SMTP_PORT", "587")
        if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("SMTP_PORT must be an integer from 1 to 65535")


def configure(path: Path, template: Path, environment: str, base_url: str, check_only: bool = False) -> None:
    if not path.exists():
        if check_only:
            raise ValueError("Missing app.env")
        content = template.read_text(encoding="utf-8")
        updates = {"APP_ENV": environment, "PUBLIC_BASE_URL": base_url, "SESSION_SECRET": secrets.token_urlsafe(48),
                   "BACKUP_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")}
        content = "\n".join(f"{line.partition('=')[0]}={updates[line.partition('=')[0]]}" if line.partition('=')[0] in updates else line for line in content.splitlines()) + "\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    values = read_env(path)
    # Existing environments must be reviewed, never repaired by silently
    # rotating either an authentication secret or a backup encryption key.
    validate(values, environment, base_url)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--template", type=Path, default=Path(__file__).with_name("app.env.example"))
    parser.add_argument("--environment", choices=("production", "staging"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--backup-network-drop-in", type=Path,
                        help="Manage the backup worker network override after validating app.env")
    parser.add_argument("--backup-timer-drop-in", type=Path,
                        help="Manage the hourly backup schedule after validating app.env")
    args = parser.parse_args()
    if args.check_only and (args.backup_network_drop_in is not None or args.backup_timer_drop_in is not None):
        parser.error("--check-only cannot change a backup override")
    try:
        configure(args.env_file, args.template, args.environment, args.base_url, args.check_only)
        if args.backup_network_drop_in is not None:
            configure_backup_network(args.backup_network_drop_in, read_env(args.env_file))
        if args.backup_timer_drop_in is not None:
            configure_backup_timer(args.backup_timer_drop_in, read_env(args.env_file))
    except (ValueError, OSError) as exc:
        # ValueError text is authored above and contains only variable names.
        print(str(exc) if isinstance(exc, ValueError) else "Cannot read or write app.env", file=sys.stderr)
        return 1
    print("Application environment checked; values are not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
