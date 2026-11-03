"""Taste interview answers, sharing tiers and explainable taste matching.

Every answer carries the owner's tier: ``share`` (usable in matching and named
in explanations), ``ask`` (used only after the owner approves a disclosure to
one specific member) or ``never`` (never used). Matching only ever compares
items that each side may show the other, so an explanation can never name
something the other person was not allowed to see.
"""
import json
from hashlib import sha256
import secrets

from . import agent_texts, growth_config, match_tuning, personality
from .agent_common import AgentError, blocked, clean_text, normalize_item, public_id, require_approval, require_owner, user_for_public_id
from .sessions import storage_time


TIERS = ("share", "ask", "never")
POLITICS = ("left", "right", "centre", "doesnt_matter")
LOOKING_FOR = ("friends", "collaborators", "club", "company")
SEXES = ("female", "male", "non_binary", "prefer_not_to_say")

# topic: (question for a natural chat, explanation verb, weight, default tier, sensitive)
TOPICS = {
    "films": ("What's a film you could watch again and again? Any genre or era you keep coming back to?", "love", 2, "share", False),
    "tv": ("What have you binge-watched lately, or what's the show you always recommend?", "watch", 2, "share", False),
    "music": ("What's been on repeat for you recently? Any artist you'd travel to see live?", "listen to", 2, "share", False),
    "books": ("Is there a book you push on everyone, or a writer you'll read anything by?", "read", 2, "share", False),
    "food": ("What's your idea of a great meal, cooking in or eating out?", "enjoy", 1, "share", False),
    "sports": ("Do you play or follow any sport?", "follow", 2, "share", False),
    "teams": ("Is there a team you support, through thick and thin?", "support", 3, "share", False),
    "hobbies": ("What do you do when nobody needs anything from you?", "are into", 2, "share", False),
    "humour": ("What makes you laugh: a comedian, a show, a kind of humour?", "laugh at", 2, "share", False),
    "values": ("What matters most to you in the people you spend time with?", "value", 3, "share", False),
    "looking_for": ("What would you like from this: new friends, collaborators, a club to join, or just some company?", "are looking for", 0, "share", False),
    "politics": ("Only if you're comfortable: do you lean left, right, centre, or does it not matter to you? You can skip this.", "", 0, "ask", True),
}
MAX_ITEMS = 12
ITEM_LENGTH = 80


def catalogue():
    return [{"topic": topic, "ask": question, "default_tier": default, "sensitive": sensitive,
             **({"choices": list(POLITICS)} if topic == "politics" else {}),
             **({"choices": list(LOOKING_FOR)} if topic == "looking_for" else {})}
            for topic, (question, _, _, default, sensitive) in TOPICS.items()]


def validate_answer(raw):
    if not isinstance(raw, dict) or not set(raw) <= {"topic", "items", "tier", "skipped", "owner_confirmed_share"}:
        raise AgentError(422, "Each answer has topic, items, tier and optional skipped and owner_confirmed_share.")
    topic = raw.get("topic")
    if topic not in TOPICS:
        raise AgentError(422, "Unknown interview topic. Use a topic from GET /api/v1/agent/interview.")
    _, _, _, default, sensitive = TOPICS[topic]
    skipped = raw.get("skipped", False)
    if type(skipped) is not bool:
        raise AgentError(422, "skipped must be true or false.")
    tier = raw.get("tier", default)
    if tier not in TIERS:
        raise AgentError(422, "tier must be share, ask or never.")
    if sensitive and not skipped and tier == "share" and raw.get("owner_confirmed_share") is not True:
        raise AgentError(409, f"{topic} is sensitive and defaults to ask first. Send owner_confirmed_share: true only if your person chose to share it freely.")
    items = raw.get("items", [])
    if skipped:
        if items:
            raise AgentError(422, "A skipped topic has no items.")
        return {"topic": topic, "items": [], "tier": tier, "skipped": True}
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise AgentError(422, f"Give 1 to {MAX_ITEMS} short items for {topic}, or mark it skipped.")
    cleaned, seen = [], set()
    for item in items:
        text = clean_text(item, "Each item", ITEM_LENGTH)
        key = normalize_item(text)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    choices = POLITICS if topic == "politics" else LOOKING_FOR if topic == "looking_for" else None
    if choices is not None:
        if any(normalize_item(item) not in choices for item in cleaned):
            raise AgentError(422, f"{topic} items must come from: " + ", ".join(choices) + ".")
        cleaned = [normalize_item(item) for item in cleaned]
        if topic == "politics" and len(cleaned) != 1:
            raise AgentError(422, "politics takes exactly one choice.")
    return {"topic": topic, "items": cleaned, "tier": tier, "skipped": False}


def _visible(answers, disclosed):
    """Items an owner lets this particular other member see, by topic."""
    result = {}
    for topic, answer in answers.items():
        if answer["skipped"] or not answer["items"]:
            continue
        if answer["tier"] == "share" or (answer["tier"] == "ask" and topic in disclosed):
            result[topic] = answer["items"]
    return result


def _join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def answer_revision(answer, nonce):
    value = {key: answer[key] for key in ("items", "tier", "skipped")}
    value["approval_nonce"] = nonce
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def pair_match(mine, theirs, mine_disclosed, theirs_disclosed, fit=None, *, tuning=None, their_tuning=None, prints=(None, None)):
    """Explain a match from the viewer's side, or return None.

    ``mine``/``theirs`` map topic -> answer. ``*_disclosed`` are the ask-first
    topics each side approved for the other. Only mutually visible items count.
    ``fit`` is ``personality.pair_fit`` for two shared fingerprints, or None.
    Hard exclusions (politics, looking_for) apply to personality matches too.
    ``tuning``/``their_tuning`` are each side's match settings (None means the
    defaults); ``prints`` are the two shared fingerprints (viewer first). Strict
    areas and must preferences rule a match out in both directions.
    """
    a, b = _visible(mine, mine_disclosed), _visible(theirs, theirs_disclosed)
    if "looking_for" in a and "looking_for" in b and not set(a["looking_for"]) & set(b["looking_for"]):
        return None
    if "politics" in a and "politics" in b and {a["politics"][0], b["politics"][0]} == {"left", "right"}:
        return None
    my_print, their_print = prints
    if match_tuning.rules_out(tuning, my_print, their_print, b) or match_tuning.rules_out(their_tuning, their_print, my_print, a):
        return None
    interest_level, interest_weight, personality_only = match_tuning.interest_rule(tuning)
    shared, strength = [], 0
    for topic, (_, verb, weight, _, _) in TOPICS.items():
        if interest_level == 0 or topic in {"looking_for", "politics"} or topic not in a or topic not in b:
            continue
        theirs_keys = {normalize_item(item) for item in b[topic]}
        common = [item for item in a[topic] if normalize_item(item) in theirs_keys]
        if common:
            shared.append({"topic": topic, "items": common})
            strength += weight * len(common) * interest_weight
    met = match_tuning.met_preferences(tuning, my_print, their_print, b)
    # Personality alone can make a match only with enough signals; it can
    # always add reasons to a taste match. A met preference also counts as a
    # reason on its own: the owner asked for exactly this.
    use_fit = fit is not None and (shared or met or (personality_only is not None and fit["strength"] >= personality_only))
    if interest_level == 4 and not shared and not any(entry["kind"] == "interest" for entry in met):
        return None
    if not shared and not use_fit and not met:
        return None
    clauses = [TOPICS[entry["topic"]][1] + " " + _join(entry["items"][:2]) for entry in
               sorted(shared, key=lambda entry: -TOPICS[entry["topic"]][2] * len(entry["items"]))[:3]]
    compatible = []
    if "looking_for" in a and "looking_for" in b:
        wanted = [item for item in a["looking_for"] if item in b["looking_for"]]
        compatible.append({"topic": "looking_for", "items": wanted})
        clauses.append(("are both looking for " if shared else "are looking for ") + _join(wanted))
    if "politics" in a and "politics" in b:
        compatible.append({"topic": "politics", "items": ["compatible"]})
        strength += 1
    sentences = []
    if clauses:
        sentences.append(("You both " if shared else "You ") + _join(clauses) + ".")
    if any(entry["topic"] == "politics" for entry in compatible):
        sentences.append("Your views on politics are compatible.")
    if use_fit:
        sentences.append(fit["explanation"])
        strength += fit["strength"]
    if met:
        sentences.append("Fits what you asked for: " + _join([match_tuning.describe(entry) for entry in met]) + ".")
        strength += match_tuning.PREFERENCE_BONUS * len(met)
    sentence = " ".join(sentences)
    # Hints use only the viewer's own private answers and what the other
    # person already shows: they never reveal the other side's private data.
    hints = []
    for topic, answer in mine.items():
        if answer["tier"] == "ask" and topic not in mine_disclosed and topic in b and not answer["skipped"]:
            keys = {normalize_item(item) for item in b[topic]}
            if any(normalize_item(item) in keys for item in answer["items"]):
                hints.append(topic)
    traits = ({"alike": fit["alike"], "complements": fit["complements"], "differences": fit["differences"]}
              if use_fit else None)
    return {"shared": shared, "compatible": compatible, "personality": traits, "explanation": sentence,
            "asked_for": [{key: value for key, value in entry.items() if key != "importance"} | {"reads_as": match_tuning.describe(entry)}
                          for entry in met],
            "strength": strength, "ask_first_overlaps": hints}


class TasteService:
    def __init__(self, database, clock, referrals=None):
        self.database, self.clock = database, clock
        self.referrals = referrals

    @staticmethod
    def _answers(connection, user_id, *, for_matching=False):
        result = {}
        for row in connection.execute("SELECT * FROM taste_answers WHERE user_id=?", (user_id,)):
            answer = {"items": json.loads(row["items_json"]), "tier": row["tier"], "skipped": bool(row["skipped"]),
                      "updated_at": row["updated_at"]}
            answer["revision"] = answer_revision(answer, row["approval_nonce"])
            result[row["topic"]] = answer
        if for_matching and not TasteService._politics_consent(connection, user_id)["active"]:
            result.pop("politics", None)
        return result

    @staticmethod
    def _politics_consent(connection, user_id):
        row = connection.execute(
            "SELECT notice_version,choice,recorded_at,source FROM politics_consent_decisions WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,)).fetchone()
        decision = dict(row) if row is not None else None
        return {"notice_version": agent_texts.POLITICS_CONSENT_VERSION,
                "statement": agent_texts.POLITICS_CONSENT_STATEMENT,
                "active": bool(row is not None and row["choice"] == "granted"
                               and row["notice_version"] == agent_texts.POLITICS_CONSENT_VERSION),
                "decision": decision,
                "storage_note": agent_texts.NEVER_SHARE_STORAGE_NOTE,
                "record_note": agent_texts.CONSENT_ATTESTATION_NOTE}

    def politics_consent(self, user_id):
        with self.database.connect() as connection:
            require_owner(connection, user_id)
            return self._politics_consent(connection, user_id)

    def decide_politics_consent(self, user_id, fields):
        if (not isinstance(fields, dict) or not set(fields) <= {"consent", "notice_version", "approved_by_owner"}
                or type(fields.get("consent")) is not bool):
            raise AgentError(422, "Send consent as true or false, with notice_version and approved_by_owner for a grant.")
        if fields["consent"] and (fields.get("notice_version") != agent_texts.POLITICS_CONSENT_VERSION
                                  or fields.get("approved_by_owner") is not True):
            raise AgentError(409, "Present the current political-processing consent statement and obtain an explicit yes before attesting to it.")
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            connection.execute(
                "INSERT INTO politics_consent_decisions(user_id,notice_version,choice,recorded_at,source) VALUES(?,?,?,?,?)",
                (user_id, agent_texts.POLITICS_CONSENT_VERSION, "granted" if fields["consent"] else "withdrawn",
                 storage_time(self.clock()), "agent_attestation"))
            if not fields["consent"]:
                connection.execute("DELETE FROM taste_disclosures WHERE user_id=? AND topic='politics'", (user_id,))
                connection.execute("DELETE FROM taste_answers WHERE user_id=? AND topic='politics'", (user_id,))
            return self._politics_consent(connection, user_id)

    @staticmethod
    def _state(connection, user_id):
        row = connection.execute("SELECT * FROM interview_state WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else {"started_at": None, "completed_at": None, "last_checkin_at": None, "checkin_count": 0}

    @staticmethod
    def _member(connection, user_id):
        row = connection.execute("SELECT * FROM member_profiles WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return {"public_id": None, "first_name": None, "age": None, "sex": None, "show_name_on_leaderboards": False,
                    "signature_opt_in": False, "signature_includes_code": False}
        return {"public_id": row["public_id"], "first_name": row["first_name"], "age": row["age"], "sex": row["sex"],
                "show_name_on_leaderboards": bool(row["show_name_on_leaderboards"]),
                "signature_opt_in": bool(row["signature_opt_in"]), "signature_includes_code": bool(row["signature_includes_code"])}

    def interview(self, user_id, connection=None):
        if connection is None:
            with self.database.connect() as opened:
                return self.interview(user_id, opened)
        require_owner(connection, user_id)
        config = growth_config.load(connection)
        answers = self._answers(connection, user_id)
        state = self._state(connection, user_id)
        answered = [topic for topic, answer in answers.items() if not answer["skipped"]]
        # Keep building over time: open topics first, then the stalest answers.
        open_topics = [topic for topic in TOPICS if topic not in answers]
        stale = sorted((answer["updated_at"], topic) for topic, answer in answers.items())
        next_topics = (open_topics + [topic for _, topic in stale])[:2]
        return {"topics": catalogue(), "answers": answers, "state": state,
                "politics_consent": self._politics_consent(connection, user_id),
                "answered": len(answered), "minimum_to_complete": config["interview_min_topics"],
                "complete": state["completed_at"] is not None,
                "next_questions": [{"topic": topic, "ask": TOPICS[topic][0]} for topic in next_topics]}

    def save_answers(self, user_id, answers):
        if not isinstance(answers, list) or not 1 <= len(answers) <= len(TOPICS):
            raise AgentError(422, f"Send 1 to {len(TOPICS)} answers.")
        cleaned = [validate_answer(answer) for answer in answers]
        if len({answer["topic"] for answer in cleaned}) != len(cleaned):
            raise AgentError(422, "Send each topic at most once.")
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            if any(answer["topic"] == "politics" and not answer["skipped"] for answer in cleaned) \
                    and not self._politics_consent(connection, user_id)["active"]:
                raise AgentError(409, "Political answers require separate current processing consent, including ask-first and never-share answers. You can skip politics.")
            previous = {row["topic"]: row for row in connection.execute("SELECT * FROM taste_answers WHERE user_id=?", (user_id,))}
            connection.execute("INSERT OR IGNORE INTO interview_state(user_id,started_at) VALUES(?,?)", (user_id, storage_time(now)))
            for answer in cleaned:
                old = previous.get(answer["topic"])
                changed = (old is None or json.loads(old["items_json"]) != answer["items"]
                           or old["tier"] != answer["tier"] or bool(old["skipped"]) != answer["skipped"])
                nonce = secrets.token_hex(16) if changed else old["approval_nonce"]
                connection.execute(
                    "INSERT INTO taste_answers(user_id,topic,items_json,tier,skipped,updated_at,approval_nonce) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(user_id,topic) DO UPDATE SET items_json=excluded.items_json,tier=excluded.tier,"
                    "skipped=excluded.skipped,updated_at=excluded.updated_at,approval_nonce=excluded.approval_nonce",
                    (user_id, answer["topic"], json.dumps(answer["items"], ensure_ascii=False), answer["tier"], int(answer["skipped"]), storage_time(now), nonce))
                if answer["tier"] != "ask" or changed:
                    # A grant covers the approved answer, not future values
                    # under the same topic. An identical save preserves it.
                    connection.execute("DELETE FROM taste_disclosures WHERE user_id=? AND topic=?", (user_id, answer["topic"]))
            public_id(connection, user_id, now)
            return self.interview(user_id, connection)

    def complete(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            answered = connection.execute("SELECT COUNT(*) FROM taste_answers WHERE user_id=? AND skipped=0", (user_id,)).fetchone()[0]
            if answered < config["interview_min_topics"]:
                raise AgentError(409, f"Answer at least {config['interview_min_topics']} topics before completing the interview. Skipped topics do not count.")
            now = storage_time(self.clock())
            connection.execute("INSERT OR IGNORE INTO interview_state(user_id,started_at) VALUES(?,?)", (user_id, now))
            connection.execute("UPDATE interview_state SET completed_at=COALESCE(completed_at,?) WHERE user_id=?", (now, user_id))
            if self.referrals is not None:
                self.referrals.on_event(connection, user_id, "interview", config)
            return self.interview(user_id, connection)

    def update_member(self, user_id, fields):
        allowed = {"first_name", "age", "sex", "show_name_on_leaderboards", "signature_opt_in", "signature_includes_code", "approved_by_owner"}
        if not isinstance(fields, dict) or not fields or not set(fields) <= allowed:
            raise AgentError(422, "Send any of: " + ", ".join(sorted(allowed - {"approved_by_owner"})) + ".")
        updates = {}
        if "first_name" in fields:
            updates["first_name"] = None if fields["first_name"] is None else clean_text(fields["first_name"], "first_name", 40)
            if updates["first_name"] and " " in updates["first_name"]:
                raise AgentError(422, "first_name is a first name only, without a surname.")
        if "age" in fields:
            if fields["age"] is not None and (type(fields["age"]) is not int or not 18 <= fields["age"] <= 120):
                raise AgentError(422, "age must be a whole number from 18 to 120, or null. Connect the Bots is for adults.")
            updates["age"] = fields["age"]
        if "sex" in fields:
            if fields["sex"] is not None and fields["sex"] not in SEXES:
                raise AgentError(422, "sex must be one of " + ", ".join(SEXES) + ", or null.")
            updates["sex"] = fields["sex"]
        for flag in ("show_name_on_leaderboards", "signature_opt_in", "signature_includes_code"):
            if flag in fields:
                if type(fields[flag]) is not bool:
                    raise AgentError(422, f"{flag} must be true or false.")
                if fields[flag] and fields.get("approved_by_owner") is not True:
                    raise AgentError(409, f"Turning on {flag} needs your person's yes. Send approved_by_owner: true after they agree.")
                updates[flag] = int(fields[flag])
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            public_id(connection, user_id, now)
            for column, value in updates.items():
                connection.execute(f"UPDATE member_profiles SET {column}=?,updated_at=? WHERE user_id=?", (value, storage_time(now), user_id))
            if updates.get("signature_opt_in") or updates.get("signature_includes_code"):
                connection.execute("INSERT INTO share_approvals(id,user_id,kind,created_at) VALUES(lower(hex(randomblob(16))),?,?,?)",
                                   (user_id, "signature", storage_time(now)))
            return self._member(connection, user_id)

    def member(self, user_id):
        with self.database.connect() as connection:
            require_owner(connection, user_id)
            return self._member(connection, user_id)

    def disclose(self, user_id, other_public_id, topics, approved, revisions=None):
        if approved is not True:
            raise AgentError(409, "Sharing an ask-first answer needs your person's yes. Send approved_by_owner: true after they agree.")
        if not isinstance(topics, list) or not 1 <= len(topics) <= len(TOPICS) or any(topic not in TOPICS for topic in topics):
            raise AgentError(422, "topics must list interview topics.")
        if not isinstance(revisions, dict) or set(revisions) != set(topics):
            raise AgentError(409, "Read the current answers and send each approved topic's exact revision.")
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            other = user_for_public_id(connection, other_public_id)
            if other is None or other == user_id or blocked(connection, user_id, other):
                raise AgentError(404, "That member is not available.")
            answers = self._answers(connection, user_id)
            if any(answers.get(topic, {}).get("tier") != "ask" for topic in topics):
                raise AgentError(409, "Only answers marked ask first can be disclosed. Never-share answers stay private.")
            if any(revisions[topic] != answers[topic]["revision"] for topic in topics):
                raise AgentError(409, "An answer changed. Show the current answer and obtain fresh approval before sharing it.")
            if "politics" in topics and not self._politics_consent(connection, user_id)["active"]:
                raise AgentError(409, "Political processing consent is not current. Skip politics or obtain separate consent first.")
            for topic in topics:
                connection.execute("INSERT OR IGNORE INTO taste_disclosures(user_id,other_user_id,topic,granted_at) VALUES(?,?,?,?)",
                                   (user_id, other, topic, storage_time(self.clock())))
            return {"disclosed": sorted(topics), "to": other_public_id}

    # Personality fingerprint ---------------------------------------------------
    @staticmethod
    def _fingerprint(connection, user_id):
        row = connection.execute("SELECT * FROM personality_fingerprints WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        values = personality.parse(row["fingerprint"])
        current = row["schema"] == personality.SCHEMA and row["notice_version"] == agent_texts.PERSONALITY_CONSENT_VERSION
        return {"schema": row["schema"], "fingerprint": row["fingerprint"], "tier": row["tier"],
                "notice_version": row["notice_version"], "approved_at": row["approved_at"], "updated_at": row["updated_at"],
                "assessed": sum(value is not None for value in values), "of": len(values),
                "used_for_matching": current and row["tier"] == "share",
                "readout": personality.readout(values)}

    @staticmethod
    def _fingerprint_for_matching(connection, user_id):
        row = connection.execute("SELECT * FROM personality_fingerprints WHERE user_id=?", (user_id,)).fetchone()
        if (row is None or row["tier"] != "share" or row["schema"] != personality.SCHEMA
                or row["notice_version"] != agent_texts.PERSONALITY_CONSENT_VERSION):
            return None
        return personality.parse(row["fingerprint"])

    def personality(self, user_id):
        with self.database.connect() as connection:
            require_owner(connection, user_id)
            return {"schema": personality.SCHEMA, "encoding": agent_texts.PERSONALITY_ENCODING,
                    "import_prompt": agent_texts.PERSONALITY_IMPORT_PROMPT,
                    "traits": personality.catalogue(), "minimum_assessed": personality.MINIMUM_ASSESSED,
                    "tiers": list(personality.TIERS),
                    "consent": {"notice_version": agent_texts.PERSONALITY_CONSENT_VERSION,
                                "statement": agent_texts.PERSONALITY_CONSENT_STATEMENT,
                                "record_note": agent_texts.CONSENT_ATTESTATION_NOTE},
                    "current": self._fingerprint(connection, user_id)}

    def save_personality(self, user_id, fields):
        allowed = {"schema", "fingerprint", "tier", "notice_version", "approved_by_owner"}
        if not isinstance(fields, dict) or not set(fields) <= allowed or not {"schema", "fingerprint"} <= set(fields):
            raise AgentError(422, "Send schema, fingerprint, tier, notice_version and approved_by_owner.")
        if fields["schema"] != personality.SCHEMA:
            raise AgentError(422, f"schema must be {personality.SCHEMA}. Read GET /api/v1/agent/personality for the current traits.")
        personality.parse(fields["fingerprint"])  # refuses a malformed or mostly empty string
        tier = fields.get("tier", "share")
        if tier not in personality.TIERS:
            raise AgentError(422, "tier must be share or never.")
        if fields.get("notice_version") != agent_texts.PERSONALITY_CONSENT_VERSION or fields.get("approved_by_owner") is not True:
            raise AgentError(409, "Show your person the readout of every trait and the current consent statement, "
                                  "and obtain their explicit yes before sending a fingerprint.")
        encoded = fields["fingerprint"].lower()
        now = storage_time(self.clock())
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            connection.execute(
                "INSERT INTO personality_fingerprints(user_id,schema,fingerprint,tier,notice_version,approved_at,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET schema=excluded.schema,fingerprint=excluded.fingerprint,tier=excluded.tier,"
                "notice_version=excluded.notice_version,approved_at=excluded.approved_at,updated_at=excluded.updated_at",
                (user_id, personality.SCHEMA, encoded, tier, agent_texts.PERSONALITY_CONSENT_VERSION, now, now))
            public_id(connection, user_id, self.clock())
            saved = self._fingerprint(connection, user_id)
            return {"saved": True, "current": saved,
                    "remember": "Your person can see this readout on their dashboard and can ask you to change or delete it at any time."}

    def delete_personality(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            removed = connection.execute("DELETE FROM personality_fingerprints WHERE user_id=?", (user_id,)).rowcount
            return {"deleted": bool(removed), "note": "The fingerprint is removed and no longer used for matching."}

    # Match tuning ----------------------------------------------------------------
    @staticmethod
    def _tuning(connection, user_id):
        """The owner's stored match settings, or None for the defaults."""
        row = connection.execute("SELECT settings_json FROM match_settings WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        stored = json.loads(row["settings_json"])
        # Re-validate on read, so a trait or topic retired later is refused
        # loudly in tests rather than silently mis-applied.
        return match_tuning.validate({"areas": stored["areas"], "preferences": stored["preferences"]})

    def _tuning_view(self, connection, user_id):
        stored = connection.execute("SELECT updated_at FROM match_settings WHERE user_id=?", (user_id,)).fetchone()
        settings = self._tuning(connection, user_id) or match_tuning.default_settings()
        return {"areas": {area: personality.LEVELS[level] for area, level in settings["areas"].items()},
                "preferences": [entry | {"reads_as": match_tuning.describe(entry)} for entry in settings["preferences"]],
                "customised": stored is not None, "updated_at": stored["updated_at"] if stored else None}

    def match_settings(self, user_id):
        with self.database.connect() as connection:
            require_owner(connection, user_id)
            return {"current": self._tuning_view(connection, user_id), **match_tuning.catalogue()}

    def save_match_settings(self, user_id, fields, *, approved=None):
        """Replace the parts sent; the rest stay. Agents must state the owner's yes."""
        require_approval(fields.get("approved_by_owner") if approved is None else approved)
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            settings = match_tuning.validate(fields, self._tuning(connection, user_id))
            encoded = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            connection.execute(
                "INSERT INTO match_settings(user_id,settings_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at "
                "WHERE match_settings.settings_json IS NOT excluded.settings_json",
                (user_id, encoded, storage_time(self.clock())))
            return {"current": self._tuning_view(connection, user_id),
                    "note": "Saved. Your next matches use these settings."}

    def reset_match_settings(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            removed = connection.execute("DELETE FROM match_settings WHERE user_id=?", (user_id,)).rowcount
            return {"reset": bool(removed), "current": self._tuning_view(connection, user_id)}

    def _disclosed(self, connection, owner, other):
        return {row[0] for row in connection.execute("SELECT topic FROM taste_disclosures WHERE user_id=? AND other_user_id=?", (owner, other))}

    def _pair(self, connection, user_id, other, mine, my_print, my_tuning):
        their_print = self._fingerprint_for_matching(connection, other)
        fit = personality.pair_fit(my_print, their_print, match_tuning.personality_levels(my_tuning))
        return pair_match(mine, self._answers(connection, other, for_matching=True), self._disclosed(connection, user_id, other),
                          self._disclosed(connection, other, user_id), fit, tuning=my_tuning,
                          their_tuning=self._tuning(connection, other), prints=(my_print, their_print))

    def pair_view(self, connection, user_id, other):
        """Why ``user_id`` and ``other`` match, from ``user_id``'s side, or None.

        Uses exactly the rules of :meth:`matches`: both must have finished the
        interview, be active and not blocked, and the pair must qualify under
        both people's settings. Used to open and re-check match introductions.
        """
        if user_id == other:
            return None
        for person in (user_id, other):
            row = connection.execute("SELECT 1 FROM interview_state s JOIN users u ON u.id=s.user_id "
                                     "WHERE s.user_id=? AND s.completed_at IS NOT NULL AND u.suspended_at IS NULL", (person,)).fetchone()
            if row is None:
                return None
        if blocked(connection, user_id, other):
            return None
        return self._pair(connection, user_id, other, self._answers(connection, user_id, for_matching=True),
                          self._fingerprint_for_matching(connection, user_id), self._tuning(connection, user_id))

    def matches(self, connection, user_id, limit, *, record=False):
        """Rank completed members by mutually visible shared taste."""
        mine = self._answers(connection, user_id, for_matching=True)
        my_print = self._fingerprint_for_matching(connection, user_id)
        my_tuning = self._tuning(connection, user_id)
        if not mine and my_print is None and not (my_tuning and my_tuning["preferences"]):
            return []
        candidates = connection.execute(
            "SELECT s.user_id FROM interview_state s JOIN users u ON u.id=s.user_id "
            "WHERE s.completed_at IS NOT NULL AND u.suspended_at IS NULL AND s.user_id<>? "
            "AND NOT EXISTS (SELECT 1 FROM user_blocks b WHERE (b.blocker_id=? AND b.blocked_id=s.user_id) OR (b.blocker_id=s.user_id AND b.blocked_id=?))",
            (user_id, user_id, user_id)).fetchall()
        now = self.clock()
        found = []
        for row in candidates:
            other = row["user_id"]
            result = self._pair(connection, user_id, other, mine, my_print, my_tuning)
            if result is None:
                continue
            seen = connection.execute("SELECT first_seen_at FROM taste_matches_seen WHERE user_id=? AND other_user_id=?", (user_id, other)).fetchone()
            member = self._member(connection, other)
            found.append((result["strength"], other, result, seen, member))
        found.sort(key=lambda entry: (-entry[0], entry[4]["public_id"] or ""))
        output = []
        for strength, other, result, seen, member in found[:limit]:
            if record and seen is None:
                connection.execute("INSERT INTO taste_matches_seen(user_id,other_user_id,first_seen_at) VALUES(?,?,?)", (user_id, other, storage_time(now)))
                if self.referrals is not None:
                    config = growth_config.load(connection)
                    self.referrals.on_event(connection, user_id, "first_match", config)
                    self.referrals.on_event(connection, other, "first_match", config)
            output.append({"member": {"public_id": member["public_id"] or public_id(connection, other, now),
                                      "first_name": member["first_name"]},
                           "new": seen is None, "explanation": result["explanation"], "shared": result["shared"],
                           "compatible": result["compatible"], "personality": result["personality"],
                           "asked_for": result["asked_for"], "ask_first_overlaps": result["ask_first_overlaps"]})
        return output

    def export_owner(self, user_id, connection):
        return {"member_profile": self._member(connection, user_id), "interview": self._state(connection, user_id),
                "answers": self._answers(connection, user_id),
                "personality_fingerprint": self._fingerprint(connection, user_id),
                "match_settings": self._tuning_view(connection, user_id),
                "politics_consent_decisions": [dict(row) for row in connection.execute(
                    "SELECT notice_version,choice,recorded_at,source FROM politics_consent_decisions WHERE user_id=? ORDER BY id", (user_id,))],
                "disclosures": [dict(row) for row in connection.execute(
                    "SELECT m.public_id AS to_member,d.topic,d.granted_at FROM taste_disclosures d "
                    "LEFT JOIN member_profiles m ON m.user_id=d.other_user_id WHERE d.user_id=? ORDER BY d.granted_at", (user_id,))]}
