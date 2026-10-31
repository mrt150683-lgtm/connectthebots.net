"""Install and verify the hosted service with sign-in disabled on Ubuntu 24.04.

Run as root in local WSL/VM or an ephemeral CI runner, never on production:
  sudo python3 tools/verify_server_staging.py --bundle /tmp/ctb-bundle \
      --out artifacts/server-staging

The bundle contains site/ and app/ as produced by the deployment tooling.
This script deliberately leaves the local staging installation available for
browser inspection. A subsequent run requires --reuse-staging and its marker.
It never changes firewall rules, SSH settings, DNS or external services.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler


MARKER = Path("/var/lib/connectthebots-staging-verifier.json")
TARGETS = (
    "/opt/connectthebots", "/etc/connectthebots", "/var/lib/connectthebots", "/var/backups/connectthebots",
    "/var/www/connectthebots", "/var/www/connectthebots-stats",
    "/etc/nginx/sites-available/connectthebots",
    "/etc/nginx/sites-enabled/connectthebots",
    "/etc/nginx/conf.d/connectthebots-log.conf",
    "/etc/systemd/system/connectthebots-app.service",
    "/etc/systemd/system/connectthebots-notifications.service",
    "/etc/systemd/system/connectthebots-notifications.timer",
    "/etc/systemd/system/connectthebots-backup.service",
    "/etc/systemd/system/connectthebots-backup.timer",
    "/etc/systemd/system/connectthebots-certificate.service",
    "/etc/systemd/system/connectthebots-certificate.timer",
)
BASE = "http://localhost"
DATABASE = Path("/var/lib/connectthebots/app.db")
VENV_PYTHON = "/opt/connectthebots/venv/bin/python"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def redact(text: str) -> str:
    # Older static setup scripts printed a generated stats credential once.
    text = re.sub(r"(?im)^.*user:\s*stats\s+password:.*$", "[stats credential redacted]", text)
    text = re.sub(r"https://sm\.hetrixtools\.net/hb/\?s=[A-Za-z0-9_-]+", "[heartbeat URL redacted]", text)
    return re.sub(r"(?im)^(SESSION_SECRET|GOOGLE_CLIENT_SECRET|SMTP_PASSWORD|BACKUP_ENCRYPTION_KEY|OFFSITE_B2_KEY_ID|OFFSITE_B2_APPLICATION_KEY|BACKUP_HEARTBEAT_URL)=.*$", r"\1=[redacted]", text)


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 1800) -> str:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(command, cwd=cwd, env=environment, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    output = redact(result.stdout)
    if result.returncode:
        raise VerificationError(f"Command {Path(command[0]).name} failed ({result.returncode}):\n{output}")
    return output


def request(path: str, *, data: bytes | None = None, method: str = "GET", port: int = 80,
            headers: dict[str, str] | None = None):
    # Explicitly bypass machine proxy settings; the target is always loopback.
    url = f"http://localhost:{port}{path}"
    req = Request(url, data=data, method=method, headers={**(headers or {}), "Host": "localhost"})
    try:
        response = build_opener(ProxyHandler({})).open(req, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def guard(args, repository: Path) -> None:
    require(sys.platform == "linux" and os.geteuid() == 0, "Use root on a disposable local Linux staging host")
    release = Path("/etc/os-release").read_text()
    require(re.search(r'^ID=ubuntu$', release, re.M) is not None
            and 'VERSION_ID="24.04"' in release, "Staging requires Ubuntu 24.04")
    require(Path("/run/systemd/system").is_dir(), "This verifier requires a systemd staging host")
    require((args.bundle / "site/deploy/server-setup.sh").is_file(), "Bundle has no site setup script")
    require((args.bundle / "app/SHA256SUMS").is_file(), "Bundle has no application digest manifest")
    marker_record = {"version": 1, "repository": str(repository), "base_url": BASE}
    if args.reuse_staging:
        require(MARKER.is_file() and not MARKER.is_symlink(), "No staging-verifier marker; refusing existing installation")
        require(json.loads(MARKER.read_text()) == marker_record, "Staging marker belongs to a different checkout or origin")
        env = Path("/etc/connectthebots/app.env")
        if env.exists():
            settings = dict(line.split("=", 1) for line in env.read_text().splitlines()
                            if line.strip() and not line.startswith("#") and "=" in line)
            require(settings.get("APP_ENV") == "staging"
                    and settings.get("PUBLIC_BASE_URL") == BASE
                    and settings.get("EMAIL_MODE") == "outbox"
                    and settings.get("ACCOUNT_SIGNIN_ENABLED", "false") == "false",
                    "Existing configuration is not this local staging environment")
        return
    require(not MARKER.exists(), "A staging marker exists; inspect it and explicitly use --reuse-staging")
    existing = [value for value in TARGETS if Path(value).exists() or Path(value).is_symlink()]
    require(not existing, "Refusing existing installation paths: " + ", ".join(existing))
    enabled = Path("/etc/nginx/sites-enabled")
    require(not enabled.exists() or not list(enabled.iterdir()), "Existing nginx sites require a separate disposable staging host")
    for port in (80, 8100):
        with socket.socket() as sock:
            require(sock.connect_ex(("127.0.0.1", port)) != 0, f"Port {port} already has a listener")
    MARKER.write_text(json.dumps(marker_record, indent=2) + "\n")
    MARKER.chmod(0o600)


def verify_backup_timer(configure, fictional: dict, out: Path, check) -> None:
    """Exercise a recognized older installed timer without any provider calls."""
    timer = "connectthebots-backup.timer"
    override = Path("/etc/systemd/system/connectthebots-backup.timer.d/40-ctb-heartbeat-schedule.conf")
    properties = "--property=TimersCalendar,AccuracyUSec,RandomizedDelayUSec"
    before = run(["systemctl", "show", timer, properties])
    check("unconfigured backup retains the nightly schedule", not override.exists()
          and "OnCalendar=*-*-* 02:15:00" in before and "RandomizedDelayUSec=5min" in before)
    confinement = run(["systemctl", "show", "connectthebots-backup.service",
                       "--property=PrivateNetwork,RestrictAddressFamilies,ReadWritePaths,ProtectSystem,User"])
    run(["systemctl", "stop", timer])
    try:
        configure.configure_backup_timer(override, fictional)
        check("offsite configuration alone does not enable monitored scheduling", not override.exists())
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(configure.LEGACY_BACKUP_TIMER_DROP_IN)
        override.chmod(0o644)
        run(["systemctl", "daemon-reload"])
        previous = run(["systemctl", "show", timer, properties])
        check("staging reproduces the previous installed quarter-hour calendar", "OnCalendar=*-*-* *:00/15:00" in previous)
        configured = fictional | {"BACKUP_HEARTBEAT_URL": "https://sm.hetrixtools.net/hb/?s=" + "fictional" * 4}
        configure.configure_backup_timer(override, configured)
        run(["systemctl", "daemon-reload"])
        hourly = run(["systemctl", "show", timer, properties])
        check("installer migrates the recognized timer to hourly", "OnCalendar=*-*-* *:00:00" in hourly
              and "00/15" not in hourly and override.read_text() == configure.BACKUP_TIMER_DROP_IN)
        check("hourly timer has no random delay and one-second accuracy", "RandomizedDelayUSec=0" in hourly
              and "AccuracyUSec=1s" in hourly)
        check("schedule migration preserves backup confinement", confinement == run([
            "systemctl", "show", "connectthebots-backup.service",
            "--property=PrivateNetwork,RestrictAddressFamilies,ReadWritePaths,ProtectSystem,User"]))
        (out / "backup-timer-migration.txt").write_text("Before:\n" + before + "Previous owned override:\n" + previous + "Migrated:\n" + hourly)
    finally:
        configure.configure_backup_timer(override, {})
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "start", timer])
    restored = run(["systemctl", "show", timer, properties])
    check("removing monitored configuration restores nightly scheduling", not override.exists()
          and "OnCalendar=*-*-* 02:15:00" in restored and "RandomizedDelayUSec=5min" in restored)


def verify(out: Path) -> list[dict]:
    results: list[dict] = []

    def check(name, condition):
        require(condition, name)
        results.append({"check": name, "passed": True})

    for _ in range(30):
        try:
            if request("/healthz")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    status, headers, body = request("/healthz")
    check("nginx forwards a minimal healthy response", status == 200 and json.loads(body) == {"status": "ok"})
    check("health is never cached", "no-store" in headers.get("Cache-Control", ""))
    for name, value in (("Content-Security-Policy", "default-src 'self'"),
                        ("X-Content-Type-Options", "nosniff"),
                        ("Permissions-Policy", "camera=()")):
        check("security header: " + name, value in headers.get(name, ""))
    check("nginx serves the landing page", request("/")[0] == 200)
    status, headers, body = request("/.well-known/security.txt")
    check("security contact reaches the app and truthfully refuses unconfigured HTTP staging",
          status == 503 and body == b"Security contact information is not currently available.\n"
          and headers.get("Content-Type", "").startswith("text/plain; charset=utf-8")
          and "no-store" in headers.get("Cache-Control", ""))
    check("security contact HEAD omits the response body", request("/.well-known/security.txt", method="HEAD")[2] == b"")
    check("unrelated well-known paths are not exposed", request("/.well-known/not-a-service")[0] == 404)
    status, _, body = request("/example")
    check("example dashboard remains explicitly fictional", status == 200 and b"example" in body.lower())
    for route in ("/signin", "/app"):
        status, _, body = request(route)
        check(route + " honestly reports accounts unavailable", status == 503 and b"Accounts are not open yet." in body)
    for route in ("/auth/google/start", "/app/account"):
        status, _, body = request(route)
        check(route + " refuses disabled account access", status == 503 and b"Sign-in is not open yet." in body)
    status, _, body = request("/api/v1/me")
    check("report identity refuses disabled intake", status == 503 and json.loads(body) == {"error": "Report intake is not open yet."})
    status, _, body = request("/api/v1/reports", method="POST", data=b"{}")
    check("report ingestion refuses disabled intake", status == 503 and json.loads(body) == {"error": "Report intake is not open yet."})
    for route in ("/board", "/app/posting"):
        status, _, body = request(route)
        check(route + " refuses disabled board access", status == 503 and b"The board is not open yet." in body)
    status, _, body = request("/board.json")
    check("public board JSON refuses disabled access", status == 503 and json.loads(body) == {"error": "The board is not open yet."})
    for route in ("/app/introductions", "/app/conversations"):
        status, _, body = request(route)
        check(route + " refuses disabled interaction access", status == 503 and b"Introductions and conversations are not open yet." in body)
    for route in ("/app/introductions", "/app/conversations/" + "a" * 43 + "/messages"):
        status, _, body = request(route, method="POST", data=b"")
        check(route + " refuses disabled interaction writes", status == 503 and b"Introductions and conversations are not open yet." in body)
    for route in ("/app/notifications", "/app/notifications/preferences"):
        status, _, body = request(route)
        check(route + " refuses disabled account access", status == 503 and b"Sign-in is not open yet." in body)
    for method in ("GET", "POST"):
        status, _, body = request("/app/report", method=method, data=b"" if method == "POST" else None)
        check(method + " concern reporting refuses disabled access", status == 503 and b"Reporting is not open yet." in body)
    status, headers, body = request("/email/unsubscribe/invalid")
    check("unsubscribe reaches the application through nginx without exposing identity",
          status in (400, 404, 410) and "default-src 'self'" in headers.get("Content-Security-Policy", ""))
    for route in ("/api/v1/unknown", "/admin", "/deploy/server-setup.sh"):
        check(route + " is not exposed as an implemented service", request(route)[0] == 404)
    check("API body limit enforced by app", request("/api/v1/reports", method="POST", data=b"x" * (32768 + 1))[0] == 413)
    check("API body limit enforced by nginx", request("/api/v1/reports", method="POST", data=b"x" * (65536 + 1))[0] == 413)
    check("general app body limit enforced", request("/app", method="POST", data=b"x" * (65536 + 1))[0] == 413)
    check("health refuses state-changing requests", request("/healthz", method="POST", data=b"")[0] == 405)

    # Only fixed fictional canaries are sent to this guarded disposable host.
    # Prove the deployed logging policy without retaining any log contents in
    # the evidence. Enabled-account error handling has separate HTTP tests.
    canary = "CTB-LOG-CANARY-7Q4"
    since = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    request("/" + canary + "?email=" + canary + "%40example.test")
    status, _, _ = request("/app/reports/preview?private=" + canary, method="POST",
                           data=("message=" + canary).encode(),
                           headers={"Authorization": "Bearer " + canary,
                                    "Content-Type": "application/x-www-form-urlencoded"})
    check("private log canary reaches disabled account boundary", status == 503)
    logs = "".join(path.read_text(errors="replace") for path in (
        Path("/var/log/nginx/connectthebots.access.log"), Path("/var/log/nginx/error.log")
    ) if path.is_file())
    logs += run(["journalctl", "--quiet", "--no-pager", "--since", since,
                 "-u", "connectthebots-app.service", "-u", "connectthebots-notifications.service"])
    check("installed logs omit fictional private path, query, body and bearer canaries", canary not in logs)

    with tempfile.TemporaryDirectory(prefix="ctb-installed-check-") as directory:
        script = (
            "import json, pathlib, connectthebots_server; "
            "from connectthebots_server.db import load_migrations; "
            "print(json.dumps({'source':str(pathlib.Path(connectthebots_server.__file__).resolve()),"
            "'migrations':[m.name for m in load_migrations()]}))"
        )
        installed = json.loads(run([VENV_PYTHON, "-I", "-c", script], cwd=Path(directory)))
        check("application imports from installed release wheel", installed["source"].startswith("/opt/connectthebots/releases/"))
        check("SQL migrations are packaged in the wheel", bool(installed["migrations"]))
        (out / "installed-wheel.json").write_text(json.dumps(installed, indent=2) + "\n")

    with sqlite3.connect(DATABASE) as connection:
        check("database uses WAL", connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
        history = connection.execute("SELECT name, sha256, applied_at FROM schema_migrations ORDER BY name").fetchall()
        check("all packaged migrations applied", [row[0] for row in history] == installed["migrations"])
        name, checksum, _ = history[0]
        try:
            connection.execute("UPDATE schema_migrations SET sha256 = ? WHERE name = ?", ("0" * 64, name))
            connection.commit()
            status, _, body = request("/healthz")
            check("schema drift produces a non-sensitive unavailable health response",
                  status == 503 and json.loads(body) == {"status": "unavailable"})
        finally:
            connection.execute("UPDATE schema_migrations SET sha256 = ? WHERE name = ?", (checksum, name))
            connection.commit()
        check("health recovers after exact history restoration", request("/healthz")[0] == 200)
        with tempfile.TemporaryDirectory(prefix="ctb-backup-check-") as scratch:
            with sqlite3.connect(Path(scratch) / "restored.db") as restored:
                connection.backup(restored)
                check("SQLite backup restores and passes integrity check", restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
                check("restored schema history matches source", restored.execute("SELECT name, sha256, applied_at FROM schema_migrations ORDER BY name").fetchall() == history)

    env_stat = Path("/etc/connectthebots/app.env").stat()
    check("environment file is root-owned and mode 0600", env_stat.st_uid == 0 and stat.S_IMODE(env_stat.st_mode) == 0o600)
    service = run(["systemctl", "show", "connectthebots-app", "--property=User,NoNewPrivileges,ProtectSystem,ProtectHome,PrivateTmp,CapabilityBoundingSet,ReadWritePaths"])
    expected = ("User=ctb", "NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=yes",
                "PrivateTmp=yes", "CapabilityBoundingSet=", "ReadWritePaths=/var/lib/connectthebots")
    check("systemd applies the required unprivileged service hardening", all(line in service.splitlines() for line in expected))
    (out / "service-hardening.txt").write_text(service)
    timer = run(["systemctl", "is-active", "connectthebots-notifications.timer"]).strip()
    check("notification timer is active", timer == "active")
    worker = run(["systemctl", "show", "connectthebots-notifications.service", "--property=User,NoNewPrivileges,ProtectSystem,ProtectHome,PrivateTmp,CapabilityBoundingSet,ReadWritePaths"])
    check("notification worker applies the same unprivileged hardening", all(line in worker.splitlines() for line in expected))
    # This invokes the installed CLI under its real unit/environment. Staging
    # is outbox-only; disabled accounts produce no provider connection.
    run(["systemctl", "start", "connectthebots-notifications.service"])
    result = run(["systemctl", "show", "connectthebots-notifications.service", "--property=Result,ExecMainStatus"])
    check("installed notification tick finishes successfully", "Result=success" in result and "ExecMainStatus=0" in result)
    (out / "notification-worker-hardening.txt").write_text(worker + result)
    backup_dir = Path("/var/backups/connectthebots")
    ctb_uid = int(run(["id", "-u", "ctb"]).strip())
    backup_stat = backup_dir.lstat()
    check("backup directory is private and owned by the service account",
          stat.S_ISDIR(backup_stat.st_mode) and backup_stat.st_uid == ctb_uid
          and stat.S_IMODE(backup_stat.st_mode) == 0o700)
    for name in ("backup", "certificate"):
        check(name + " timer is active", run(["systemctl", "is-active", "connectthebots-" + name + ".timer"]).strip() == "active")
    backup = run(["systemctl", "show", "connectthebots-backup.service", "--property=User,NoNewPrivileges,ProtectSystem,ProtectHome,PrivateTmp,PrivateNetwork,RestrictAddressFamilies,CapabilityBoundingSet,ReadWritePaths"])
    backup_expected = (*expected[:-1], "ReadWritePaths=/var/lib/connectthebots /var/backups/connectthebots", "PrivateNetwork=yes", "RestrictAddressFamilies=AF_UNIX")
    check("backup worker is confined and has no network", all(line in backup.splitlines() for line in backup_expected))
    previous = {path.name for path in backup_dir.glob("ctb-backup-*.ctb")}
    run(["systemctl", "start", "connectthebots-backup.service"])
    backup_result = run(["systemctl", "show", "connectthebots-backup.service", "--property=Result,ExecMainStatus"])
    check("installed encrypted backup unit finishes successfully", "Result=success" in backup_result and "ExecMainStatus=0" in backup_result)
    snapshots = [path for path in backup_dir.glob("ctb-backup-*.ctb") if path.name not in previous]
    check("installed worker creates an encrypted backup", bool(snapshots))
    protected = True
    for path in snapshots:
        info = path.lstat()
        with path.open("rb") as encrypted:
            protected = protected and encrypted.read(13) == b"CTB-BACKUP-1\n"
        protected = protected and stat.S_ISREG(info.st_mode) and info.st_uid == ctb_uid and stat.S_IMODE(info.st_mode) == 0o600
    check("encrypted backup files have the service owner and mode 0600", protected)
    (out / "backup-worker-hardening.txt").write_text(backup + backup_result)
    # Exercise the installer-owned conditional override using fictional values.
    # No credentials enter the real environment, and the network-enabled unit
    # is never started. The effective systemd policy, not just file text, is
    # verified before the original local-only policy is restored.
    configure_path = Path(__file__).resolve().parents[1] / "site/deploy/configure_app.py"
    spec = importlib.util.spec_from_file_location("staging_configure_app", configure_path)
    configure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configure)
    override = Path("/etc/systemd/system/connectthebots-backup.service.d/40-ctb-offsite-network.conf")
    check("disabled staging has no offsite network override", not override.exists())
    fictional = {"OFFSITE_B2_KEY_ID": "fictional-key", "OFFSITE_B2_APPLICATION_KEY": "fictional-secret",
                 "OFFSITE_B2_BUCKET_ID": "fictional-bucket", "OFFSITE_B2_PREFIX": "ctb-" + "a" * 32 + "/"}
    try:
        configure.configure_backup_network(override, fictional)
        run(["systemctl", "daemon-reload"])
        enabled = run(["systemctl", "show", "connectthebots-backup.service", "--property=PrivateNetwork,RestrictAddressFamilies,ReadWritePaths,User,NoNewPrivileges,ProtectSystem,ProtectHome,PrivateTmp,CapabilityBoundingSet"])
        enabled_lines = enabled.splitlines()
        check("configured offsite backup permits normal outbound IP networking", "PrivateNetwork=no" in enabled_lines
              and set(next(line.partition("=")[2] for line in enabled_lines if line.startswith("RestrictAddressFamilies=")).split()) == {"AF_UNIX", "AF_INET", "AF_INET6"})
        check("offsite networking retains unprivileged filesystem confinement", all(line in enabled_lines for line in expected[:-1])
              and "ReadWritePaths=/var/lib/connectthebots /var/backups/connectthebots" in enabled_lines)
        (out / "backup-offsite-network-policy.txt").write_text(enabled)
    finally:
        configure.configure_backup_network(override, {})
        run(["systemctl", "daemon-reload"])
    restored = run(["systemctl", "show", "connectthebots-backup.service", "--property=PrivateNetwork,RestrictAddressFamilies"])
    check("disabling offsite restores local-only backup networking", not override.exists()
          and "PrivateNetwork=yes" in restored.splitlines() and "RestrictAddressFamilies=AF_UNIX" in restored.splitlines())
    verify_backup_timer(configure, fictional, out, check)
    certificate = run(["systemctl", "show", "connectthebots-certificate.service", "--property=User,NoNewPrivileges,ProtectSystem,ProtectHome,PrivateTmp,CapabilityBoundingSet,ReadWritePaths"])
    check("certificate worker has no backup-directory write permission", all(line in certificate.splitlines() for line in expected))
    run(["systemctl", "start", "connectthebots-certificate.service"])
    certificate_result = run(["systemctl", "show", "connectthebots-certificate.service", "--property=Result,ExecCondition,ExecMainStartTimestampMonotonic"])
    check("HTTP-only staging skips certificate network work",
          "Result=exec-condition" in certificate_result and "status=1" in certificate_result
          and "ExecMainStartTimestampMonotonic=0" in certificate_result)
    (out / "certificate-worker-hardening.txt").write_text(certificate + certificate_result)
    (out / "nginx-check.txt").write_text(run(["nginx", "-t"]))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("artifacts/server-staging"))
    parser.add_argument("--reuse-staging", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="Requires --reuse-staging; skip installation")
    args = parser.parse_args()
    args.bundle = args.bundle.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    repository = Path(__file__).resolve().parents[1]
    try:
        require(not args.verify_only or args.reuse_staging, "--verify-only requires --reuse-staging")
        guard(args, repository)
        if not args.verify_only:
            command = ["bash", str(args.bundle / "site/deploy/server-setup.sh"), "--with-app",
                       "--environment", "staging", "--domain", "localhost", "--skip-tls", "--no-firewall"]
            output = run(command)
            (args.out / "setup.log").write_text(output)
        checks = verify(args.out)
        record = {"passed": True, "scope": "local Ubuntu hosted service with sign-in disabled; no TLS",
                  "checked_at": datetime.now(timezone.utc).isoformat(), "base_url": BASE,
                  "checks": checks}
        (args.out / "verification.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"PASS: {len(checks)} local staging checks; evidence: {args.out}")
        return 0
    except (VerificationError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        message = redact(str(error))
        (args.out / "failure.txt").write_text(message + "\n")
        (args.out / "verification.json").write_text(json.dumps({"passed": False, "error": message}, indent=2) + "\n")
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
