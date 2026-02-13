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
            print_progress("Soft", 0, 0, pr["id"], status="skipped")
            skipped += 1
            continue
        tasks.append((pr, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[Soft] nothing to do ({skipped} skipped)")
        return

    print_phase_start("Soft", total + skipped, concurrency)
    if skipped > 0:
        print(f"[Soft] {skipped} result(s) exist, skipped")

    phase_start = time.time()
    model_names = ", ".join(m["id"] for m in models)
    rounds = config["soft_score"]["rounds"]

    def run_one(index, pr, output_path):
        """Run a single soft score task."""
        print_progress("Soft", index, total, pr["id"], status=f"debating ({model_names}, {rounds} rounds)")
        start = time.time()

        magpie_cfg = generate_magpie_config(
            models=models,
            config=config,
            is_hard=False,
        )

        run_magpie(pr["url"], magpie_cfg, output_path)
        elapsed = time.time() - start

        if result_exists(output_path):
            print_progress("Soft", index, total, pr["id"], status="done", elapsed=elapsed)
        else:
            print_progress("Soft", index, total, pr["id"], status="failed", elapsed=elapsed)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (pr, output_path) in enumerate(tasks, 1):
            future = pool.submit(run_one, i, pr, output_path)
            futures[future] = (i, pr)

        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                i, pr = futures[future]
                print(f"  [ERROR] {pr['id']}: {exc}")

    print_phase_end("Soft", total, time.time() - phase_start)
