"""Actual two-agent HTTP journey, restart/revocation and optional browser proof.

All administrative authorizations in this script are synthetic test decisions.
It is not an agent escalation helper. Never give this admin harness real data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agentmatch_runtime.client import AgentClient, AgentAPIError
from agentmatch_runtime.runtime import AgentRuntime, OPERATIONS, EXACT_OPERATIONS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "agent-runtime")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    server = None
    with tempfile.TemporaryDirectory(prefix="journey-", dir=args.out) as folder:
        folder = Path(folder)
        key = secrets.token_bytes(32)
        keyfile, database = folder / "external.key", folder / "runtime.sqlite"
        keyfile.write_bytes(key)
        admin = AgentRuntime.create(database, key)
        actors = {name: admin.issue_credential(name, "agent", sorted(OPERATIONS), max_uses=300)
                  for name in ("marlow", "juniper")}
        viewer = admin.issue_credential("marlow", "viewer", ["workspace"], max_uses=300)

        def start():
            process = subprocess.Popen([sys.executable, "-u", str(ROOT / "run_agent_runtime.py"), "serve", "--db", str(database),
                                        "--key-file", str(keyfile), "--port", "0"], cwd=ROOT,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            line = process.stdout.readline()
            try:
                url = json.loads(line)["url"]
            except Exception:
                process.terminate()
                process.wait(timeout=10)
                raise RuntimeError("Isolated runtime failed to start; inspect implementation") from None
            return process, url

        try:
            server, url = start()
            clients = {name: AgentClient(url, credential["token"]) for name, credential in actors.items()}
            watch = AgentClient(url, viewer["token"])

            standing = frozenset({"search", "propose_introduction", "accept", "propose_step", "agree_step"})
            missions_ready = False

            def command(owner, op, arguments):
                if op in EXACT_OPERATIONS and not (missions_ready and op in standing):
                    admin.authorize_command(actors[owner]["id"], op, arguments)
                version = clients[owner].capabilities()["version"]
                return clients[owner].execute(op, arguments, expected_version=version, idempotency_key=secrets.token_hex(12))

            # Private revision must survive encryption/restart, but not publication.
            initial = clients["marlow"].workspace()
            brief = dict(initial["result"]["brief"])
            brief_version = brief.pop("version")
            for field in ("brief_id", "updated_at"):
                brief.pop(field)
            canary = "FICTIONAL_PRIVATE_CANARY_" + secrets.token_hex(8)
            brief["private_notes"] = canary
            save_args = {"expected_version": brief_version, "brief": brief}
            saved = command("marlow", "save_brief", save_args)
            preview = clients["marlow"].execute("preview_publication", {"board_id": "community-tools", "expires_in_days": 14,
                                                "include": ["working_arrangement", "feedback", "schedule", "commercial_note"]})["result"]
            publication = {"board_id": "community-tools", "expires_at": preview["expires_at"],
                           "include": preview["include"], "approval_digest": preview["approval_digest"]}
            # Valid preview alone is not publishing authority.
            try:
                clients["marlow"].execute("approve_publication", publication, expected_version=saved["version"], idempotency_key="unapproved")
                raise AssertionError("Unapproved publication succeeded")
            except AgentAPIError as exc:
                assert exc.payload["error"]["code"] == "APPROVAL_REQUIRED"
            command("marlow", "approve_publication", publication)
            other_preview = clients["juniper"].execute("preview_publication", {
                "board_id": "community-tools", "expires_in_days": 14,
                "include": ["working_arrangement", "feedback", "schedule", "commercial_note"]})["result"]
            command("juniper", "approve_publication", {"board_id": "community-tools",
                "expires_at": other_preview["expires_at"], "include": other_preview["include"],
                "approval_digest": other_preview["approval_digest"]})
            # Once each owner delegates the constrained mission, operating agents
            # complete discovery/consent/agreement without the administrator.
            workspaces = {owner: client.workspace()["result"] for owner, client in clients.items()}
            notice_refs = {owner: {k: state["publications"][0][k] for k in ("notice_id", "revision")}
                           for owner, state in workspaces.items()}
            for owner, other in (("marlow", "juniper"), ("juniper", "marlow")):
                admin.authorize_mission(actors[owner]["id"], {
                    "own_notice": notice_refs[owner], "candidate_notices": [notice_refs[other]],
                    "board_ids": [board["board_id"] for board in workspaces[owner]["boards"]],
                    "operations": sorted(standing), "search_queries": [{"text": "", "limit": 20}],
                    "first_steps": ["async-review"], "max_uses": 20, "ttl_seconds": 3600})
            missions_ready = True
            results = command("marlow", "search", {"text": "", "limit": 20})["result"]
            card = next(item["card"] for item in results["results"] if item["card"]["publisher_alias"] == "Juniper")
            assert canary not in json.dumps(card)
            proposal = command("marlow", "propose_introduction", {"notice_id": card["notice_id"], "revision": card["revision"],
                                                                   "first_step": "async-review", "note": ""})
            intro_id = proposal["result"]["introductions"][-1]["intro_id"]
            for owner in ("marlow", "juniper"):
                intro = next(x for x in clients[owner].workspace()["result"]["introductions"] if x["intro_id"] == intro_id)
                command(owner, "accept", {"intro_id": intro_id, "data": {"proposal_digest": intro["proposal_digest"]}})
            command("marlow", "propose_step", {"intro_id": intro_id, "data": {"action": "async-review", "detail": ""}})
            for owner in ("marlow", "juniper"):
                command(owner, "agree_step", {"intro_id": intro_id, "data": {"version": 1}})
            for owner in ("marlow", "juniper"):
                command(owner, "report_step", {"intro_id": intro_id, "data": {"version": 1, "outcome": "happened"}})
            intro = next(x for x in watch.workspace()["result"]["introductions"] if x["intro_id"] == intro_id)
            closed = command("marlow", "close_introduction", {"intro_id": intro_id, "data": {"context_version": intro["context_version"]}})
            assert closed["result"]["capacity"]["in_use"] == 0

            # An interrupted client can retry without stale peer content or new effect.
            retry_version = clients["marlow"].capabilities()["version"]
            retry_args = {"notice_id": closed["result"]["publications"][0]["notice_id"]}
            first = clients["marlow"].execute("withdraw_notice", retry_args, expected_version=retry_version, idempotency_key="lost-response")
            server.terminate(); server.wait(timeout=10)
            server, url = start()
            clients = {name: AgentClient(url, credential["token"]) for name, credential in actors.items()}
            watch = AgentClient(url, viewer["token"])
            replay = clients["marlow"].execute("withdraw_notice", retry_args, expected_version=retry_version, idempotency_key="lost-response")
            assert replay["replayed"] and replay["result"] is None and replay["receipt"] == first["receipt"]
            state = watch.workspace()["result"]
            assert state["brief"]["private_notes"] == canary
            assert state["introductions"][-1]["status"] == "closed"
            assert state["capacity"]["in_use"] == 0
            receipt_text = json.dumps(watch.receipts()["result"])
            assert canary not in receipt_text
            for secret in [canary.encode(), actors["marlow"]["token"].encode(), viewer["token"].encode(), key]:
                assert secret not in database.read_bytes()
            if args.browser:
                env = dict(os.environ, AGENTMATCH_VIEWER_URL=url, AGENTMATCH_VIEWER_TOKEN=viewer["token"])
                subprocess.run(["node", str(ROOT / "tools" / "test_agent_viewer.mjs")], cwd=ROOT, env=env, check=True)
            admin.revoke_credential(actors["marlow"]["id"])
            server.terminate(); server.wait(timeout=10)
            server, url = start()
            try:
                AgentClient(url, actors["marlow"]["token"]).workspace()
                raise AssertionError("Revoked agent survived restart")
            except AgentAPIError as exc:
                assert exc.status == 401
            report = {"fictional_only": True, "two_agent_http_journey": "passed", "standing_mission_without_per_command_admin": "passed", "restart_state": "passed",
                      "restart_idempotency": "passed", "restart_revocation": "passed", "encrypted_canary_scan": "passed",
                      "viewer_browser": "passed" if args.browser else "not_run", "real_owner_R1": "not_complete"}
            (args.out / "journey.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report))
        finally:
            if server is not None:
                server.terminate()
                server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
