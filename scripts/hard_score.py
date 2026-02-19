"""R1 score pipeline: each model independently reviews each bug-introducing PR.

Each model runs as a single Magpie reviewer with -r 1, producing one independent
review per model per PR. Checkpoint granularity: per model per PR.

Results go to results/r1/<pr-id>/<model-id>.json
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from scripts.common import (
    RESULTS_DIR,
    checkout_milvus_to_pr,
    generate_magpie_config,
    load_json,
    print_phase_end,
    print_phase_start,
    print_progress,
    result_exists,
    run_magpie,
    save_json,
)


def run_hard_score(config, manifest, pr_filter=None, model_filter=None, force=False, no_context=False):
    """Run R1 score pipeline.

    For each hard PR × each model:
      1. Generate Magpie config with single reviewer
      2. Run magpie review with -r 1
      3. Extract review and save to results/r1/<pr-id>/<model-id>.json

    If no_context=True, results go to results/r1_nocontext/ instead.
    """
    result_subdir = "r1_nocontext" if no_context else "r1"
    phase_label = "R1-noctx" if no_context else "R1"

    hard_prs = [p for p in manifest["prs"] if p["category"] == "hard"]
    models = config["models"]
    if model_filter:
        models = [m for m in models if m["id"] == model_filter]
    concurrency = config["execution"]["concurrency"]

    if pr_filter:
        hard_prs = [p for p in hard_prs if p["id"] == pr_filter]

    # Build task list: one task per (PR, model) pair
    tasks = []
    skipped = 0
    for pr in hard_prs:
        for model in models:
            output_path = RESULTS_DIR / result_subdir / pr["id"] / f"{model['id']}.json"
            if not force and result_exists(output_path):
                print_progress(phase_label, 0, 0, pr["id"], model_id=model["id"], status="skipped")
                skipped += 1
                continue
            tasks.append((pr, model, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[{phase_label}] nothing to do ({skipped} skipped)")
        return

    print_phase_start(phase_label, total + skipped, concurrency)
    if skipped > 0:
        print(f"[{phase_label}] {skipped} result(s) exist, skipped")

    phase_start = time.time()

    def run_one(index, pr, model, output_path):
        """Run single model on one PR with Magpie -r 1."""
        print_progress(phase_label, index, total, pr["id"], model_id=model["id"], status="reviewing")
        start = time.time()

        # Anti-cheat: checkout milvus repo to PR's merge commit
        sha = checkout_milvus_to_pr(pr["url"])
        if sha:
            print(f"  [checkout] {pr['id']}: milvus repo at {sha[:12]}")

        # Generate config with single model as reviewer
        magpie_cfg, rounds = generate_magpie_config(
            models=[model],
            config=config,
            is_hard=True,
            no_context=no_context,
        )

        # Run Magpie
        combined_path = RESULTS_DIR / result_subdir / pr["id"] / f"_{model['id']}_combined.json"
        run_magpie(pr["url"], magpie_cfg, combined_path, rounds=rounds, skip_context=no_context)
        elapsed = time.time() - start

        if result_exists(combined_path):
            # Extract this model's review from combined output
            data = load_json(combined_path)
            provider = model["magpie_provider"]
            model_messages = [m for m in data.get("messages", []) if m.get("reviewerId") == provider]
            model_summaries = [s for s in data.get("summaries", []) if s.get("reviewerId") == provider]
            model_issues = [i for i in data.get("parsedIssues", []) if provider in i.get("raisedBy", [])]

            model_result = {
                "prNumber": data.get("prNumber", ""),
                "analysis": data.get("analysis", ""),
                "messages": model_messages,
                "summaries": model_summaries,
                "finalConclusion": data.get("finalConclusion", ""),
                "parsedIssues": model_issues,
                "tokenUsage": [t for t in data.get("tokenUsage", []) if t.get("reviewerId") == provider],
                "mode": result_subdir,
            }
            save_json(output_path, model_result)

            # Validate: check we got actual review content
            if model_messages and len(model_messages[0].get("content", "")) > 100:
                print_progress(phase_label, index, total, pr["id"], model_id=model["id"], status="done", elapsed=elapsed)
            else:
                print_progress(phase_label, index, total, pr["id"], model_id=model["id"],
                               status=f"done (warning: short review {len(model_messages[0].get('content', '') if model_messages else '')} chars)",
                               elapsed=elapsed)
        else:
            print_progress(phase_label, index, total, pr["id"], model_id=model["id"], status="failed", elapsed=elapsed)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (pr, model, output_path) in enumerate(tasks, 1):
            future = pool.submit(run_one, i, pr, model, output_path)
            futures[future] = (i, pr, model)

        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                i, pr, model = futures[future]
                print(f"  [ERROR] {pr['id']} × {model['id']}: {exc}")

    print_phase_end(phase_label, total, time.time() - phase_start)
