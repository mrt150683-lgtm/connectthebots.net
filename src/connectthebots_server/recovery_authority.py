"""Independent append-only recovery authority; no identity or content leaves here.

Every protected commit consumes one remote generation first. Ambiguous writes
are never retried or rolled back. A mismatch intentionally sacrifices recovery
availability rather than treating a stale backup as permission to restore data.
"""
import base64
from hashlib import sha256
import hmac
import json
import os
import re
import secrets
import stat
from .recovery import RecoveryError

HEX32 = re.compile(r"[a-f0-9]{32}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
MAX_MARKERS = 100000


class AuthorityError(RecoveryError):
    @property
    def detail(self):
        return str(self)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def local_state(connection):
    row = connection.execute("SELECT a.*,r.lineage_id FROM recovery_authority a JOIN recovery_state r ON r.singleton=a.singleton WHERE a.singleton=1").fetchone()
    if row is None:
        raise AuthorityError("Recovery authority is unavailable.")
    return dict(row)


def from_settings(settings):
    if not settings.offsite_b2_key_id:
        return None
    from .b2_storage import B2Storage
    key = base64.b64decode(settings.backup_encryption_key, altchars=b"-_", validate=True)
    return RecoveryAuthority(B2Storage(settings.offsite_b2_key_id, settings.offsite_b2_application_key,
                                      settings.offsite_b2_bucket_id, settings.offsite_b2_prefix), key,
                             settings.offsite_b2_prefix)


class RecoveryAuthority:
    def __init__(self, storage, backup_key, prefix):
        if len(backup_key) != 32:
            raise AuthorityError("Recovery authority requires the retained backup encryption key.")
        self.storage = storage
        self.prefix = prefix + "authority/"
        self.backup_prefix = prefix + "snapshots/"
        # Distinct domain/key from AES-GCM. Never use SESSION_SECRET here.
        self._mac_key = hmac.digest(backup_key, b"ctb-recovery-authority-v1", "sha256")

    def _signed(self, lineage, generation, marker, previous):
        payload = {"version": 1, "lineage": lineage, "generation": generation,
                   "marker": marker, "previous": previous}
        return {"payload": payload, "mac": hmac.new(self._mac_key, canonical(payload), sha256).hexdigest()}

    def _decode(self, raw):
        try:
            value = json.loads(raw)
            if set(value) != {"payload", "mac"}:
                raise ValueError
            payload = value["payload"]
            if set(payload) != {"version", "lineage", "generation", "marker", "previous"}:
                raise ValueError
            if payload["version"] != 1 or type(payload["generation"]) is not int or not 0 <= payload["generation"] < MAX_MARKERS:
                raise ValueError
            if not HEX32.fullmatch(payload["lineage"]) or not HEX32.fullmatch(payload["marker"]):
                raise ValueError
            if payload["previous"] and not HEX64.fullmatch(payload["previous"]):
                raise ValueError
            if not HEX64.fullmatch(value["mac"]) or not hmac.compare_digest(value["mac"], self._signed(**{
                "lineage": payload["lineage"], "generation": payload["generation"],
                "marker": payload["marker"], "previous": payload["previous"]})["mac"]):
                raise ValueError
            if canonical(value) != raw.encode("ascii"):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise AuthorityError("Independent recovery authority is invalid; operation refused.") from None

    def _name(self, value):
        payload = value["payload"]
        return self.prefix + f'{payload["generation"]:020d}-{payload["marker"]}.json'

    def latest(self, *, allow_empty=False):
        from .b2_storage import B2StorageError
        try:
            files = self.storage.list_versions(self.prefix)
            if not files:
                if allow_empty:
                    return None
                raise AuthorityError("Independent recovery authority is missing; operation refused.")
            if len(files) > MAX_MARKERS:
                raise AuthorityError("Independent recovery history requires operator review.")
            values = []
            for item in files:
                if item["action"] != "upload":
                    raise AuthorityError("Independent recovery history was hidden or changed; operation refused.")
                raw = item.get("metadata", {}).get("ctb-authority", "")
                value = self._decode(raw)
                if item["name"] != self._name(value) or item["length"] != len(raw):
                    raise AuthorityError("Independent recovery history conflicts; operation refused.")
                values.append((value, item))
            values.sort(key=lambda entry: entry[0]["payload"]["generation"])
            lineage = values[0][0]["payload"]["lineage"]
            previous = ""
            for index, (value, item) in enumerate(values):
                payload = value["payload"]
                if payload["generation"] != index or payload["lineage"] != lineage or payload["previous"] != previous:
                    raise AuthorityError("Independent recovery history has a gap or fork; operation refused.")
                previous = value["mac"]
            value, item = values[-1]
            if self.storage.read(item["id"], max_bytes=2048) != canonical(value):
                raise AuthorityError("Independent recovery history conflicts; operation refused.")
            return value
        except B2StorageError:
            raise AuthorityError("Independent recovery storage is unavailable; operation refused.") from None
        except (KeyError, TypeError, AttributeError):
            raise AuthorityError("Independent recovery authority is invalid; operation refused.") from None

    def require(self, state):
        value = self.latest()
        payload = value["payload"]
        if not state["enrolled"] or (payload["lineage"], payload["generation"], payload["marker"]) != (
            state["lineage_id"], state["generation"], state["marker"]
        ):
            raise AuthorityError("Local state differs from independent recovery authority; operation refused.")
        return value

    def _append(self, value):
        from .b2_storage import B2StorageError
        raw = canonical(value)
        try:
            self.storage.write(self._name(value), raw, metadata={"ctb-authority": raw.decode("ascii")})
        except B2StorageError:
            # The write may already have succeeded. Never retry with a new nonce.
            raise AuthorityError("Recovery authority write is unconfirmed; operation refused. Do not retry or reset authority.") from None
        if self.latest() != value:
            raise AuthorityError("Recovery authority write conflicts; operation refused.")

    def enroll(self, database):
        with database.transaction(_guard_recovery=False) as connection:
            state = local_state(connection)
            if state["enrolled"] or self.latest(allow_empty=True) is not None:
                raise AuthorityError("Enroll only an unregistered database in an empty dedicated authority prefix.")
            value = self._signed(state["lineage_id"], 0, secrets.token_hex(16), "")
            self._append(value)
            connection.execute("UPDATE recovery_authority SET enrolled=1,generation=0,marker=? WHERE singleton=1", (value["payload"]["marker"],))
        database.recovery_authority = self
        return {"enrolled": True, "generation": 0}

    def advance(self, connection, before):
        value = self.require(before)
        payload = value["payload"]
        if payload["generation"] + 1 >= MAX_MARKERS:
            raise AuthorityError("Independent recovery history requires operator review.")
        following = self._signed(payload["lineage"], payload["generation"] + 1, secrets.token_hex(16), value["mac"])
        self._append(following)
        connection.execute("UPDATE recovery_authority SET generation=?,marker=? WHERE singleton=1",
                           (following["payload"]["generation"], following["payload"]["marker"]))

    def upload_backup(self, path):
        from .b2_storage import B2StorageError
        # Called only for the completed encrypted file created by RecoveryService.
        limit = 128 * 1024 * 1024
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > limit:
                raise AuthorityError("Offsite backup must be a private completed file within the 128 MiB limit.")
            content = source.read(limit + 1)
        if len(content) > limit or len(content) != info.st_size:
            raise AuthorityError("Offsite backup changed or exceeds the reviewed 128 MiB limit.")
        digest = sha256(content).hexdigest()
        try:
            file_id = self.storage.write(self.backup_prefix + path.name, content)
            if not hmac.compare_digest(sha256(self.storage.read(file_id, max_bytes=len(content))).hexdigest(), digest):
                raise AuthorityError("Offsite backup read-back verification failed.")
        except B2StorageError:
            raise AuthorityError("Offsite backup upload is unconfirmed; keep the completed local backup.") from None
        return {"offsite_verified": True, "sha256": digest}
