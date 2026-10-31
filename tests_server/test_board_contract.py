"""Public schema rejects accidental profile disclosure and malformed records."""

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from tests_server.board_contract import board_validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None


@unittest.skipUnless(HAS_JSONSCHEMA, "Install development-only jsonschema==4.26.0 to validate the public board schema")
class BoardContractTests(unittest.TestCase):
    def setUp(self):
        self.validator = board_validator()
        self.posting = {
            "slug": "0123456789abcdef0123456789abcdef",
            "text": "I build fictional sensors. I offer field measurements. I seek firmware help.",
            "tags": ["sensors", "firmware"], "looking_for": "Firmware help", "offering": "Fictional field measurements",
            "published_at": "2026-10-30T08:00:00.000001Z", "updated_at": "2026-10-30T08:00:00.000001Z",
        }
        self.feed = {"schema_version": 1, "updated_at": "2026-10-30T08:00:00.000001Z", "page": 1,
                     "page_size": 20, "total": 1, "has_next": False, "items": [self.posting]}

    def test_schema_accepts_public_item_and_feed(self):
        self.validator.validate(self.posting)
        self.validator.validate(self.feed)
        self.validator.validate({**self.feed, "items": [], "total": 0})

    def test_real_service_empty_published_and_withdrawn_feeds_validate(self):
        from connectthebots_server.board import BoardService
        from connectthebots_server.db import Database

        clock = lambda: datetime(2026, 10, 30, 8, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "board.db", clock)
            database.migrate()
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    ("fictional-owner", "fixture-owner", "fictional-owner@example.invalid", "Fictional Owner", clock().isoformat(), clock().isoformat()),
                )
            service = BoardService(database, clock)
            self.validator.validate(service.public_list())
            draft = service.save_draft("fictional-owner", {key: self.posting[key] for key in ("text", "tags", "looking_for", "offering")})
            public = service.publish("fictional-owner", draft["revision"], draft["payload_hash"])
            self.validator.validate(service.public_list())
            self.validator.validate(service.get_public(public["slug"]))
            service.unpublish("fictional-owner")
            self.validator.validate(service.public_list())

    def test_account_and_approval_metadata_are_rejected_at_every_public_level(self):
        for field in ("email", "display_name", "google_sub", "user_id", "profile", "revision", "payload_hash", "grant_id", "draft"):
            for target in ("item", "nested", "feed"):
                with self.subTest(field=field, target=target):
                    record = deepcopy(self.posting if target == "item" else self.feed)
                    (record["items"][0] if target == "nested" else record)[field] = "fictional-private-value"
                    self.assertFalse(self.validator.is_valid(record))

    def test_timestamp_format_is_actually_enforced(self):
        for timestamp in ("2026-02-30T08:00:00.000001Z", "2026-10-30T25:00:00.000001Z", "2026-10-30T08:00:00Z",
                          "2026-10-30T08:00:00.000001+00:00", "not-a-time"):
            with self.subTest(timestamp=timestamp):
                self.assertFalse(self.validator.is_valid({**self.posting, "published_at": timestamp}))
                self.assertFalse(self.validator.is_valid({**self.feed, "updated_at": timestamp}))

    def test_bounds_and_types_reject_malformed_public_data(self):
        for changes in ({"slug": "owner-email@example.invalid"}, {"slug": self.posting["slug"] + "\n"}, {"text": "x" * 801}, {"tags": ["x"] * 6},
                        {"tags": [""]}, {"looking_for": "x" * 161}, {"offering": None}):
            with self.subTest(changes=changes):
                self.assertFalse(self.validator.is_valid({**self.posting, **changes}))
        for changes in ({"page": 0}, {"page": True}, {"page_size": 51}, {"total": -1}, {"has_next": 1},
                        {"schema_version": 2}, {"items": [self.posting] * 51}):
            with self.subTest(changes=changes):
                self.assertFalse(self.validator.is_valid({**self.feed, **changes}))


if __name__ == "__main__":
    unittest.main()
