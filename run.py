#!/usr/bin/env python3
"""AI Code Review Arena - One-click evaluation runner."""

import argparse
import sys
import time

from scripts.common import load_config, load_manifest
from scripts.raw_score import run_raw_score
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
  python run.py                    # Run full pipeline (raw + r1 + debate + judge + report)
  python run.py --raw              # Run raw reviews only (model CLI directly)
  python run.py --r1               # Run Magpie R1 only (independent review)
  python run.py --debate           # Run debate only (multi-round)
  python run.py --judge            # Run judge only (scoring)
  python run.py --report           # Generate report only
  python run.py --pr pr-33820      # Run specific PR only
  python run.py --model claude     # Run specific model only (raw + r1)
  python run.py --force            # Force re-run (ignore existing results)
        """,
    )
    parser.add_argument("--raw", action="store_true", help="Run raw score only (direct model CLI review)")
    parser.add_argument("--r1", action="store_true", help="Run R1 score only (Magpie single-round independent review)")
    parser.add_argument("--debate", action="store_true", help="Run debate only (Magpie multi-round debate)")
    parser.add_argument("--judge", action="store_true", help="Run judge only (hard verdict + soft scoring)")
    parser.add_argument("--report", action="store_true", help="Generate report only")
    parser.add_argument("--pr", help="Run specific PR only (e.g. pr-33820)")
    parser.add_argument("--model", help="Run specific model only (e.g. claude)")
    parser.add_argument("--force", action="store_true", help="Force re-run, ignore existing results")
    parser.add_argument("--no-context", action="store_true",
                        help="Run R1/debate without analyzer context injection (results stored in r1_nocontext/debate_nocontext)")

    args = parser.parse_args()

    config = load_config()
    manifest = load_manifest()

    # Validate
    model_ids = [m["id"] for m in config["models"]]
    pr_ids = [p["id"] for p in manifest["prs"]]

    if args.model:
        for m in args.model.split(","):
            if m not in model_ids:
                print(f"ERROR: model '{m}' not in config. Available: {', '.join(model_ids)}")
                sys.exit(1)

    if args.pr and args.pr not in pr_ids:
        print(f"ERROR: PR '{args.pr}' not in manifest. Available: {', '.join(pr_ids)}")
        sys.exit(1)

    run_all = not (args.raw or args.r1 or args.debate or args.judge or args.report)

    hard_pr_count = sum(1 for p in manifest['prs'] if p['category'] == 'hard')
    soft_pr_count = sum(1 for p in manifest['prs'] if p['category'] == 'soft')

    print("=" * 60)
    print("AI Code Review Arena")
    print(f"Models: {', '.join(model_ids)}")
    print(f"PR count: {len(pr_ids)} ({hard_pr_count} hard + {soft_pr_count} soft)")
    print(f"Data points per hard PR: {len(model_ids) * 2 + 1} (raw×{len(model_ids)} + r1×{len(model_ids)} + 1 debate)")
    print(f"Concurrency: {config['execution']['concurrency']}")
    if args.pr:
        print(f"Filter PR: {args.pr}")
    if args.model:
        print(f"Filter model: {args.model}")
    if args.force:
        print("Mode: force re-run")
    print("=" * 60)

    total_start = time.time()

    if run_all or args.raw:
        run_raw_score(config, manifest, args.pr, args.model, args.force)

    if run_all or args.r1:
        run_hard_score(config, manifest, args.pr, args.model, args.force,
                       no_context=getattr(args, 'no_context', False))

    if run_all or args.debate:
        # For debate, --model accepts comma-separated list of models to include
        debate_models = args.model.split(",") if args.model else None
        run_soft_score(config, manifest, args.pr, args.force,
                       no_context=getattr(args, 'no_context', False),
                       model_filter=debate_models)

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
