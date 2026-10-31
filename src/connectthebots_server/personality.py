"""Personality fingerprint: a compact, owner-approved trait vector and its matching rules.

A fingerprint is 96 traits, each one byte written as two lowercase hex digits
(192 characters in all). ``00`` is the far left of a trait's scale, ``fe`` the
far right and ``7f`` the middle; ``ff`` means "not assessed" and is ignored.
The person's agent estimates the values from what it already knows about them,
shows them a plain-language readout and sends the string only after their yes.

Matching never returns values, distances or scores. It names traits two people
share (both near the same end of an *alike* trait) and pairings that fit
together (opposite ends of a *complement* trait, such as one who likes to lead
and one who is happy to follow). Middling values say little and never count.
This module is pure: no storage, no clock and no agent text.
"""
import re

from .agent_common import AgentError


SCHEMA = "ctb-personality-v1"
UNKNOWN = 0xFF
MIDDLE = 127
MINIMUM_ASSESSED = 12

# Thresholds on the 0-254 scale. Every rule below has a fictional test.
ALIKE_LEAN = 40          # each person at least this far from the middle, on the same side
ALIKE_CLOSE = 70         # and no further apart than this
COMPLEMENT_LEAN = 30     # each person at least this far from the middle, on opposite sides
COMPLEMENT_GAP = 100     # and at least this far apart
CLASH_GAP = 170          # an alike trait this far apart is a difference worth knowing
MIN_PERSONALITY_ONLY = 5  # signals needed to match on personality without shared taste

# (key, left label, right label, rule, left phrase, right phrase, what to look for)
# alike phrases complete "you're both ..."; complement phrases complete
# "one of you ..." and "the other ...".
DIMENSIONS = (
    ("outlook", "wistful", "upbeat", "alike", "on the wistful side", "upbeat",
     "General outlook day to day, sad-leaning to happy-leaning. Not mood problems or health: if they are going through a hard time, leave it unassessed."),
    ("energy", "calm", "lively", "alike", "calm", "lively", "How much energy they bring to a room."),
    ("sociability", "small groups", "big crowds", "alike", "happiest in small groups", "happiest in a crowd", "Quiet evenings with one or two people, or big busy gatherings."),
    ("pace", "unhurried", "fast-paced", "alike", "unhurried", "fast-paced", "The speed they like to live and work at."),
    ("novelty", "loyal to favourites", "always after something new", "alike", "loyal to your favourites", "always after something new", "Rewatching and revisiting, or always trying the next thing."),
    ("thinking", "practical", "theoretical", "alike", "practical thinkers", "theory lovers", "Hands-on and concrete, or ideas and theory."),
    ("decisions", "gut feel", "weighs everything", "alike", "gut-feel deciders", "careful deciders", "How they make choices."),
    ("planning", "spontaneous", "planner", "alike", "spontaneous", "planners", "Making it up as they go, or plans and lists."),
    ("risk", "cautious", "adventurous", "alike", "cautious", "adventurous", "Appetite for risk and the unknown."),
    ("directness", "tactful", "blunt", "alike", "tactful", "straight talkers", "Softening things, or saying them straight."),
    ("humour", "dry", "silly", "alike", "dry-humoured", "silly", "Deadpan and understated, or daft and playful."),
    ("warmth", "reserved", "openly warm", "alike", "reserved at first", "openly warm", "How quickly they show warmth to new people."),
    ("competition", "cooperative", "competitive", "alike", "cooperative", "competitive", "Playing together, or playing to win."),
    ("debate", "keeps the peace", "loves a debate", "alike", "peacekeepers", "keen debaters", "Avoiding arguments, or enjoying a good one."),
    ("teamwork", "works alone", "works in a team", "alike", "independent workers", "team players", "Where they do their best work."),
    ("drive", "content", "driven", "alike", "content with life as it is", "driven", "Ambition and how hard they push."),
    ("order", "relaxed about mess", "neat and orderly", "alike", "relaxed about mess", "neat and orderly", "Tidiness and order."),
    ("chronotype", "early bird", "night owl", "alike", "early birds", "night owls", "When they come alive."),
    ("going_out", "homebody", "out and about", "alike", "homebodies", "always out and about", "Staying in, or going out."),
    ("outdoors", "indoors", "outdoors", "alike", "indoor people", "outdoor people", "Where they like to spend free time."),
    ("depth", "keeps it light", "wants deep talk", "alike", "happy keeping things light", "fans of deep conversations", "Small talk and banter, or big conversations."),
    ("contact", "now and then", "stays in touch often", "alike", "fine with keeping in touch now and then", "keen to stay in touch often", "How often they like to hear from people."),
    ("meeting", "online", "in person", "alike", "happy online", "keen to meet in person", "Online chats, or meeting face to face."),
    ("openness", "keeps feelings private", "wears heart on sleeve", "alike", "private about feelings", "open about feelings", "How openly they share how they feel."),
    ("convention", "traditional", "unconventional", "alike", "traditional", "unconventional", "Tried and tested ways, or their own way."),
    ("breadth", "goes deep on a few things", "dabbles in lots", "alike", "deep specialists", "happy dabblers", "A few deep interests, or many light ones."),
    ("initiative", "happy to follow", "likes to lead", "complement", "is happy to follow a lead", "likes to take the lead", "Taking charge, or backing someone else's plan."),
    ("conversation", "listener", "talker", "complement", "loves to listen", "loves to talk", "In a conversation, mostly listening or mostly talking."),
    ("scope", "detail", "big picture", "complement", "loves the details", "sees the big picture", "Zooming in on details, or zooming out."),
    ("project_role", "finisher", "starter", "complement", "loves finishing things", "loves starting things", "Starting new things, or seeing them through."),
    ("style", "analytical", "artistic", "complement", "thinks in numbers and systems", "thinks in pictures and stories", "Analytical, or artistic and visual."),
    ("mentoring", "keen to learn", "keen to teach", "complement", "is keen to learn", "is keen to teach", "Right now, more keen to learn from people or to teach them."),    # Temperament
    ("patience", "restless", "patient", "alike", "quick to get restless", "patient", "How long they happily wait for things."),
    ("composure", "excitable", "unflappable", "alike", "excitable", "unflappable", "How they react when things go wrong."),
    ("seriousness", "playful", "serious-minded", "alike", "playful", "serious-minded", "Default mood: messing about, or getting on with it."),
    ("sensitivity", "thick-skinned", "sensitive", "alike", "thick-skinned", "sensitive souls", "How strongly a sharp word lands."),
    ("confidence", "modest", "self-assured", "alike", "modest", "self-assured", "How they talk about their own abilities."),
    ("adaptability", "fond of routine", "adapts easily", "alike", "fond of routine", "easily adaptable", "How they handle sudden changes of plan."),
    ("intensity", "easygoing", "intense", "alike", "easygoing", "intense", "How fully they throw themselves into things."),
    ("reflection", "acts first", "reflects first", "alike", "doers first", "reflective", "Jump in, or think it over first."),
    # Thinking
    ("imagination", "realistic", "imaginative", "alike", "realistic", "imaginative", "Sticking to what is, or dreaming up what could be."),
    ("scepticism", "trusting", "sceptical", "alike", "trusting", "sceptical", "Taking things at face value, or wanting proof."),
    ("learning_style", "learns by doing", "learns by reading", "alike", "learn-by-doing types", "learn-by-reading types", "How they pick up something new."),
    ("problem_solving", "tried and tested", "inventive", "alike", "fans of tried-and-tested fixes", "inventive problem-solvers", "Known fixes, or new approaches."),
    ("focus", "multitasker", "one thing at a time", "alike", "multitaskers", "one-thing-at-a-time people", "Juggling, or single focus."),
    ("understanding", "happy with the gist", "needs to know how it works", "alike", "happy with the gist", "people who need to know how things work", "How deep they need to understand things."),
    ("precision", "rough and ready", "precise", "alike", "rough-and-ready", "precise", "Ballpark, or exact."),
    ("time_horizon", "lives in the moment", "plans for the long term", "alike", "live-in-the-moment types", "long-term thinkers", "Today, or years ahead."),
    # Social
    ("spotlight", "blends in", "loves the spotlight", "alike", "happy blending in", "happy in the spotlight", "In a group, background or centre stage."),
    ("strangers", "slow to warm up", "chatty with strangers", "alike", "slow to warm up", "chatty with strangers", "How they are with people they have just met."),
    ("circle", "small close circle", "wide circle", "alike", "keepers of a small close circle", "people with a wide circle", "A few close friends, or many friends."),
    ("space", "likes company around", "needs plenty of space", "alike", "happiest with company around", "people who need plenty of space", "How much time alone they need."),
    ("timekeeping", "relaxed about timing", "punctual", "alike", "relaxed about timing", "punctual", "Running late, or always on time."),
    ("teasing", "earnest", "loves a tease", "alike", "earnest", "fond of teasing", "Sincere, or playful teasing."),
    ("privacy", "open book", "private person", "alike", "open books", "private people", "How much of their life they share."),
    ("hosting", "loves being a guest", "loves hosting", "complement", "loves being a guest", "loves hosting", "Throwing the party, or turning up to it."),
    ("support_style", "offers a listening ear", "offers practical help", "complement", "offers a listening ear", "offers practical help", "When a friend struggles, listening or fixing."),
    # Communication
    ("message_length", "short messages", "long messages", "alike", "short-message people", "long-message people", "Typical length of what they write."),
    ("medium", "texter", "caller", "alike", "texters", "callers", "Typing, or calls and voice notes."),
    ("reply_speed", "replies when free", "replies straight away", "alike", "relaxed repliers", "quick repliers", "How quickly they answer messages."),
    ("storytelling", "sticks to facts", "tells stories", "alike", "fact-tellers", "storytellers", "Bare facts, or the whole story."),
    ("feedback", "gentle feedback", "frank feedback", "alike", "fond of gentle feedback", "fond of frank feedback", "How they like to hear criticism."),
    ("small_talk", "skips small talk", "enjoys small talk", "alike", "small-talk skippers", "small-talk lovers", "Chit-chat: chore or pleasure."),
    ("topics", "talks about people", "talks about ideas", "alike", "people-talkers", "idea-talkers", "What their conversations drift towards."),
    # Disagreement
    ("conflict_style", "cools off first", "talks it out now", "alike", "cool-off-first types", "talk-it-out-now types", "After a disagreement, space first or talk it through."),
    ("compromise", "holds firm", "meets halfway", "alike", "firm", "quick to compromise", "How readily they give ground."),
    ("rules", "bends rules", "follows rules", "alike", "rule-benders", "rule-followers", "Rules as guidelines, or rules as rules."),
    # Working together
    ("work_rhythm", "works in bursts", "works steadily", "alike", "burst workers", "steady workers", "Sprints, or a steady pace."),
    ("deadlines", "last minute", "early", "alike", "last-minute finishers", "early finishers", "When they finish against a deadline."),
    ("roles", "loose roles", "clear roles", "alike", "fine with loose roles", "keen on clear roles", "Figuring it out together, or clear jobs."),
    ("polish", "ships fast", "polishes", "alike", "ship-it-fast types", "polishers", "Done is better than perfect, or perfect is better than done."),
    ("commitment", "dips in", "all in", "alike", "dip-in types", "all-in types", "How much of themselves they give a project."),
    ("project_scale", "small and cosy", "big and bold", "alike", "fans of small cosy projects", "fans of big bold projects", "The size of thing they like to take on."),
    ("tech", "low-tech", "high-tech", "alike", "low-tech", "high-tech", "Pen and paper, or the latest gadget."),
    ("decision_role", "weighs the options", "makes the call", "complement", "weighs the options", "makes the call", "In a group decision, laying out options or deciding."),
    ("energy_role", "steady presence", "brings the spark", "complement", "is the steady presence", "brings the spark", "In a group, the calm one or the one who lifts the energy."),
    ("make_or_tell", "builds it", "tells people about it", "complement", "loves building the thing", "loves telling people about it", "Making the thing, or spreading the word."),
    ("hands_or_organise", "makes by hand", "organises", "complement", "loves making things by hand", "loves organising", "Hands on the work, or arranging the work."),
    ("out_front", "behind the scenes", "out front", "complement", "is happy behind the scenes", "is happy out front", "Happiest unseen, or facing people."),
    ("advice", "seeks other views", "gives advice", "complement", "likes hearing other views", "loves giving advice", "Asking what others think, or offering a view."),
    # Everyday life
    ("activity", "relaxed about exercise", "active", "alike", "relaxed about exercise", "active", "How much they move for fun."),
    ("travel", "home comforts", "travel bug", "alike", "fond of home comforts", "bitten by the travel bug", "Staying close to home, or exploring."),
    ("setting", "countryside", "city", "alike", "country people", "city people", "Where they feel most at home."),
    ("noise", "quiet places", "lively places", "alike", "fans of quiet places", "fans of lively places", "Cafe corner, or buzzing bar."),
    ("food_adventure", "familiar food", "adventurous eater", "alike", "fans of familiar food", "adventurous eaters", "Favourite dish, or anything new on the menu."),
    ("weekends", "rest", "full of plans", "alike", "weekend resters", "weekend planners", "Empty weekends, or full ones."),
    ("animals", "not an animal person", "animal lover", "alike", "not big on animals", "animal lovers", "How they feel about pets and animals."),
    ("online_life", "mostly offline", "always online", "alike", "mostly offline", "always online", "How much of life happens on screens."),
    ("mainstream", "popular picks", "niche picks", "alike", "fans of the popular stuff", "fans of niche stuff", "Big hits, or deep cuts."),
    ("making", "enjoys", "makes", "alike", "enjoyers", "makers", "Enjoying films, music and games, or making their own."),
    ("nostalgia", "present-focused", "nostalgic", "alike", "present-focused", "nostalgic", "How often they look back fondly."),
    ("hobby_intensity", "casual hobbyist", "intense hobbyist", "alike", "casual hobbyists", "intense hobbyists", "Dabbling for fun, or going deep and serious."),
    ("surprises", "likes to know what's coming", "loves surprises", "alike", "fans of knowing what's coming", "lovers of surprises", "Surprise party: delight or dread."),
    ("edge", "gentle humour", "edgy humour", "alike", "gentle-humoured", "fans of edgy humour", "How near the bone their jokes go."),
    ("community", "keeps to themselves", "community-minded", "alike", "happy keeping to yourselves", "community-minded", "Involvement in local groups and helping out."),
    ("learning_drive", "knows enough", "lifelong learner", "alike", "content with what you know", "lifelong learners", "Appetite for learning new things."),
)
KEYS = tuple(entry[0] for entry in DIMENSIONS)
LENGTH = 2 * len(DIMENSIONS)
TIERS = ("share", "never")


def catalogue():
    """The trait list as agents receive it, in fingerprint order."""
    return [{"position": index, "hex_offset": 2 * index, "key": key, "left": left, "right": right, "rule": rule, "look_for": hint}
            for index, (key, left, right, rule, _, _, hint) in enumerate(DIMENSIONS)]


def parse(value):
    """Return the trait values (``None`` when not assessed), or refuse."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{%d}" % LENGTH, value) is None:
        raise AgentError(422, f"fingerprint must be exactly {LENGTH} hex digits: two per trait, 00 to fe, ff for not assessed.")
    values = [int(value[index:index + 2], 16) for index in range(0, LENGTH, 2)]
    decoded = [None if number == UNKNOWN else number for number in values]
    if sum(number is not None for number in decoded) < MINIMUM_ASSESSED:
        raise AgentError(422, f"Assess at least {MINIMUM_ASSESSED} traits. Use ff for the rest.")
    return decoded


def _band(number, left, right):
    if number is None:
        return "not assessed"
    if number <= 50:
        return "strongly " + left
    if number <= 101:
        return "somewhat " + left
    if number <= 152:
        return f"in between {left} and {right}"
    if number <= 203:
        return "somewhat " + right
    return "strongly " + right


def readout(values):
    """Plain-language lines for the owner: what the string says, trait by trait."""
    return [{"key": key, "scale": f"{left} to {right}", "reads_as": _band(number, left, right)}
            for (key, left, right, *_), number in zip(DIMENSIONS, values)]


def pair_fit(mine, theirs):
    """Compare two shared fingerprints from the viewer's side, or return None.

    The result names traits only; it never carries values or the internal
    strength, which the caller uses for ordering and then drops.
    """
    if mine is None or theirs is None:
        return None
    alike, complements, differences, strength = [], [], [], 0
    for (key, _, _, rule, left_phrase, right_phrase, _), a, b in zip(DIMENSIONS, mine, theirs):
        if a is None or b is None:
            continue
        gap, side_a, side_b = abs(a - b), a - MIDDLE, b - MIDDLE
        if rule == "alike":
            if side_a * side_b > 0 and min(abs(side_a), abs(side_b)) >= ALIKE_LEAN and gap <= ALIKE_CLOSE:
                alike.append((min(abs(side_a), abs(side_b)), {"trait": key, "both": right_phrase if side_a > 0 else left_phrase}))
                strength += 1
            elif gap >= CLASH_GAP:
                differences.append(key)
                strength -= 1
        elif side_a * side_b < 0 and min(abs(side_a), abs(side_b)) >= COMPLEMENT_LEAN and gap >= COMPLEMENT_GAP:
            complements.append((gap, {"trait": key, "one_of_you": right_phrase, "the_other": left_phrase}))
            strength += 2
    if not alike and not complements:
        return None
    alike = [entry for _, entry in sorted(alike, key=lambda item: -item[0])]
    complements = [entry for _, entry in sorted(complements, key=lambda item: -item[0])]
    sentences = []
    if alike:
        sentences.append("Personality-wise, you're both " + _join([entry["both"] for entry in alike[:3]]) + ".")
    if complements:
        sentences.append("You could suit each other: " + "; ".join(
            f"one of you {entry['one_of_you']} and the other {entry['the_other']}" for entry in complements[:2]) + ".")
    return {"alike": alike, "complements": complements, "differences": differences,
            "explanation": " ".join(sentences), "strength": strength}


def _join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
