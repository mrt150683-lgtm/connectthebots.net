"""Hostile client and viewer boundaries, without opening sockets in R0 checks."""
import importlib.util
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import secrets
import tempfile
import unittest

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(HAS_CRYPTO, "Optional runtime extra is not installed")
class AgentTransportTests(unittest.TestCase):
    def setUp(self):
        from agentmatch_runtime.runtime import AgentRuntime
        from agentmatch_runtime.http_server import AgentApp
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = AgentRuntime.create(Path(self.tmp.name) / "test.sqlite", secrets.token_bytes(32))
        self.agent = self.runtime.issue_credential("marlow", "agent", ["workspace", "save_brief", "search"])
        self.viewer = self.runtime.issue_credential("marlow", "viewer", ["workspace"])
        self.app = AgentApp(self.runtime, port=8766)

    def request(self, method="GET", path="/v1/workspace", *, token=None, headers=None, body=b""):
        h = {"Host": "127.0.0.1:8766", "Authorization": "Bearer " + (token or self.agent["token"])}
        h.update(headers or {})
        reply = self.app.handle(method, path, h, body)
        return reply.status, json.loads(reply.body)

    def test_owner_is_bound_and_viewer_cannot_execute_mutations(self):
        code, ws = self.request(token=self.viewer["token"])
        self.assertEqual(code, 200)
        self.assertEqual(ws["result"]["owner"]["owner_id"], "marlow")
        command = {"operation": "save_brief", "arguments": {}, "idempotency_key": "test", "expected_version": ws["version"]}
        code, error = self.request("POST", "/v1/commands", token=self.viewer["token"],
                                   headers={"Content-Type": "application/json"}, body=json.dumps(command).encode())
        self.assertEqual(code, 403)
        self.assertEqual(error["error"]["code"], "SCOPE_REQUIRED")
        code, _ = self.request("GET", "/v1/owners/juniper/workspace")
        self.assertEqual(code, 404)
        self.assertEqual(self.request(path="/v1/workspace?owner_id=juniper")[0], 400)

    def test_native_agent_access_and_host_origin_auth_fail_closed(self):
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request(headers={"Host": "attacker.example:8766"})[0], 421)
        self.assertEqual(self.request(headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request(headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request(headers={"Authorization": ""})[0], 401)
        self.assertEqual(self.request(headers={"Authorization": "Bearer guessed"})[0], 401)
        self.assertEqual(self.request("OPTIONS", "/v1/commands")[0], 405)

    def test_strict_json_no_duplicate_keys_and_no_unknown_command_fields(self):
        for body in (b'{"operation":"workspace","operation":"search","arguments":{}}',
                     b'{"operation":"workspace","arguments":{},"owner":"juniper"}',
                     b'{"operation":"workspace","arguments":NaN}'):
            code, _ = self.request("POST", "/v1/commands", headers={"Content-Type": "application/json"}, body=body)
            self.assertEqual(code, 400)
        self.assertEqual(self.request("POST", "/v1/commands", body=b"{}")[0], 415)
        self.assertEqual(self.request("POST", "/v1/commands", headers={"Content-Type": "application/json"}, body=b"x" * 65537)[0], 413)

    def test_admin_routes_and_files_are_not_exposed(self):
        for path in ("/v1/issue", "/v1/authorize", "/v1/revoke", "/../test.sqlite", "/.env", "/api/session"):
            self.assertEqual(self.request(path=path)[0], 404)

    def test_catalogue_lists_only_supported_commands_and_no_seed_private_data(self):
        from agentmatch_runtime.runtime import OPERATIONS
        from agentmatch_runtime.catalogue import SCHEMAS, BRIEF
        from agentmatch_workbench.domain.briefs import BRIEF_FIELDS
        self.assertEqual(set(SCHEMAS), set(OPERATIONS))
        self.assertEqual(set(BRIEF["properties"]), BRIEF_FIELDS)
        status, catalogue = self.request(path="/v1/operations")
        self.assertEqual(status, 200)
        self.assertNotIn(self.agent["token"], json.dumps(catalogue))
        status, spec = self.request(path="/openapi.json", headers={"Authorization": ""})
        self.assertEqual(status, 200)
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertNotIn("/v1/authorize", spec["paths"])

    def test_receipt_cursor_validation_and_revoked_viewer(self):
        self.assertEqual(self.request(path="/v1/receipts?after=0&limit=1")[0], 200)
        for query in ("after=-1", "limit=101", "after=x", "limit=1&limit=2", "token=secret"):
            self.assertEqual(self.request(path="/v1/receipts?" + query)[0], 400)
        self.runtime.revoke_credential(self.viewer["id"])
        self.assertEqual(self.request(token=self.viewer["token"])[0], 401)

    def test_client_refuses_remote_or_credential_urls(self):
        from agentmatch_runtime.client import AgentClient
        for url in ("https://example.com", "http://127.0.0.1:8766/path", "http://user:secret@localhost:8766", "http://localhost:8766/?token=secret"):
            with self.assertRaises(ValueError):
                AgentClient(url, "synthetic")

    def test_cli_provisions_and_revokes_without_exposing_key(self):
        from agentmatch_runtime.cli import main
        keyfile = Path(self.tmp.name) / "admin.key"
        keyfile.write_bytes(secrets.token_bytes(32))
        database = Path(self.tmp.name) / "cli.sqlite"
        flags = ["--db", str(database), "--key-file", str(keyfile)]
        def call(arguments):
            stream = StringIO()
            with redirect_stdout(stream):
                status = main(arguments)
            self.assertEqual(status, 0)
            self.assertNotIn(keyfile.read_bytes().hex(), stream.getvalue())
            return json.loads(stream.getvalue())
        self.assertTrue(call(["init", *flags, "--fictional-demo"])["created"])
        credential = call(["issue", *flags, "--owner", "marlow", "--role", "viewer"])
        self.assertEqual(credential["role"], "viewer")
        self.assertTrue(call(["revoke", *flags, "--credential", credential["id"]])["revoked"])
