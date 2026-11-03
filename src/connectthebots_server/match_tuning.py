"""Owner match tuning: per-area strictness sliders and stated preferences.

An owner (or their agent, with the owner's yes) can make each area of matching
looser or tighter, and can state what they want in someone ("into jazz",
"a night owl"). The settings change only the owner's own match list, with two
reciprocal hard rules: a *strict* area rules out a clear clash on any of its
traits, and a *must* preference rules out anyone who clearly does not meet it.
Both apply in both directions, so neither person is shown someone whose own
settings rule them out. Other members never see anyone's settings.

Everything here is deterministic. Preferences are structured (a trait and a
side, or a topic and an item), never free text a model interprets. This module
is pure: no storage, no clock and no agent text.
"""
from . import personality
from .agent_common import AgentError, clean_text, normalize_item


AREAS = tuple(personality.AREAS) + ("interests",)
INTEREST_LABEL = ("Shared interests", "Films, music, teams, hobbies and the other favourites from your interview.")
IMPORTANCE = ("nice", "must")
MAX_PREFERENCES = 12
PREFERENCE_BONUS = 3
# A trait preference is met when the other person clearly leans the wanted way:
# the same lean an alike trait needs at the balanced level.
PREFERENCE_LEAN = personality.ALIKE_LEAN

# Interests slider: (weight multiplier for shared items, signals personality
# needs to make a match without shared interests; None = never).
INTEREST_LEVELS = {1: (1, 3), 2: (1, personality.MIN_PERSONALITY_ONLY), 3: (2, 8), 4: (3, None)}

LEVEL_MEANINGS = {
    "off": "Not used at all for your matches.",
    "loose": "Counts looser similarities: people only need to lean the same way.",
    "balanced": "The default.",
    "tight": "Needs closer, clearer similarities, and these count for more.",
    "strict": "Needs very close similarities, counts most, and a clear clash here rules a match out for both of you.",
}
INTEREST_MEANINGS = {
    "off": "Shared favourites are not used; personality and your stated preferences still are.",
    "loose": "Shared favourites count as usual, and personality alone can make a match more easily.",
    "balanced": "The default.",
    "tight": "Shared favourites count double, and personality alone needs more to make a match.",
    "strict": "Shared favourites count triple, and you only see people you share at least one favourite with.",
}


def default_settings():
    return {"areas": {area: personality.DEFAULT_LEVEL for area in AREAS}, "preferences": []}


def catalogue():
    """What an agent or the dashboard needs to show and change the settings."""
    areas = [{"area": area, "label": label, "about": about, "traits": list(keys)}
             for area, (label, about, keys) in personality.AREAS.items()]
    areas.append({"area": "interests", "label": INTEREST_LABEL[0], "about": INTEREST_LABEL[1], "traits": []})
    return {"areas": areas, "levels": list(personality.LEVELS), "default_level": personality.LEVELS[personality.DEFAULT_LEVEL],
            "level_meanings": LEVEL_MEANINGS, "interest_level_meanings": INTEREST_MEANINGS,
            "preference_kinds": {
                "trait": {"fields": ["kind", "trait", "want", "importance"], "want": ["left", "right"],
                          "about": "Someone who clearly leans to one end of a personality trait. Used only while both of you share a fingerprint. Trait keys and their left/right labels are in GET /api/v1/agent/personality."},
                "interest": {"fields": ["kind", "topic", "item", "importance"],
                             "topics": [topic for topic in _interest_topics()],
                             "about": "Someone who lists this item among the favourites they let you see."}},
            "importance": {"nice": "Ranks people who meet it higher.",
                           "must": "Rules out anyone who clearly does not meet it, in both directions. For a trait, people who have not shared that trait stay in; for an interest, the item must be among what they let you see."},
            "max_preferences": MAX_PREFERENCES}


def _interest_topics():
    from .taste import TOPICS
    return [topic for topic in TOPICS if topic not in {"looking_for", "politics"}]


def _level(value, area):
    if isinstance(value, str) and value in personality.LEVELS:
        return personality.LEVELS.index(value)
    if type(value) is int and 0 <= value < len(personality.LEVELS):
        return value
    raise AgentError(422, f"{area} must be one of " + ", ".join(personality.LEVELS) + " (or 0 to 4).")


def validate(fields, current=None):
    """Return complete, normalised settings. Omitted parts keep their current value."""
    if not isinstance(fields, dict) or not set(fields) <= {"areas", "preferences", "approved_by_owner"}:
        raise AgentError(422, "Send areas and/or preferences, with approved_by_owner.")
    settings = current or default_settings()
    areas = dict(settings["areas"])
    if "areas" in fields:
        if not isinstance(fields["areas"], dict) or not set(fields["areas"]) <= set(AREAS):
            raise AgentError(422, "areas maps area names to levels. Areas: " + ", ".join(AREAS) + ".")
        for area, value in fields["areas"].items():
            areas[area] = _level(value, area)
    preferences = settings["preferences"]
    if "preferences" in fields:
        raw = fields["preferences"]
        if not isinstance(raw, list) or len(raw) > MAX_PREFERENCES:
            raise AgentError(422, f"preferences is a list of up to {MAX_PREFERENCES} items.")
        preferences, seen = [], set()
        for entry in raw:
            preference = _preference(entry)
            key = tuple(sorted((name, normalize_item(value)) for name, value in preference.items() if name != "importance"))
            if key in seen:
                raise AgentError(422, "Each preference may appear once.")
            seen.add(key)
            preferences.append(preference)
    return {"areas": areas, "preferences": preferences}


def _preference(entry):
    if not isinstance(entry, dict):
        raise AgentError(422, "Each preference is an object.")
    kind, importance = entry.get("kind"), entry.get("importance", "nice")
    if importance not in IMPORTANCE:
        raise AgentError(422, "importance must be nice or must.")
    if kind == "trait":
        if set(entry) - {"kind", "trait", "want", "importance"}:
            raise AgentError(422, "A trait preference has kind, trait, want and importance.")
        if entry.get("trait") not in personality.KEYS:
            raise AgentError(422, "Unknown trait. Use a key from GET /api/v1/agent/personality.")
        if entry.get("want") not in {"left", "right"}:
            raise AgentError(422, "want must be left or right: the end of the trait's scale your person wants.")
        return {"kind": "trait", "trait": entry["trait"], "want": entry["want"], "importance": importance}
    if kind == "interest":
        if set(entry) - {"kind", "topic", "item", "importance"}:
            raise AgentError(422, "An interest preference has kind, topic, item and importance.")
        if entry.get("topic") not in _interest_topics():
            raise AgentError(422, "topic must be one of: " + ", ".join(_interest_topics()) + ".")
        item = clean_text(entry.get("item"), "item", 80)
        return {"kind": "interest", "topic": entry["topic"], "item": item, "importance": importance}
    raise AgentError(422, "kind must be trait or interest.")


def describe(preference):
    """Plain words for one preference, for the owner and for match reasons."""
    if preference["kind"] == "interest":
        return f"{preference['item']} ({preference['topic']})"
    entry = personality.DIMENSIONS[personality.KEYS.index(preference["trait"])]
    return entry[1] if preference["want"] == "left" else entry[2]


def _trait_state(preference, own_print, values):
    """True if clearly met, False if clearly not, None if not known.

    Like personality matching, a trait preference acts only when both people
    chose to share a fingerprint.
    """
    if own_print is None or values is None:
        return None
    value = values[personality.KEYS.index(preference["trait"])]
    if value is None:
        return None
    lean = value - personality.MIDDLE if preference["want"] == "right" else personality.MIDDLE - value
    return lean >= PREFERENCE_LEAN


def _interest_met(preference, visible):
    wanted = normalize_item(preference["item"])
    return any(normalize_item(item) == wanted for item in visible.get(preference["topic"], []))


def rules_out(settings, own_print, other_print, other_visible):
    """Whether the owner's strict areas or must preferences exclude this person.

    ``other_visible`` is only what the other person lets this owner see, so a
    rule can never act on hidden answers.
    """
    if settings is None:
        return False
    levels = {area: level for area, level in settings["areas"].items() if area != "interests"}
    if personality.strict_clashes(own_print, other_print, levels):
        return True
    for preference in settings["preferences"]:
        if preference["importance"] != "must":
            continue
        if preference["kind"] == "trait" and _trait_state(preference, own_print, other_print) is False:
            return True
        if preference["kind"] == "interest" and not _interest_met(preference, other_visible):
            return True
    return False


def met_preferences(settings, own_print, other_print, other_visible):
    if settings is None:
        return []
    met = []
    for preference in settings["preferences"]:
        if preference["kind"] == "trait" and _trait_state(preference, own_print, other_print) is True:
            met.append(preference)
        elif preference["kind"] == "interest" and _interest_met(preference, other_visible):
            met.append(preference)
    return met


def interest_rule(settings):
    """(level, weight multiplier, personality-only threshold or None)."""
    level = personality.DEFAULT_LEVEL if settings is None else settings["areas"].get("interests", personality.DEFAULT_LEVEL)
    if level == 0:
        return 0, 0, personality.MIN_PERSONALITY_ONLY
    return (level, *INTEREST_LEVELS[level])


def personality_levels(settings):
    if settings is None:
        return None
    return {area: level for area, level in settings["areas"].items() if area != "interests"}
