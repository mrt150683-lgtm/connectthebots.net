"""Report-key generation and safe display metadata. No plaintext persistence."""

import base64
from hashlib import sha256
import re
import secrets

from .report_schema import ReportError, text_field


KEY_PATTERN = re.compile(r"ctb_[A-Z2-7]{52}\Z")


def new_report_key():
    return "ctb_" + base64.b32encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def key_hash(value):
    return sha256(value.encode("ascii")).hexdigest()


def bearer_key(authorization):
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise ReportError(401, "Use a current report key in the Authorization: Bearer header.")
    value = authorization[7:]
    if not KEY_PATTERN.fullmatch(value):
        raise ReportError(401, "Use a current report key in the Authorization: Bearer header.")
    return value


def key_name(value):
    return text_field(value, "Key name", 80)


def key_metadata(row):
    return {name: row[name] for name in ("id", "name", "last_four", "created_at", "last_used_at", "revoked_at")}
