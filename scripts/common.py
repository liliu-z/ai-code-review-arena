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
MILVUS_REPO = Path(os.environ.get("MILVUS_REPO", str(Path.home() / "milvus")))


def checkout_milvus_to_pr(pr_url):
    """Checkout local milvus repo to a PR's merge commit.

    This prevents models from seeing post-merge fixes in local code.

    Args:
        pr_url: GitHub PR URL

    Returns:
        str: the merge commit SHA, or None on failure
    """
    # Get merge commit SHA
    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "mergeCommit", "--jq", ".mergeCommit.oid"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        print(f"  [WARN] Could not get merge commit for {pr_url}")
        return None

    sha = result.stdout.strip()

    # Checkout to that commit
    checkout_result = subprocess.run(
        ["git", "checkout", sha, "--quiet"],
        capture_output=True, text=True, timeout=30,
        cwd=str(MILVUS_REPO),
    )
    if checkout_result.returncode != 0:
        # Try fetching first
        subprocess.run(
            ["git", "fetch", "origin", sha, "--quiet"],
            capture_output=True, text=True, timeout=60,
            cwd=str(MILVUS_REPO),
        )
        checkout_result = subprocess.run(
            ["git", "checkout", sha, "--quiet"],
            capture_output=True, text=True, timeout=30,
            cwd=str(MILVUS_REPO),
        )
        if checkout_result.returncode != 0:
            print(f"  [WARN] Could not checkout {sha[:12]}: {checkout_result.stderr.strip()[:100]}")
            return None

    return sha


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


def generate_magpie_config(models, config, is_hard=False, no_context=False):
    """Generate a complete Magpie config YAML and save to temp file.

    Args:
        models: list of model dicts from config (each has 'id' and 'magpie_provider')
        config: full config dict
        is_hard: if True, use hard_score settings; otherwise soft_score
        no_context: if True, minimize analyzer output and disable context gathering

    Returns:
        tuple: (Path to generated config file, rounds)
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
        provider_entry = {"enabled": True}
        # MiniMax needs API key in provider config
        if model["magpie_provider"] == "minimax" and model.get("api_key"):
            provider_entry["api_key"] = model["api_key"]
        providers[model["magpie_provider"]] = provider_entry

    if no_context:
        # Minimal analyzer: just state the PR number, no deep analysis
        analyzer_prompt = "Say only: 'Review the PR diff below.' Do not analyze or summarize the PR."
        # Disable context gathering
        context_config = {"enabled": False}
    else:
        analyzer_prompt = (
            "You are a senior engineer providing concise PR context analysis. "
            "Summarize what this PR does, what files are affected, and any areas of concern.\n\n"
            "CRITICAL RULES:\n"
            "- Do NOT include the PR's merge status, approval status, or review labels (LGTM, /approve, etc.).\n"
            "- Do NOT mention whether this PR was merged, reverted, or closed.\n"
            "- Do NOT reference any follow-up, fix, hotfix, or revert PRs.\n"
            "- Do NOT include dates or timestamps of PR events.\n"
            "- Focus ONLY on the code changes, affected files, and technical concerns."
        )
        context_config = None

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
            "prompt": analyzer_prompt,
        },
        "summarizer": {
            "model": first_provider,
            "prompt": (
                "You are a neutral technical reviewer. Synthesize the debate into a final conclusion. "
                "Highlight consensus points and unresolved disagreements. Be concise.\n"
                "Do NOT reference any follow-up PRs, fix PRs, revert PRs, or post-merge events. "
                "Do NOT mention the PR's merge status or approval status."
            ),
        },
    }

    if context_config:
        magpie_config["contextGatherer"] = context_config

    # Write to magpie_configs/ directory with unique name
    MAGPIE_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = MAGPIE_CONFIGS_DIR / f"magpie_{uuid.uuid4().hex[:12]}.yaml"

    with open(config_path, "w") as f:
        yaml.dump(magpie_config, f, default_flow_style=False, allow_unicode=True)

    return config_path, rounds


def run_magpie(pr_url, config_path, output_path, format="json", rounds=None, skip_context=False):
    """Run magpie review command.

    Args:
        pr_url: GitHub PR URL
        config_path: path to Magpie config file
        output_path: path for output file
        format: output format (json or markdown)
        rounds: max debate rounds (overrides Magpie CLI default of 5)
        skip_context: if True, add --skip-context flag to disable context gathering

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

    if rounds is not None:
        cmd.extend(["-r", str(rounds)])

    if skip_context:
        cmd.append("--skip-context")

    # Clean env: unset CLAUDECODE to allow nested claude-code sessions
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    # Use Gemini's best model; fall back to flash if quota issues occur
    if "GEMINI_MODEL" not in env:
        env["GEMINI_MODEL"] = "gemini-3-pro-preview"
    # Pass MiniMax API key if set in any model config
    if "MINIMAX_API_KEY" not in env:
        # Try to load from config file
        try:
            cfg = load_config()
            for m in cfg.get("models", []):
                if m.get("id") == "minimax" and m.get("api_key"):
                    env["MINIMAX_API_KEY"] = m["api_key"]
                    break
        except Exception:
            pass

    result = subprocess.run(
        cmd,
        input="n\n",  # Auto-answer "no" to "post comments to GitHub?" prompt
        capture_output=True,
        text=True,
        timeout=3600,  # 60 minute timeout per review
        env=env,
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


def get_pr_diff(pr_url):
    """Get PR diff via gh CLI.

    Args:
        pr_url: GitHub PR URL

    Returns:
        str: diff text
    """
    result = subprocess.run(
        ["gh", "pr", "diff", pr_url],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"  [ERROR] gh pr diff failed: {result.stderr.strip()[:200]}")
        return ""
    return result.stdout


def get_pr_info(pr_url):
    """Get PR metadata via gh CLI.

    Args:
        pr_url: GitHub PR URL

    Returns:
        dict: {title, body}
    """
    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "title,body"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"  [ERROR] gh pr view failed: {result.stderr.strip()[:200]}")
        return {"title": "", "body": ""}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"title": "", "body": ""}


def run_judge(model_config, prompt_text, timeout=600, cwd=None):
    """Run a model CLI with the given prompt.

    Handles model-specific invocation differences:
    - Claude: stdin pipe with -p flag
    - Gemini: prompt as positional arg (like Magpie does)
    - Codex: stdin pipe, parse JSONL output
    - Minimax: direct API call (OpenAI-compatible)
    - Qwen: stdin pipe

    Args:
        model_config: model dict from config (has 'id', 'judge_cmd')
        prompt_text: the full prompt to send
        timeout: timeout in seconds (default 600 = 10 minutes)
        cwd: working directory for the subprocess (default: clean temp dir)

    Returns:
        str: model's response text
    """
    model_id = model_config["id"]

    # Minimax: use direct API call instead of CLI subprocess
    if model_id == "minimax":
        return _run_minimax_api(model_config, prompt_text, timeout)

    # Clean env: unset CLAUDECODE to allow nested claude-code sessions
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    if "GEMINI_MODEL" not in env:
        env["GEMINI_MODEL"] = "gemini-3-pro-preview"

    # Use provided cwd, or fall back to a clean temp directory
    clean_cwd = cwd or tempfile.mkdtemp(prefix="arena_")
    created_tmp = cwd is None

    if model_id == "gemini":
        # Gemini: pass prompt as positional arg (not stdin), like Magpie does
        cmd = ["gemini", "-y", prompt_text]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=clean_cwd,
        )
    elif model_id == "codex":
        # Codex: stdin pipe with --json for structured output
        cmd = ["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "-"]
        result = subprocess.run(
            cmd,
            input=prompt_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=clean_cwd,
        )
    elif model_id == "qwen":
        # Qwen: stdin pipe with -p '' for headless mode
        # Use 5 turns (consistent with Magpie's qwen-code.ts setting)
        cmd = ["qwen", "-p", "", "-y", "--max-session-turns", "5", "--output-format", "text"]
        result = subprocess.run(
            cmd,
            input=prompt_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=clean_cwd,
        )
    else:
        # Claude and others: stdin pipe
        judge_cmd = model_config["judge_cmd"]
        result = subprocess.run(
            judge_cmd,
            shell=True,
            input=prompt_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=clean_cwd,
        )

    # Clean up temp dir (only if we created it)
    if created_tmp:
        try:
            os.rmdir(clean_cwd)
        except OSError:
            pass

    if result.returncode != 0:
        print(f"  [ERROR] {model_id} return code {result.returncode}")
        if result.stderr:
            lines = result.stderr.strip().split("\n")
            for line in lines[-3:]:
                print(f"  {line}")
        return ""

    # Parse output based on model
    output = result.stdout.strip()

    if model_id == "codex" and output:
        # Parse JSONL: extract text from item.completed events
        text_parts = []
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if (event.get("type") == "item.completed"
                        and event.get("item", {}).get("type") == "agent_message"
                        and event.get("item", {}).get("text")):
                    text_parts.append(event["item"]["text"])
            except json.JSONDecodeError:
                # Not JSON, use as plain text
                text_parts.append(line)
        return "\n".join(text_parts) if text_parts else output

    return output


def _run_minimax_api(model_config, prompt_text, timeout=600):
    """Run MiniMax API call directly (OpenAI-compatible endpoint).

    Args:
        model_config: model dict with 'api_key'
        prompt_text: the full prompt
        timeout: timeout in seconds

    Returns:
        str: model's response text, with <think> tags stripped
    """
    import urllib.request

    api_key = model_config.get("api_key", os.environ.get("MINIMAX_API_KEY", ""))
    if not api_key:
        print("  [ERROR] minimax: no API key found")
        return ""

    payload = json.dumps({
        "model": "MiniMax-M2.5",
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 16000,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.minimax.io/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            # Strip <think>...</think> reasoning tags
            content = re.sub(r'<think>[\s\S]*?</think>\s*', '', content)
            return content.strip()
    except Exception as e:
        print(f"  [ERROR] minimax API call failed: {e}")
        return ""


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
    """Extract review content from a result file (Raw or Magpie JSON).

    Only includes the actual reviewer messages — NOT Magpie framework artifacts
    like finalConclusion, parsedIssues, or summaries. This ensures the judge
    evaluates what the model actually wrote, and avoids leaking information
    from the Magpie summarizer (which may reference fix PRs or post-merge info).
    """
    data = load_json(result_path)

    parts = []

    # Include only reviewer messages (what the model actually wrote)
    for msg in data.get("messages", []):
        parts.append(f"## {msg.get('reviewerId', 'Unknown')} Review\n\n{msg.get('content', '')}")

    return "\n\n".join(parts)


def extract_reviews_by_model(result_path):
    """Extract individual reviews keyed by reviewer/model ID from debate result.

    Only includes actual reviewer messages, not Magpie-generated summaries.

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

    return reviews


def extract_first_round_reviews(result_path):
    """Extract only the FIRST review per reviewer (before debate rounds).

    This avoids passing multi-round debate text where models reference each
    other by name, which defeats anonymization.

    Returns:
        dict: {reviewer_id: first_round_review_text}
    """
    data = load_json(result_path)
    reviews = {}

    for msg in data.get("messages", []):
        reviewer_id = msg.get("reviewerId", "unknown")
        content = msg.get("content", "")
        # Only take the first message per reviewer
        if reviewer_id not in reviews:
            reviews[reviewer_id] = content

    return reviews


def strip_model_names(text, model_names):
    """Strip known model/provider names from review text to improve anonymization.

    Removes patterns like [claude-code], "gemini-cli's review", etc.

    Args:
        text: review text
        model_names: list of model identifiers to strip (e.g. ["claude-code", "gemini-cli"])

    Returns:
        cleaned text
    """
    for name in model_names:
        # Remove bracketed references like [claude-code]
        text = re.sub(r'\[' + re.escape(name) + r'\]', '', text)
        # Remove "Response to <model>" headers
        text = re.sub(r'(?i)response to ' + re.escape(name), 'Response to the other reviewer', text)
        # Remove standalone references
        text = re.sub(r'(?<!\w)' + re.escape(name) + r'(?!\w)', 'the other reviewer', text)
    # Clean up any resulting double spaces or empty headers
    text = re.sub(r'## +\n', '', text)
    text = re.sub(r'  +', ' ', text)
    return text
