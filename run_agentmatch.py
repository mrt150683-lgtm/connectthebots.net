"""Run the source checkout using Python alone; no installation is necessary."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from agentmatch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

