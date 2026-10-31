"""Owner numbers: counts only, from the database and the shortened access log."""
from datetime import date, timedelta
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from connectthebots_server import traffic
from connectthebots_server.sessions import storage_time

from test_operator import OperatorFixture

ROOT = Path(__file__).resolve().parents[1]


def log(ip, day, method, path, status):
    return '%s - - [%s:10:00:00 +0000] "%s %s HTTP/1.1" %s 0 "-" "-"\n' % (ip, day, method, path, status)


class TrafficTests(unittest.TestCase):
    def test_counts_visitors_pages_and_film_events_by_day(self):
        lines = [
            log("203.0.113.0", "30/Oct/2026", "GET", "/", "200"),
            log("203.0.113.0", "30/Oct/2026", "GET", "/about", "200"),
            log("198.51.100.0", "30/Oct/2026", "GET", "/", "304"),
            log("198.51.100.0", "30/Oct/2026", "POST", "/video-play", "204"),
            log("198.51.100.0", "30/Oct/2026", "POST", "/video-sound", "204"),
            log("198.51.100.0", "30/Oct/2026", "POST", "/video-done", "204"),
            log("203.0.113.0", "31/Oct/2026", "GET", "/", "200"),
            log("203.0.113.0", "31/Oct/2026", "POST", "/video-play", "204"),
            # Not counted: assets, failures, GET probes of the counter, old days, junk.
            log("192.0.2.0", "31/Oct/2026", "GET", "/assets/", "200"),
            log("192.0.2.0", "31/Oct/2026", "GET", "/other", "404"),
            log("192.0.2.0", "31/Oct/2026", "GET", "/video-play", "204"),
            log("192.0.2.0", "01/Oct/2026", "GET", "/", "200"),
            "not a log line\n",
        ]
        result = traffic.summarize(lines, date(2026, 10, 31), days=2)
        self.assertEqual((result["visitors"], result["page_views"]), (2, 4))
        self.assertEqual(result["pages"]["/"], 3)
        self.assertEqual((result["video_plays"], result["video_sound_on"], result["video_finished"]), (2, 1, 1))
        self.assertEqual([row["day"] for row in result["by_day"]], ["2026-10-30", "2026-10-31"])
        self.assertEqual(result["by_day"][0]["visitors"], 2)
        self.assertEqual(result["by_day"][1], {"day": "2026-10-31", "visitors": 1, "page_views": 1, "video_plays": 1, "video_sound_on": 0, "video_finished": 0})

    def test_cli_reads_rotated_gzip_logs_and_skips_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            today = traffic.datetime.now(traffic.timezone.utc).date()
            stamp = today.strftime("%d/%b/%Y")
            plain = Path(folder) / "access.log"
            plain.write_text(log("203.0.113.0", stamp, "GET", "/", "200"))
            with gzip.open(Path(folder) / "access.log.2.gz", "wt") as handle:
                handle.write(log("198.51.100.0", stamp, "POST", "/video-done", "204"))
            out = io.StringIO()
            with patch("sys.stdout", out):
                code = traffic.main(["--days", "1", str(plain), str(Path(folder) / "access.log.2.gz"), str(Path(folder) / "gone.log")])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertEqual((result["visitors"], result["video_finished"]), (1, 1))
        with patch("sys.stderr", io.StringIO()):
            self.assertEqual(traffic.main(["--days", "31", "x"]), 2)

    def test_nginx_counter_routes_are_logged_by_category_only(self):
        script = (ROOT / "site/deploy/server-setup.sh").read_text()
        for name in ("play", "sound", "done"):
            self.assertIn("location = /v/%s { return 204; }" % name, script)
            self.assertIn("  /v/%s /video-%s;" % (name, name), script)
        counter = (ROOT / "site/assets/watch-count.js").read_text()
        self.assertIn("globalPrivacyControl", counter)
        self.assertNotIn("cookie", counter.split("*/", 1)[1].lower())
        for page in ("index.html", "about.html"):
            self.assertIn('<script src="assets/watch-count.js"></script>', (ROOT / "site" / page).read_text())


class NumbersTests(OperatorFixture):
    def test_numbers_are_counts_without_private_details(self):
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET created_at=? WHERE id='three'", (storage_time(self.now - timedelta(days=3)),))
            connection.execute("INSERT INTO report_keys(id,user_id,key_hash,last_four,name,created_at,last_used_at) VALUES('k1','one','h1','abcd','agent',?,?)", (storage_time(self.now), storage_time(self.now)))
            connection.execute("INSERT INTO report_keys(id,user_id,key_hash,last_four,name,created_at,revoked_at) VALUES('k2','two','h2','abcd','agent',?,?)", (storage_time(self.now), storage_time(self.now)))
            connection.execute("INSERT INTO referrals(id,referrer_id,referred_id,code,entered_via,entered_at,qualified_at) VALUES('r1','one','two','CODE','signup',?,?)", (storage_time(self.now), storage_time(self.now)))
        self.request()
        result = self.operator.numbers(days=7)
        self.assertEqual(result["signups"]["total"], 3)
        self.assertEqual(result["signups"]["last_24_hours"], 2)
        self.assertEqual(result["signups"]["by_day"]["2026-10-30"], 2)
        self.assertEqual(result["signups"]["by_day"]["2026-10-27"], 1)
        self.assertEqual(len(result["signups"]["by_day"]), 7)
        self.assertEqual(result["agents"]["people_with_an_agent_connected"], 1)
        self.assertEqual(result["agents"]["agents_active_last_7_days"], 1)
        self.assertEqual(result["agents"]["connected_by_day"]["2026-10-30"], 2)
        self.assertEqual((result["referrals"]["total"], result["referrals"]["qualified"]), (1, 1))
        self.assertEqual(result["introductions"]["requested"], 1)
        text = json.dumps(result)
        for secret in ("private", "Private", "one", "CODE"):
            self.assertNotIn(secret, text)
        self.error(422, self.operator.numbers, days=0)
        self.error(422, self.operator.numbers, days=91)


if __name__ == "__main__":
    unittest.main()
