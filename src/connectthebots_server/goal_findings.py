"""Bounded evidence, independent checks and derived finding status.

C9's exact evidence matrix:
* literature_summary, result and negative_result always need a quoted HTTPS
  source; every sensitive-tier kind needs one too.
* standard proof_step, code, design and method need either a quoted HTTPS
  source or a nonempty reproducible method. An artifact is optional and must
  carry an HTTPS URL and a SHA-256 when supplied.
* a standard question can identify its unknown without a source or method.

The server does not fetch sources or establish their truth. Private captured
independence is not part of the public content or hash envelope. Every visible
dissent can dispute a finding, including a referral-linked review; only
independent supports count toward checked/confirmed. Other verdicts alone leave
a standard finding new. Sensitive peer-check visibility is derived separately
from status, so recording a review never changes recovery authority.

``finding_status`` takes plain dicts. A finding has id, seq and stored status;
reviews have id, finding_id, seq, reviewer (a per-goal handle), verdict,
independent (bool) and status. Relations are later findings with id, seq,
builds_on (entry IDs), and their already-derived status. A checked/confirmed
descendant resolves dissent only when it is later than that dissent. These
inputs need no database, clock, network or mutable account identity.
"""
from datetime import date
import json
import re
from urllib.parse import urlsplit

from . import growth_config
from .agent_common import AgentError, blocked, clean_text
from .agent_texts import GOAL_NOTICE, GOAL_SENSITIVE_BANNER
from .goal_record import entry_view
from .goal_safety import check_proposal
from .goals import _object, _text


FINDING_KINDS = ("literature_summary", "result", "proof_step", "negative_result", "method", "code", "design", "question")
VERDICTS = ("supports", "does_not_support", "could_not_check", "needs_change")
_EXTERNAL_KINDS = {"literature_summary", "result", "negative_result"}
_METHOD_KINDS = {"proof_step", "method", "code", "design"}
_ENTRY_ID = re.compile(r"e_[0-9a-f]{64}\Z")
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_FINDING_FIELDS = {"task_id", "kind", "summary", "detail", "method", "sources", "confidence", "unknowns", "builds_on"}
_DEPENDENT_KINDS = {"finding", "review", "summary", "step", "charter"}


def _get(value, key, default=None):
    return value[key] if key in value.keys() else default


def _live_reviews(reviews, finding_id=None):
    return [review for review in reviews
            if _get(review, "status", "visible") == "visible"
            and ("content" not in review or review["content"] is not None)
            and (finding_id is None or _get(review, "finding_id", finding_id) == finding_id)]


def finding_status(finding, reviews, relations):
    """Return a word status from normalized records, without changing them."""
    stored = _get(finding, "status", "visible")
    if stored in ("withdrawn", "hidden", "held"):
        return stored
    reviews = _live_reviews(reviews, finding["id"])
    dissents = [review for review in reviews if review["verdict"] == "does_not_support"]
    if dissents:
        nodes, reach_cache = {node["id"]: node for node in relations}, {}

        def reaches(node, target):
            key = (node["id"], target)
            if key in reach_cache:
                return reach_cache[key]
            stack, active = [(node, False)], set()
            while stack:
                current, expanded = stack.pop()
                current_key = (current["id"], target)
                if current_key in reach_cache:
                    continue
                refs = _get(current, "builds_on", ())
                if target in refs:
                    reach_cache[current_key] = True
                    continue
                if expanded:
                    active.discard(current["id"])
                    reach_cache[current_key] = any(reach_cache.get((ref, target), False) for ref in refs)
                    continue
                if current["id"] in active:
                    reach_cache[current_key] = False
                    continue
                active.add(current["id"])
                stack.append((current, True))
                stack.extend((nodes[ref], False) for ref in reversed(refs) if ref in nodes and (ref, target) not in reach_cache)
            return reach_cache[key]

        for dissent in dissents:
            later_than = max(_get(finding, "seq", 0), _get(dissent, "seq", 0))
            if not any(node["status"] in ("checked", "confirmed") and node["seq"] > later_than
                       and (reaches(node, finding["id"]) or _get(dissent, "id") is not None and reaches(node, dissent["id"]))
                       for node in relations):
                return "disputed"
    supporters = {_get(review, "reviewer") for review in reviews
                  if review["verdict"] == "supports" and _get(review, "independent") is True
                  and _get(review, "reviewer") is not None
                  and _get(review, "reviewer") != _get(finding, "author")}
    if len(supporters) >= 2:
        return "confirmed"
    if supporters:
        return "checked"
    return "pending_peer_check" if stored == "pending_peer_check" else "new"


def _reviews(connection, goal_id):
    result = []
    for row in connection.execute(
        "SELECT e.id,e.seq,e.status,e.content_json,c.finding_id,c.reviewer_handle,c.independent "
        "FROM goal_review_checks c JOIN goal_entries e ON e.id=c.entry_id WHERE e.goal_id=? ORDER BY e.seq", (goal_id,)):
        if row["content_json"] is None:
            continue
        content = json.loads(row["content_json"])
        result.append({"id": row["id"], "seq": row["seq"], "status": row["status"],
                       "finding_id": row["finding_id"], "reviewer": row["reviewer_handle"],
                       "independent": bool(row["independent"]), "verdict": content["verdict"]})
    return result


def finding_states(connection, goal_id):
    """Derive newest descendants first; only later findings can resolve dissent."""
    reviews = {}
    for review in _reviews(connection, goal_id):
        reviews.setdefault(review["finding_id"], []).append(review)
    descendants, states = [], {}
    for row in connection.execute("SELECT id,seq,status,author_handle,refs_json FROM goal_entries "
                                  "WHERE goal_id=? AND kind='finding' ORDER BY seq DESC", (goal_id,)):
        finding = {"id": row["id"], "seq": row["seq"], "status": row["status"], "author": row["author_handle"],
                   "builds_on": json.loads(row["refs_json"])}
        state = finding_status(finding, reviews.get(finding["id"], ()), descendants)
        states[finding["id"]] = state
        descendants.append({**finding, "status": state})
    return states


def _independent_checks(connection, finding_id, *, supports_only=False):
    condition = (" AND json_extract(e.content_json,'$.verdict')='supports'" if supports_only else
                 " AND json_extract(e.content_json,'$.verdict') IN ('supports','does_not_support','needs_change')")
    return connection.execute(
        "SELECT COUNT(DISTINCT c.reviewer_handle) FROM goal_review_checks c JOIN goal_entries e ON e.id=c.entry_id "
        "WHERE c.finding_id=? AND c.independent=1 AND e.status='visible' AND e.content_json IS NOT NULL" + condition,
        (finding_id,)).fetchone()[0]


def _dependency_rows(connection, entry):
    if entry["kind"] not in _DEPENDENT_KINDS:
        return []
    dependencies = []
    for ref in json.loads(entry["refs_json"]):
        row = connection.execute("SELECT * FROM goal_entries WHERE id=? AND goal_id=? AND seq<?",
                                 (ref, entry["goal_id"], entry["seq"])).fetchone()
        if row is None:
            return None
        # Authored task steps can copy their charter; operator audit events
        # stay outside this authored-work dependency graph.
        if row["kind"] in _DEPENDENT_KINDS:
            dependencies.append(row)
    if entry["kind"] == "finding":
        provenance = connection.execute("SELECT task_id,task_entry_id FROM goal_findings WHERE entry_id=?", (entry["id"],)).fetchone()
        if provenance is not None:
            # A generic record finding makes no assigned-task claim. Once a
            # task attribution exists, its exact source must be knowable and
            # publicly readable, including after an old snapshot is migrated.
            if provenance["task_entry_id"] is None:
                return None
            source = connection.execute("SELECT * FROM goal_entries WHERE id=? AND goal_id=? AND seq<? AND kind='step' "
                "AND json_extract(content_json,'$.task_id')=?",
                (provenance["task_entry_id"], entry["goal_id"], entry["seq"], provenance["task_id"])).fetchone()
            if source is None:
                return None
            dependencies.append(source)
    return dependencies


def public_entry_content(connection, entry, states=None, *, memo=None, visiting=None):
    """Current public content, including all explicitly cited authored work.

    Traverse the earlier-entry DAG iteratively so a long valid research chain
    cannot exhaust Python's recursion limit. A support's stored visibility is
    checked directly, avoiding a cycle through its finding's public projection.
    Nothing here erases independently authored content or changes a record.
    """
    memo = {} if memo is None else memo
    active, dependencies = set() if visiting is None else set(visiting), {}
    stack = [(entry, False)]
    while stack:
        current, expanded = stack.pop()
        key = current["id"]
        if key in memo:
            continue
        if expanded:
            active.discard(key)
            memo[key] = all(memo.get(row["id"], False) for row in dependencies[key])
            continue
        if key in active:
            memo[key] = False
            continue
        if current["content_json"] is None or current["status"] not in ("visible", "pending_peer_check"):
            memo[key] = False
            continue
        if current["status"] == "pending_peer_check" and (current["kind"] != "finding" or
                not _independent_checks(connection, key, supports_only=True)):
            memo[key] = False
            continue
        rows = _dependency_rows(connection, current)
        if rows is None:
            memo[key] = False
            continue
        dependencies[key] = rows
        active.add(key)
        stack.append((current, True))
        stack.extend((row, False) for row in reversed(rows) if row["id"] not in memo)
    return memo[entry["id"]]


def entry_dependencies_public(connection, entry, states=None, *, memo=None, visiting=None):
    """Reviewer-only pending access never releases a private cited ancestor."""
    rows = _dependency_rows(connection, entry)
    memo = {} if memo is None else memo
    return rows is not None and all(public_entry_content(connection, row, states, memo=memo, visiting=visiting) for row in rows)


def public_finding_content(connection, entry, states=None, *, memo=None, visiting=None):
    """Public finding and ancestors; a later dissent alone does not unpublish it."""
    return entry["kind"] == "finding" and public_entry_content(connection, entry, states, memo=memo, visiting=visiting)


def entry_readable(connection, entry, goal, viewer_id=None, *, operator=False, states=None, memo=None, visiting=None):
    """Normal records never grant a reviewer-only exception for pending work."""
    if _get(entry, "content_json") is None or entry["status"] == "withdrawn":
        return False
    if operator or viewer_id is not None and entry["author_user_id"] == viewer_id:
        return True
    return public_entry_content(connection, entry, states, memo=memo, visiting=visiting)


def _linked(connection, left, right):
    return left is not None and right is not None and connection.execute(
        "SELECT 1 FROM referrals WHERE (referrer_id=? AND referred_id=?) OR (referrer_id=? AND referred_id=?)",
        (left, right, right, left)).fetchone() is not None


def review_offers(connection, user_id, goal, limit=10, *, now=None):
    """Narrow active-session offers; no accounts or independence edges escape.

    Callers must first require the current work session. An explicit clock is
    needed for joining eligibility and a live optional-name projection; absence
    fails closed instead of reading the wall clock.
    """
    if type(limit) is not int or not 0 <= limit <= 10:
        raise AgentError(422, "Review offer limit must be a whole number from 0 to 10.")
    if now is None or not limit or goal["state"] != "open" or goal["review_pending"] or goal["charter_json"] is None:
        return []
    member = connection.execute("SELECT m.*,u.suspended_at FROM goal_members m JOIN users u ON u.id=m.user_id "
                                "WHERE m.goal_id=? AND m.user_id=?", (goal["id"], user_id)).fetchone()
    if member is None or member["state"] != "active" or member["suspended_at"] is not None:
        return []
    from .goal_members import GoalMemberService, display_author
    eligible = GoalMemberService(None, lambda: now, None)._eligibility(
        connection, user_id, json.loads(goal["charter_json"]), json.loads(member["capabilities_json"]), growth_config.load(connection))
    if not eligible["eligible"]:
        return []
    states, offers, memo = finding_states(connection, goal["id"]), [], {}
    for row in connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND kind='finding' "
                                  "AND status IN ('visible','pending_peer_check') AND content_json IS NOT NULL ORDER BY created_at,seq", (goal["id"],)):
        author = row["author_user_id"]
        if row["author_handle"] == member["handle"] or author == user_id or author is None:
            continue
        if blocked(connection, author, user_id) or _linked(connection, author, user_id):
            continue
        if connection.execute("SELECT 1 FROM goal_review_checks WHERE finding_id=? AND reviewer_handle=?",
                              (row["id"], member["handle"])).fetchone() or _independent_checks(connection, row["id"]) >= 2:
            continue
        if not entry_dependencies_public(connection, row, states, memo=memo):
            continue
        offer = entry_view(row, goal["slug"])
        offer["status"] = states[row["id"]]
        offer["display_author"] = display_author(connection, goal, author, now) or {"handle": row["author_handle"]}
        offers.append(offer)
        if len(offers) == limit:
            break
    return offers


def _url(value, label):
    value = clean_text(value, label, 2048)
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("HTTPS source required")
        parsed.port
    except ValueError:
        raise AgentError(422, f"{label} must be an HTTPS URL without account credentials.") from None
    return value


def _source(value):
    _object(value, "source", {"url", "title", "quote", "accessed"})
    accessed = value["accessed"]
    if not isinstance(accessed, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", accessed) is None:
        raise AgentError(422, "source accessed must be an ISO date.")
    try:
        date.fromisoformat(accessed)
    except ValueError:
        raise AgentError(422, "source accessed must be an ISO date.") from None
    title = clean_text(value["title"], "source title", 200)
    quote = clean_text(value["quote"], "source quote", 300, minimum=0, multiline=True)
    check_proposal(title + "\n" + quote)
    return {"url": _url(value["url"], "source URL"), "title": title, "quote": quote, "accessed": accessed}


def _text_items(value, label, count, maximum):
    if not isinstance(value, list) or len(value) > count:
        raise AgentError(422, f"{label} must be a list of at most {count} items.")
    return [_text(item, label, maximum, multiline=True) for item in value]


def _entry_id(value, label):
    if not isinstance(value, str) or _ENTRY_ID.fullmatch(value) is None:
        raise AgentError(422, f"{label} must be a goal record entry ID.")
    return value


def validate_finding(body, tier):
    _object(body, "finding", _FINDING_FIELDS, {"artifact"})
    kind = body["kind"]
    if not isinstance(kind, str) or kind not in FINDING_KINDS:
        raise AgentError(422, "Choose a supported finding kind.")
    if body["confidence"] not in ("tentative", "supported", "strong"):
        raise AgentError(422, "confidence must be tentative, supported or strong.")
    sources = body["sources"]
    if not isinstance(sources, list) or len(sources) > 10:
        raise AgentError(422, "sources must be a list of at most 10 sources.")
    sources = [_source(source) for source in sources]
    method = _text(body["method"], "method", 1500, minimum=0, multiline=True)
    quoted = any(source["quote"] for source in sources)
    if (tier == "sensitive" or kind in _EXTERNAL_KINDS) and not quoted:
        raise AgentError(422, "This finding needs at least one HTTPS source with a supporting quotation.")
    if tier != "sensitive" and kind in _METHOD_KINDS and not method and not quoted:
        raise AgentError(422, "This finding needs a reproducible method or a quoted HTTPS source.")
    refs = body["builds_on"]
    if not isinstance(refs, list) or len(refs) > 50:
        raise AgentError(422, "builds_on must be a list of at most 50 earlier entry IDs.")
    refs = [_entry_id(ref, "builds_on") for ref in refs]
    if len(set(refs)) != len(refs):
        raise AgentError(422, "builds_on must not repeat an entry ID.")
    result = {"task_id": clean_text(body["task_id"], "task_id", 80), "kind": kind,
              "summary": _text(body["summary"], "summary", 600, multiline=True),
              "detail": _text(body["detail"], "detail", 4000, multiline=True), "method": method,
              "sources": sources, "confidence": body["confidence"],
              "unknowns": _text_items(body["unknowns"], "unknowns", 5, 300), "builds_on": refs,
              "written_by": "assistant"}
    if "artifact" in body:
        artifact = body["artifact"]
        _object(artifact, "artifact", {"url", "sha256"})
        if not isinstance(artifact["sha256"], str) or _HEX.fullmatch(artifact["sha256"]) is None:
            raise AgentError(422, "artifact sha256 must contain exactly 64 hexadecimal characters.")
        result["artifact"] = {"url": _url(artifact["url"], "artifact URL"), "sha256": artifact["sha256"].lower()}
    return result


class GoalFindingService:
    def __init__(self, database, clock, goals, sessions):
        self.database, self.clock, self.goals, self.sessions = database, clock, goals, sessions

    def _result(self, connection, goal, entry):
        states = finding_states(connection, goal["id"])
        if entry["kind"] == "finding":
            entry["status"] = states[entry["id"]]
        result = {"entry": entry, "status": entry["status"], "notice": GOAL_NOTICE}
        if goal["tier"] == "sensitive":
            result["banner"] = GOAL_SENSITIVE_BANNER
        return result

    def finding(self, user_id, session_id, body):
        with self.database.transaction() as connection:
            session = self.sessions.require_session(connection, user_id, session_id, kind="finding", consume=True)
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (session["goal_id"],)).fetchone()
            content = validate_finding(body, goal["tier"])
            task = connection.execute("SELECT * FROM goal_tasks WHERE id=? AND goal_id=? AND task_json IS NOT NULL",
                                      (content["task_id"], goal["id"])).fetchone()
            if task is None or content["task_id"] != session["task_id"]:
                raise AgentError(422, "The finding must answer the task assigned to this session.")
            if task["state"] != "open" or self.goals.task_projection(connection, goal, task) is None:
                raise AgentError(409, "The assigned task is no longer open and publicly readable. Start fresh work on an available task.")
            task_source = connection.execute(
                "SELECT id FROM goal_entries WHERE goal_id=? AND kind='step' AND json_extract(content_json,'$.task_id')=? "
                "AND json_type(content_json,'$.task')='object' ORDER BY seq DESC LIMIT 1", (goal["id"], task["id"])).fetchone()
            if task_source is None:
                raise AgentError(409, "The assigned task needs a current public source before adding a finding.")
            for ref in content["builds_on"]:
                if connection.execute("SELECT 1 FROM goal_entries WHERE id=? AND goal_id=?", (ref, goal["id"])).fetchone() is None:
                    raise AgentError(422, "builds_on must refer to earlier entries in this goal.")
            member = self.sessions.members.member(connection, goal, user_id)
            entry = self.goals._append(connection, goal, "finding", member["handle"], content, self.clock(),
                refs=content["builds_on"], author_user_id=user_id, session_id=session_id,
                status="pending_peer_check" if goal["tier"] == "sensitive" else "visible")
            connection.execute("INSERT INTO goal_findings(entry_id,task_id,task_entry_id) VALUES(?,?,?)",
                               (entry["id"], content["task_id"], task_source["id"]))
            return self._result(connection, goal, entry)

    def review(self, user_id, session_id, body):
        with self.database.transaction() as connection:
            session = self.sessions.require_session(connection, user_id, session_id, kind="review", consume=True)
            _object(body, "review", {"finding_id", "verdict", "note", "checked"})
            finding_id = _entry_id(body["finding_id"], "finding_id")
            if body["verdict"] not in VERDICTS:
                raise AgentError(422, "Choose a supported review verdict.")
            checked = _text_items(body["checked"], "checked", 5, 80)
            if len(set(checked)) != len(checked):
                raise AgentError(422, "checked must not repeat an item.")
            if not checked and body["verdict"] != "could_not_check":
                raise AgentError(422, "Name at least one thing checked for this review verdict.")
            note = _text(body["note"], "review note", 1500, multiline=True)
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (session["goal_id"],)).fetchone()
            finding = connection.execute("SELECT * FROM goal_entries WHERE id=? AND goal_id=? AND kind='finding'",
                                         (finding_id, goal["id"])).fetchone()
            member = self.sessions.members.member(connection, goal, user_id)
            if finding is None or finding["content_json"] is None or finding["status"] not in ("visible", "pending_peer_check"):
                raise AgentError(404, "Finding not found.")
            if finding["author_user_id"] == user_id or finding["author_handle"] == member["handle"]:
                raise AgentError(409, "You cannot review your own finding.")
            if finding["author_user_id"] is None or blocked(connection, user_id, finding["author_user_id"]):
                raise AgentError(404, "Finding not found.")
            if connection.execute("SELECT 1 FROM goal_review_checks WHERE finding_id=? AND reviewer_handle=?",
                                  (finding_id, member["handle"])).fetchone():
                raise AgentError(409, "You have already reviewed this finding.")
            independent = not _linked(connection, user_id, finding["author_user_id"])
            if not entry_dependencies_public(connection, finding):
                raise AgentError(404, "Finding not found.")
            if not public_finding_content(connection, finding) and not independent:
                raise AgentError(404, "Finding not found.")
            content = {"finding_id": finding_id, "verdict": body["verdict"], "note": note,
                       "checked": checked, "written_by": "assistant"}
            entry = self.goals._append(connection, goal, "review", member["handle"], content, self.clock(),
                                       refs=[finding_id], author_user_id=user_id, session_id=session_id)
            connection.execute("INSERT INTO goal_review_checks(entry_id,finding_id,reviewer_handle,reviewer_user_id,independent) "
                               "VALUES(?,?,?,?,?)", (entry["id"], finding_id, member["handle"], user_id, int(independent)))
            result = self._result(connection, goal, entry)
            result["finding_status"] = finding_states(connection, goal["id"])[finding_id]
            return result
