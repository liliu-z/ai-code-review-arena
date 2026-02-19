"""Report generator: aggregate results and generate summaries."""

import csv
import json
import io
import os
from collections import defaultdict
from pathlib import Path

from scripts.common import RESULTS_DIR, load_json, save_json


# Per-model modes (each model judged individually)
PER_MODEL_MODES = ["raw", "r1", "r1_nocontext"]
# Debate modes (judged as one collaborative unit)
DEBATE_MODES = ["debate", "debate_nocontext"]
# All modes
REVIEW_MODES = PER_MODEL_MODES + DEBATE_MODES


def run_report(config, manifest):
    """Generate reports from all results."""
    reports_dir = RESULTS_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"[Report] generating...")
    print(f"{'='*60}")

    hard_summary = _generate_hard_report(config, manifest, reports_dir)
    soft_summary = _generate_soft_report(config, manifest, reports_dir)
    bias_summary = _generate_bias_report(config, manifest, reports_dir)
    _generate_text_summary(config, manifest, reports_dir, hard_summary, soft_summary, bias_summary)

    print(f"[Report] done: {reports_dir}")


def _generate_hard_report(config, manifest, reports_dir):
    """Generate hard score report: bug detection rates by mode, model, and difficulty.

    Per-model modes (raw, r1, r1_nocontext): per-model detection rates.
    Debate modes (debate, debate_nocontext): overall detection rate (one score).
    """
    hard_prs = [p for p in manifest["prs"] if p["category"] == "hard"]
    models = config["models"]

    verdicts_path = RESULTS_DIR / "judge" / "verdicts.json"
    if not verdicts_path.exists():
        print("[Report] skipped hard score report (no judge results)")
        return {}

    verdicts = load_json(verdicts_path)

    rows = []
    # For per-model modes: summary[mode][model_id][difficulty] = {found, total}
    summary = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"found": 0, "total": 0})))
    # For debate modes: debate_summary[mode][difficulty] = {found, total}
    debate_summary = defaultdict(lambda: defaultdict(lambda: {"found": 0, "total": 0}))

    for mode in PER_MODEL_MODES:
        for pr in hard_prs:
            difficulty = pr.get("difficulty", "unknown")
            for bug in pr.get("known_bugs", []):
                for model in models:
                    key = f"{mode}/{pr['id']}/{bug['id']}/{model['id']}"
                    v = verdicts.get(key, {})
                    if not v:
                        continue
                    found = v.get("found", False)
                    rows.append({
                        "mode": mode,
                        "model": model["id"],
                        "pr_id": pr["id"],
                        "bug_id": bug["id"],
                        "difficulty": difficulty,
                        "found": found,
                    })
                    summary[mode][model["id"]][difficulty]["total"] += 1
                    if found:
                        summary[mode][model["id"]][difficulty]["found"] += 1

    for mode in DEBATE_MODES:
        for pr in hard_prs:
            difficulty = pr.get("difficulty", "unknown")
            for bug in pr.get("known_bugs", []):
                key = f"{mode}/{pr['id']}/{bug['id']}/debate"
                v = verdicts.get(key, {})
                if not v:
                    continue
                found = v.get("found", False)
                rows.append({
                    "mode": mode,
                    "model": "debate",
                    "pr_id": pr["id"],
                    "bug_id": bug["id"],
                    "difficulty": difficulty,
                    "found": found,
                })
                debate_summary[mode][difficulty]["total"] += 1
                if found:
                    debate_summary[mode][difficulty]["found"] += 1

    # Write CSV
    csv_path = reports_dir / "hard_scores.csv"
    if rows:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"  Hard score details: {csv_path}")

    # Build summary_data combining both per-model and debate
    summary_data = {}

    for mode in PER_MODEL_MODES:
        for model_id, by_diff in summary[mode].items():
            mode_model_key = f"{mode}/{model_id}"
            model_summary = {"mode": mode, "model": model_id}
            total_found = 0
            total_total = 0
            for diff, counts in by_diff.items():
                rate = counts["found"] / counts["total"] if counts["total"] > 0 else 0
                model_summary[diff] = {
                    "found": counts["found"],
                    "total": counts["total"],
                    "rate": round(rate, 2),
                }
                total_found += counts["found"]
                total_total += counts["total"]
            overall_rate = total_found / total_total if total_total > 0 else 0
            model_summary["overall"] = {
                "found": total_found,
                "total": total_total,
                "rate": round(overall_rate, 2),
            }
            summary_data[mode_model_key] = model_summary

    for mode in DEBATE_MODES:
        by_diff = debate_summary[mode]
        if not by_diff:
            continue
        mode_key = f"{mode}/debate"
        mode_summary = {"mode": mode, "model": "debate"}
        total_found = 0
        total_total = 0
        for diff, counts in by_diff.items():
            rate = counts["found"] / counts["total"] if counts["total"] > 0 else 0
            mode_summary[diff] = {
                "found": counts["found"],
                "total": counts["total"],
                "rate": round(rate, 2),
            }
            total_found += counts["found"]
            total_total += counts["total"]
        overall_rate = total_found / total_total if total_total > 0 else 0
        mode_summary["overall"] = {
            "found": total_found,
            "total": total_total,
            "rate": round(overall_rate, 2),
        }
        summary_data[mode_key] = mode_summary

    save_json(reports_dir / "hard_summary.json", summary_data)
    print(f"  Hard score summary: {reports_dir / 'hard_summary.json'}")
    return summary_data


def _generate_soft_report(config, manifest, reports_dir):
    """Generate soft score report: average dimension scores by model."""
    all_prs = manifest["prs"]
    models = config["models"]
    dimensions = config["judge"]["dimensions"]

    rows = []
    scores_by_model = defaultdict(lambda: defaultdict(list))

    for pr in all_prs:
        mapping_path = RESULTS_DIR / "judge" / "soft" / pr["id"] / "mapping.json"
        if not mapping_path.exists():
            continue
        mapping_data = load_json(mapping_path)
        reverse = mapping_data.get("reverse", {})

        for judge_model in models:
            judge_path = RESULTS_DIR / "judge" / "soft" / pr["id"] / f"{judge_model['id']}.json"
            if not judge_path.exists():
                continue
            judge_result = load_json(judge_path)
            scores = judge_result.get("scores", {})

            for anon_label, dim_scores in scores.items():
                real_model = reverse.get(anon_label, anon_label)
                for dim in dimensions:
                    dim_id = dim["id"]
                    score = dim_scores.get(dim_id)
                    if score is not None:
                        rows.append({
                            "model": real_model,
                            "pr_id": pr["id"],
                            "judge": judge_model["id"],
                            "dimension": dim_id,
                            "score": score,
                        })
                        scores_by_model[real_model][dim_id].append(score)

    csv_path = reports_dir / "soft_scores.csv"
    if rows:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"  Soft score details: {csv_path}")

    summary_data = {}
    for model_id, by_dim in scores_by_model.items():
        model_summary = {}
        for dim_id, score_list in by_dim.items():
            avg = sum(score_list) / len(score_list) if score_list else 0
            model_summary[dim_id] = {
                "avg": round(avg, 2),
                "min": min(score_list) if score_list else 0,
                "max": max(score_list) if score_list else 0,
                "count": len(score_list),
            }
        all_scores = [s for sl in by_dim.values() for s in sl]
        model_summary["overall"] = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0
        summary_data[model_id] = model_summary

    save_json(reports_dir / "soft_summary.json", summary_data)
    print(f"  Soft score summary: {reports_dir / 'soft_summary.json'}")
    return summary_data


def _generate_bias_report(config, manifest, reports_dir):
    """Generate judge bias report: does each model score itself higher?"""
    all_prs = manifest["prs"]
    models = config["models"]

    self_scores = defaultdict(list)
    other_scores = defaultdict(list)

    for pr in all_prs:
        mapping_path = RESULTS_DIR / "judge" / "soft" / pr["id"] / "mapping.json"
        if not mapping_path.exists():
            continue
        mapping_data = load_json(mapping_path)
        reverse = mapping_data.get("reverse", {})

        for judge_model in models:
            judge_path = RESULTS_DIR / "judge" / "soft" / pr["id"] / f"{judge_model['id']}.json"
            if not judge_path.exists():
                continue
            judge_result = load_json(judge_path)
            scores = judge_result.get("scores", {})

            for anon_label, dim_scores in scores.items():
                real_model = reverse.get(anon_label, anon_label)
                all_dim_scores = [v for v in dim_scores.values() if isinstance(v, (int, float))]
                avg = sum(all_dim_scores) / len(all_dim_scores) if all_dim_scores else 0

                judge_provider = judge_model["magpie_provider"]
                if real_model == judge_provider or real_model == judge_model["id"]:
                    self_scores[judge_model["id"]].append(avg)
                else:
                    other_scores[judge_model["id"]].append(avg)

    bias_data = {}
    for model in models:
        mid = model["id"]
        if not self_scores[mid] and not other_scores[mid]:
            continue
        self_avg = sum(self_scores[mid]) / len(self_scores[mid]) if self_scores[mid] else 0
        other_avg = sum(other_scores[mid]) / len(other_scores[mid]) if other_scores[mid] else 0
        bias = self_avg - other_avg
        bias_data[mid] = {
            "self_avg": round(self_avg, 2),
            "other_avg": round(other_avg, 2),
            "bias": round(bias, 2),
            "self_count": len(self_scores[mid]),
            "other_count": len(other_scores[mid]),
        }

    save_json(reports_dir / "judge_bias.json", bias_data)
    print(f"  Judge bias: {reports_dir / 'judge_bias.json'}")
    return bias_data


def _generate_text_summary(config, manifest, reports_dir, hard_summary, soft_summary, bias_summary):
    """Generate human-readable summary."""
    models = config["models"]
    lines = []
    lines.append("=" * 70)
    lines.append("AI Code Review Arena - Evaluation Results Summary")
    lines.append("=" * 70)

    if hard_summary:
        # Per-model modes
        lines.append("\n## Hard Score: Bug Detection Rate (Per-Model Modes)")
        lines.append(f"{'Mode':<16} {'Model':<10} {'L2':<8} {'L3':<8} {'Total':<8}")
        lines.append("-" * 56)
        for mode in PER_MODEL_MODES:
            for model in models:
                key = f"{mode}/{model['id']}"
                data = hard_summary.get(key, {})
                if not data:
                    continue
                l2 = data.get("L2", {})
                l3 = data.get("L3", {})
                overall = data.get("overall", {})
                lines.append(
                    f"{mode:<16} {model['id']:<10} "
                    f"{l2.get('found', 0)}/{l2.get('total', 0):<5} "
                    f"{l3.get('found', 0)}/{l3.get('total', 0):<5} "
                    f"{overall.get('rate', 0):.0%}"
                )

        # Debate modes
        lines.append(f"\n## Hard Score: Bug Detection Rate (Debate Modes)")
        lines.append(f"{'Mode':<24} {'L2':<8} {'L3':<8} {'Total':<8}")
        lines.append("-" * 48)
        for mode in DEBATE_MODES:
            key = f"{mode}/debate"
            data = hard_summary.get(key, {})
            if not data:
                continue
            l2 = data.get("L2", {})
            l3 = data.get("L3", {})
            overall = data.get("overall", {})
            lines.append(
                f"{mode:<24} "
                f"{l2.get('found', 0)}/{l2.get('total', 0):<5} "
                f"{l3.get('found', 0)}/{l3.get('total', 0):<5} "
                f"{overall.get('rate', 0):.0%}"
            )

    if soft_summary:
        lines.append("\n## Soft Score: Review Quality Rating (1-10)")
        dims = config["judge"]["dimensions"]
        header = f"{'Model':<12}" + "".join(f" {d['name']:<10}" for d in dims) + f" {'Overall':<6}"
        lines.append(header)
        lines.append("-" * len(header))
        for model_id, by_dim in soft_summary.items():
            parts = [f"{model_id:<12}"]
            for d in dims:
                avg = by_dim.get(d["id"], {}).get("avg", 0) if isinstance(by_dim.get(d["id"]), dict) else 0
                parts.append(f" {avg:<10.1f}")
            overall = by_dim.get("overall", 0)
            parts.append(f" {overall:<6.1f}")
            lines.append("".join(parts))

    if bias_summary:
        lines.append("\n## Judge Bias Analysis (Self Score - Others Score)")
        lines.append(f"{'Model':<12} {'Self Avg':<10} {'Other Avg':<10} {'Bias':<8}")
        lines.append("-" * 40)
        for model_id, bias in bias_summary.items():
            sign = "+" if bias["bias"] > 0 else ""
            lines.append(
                f"{model_id:<12} "
                f"{bias['self_avg']:<10.1f} "
                f"{bias['other_avg']:<10.1f} "
                f"{sign}{bias['bias']:.1f}"
            )

    lines.append("\n" + "=" * 70)

    summary_text = "\n".join(lines)
    print(summary_text)

    with open(reports_dir / "summary.txt", "w") as f:
        f.write(summary_text)
    print(f"\n  Summary text: {reports_dir / 'summary.txt'}")
