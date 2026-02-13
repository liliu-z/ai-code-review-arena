"""Common utilities for AI Code Review Arena."""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
RESULTS_DIR = PROJECT_ROOT / "results"
MAGPIE_CONFIGS_DIR = PROJECT_ROOT / "magpie_configs"


def load_config():
    """Load main config.yaml."""
    with open(PROJECT_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_manifest():
    """Load prs/manifest.yaml."""
    with open(PROJECT_ROOT / "prs" / "manifest.yaml") as f:
        return yaml.safe_load(f)


def load_prompt(name):
    """Load a prompt template from prompts/ directory."""
    with open(PROJECT_ROOT / "prompts" / name) as f:
        return f.read()


def result_exists(path):
    """Check if a result file exists and is non-empty."""
    p = Path(path) if not isinstance(path, Path) else path
    return p.exists() and p.stat().st_size > 0


def ensure_dir(path):
    """Ensure directory exists."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def save_json(path, data):
    """Save data as JSON."""
    ensure_dir(path)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_json(path):
    """Load JSON file."""
    with open(path) as f:
        return json.load(f)


def print_progress(phase, index, total, pr_id, model_id=None, status="started", elapsed=None):
    """Print formatted progress line.

    Examples:
        [Hard] [1/24] pr-33820 × claude ... started
        [Hard] [1/24] pr-33820 × claude ... done (2m34s)
        [Hard] [skipped] pr-33820 × claude (result exists)
    """
    elapsed_str = ""
    if elapsed is not None:
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        if minutes > 0:
            elapsed_str = f" ({minutes}m{seconds:02d}s)"
        else:
            elapsed_str = f" ({seconds}s)"

    if status == "skipped":
        model_part = f" × {model_id}" if model_id else ""
        print(f"[{phase}] [skipped] {pr_id}{model_part} (result exists)")
    else:
        counter = f"[{index}/{total}]"
        model_part = f" × {model_id}" if model_id else ""
        print(f"[{phase}] {counter} {pr_id}{model_part} ... {status}{elapsed_str}")

    sys.stdout.flush()


def print_phase_start(phase, task_count, concurrency=None):
    """Print phase start banner."""
    conc = f", concurrency {concurrency}" if concurrency else ""
    print(f"\n{'='*60}")
    print(f"[{phase}] starting: {task_count} tasks{conc}")
    print(f"{'='*60}")
    sys.stdout.flush()


def print_phase_end(phase, total, elapsed):
    """Print phase end banner."""
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    print(f"[{phase}] all done: {total} tasks, elapsed {minutes}m{seconds:02d}s")
    sys.stdout.flush()


def generate_magpie_config(models, config, is_hard=False):
    """Generate a complete Magpie config YAML and save to temp file.

    Args:
        models: list of model dicts from config (each has 'id' and 'magpie_provider')
        config: full config dict
        is_hard: if True, use hard_score settings; otherwise soft_score

    Returns:
        Path to generated config file
    """
    review_prompt = config.get("review_prompt", "You are a senior engineer reviewing this PR.")

    if is_hard:
        rounds = config["hard_score"]["rounds"]
        check_convergence = False
    else:
        rounds = config["soft_score"]["rounds"]
        check_convergence = config["soft_score"].get("check_convergence", True)

    # Build reviewers section
    reviewers = {}
    for model in models:
        provider = model["magpie_provider"]
        reviewers[provider] = {
            "model": provider,
            "prompt": review_prompt,
        }

    # Use first model as analyzer and summarizer
    first_provider = models[0]["magpie_provider"]

    # Dynamically build providers from models being used
    providers = {}
    for model in models:
        providers[model["magpie_provider"]] = {"enabled": True}

    magpie_config = {
        "providers": providers,
        "defaults": {
            "max_rounds": rounds,
            "output_format": "json",
            "check_convergence": check_convergence,
        },
        "reviewers": reviewers,
        "analyzer": {
            "model": first_provider,
            "prompt": "You are a senior engineer providing concise PR context analysis. Summarize what this PR does, what files are affected, and any areas of concern.",
        },
        "summarizer": {
            "model": first_provider,
            "prompt": "You are a neutral technical reviewer. Synthesize the debate into a final conclusion. Highlight consensus points and unresolved disagreements. Be concise.",
        },
    }

    # Write to magpie_configs/ directory with unique name
    MAGPIE_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = MAGPIE_CONFIGS_DIR / f"magpie_{uuid.uuid4().hex[:12]}.yaml"

    with open(config_path, "w") as f:
        yaml.dump(magpie_config, f, default_flow_style=False, allow_unicode=True)

    return config_path


def run_magpie(pr_url, config_path, output_path, format="json"):
    """Run magpie review command.

    Args:
        pr_url: GitHub PR URL
        config_path: path to Magpie config file
        output_path: path for output file
        format: output format (json or markdown)

    Returns:
        subprocess.CompletedProcess
    """
    ensure_dir(output_path)

    cmd = [
        "magpie", "review", pr_url,
        "-c", str(config_path),
        "-o", str(output_path),
        "-f", format,
        "-a",  # use all reviewers (skip interactive selection)
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,  # 10 minute timeout per review
    )

    if result.returncode != 0:
        print(f"  [ERROR] magpie return code {result.returncode}")
        if result.stderr:
            # Print last 5 lines of stderr
            lines = result.stderr.strip().split("\n")
            for line in lines[-5:]:
                print(f"  {line}")
        sys.stdout.flush()

    return result


def run_judge(model_config, prompt_text):
    """Run a judge model CLI with the given prompt via stdin pipe.

    Args:
        model_config: model dict from config (has 'id', 'judge_cmd')
        prompt_text: the full prompt to send

    Returns:
        str: model's response text
    """
    judge_cmd = model_config["judge_cmd"]

    result = subprocess.run(
        judge_cmd,
        shell=True,
        input=prompt_text,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minute timeout for judge
    )

    if result.returncode != 0:
        print(f"  [ERROR] {model_config['id']} judge return code {result.returncode}")
        if result.stderr:
            lines = result.stderr.strip().split("\n")
            for line in lines[-3:]:
                print(f"  {line}")
        return ""

    return result.stdout.strip()


def parse_judge_json(response):
    """Extract JSON from judge model response.

    Models may wrap JSON in markdown code blocks or include extra text.
    This function extracts the JSON object.
    """
    if not response:
        return {}

    text = response.strip()

    # Try to find JSON in code blocks first
    json_block = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if json_block:
        text = json_block.group(1).strip()

    # Try to find JSON object directly
    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Last resort: try the whole text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"  [WARN] failed to parse judge JSON output")
        return {}


def extract_review_content(result_path):
    """Extract review content from a Magpie JSON result file.

    Magpie output structure:
    {
        "messages": [{"reviewerId": "...", "content": "..."}],
        "summaries": [{"reviewerId": "...", "summary": "..."}],
        "finalConclusion": "...",
        "parsedIssues": [...]
    }
    """
    data = load_json(result_path)

    parts = []

    # Include all reviewer messages
    for msg in data.get("messages", []):
        parts.append(f"## {msg.get('reviewerId', 'Unknown')} Review\n\n{msg.get('content', '')}")

    # Include final conclusion if present
    conclusion = data.get("finalConclusion", "")
    if conclusion:
        parts.append(f"## Final Conclusion\n\n{conclusion}")

    # Include parsed issues if present
    issues = data.get("parsedIssues", [])
    if issues:
        parts.append("## Identified Issues\n")
        for issue in issues:
            severity = issue.get("severity", "unknown")
            title = issue.get("title", "")
            desc = issue.get("description", "")
            parts.append(f"- [{severity}] {title}: {desc}")

    return "\n\n".join(parts)


def extract_reviews_by_model(result_path):
    """Extract individual reviews keyed by reviewer/model ID from debate result.

    Returns:
        dict: {reviewer_id: review_text}
    """
    data = load_json(result_path)
    reviews = {}

    for msg in data.get("messages", []):
        reviewer_id = msg.get("reviewerId", "unknown")
        content = msg.get("content", "")
        if reviewer_id in reviews:
            reviews[reviewer_id] += "\n\n---\n\n" + content
        else:
            reviews[reviewer_id] = content

    # Also append summaries
    for summary in data.get("summaries", []):
        reviewer_id = summary.get("reviewerId", "unknown")
        summary_text = summary.get("summary", "")
        if summary_text:
            if reviewer_id in reviews:
                reviews[reviewer_id] += "\n\n## Summary\n\n" + summary_text
            else:
                reviews[reviewer_id] = summary_text

    return reviews
