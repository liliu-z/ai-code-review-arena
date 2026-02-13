"""Report generator: aggregate results and generate summaries."""

import csv
import json
import io
import os
from collections import defaultdict
from pathlib import Path

from scripts.common import RESULTS_DIR, load_json, save_json


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
    """Generate hard score report: bug detection rates by model and difficulty."""
    hard_prs = [p for p in manifest["prs"] if p["category"] == "hard"]
    models = config["models"]

    # Load verdicts
    verdicts_path = RESULTS_DIR / "judge" / "hard" / "verdicts.json"
    if not verdicts_path.exists():
        print("[Report] skipped hard score report (no judge results)")
        return {}

    verdicts = load_json(verdicts_path)

    # Build CSV rows and summary
    rows = []
    summary = defaultdict(lambda: defaultdict(lambda: {"found": 0, "total": 0}))

    for pr in hard_prs:
        difficulty = pr.get("difficulty", "unknown")
        for bug in pr.get("known_bugs", []):
            for model in models:
                key = f"{pr['id']}/{bug['id']}/{model['id']}"
                v = verdicts.get(key, {})
                found = v.get("found", False)
                rows.append({
                    "model": model["id"],
                    "pr_id": pr["id"],
                    "bug_id": bug["id"],
                    "difficulty": difficulty,
                    "found": found,
                    "yes_votes": v.get("yes_count", 0),
                    "total_votes": v.get("total_votes", 0),
                })
                summary[model["id"]][difficulty]["total"] += 1
                if found:
                    summary[model["id"]][difficulty]["found"] += 1

    # Write CSV
    csv_path = reports_dir / "hard_scores.csv"
    if rows:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"  Hard score details: {csv_path}")

    # Write summary JSON
    summary_data = {}
    for model_id, by_diff in summary.items():
        model_summary = {}
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
        summary_data[model_id] = model_summary

    save_json(reports_dir / "hard_summary.json", summary_data)
    print(f"  Hard score summary: {reports_dir / 'hard_summary.json'}")
    return summary_data


def _generate_soft_report(config, manifest, reports_dir):
    """Generate soft score report: average dimension scores by model."""
    all_prs = manifest["prs"]
    models = config["models"]
    dimensions = config["judge"]["dimensions"]

    # Collect all scores
    rows = []
    # scores_by_model[real_model_id][dimension] = [scores...]
    scores_by_model = defaultdict(lambda: defaultdict(list))

    for pr in all_prs:
        # Load mapping to de-anonymize
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

    # Write CSV
    csv_path = reports_dir / "soft_scores.csv"
    if rows:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"  Soft score details: {csv_path}")

    # Write summary JSON
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
        # Overall average across all dimensions
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
    dimensions = config["judge"]["dimensions"]

    # self_scores[judge_id] = [scores when judging self]
    # other_scores[judge_id] = [scores when judging others]
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

                # Is judge scoring itself (via magpie_provider matching)?
                judge_provider = judge_model["magpie_provider"]
                if real_model == judge_provider or real_model == judge_model["id"]:
                    self_scores[judge_model["id"]].append(avg)
                else:
                    other_scores[judge_model["id"]].append(avg)

    # Build bias summary
    bias_data = {}
    for model in models:
        mid = model["id"]
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
    lines = []
    lines.append("=" * 60)
    lines.append("AI Code Review Arena - Evaluation Results Summary")
    lines.append("=" * 60)

    # Hard scores
    if hard_summary:
        lines.append("\n## Hard Score: Bug Detection Rate")
        lines.append(f"{'Model':<12} {'L1':<10} {'L2':<10} {'L3':<10} {'Total':<10}")
        lines.append("-" * 52)
        for model_id, by_diff in hard_summary.items():
            l1 = by_diff.get("L1", {})
            l2 = by_diff.get("L2", {})
            l3 = by_diff.get("L3", {})
            overall = by_diff.get("overall", {})
            lines.append(
                f"{model_id:<12} "
                f"{l1.get('found', 0)}/{l1.get('total', 0):<7} "
                f"{l2.get('found', 0)}/{l2.get('total', 0):<7} "
                f"{l3.get('found', 0)}/{l3.get('total', 0):<7} "
                f"{overall.get('rate', 0):.0%}"
            )

    # Soft scores
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

    # Bias
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

    lines.append("\n" + "=" * 60)

    summary_text = "\n".join(lines)

    # Print to stdout
    print(summary_text)

    # Save to file
    with open(reports_dir / "summary.txt", "w") as f:
        f.write(summary_text)
    print(f"\n  Summary text: {reports_dir / 'summary.txt'}")
