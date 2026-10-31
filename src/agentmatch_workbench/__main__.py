"""``python -m agentmatch_workbench --ui-dir workbench/ui``"""
from .adapters.http_server import main

raise SystemExit(main())
