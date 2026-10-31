"""Exact-payload board grants and use-time public visibility.

Publication hashes are consistency checks, not authentication credentials.
The web adapter supplies the authenticated owner and the approved preview.
"""

from datetime import datetime, timedelta
import hmac
import json
import re
import secrets
import unicodedata

from .board_schema import BoardError, canonical_payload, payload_hash, search_text, validate_posting
from .sessions import random_token, storage_time


SLUG = re.compile(r"[0-9a-f]{32}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")


def _board_updated_at(connection):
    value = connection.execute("SELECT updated_at FROM board_state WHERE id = 1").fetchone()[0]
    if value is None:
        value = connection.execute("SELECT applied_at FROM schema_migrations WHERE name = ?", ("0005_board.sql",)).fetchone()[0]
    return storage_time(datetime.fromisoformat(value.replace("Z", "+00:00")))


def touch_board(connection, clock):
    """Advance public freshness inside the caller's mutation transaction.

    Account deletion/moderation must call this when it changes public contents.
    Frozen or regressed test clocks cannot make a withdrawal look unchanged.
    """
    previous = datetime.fromisoformat(_board_updated_at(connection).replace("Z", "+00:00"))
    now = clock()
    value = storage_time(now if now > previous else previous + timedelta(microseconds=1))
    connection.execute("UPDATE board_state SET updated_at = ? WHERE id = 1", (value,))
    return value


class BoardService:
    def __init__(self, database, clock):
        self.database = database
        self.clock = clock

    @staticmethod
    def _owner(connection, user_id):
        row = connection.execute("SELECT suspended_at FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise BoardError(401, "Please sign in again.")
        if row["suspended_at"] is not None:
            raise BoardError(403, "This account is not available.")

    @staticmethod
    def _payload(row):
        try:
            payload = validate_posting(json.loads(row["payload_json"]))
            if not hmac.compare_digest(payload_hash(payload), row["payload_hash"]):
                raise ValueError
            return payload
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise BoardError(503, "This posting is temporarily unavailable.") from None

    def _draft(self, connection, user_id):
        row = connection.execute(
            "SELECT r.* FROM board_postings p JOIN board_revisions r ON r.user_id=p.user_id AND r.revision=p.draft_revision WHERE p.user_id=?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        return {"revision": row["revision"], "payload": self._payload(row), "payload_hash": row["payload_hash"], "saved_at": row["saved_at"]}

    def draft(self, user_id):
        with self.database.connect() as connection:
            self._owner(connection, user_id)
            return self._draft(connection, user_id)

    def _append_draft(self, connection, user_id, payload, revision):
        saved = storage_time(self.clock())
        digest = payload_hash(payload)
        connection.execute(
            "INSERT INTO board_revisions(user_id,revision,payload_json,payload_hash,search_text,saved_at) VALUES(?,?,?,?,?,?)",
            (user_id, revision, canonical_payload(payload).decode("utf-8"), digest, search_text(payload), saved),
        )
        connection.execute("UPDATE board_postings SET draft_revision=? WHERE user_id=?", (revision, user_id))
        return {"revision": revision, "payload": payload, "payload_hash": digest, "saved_at": saved}

    def save_draft(self, user_id, payload):
        payload = validate_posting(payload)
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            posting = connection.execute("SELECT * FROM board_postings WHERE user_id=?", (user_id,)).fetchone()
            revision = posting["draft_revision"] + 1 if posting else 1
            if posting is None:
                connection.execute("INSERT INTO board_postings(user_id,slug,draft_revision) VALUES(?,?,?)", (user_id, secrets.token_hex(16), revision))
            return self._append_draft(connection, user_id, payload, revision)

    @staticmethod
    def _register_checks(connection):
        def checked_hash(raw):
            try:
                return payload_hash(validate_posting(json.loads(raw)))
            except (ValueError, TypeError, UnicodeError):
                return None
        connection.create_function("board_payload_hash", 1, checked_hash, deterministic=True)

    # Join only the exact grant/revision/owner combination. Neither a private
    # draft nor a revoked, wrong-owner or mismatched grant can authorize output.
    _APPROVED = (
        "SELECT p.slug,p.hidden,p.ever_published,p.updated_at,r.payload_json,r.payload_hash,g.approved_at,g.revision "
        "FROM board_postings p JOIN users u ON u.id=p.user_id "
        "JOIN board_publication_grants g ON g.id=p.active_grant_id AND g.user_id=p.user_id "
        "JOIN board_revisions r ON r.user_id=p.user_id AND r.revision=g.revision "
        "WHERE g.revoked_at IS NULL AND g.payload_hash=r.payload_hash AND g.payload_hash=board_payload_hash(r.payload_json) "
    )
    _VISIBLE = "AND p.hidden=0 AND u.suspended_at IS NULL "

    def _item(self, row):
        payload = self._payload(row)
        # This explicit outward projection is the entire public data contract.
        return {"slug": row["slug"], "text": payload["text"], "tags": payload["tags"],
                "looking_for": payload["looking_for"], "offering": payload["offering"],
                "published_at": row["approved_at"], "updated_at": row["updated_at"]}

    def _owner_posting(self, connection, user_id):
        posting = connection.execute("SELECT * FROM board_postings WHERE user_id=?", (user_id,)).fetchone()
        if posting is None:
            return {"slug": None, "draft": None, "published": None, "visible": False, "hidden": False, "has_unpublished_changes": False}
        self._register_checks(connection)
        approved = connection.execute(self._APPROVED + "AND p.user_id=?", (user_id,)).fetchone()
        draft = self._draft(connection, user_id)
        return {"slug": posting["slug"], "draft": draft, "published": self._item(approved) if approved else None,
                "visible": approved is not None and not bool(posting["hidden"]), "hidden": bool(posting["hidden"]),
                "has_unpublished_changes": approved is None or approved["revision"] != draft["revision"]}

    def owner_posting(self, user_id):
        with self.database.connect() as connection:
            self._owner(connection, user_id)
            return self._owner_posting(connection, user_id)

    def publish(self, user_id, expected_revision, expected_hash):
        if type(expected_revision) is not int or expected_revision < 1 or not isinstance(expected_hash, str) or HASH.fullmatch(expected_hash) is None:
            raise BoardError(409, "This preview has changed. Review the current draft before publishing.")
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            posting = connection.execute("SELECT * FROM board_postings WHERE user_id=?", (user_id,)).fetchone()
            draft = self._draft(connection, user_id)
            if draft is None or draft["revision"] != expected_revision or not hmac.compare_digest(draft["payload_hash"], expected_hash):
                raise BoardError(409, "This preview has changed. Review the current draft before publishing.")
            if posting["hidden"]:
                raise BoardError(403, "This posting is hidden. Contact the service operator before publishing it.")
            self._register_checks(connection)
            current = connection.execute(self._APPROVED + "AND p.user_id=?", (user_id,)).fetchone()
            if current is not None and current["revision"] == expected_revision:
                return self._item(current)
            changed_at = touch_board(connection, self.clock)
            if posting["active_grant_id"] is not None:
                connection.execute("UPDATE board_publication_grants SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL", (changed_at, posting["active_grant_id"], user_id))
            grant_id = random_token()
            connection.execute(
                "INSERT INTO board_publication_grants(id,user_id,revision,payload_hash,approved_at) VALUES(?,?,?,?,?)",
                (grant_id, user_id, expected_revision, expected_hash, changed_at),
            )
            connection.execute("UPDATE board_postings SET active_grant_id=?,ever_published=1,updated_at=? WHERE user_id=?", (grant_id, changed_at, user_id))
            row = connection.execute(self._APPROVED + "AND p.user_id=?", (user_id,)).fetchone()
            return self._item(row)

    def unpublish(self, user_id):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            posting = connection.execute("SELECT * FROM board_postings WHERE user_id=?", (user_id,)).fetchone()
            if posting is None:
                return self._owner_posting(connection, user_id)
            draft = self._draft(connection, user_id)
            if posting["active_grant_id"] is not None:
                changed_at = touch_board(connection, self.clock)
                connection.execute("UPDATE board_publication_grants SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL", (changed_at, posting["active_grant_id"], user_id))
                connection.execute("UPDATE board_postings SET active_grant_id=NULL,updated_at=? WHERE user_id=?", (changed_at, user_id))
            # Withdrawal invalidates every older approval preview, even if its
            # payload equals the next draft. A later publish needs fresh review.
            self._append_draft(connection, user_id, draft["payload"], draft["revision"] + 1)
            return self._owner_posting(connection, user_id)

    @staticmethod
    def _viewer_filter(viewer_id):
        if viewer_id is None:
            return "", ()
        return ("AND NOT EXISTS (SELECT 1 FROM user_blocks b WHERE (b.blocker_id=? AND b.blocked_id=p.user_id) OR (b.blocker_id=p.user_id AND b.blocked_id=?)) ", (viewer_id, viewer_id))

    def get_public(self, slug, *, viewer_id=None):
        if not isinstance(slug, str) or SLUG.fullmatch(slug) is None:
            raise BoardError(404, "Posting not found.")
        with self.database.connect() as connection:
            if viewer_id is not None:
                self._owner(connection, viewer_id)
            self._register_checks(connection)
            viewer_filter, parameters = self._viewer_filter(viewer_id)
            row = connection.execute(self._APPROVED + self._VISIBLE + viewer_filter + "AND p.slug=?", (*parameters, slug)).fetchone()
            if row is not None:
                return self._item(row)
            existing = connection.execute("SELECT ever_published,user_id FROM board_postings WHERE slug=?", (slug,)).fetchone()
            if existing is not None and viewer_id is not None:
                blocked = connection.execute("SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)", (viewer_id, existing["user_id"], existing["user_id"], viewer_id)).fetchone()
                if blocked:
                    raise BoardError(404, "Posting not found.")
            if existing is not None and existing["ever_published"]:
                raise BoardError(410, "This posting is no longer available.")
            raise BoardError(404, "Posting not found.")

    def public_list(self, query="", page=1, page_size=20, *, viewer_id=None):
        if not isinstance(query, str) or len(query) > 100 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF or unicodedata.category(char) == "Cf" for char in query):
            raise BoardError(422, "Search must contain at most 100 characters without control characters.")
        if type(page) is not int or not 1 <= page <= 10000 or type(page_size) is not int or not 1 <= page_size <= 50:
            raise BoardError(422, "Choose a page from 1 to 10000 and a page size from 1 to 50.")
        query = unicodedata.normalize("NFKC", query.strip()).casefold()
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.database.connect() as connection:
            self._register_checks(connection)
            connection.execute("BEGIN")  # Count, rows and timestamp share a snapshot.
            if viewer_id is not None:
                self._owner(connection, viewer_id)
            viewer_filter, parameters = self._viewer_filter(viewer_id)
            selected = self._APPROVED + self._VISIBLE + viewer_filter + "AND r.search_text LIKE ? ESCAPE '\\' "
            total = connection.execute("SELECT COUNT(*) FROM (" + selected + ")", (*parameters, pattern)).fetchone()[0]
            rows = connection.execute(selected + "ORDER BY p.updated_at DESC,p.slug ASC LIMIT ? OFFSET ?", (*parameters, pattern, page_size, (page - 1) * page_size)).fetchall()
            updated_at = _board_updated_at(connection)
            if viewer_id is not None:
                private = connection.execute("SELECT updated_at FROM board_view_state WHERE user_id=?", (viewer_id,)).fetchone()
                if private:
                    updated_at = max(updated_at, private[0])
            return {"schema_version": 1, "updated_at": updated_at, "page": page, "page_size": page_size,
                    "total": total, "has_next": page * page_size < total, "items": [self._item(row) for row in rows]}

    def export_owner(self, user_id, *, connection=None):
        if connection is None:
            with self.database.connect() as opened:
                opened.execute("BEGIN")
                return self.export_owner(user_id, connection=opened)
        # Account exports provide their existing snapshot. This helper neither
        # opens a second connection nor commits the caller's transaction.
        self._owner(connection, user_id)
        revisions = [{"revision": row["revision"], "payload": self._payload(row), "payload_hash": row["payload_hash"], "saved_at": row["saved_at"]}
                     for row in connection.execute("SELECT * FROM board_revisions WHERE user_id=? ORDER BY revision", (user_id,))]
        grants = [dict(row) for row in connection.execute("SELECT id,revision,payload_hash,approved_at,revoked_at FROM board_publication_grants WHERE user_id=? ORDER BY approved_at,id", (user_id,))]
        return {"posting": self._owner_posting(connection, user_id), "revisions": revisions, "publication_grants": grants}
