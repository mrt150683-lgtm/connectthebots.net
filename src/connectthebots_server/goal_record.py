"""Canonical, append-only goal record envelopes and independent verification.

Mutable visibility and private account links are outside the hash envelope.
Content erasure keeps its digest, so a verifier can still check the chain without
claiming to have checked content that is no longer present.
"""
from hashlib import sha256
import json

from .sessions import storage_time


ZERO_HASH = "0" * 64
HASH_FIELDS = ("goal", "seq", "kind", "author", "created_at", "refs", "content_sha256", "previous")


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return sha256(canonical_json(value).encode("ascii")).hexdigest()


def entry_view(row, slug, *, include_content=True):
    return {
        "id": row["id"], "goal": slug, "seq": row["seq"], "kind": row["kind"],
        "author": row["author_handle"], "created_at": row["created_at"],
        "refs": json.loads(row["refs_json"]), "content_sha256": row["content_sha256"],
        "previous": row["previous_sha256"], "entry_sha256": row["entry_sha256"],
        "status": row["status"],
        "content": json.loads(row["content_json"]) if include_content and row["status"] != "withdrawn" and row["content_json"] is not None else None,
    }


def append_entry(connection, goal, kind, author_handle, content, now, *, refs=(), author_user_id=None, session_id=None,
                 status="visible"):
    """Append under the caller's BEGIN IMMEDIATE transaction; never commit here."""
    if not connection.in_transaction:
        raise ValueError("Goal record writes require a transaction")
    last = connection.execute("SELECT seq,entry_sha256 FROM goal_entries WHERE goal_id=? ORDER BY seq DESC LIMIT 1",
                              (goal["id"],)).fetchone()
    envelope = {
        "goal": goal["slug"], "seq": last["seq"] + 1 if last else 1, "kind": kind,
        "author": author_handle, "created_at": storage_time(now), "refs": list(refs),
        "content_sha256": digest(content), "previous": last["entry_sha256"] if last else ZERO_HASH,
    }
    entry_hash = digest(envelope)
    # The content-addressed id can also be checked without retaining cleartext.
    entry_id = "e_" + entry_hash
    connection.execute(
        "INSERT INTO goal_entries(id,goal_id,seq,kind,author_user_id,author_handle,session_id,refs_json,content_json,"
        "content_sha256,previous_sha256,entry_sha256,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (entry_id, goal["id"], envelope["seq"], kind, author_user_id, author_handle, session_id,
         canonical_json(list(refs)), canonical_json(content), envelope["content_sha256"], envelope["previous"], entry_hash,
         status, envelope["created_at"]))
    row = connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry_id,)).fetchone()
    return entry_view(row, goal["slug"])


def verify_record(entries, expected_goal=None):
    """Verify a complete record from seq 1; report the first broken envelope.

    Visibility is not an immutable claim: an operator can hide or release an
    entry. Missing cleartext skips only the corresponding content-hash check.
    """
    previous, checked, goal = ZERO_HASH, 0, expected_goal
    for number, entry in enumerate(entries, 1):
        try:
            seq = entry["seq"]
            if type(seq) is not int or seq != number:
                raise ValueError("sequence is not consecutive")
            if goal is None:
                goal = entry["goal"]
            if entry["goal"] != goal:
                raise ValueError("goal identifier changed")
            if entry["previous"] != previous:
                raise ValueError("previous hash does not match")
            envelope = {key: entry[key] for key in HASH_FIELDS}
            current = digest(envelope)
            if current != entry["entry_sha256"]:
                raise ValueError("entry hash does not match")
            if "id" in entry and entry["id"] != "e_" + current:
                raise ValueError("entry id does not match")
            if entry.get("content") is not None and digest(entry["content"]) != entry["content_sha256"]:
                raise ValueError("content hash does not match")
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
            return {"valid": False, "entries": checked, "first_broken": number, "detail": str(error)}
        previous, checked = entry["entry_sha256"], checked + 1
    return {"valid": True, "entries": checked, "first_broken": None, "detail": "Record links and available content match."}
