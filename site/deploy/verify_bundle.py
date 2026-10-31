"""Verify exactly one application wheel and a hashed dependency lock."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re


def verify(directory: Path) -> tuple[Path, str]:
    wheels = list(directory.glob("agentmatch-*-py3-none-any.whl"))
    if len(wheels) != 1 or not re.fullmatch(r"agentmatch-[A-Za-z0-9_.+]+-py3-none-any\.whl", wheels[0].name):
        raise ValueError("Bundle must contain exactly one built agentmatch wheel")
    expected = {wheels[0].name, "requirements-server.lock"}
    entries: dict[str, str] = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.+-]+)", line)
        if match is None or match[2] not in expected or match[2] in entries:
            raise ValueError("Invalid bundle checksum manifest")
        entries[match[2]] = match[1]
    if set(entries) != expected:
        raise ValueError("Incomplete bundle checksum manifest")
    for name, digest in entries.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Bundle checksum mismatch")
    release = hashlib.sha256("".join(entries[name] for name in sorted(entries)).encode("ascii")).hexdigest()[:24]
    return wheels[0], release


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    try:
        wheel, release = verify(arguments.directory)
    except (OSError, ValueError):
        parser.exit(1, "Invalid application bundle or checksum manifest\n")
    print(wheel.name)
    print(release)
