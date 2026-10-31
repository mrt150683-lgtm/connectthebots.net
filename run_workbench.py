"""Run the fictional local collaboration workbench from a source checkout.

Build the interface once first:  (cd workbench/ui && npm ci && npm run build)
Then:                             python3 run_workbench.py
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from agentmatch_workbench.adapters.http_server import main

if __name__ == "__main__":
    arguments = sys.argv[1:]
    if not any(arg == "--ui-dir" or arg.startswith("--ui-dir=") for arg in arguments):
        arguments += ["--ui-dir", str(ROOT / "workbench" / "ui")]
    raise SystemExit(main(arguments))
