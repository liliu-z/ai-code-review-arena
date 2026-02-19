"""Raw score pipeline: each model reviews each PR directly without Magpie framework.

Uses the same review_prompt as Magpie reviewers. Gives the model the PR URL
and lets it fetch the diff itself (same as Magpie does).
This measures the model's bare code review capability.

Anti-cheating measures:
- Checkout local milvus repo to PR's merge commit (no post-fix code visible)
- Prompt explicitly forbids browsing master, git checkout, or referencing fix PRs
- Post-review validation checks for cheating signals
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from scripts.common import (
    MILVUS_REPO,
    RESULTS_DIR,
    checkout_milvus_to_pr,
    get_pr_diff,
    get_pr_info,
    print_phase_end,
    print_phase_start,
    print_progress,
    result_exists,
    run_judge,
    save_json,
)

# Models that are API-only (no tool/web access) and need the diff included in the prompt
API_ONLY_MODELS = {"minimax"}

# Anti-cheating instructions appended to every raw review prompt
ANTI_CHEAT_PROMPT = """
IMPORTANT RULES — strict compliance required:
- You MUST only analyze the PR diff as submitted. Fetch the diff from the PR URL above.
- Do NOT run git checkout, git log, git blame, or any git commands on any repository.
- Do NOT browse the GitHub repository beyond the PR URL provided.
- Do NOT look at or reference the current main/master branch code.
- Do NOT reference any revert, fix, hotfix, or follow-up PRs.
- Do NOT mention whether this PR was later reverted or fixed.
- Base your review SOLELY on the code changes visible in the PR diff.
"""


def _validate_raw_review(content):
    """Check if raw review content is a real review, not an error message."""
    if not content or len(content) < 100:
        return False, "too short"
    # Check for common error patterns from models that can't access tools
    error_patterns = [
        "unable to access",
        "permission to access",
        "approve one of the pending",
        "need permission",
        "cannot retrieve",
        "I can't access",
    ]
    content_lower = content.lower()
    for pattern in error_patterns:
        if pattern.lower() in content_lower:
            return False, f"error message detected: '{pattern}'"
    return True, "ok"


def _detect_cheating(content):
    """Detect signs of cheating in a review (referencing post-merge info).

    Returns:
        tuple: (is_cheating: bool, signals: list of str)
    """
    signals = []
    content_lower = content.lower()

    # Check for references to revert/fix PRs
    import re
    # Pattern: "reverted in #NNN" or "fixed in #NNN" or "follow-up PR #NNN"
    revert_refs = re.findall(r'revert(?:ed|s)?\s+(?:in|by|via)\s+#\d+', content_lower)
    if revert_refs:
        signals.append(f"revert reference: {revert_refs}")

    fix_refs = re.findall(r'fix(?:ed|es)?\s+(?:in|by|via)\s+#\d+', content_lower)
    if fix_refs:
        signals.append(f"fix reference: {fix_refs}")

    followup_refs = re.findall(r'follow[- ]?up\s+(?:pr|pull request|commit)\s+#\d+', content_lower)
    if followup_refs:
        signals.append(f"follow-up reference: {followup_refs}")

    # Check for "current master" or "merged master" references
    master_refs = re.findall(r'(?:current|merged|latest)\s+(?:master|main)\s+(?:branch|version|code)', content_lower)
    if master_refs:
        signals.append(f"master branch reference: {master_refs}")

    # Check for "was later" or "subsequently" patterns suggesting post-merge knowledge
    post_merge = re.findall(r'(?:was later|subsequently|has since been|was eventually)\s+\w+', content_lower)
    if post_merge:
        signals.append(f"post-merge knowledge: {post_merge}")

    # Check for explicit "this was reverted" statements
    if "this pr was reverted" in content_lower or "this was reverted" in content_lower:
        signals.append("explicit revert statement")

    return len(signals) > 0, signals


def run_raw_score(config, manifest, pr_filter=None, model_filter=None, force=False):
    """Run raw score pipeline.

    For each hard PR × each model:
      1. Checkout local milvus repo to PR's merge commit
      2. Construct prompt: review_prompt + anti-cheat rules + PR URL
      3. Let model CLI fetch diff and review autonomously
      4. Validate review content is real (not error/permission message)
      5. Check for cheating signals
      6. Save to results/raw/<pr-id>/<model-id>.json
    """
    hard_prs = [p for p in manifest["prs"] if p["category"] == "hard"]
    models = config["models"]
    if model_filter:
        models = [m for m in models if m["id"] == model_filter]
    concurrency = config["execution"]["concurrency"]
    review_prompt = config.get("review_prompt", "You are a senior engineer reviewing this PR.")

    if pr_filter:
        hard_prs = [p for p in hard_prs if p["id"] == pr_filter]

    # Build task list: one task per (PR, model) pair
    tasks = []
    skipped = 0
    for pr in hard_prs:
        for model in models:
            output_path = RESULTS_DIR / "raw" / pr["id"] / f"{model['id']}.json"
            if not force and result_exists(output_path):
                print_progress("Raw", 0, 0, pr["id"], model_id=model["id"], status="skipped")
                skipped += 1
                continue
            tasks.append((pr, model, output_path))

    total = len(tasks)
    if total == 0:
        print(f"[Raw] nothing to do ({skipped} skipped)")
        return

    print_phase_start("Raw", total + skipped, concurrency)
    if skipped > 0:
        print(f"[Raw] {skipped} result(s) exist, skipped")

    phase_start = time.time()

    def run_one(index, pr, model, output_path):
        """Run a single raw review."""
        print_progress("Raw", index, total, pr["id"], model_id=model["id"], status="reviewing")
        start = time.time()

        # Step 1: Checkout milvus repo to PR's merge commit (prevents local code cheating)
        # The subprocess still runs from a temp dir, but if the model somehow
        # finds the milvus repo, it will see code at the right point in time.
        sha = checkout_milvus_to_pr(pr["url"])
        if sha:
            print(f"  [checkout] {pr['id']}: milvus repo at {sha[:12]}")

        # Step 2: Build prompt with anti-cheat rules
        if model["id"] in API_ONLY_MODELS:
            # API-only models can't fetch URLs — include diff directly
            diff = get_pr_diff(pr["url"])
            pr_info = get_pr_info(pr["url"])
            if not diff:
                print_progress("Raw", index, total, pr["id"], model_id=model["id"],
                               status="failed (could not fetch diff)", elapsed=time.time() - start)
                return
            prompt = f"""{review_prompt}

Please review this GitHub PR: {pr["url"]}
Title: {pr_info.get('title', '')}

Description:
{pr_info.get('body', '')[:2000]}

PR Diff:
{diff[:80000]}

Review this diff thoroughly. Focus on correctness, potential bugs, edge cases, and code quality issues.
{ANTI_CHEAT_PROMPT}"""
        else:
            # CLI-based models can fetch the diff themselves
            prompt = f"""{review_prompt}

Please review this GitHub PR: {pr["url"]}

Fetch the PR diff and review it thoroughly. Focus on correctness, potential bugs, edge cases, and code quality issues.
{ANTI_CHEAT_PROMPT}"""

        # Step 3: Run review (cwd=None -> clean temp dir)
        response = run_judge(model, prompt, timeout=1800)
        elapsed = time.time() - start

        if response:
            # Validate the review is real content
            valid, reason = _validate_raw_review(response)
            if not valid:
                print_progress("Raw", index, total, pr["id"], model_id=model["id"],
                               status=f"INVALID ({reason})", elapsed=elapsed)
                print(f"  [WARN] {pr['id']} × {model['id']}: review failed validation: {reason}")
                print(f"  [WARN] First 200 chars: {response[:200]}")
                return

            # Check for cheating
            is_cheating, signals = _detect_cheating(response)
            if is_cheating:
                print(f"  [CHEAT] {pr['id']} × {model['id']}: cheating detected!")
                for sig in signals:
                    print(f"    - {sig}")

            result = {
                "prNumber": pr.get("url", "").split("/")[-1],
                "messages": [{"reviewerId": model["id"], "content": response}],
                "mode": "raw",
                "cheating_signals": signals if is_cheating else [],
            }
            save_json(output_path, result)
            cheat_tag = " [CHEAT]" if is_cheating else ""
            print_progress("Raw", index, total, pr["id"], model_id=model["id"],
                           status=f"done ({len(response)} chars){cheat_tag}", elapsed=elapsed)
        else:
            print_progress("Raw", index, total, pr["id"], model_id=model["id"], status="failed", elapsed=elapsed)

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

    print_phase_end("Raw", total, time.time() - phase_start)
