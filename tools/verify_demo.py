"""Verify the frozen R0 demo without changing its source or recorded evidence.

The original R0 writer uses native text newlines. A Windows run therefore has
CRLF bytes even though its JSON and manifest are identical to the Linux record.
This helper reports that distinction, compares all bytes after only CRLF -> LF,
and writes a separate LF copy. It never rewrites the raw output or the golden
record, and a changed value, manifest or formatting still fails verification.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def compare_demo(actual: bytes, expected: bytes) -> dict[str, Any]:
    """Only platform newline differences are tolerated; the manifest is exact."""
    actual_record = json.loads(actual)
    expected_record = json.loads(expected)
    if not isinstance(actual_record, dict) or not isinstance(expected_record, dict):
        raise ValueError("Both demo records must be JSON objects")
    if "manifest" not in actual_record or "manifest" not in expected_record:
        raise ValueError("Both demo records must contain a manifest")
    result = {
        "raw_bytes_equal": actual == expected,
        "lf_bytes_equal": actual.replace(b"\r\n", b"\n") == expected.replace(b"\r\n", b"\n"),
        "manifest_equal": actual_record["manifest"] == expected_record["manifest"],
        "semantic_equal": actual_record == expected_record,
    }
    result["passed"] = all(result[key] for key in ("lf_bytes_equal", "manifest_equal", "semantic_equal"))
    return result


def main(argv: list[str] | None = None) -> int:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=repository / "artifacts" / "reproducibility")
    args = parser.parse_args(argv)
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, str(repository / "run_agentmatch.py"), "demo", "--out", str(output / "demo")]
    completed = subprocess.run(command, cwd=repository, env=environment, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120)
    (output / "command.log").write_bytes((completed.stdout + completed.stderr).encode("utf-8"))
    if completed.returncode:
        print("Demo generation failed. See " + str(output / "command.log"), file=sys.stderr)
        return completed.returncode
    actual = (output / "demo" / "demo.json").read_bytes()
    expected = (repository / "examples" / "r0" / "demo" / "demo.json").read_bytes()
    result = compare_demo(actual, expected)
    (output / "canonical-demo.json").write_bytes(actual.replace(b"\r\n", b"\n"))
    (output / "verification.json").write_bytes((json.dumps(result, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
