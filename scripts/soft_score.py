"""Soft score pipeline: all models debate each PR in multi-round mode."""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.common import (
    RESULTS_DIR,
    generate_magpie_config,
    print_phase_end,
    print_phase_start,
    print_progress,
    result_exists,
    run_magpie,
)


def run_soft_score(config, manifest, pr_filter=None, force=False):
    """Run soft score pipeline.

    For each PR (hard + soft, all participate):
      1. Generate Magpie config with all models as reviewers
      2. Run magpie review with multi-round debate
      3. Save result to results/soft/<pr-id>/debate.json
    """
    all_prs = manifest["prs"]
    models = config["models"]
    concurrency = config["execution"]["concurrency"]

    if pr_filter:
        all_prs = [p for p in all_prs if p["id"] == pr_filter]

    # Build task list
    tasks = []
    skipped = 0
    for pr in all_prs:
        output_path = RESULTS_DIR / "soft" / pr["id"] / "debate.json"
        if not force and result_exists(output_path):
            print_progress("软分", 0, 0, pr["id"], status="跳过")
            skipped += 1
            continue
        tasks.append((pr, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[软分] 无需执行 ({skipped} 个已跳过)")
        return

    print_phase_start("软分", total + skipped, concurrency)
    if skipped > 0:
        print(f"[软分] 其中 {skipped} 个已有结果，跳过")

    phase_start = time.time()
    model_names = ", ".join(m["id"] for m in models)
    rounds = config["soft_score"]["rounds"]

    def run_one(index, pr, output_path):
        """Run a single soft score task."""
        print_progress("软分", index, total, pr["id"], status=f"辩论中 ({model_names}, {rounds}轮)")
        start = time.time()

        magpie_cfg = generate_magpie_config(
            models=models,
            config=config,
            is_hard=False,
        )

        run_magpie(pr["url"], magpie_cfg, output_path)
        elapsed = time.time() - start

        if result_exists(output_path):
            print_progress("软分", index, total, pr["id"], status="完成", elapsed=elapsed)
        else:
            print_progress("软分", index, total, pr["id"], status="失败", elapsed=elapsed)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (pr, output_path) in enumerate(tasks, 1):
            future = pool.submit(run_one, i, pr, output_path)
            futures[future] = (i, pr)

        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                i, pr = futures[future]
                print(f"  [错误] {pr['id']}: {exc}")

    print_phase_end("软分", total, time.time() - phase_start)
