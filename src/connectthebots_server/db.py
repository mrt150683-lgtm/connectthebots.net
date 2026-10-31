"""SQLite connections and atomic, checked SQL migrations, without an ORM."""

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from pathlib import Path
import re
import sqlite3
from typing import Iterator

from .clock import Clock, timestamp


class MigrationError(RuntimeError):
    """Refuse an unknown, edited, out-of-order or invalid schema history."""


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return sha256(self.sql.encode("utf-8")).hexdigest()


def load_migrations() -> tuple[Migration, ...]:
    directory = resources.files("connectthebots_server").joinpath("migrations")
    migrations = tuple(
        Migration(item.name, item.read_bytes().decode("utf-8"))
        for item in sorted(directory.iterdir(), key=lambda entry: entry.name)
        if item.name.endswith(".sql")
    )
    _validate_migrations(migrations)
    return migrations


def _validate_migrations(migrations: tuple[Migration, ...]) -> None:
    if not migrations:
        raise MigrationError("No packaged database migrations were found")
    for index, migration in enumerate(migrations, start=1):
        if not re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", migration.name) or int(migration.name[:4]) != index:
            raise MigrationError("Packaged database migrations must be consecutively numbered")


def _statements(sql: str) -> Iterator[str]:
    """Let SQLite recognize complete statements, including quoted semicolons.

    ``executescript`` implicitly commits an existing transaction, so it cannot
    be used for atomic startup migration. Packaged migration text is trusted;
    values received from users must always use bound query parameters instead.
    """
    buffer = ""
    for character in sql:
        buffer += character
        if character == ";" and sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""
    remainder = re.sub(r"/\*.*?\*/|--[^\n]*", "", buffer, flags=re.DOTALL).strip()
    if remainder:
        raise MigrationError("Every migration statement must end with a semicolon")


class Database:
    def __init__(self, path: Path | str, clock: Clock, *, migrations: tuple[Migration, ...] | None = None, recovery_authority=None):
        self.path = Path(path)
        self.clock = clock
        self.migrations = load_migrations() if migrations is None else migrations
        self.recovery_authority = recovery_authority
        _validate_migrations(self.migrations)

    @contextmanager
    def connect(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        """Open an independent connection. Reads never recreate a missing DB."""
        uri = self.path.resolve().as_uri() + ("?mode=rwc" if create else "?mode=rw")
        # A protected writer can wait for remote authority while holding the
        # SQLite writer lock. Other async request paths must never busy-wait on
        # that lock and freeze the event loop; they receive a retryable failure.
        timeout = 0 if self.recovery_authority is not None else 5
        connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=timeout)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 0" if timeout == 0 else "PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, _guard_recovery=True) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                authority = None
                if _guard_recovery and connection.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_authority' AND type='table'").fetchone():
                    from .recovery_authority import local_state
                    authority = local_state(connection)
                yield connection
                self._advance_authority(connection, authority)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _advance_authority(self, connection, before):
        if before:
            from .recovery_authority import AuthorityError, local_state
            after = local_state(connection)
            if after.get("growth_revision", 0) != before.get("growth_revision", 0):
                # Local-only intact restores need the same privacy boundary.
                # Quarantine bypasses this transaction guard because it only
                # restricts access to already authenticated private scratch.
                connection.execute("UPDATE recovery_authority SET growth_generation=growth_generation+1 WHERE singleton=1")
            if before["enrolled"] and after["revision"] != before["revision"]:
                if self.recovery_authority is None:
                    raise AuthorityError("Independent recovery configuration is required; change refused.")
                self.recovery_authority.advance(connection, before)

    def _history(self, connection: sqlite3.Connection) -> list[sqlite3.Row]:
        present = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", ("schema_migrations",)
        ).fetchone()
        if not present:
            other = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if other:
                raise MigrationError("Database has tables but no migration history")
            return []
        return list(connection.execute("SELECT name, sha256 FROM schema_migrations ORDER BY name"))

    def _verify_history(self, history: list[sqlite3.Row]) -> None:
        if len(history) > len(self.migrations):
            raise MigrationError("Database schema is newer than this application")
        for record, expected in zip(history, self.migrations):
            if record["name"] != expected.name or record["sha256"] != expected.checksum:
                raise MigrationError("Database migration history does not match this application")

    def migrate(self) -> None:
        self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        with self.connect(create=True) as connection:
            if connection.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower() != "wal":
                raise MigrationError("SQLite WAL mode could not be enabled")
            connection.execute("BEGIN IMMEDIATE")
            try:
                authority = None
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_authority' AND type='table'").fetchone():
                    from .recovery_authority import local_state
                    authority = local_state(connection)
                history = self._history(connection)
                self._verify_history(history)
                applied_at = timestamp(self.clock())
                for migration in self.migrations[len(history):]:
                    for statement in _statements(migration.sql):
                        # Transaction control in a migration would break the
                        # all-or-nothing startup contract. SQLite's authorizer
                        # refuses it even behind comments or unusual spacing.
                        def authorize(action, arg1, arg2, database, trigger):
                            return sqlite3.SQLITE_DENY if action in {
                                sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                                sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
                            } else sqlite3.SQLITE_OK
                        connection.set_authorizer(authorize)
                        try:
                            connection.execute(statement)
                        finally:
                            connection.set_authorizer(None)
                    connection.execute(
                        "INSERT INTO schema_migrations (name, sha256, applied_at) VALUES (?, ?, ?)",
                        (migration.name, migration.checksum, applied_at),
                    )
                # A data-cleanup migration must not leave an older encrypted
                # snapshot independently eligible to resurrect erased data.
                self._advance_authority(connection, authority)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def is_current(self) -> bool:
        """Read-only health decision; failure details never leave this boundary."""
        try:
            with self.connect() as connection:
                history = self._history(connection)
                self._verify_history(history)
                return len(history) == len(self.migrations)
        except (sqlite3.Error, OSError, MigrationError):
            return False
