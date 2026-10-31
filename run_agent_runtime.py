"""Source-checkout entry point for the separate optional agent runtime."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from agentmatch_runtime.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
