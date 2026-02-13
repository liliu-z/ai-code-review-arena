"""Judge pipeline: hard score bug detection + soft score quality rating."""

import json
import random
import string
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.common import (
    RESULTS_DIR,
    extract_review_content,
    extract_reviews_by_model,
    load_prompt,
    parse_judge_json,
    print_phase_end,
    print_phase_start,
    print_progress,
    result_exists,
    run_judge,
    save_json,
    load_json,
)


def run_hard_judge(config, manifest, force=False):
    """Run hard score judging.

    For each hard PR × each reviewed model:
      - All models judge: did the review find the known bug? (YES/NO)
      - Majority vote determines final verdict
    """
    hard_prs = [p for p in manifest["prs"] if p["category"] == "hard"]
    models = config["models"]
    concurrency = config["execution"]["concurrency"]
    prompt_template = load_prompt("hard_judge.txt")

    # Build task list: (pr, reviewed_model, bug, judge_model)
    tasks = []
    skipped = 0
    for pr in hard_prs:
        for bug in pr.get("known_bugs", []):
            for reviewed_model in models:
                review_path = RESULTS_DIR / "hard" / pr["id"] / f"{reviewed_model['id']}.json"
                if not review_path.exists():
                    continue
                for judge_model in models:
                    output_path = (
                        RESULTS_DIR / "judge" / "hard" / pr["id"]
                        / f"{reviewed_model['id']}_bug_{bug['id']}_by_{judge_model['id']}.json"
                    )
                    if not force and result_exists(output_path):
                        skipped += 1
                        continue
                    tasks.append((pr, bug, reviewed_model, judge_model, review_path, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[裁判-硬分] 无需执行 ({skipped} 个已跳过)")
        return

    print_phase_start("裁判-硬分", total + skipped, concurrency)
    if skipped > 0:
        print(f"[裁判-硬分] 其中 {skipped} 个已有结果，跳过")

    phase_start = time.time()

    def run_one(index, pr, bug, reviewed_model, judge_model, review_path, output_path):
        review_content = extract_review_content(review_path)
        prompt = prompt_template.format(
            bug_description=bug["description"],
            review_content=review_content,
        )
        response = run_judge(judge_model, prompt)
        result = parse_judge_json(response)
        result["pr_id"] = pr["id"]
        result["bug_id"] = bug["id"]
        result["reviewed_model"] = reviewed_model["id"]
        result["judge_model"] = judge_model["id"]
        save_json(output_path, result)
        return result

    results_by_key = {}  # (pr_id, bug_id, reviewed_model) -> [(judge_id, verdict)]

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (pr, bug, reviewed_model, judge_model, review_path, output_path) in enumerate(tasks, 1):
            future = pool.submit(
                run_one, i, pr, bug, reviewed_model, judge_model, review_path, output_path
            )
            futures[future] = (i, pr, bug, reviewed_model, judge_model)

        for future in as_completed(futures):
            i, pr, bug, reviewed_model, judge_model = futures[future]
            exc = future.exception()
            if exc:
                print(f"  [错误] {pr['id']}: {reviewed_model['id']} by {judge_model['id']}: {exc}")
                continue

            result = future.result()
            verdict = result.get("verdict", "UNKNOWN")
            print(
                f"[裁判-硬分] [{i}/{total}] {pr['id']}: "
                f"{reviewed_model['id']}的review → 裁判{judge_model['id']} ... {verdict}"
            )

            key = (pr["id"], bug["id"], reviewed_model["id"])
            if key not in results_by_key:
                results_by_key[key] = []
            results_by_key[key].append((judge_model["id"], verdict))

    # Print majority vote results
    print(f"\n[裁判-硬分] === 最终判定 ===")
    for (pr_id, bug_id, model_id), votes in sorted(results_by_key.items()):
        yes_count = sum(1 for _, v in votes if v.upper() == "YES")
        total_votes = len(votes)
        final = "YES" if yes_count > total_votes / 2 else "NO"
        vote_detail = ", ".join(f"{j}={v}" for j, v in votes)
        print(f"  {pr_id} × {model_id} (bug: {bug_id}): {final} ({yes_count}/{total_votes}) [{vote_detail}]")

    # Save aggregated verdicts
    verdicts_path = RESULTS_DIR / "judge" / "hard" / "verdicts.json"
    verdicts = {}
    for (pr_id, bug_id, model_id), votes in results_by_key.items():
        yes_count = sum(1 for _, v in votes if v.upper() == "YES")
        key = f"{pr_id}/{bug_id}/{model_id}"
        verdicts[key] = {
            "found": yes_count > len(votes) / 2,
            "yes_count": yes_count,
            "total_votes": len(votes),
            "votes": {j: v for j, v in votes},
        }
    save_json(verdicts_path, verdicts)

    print_phase_end("裁判-硬分", total, time.time() - phase_start)


def _create_anonymous_mapping(model_ids):
    """Create random anonymous mapping: model_id -> 'Reviewer A/B/C/...'

    Returns:
        dict: {model_id: anonymous_label}
        dict: {anonymous_label: model_id} (reverse mapping)
    """
    labels = [f"Reviewer {chr(65 + i)}" for i in range(len(model_ids))]
    shuffled = list(model_ids)
    random.shuffle(shuffled)
    mapping = dict(zip(shuffled, labels))
    reverse_mapping = {v: k for k, v in mapping.items()}
    return mapping, reverse_mapping


def run_soft_judge(config, manifest, force=False):
    """Run soft score judging.

    For each PR's debate:
      1. Extract reviews by model
      2. Anonymize (random Reviewer A/B/C mapping, different per PR)
      3. Each model judges all reviewers on 4 dimensions (1-10)
    """
    all_prs = manifest["prs"]
    models = config["models"]
    concurrency = config["execution"]["concurrency"]
    prompt_template = load_prompt("soft_judge.txt")
    dimensions = config["judge"]["dimensions"]

    # Build task list
    tasks = []
    skipped = 0
    for pr in all_prs:
        debate_path = RESULTS_DIR / "soft" / pr["id"] / "debate.json"
        if not debate_path.exists():
            continue

        for judge_model in models:
            output_path = RESULTS_DIR / "judge" / "soft" / pr["id"] / f"{judge_model['id']}.json"
            if not force and result_exists(output_path):
                skipped += 1
                continue
            tasks.append((pr, judge_model, debate_path, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[裁判-软分] 无需执行 ({skipped} 个已跳过)")
        return

    print_phase_start("裁判-软分", total + skipped, concurrency)
    if skipped > 0:
        print(f"[裁判-软分] 其中 {skipped} 个已有结果，跳过")

    phase_start = time.time()

    # Pre-compute anonymous mappings per PR (consistent for all judges on same PR)
    pr_mappings = {}
    for pr in all_prs:
        debate_path = RESULTS_DIR / "soft" / pr["id"] / "debate.json"
        if not debate_path.exists():
            continue
        reviews = extract_reviews_by_model(debate_path)
        model_ids = list(reviews.keys())
        mapping, reverse = _create_anonymous_mapping(model_ids)
        pr_mappings[pr["id"]] = {
            "reviews": reviews,
            "mapping": mapping,
            "reverse": reverse,
        }
        # Save mapping for later de-anonymization
        mapping_path = RESULTS_DIR / "judge" / "soft" / pr["id"] / "mapping.json"
        save_json(mapping_path, {"mapping": mapping, "reverse": reverse})

    def run_one(index, pr, judge_model, output_path):
        pr_data = pr_mappings.get(pr["id"])
        if not pr_data:
            return None

        reviews = pr_data["reviews"]
        mapping = pr_data["mapping"]

        # Build anonymized reviews text
        anon_parts = []
        for model_id, label in sorted(mapping.items(), key=lambda x: x[1]):
            review = reviews.get(model_id, "(no review found)")
            anon_parts.append(f"### {label}\n\n{review}")
        anonymized_text = "\n\n---\n\n".join(anon_parts)

        # Build score template for JSON format hint
        labels = sorted(mapping.values())
        score_entries = []
        dim_keys = [d["id"] for d in dimensions]
        for label in labels:
            dims = ", ".join(f'"{d}": N' for d in dim_keys)
            score_entries.append(f'"{label}": {{{dims}}}')
        score_template = ", ".join(score_entries)

        prompt = prompt_template.format(
            pr_title=pr["title"],
            pr_url=pr["url"],
            anonymized_reviews=anonymized_text,
            score_template=score_template,
        )

        response = run_judge(judge_model, prompt)
        result = parse_judge_json(response)
        result["pr_id"] = pr["id"]
        result["judge_model"] = judge_model["id"]
        save_json(output_path, result)
        return result

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (pr, judge_model, debate_path, output_path) in enumerate(tasks, 1):
            future = pool.submit(run_one, i, pr, judge_model, output_path)
            futures[future] = (i, pr, judge_model)

        for future in as_completed(futures):
            i, pr, judge_model = futures[future]
            exc = future.exception()
            if exc:
                print(f"  [错误] {pr['id']} → 裁判{judge_model['id']}: {exc}")
                continue

            result = future.result()
            if result and "scores" in result:
                print(f"[裁判-软分] [{i}/{total}] {pr['id']} → 裁判{judge_model['id']} ...")
                for reviewer_label, scores in result["scores"].items():
                    dim_strs = []
                    for d in dimensions:
                        val = scores.get(d["id"], "?")
                        dim_strs.append(f"{d['name']}={val}")
                    print(f"  {reviewer_label}: {', '.join(dim_strs)}")

    print_phase_end("裁判-软分", total, time.time() - phase_start)
