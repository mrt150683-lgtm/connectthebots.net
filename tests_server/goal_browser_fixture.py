"""Disposable loopback goal browser fixture; filesystem controls, no test routes."""
from __future__ import annotations

import argparse
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import subprocess
import tempfile
import threading

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(REPOSITORY / "src"))

from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from connectthebots_server import agent_texts, growth_config
from connectthebots_server.app import create_app
from connectthebots_server.auth import GoogleProvider
from connectthebots_server.config import Settings
from connectthebots_server.sessions import storage_time
from tests_server.oidc_fixture import FixtureClock, LoopbackGoogleTransport, SyntheticOIDC, listener, serve
from tests_server.test_goals import fictional_charter


def seed(app, clock):
    database, now = app.state.database, clock()
    with database.transaction() as connection:
        owners = {row["google_sub"].removeprefix("fixture-"): row["id"] for row in connection.execute(
            "SELECT id,google_sub FROM users WHERE google_sub IN ('fixture-ama','fixture-joon')")}
        if set(owners) != {"ama", "joon"}:
            raise ValueError("Sign in both fictional people before seeding goals")
        if connection.execute("SELECT COUNT(*) FROM goals").fetchone()[0]:
            raise ValueError("Fictional goals may be seeded only once")
        growth_config.store(connection, "feature.goals", "everyone", now)
        growth_config.store(connection, "goals_led_per_member", 2, now)
        for owner in owners.values():
            connection.execute("UPDATE users SET created_at=? WHERE id=?", (storage_time(now - timedelta(days=30)), owner))
            connection.execute(
                "INSERT INTO early_access_grants(user_id,source,reference,granted_at,expires_at) VALUES(?,?,?,?,?)",
                (owner, "referral_perk", "fictional-goal-browser", storage_time(now), storage_time(now + timedelta(days=30))))
    for person, owner in owners.items():
        app.state.taste.update_member(owner, {"first_name": person.title()})
    slugs = []
    for title in ("Fictional: tidal clock puzzle", "Fictional: prism notation puzzle"):
        goal = app.state.goals.propose(owners["ama"], fictional_charter(title), True)["goal"]
        slug = goal["slug"]
        app.state.goals.operator_action(slug, "approve", "Fictional browser pilot charter reviewed.")
        for owner in owners.values():
            app.state.goal_members.join(owner, slug, {"capabilities": ["mathematics"], "approved_by_owner": True})
        slugs.append(slug)
    # These fictional cleartext keys exist only in a consumed, temporary ack.
    keys = {person: app.state.reports.create_key(owner, "Fictional browser agent")["key"] for person, owner in owners.items()}
    return {"owners": owners, "keys": keys, "slugs": slugs,
            "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION,
            "lead_notice_version": agent_texts.GOAL_LEAD_VERSION,
            "until": (now.date() + timedelta(days=30)).isoformat()}


def seed_safety(app, clock):
    """One neutral sensitive scope after the ordinary owner-stop journey."""
    database, now = app.state.database, clock()
    with database.connect() as connection:
        owners = {row["google_sub"].removeprefix("fixture-"): row["id"] for row in connection.execute(
            "SELECT id,google_sub FROM users WHERE google_sub IN ('fixture-ama','fixture-joon')")}
        if set(owners) != {"ama", "joon"} or connection.execute("SELECT 1 FROM goals WHERE tier='sensitive'").fetchone():
            raise ValueError("Safety scope requires the two existing fictional owners and may be seeded once")
        standards = [row["slug"] for row in connection.execute("SELECT slug FROM goals WHERE tier='standard' AND state='open'")]
    for slug in standards:
        app.state.goals.operator_action(slug, "pause", "Fictional ordinary browser journey is complete; reserve this scope for role testing.")
    with database.transaction() as connection:
        growth_config.store(connection, "feature.goals", "everyone", now)
        growth_config.store(connection, "goals_sensitive_open_max", 2, now)
        # Two fictional people only; self-reports remain excluded by the real service.
        growth_config.store(connection, "report_hide_threshold", 1, now)
    charter = fictional_charter("Fictional: published study comparisons", "animal_health")
    charter["aim"] = "Compare published fictional methods and sample counts without giving individual advice."
    goal = app.state.goals.propose(owners["ama"], charter, True)["goal"]
    app.state.goals.operator_action(goal["slug"], "approve", "Fictional neutral study-comparison scope reviewed for the local browser fixture.")
    handles = {person: app.state.goal_members.join(owner, goal["slug"],
               {"capabilities": ["mathematics"], "approved_by_owner": True})["member"]["handle"] for person, owner in owners.items()}
    detail = app.state.goals.detail(owners["ama"], goal["slug"])["goal"]
    keys = {person: app.state.reports.create_key(owner, "Fictional safety browser agent")["key"] for person, owner in owners.items()}
    return {"slug": goal["slug"], "task_id": detail["open_tasks"][0]["id"], "charter_revision": detail["charter_revision"],
            "keys": keys, "handles": handles, "banner": agent_texts.GOAL_SENSITIVE_BANNER,
            "hold_notice": agent_texts.GOAL_SAFETY_HOLD, "role_notice_version": agent_texts.GOAL_LEAD_ROLE_VERSION,
            "accessed": now.date().isoformat(),
            "until": (now.date() + timedelta(days=30)).isoformat()}


def seed_pages(app, clock):
    """Public-page/check-in tail after the original two-owner work journey."""
    with app.state.database.connect() as connection:
        owners = {row["google_sub"].removeprefix("fixture-"): row["id"] for row in connection.execute(
            "SELECT id,google_sub FROM users WHERE google_sub IN ('fixture-ama','fixture-joon','fixture-priya')")}
        if set(owners) != {"ama", "joon", "priya"}:
            raise ValueError("Sign in the third fictional reader before the public-page tail")
        paused = [row["slug"] for row in connection.execute("SELECT slug FROM goals WHERE category='mathematics' AND state='paused'")]
    for slug in paused:
        app.state.goals.operator_action(slug, "resume", "Fictional public-page fixture resumes reviewed scope without granting work.")
    private = app.state.goals.propose(owners["joon"],
        fictional_charter("Fictional: private M5 scope canary", "animal_health"), True)["goal"]
    with app.state.database.transaction() as connection:
        # Match the existing fictional owners' eligibility, without changing
        # the real minimum-age rule or creating membership/work permission.
        connection.execute("UPDATE users SET created_at=? WHERE id=?",
                           (storage_time(clock() - timedelta(days=30)), owners["priya"]))
    app.state.taste.update_member(owners["priya"], {"first_name": "Priya"})
    app.state.taste.save_answers(owners["priya"], [{"topic": "hobbies", "items": ["mathematics"], "tier": "ask"}])
    key = app.state.reports.create_key(owners["priya"], "Fictional public-page reader")["key"]
    return {"private_slug": private["slug"], "key": key}


def interest_tier(app, command):
    if command.get("tier") not in ("ask", "share", "never"):
        raise ValueError("Unknown fictional interest tier")
    with app.state.database.connect() as connection:
        row = connection.execute("SELECT id FROM users WHERE google_sub='fixture-priya'").fetchone()
        if row is None:
            raise ValueError("The fictional reader has not signed in")
        owner = row["id"]
    app.state.taste.save_answers(owner, [{"topic": "hobbies", "items": ["mathematics"], "tier": command["tier"]}])


def operator_command(app, clock, command):
    """Run the actual CLI in a scratch-only environment, never an admin route."""
    action = command.get("operator_action")
    if action in ("release", "unhide"):
        entry_id = command.get("entry_id")
        if not isinstance(entry_id, str) or re.fullmatch(r"e_[0-9a-f]{64}", entry_id) is None:
            raise ValueError("Invalid fictional operator entry")
        arguments = ["goals", action, entry_id, "Fictional operator checked the local source and recorded this decision."]
    elif action == "assign-lead":
        slug, handle = command.get("slug"), command.get("handle")
        if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9-]{1,80}", slug) is None or not isinstance(handle, str) or re.fullmatch(r"c_[0-9a-f]{8}", handle) is None:
            raise ValueError("Invalid fictional operator assignment")
        arguments = ["goals", action, slug, handle, "Fictional owner-approved lead assignment in the local browser fixture."]
    else:
        raise ValueError("Unknown fictional operator action")
    scratch = app.state.database.path.parent
    cli_temp = scratch / "operator-temp"
    cli_temp.mkdir(exist_ok=True)
    environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT") if key in os.environ}
    environment.update({"APP_ENV": "staging", "PUBLIC_BASE_URL": "https://fictional-goals.example.test",
        "DATABASE_PATH": str(app.state.database.path.resolve()), "SESSION_SECRET": "fictional-browser-operator-" + "x" * 40,
        "SIGNUPS": "closed", "EMAIL_MODE": "outbox", "ACCOUNT_SIGNIN_ENABLED": "false",
        "PYTHONPATH": str(REPOSITORY / "src"), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(cli_temp), "TMP": str(cli_temp)})
    # Preserve the fixture's deliberate time advance across a real process/CLI
    # boundary. No production clock or application route is changed.
    wrapper = ("from datetime import datetime; import sys; from connectthebots_server import clock; "
               "now=datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')); "
               "clock.utc_now=lambda:now; from connectthebots_server import admin; raise SystemExit(admin.main(sys.argv[2:]))")
    expected_success = command.get("expected_success", True)
    if type(expected_success) is not bool:
        raise ValueError("Invalid fictional operator outcome expectation")
    result = subprocess.run([sys.executable, "-c", wrapper, storage_time(clock()), *arguments], cwd=REPOSITORY,
                            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=45)
    if (result.returncode == 0) != expected_success:
        raise ValueError("Fictional operator command failed: " + result.stderr[:400])
    if result.returncode == 0:
        json.loads(result.stdout)
    elif result.returncode != 2 or result.stdout or not result.stderr.strip() or "Traceback" in result.stderr:
        raise ValueError("A refused fictional operator action did not return the controlled CLI refusal")
    return {"operator": {"action": action, "success": result.returncode == 0, "returncode": result.returncode}}


def snapshot(app, clock, provider):
    """Independent SQL evidence, excluding keys, tokens and approval authority."""
    with app.state.database.connect() as connection:
        connection.execute("BEGIN")
        people = {row["id"]: row["google_sub"].removeprefix("fixture-") for row in connection.execute(
            "SELECT id,google_sub FROM users WHERE google_sub IN ('fixture-ama','fixture-joon','fixture-priya')")}
        allocations = []
        for row in connection.execute("SELECT a.*,g.slug FROM goal_allocations a JOIN goals g ON g.id=a.goal_id ORDER BY g.slug,a.user_id"):
            allocations.append({"person": people[row["user_id"]], "slug": row["slug"],
                **{name: row[name] for name in ("sessions_per_week", "minutes_per_session", "stopped_at", "stop_reason", "show_first_name", "charter_revision", "lead_notice_version", "lead_approved_at")},
                "active": row["stopped_at"] is None and row["permission_expires_at"] > storage_time(clock()),
                "approval_digest": sha256(row["approval_id"].encode()).hexdigest()})
        sessions = [{"person": people[row["user_id"]], "slug": row["slug"],
                     **{name: row[name] for name in ("id", "ends_at", "grace_ends_at", "ended_at", "end_reason", "reserved_minutes", "minutes_used")}}
                    for row in connection.execute("SELECT s.*,g.slug FROM goal_sessions s JOIN goals g ON g.id=s.goal_id ORDER BY s.started_at,s.id")]
        members = [{"person": people[row["user_id"]], "slug": row["slug"], "show_first_name": row["show_first_name"], "state": row["state"]}
                   for row in connection.execute("SELECT m.*,g.slug FROM goal_members m JOIN goals g ON g.id=m.goal_id ORDER BY g.slug,m.user_id")]
        entries = [{"person": people.get(row["author_user_id"], "system"),
                    **{name: row[name] for name in ("id", "slug", "seq", "kind", "status", "created_at", "content_sha256", "entry_sha256")},
                    "content_present": row["content_json"] is not None}
                   for row in connection.execute("SELECT e.*,g.slug FROM goal_entries e JOIN goals g ON g.id=e.goal_id ORDER BY e.goal_id,e.seq")]
        goals = [{"slug": row["slug"], "lead": people.get(row["lead_user_id"]), "state": row["state"], "tier": row["tier"], "charter_revision": row["charter_revision"]}
                 for row in connection.execute("SELECT * FROM goals ORDER BY slug")]
        role_approvals = [{"slug": row["slug"], "person": people[row["user_id"]], "state": row["state"], "charter_revision": row["charter_revision"]}
                         for row in connection.execute("SELECT a.*,g.slug FROM goal_lead_approvals a JOIN goals g ON g.id=a.goal_id ORDER BY g.slug,a.user_id")]
        handovers = [{"slug": row["slug"], "person": people[row["user_id"]], "state": row["state"], "charter_revision": row["charter_revision"]}
                     for row in connection.execute("SELECT h.*,g.slug FROM goal_lead_handovers h JOIN goals g ON g.id=h.goal_id ORDER BY g.slug")]
        reports = [{"entry_id": row["entry_id"], "reviewed": row["reviewed_at"] is not None} for row in connection.execute("SELECT entry_id,reviewed_at FROM goal_reports ORDER BY id")]
        # Independent evidence that reminders neither grant nor start work.
        # Only the digest is persisted; private permission values stay in SQLite.
        work_rows = {table: [dict(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY rowid")]
                     for table in ("goals", "goal_members", "goal_allocations", "goal_sessions", "goal_tasks", "goal_entries")}
        work_digest = sha256(json.dumps(work_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"clock": storage_time(clock()), "feature": growth_config.load(connection)["feature.goals"],
                "allocations": allocations, "sessions": sessions, "members": members, "entries": entries,
                "goals": goals, "role_approvals": role_approvals, "handovers": handovers, "reports": reports,
                "work_digest": work_digest,
                "foreign_keys_valid": not bool(connection.execute("PRAGMA foreign_key_check").fetchone()),
                "oidc": {"code_exchanges": provider.exchanges, "pkce_verified": provider.pkce_verified, "jwks_reads": provider.jwks_reads}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    clock, servers = FixtureClock(frozen=True), []
    provider = SyntheticOIDC(clock)
    scratch_parent = directory if sys.platform == "win32" else None
    with tempfile.TemporaryDirectory(prefix="fictional-goals-", dir=scratch_parent) as scratch:
        try:
            provider_socket = listener()
            provider_url = "http://127.0.0.1:" + str(provider_socket.getsockname()[1])
            serve(provider.app(), provider_socket, servers)
            app_socket = listener()
            base = "http://localhost:" + str(app_socket.getsockname()[1])
            settings = Settings.from_env({"APP_ENV": "staging", "PUBLIC_BASE_URL": base,
                "DATABASE_PATH": str(Path(scratch) / "goals.db"), "SESSION_SECRET": secrets.token_urlsafe(48),
                "SIGNUPS": "open", "EMAIL_MODE": "outbox", "ACCOUNT_SIGNIN_ENABLED": "true",
                "GOOGLE_CLIENT_ID": "fictional-browser-client", "GOOGLE_CLIENT_SECRET": "fictional-browser-secret"})
            google = GoogleProvider(settings, clock, transport=LoopbackGoogleTransport(provider_url))
            google.authorization_url = provider_url + "/authorize"
            provider.allowed_redirects.add(base + "/auth/google/callback")
            app = create_app(settings, clock, google_provider=google)
            # Production serves the same static assets through nginx; no test
            # mutation or data-inspection endpoint is added to the app.
            app.router.routes.append(Mount("/assets", app=StaticFiles(directory=REPOSITORY / "site/assets")))
            serve(app, app_socket, servers)
            (directory / "ready.json").write_text(json.dumps({"url": base, "provider_url": provider_url, "clock": storage_time(clock())}) + "\n", encoding="utf-8")
            print("Fictional goal browser fixture ready", flush=True)
            command_file = directory / "goals-command.json"
            while not stopped.wait(0.05) and not (directory / "stop").exists():
                if not command_file.exists():
                    continue
                try:
                    command = json.loads(command_file.read_text(encoding="utf-8"))
                    command_file.unlink()
                except (FileNotFoundError, PermissionError):
                    continue
                try:
                    action, extra = command.get("action"), {}
                    if action == "seed":
                        extra = seed(app, clock)
                    elif action == "seed_safety":
                        extra = seed_safety(app, clock)
                    elif action == "seed_pages":
                        extra = seed_pages(app, clock)
                    elif action == "interest_tier":
                        interest_tier(app, command)
                    elif action == "operator":
                        extra = operator_command(app, clock, command)
                    elif action == "advance":
                        clock.advance(command["seconds"])
                    elif action in ("feature_off", "feature_on"):
                        with app.state.database.transaction() as connection:
                            growth_config.store(connection, "feature.goals", "off" if action == "feature_off" else "everyone", clock())
                    elif action != "snapshot":
                        raise ValueError("Unknown fictional goal command")
                    result = {"id": command["id"], "snapshot": snapshot(app, clock, provider), **extra}
                except Exception as error:
                    result = {"id": command.get("id"), "error": type(error).__name__ + ": " + str(error)}
                pending = directory / "goals-ack.pending"
                pending.write_text(json.dumps(result) + "\n", encoding="utf-8")
                pending.replace(directory / "goals-ack.json")
            evidence = {"fixture_only": True, "real_google_tested": False, **snapshot(app, clock, provider)}
            (directory / "fixture-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        finally:
            for filename in ("goals-ack.json", "goals-ack.pending"):
                (directory / filename).unlink(missing_ok=True)
            for server, _, _ in servers:
                server.should_exit = True
            for _, thread, sock in servers:
                thread.join(timeout=5)
                sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
