"""Local administrator and agent CLI; admin operations are never HTTP routes.

The administrator process is a trusted provisioning boundary. The key comes
from an external 32-byte file; the agent client reads only an environment token.
Neither boundary attempts to defeat software running as the same OS account.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from agentmatch.numeric import strict_json_loads


def _json(value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentMatch fictional agent runtime and read-only viewer")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "serve", "issue", "revoke", "authorize", "authorize-mission", "revoke-mission"):
        p = sub.add_parser(name)
        p.add_argument("--db", type=Path, required=True)
        p.add_argument("--key-file", type=Path, required=True, help="External binary 32-byte vault key, never a bearer credential")
        if name == "init":
            p.add_argument("--fictional-demo", action="store_true", required=True)
        elif name == "serve":
            p.add_argument("--port", type=int, default=8766)
            p.add_argument("--viewer-dir", type=Path, default=Path(__file__).resolve().parents[2] / "workbench" / "viewer")
        elif name == "issue":
            p.add_argument("--owner", required=True)
            p.add_argument("--role", choices=("agent", "viewer"), required=True)
            p.add_argument("--scope", action="append", default=[])
            p.add_argument("--ttl-seconds", type=int, default=3600)
            p.add_argument("--max-uses", type=int, default=100)
        elif name == "revoke":
            p.add_argument("--credential", required=True)
        elif name == "authorize":
            p.add_argument("--credential", required=True)
            p.add_argument("--request", type=Path, required=True, help="Reviewed JSON {operation,arguments}; no shell-embedded private text")
        elif name == "authorize-mission":
            p.add_argument("--credential", required=True)
            p.add_argument("--policy", type=Path, required=True, help="Reviewed bounded mission JSON; scopes remain an upper bound")
        elif name == "revoke-mission":
            p.add_argument("--credential", required=True)
            p.add_argument("--mission", required=True)
    p = sub.add_parser("call")
    p.add_argument("--url", default="http://127.0.0.1:8766")
    p.add_argument("--token-env", default="AGENTMATCH_TOKEN")
    p.add_argument("--request", type=Path, help="Command JSON; omit to read workspace")
    sub.add_parser("schema")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            from .catalogue import openapi
            _json(openapi())
            return 0
        if args.command == "call":
            from .client import AgentClient
            token = os.environ.get(args.token_env)
            if not token:
                raise ValueError("Set the named environment variable to an agent or viewer credential.")
            client = AgentClient(args.url, token)
            _json(client.execute(**strict_json_loads(args.request.read_bytes())) if args.request else client.workspace())
            return 0
        from .runtime import AgentRuntime
        key = args.key_file.read_bytes()
        if len(key) != 32:
            raise ValueError("The external vault key must contain exactly 32 bytes.")
        if args.command == "init":
            AgentRuntime.create(args.db, key)
            _json({"created": True, "fictional_only": True})
            return 0
        runtime = AgentRuntime.open(args.db, key)
        if args.command == "serve":
            from .http_server import serve
            serve(runtime, port=args.port, viewer_dir=args.viewer_dir)
        elif args.command == "issue":
            _json(runtime.issue_credential(owner_id=args.owner, role=args.role,
                                          scopes=args.scope or (["workspace"] if args.role == "viewer" else []),
                                          ttl_seconds=args.ttl_seconds, max_uses=args.max_uses))
        elif args.command == "revoke":
            _json(runtime.revoke_credential(args.credential))
        elif args.command == "authorize":
            command = strict_json_loads(args.request.read_bytes(), max_bytes=65536)
            if not isinstance(command, dict) or set(command) != {"operation", "arguments"}:
                raise ValueError("Approval file must contain exactly operation and arguments.")
            _json(runtime.authorize_command(args.credential, **command))
        elif args.command == "authorize-mission":
            policy = strict_json_loads(args.policy.read_bytes(), max_bytes=65536)
            _json(runtime.authorize_mission(args.credential, policy))
        elif args.command == "revoke-mission":
            _json(runtime.revoke_mission(args.credential, args.mission))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # Preserve structured safe runtime errors; never echo file bytes/tokens.
        if hasattr(exc, "to_dict"):
            _json(exc.to_dict())
        elif hasattr(exc, "payload"):
            _json(exc.payload)
        else:
            _json({"error": {"code": "LOCAL_SETUP_ERROR", "message": "Check the command, optional runtime dependency, paths and external key. No private input was printed."}})
        return 1
