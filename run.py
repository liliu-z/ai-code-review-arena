#!/usr/bin/env python3
"""AI Code Review Arena - One-click evaluation runner."""

import argparse
import sys
import time

from scripts.common import load_config, load_manifest
from scripts.hard_score import run_hard_score
from scripts.soft_score import run_soft_score
from scripts.judge import run_hard_judge, run_soft_judge
from scripts.report import run_report


def main():
    parser = argparse.ArgumentParser(
        description="AI Code Review Arena: Multi-AI model code review arena",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                    # Run full pipeline
  python run.py --hard             # Run hard score only (independent review)
  python run.py --soft             # Run soft score only (debate)
  python run.py --judge            # Run judge only (scoring)
  python run.py --report           # Generate report only
  python run.py --pr pr-33820      # Run specific PR only
  python run.py --model claude     # Run specific model only
  python run.py --force            # Force re-run (ignore existing results)
        """,
    )
    parser.add_argument("--hard", action="store_true", help="Run hard score only (independent review per model)")
    parser.add_argument("--soft", action="store_true", help="Run soft score only (all models debate)")
    parser.add_argument("--judge", action="store_true", help="Run judge only (hard verdict + soft scoring)")
    parser.add_argument("--report", action="store_true", help="Generate report only")
    parser.add_argument("--pr", help="Run specific PR only (e.g. pr-33820)")
    parser.add_argument("--model", help="Run specific model only (e.g. claude)")
    parser.add_argument("--force", action="store_true", help="Force re-run, ignore existing results")

    args = parser.parse_args()

    config = load_config()
    manifest = load_manifest()

    # Validate
    model_ids = [m["id"] for m in config["models"]]
    pr_ids = [p["id"] for p in manifest["prs"]]

    if args.model and args.model not in model_ids:
        print(f"ERROR: model '{args.model}' not in config. Available: {', '.join(model_ids)}")
        sys.exit(1)

    if args.pr and args.pr not in pr_ids:
        print(f"ERROR: PR '{args.pr}' not in manifest. Available: {', '.join(pr_ids)}")
        sys.exit(1)

    run_all = not (args.hard or args.soft or args.judge or args.report)

    print("=" * 60)
    print("AI Code Review Arena")
    print(f"Models: {', '.join(model_ids)}")
    print(f"PR count: {len(pr_ids)} ({sum(1 for p in manifest['prs'] if p['category'] == 'hard')} hard + {sum(1 for p in manifest['prs'] if p['category'] == 'soft')} soft)")
    print(f"Concurrency: {config['execution']['concurrency']}")
    if args.pr:
        print(f"Filter PR: {args.pr}")
    if args.model:
        print(f"Filter model: {args.model}")
    if args.force:
        print("Mode: force re-run")
    print("=" * 60)

    total_start = time.time()

    if run_all or args.hard:
        run_hard_score(config, manifest, args.pr, args.model, args.force)

    if run_all or args.soft:
        run_soft_score(config, manifest, args.pr, args.force)

    if run_all or args.judge:
        run_hard_judge(config, manifest, args.force)
        run_soft_judge(config, manifest, args.force)

    if run_all or args.report:
        run_report(config, manifest)

    total_elapsed = time.time() - total_start
    minutes = int(total_elapsed // 60)
    seconds = int(total_elapsed % 60)
    print(f"\n{'='*60}")
    print(f"All done! Total elapsed: {minutes}m{seconds:02d}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
