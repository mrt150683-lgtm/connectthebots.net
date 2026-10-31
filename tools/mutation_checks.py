"""Two deliberate regressions, in disposable copies, test independent gates."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run() -> dict:
    repository = Path(__file__).resolve().parents[1]
    cases = (
        ("hard_veto_removed", "scoring.py", "    if failures:\n", "    if False and failures:\n",
         "tests.test_matching.MatchingTests.test_T_MATCH_002_hard_failure_overrides_score"),
        ("audience_check_removed", "consent.py", "    if peer != grant.peer_id or purpose != grant.purpose",
         "    if False or purpose != grant.purpose",
         "tests.test_consent.ConsentTests.test_T_PRIV_002_exact_audience_purpose_and_question"),
    )
    results = []
    for name, module, before, after, test in cases:
        with tempfile.TemporaryDirectory(prefix="agentmatch-mutation-") as temporary:
            root = Path(temporary)
            shutil.copytree(repository / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copytree(repository / "tests", root / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            path = root / "src" / "agentmatch" / module
            original = path.read_text()
            if original.count(before) != 1:
                raise RuntimeError(f"Mutation anchor changed: {name}; review this check explicitly")
            path.write_text(original.replace(before, after, 1))
            environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(root / "src"),
                           "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
            completed = subprocess.run([sys.executable, "-m", "unittest", test, "-v"], cwd=root,
                                       env=environment, text=True, capture_output=True, timeout=30)
            detail = completed.stdout + completed.stderr
            killed = completed.returncode != 0 and "AssertionError" in detail and "FAILED" in detail
            results.append({"mutation": name, "test": test, "detected": killed,
                            "exit_code": completed.returncode, "output": detail})
    return {"scope": "two selected deliberate R0 regressions, not comprehensive mutation coverage",
            "all_detected": all(item["detected"] for item in results), "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"all_detected": result["all_detected"], "mutations": len(result["results"])}))
    raise SystemExit(0 if result["all_detected"] else 1)
