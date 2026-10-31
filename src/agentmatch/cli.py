"""Small standard-library CLI for bundled synthetic data only."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Sequence

from . import __version__
from .audit import json_text
from .config import load_defaults, selectivity


def parser() -> argparse.ArgumentParser:
    defaults = load_defaults()
    cli = argparse.ArgumentParser(prog="agentmatch", description="Offline synthetic AgentMatch reference. No real data, inference or network transport.")
    cli.add_argument("--version", action="version", version=f"AgentMatch {__version__} (R0 synthetic reference)")
    commands = cli.add_subparsers(dest="command", required=True)
    fixtures = commands.add_parser("fixtures", help="Verify or list the bundled fictional corpus")
    fixture_actions = fixtures.add_subparsers(dest="fixture_action", required=True)
    fixture_actions.add_parser("verify")
    fixture_actions.add_parser("list")
    demo = commands.add_parser("demo", help="Run the complete scripted journey and write JSON plus HTML")
    demo.add_argument("--seed", type=int, default=defaults["r0"]["default_seed"])
    demo.add_argument("--as-of", default=defaults["r0"]["default_as_of"], help="Timezone-aware frozen clock")
    demo.add_argument("--out", type=Path, default=Path("artifacts/r0/demo"))
    evaluation = commands.add_parser("evaluate", help="Evaluate every other fictional candidate")
    evaluation.add_argument("--owner", required=True, help="Use fixtures list to see IDs")
    evaluation.add_argument("--intent", required=True)
    evaluation.add_argument("--as-of", default=defaults["r0"]["default_as_of"])
    options = evaluation.add_mutually_exclusive_group()
    options.add_argument("--selectivity", choices=tuple(defaults["scoring_fixture_policy"]["selectivity_presets"]))
    options.add_argument("--fit-threshold", metavar="DECIMAL", help="Exact custom preference threshold from 0 through 1")
    evaluation.add_argument("--json", action="store_true", help="Include every exact component and local decision")
    simulation = commands.add_parser("simulate", help="Run a bundled fictional interview or social scenario")
    scenario = simulation.add_mutually_exclusive_group(required=True)
    scenario.add_argument("--scenario", metavar="ID")
    scenario.add_argument("--list", action="store_true")
    simulation.add_argument("--as-of", default=defaults["r0"]["default_as_of"])
    verification = commands.add_parser("verify", help="Execute the shipped model-free, transport-free test suite")
    verification.add_argument("--offline", action="store_true", required=True,
                              help="Suite selector; does not create network isolation")
    verification.add_argument("--out", type=Path, default=Path("artifacts/r0/verification"))
    verification.add_argument("--json", action="store_true")
    commands.add_parser("status", help="Show implemented and deferred capabilities")
    return cli


def _print(value: Any) -> None:
    print(json_text(value), end="")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"import", "interview", "contact", "dating", "serve", "server", "download-model", "connect"}:
        print("UNSUPPORTED_R0_CAPABILITY: this release accepts bundled fictional scenarios only. "
              "Use 'simulate --list'. No real input was opened or transmitted.", file=sys.stderr)
        return 2
    try:
        args = parser().parse_args(arguments)
        if args.command == "fixtures":
            from .fixtures import list_owners, verify
            _print(verify() if args.fixture_action == "verify" else {"synthetic_only": True, "owners": list_owners()})
        elif args.command == "demo":
            from .demo import build_demo, write_demo
            from .models import parse_time
            report = build_demo(as_of=parse_time(args.as_of), seed=args.seed)
            write_demo(report, args.out)
            print(f"AgentMatch {__version__} · R0 synthetic reference")
            print("40 fictional owners · 200 annotated conversations · no inference or networking")
            print(f"Scanned {report['evaluation']['scanned_candidates']} candidates for Lumen.")
            print(f"Selected fictional offers: {', '.join(report['evaluation']['offered_owner_ids']) or 'none'}")
            print(f"Open the report: {(args.out / 'demo.html').resolve()}")
            print(f"Full evidence: {(args.out / 'demo.json').resolve()}")
        elif args.command == "evaluate":
            from .fixtures import load_corpus
            from .models import parse_time
            from .ranking import evaluate_owner
            corpus = load_corpus()
            owner = corpus.owner(args.owner)
            if args.intent != owner.intent.intent_id:
                raise ValueError("UNKNOWN_SYNTHETIC_INTENT: use the owner's exact fixture intent ID")
            threshold = selectivity(args.selectivity, args.fit_threshold) if args.selectivity is not None or args.fit_threshold is not None else None
            result = evaluate_owner(owner, corpus.owners, parse_time(args.as_of), threshold=threshold)
            if args.json:
                _print(result)
            else:
                print(f"{owner.alias} · {owner.intent.intent_id} · synthetic reference")
                print(f"Preference threshold {result['effective_threshold']['display']}; "
                      f"scanned {result['scanned_candidates']} candidates; {result['qualified_pairs']} reciprocal qualifications.")
                for row in result["ranked_candidates"]:
                    print(f"{row['rank']:>2}. {row['candidate_id']:<16} supported fit {row['reciprocal_supported_fit']['display']:<8} {row['selection']}")
                if not result["ranked_candidates"]:
                    print("No reciprocal qualifying candidate. No weak replacement was inserted.")
                counts: dict[str, int] = {}
                for pair in result["assessments"]:
                    key = pair["a_to_b"]["status"]
                    counts[key] = counts.get(key, 0) + 1
                print("Local decisions: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
                print("Use --json for exact F/S/C/U, every atom, and both private reference directions.")
                print("Scores are policy quantities, not compatibility probabilities.")
        elif args.command == "simulate":
            from .models import parse_time
            from .interview import scenario_names as interview_names, run_scenario as run_interview
            from .simulator import scenario_names, run_scenario
            if args.list:
                _print({"synthetic_only": True, "scenarios": list(interview_names()) + list(scenario_names())})
            elif args.scenario in interview_names():
                _print(run_interview(args.scenario, parse_time(args.as_of)))
            else:
                _print(run_scenario(args.scenario, parse_time(args.as_of)))
        elif args.command == "verify":
            from .verification import run_verification
            result = run_verification(args.out)
            if args.json:
                _print(result)
            else:
                print(f"{'PASS' if result['success'] else 'FAIL'}: {result['tests_run']} tests; "
                      f"{result['failures']} failures, {result['errors']} errors, {result['skipped']} skipped.")
                print(f"Evidence: {args.out.resolve()}")
                print("--offline selects the suite. Network enforcement requires the separate isolation harness.")
            return 0 if result["success"] else 1
        elif args.command == "status":
            _print({"version": __version__, "release": "R0", "synthetic_only": True,
                    "implemented": ["Exact reciprocal matching", "Claim lifecycle and disclosure projection",
                                    "Scripted interview and specific review", "Intro/context consent simulation",
                                    "In-memory queue/retry/block simulation", "Private coach routing simulation"],
                    "not_implemented": ["Real-history import", "Encrypted local vault", "Local models",
                                        "Passkeys", "WebRTC transport", "Discovery service", "Live social network"],
                    "runtime_dependencies": [], "servers_required": 0})
        return 0
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
