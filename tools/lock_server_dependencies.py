"""Write a cross-platform wheel hash lock from a previously resolved pip report.

Resolve intentionally chosen exact direct pins first with pip --dry-run
--ignore-installed --report. This tool does not select or upgrade versions.
Hashes are read from the matching release's PyPI JSON metadata; source archives
are excluded because deployment uses --only-binary=:all:.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("requirements-server.lock"))
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    lines = ["# Exact hosted runtime dependency closure. Wheels only, all supported platforms.",
             "# Regenerate from a reviewed pip resolution with tools/lock_server_dependencies.py.",
             "# Install: pip install --require-hashes --only-binary=:all: -r requirements-server.lock", ""]
    for item in sorted(report["install"], key=lambda entry: entry["metadata"]["name"].lower()):
        name, version = item["metadata"]["name"], item["metadata"]["version"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not re.fullmatch(r"[A-Za-z0-9_.+!-]+", version):
            raise ValueError("Invalid package identity in resolution")
        request = Request(f"https://pypi.org/pypi/{name}/{version}/json", headers={"User-Agent": "ConnectTheBots-lock-builder"})
        with urlopen(request, timeout=30) as response:
            release = json.load(response)
        hashes = sorted({entry["digests"]["sha256"] for entry in release["urls"]
                         if entry["packagetype"] == "bdist_wheel" and not entry["yanked"]})
        if not hashes or any(not re.fullmatch(r"[a-f0-9]{64}", digest) for digest in hashes):
            raise ValueError(f"No valid unyanked wheel hashes for {name}")
        resolved_hash = item["download_info"]["archive_info"]["hashes"]["sha256"]
        if resolved_hash not in hashes:
            raise ValueError(f"Resolved wheel no longer matches release metadata: {name}")
        lines.append(f"{name}=={version} \\")
        lines.extend("    --hash=sha256:" + digest + (" \\" if i < len(hashes)-1 else "")
                     for i, digest in enumerate(hashes))
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Pinned {len(report['install'])} resolved packages; no versions selected by this tool.")


if __name__ == "__main__":
    main()
