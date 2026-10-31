"""One encrypted snapshot, committed atomically with SQLite's writer lock.

SQLite receives only authenticated ciphertext, random nonces and format/epoch
metadata. The externally supplied 256-bit key is never stored here. AES-GCM
uses a fresh random 96-bit nonce for each write and binds the format and epoch
as associated data. A wrong key or altered ciphertext fails closed. A complete
old file can still be rolled back by a filesystem attacker; this local
prototype has no trusted external monotonic counter or key-management claim.

The lock covers authorization, domain effects, budgets, receipts and retries,
including when separate runtime instances use the same database. A failed
commit exposes no successful result. See tests/test_agent_runtime.py.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator

FORMAT = b"agentmatch-encrypted-runtime-1"
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


class VaultError(ValueError):
    pass


def _cipher(key: bytes):
    if not isinstance(key, bytes) or len(key) != 32:
        raise VaultError("VAULT_KEY_MUST_BE_32_BYTES")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise VaultError("RUNTIME_DEPENDENCY_REQUIRED") from exc
    return AESGCM(key)


def _json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class EncryptedVault:
    def __init__(self, path: Path | str, key: bytes):
        self.path = Path(path).resolve()
        self._cipher = _cipher(key)

    def _connect(self) -> sqlite3.Connection:
        # mode=rw prevents a typo during recovery from silently creating a vault.
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA trusted_schema=OFF")
        return connection

    def create(self, state: dict[str, Any]) -> None:
        # Exclusive creation preserves an existing vault even if initialization fails.
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE vault (id INTEGER PRIMARY KEY CHECK(id=1), epoch INTEGER NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL)")
                nonce, ciphertext = self._encrypt(1, state)
                connection.execute("INSERT INTO vault VALUES (1, ?, ?, ?)", (1, nonce, ciphertext))
                connection.execute("COMMIT")
        except Exception:
            # Preserve the failed file for inspection; never replace an existing file.
            raise

    def _encrypt(self, epoch: int, state: dict[str, Any]) -> tuple[bytes, bytes]:
        data = _json(state)
        if len(data) > MAX_SNAPSHOT_BYTES:
            raise VaultError("VAULT_LIMIT_REACHED")
        nonce = os.urandom(12)
        return nonce, self._cipher.encrypt(nonce, data, FORMAT + b":" + str(epoch).encode("ascii"))

    def _load(self, connection: sqlite3.Connection) -> tuple[int, dict[str, Any]]:
        from cryptography.exceptions import InvalidTag
        try:
            rows = connection.execute("SELECT epoch, nonce, ciphertext FROM vault WHERE id=1").fetchall()
            if len(rows) != 1:
                raise VaultError("VAULT_INVALID")
            epoch, nonce, ciphertext = rows[0]
            if type(epoch) is not int or epoch < 1 or not isinstance(nonce, bytes) or len(nonce) != 12:
                raise VaultError("VAULT_INVALID")
            if not isinstance(ciphertext, bytes) or len(ciphertext) > MAX_SNAPSHOT_BYTES + 16:
                raise VaultError("VAULT_INVALID")
            raw = self._cipher.decrypt(nonce, ciphertext, FORMAT + b":" + str(epoch).encode("ascii"))
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise VaultError("VAULT_INVALID")
            return epoch, value
        except (InvalidTag, json.JSONDecodeError, UnicodeError, sqlite3.DatabaseError) as exc:
            raise VaultError("VAULT_UNREADABLE") from exc

    def _write(self, connection: sqlite3.Connection, epoch: int, state: dict[str, Any]) -> None:
        nonce, ciphertext = self._encrypt(epoch, state)
        connection.execute("UPDATE vault SET epoch=?, nonce=?, ciphertext=? WHERE id=1", (epoch, nonce, ciphertext))

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[dict[str, Any]]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            epoch, state = self._load(connection)
            yield state
            if write:
                self._write(connection, epoch + 1, state)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
