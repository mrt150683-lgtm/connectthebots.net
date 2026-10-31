"""Strict, bounded report input shared by bearer ingestion and paste previews."""

from datetime import datetime, timezone
import json
import re
import unicodedata
from urllib.parse import urlsplit


MAX_REPORT_BYTES = 32 * 1024
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z")
_SCORE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|percent\b|per\s+cent\b)"
    r"|\b(?:score|rating|confidence|compatibility|match|fit)(?:\s+(?:score|rating))?\s*(?:(?:is|of)\s*|[:=]\s*)?\d"
    r"|\b\d+(?:\.\d+)?\s*(?:out\s+of\s+|/\s*)(?:10|100)\b",
    re.IGNORECASE,
)


class ReportError(ValueError):
    """Fixed, user-safe failure details. Never include the submitted payload."""

    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


def _fail(detail):
    raise ReportError(422, detail)


def text_field(value, label, maximum, *, scores=True):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _fail(f"{label} must contain between 1 and {maximum} characters.")
    if any((ord(char) < 32 and char not in "\n\t") or 0xD800 <= ord(char) <= 0xDFFF or unicodedata.category(char) == "Cf" for char in value):
        _fail(f"{label} contains unsupported control characters.")
    value = value.strip()
    if scores and _SCORE.search(unicodedata.normalize("NFKC", value)):
        _fail("Reports use plain reasons, not scores or percentages. Remove the numeric score or percentage.")
    return value


def https_url(value, label):
    value = text_field(value, label, 2048, scores=False)
    if any(char.isspace() for char in value) or "\\" in value:
        _fail(f"{label} must be an HTTPS URL without spaces or credentials.")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == "https" and parsed.hostname and parsed.username is None and parsed.password is None
        valid = valid and (parsed.port is None or 1 <= parsed.port <= 65535)
        # Force bracket/IDNA validation without resolving or fetching the host.
        parsed.hostname.encode("idna") if parsed.hostname else None
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        _fail(f"{label} must be an HTTPS URL without spaces or credentials.")
    return value


def _object(value, required, optional, label):
    if type(value) is not dict or not required <= value.keys() or not value.keys() <= required | optional:
        _fail(f"{label} has missing or unknown fields.")
    return value


def validate_report(payload):
    """Return a detached, validated value, never the caller's mutable object."""
    common = {"kind", "checked_at", "places_checked", "posts_read"}
    data = _object(payload, common, {"question", "connection"}, "Report")
    kind = data["kind"]
    if type(kind) is not str or kind not in {"nothing", "question", "connection"}:
        _fail("Report kind must be nothing, question or connection.")
    expected = common | ({kind} if kind != "nothing" else set())
    if data.keys() != expected:
        _fail("Include question details only for a question, or connection details only for a connection.")
    checked = data["checked_at"]
    if not isinstance(checked, str) or not _TIMESTAMP.fullmatch(checked):
        _fail("Checked at must be an RFC 3339 timestamp with seconds and a timezone.")
    try:
        # Python normalizes offset minutes such as +00:99. The grammar above
        # must bound both components before converting the supplied timestamp.
        instant = datetime.fromisoformat(checked.replace("Z", "+00:00"))
        checked = instant.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        _fail("Checked at must be a valid RFC 3339 timestamp.")
    places = data["places_checked"]
    if type(places) is not list or len(places) > 20:
        _fail("Places checked must be a list of at most 20 places.")
    clean_places = []
    for place in places:
        _object(place, {"name"}, {"url"}, "Place")
        clean = {"name": text_field(place["name"], "Place name", 120)}
        if "url" in place:
            clean["url"] = https_url(place["url"], "Place URL")
        clean_places.append(clean)
    posts = data["posts_read"]
    if type(posts) is not int or not 0 <= posts <= 1_000_000:
        _fail("Posts read must be a whole number from 0 to 1000000.")
    result = {"kind": kind, "checked_at": checked, "places_checked": clean_places, "posts_read": posts}
    if kind == "question":
        question = _object(data[kind], {"text", "why"}, set(), "Question")
        result[kind] = {"text": text_field(question["text"], "Question", 1000), "why": text_field(question["why"], "Why this question", 2000)}
    elif kind == "connection":
        required = {"who", "where", "why_now", "they_bring", "they_could_gain", "i_bring", "i_could_gain", "evidence", "still_unknown", "first_step"}
        connection = _object(data[kind], required, {"board_slug"}, "Connection")
        clean = {"who": text_field(connection["who"], "Connection name", 200), "where": https_url(connection["where"], "Connection URL")}
        for field in ("why_now", "they_bring", "they_could_gain", "i_bring", "i_could_gain", "still_unknown", "first_step"):
            clean[field] = text_field(connection[field], field.replace("_", " ").capitalize(), 2000)
        evidence = connection["evidence"]
        if type(evidence) is not list or not 1 <= len(evidence) <= 20:
            _fail("Connection evidence must contain between 1 and 20 items.")
        clean["evidence"] = []
        for item in evidence:
            _object(item, {"quote_or_link", "matches"}, set(), "Evidence")
            quote = text_field(item["quote_or_link"], "Evidence quote or link", 2000)
            if re.match(r"(?:[a-z][a-z0-9+.-]*://|javascript:|data:|mailto:|file:|//)", quote, re.IGNORECASE):
                quote = https_url(quote, "Evidence link")
            clean["evidence"].append({"quote_or_link": quote, "matches": text_field(item["matches"], "Evidence reason", 1000)})
        if "board_slug" in connection:
            slug = connection["board_slug"]
            if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?", slug):
                _fail("Board slug must contain 1 to 80 lowercase letters, digits or hyphens.")
            clean["board_slug"] = slug
        result[kind] = clean
    if len(canonical_report(result)) > MAX_REPORT_BYTES:
        raise ReportError(413, "Report is too large. The limit is 32 KiB.")
    return result


def canonical_report(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def parse_json_report(raw):
    if not isinstance(raw, bytes):
        _fail("Report body must be UTF-8 JSON.")
    if len(raw) > MAX_REPORT_BYTES:
        raise ReportError(413, "Report is too large. The limit is 32 KiB.")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                _fail("Report JSON contains a duplicate field.")
            result[key] = value
        return result

    def constant(value):
        _fail("Report JSON cannot contain non-finite numbers.")

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, ReportError):
            raise
        _fail("Report body must be valid UTF-8 JSON.")
    return validate_report(payload)


def parse_paste(value):
    """Parse explicit v2 fields; never invent missing check-in metadata."""
    if not isinstance(value, str):
        _fail("Paste a report using the version 2 field labels.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        _fail("The pasted report contains invalid text.")
    if size > MAX_REPORT_BYTES:
        raise ReportError(413, "Report is too large. The limit is 32 KiB.")
    allowed = {"Kind", "Checked at", "Places checked", "Posts read", "Question", "Why", "Connection", "Why now", "They bring", "They could gain", "I bring", "I could gain", "Evidence", "Still unknown", "Small first step", "Board slug"}
    fields = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        label, separator, content = line.partition(":")
        label, content = label.strip(), content.strip()
        if not separator or label not in allowed or not content or (label in fields and label not in {"Places checked", "Evidence"}):
            _fail("Use one named field per line; unknown, empty or repeated fields are not accepted.")
        fields.setdefault(label, []).append(content)
    common = {"Kind", "Checked at", "Places checked", "Posts read"}
    if not common <= fields.keys():
        _fail("Paste needs Kind, Checked at, Places checked and Posts read. Older reports need these details added explicitly.")
    kind = fields["Kind"][0]
    kind_fields = {"nothing": set(), "question": {"Question", "Why"}, "connection": {"Connection", "Why now", "They bring", "They could gain", "I bring", "I could gain", "Evidence", "Still unknown", "Small first step"}}
    if kind not in kind_fields:
        _fail("Report kind must be nothing, question or connection.")
    expected = common | kind_fields[kind]
    if not expected <= fields.keys() or not fields.keys() <= expected | ({"Board slug"} if kind == "connection" else set()):
        _fail("The pasted fields do not match the report kind.")
    count = fields["Posts read"][0]
    if not re.fullmatch(r"[0-9]{1,7}", count):
        _fail("Posts read must be a whole number from 0 to 1000000.")
    places = []
    if fields["Places checked"] != ["none"]:
        for item in fields["Places checked"]:
            if item == "none":
                _fail("Places checked: none cannot be combined with named places.")
            name, separator, url = item.partition(" | ")
            places.append({"name": name, **({"url": url} if separator else {})})
    payload = {"kind": kind, "checked_at": fields["Checked at"][0], "places_checked": places, "posts_read": int(count)}
    if kind == "question":
        payload[kind] = {"text": fields["Question"][0], "why": fields["Why"][0]}
    if kind == "connection":
        who, separator, where = fields["Connection"][0].partition(" | ")
        if not separator:
            _fail("Connection needs a name followed by | and its HTTPS URL.")
        connection = {"who": who, "where": where}
        labels = {"Why now": "why_now", "They bring": "they_bring", "They could gain": "they_could_gain", "I bring": "i_bring", "I could gain": "i_could_gain", "Still unknown": "still_unknown", "Small first step": "first_step", "Board slug": "board_slug"}
        connection.update({name: fields[label][0] for label, name in labels.items() if label in fields})
        connection["evidence"] = []
        for item in fields["Evidence"]:
            quote, separator, matches = item.partition(" | ")
            if not separator:
                _fail("Each Evidence line needs a quote or HTTPS link followed by | and what it matches.")
            connection["evidence"].append({"quote_or_link": quote, "matches": matches})
        payload[kind] = connection
    return validate_report(payload)
