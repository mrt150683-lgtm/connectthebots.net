"""Explicit two-person introductions and conversations, without profile sharing."""

from datetime import datetime, timedelta
from hashlib import sha256
import hmac
import json
import math
import re

from .board import BoardError, _board_updated_at
from .board_schema import canonical_payload
from .report_schema import ReportError, text_field
from .sessions import random_token, storage_time, valid_token


class InteractionError(ValueError):
    def __init__(self, status, detail, retry_after=None):
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


def _text(value, label, maximum, *, preserve=False):
    try:
        clean = text_field(value, label, maximum)
    except ReportError as error:
        subject = "Messages" if label == "Message" else "Requests"
        raise InteractionError(error.status, error.detail.replace("Reports use", subject + " use")) from None
    return value if preserve else clean


def _digest(value):
    return sha256(canonical_payload(value)).hexdigest()


def _nonce(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value) is None:
        raise InteractionError(400, "This form identifier is invalid. Open a fresh form.")
    return sha256(value.encode("ascii")).hexdigest()


class InteractionService:
    def __init__(self, database, clock, board, *, notifications=None):
        self.database, self.clock, self.board = database, clock, board
        self.notifications = notifications

    @staticmethod
    def _owner(connection, user_id):
        row = connection.execute("SELECT suspended_at FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise InteractionError(401, "Please sign in again.")
        if row["suspended_at"] is not None:
            raise InteractionError(403, "This account is not available.")

    @staticmethod
    def _blocked(connection, left, right):
        return connection.execute(
            "SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
            (left, right, right, left),
        ).fetchone() is not None

    @staticmethod
    def _active(connection, user_id):
        return user_id is not None and connection.execute("SELECT 1 FROM users WHERE id=? AND suspended_at IS NULL", (user_id,)).fetchone() is not None

    def _approved(self, connection, *, user_id=None, slug=None):
        self.board._register_checks(connection)
        field, value = ("p.user_id", user_id) if user_id is not None else ("p.slug", slug)
        # Field selection is a fixed internal allowlist, never request SQL.
        row = connection.execute(self.board._APPROVED + self.board._VISIBLE + "AND " + field + "=?", (value,)).fetchone()
        if row is None:
            return None
        identity = connection.execute("SELECT user_id,active_grant_id FROM board_postings WHERE slug=?", (row["slug"],)).fetchone()
        return {"owner": identity["user_id"], "grant": identity["active_grant_id"], "posting": self.board._item(row)}

    def _prepare(self, connection, user_id, target_slug, note, reason):
        self._owner(connection, user_id)
        note, reason = _text(note, "Note", 600), _text(reason, "Reason", 300)
        if not isinstance(target_slug, str) or re.fullmatch(r"[0-9a-f]{32}", target_slug) is None:
            raise InteractionError(404, "This introduction is not available.")
        mine, theirs = self._approved(connection, user_id=user_id), self._approved(connection, slug=target_slug)
        if mine is None:
            raise InteractionError(409, "Publish your own posting before asking for an introduction.")
        if theirs is None or theirs["owner"] == user_id or self._blocked(connection, user_id, theirs["owner"]):
            raise InteractionError(404, "This introduction is not available.")
        context = {"target_slug": target_slug, "requester_posting": mine["posting"], "recipient_posting": theirs["posting"], "note": note, "reason": reason}
        digest = _digest({"context": context, "requester": user_id, "recipient": theirs["owner"], "requester_grant": mine["grant"], "recipient_grant": theirs["grant"]})
        return {**context, "approval_hash": digest}, mine, theirs

    def prepare_request(self, user_id, target_slug, note, reason):
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            return self._prepare(connection, user_id, target_slug, note, reason)[0]

    @staticmethod
    def _intro(connection, user_id, introduction_id):
        row = connection.execute("SELECT * FROM introductions WHERE id=? AND (requester_id=? OR recipient_id=?)", (introduction_id, user_id, user_id)).fetchone()
        if row is None:
            raise InteractionError(404, "Introduction not found.")
        return row

    def _context_current(self, connection, row):
        mine = self._approved(connection, user_id=row["requester_id"]) if row["requester_id"] else None
        theirs = self._approved(connection, user_id=row["recipient_id"]) if row["recipient_id"] else None
        return (mine is not None and theirs is not None and mine["grant"] == row["requester_grant_id"]
                and theirs["grant"] == row["recipient_grant_id"] and not self._blocked(connection, row["requester_id"], row["recipient_id"]))

    def _refresh(self, connection, row):
        if row["status"] != "requested":
            return row
        state = None
        if row["expires_at"] <= storage_time(self.clock()):
            state = "expired"
        elif self._blocked(connection, row["requester_id"], row["recipient_id"]):
            state = "blocked"
        elif not self._context_current(connection, row):
            state = "withdrawn"
        if state:
            connection.execute("UPDATE introductions SET status=?,resolved_at=? WHERE id=?", (state, storage_time(self.clock()), row["id"]))
            row = connection.execute("SELECT * FROM introductions WHERE id=?", (row["id"],)).fetchone()
        return row

    def _refresh_owned(self, connection, user_id):
        for row in connection.execute("SELECT * FROM introductions WHERE status='requested' AND (requester_id=? OR recipient_id=?)", (user_id, user_id)).fetchall():
            self._refresh(connection, row)

    def _intro_view(self, connection, user_id, row):
        state = row["status"]
        if state == "blocked" and row["blocked_by_id"] != user_id:
            state = "unavailable"
        direction = "outgoing" if row["requester_id"] == user_id else "incoming"
        shared = row["status"] in {"requested", "accepted"} and row["context_hash"] is not None
        conversation = connection.execute("SELECT id FROM conversations WHERE introduction_id=?", (row["id"],)).fetchone()
        peer = row["recipient_id"] if direction == "outgoing" else row["requester_id"]
        actions = []
        if row["status"] == "requested":
            actions = ["withdraw"] if direction == "outgoing" else ["accept", "decline"]
        elif row["status"] == "accepted" and conversation is not None:
            actions = ["open_conversation"]
        if peer is not None and not self._blocked(connection, user_id, peer):
            actions.append("block")
        return {"id": row["id"], "direction": direction, "state": state, "created_at": row["created_at"], "expires_at": row["expires_at"],
                "context_hash": row["context_hash"] if shared else None,
                "requester_posting": json.loads(row["requester_snapshot_json"]) if shared and row["requester_snapshot_json"] else None,
                "recipient_posting": json.loads(row["recipient_snapshot_json"]) if shared and row["recipient_snapshot_json"] else None,
                "note": row["note"] if shared else None, "reason": row["reason"] if shared else None,
                "conversation_id": conversation["id"] if conversation else None, "actions": actions}

    def request(self, user_id, preview, nonce):
        required = {"target_slug", "requester_posting", "recipient_posting", "note", "reason", "approval_hash"}
        if type(preview) is not dict or preview.keys() != required:
            raise InteractionError(400, "Open a fresh introduction preview.")
        nonce_hash, input_hash = _nonce(nonce), _digest(preview)
        result, failure = None, None
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            self._refresh_owned(connection, user_id)
            previous = connection.execute("SELECT * FROM intro_receipts WHERE owner_id=? AND nonce_hash=?", (user_id, nonce_hash)).fetchone()
            if previous:
                if not hmac.compare_digest(previous["input_hash"], input_hash):
                    failure = InteractionError(409, "This confirmation was already used for different content.")
                else:
                    row = self._refresh(connection, self._intro(connection, user_id, previous["introduction_id"]))
                    result = self._intro_view(connection, user_id, row)
            else:
                current, mine, theirs = self._prepare(connection, user_id, preview["target_slug"], preview["note"], preview["reason"])
                if not hmac.compare_digest(_digest(current), input_hash):
                    failure = InteractionError(409, "The approved postings or request changed. Preview the request again.")
                else:
                    peer = theirs["owner"]
                    pair = (user_id, peer, peer, user_id)
                    duplicate = connection.execute("SELECT 1 FROM introductions WHERE status='requested' AND ((requester_id=? AND recipient_id=?) OR (requester_id=? AND recipient_id=?))", pair).fetchone()
                    open_chat = connection.execute("SELECT 1 FROM conversations WHERE closed_at IS NULL AND ((requester_id=? AND recipient_id=?) OR (requester_id=? AND recipient_id=?))", pair).fetchone()
                    pending = connection.execute("SELECT COUNT(*) FROM introductions WHERE requester_id=? AND status='requested'", (user_id,)).fetchone()[0]
                    now = self.clock()
                    recent = connection.execute("SELECT created_at FROM introductions WHERE requester_id=? AND created_at>? ORDER BY created_at", (user_id, storage_time(now - timedelta(days=7)))).fetchall()
                    if duplicate or open_chat:
                        failure = InteractionError(409, "An introduction or open conversation already exists with this posting's author.")
                    elif pending >= 5:
                        failure = InteractionError(429, "You can have up to five pending outgoing requests. Wait for a reply or withdraw one.", 60)
                    elif len(recent) >= 20:
                        retry = math.ceil((datetime.fromisoformat(recent[-20][0].replace("Z", "+00:00")) + timedelta(days=7) - now).total_seconds())
                        failure = InteractionError(429, "You can send up to twenty new requests in any seven days.", max(1, retry))
                    else:
                        intro_id, created = random_token(), storage_time(now)
                        connection.execute(
                            "INSERT INTO introductions(id,requester_id,recipient_id,requester_grant_id,recipient_grant_id,requester_snapshot_json,recipient_snapshot_json,note,reason,context_hash,status,created_at,expires_at,requester_accepted_at) VALUES(?,?,?,?,?,?,?,?,?,?,'requested',?,?,?)",
                            (intro_id, user_id, peer, mine["grant"], theirs["grant"], json.dumps(current["requester_posting"]), json.dumps(current["recipient_posting"]), current["note"], current["reason"], current["approval_hash"], created, storage_time(now + timedelta(days=14)), created),
                        )
                        connection.execute("INSERT INTO intro_receipts(owner_id,nonce_hash,input_hash,introduction_id) VALUES(?,?,?,?)", (user_id, nonce_hash, input_hash, intro_id))
                        if self.notifications is not None:
                            self.notifications.record_event(connection, peer, "introduction_request", intro_id, user_id, intro_id)
                        result = self._intro_view(connection, user_id, self._intro(connection, user_id, intro_id))
        if failure:
            raise failure
        return result

    def get_introduction(self, user_id, introduction_id):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            row = self._refresh(connection, self._intro(connection, user_id, introduction_id))
            return self._intro_view(connection, user_id, row)

    @staticmethod
    def _pagination(limit, page):
        if type(limit) is not int or not 1 <= limit <= 50 or type(page) is not int or not 1 <= page <= 10000:
            raise InteractionError(422, "Choose a page from 1 to 10000 and a page size from 1 to 50.")
        return limit * (page - 1)

    def list_introductions(self, user_id, limit=20, page=1):
        offset = self._pagination(limit, page)
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            self._refresh_owned(connection, user_id)
            sections = {"pending_incoming": ("recipient_id=? AND status='requested'", (user_id,)),
                        "pending_outgoing": ("requester_id=? AND status='requested'", (user_id,)),
                        "history": ("(requester_id=? OR recipient_id=?) AND status!='requested'", (user_id, user_id))}
            result, totals = {}, {}
            for name, (where, parameters) in sections.items():
                totals[name] = connection.execute("SELECT COUNT(*) FROM introductions WHERE " + where, parameters).fetchone()[0]
                rows = connection.execute("SELECT * FROM introductions WHERE " + where + " ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?", (*parameters, limit, offset))
                result[name] = [self._intro_view(connection, user_id, row) for row in rows]
            return {**result, "totals": totals, "has_next": any(total > offset + limit for total in totals.values()), "page": page, "page_size": limit}

    def accept(self, user_id, introduction_id, expected_context_hash):
        result, failure = None, None
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            row = self._refresh(connection, self._intro(connection, user_id, introduction_id))
            if row["recipient_id"] != user_id:
                failure = InteractionError(403, "Only the recipient can accept this request.")
            elif not isinstance(expected_context_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_context_hash) is None or row["context_hash"] is None or not hmac.compare_digest(row["context_hash"], expected_context_hash):
                failure = InteractionError(409, "This introduction context changed. Open it again.")
            elif row["status"] not in {"requested", "accepted"}:
                failure = InteractionError(410, "This request is no longer available.")
            elif row["status"] == "accepted":
                conversation = connection.execute("SELECT * FROM conversations WHERE introduction_id=?", (introduction_id,)).fetchone()
                result = {"introduction": self._intro_view(connection, user_id, row), "conversation": self._conversation_view(connection, user_id, self._conversation(connection, user_id, conversation["id"]))}
            else:
                now = storage_time(self.clock())
                connection.execute("UPDATE introductions SET status='accepted',recipient_accepted_at=?,resolved_at=? WHERE id=?", (now, now, introduction_id))
                conversation_id = random_token()
                connection.execute("INSERT INTO conversations(id,introduction_id,requester_id,recipient_id,opened_at) VALUES(?,?,?,?,?)", (conversation_id, introduction_id, row["requester_id"], row["recipient_id"], now))
                if self.notifications is not None:
                    self.notifications.record_event(connection, row["requester_id"], "introduction_accepted", introduction_id, user_id, introduction_id)
                row = self._intro(connection, user_id, introduction_id)
                conversation = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
                result = {"introduction": self._intro_view(connection, user_id, row), "conversation": self._conversation_view(connection, user_id, conversation)}
        if failure:
            raise failure
        return result

    def _resolve(self, user_id, introduction_id, status):
        result, failure = None, None
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            row = self._refresh(connection, self._intro(connection, user_id, introduction_id))
            actor = row["recipient_id"] if status == "declined" else row["requester_id"]
            if actor != user_id:
                failure = InteractionError(403, "This action is not available to you.")
            elif row["status"] == status:
                result = self._intro_view(connection, user_id, row)
            elif row["status"] != "requested":
                failure = InteractionError(409, "This request is no longer pending.")
            else:
                connection.execute("UPDATE introductions SET status=?,resolved_at=? WHERE id=?", (status, storage_time(self.clock()), introduction_id))
                result = self._intro_view(connection, user_id, self._intro(connection, user_id, introduction_id))
        if failure:
            raise failure
        return result

    def decline(self, user_id, introduction_id):
        return self._resolve(user_id, introduction_id, "declined")

    def withdraw(self, user_id, introduction_id):
        return self._resolve(user_id, introduction_id, "withdrawn")

    def _touch_views(self, connection, users):
        global_time = datetime.fromisoformat(_board_updated_at(connection).replace("Z", "+00:00"))
        for user_id in users:
            prior = connection.execute("SELECT updated_at FROM board_view_state WHERE user_id=?", (user_id,)).fetchone()
            previous = max(global_time, datetime.fromisoformat(prior[0].replace("Z", "+00:00"))) if prior else global_time
            now = self.clock()
            value = storage_time(now if now > previous else previous + timedelta(microseconds=1))
            connection.execute("INSERT INTO board_view_state(user_id,updated_at) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at", (user_id, value))

    def _block(self, connection, user_id, peer):
        if peer is None or peer == user_id or connection.execute("SELECT 1 FROM users WHERE id=?", (peer,)).fetchone() is None:
            raise InteractionError(404, "This action is not available.")
        self._refresh_owned(connection, user_id)
        now = storage_time(self.clock())
        created = connection.execute("INSERT OR IGNORE INTO user_blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)", (user_id, peer, now)).rowcount
        pair = (user_id, peer, peer, user_id)
        connection.execute("UPDATE introductions SET status='blocked',blocked_by_id=?,resolved_at=? WHERE status='requested' AND ((requester_id=? AND recipient_id=?) OR (requester_id=? AND recipient_id=?))", (user_id, now, *pair))
        connection.execute("UPDATE conversations SET closed_at=?,closed_reason='unavailable' WHERE closed_at IS NULL AND ((requester_id=? AND recipient_id=?) OR (requester_id=? AND recipient_id=?))", (now, *pair))
        if created:
            self._touch_views(connection, (user_id, peer))

    def block_introduction(self, user_id, introduction_id):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            row = self._intro(connection, user_id, introduction_id)
            self._block(connection, user_id, row["recipient_id"] if row["requester_id"] == user_id else row["requester_id"])
            return self._intro_view(connection, user_id, self._intro(connection, user_id, introduction_id))

    def block_posting(self, user_id, slug):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            peer = self._approved(connection, slug=slug)
            if peer is None:
                raise InteractionError(404, "Posting not found.")
            self._block(connection, user_id, peer["owner"])
            return {"blocked": True}

    def _conversation(self, connection, user_id, conversation_id):
        row = connection.execute("SELECT * FROM conversations WHERE id=? AND (requester_id=? OR recipient_id=?)", (conversation_id, user_id, user_id)).fetchone()
        if row is None:
            raise InteractionError(404, "Conversation not found.")
        peer = row["recipient_id"] if row["requester_id"] == user_id else row["requester_id"]
        if row["closed_at"] is None and (not self._active(connection, peer) or self._blocked(connection, user_id, peer)):
            connection.execute("UPDATE conversations SET closed_at=?,closed_reason='unavailable' WHERE id=?", (storage_time(self.clock()), row["id"]))
            row = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        return row

    @staticmethod
    def _message_view(user_id, row):
        return {"id": row["id"], "author": "deleted" if row["sender_id"] is None else "you" if row["sender_id"] == user_id else "other", "text": row["body"], "created_at": row["created_at"]}

    def _conversation_view(self, connection, user_id, row, *, before=None, limit=50, include_messages=True):
        messages, more = [], False
        if include_messages:
            self._pagination(limit, 1)
            parameters = [row["id"]]
            where = "conversation_id=?"
            if before is not None:
                if not valid_token(before):
                    raise InteractionError(404, "Message page not found.")
                cursor = connection.execute("SELECT rowid,created_at FROM messages WHERE id=? AND conversation_id=?", (before, row["id"])).fetchone()
                if cursor is None:
                    raise InteractionError(404, "Message page not found.")
                where += " AND (created_at<? OR (created_at=? AND rowid<?))"
                parameters.extend([cursor["created_at"], cursor["created_at"], cursor["rowid"]])
            found = connection.execute("SELECT * FROM messages WHERE " + where + " ORDER BY created_at DESC,rowid DESC LIMIT ?", (*parameters, limit + 1)).fetchall()
            more = len(found) > limit
            messages = [self._message_view(user_id, item) for item in reversed(found[:limit])]
        return {"id": row["id"], "introduction_id": row["introduction_id"], "state": "open" if row["closed_at"] is None else "closed", "opened_at": row["opened_at"], "closed_at": row["closed_at"], "peer_label": "The other person", "messages": messages, "has_more": more}

    def get_conversation(self, user_id, conversation_id, before=None, limit=50):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            return self._conversation_view(connection, user_id, self._conversation(connection, user_id, conversation_id), before=before, limit=limit)

    def list_conversations(self, user_id, limit=20, page=1):
        offset = self._pagination(limit, page)
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            total = connection.execute("SELECT COUNT(*) FROM conversations WHERE requester_id=? OR recipient_id=?", (user_id, user_id)).fetchone()[0]
            rows = connection.execute("SELECT id FROM conversations WHERE requester_id=? OR recipient_id=? ORDER BY opened_at DESC,rowid DESC LIMIT ? OFFSET ?", (user_id, user_id, limit, offset)).fetchall()
            items = [self._conversation_view(connection, user_id, self._conversation(connection, user_id, row["id"]), include_messages=False) for row in rows]
            return {"items": items, "total": total, "has_next": total > offset + limit, "page": page, "page_size": limit}

    def send_message(self, user_id, conversation_id, text, nonce):
        text = _text(text, "Message", 4000, preserve=True)
        nonce_hash, input_hash = _nonce(nonce), _digest({"conversation": conversation_id, "text": text})
        result, failure = None, None
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            conversation = self._conversation(connection, user_id, conversation_id)
            if conversation["closed_at"] is not None:
                failure = InteractionError(409, "This conversation is closed.")
            else:
                previous = connection.execute("SELECT * FROM message_receipts WHERE owner_id=? AND nonce_hash=?", (user_id, nonce_hash)).fetchone()
                if previous:
                    if not hmac.compare_digest(previous["input_hash"], input_hash):
                        failure = InteractionError(409, "This message form was already used for different content.")
                    else:
                        result = self._message_view(user_id, connection.execute("SELECT * FROM messages WHERE id=?", (previous["message_id"],)).fetchone())
                else:
                    now = self.clock()
                    recent = connection.execute("SELECT created_at FROM messages WHERE sender_id=? AND created_at>? ORDER BY created_at", (user_id, storage_time(now - timedelta(days=1)))).fetchall()
                    if len(recent) >= 100:
                        retry = math.ceil((datetime.fromisoformat(recent[-100][0].replace("Z", "+00:00")) + timedelta(days=1) - now).total_seconds())
                        failure = InteractionError(429, "You can send up to one hundred messages in any 24 hours.", max(1, retry))
                    else:
                        message_id = random_token()
                        connection.execute("INSERT INTO messages(id,conversation_id,sender_id,body,created_at) VALUES(?,?,?,?,?)", (message_id, conversation_id, user_id, text, storage_time(now)))
                        connection.execute("INSERT INTO message_receipts(owner_id,nonce_hash,input_hash,message_id) VALUES(?,?,?,?)", (user_id, nonce_hash, input_hash, message_id))
                        if self.notifications is not None:
                            peer = conversation["recipient_id"] if conversation["requester_id"] == user_id else conversation["requester_id"]
                            self.notifications.record_event(connection, peer, "new_message", conversation_id, user_id, message_id)
                        result = self._message_view(user_id, connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())
        if failure:
            raise failure
        return result

    def close_conversation(self, user_id, conversation_id):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            row = self._conversation(connection, user_id, conversation_id)
            if row["closed_at"] is None:
                connection.execute("UPDATE conversations SET closed_at=?,closed_reason='left' WHERE id=?", (storage_time(self.clock()), conversation_id))
            return self._conversation_view(connection, user_id, self._conversation(connection, user_id, conversation_id))

    def block_conversation(self, user_id, conversation_id):
        with self.database.transaction() as connection:
            self._owner(connection, user_id)
            row = self._conversation(connection, user_id, conversation_id)
            self._block(connection, user_id, row["recipient_id"] if row["requester_id"] == user_id else row["requester_id"])
            return self._conversation_view(connection, user_id, self._conversation(connection, user_id, conversation_id))

    def export_owner(self, user_id, *, connection=None):
        if connection is None:
            with self.database.transaction() as opened:
                return self.export_owner(user_id, connection=opened)
        self._owner(connection, user_id)
        self._refresh_owned(connection, user_id)
        intros = []
        for row in connection.execute("SELECT * FROM introductions WHERE requester_id=? OR recipient_id=? ORDER BY created_at,id", (user_id, user_id)):
            outgoing = row["requester_id"] == user_id
            own_snapshot = row["requester_snapshot_json"] if outgoing else row["recipient_snapshot_json"]
            intros.append({**self._intro_view(connection, user_id, row),
                "own_posting_snapshot": json.loads(own_snapshot) if own_snapshot else None,
                "own_note": row["note"] if outgoing else None,
                "own_reason": row["reason"] if outgoing else None})
        messages = [dict(row) for row in connection.execute("SELECT id,conversation_id,body AS text,created_at FROM messages WHERE sender_id=? ORDER BY created_at,rowid", (user_id,))]
        conversations = [self._conversation_view(connection, user_id, self._conversation(connection, user_id, row["id"]), include_messages=False) for row in connection.execute("SELECT id FROM conversations WHERE requester_id=? OR recipient_id=? ORDER BY opened_at,id", (user_id, user_id)).fetchall()]
        blocks = [dict(row) for row in connection.execute(
            "SELECT b.created_at,p.slug AS blocked_posting_slug FROM user_blocks b LEFT JOIN board_postings p ON p.user_id=b.blocked_id WHERE b.blocker_id=? ORDER BY b.created_at,b.rowid", (user_id,))]
        return {"introductions": intros, "messages": messages, "conversations": conversations, "blocks": blocks}

    def erase_owner(self, user_id, *, connection):
        """Redact shared records before the parent's atomic user deletion."""
        now = storage_time(self.clock())
        connection.execute("DELETE FROM intro_receipts WHERE introduction_id IN (SELECT id FROM introductions WHERE requester_id=? OR recipient_id=?)", (user_id, user_id))
        connection.execute("DELETE FROM message_receipts WHERE owner_id=?", (user_id,))
        connection.execute("UPDATE messages SET body='[deleted]',sender_id=NULL WHERE sender_id=?", (user_id,))
        connection.execute("UPDATE conversations SET closed_at=COALESCE(closed_at,?),closed_reason='unavailable' WHERE requester_id=? OR recipient_id=?", (now, user_id, user_id))
        connection.execute("UPDATE introductions SET requester_snapshot_json=NULL,recipient_snapshot_json=NULL,requester_grant_id=NULL,recipient_grant_id=NULL,note=NULL,reason=NULL,context_hash=NULL,status=CASE WHEN status='requested' THEN 'withdrawn' ELSE status END,resolved_at=COALESCE(resolved_at,?) WHERE requester_id=? OR recipient_id=?", (now, user_id, user_id))
        for table in ("introductions", "conversations"):
            connection.execute("UPDATE " + table + " SET requester_id=NULL WHERE requester_id=?", (user_id,))
            connection.execute("UPDATE " + table + " SET recipient_id=NULL WHERE recipient_id=?", (user_id,))
        connection.execute("UPDATE introductions SET blocked_by_id=NULL WHERE blocked_by_id=?", (user_id,))
        connection.execute("DELETE FROM introductions WHERE requester_id IS NULL AND recipient_id IS NULL")
