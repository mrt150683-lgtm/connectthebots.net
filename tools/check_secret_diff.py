"""Reject likely credentials in added Git diff lines without printing their values.

This bounded check complements review; it is not a complete secret detector.
Synthetic test strings must be assembled from fragments rather than accepted by
an allowlist that could accidentally exempt a real credential.
"""
from __future__ import annotations

import argparse
import re
import subprocess


PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bctb_[A-Z2-7]{52}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"https://sm\.hetrixtools\.net/hb/\?s=[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)^\+\s*(?:SESSION_SECRET|GOOGLE_CLIENT_SECRET|SMTP_PASSWORD|BACKUP_ENCRYPTION_KEY|OFFSITE_B2_KEY_ID|OFFSITE_B2_APPLICATION_KEY)\s*=\s*[\"']?[A-Za-z0-9_+/=-]{24,}"),
)


def findings(diff: str) -> list[tuple[str, int]]:
    path, line = "unknown", 0
    found = []
    for text in diff.splitlines():
        if text.startswith("+++ b/"):
            path = text[6:]
        elif text.startswith("@@"):
            match = re.search(r"\+(\d+)", text)
            line = int(match.group(1)) if match else 0
        elif text.startswith("+") and not text.startswith("+++"):
            if any(pattern.search(text) for pattern in PATTERNS):
                found.append((path, line))
            line += 1
        elif text.startswith(" "):
            line += 1
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="HEAD")
    args = parser.parse_args()
    # Resolve before passing the revision to diff; no shell interpolation.
    base = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.base + "^{commit}"], text=True
    ).strip()
    diff = subprocess.check_output(
        ["git", "diff", "--no-ext-diff", "--unified=0", base, "--"],
        text=True, encoding="utf-8",
    )
    found = findings(diff)
    for path, line in found:
        print(f"Possible credential in {path}:{line}; value withheld")
    if not found:
        print("No likely credentials found in added diff lines (bounded pattern check).")
    return bool(found)


if __name__ == "__main__":
    raise SystemExit(main())
