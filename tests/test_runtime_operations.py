"""Operational receipt navigation and genuine SQLite contention regressions."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agentmatch_runtime.http_server import AgentApp
from agentmatch_runtime.runtime import AgentRuntime, AgentRuntimeError


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "optional runtime requires cryptography")
class RuntimeOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = AgentRuntime.create(Path(self.temp.name) / "state.db", secrets.token_bytes(32))
        self.credential = self.runtime.issue_credential("marlow", "viewer", ["workspace"], max_uses=200)
        self.app = AgentApp(self.runtime, port=9876)
        self.headers = {"Host": "127.0.0.1:9876", "Authorization": "Bearer " + self.credential["token"]}

    def get(self, path):
        response = self.app.handle("GET", path, self.headers)
        return response.status, json.loads(response.body)

    def test_latest_receipts_include_new_refusal_and_page_back_without_duplicates(self):
        for _ in range(55):
            self.runtime.execute(self.credential["token"], "workspace", {})
        with self.assertRaises(AgentRuntimeError):
            self.runtime.execute(self.credential["token"], "search", {}, "forbidden", 0)
        status, data = self.get("/v1/receipts?order=desc&limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(data["result"]["items"][0]["code"], "SCOPE_REQUIRED")
        sequences = []
        while True:
            page = data["result"]
            sequences.extend(item["sequence"] for item in page["items"])
            if not page["has_more"]:
                self.assertIsNone(page["next_before"])
                break
            status, data = self.get(f'/v1/receipts?order=desc&limit=10&before={page["next_before"]}')
            self.assertEqual(status, 200)
        self.assertGreater(len(sequences), 50)
        self.assertEqual(sequences, sorted(set(sequences), reverse=True))
        asc, cursor = [], 0
        while True:
            page = self.runtime.receipts(self.credential["token"], after=cursor, limit=7)["result"]
            asc.extend(item["sequence"] for item in page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        self.assertEqual(asc, list(reversed(sequences)))

    def test_receipt_pages_remain_owner_scoped(self):
        other = self.runtime.issue_credential("juniper", "viewer", ["workspace"])
        self.runtime.execute(other["token"], "workspace", {})
        for query in ("", "?order=desc"):
            _, data = self.get("/v1/receipts" + query)
            self.assertTrue(data["result"]["items"])
            self.assertTrue(all(item["owner_id"] == "marlow" for item in data["result"]["items"]))

    def test_truncated_receipt_history_is_signalled(self):
        with patch("agentmatch_runtime.runtime.MAX_RECEIPTS", 3):
            for _ in range(5):
                self.runtime.execute(self.credential["token"], "workspace", {})
            _, data = self.get("/v1/receipts?order=desc")
            self.assertEqual(len(data["result"]["items"]), 3)
            self.assertTrue(data["result"]["history_truncated"])
            self.assertFalse(data["result"]["has_more"])

    def test_invalid_receipt_direction_cursors_are_refused(self):
        for query in ("order=down", "order=desc&after=0", "before=1", "order=desc&before=0",
                      "order=desc&before=x", "limit=101", "order=asc&order=desc"):
            self.assertEqual(self.get("/v1/receipts?" + query)[0], 400, query)

    def test_real_sqlite_writer_lock_returns_retryable_error_then_commits_once(self):
        before = self.runtime.capabilities(self.credential["token"])["result"]["uses"]
        connect = self.runtime.vault._connect
        def short_wait_connection():
            connection = connect()
            connection.execute("PRAGMA busy_timeout=20")
            return connection
        headers = {**self.headers, "Content-Type": "application/json"}
        command = json.dumps({"operation": "workspace", "arguments": {}, "idempotency_key": "locked-read"}).encode()
        with closing(sqlite3.connect(self.runtime.vault.path, isolation_level=None)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            with patch.object(self.runtime.vault, "_connect", short_wait_connection):
                response = self.app.handle("POST", "/v1/commands", headers, command)
            error = json.loads(response.body)["error"]
            self.assertEqual(response.status, 503)
            self.assertEqual(error["code"], "STORAGE_UNAVAILABLE")
            self.assertEqual(error["details"], {"retryable": True, "retry_after_seconds": 1})
            lock.execute("ROLLBACK")
        self.assertEqual(self.runtime.capabilities(self.credential["token"])["result"]["uses"], before)
        success = self.app.handle("POST", "/v1/commands", headers, command)
        replay = self.app.handle("POST", "/v1/commands", headers, command)
        self.assertEqual(success.status, 200)
        self.assertEqual(replay.status, 200)
        self.assertEqual(json.loads(success.body)["receipt"], json.loads(replay.body)["receipt"])
        self.assertEqual(self.runtime.capabilities(self.credential["token"])["result"]["uses"], before + 1)

    def test_unrelated_sqlite_error_is_not_advertised_as_retryable(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            try:
                connection.execute("SELECT * FROM missing_table")
            except sqlite3.OperationalError as exc:
                fault = exc
        with patch.object(self.runtime, "capabilities", side_effect=fault):
            with self.assertRaises(sqlite3.OperationalError):
                self.get("/v1/capabilities")
