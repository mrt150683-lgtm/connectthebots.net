"""Build a fresh, reviewable site + application-wheel deployment directory.

Only declared public site files and deployment tooling are copied. Repository
history, tests, caches and local environment/database/key files are excluded.
The output must not exist; this command never overwrites a previous release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile


PUBLIC_PAGES = ("index.html", "about.html", "signin.html", "dashboard.html", "film.html", "deals.html", "prompts.html", "agents.json", "llms.txt")
DEPLOY_FILES = (
    "server-setup.sh", "app-setup.sh", "deploy.sh", "deploy.ps1",
    "verify_bundle.py", "configure_app.py", "app.env.example", "connectthebots-app.service",
    "connectthebots-notifications.service", "connectthebots-notifications.timer",
    "connectthebots-backup.service", "connectthebots-backup.timer",
    "connectthebots-certificate.service", "connectthebots-certificate.timer",
)
ASSET_SUFFIXES = {".css", ".js", ".svg", ".woff2", ".woff", ".ttf", ".txt", ".md",
                  ".jpg", ".jpeg", ".png", ".ico", ".mp4", ".webm", ".vtt"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(wheel: Path, out: Path, repository: Path) -> dict:
    wheel = wheel.resolve(strict=True)
    if not re.fullmatch(r"agentmatch-[A-Za-z0-9_.+]+-py3-none-any\.whl", wheel.name):
        raise ValueError("Expected a built universal agentmatch wheel")
    with zipfile.ZipFile(wheel) as archive:
        entries = archive.namelist()
        if "connectthebots_server/app.py" not in entries or not any(
            name.startswith("connectthebots_server/migrations/") and name.endswith(".sql") for name in entries
        ):
            raise ValueError("Wheel is missing the hosted application or packaged SQL migrations")
    lock = repository / "requirements-server.lock"
    if not lock.is_file() or lock.is_symlink():
        raise ValueError("Missing regular requirements-server.lock")
    site = repository / "site"
    sources = [site / page for page in PUBLIC_PAGES]
    sources += [site / "deploy" / name for name in DEPLOY_FILES]
    for item in sorted((site / "assets").rglob("*")):
        if item.is_symlink():
            raise ValueError("Public assets must not contain symbolic links")
        if item.is_file() and item.suffix.lower() in ASSET_SUFFIXES and not any(part.startswith(".") for part in item.relative_to(site).parts):
            sources.append(item)
    for source in sources:
        if not source.is_file() or source.is_symlink():
            raise ValueError("Missing regular public/deployment file: " + str(source.relative_to(repository)))
    out.mkdir(parents=True, exist_ok=False)
    for source in sources:
        destination = out / "site" / source.relative_to(site)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    app = out / "app"
    app.mkdir()
    shutil.copyfile(wheel, app / wheel.name)
    shutil.copyfile(lock, app / lock.name)
    checksums = {name: digest(app / name) for name in (wheel.name, lock.name)}
    (app / "SHA256SUMS").write_text("".join(f"{value}  {name}\n" for name, value in checksums.items()), encoding="utf-8", newline="\n")
    record = {"application": checksums, "site_files": len(sources), "bundle": str(out)}
    (out / "release.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.wheel, args.out.resolve(), Path(__file__).resolve().parents[1])
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
