"""Machine response/action conformance, using the published JSON Schema subset.

The dependency-free validator is deliberately limited to keywords emitted by
this API. It fails on unknown assertion keywords; it is not a general-purpose
JSON Schema implementation or part of the runtime permission boundary.
"""
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import secrets
import tempfile
import unittest

from agentmatch_runtime.catalogue import SCHEMAS, catalogue, openapi, ref, result_name
from agentmatch_runtime.http_server import AgentApp
from agentmatch_runtime.machine_views import project_result
from agentmatch_runtime.runtime import AgentRuntime, EXACT_OPERATIONS, OPERATIONS


def validate_schema(value, schema, document, path="$", depth=0):
    assert depth < 100, "schema recursion limit"
    known = {"$ref", "type", "properties", "required", "additionalProperties", "items", "minItems", "maxItems",
             "uniqueItems", "enum", "const", "minimum", "maximum", "minLength", "maxLength", "pattern",
             "format", "anyOf", "oneOf", "description"}
    assert not set(schema) - known, f"unhandled schema keywords {set(schema) - known}"
    if "$ref" in schema:
        target = schema["$ref"].split("#", 1)[-1]
        resolved = document
        for part in target.lstrip("/").split("/"):
            resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
        validate_schema(value, resolved, document, path, depth + 1)
        return
    for union in ("anyOf", "oneOf"):
        if union in schema:
            matched, failures = 0, []
            for choice in schema[union]:
                try:
                    validate_schema(value, choice, document, path, depth + 1)
                    matched += 1
                except AssertionError as exc:
                    failures.append(str(exc))
            assert matched >= 1 if union == "anyOf" else matched == 1, f"{path} no {union} match: {failures}"
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        assert any(type(value) is types[t] for t in allowed), f"{path} expected {allowed}, got {type(value).__name__}"
    if "const" in schema:
        assert type(value) is type(schema["const"]) and value == schema["const"], f"{path} const"
    if "enum" in schema:
        assert any(type(value) is type(x) and value == x for x in schema["enum"]), f"{path} enum {value!r}"
    if isinstance(value, dict):
        assert not set(schema.get("required", [])) - value.keys(), f"{path} missing {set(schema.get('required', [])) - value.keys()}"
        properties = schema.get("properties", {})
        for key, item in value.items():
            child = properties.get(key, schema.get("additionalProperties", True))
            assert child is not False, f"{path} unexpected {key}"
            if isinstance(child, dict):
                validate_schema(item, child, document, path + "." + key, depth + 1)
    if isinstance(value, list):
        assert len(value) >= schema.get("minItems", 0), path + " minItems"
        assert len(value) <= schema.get("maxItems", len(value)), path + " maxItems"
        if schema.get("uniqueItems"):
            assert len({json.dumps(x, sort_keys=True) for x in value}) == len(value), path + " uniqueItems"
        if "items" in schema:
            for index, item in enumerate(value):
                validate_schema(item, schema["items"], document, f"{path}[{index}]", depth + 1)
    if isinstance(value, str):
        assert len(value) >= schema.get("minLength", 0), path + " minLength"
        assert len(value) <= schema.get("maxLength", len(value)), path + " maxLength"
        if "pattern" in schema:
            assert re.search(schema["pattern"], value), path + " pattern"
        if schema.get("format") == "date-time":
            assert datetime.fromisoformat(value).tzinfo is not None, path + " timezone"
    if type(value) is int:
        assert value >= schema.get("minimum", value), path + " minimum"
        assert value <= schema.get("maximum", value), path + " maximum"


def validate_response(value, name, document=None):
    document = document or openapi()
    validate_schema(value, ref(name), document)


class MachineContractSchemaTests(unittest.TestCase):
    def test_every_endpoint_and_operation_has_resolvable_output_and_error_schema(self):
        document = openapi()
        for path in document["paths"].values():
            for endpoint in path.values():
                for status in ("200", "default"):
                    schema = endpoint["responses"][status]["content"]["application/json"]["schema"]
                    self.assertIn(schema["$ref"].rsplit("/", 1)[-1], document["components"]["schemas"])
        validate_response({"version": 0, "result": catalogue()}, "CatalogueEnvelope", document)
        for operation in catalogue()["operations"]:
            self.assertIn(operation["output_schema"]["$ref"].rsplit("/", 1)[-1], document["components"]["schemas"])

    def test_schema_rejects_missing_resources_wrong_scalars_and_unlisted_private_fields(self):
        invalid = [{"version": "0", "result": {}}, {"version": 0, "result": {}, "private": "canary"}]
        for value in invalid:
            with self.assertRaises(AssertionError):
                validate_response(value, "CapabilitiesEnvelope")

    def test_projection_preserves_manual_labels_and_filters_role_and_scope(self):
        manual = {"introductions": [{"intro_id": "i-1", "actions": ["close", "block"], "context_version": 2}],
                  "information_requests": []}
        before = deepcopy(manual)
        projected = project_result("workspace", manual, {"role": "agent", "scopes": ["close_introduction"]})
        self.assertEqual(manual, before)
        self.assertEqual([a["operation"] for a in projected["introductions"][0]["actions"]], ["close_introduction"])
        self.assertEqual(projected["introductions"][0]["actions"][0]["arguments"], {"intro_id": "i-1", "data": {"context_version": 2}})
        viewer = project_result("workspace", manual, {"role": "viewer", "scopes": ["workspace"]})
        self.assertEqual(viewer["introductions"][0]["actions"], [])


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "optional runtime requires cryptography")
class RuntimeResponseContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime-contract-")
        self.addCleanup(self.temp.cleanup)
        self.runtime = AgentRuntime.create(Path(self.temp.name) / "state.db", secrets.token_bytes(32))
        self.credentials = {owner: self.runtime.issue_credential(owner, "agent", sorted(OPERATIONS))
                            for owner in ("marlow", "juniper", "sable")}
        self.app = AgentApp(self.runtime, port=9876)
        self.document = openapi()
        self.counter = 0

    def request(self, owner, method, path, data=None):
        headers = {"Host": "127.0.0.1:9876", "Authorization": "Bearer " + self.credentials[owner]["token"],
                   "Content-Type": "application/json"}
        response = self.app.handle(method, path, headers, b"" if data is None else json.dumps(data).encode())
        parsed = json.loads(response.body)
        endpoint = self.document["paths"][path.split("?")[0]][method.lower()]
        status = "200" if response.status == 200 else "default"
        validate_schema(parsed, endpoint["responses"][status]["content"]["application/json"]["schema"], self.document)
        return response.status, parsed

    def command(self, owner, operation, args, *, authorize=True):
        credential = self.credentials[owner]
        if authorize and operation in EXACT_OPERATIONS:
            self.runtime.authorize_command(credential["id"], operation, args)
        self.counter += 1
        version = self.runtime.capabilities(credential["token"])["version"]
        status, response = self.request(owner, "POST", "/v1/commands", {
            "operation": operation, "arguments": args, "expected_version": version, "idempotency_key": str(self.counter)})
        self.assertEqual(status, 200, response)
        validate_response(response["result"], result_name(operation), self.document)
        return response["result"]

    def workspace(self, owner):
        return self.request(owner, "GET", "/v1/workspace")[1]["result"]

    def publish(self, owner, include=None):
        preview = self.command(owner, "preview_publication", {"board_id": "community-tools", "expires_in_days": 14,
                               "include": include if include is not None else ["working_arrangement", "feedback", "schedule", "commercial_note"]})
        return self.command(owner, "approve_publication", {"board_id": "community-tools", **{k: preview[k] for k in ("expires_at", "include", "approval_digest")}})

    def follow(self, owner, action):
        self.assertEqual(action["required_arguments"], [])
        validate_schema(action["arguments"], SCHEMAS[action["operation"]], self.document)
        return self.command(owner, action["operation"], action["arguments"])

    def test_api_shapes_and_returned_introduction_actions_execute(self):
        for path in ("/v1/capabilities", "/v1/operations", "/v1/receipts", "/v1/receipts?order=desc&limit=1"):
            self.assertEqual(self.request("marlow", "GET", path)[0], 200)
        self.publish("marlow"); self.publish("juniper")
        search = self.command("marlow", "search", {"text": "", "limit": 20})
        card = next(item["card"] for item in search["results"] if item["card"]["publisher_alias"] == "Juniper")
        self.command("marlow", "explain", {k: card[k] for k in ("notice_id", "revision")})
        self.command("marlow", "propose_introduction", {k: card[k] for k in ("notice_id", "revision")} | {"first_step": "async-review", "note": ""})
        for owner in ("marlow", "juniper"):
            intro = self.workspace(owner)["introductions"][-1]
            self.assertIn("block_introduction", [a["operation"] for a in intro["actions"]])
            self.follow(owner, next(a for a in intro["actions"] if a["operation"] == "accept"))
        intro = self.workspace("marlow")["introductions"][-1]
        proposal = next(a for a in intro["actions"] if a["operation"] == "propose_step")
        self.assertEqual(proposal["required_arguments"], ["/data/action", "/data/detail"])
        proposal["arguments"]["data"] = {"action": "async-review", "detail": ""}
        self.command("marlow", proposal["operation"], proposal["arguments"])
        intro = self.workspace("juniper")["introductions"][-1]
        self.follow("juniper", next(a for a in intro["actions"] if a["operation"] == "agree_step"))
        intro = self.workspace("marlow")["introductions"][-1]
        closed = self.follow("marlow", next(a for a in intro["actions"] if a["operation"] == "close_introduction"))
        self.assertEqual(closed["introductions"][-1]["status"], "closed")

    def test_incoming_clarification_review_decline_and_answer_actions_execute(self):
        self.publish("marlow")
        search = self.command("marlow", "search", {"text": "", "limit": 20})
        card = next(item["card"] for item in search["results"] if item["card"]["publisher_alias"] == "Sable")
        info = self.command("marlow", "explain", {k: card[k] for k in ("notice_id", "revision")})["information_request"]
        args = {k: card[k] for k in ("notice_id", "revision")} | {"field": "feedback_style", "requester_notice_id": info["requester_notice_id"], "requester_revision": info["requester_revision"]}
        self.command("marlow", "request_information", args)
        incoming = self.workspace("sable")["information_requests"][-1]
        self.assertEqual([a["operation"] for a in incoming["actions"]], ["workspace", "decline_information"])
        self.follow("sable", incoming["actions"][0])
        self.follow("sable", incoming["actions"][1])
        self.assertEqual(self.workspace("marlow")["information_requests"][-1]["status"], "declined")
        # A new target revision permits a distinct request; only publication answers it.
        self.publish("sable", include=[])
        card = self.workspace("sable")["publications"][-1]["card"]
        args.update({k: card[k] for k in ("notice_id", "revision")})
        self.command("marlow", "request_information", args)
        brief = dict(self.workspace("sable")["brief"])
        version = brief.pop("version")
        for key in ("brief_id", "updated_at"):
            brief.pop(key)
        brief.update(feedback_style="direct", accepted_feedback=["direct"])
        self.command("sable", "save_brief", {"expected_version": version, "brief": brief})
        self.publish("sable")
        answered = self.workspace("marlow")["information_requests"][-1]
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["actions"][0]["operation"], "explain")
        self.follow("marlow", answered["actions"][0])

    def test_error_shapes_and_narrow_mutation_response(self):
        status, _ = self.request("marlow", "POST", "/v1/commands", {"operation": "block", "arguments": {}})
        self.assertEqual(status, 404)
        status, _ = self.request("marlow", "POST", "/v1/commands", {"operation": "search", "arguments": {}, "idempotency_key": "not-approved", "expected_version": 0})
        self.assertEqual(status, 403)
        brief = dict(self.workspace("marlow")["brief"])
        version = brief.pop("version")
        for key in ("brief_id", "updated_at"):
            brief.pop(key)
        self.credentials["marlow"] = self.runtime.issue_credential("marlow", "agent", ["save_brief"])
        result = self.command("marlow", "save_brief", {"expected_version": version, "brief": brief})
        self.assertEqual(result, {"completed": True})

    def test_recovered_search_and_recovery_receipt_match_published_schemas(self):
        credential = self.credentials["marlow"]
        args = {"text": "", "limit": 20}
        self.runtime.authorize_command(credential["id"], "search", args)
        command = {"operation": "search", "arguments": args, "expected_version": 0, "idempotency_key": "lost-search"}
        self.assertEqual(self.request("marlow", "POST", "/v1/commands", command)[0], 200)
        status, recovered = self.request("marlow", "POST", "/v1/commands", command)
        self.assertEqual(status, 200)
        self.assertTrue(recovered["replayed"])
        self.assertTrue(recovered["result"]["coverage"]["recovered_original_candidates"])
        schema = next(o["output_schema"] for o in catalogue()["operations"] if o["name"] == "search")
        validate_schema(recovered, schema, self.document)
        status, receipts = self.request("marlow", "GET", "/v1/receipts?order=desc")
        self.assertEqual(status, 200)
        self.assertIn("original_receipt_sequence", receipts["result"]["items"][0])


if __name__ == "__main__":
    unittest.main()
