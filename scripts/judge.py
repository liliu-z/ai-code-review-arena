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
    extract_first_round_reviews,
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
    strip_model_names,
)

# Review modes for hard judging
REVIEW_MODES = ["raw", "r1", "r1_nocontext", "debate", "debate_nocontext"]

# Debate modes produce a single collaborative result (not per-model)
DEBATE_MODES = {"debate", "debate_nocontext"}


def _get_review_path(mode, pr_id, model_id=None):
    """Get the review file path for a given mode/pr/model."""
    if mode == "raw":
        return RESULTS_DIR / "raw" / pr_id / f"{model_id}.json"
    elif mode == "r1":
        return RESULTS_DIR / "r1" / pr_id / f"{model_id}.json"
    elif mode == "r1_nocontext":
        return RESULTS_DIR / "r1_nocontext" / pr_id / f"{model_id}.json"
    elif mode == "debate":
        return RESULTS_DIR / "debate" / pr_id / "debate.json"
    elif mode == "debate_nocontext":
        return RESULTS_DIR / "debate_nocontext" / pr_id / "debate.json"
    raise ValueError(f"Unknown mode: {mode}")


def _get_debate_full_content(mode, pr_id):
    """Extract the FULL debate content as one unit (all messages + conclusion).

    For debate modes, the entire collaborative discussion is judged as a whole,
    not per-model. This avoids double-counting and misattribution.
    """
    debate_path = _get_review_path(mode, pr_id)
    if not debate_path.exists():
        return None
    return extract_review_content(debate_path)


def _get_individual_review_content(mode, pr_id, model_id):
    """Extract individual model review content for non-debate modes."""
    path = _get_review_path(mode, pr_id, model_id)
    if not path.exists():
        return None
    return extract_review_content(path)


def run_hard_judge(config, manifest, force=False):
    """Run hard score judging across all review modes.

    Uses Claude as the sole judge (no majority vote).

    For raw/r1/r1_nocontext: each model's review is judged individually.
    For debate/debate_nocontext: the entire debate is judged as ONE unit.
    """
    hard_prs = [p for p in manifest["prs"] if p["category"] == "hard"]
    models = config["models"]
    concurrency = config["execution"]["concurrency"]
    prompt_template = load_prompt("hard_judge.txt")

    # Find claude model config (sole judge)
    claude_model = None
    for m in models:
        if m["id"] == "claude":
            claude_model = m
            break
    if not claude_model:
        print("[Judge-Hard] ERROR: claude model not found in config")
        return

    # Build task list
    tasks = []
    skipped = 0

    for mode in REVIEW_MODES:
        for pr in hard_prs:
            for bug in pr.get("known_bugs", []):
                if mode in DEBATE_MODES:
                    # Debate: judge as one unit
                    debate_path = _get_review_path(mode, pr["id"])
                    if not debate_path.exists():
                        continue
                    output_path = (
                        RESULTS_DIR / "judge" / mode / pr["id"]
                        / f"debate_bug_{bug['id']}_by_claude.json"
                    )
                    if not force and result_exists(output_path):
                        skipped += 1
                        continue
                    tasks.append((mode, pr, bug, None, output_path))
                else:
                    # Per-model modes: judge each model's review
                    for reviewed_model in models:
                        review_path = _get_review_path(mode, pr["id"], reviewed_model["id"])
                        if not review_path.exists():
                            continue
                        output_path = (
                            RESULTS_DIR / "judge" / mode / pr["id"]
                            / f"{reviewed_model['id']}_bug_{bug['id']}_by_claude.json"
                        )
                        if not force and result_exists(output_path):
                            skipped += 1
                            continue
                        tasks.append((mode, pr, bug, reviewed_model, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[Judge-Hard] nothing to do ({skipped} skipped)")
        return

    print_phase_start("Judge-Hard", total + skipped, concurrency)
    if skipped > 0:
        print(f"[Judge-Hard] {skipped} result(s) exist, skipped")

    phase_start = time.time()

    def run_one(index, mode, pr, bug, reviewed_model, output_path):
        if mode in DEBATE_MODES:
            review_content = _get_debate_full_content(mode, pr["id"])
            reviewed_id = "debate"
        else:
            review_content = _get_individual_review_content(mode, pr["id"], reviewed_model["id"])
            reviewed_id = reviewed_model["id"]

        if not review_content:
            return None

        prompt = prompt_template.format(
            bug_description=bug["description"],
            review_content=review_content,
        )
        response = run_judge(claude_model, prompt)
        result = parse_judge_json(response)
        result["mode"] = mode
        result["pr_id"] = pr["id"]
        result["bug_id"] = bug["id"]
        result["reviewed_model"] = reviewed_id
        result["judge_model"] = "claude"
        save_json(output_path, result)
        return result

    verdicts = {}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (mode, pr, bug, reviewed_model, output_path) in enumerate(tasks, 1):
            future = pool.submit(run_one, i, mode, pr, bug, reviewed_model, output_path)
            futures[future] = (i, mode, pr, bug, reviewed_model)

        for future in as_completed(futures):
            i, mode, pr, bug, reviewed_model = futures[future]
            exc = future.exception()
            if exc:
                reviewed_id = "debate" if mode in DEBATE_MODES else reviewed_model["id"]
                print(f"  [ERROR] {mode}/{pr['id']}: {reviewed_id}: {exc}")
                continue

            result = future.result()
            if result is None:
                continue
            verdict = result.get("verdict", "UNKNOWN")
            reviewed_id = result["reviewed_model"]
            print(f"[Judge-Hard] [{i}/{total}] {mode}/{pr['id']}/{reviewed_id} ... {verdict}")

            key = f"{mode}/{pr['id']}/{bug['id']}/{reviewed_id}"
            verdicts[key] = {
                "mode": mode,
                "found": verdict.upper() == "YES",
                "verdict": verdict,
                "confidence": result.get("confidence", ""),
                "reasoning": result.get("reasoning", ""),
            }

    # Also load existing results that were skipped
    for mode in REVIEW_MODES:
        for pr in hard_prs:
            for bug in pr.get("known_bugs", []):
                if mode in DEBATE_MODES:
                    key = f"{mode}/{pr['id']}/{bug['id']}/debate"
                    if key in verdicts:
                        continue
                    output_path = (
                        RESULTS_DIR / "judge" / mode / pr["id"]
                        / f"debate_bug_{bug['id']}_by_claude.json"
                    )
                    if result_exists(output_path):
                        data = load_json(output_path)
                        v = data.get("verdict", "UNKNOWN")
                        verdicts[key] = {
                            "mode": mode,
                            "found": v.upper() == "YES",
                            "verdict": v,
                            "confidence": data.get("confidence", ""),
                            "reasoning": data.get("reasoning", ""),
                        }
                else:
                    for reviewed_model in models:
                        key = f"{mode}/{pr['id']}/{bug['id']}/{reviewed_model['id']}"
                        if key in verdicts:
                            continue
                        output_path = (
                            RESULTS_DIR / "judge" / mode / pr["id"]
                            / f"{reviewed_model['id']}_bug_{bug['id']}_by_claude.json"
                        )
                        if result_exists(output_path):
                            data = load_json(output_path)
                            v = data.get("verdict", "UNKNOWN")
                            verdicts[key] = {
                                "mode": mode,
                                "found": v.upper() == "YES",
                                "verdict": v,
                                "confidence": data.get("confidence", ""),
                                "reasoning": data.get("reasoning", ""),
                            }

    # Print results
    print(f"\n[Judge-Hard] === Final Verdicts ===")
    for key in sorted(verdicts.keys()):
        v = verdicts[key]
        found_str = "YES" if v["found"] else "NO"
        print(f"  {key}: {found_str} ({v.get('confidence', '')})")

    # Save verdicts
    verdicts_path = RESULTS_DIR / "judge" / "verdicts.json"
    save_json(verdicts_path, verdicts)

    print_phase_end("Judge-Hard", total, time.time() - phase_start)


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
        debate_path = RESULTS_DIR / "debate" / pr["id"] / "debate.json"
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
        print(f"[Judge-Soft] nothing to do ({skipped} skipped)")
        return

    print_phase_start("Judge-Soft", total + skipped, concurrency)
    if skipped > 0:
        print(f"[Judge-Soft] {skipped} result(s) exist, skipped")

    phase_start = time.time()

    # Collect all known model/provider names for stripping
    all_model_names = []
    for m in models:
        all_model_names.append(m["id"])
        all_model_names.append(m["magpie_provider"])
    # Deduplicate and sort by length (longest first) to avoid partial matches
    all_model_names = sorted(set(all_model_names), key=len, reverse=True)

    # Pre-compute anonymous mappings per PR (consistent for all judges on same PR)
    pr_mappings = {}
    for pr in all_prs:
        debate_path = RESULTS_DIR / "debate" / pr["id"] / "debate.json"
        if not debate_path.exists():
            continue
        # Use first-round only reviews to avoid debate cross-references
        reviews = extract_first_round_reviews(debate_path)
        # Strip model names from review content to improve anonymization
        cleaned_reviews = {}
        for model_id, text in reviews.items():
            cleaned_reviews[model_id] = strip_model_names(text, all_model_names)
        model_ids = list(cleaned_reviews.keys())
        mapping, reverse = _create_anonymous_mapping(model_ids)
        pr_mappings[pr["id"]] = {
            "reviews": cleaned_reviews,
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
                print(f"  [ERROR] {pr['id']} -> judge {judge_model['id']}: {exc}")
                continue

            result = future.result()
            if result and "scores" in result:
                print(f"[Judge-Soft] [{i}/{total}] {pr['id']} -> judge {judge_model['id']} ...")
                for reviewer_label, scores in result["scores"].items():
                    dim_strs = []
                    for d in dimensions:
                        val = scores.get(d["id"], "?")
                        dim_strs.append(f"{d['name']}={val}")
                    print(f"  {reviewer_label}: {', '.join(dim_strs)}")

    print_phase_end("Judge-Soft", total, time.time() - phase_start)
