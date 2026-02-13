"""Hard score pipeline: each model independently reviews each bug-introducing PR."""

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


def run_hard_score(config, manifest, pr_filter=None, model_filter=None, force=False):
    """Run hard score pipeline.

    For each hard PR × each model:
      1. Generate Magpie config with only that model as reviewer
      2. Run magpie review with -r 1 (single round, independent)
      3. Save result to results/hard/<pr-id>/<model-id>.json
    """
    hard_prs = [p for p in manifest["prs"] if p["category"] == "hard"]
    models = config["models"]
    concurrency = config["execution"]["concurrency"]

    if pr_filter:
        hard_prs = [p for p in hard_prs if p["id"] == pr_filter]
    if model_filter:
        models = [m for m in models if m["id"] == model_filter]

    # Build task list
    tasks = []
    skipped = 0
    for pr in hard_prs:
        for model in models:
            output_path = RESULTS_DIR / "hard" / pr["id"] / f"{model['id']}.json"
            if not force and result_exists(output_path):
                print_progress("Hard", 0, 0, pr["id"], model["id"], "skipped")
                skipped += 1
                continue
            tasks.append((pr, model, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[Hard] nothing to do ({skipped} skipped)")
        return

    print_phase_start("Hard", total + skipped, concurrency)
    if skipped > 0:
        print(f"[Hard] {skipped} result(s) exist, skipped")

    phase_start = time.time()
    completed_count = 0

    def run_one(index, pr, model, output_path):
        """Run a single hard score task."""
        print_progress("Hard", index, total, pr["id"], model["id"], "started")
        start = time.time()

        # Generate config with only this model
        magpie_cfg = generate_magpie_config(
            models=[model],
            config=config,
            is_hard=True,
        )

        run_magpie(pr["url"], magpie_cfg, output_path)
        elapsed = time.time() - start

        if result_exists(output_path):
            print_progress("Hard", index, total, pr["id"], model["id"], "done", elapsed)
        else:
            print_progress("Hard", index, total, pr["id"], model["id"], "failed", elapsed)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (pr, model, output_path) in enumerate(tasks, 1):
            future = pool.submit(run_one, i, pr, model, output_path)
            futures[future] = (i, pr, model)

        for future in as_completed(futures):
            completed_count += 1
            exc = future.exception()
            if exc:
                i, pr, model = futures[future]
                print(f"  [ERROR] {pr['id']} × {model['id']}: {exc}")

    print_phase_end("Hard", total, time.time() - phase_start)
