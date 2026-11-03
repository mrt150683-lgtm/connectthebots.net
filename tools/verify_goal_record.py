"""Check a Common goals JSONL record from a file or paginated HTTPS URL.

Uses Python's standard library only. Run this tool yourself; the service never
fetches a submitted URL. A valid chain authenticates the available record, not
its scientific claims, missing cleartext, or completeness against a saved head.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


# Keep this file self-contained: the public download must run with python -S,
# without an installed package, a repository checkout or third-party modules.
HASH_FIELDS = ("goal", "seq", "kind", "author", "created_at", "refs", "content_sha256", "previous")


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return sha256(encoded.encode("ascii")).hexdigest()


def verify_record(entries, expected_goal=None):
    """Check the documented envelope format independently of server imports."""
    previous, checked, goal = "0" * 64, 0, expected_goal
    for number, entry in enumerate(entries, 1):
        try:
            if type(entry["seq"]) is not int or entry["seq"] != number:
                raise ValueError("sequence is not consecutive")
            if goal is None:
                goal = entry["goal"]
            if entry["goal"] != goal:
                raise ValueError("goal identifier changed")
            if entry["previous"] != previous:
                raise ValueError("previous hash does not match")
            current = _digest({name: entry[name] for name in HASH_FIELDS})
            if entry["entry_sha256"] != current:
                raise ValueError("entry hash does not match")
            if "id" in entry and entry["id"] != "e_" + current:
                raise ValueError("entry id does not match")
            if entry.get("content") is not None and _digest(entry["content"]) != entry["content_sha256"]:
                raise ValueError("content hash does not match")
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
            return {"valid": False, "entries": checked, "first_broken": number, "detail": str(error)}
        previous, checked = current, checked + 1
    return {"valid": True, "entries": checked, "first_broken": None, "detail": "Record links and available content match."}


MAX_PAGES = 1000
MAX_ENTRIES = 100000
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
TIMEOUT = 15


class RecordInputError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecordInputError("JSON contains a repeated object field.")
        result[key] = value
    return result


def _not_finite(value):
    raise RecordInputError("JSON contains a nonfinite number.")


def decode_page(data):
    try:
        lines = data.decode("utf-8").splitlines()
        entries = [json.loads(line, object_pairs_hook=_unique_object, parse_constant=_not_finite)
                   for line in lines if line.strip()]
    except (UnicodeError, ValueError, RecursionError) as error:
        raise RecordInputError("The record must contain one valid UTF-8 JSON object per line.") from error
    if any(not isinstance(entry, dict) for entry in entries):
        raise RecordInputError("Each record line must be a JSON object.")
    if len(entries) > MAX_ENTRIES:
        raise RecordInputError("The record exceeds the entry limit.")
    return entries


def _url(value, *, initial=False):
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None or
                parts.password is not None or parts.fragment or not parts.path or
                any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ValueError
        # Accessing port validates its syntax/range.
        parts.port
        fields = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        if len({key for key, _ in fields}) != len(fields) or any(key not in ("after", "limit") for key, _ in fields):
            raise ValueError
        params = dict(fields)
        for key, number in params.items():
            if not number.isascii() or not number.isdigit() or len(number) > 19:
                raise ValueError
            if not (0 <= int(number) <= 2**63 - 1 if key == "after" else 1 <= int(number) <= 100):
                raise ValueError
        if initial and int(params.get("after", "0")) != 0:
            raise ValueError
    except ValueError as error:
        raise RecordInputError("Use an HTTPS record URL without credentials/fragments, starting at after=0; only after and limit are accepted.") from error
    return parts, params


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        raise RecordInputError("Record downloads do not follow redirects. Use the final HTTPS record URL.")


def read_url(value, *, opener=None):
    parts, params = _url(value, initial=True)
    limit = int(params.get("limit", "100"))
    base = parts._replace(query="", fragment="")
    opener = opener or build_opener(_NoRedirect())
    entries, after, total = [], 0, 0
    for _ in range(MAX_PAGES):
        url = urlunsplit(base._replace(query=urlencode({"after": after, "limit": limit})))
        request = Request(url, headers={"Accept": "application/x-ndjson", "User-Agent": "CommonGoalsRecordVerifier/1"})
        with opener.open(request, timeout=TIMEOUT) as response:
            if response.geturl() != url:
                raise RecordInputError("The record response changed URL.")
            data = response.read(MAX_PAGE_BYTES + 1)
            if len(data) > MAX_PAGE_BYTES:
                raise RecordInputError("A record page exceeds the byte limit.")
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise RecordInputError("The record exceeds the total byte limit.")
            page = decode_page(data)
            entries.extend(page)
            if len(page) > limit or len(entries) > MAX_ENTRIES:
                raise RecordInputError("The record exceeds the page or total entry limit.")
            # Check before calculating another cursor: gaps, duplicate pages and
            # tampering name the first broken entry rather than looping.
            report = verify_record(entries)
            if not report["valid"]:
                return entries
            next_after = page[-1]["seq"] if page else after
            advertised = response.headers.get("X-Record-Next-After")
            if advertised is not None and advertised != str(next_after):
                raise RecordInputError("The record pagination cursor does not match its entries.")
            link = response.headers.get("Link")
            if link:
                match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="next"\s*', link)
                if match is None or not page:
                    raise RecordInputError("The record next-page link is invalid.")
                linked, linked_params = _url(match[1])
                if (linked.scheme, linked.netloc, linked.path) != (base.scheme, base.netloc, base.path) or \
                        int(linked_params.get("after", "-1")) != next_after or int(linked_params.get("limit", str(limit))) != limit:
                    raise RecordInputError("The record next-page link changes its target or cursor.")
            if not page or not link and len(page) < limit:
                return entries
            if next_after <= after:
                raise RecordInputError("The record pagination did not advance.")
            after = next_after
    raise RecordInputError("The record exceeds the page limit.")


def read_record(source):
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", source):
        return read_url(source)
    with Path(source).open("rb") as stream:
        data = stream.read(MAX_TOTAL_BYTES + 1)
    if len(data) > MAX_TOTAL_BYTES:
        raise RecordInputError("The record exceeds the byte limit.")
    return decode_page(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", metavar="URL_OR_FILE")
    args = parser.parse_args(argv)
    try:
        entries = read_record(args.source)
        report = verify_record(entries)
    except (RecordInputError, OSError, HTTPError, URLError) as error:
        print("Unable to check record: " + str(error), file=sys.stderr)
        return 2
    if not report["valid"]:
        print(f"BROKEN at entry {report['first_broken']}: {report['detail']}")
        return 1
    print(f"Verified {report['entries']} entries: all links and available content hashes match.")
    if entries:
        last = entries[-1]
        print(f"Head: goal={last['goal']} seq={last['seq']} entry_sha256={last['entry_sha256']}")
    print("Missing content was not rechecked; compare the last hash with a saved head to check completeness.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
