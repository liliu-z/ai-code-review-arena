"""Debate pipeline: all models debate each PR in multi-round mode.

Results go to results/debate/<pr-id>/debate.json
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.common import (
    RESULTS_DIR,
    checkout_milvus_to_pr,
    generate_magpie_config,
    print_phase_end,
    print_phase_start,
    print_progress,
    result_exists,
    run_magpie,
)


def run_soft_score(config, manifest, pr_filter=None, force=False, no_context=False, model_filter=None):
    """Run soft score pipeline.

    For each PR (hard + soft, all participate):
      1. Generate Magpie config with all models as reviewers
      2. Run magpie review with multi-round debate
      3. Save result to results/debate/<pr-id>/debate.json

    If no_context=True, results go to results/debate_nocontext/ instead.
    If model_filter is a list, only those model IDs participate in the debate.
    """
    result_subdir = "debate_nocontext" if no_context else "debate"
    phase_label = "Debate-noctx" if no_context else "Debate"

    all_prs = manifest["prs"]
    models = config["models"]
    if model_filter:
        if isinstance(model_filter, str):
            model_filter = [model_filter]
        models = [m for m in models if m["id"] in model_filter]
    concurrency = config["execution"]["concurrency"]

    if pr_filter:
        all_prs = [p for p in all_prs if p["id"] == pr_filter]

    # Build task list
    tasks = []
    skipped = 0
    for pr in all_prs:
        output_path = RESULTS_DIR / result_subdir / pr["id"] / "debate.json"
        if not force and result_exists(output_path):
            print_progress(phase_label, 0, 0, pr["id"], status="skipped")
            skipped += 1
            continue
        tasks.append((pr, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[{phase_label}] nothing to do ({skipped} skipped)")
        return

    print_phase_start(phase_label, total + skipped, concurrency)
    if skipped > 0:
        print(f"[{phase_label}] {skipped} result(s) exist, skipped")

    phase_start = time.time()
    model_names = ", ".join(m["id"] for m in models)
    rounds = config["soft_score"]["rounds"]

    def run_one(index, pr, output_path):
        """Run a single soft score task."""
        print_progress(phase_label, index, total, pr["id"], status=f"debating ({model_names}, {rounds} rounds)")
        start = time.time()

        # Anti-cheat: checkout milvus repo to PR's merge commit
        sha = checkout_milvus_to_pr(pr["url"])
        if sha:
            print(f"  [checkout] {pr['id']}: milvus repo at {sha[:12]}")

        magpie_cfg, cfg_rounds = generate_magpie_config(
            models=models,
            config=config,
            is_hard=False,
            no_context=no_context,
        )

        run_magpie(pr["url"], magpie_cfg, output_path, rounds=cfg_rounds, skip_context=no_context)
        elapsed = time.time() - start

        if result_exists(output_path):
            print_progress(phase_label, index, total, pr["id"], status="done", elapsed=elapsed)
        else:
            print_progress(phase_label, index, total, pr["id"], status="failed", elapsed=elapsed)

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

    print_phase_end(phase_label, total, time.time() - phase_start)
