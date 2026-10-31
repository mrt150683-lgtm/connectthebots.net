"""Count visits and film plays from the shortened nginx access log.

The log never holds full addresses, queries, referrers or browser strings (see
server-setup.sh), so these are approximate counts: one shortened address can be
several people, and bots are included. Run as root, read-only:

    /opt/connectthebots/venv/bin/python -m connectthebots_server.traffic --days 14 /var/log/nginx/connectthebots.access.log*
"""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import json
import re
import sys

PAGES = ("/", "/about", "/signin", "/example", "/film")
VIDEO = {"/video-play": "plays", "/video-sound": "sound_on", "/video-done": "finished"}
_LINE = re.compile(r'^(?P<ip>\S+) - - \[(?P<day>\d{2}/\w{3}/\d{4}):[^\]]*\] "(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" (?P<status>\d{3}) ')


def summarize(lines, today, days=14):
    first = today - timedelta(days=days - 1)
    per_day = {}
    totals = {"visitors": set(), "page_views": 0, "plays": 0, "sound_on": 0, "finished": 0, "pages": {page: 0 for page in PAGES}}
    for line in lines:
        match = _LINE.match(line)
        if not match:
            continue
        try:
            day = datetime.strptime(match["day"], "%d/%b/%Y").date()
        except ValueError:
            continue
        if not first <= day <= today:
            continue
        entry = per_day.setdefault(day, {"visitors": set(), "page_views": 0, "plays": 0, "sound_on": 0, "finished": 0})
        path, method, status = match["path"], match["method"], match["status"]
        if path in PAGES and method == "GET" and status in ("200", "304"):
            entry["visitors"].add(match["ip"])
            totals["visitors"].add(match["ip"])
            entry["page_views"] += 1
            totals["page_views"] += 1
            totals["pages"][path] += 1
        elif path in VIDEO and method == "POST" and status == "204":
            entry[VIDEO[path]] += 1
            totals[VIDEO[path]] += 1
    rows = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        entry = per_day.get(day, {"visitors": (), "page_views": 0, "plays": 0, "sound_on": 0, "finished": 0})
        rows.append({"day": day.isoformat(), "visitors": len(entry["visitors"]), "page_views": entry["page_views"],
                     "video_plays": entry["plays"], "video_sound_on": entry["sound_on"], "video_finished": entry["finished"]})
    return {"days": days, "from": first.isoformat(), "to": today.isoformat(),
            "visitors": len(totals["visitors"]), "page_views": totals["page_views"], "pages": totals["pages"],
            "video_plays": totals["plays"], "video_sound_on": totals["sound_on"], "video_finished": totals["finished"],
            "by_day": rows}


def _read(paths):
    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                yield from handle
        except FileNotFoundError:
            continue


def main(argv=None):
    parser = argparse.ArgumentParser(prog="connectthebots_server.traffic")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("logs", nargs="+")
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 30:
        print("Logs are kept for 30 days; choose from 1 to 30.", file=sys.stderr)
        return 2
    today = datetime.now(timezone.utc).date()
    print(json.dumps(summarize(_read(args.logs), today, args.days), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
