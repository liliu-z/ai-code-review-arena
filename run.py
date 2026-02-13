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
        description="AI Code Review Arena: 多 AI 模型代码审查竞技场",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run.py                    # 跑全部流程
  python run.py --hard             # 只跑硬分 (独立 review)
  python run.py --soft             # 只跑软分 (辩论)
  python run.py --judge            # 只跑裁判 (打分)
  python run.py --report           # 只生成报告
  python run.py --pr pr-33820      # 只跑指定 PR
  python run.py --model claude     # 只跑指定模型
  python run.py --force            # 强制重跑 (忽略已有结果)
        """,
    )
    parser.add_argument("--hard", action="store_true", help="只跑硬分 (每模型独立 review)")
    parser.add_argument("--soft", action="store_true", help="只跑软分 (全模型辩论)")
    parser.add_argument("--judge", action="store_true", help="只跑裁判 (硬分判定 + 软分打分)")
    parser.add_argument("--report", action="store_true", help="只生成报告")
    parser.add_argument("--pr", help="只跑指定 PR (如 pr-33820)")
    parser.add_argument("--model", help="只跑指定模型 (如 claude)")
    parser.add_argument("--force", action="store_true", help="强制重跑，忽略已有结果")

    args = parser.parse_args()

    config = load_config()
    manifest = load_manifest()

    # Validate
    model_ids = [m["id"] for m in config["models"]]
    pr_ids = [p["id"] for p in manifest["prs"]]

    if args.model and args.model not in model_ids:
        print(f"错误: 模型 '{args.model}' 不在配置中。可用: {', '.join(model_ids)}")
        sys.exit(1)

    if args.pr and args.pr not in pr_ids:
        print(f"错误: PR '{args.pr}' 不在 manifest 中。可用: {', '.join(pr_ids)}")
        sys.exit(1)

    run_all = not (args.hard or args.soft or args.judge or args.report)

    print("=" * 60)
    print("AI Code Review Arena")
    print(f"模型: {', '.join(model_ids)}")
    print(f"PR 数量: {len(pr_ids)} ({sum(1 for p in manifest['prs'] if p['category'] == 'hard')} 硬分 + {sum(1 for p in manifest['prs'] if p['category'] == 'soft')} 软分)")
    print(f"并发: {config['execution']['concurrency']}")
    if args.pr:
        print(f"筛选 PR: {args.pr}")
    if args.model:
        print(f"筛选模型: {args.model}")
    if args.force:
        print("模式: 强制重跑")
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
    print(f"全部完成! 总耗时: {minutes}m{seconds:02d}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
