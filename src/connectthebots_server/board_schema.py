"""The complete, explicitly typed public posting. Profile data stays separate."""

from hashlib import sha256
import json
import re
import unicodedata

from .report_schema import ReportError, text_field


class BoardError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _text(value, label, maximum):
    try:
        return text_field(value, label, maximum)
    except ReportError as error:
        raise BoardError(error.status, error.detail.replace("Reports use", "Postings use")) from None


def sentence_count(text):
    """Count punctuation boundaries without treating URLs/decimals as sentences.

    This is a deterministic writing constraint, not semantic analysis. A final
    sentence need not have a full stop. Common title abbreviations are ignored.
    """
    count, start = 0, 0
    for ending in re.finditer(r"[.!?]+[\"'”’)\]]*(?=\s|$)", text):
        segment = text[start:ending.start()]
        word = re.search(r"([A-Za-z.]+)$", segment)
        if ending.group(0) == "." and word and word.group(1).lower() in {"mr", "mrs", "ms", "dr", "prof", "e.g", "i.e"}:
            continue
        if any(char.isalpha() for char in segment):
            count += 1
        start = ending.end()
    if any(char.isalpha() for char in text[start:]):
        count += 1
    return count


def validate_posting(payload):
    if type(payload) is not dict or payload.keys() != {"text", "tags", "looking_for", "offering"}:
        raise BoardError(422, "A posting needs exactly text, tags, looking_for and offering.")
    text = _text(payload["text"], "Posting", 800)
    if not 3 <= sentence_count(text) <= 5:
        raise BoardError(422, "Write the posting as three to five sentences, using full stops, question marks or exclamation marks between them.")
    tags = payload["tags"]
    if type(tags) is not list or len(tags) > 5:
        raise BoardError(422, "Use a list of up to five tags.")
    clean_tags, seen = [], set()
    for tag in tags:
        tag = _text(tag, "Tag", 30)
        folded = unicodedata.normalize("NFKC", tag).casefold()
        if folded in seen:
            raise BoardError(422, "Use each tag only once.")
        seen.add(folded)
        clean_tags.append(tag)
    return {"text": text, "tags": clean_tags, "looking_for": _text(payload["looking_for"], "Looking for", 160), "offering": _text(payload["offering"], "Offering", 160)}


def canonical_payload(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def payload_hash(payload):
    return sha256(canonical_payload(payload)).hexdigest()


def search_text(payload):
    return unicodedata.normalize("NFKC", " ".join([payload["text"], payload["looking_for"], payload["offering"], *payload["tags"]])).casefold()
