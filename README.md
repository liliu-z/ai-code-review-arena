# AI Code Review Arena

A multi-AI-model code review arena: real Milvus PRs serve as the battleground where AI models review, debate, and anonymously score each other.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Ensure the following CLIs are installed
magpie --version    # Magpie (npm install -g magpie)
claude --version    # Claude Code CLI
gemini --version    # Gemini CLI
codex --version     # Codex CLI

# 3. Run everything
python run.py
```

## Evaluation Pipeline

```
python run.py
 ├─ 1. Hard Score   Each model independently reviews the original PR with injected bugs (-r 1)
 ├─ 2. Soft Score   All models debate each PR (multi-round adversarial)
 ├─ 3. Judge        Hard Score: three-way vote on whether the bug was found
 │                  Soft Score: anonymous peer review across 4 dimensions (1-10)
 └─ 4. Report       Leaderboard + judge bias analysis
```

## Step-by-Step Execution

```bash
python run.py --hard             # Run Hard Score only
python run.py --soft             # Run Soft Score only
python run.py --judge            # Run Judge only
python run.py --report           # Generate report only
python run.py --pr pr-47154      # Run a specific PR only
python run.py --model claude     # Run a specific model only
python run.py --force            # Force re-run (ignore existing results)
```

Resume from checkpoint is supported: completed tasks are automatically skipped.

## Configuration

### config.yaml -- Models and Parameters

```yaml
models:
  - id: claude
    magpie_provider: claude-code
    judge_cmd: "claude -p"           # prompt via stdin
  - id: gemini
    magpie_provider: gemini-cli
    judge_cmd: "gemini"              # prompt via stdin
  - id: codex
    magpie_provider: codex-cli
    judge_cmd: "codex exec -"        # prompt via stdin
```

**Adding a new model**: simply add an entry with `magpie_provider` and `judge_cmd`.

### prs/manifest.yaml -- PR Dataset

```yaml
prs:
  - id: pr-47154
    url: "https://github.com/milvus-io/milvus/pull/47154"
    category: hard        # hard = Hard Score, soft = Soft Score
    difficulty: L2         # L1/L2/L3
    known_bugs:
      - id: bug-name
        description: "Bug description used by the judge for evaluation"
```

## Scoring System

### Hard Score: Bug Detection Rate

- Each model independently reviews the original PR that contains injected bugs
- Three models vote to determine whether a known bug was found (majority rule)
- Results are grouped by L1/L2/L3 difficulty levels

### Soft Score: Review Quality

- After a multi-model debate, each model's review is extracted
- Reviews are anonymized (Reviewer A/B/C) with randomized mapping
- Each model acts as a judge and scores all anonymous reviews
- 4 dimensions: Accuracy, Actionability, Depth, Clarity (1-10)

## Results Directory

```
results/
├── hard/<pr-id>/<model-id>.json          # Independent review raw output
├── soft/<pr-id>/debate.json              # Debate raw output
├── judge/hard/<pr-id>/...                # Hard Score judge results
├── judge/soft/<pr-id>/...                # Soft Score judge results
└── reports/
    ├── hard_scores.csv                   # Hard Score details
    ├── soft_scores.csv                   # Soft Score details
    ├── hard_summary.json                 # Hard Score summary
    ├── soft_summary.json                 # Soft Score summary
    ├── judge_bias.json                   # Judge bias analysis
    └── summary.txt                       # Human-readable summary
```

## Prerequisites

- Python 3.10+
- [Magpie](https://github.com/liliu-z/magpie) (`npm install -g magpie`)
- Claude Code CLI (`claude`)
- Gemini CLI (`gemini`)
- Codex CLI (`codex`)
- GitHub CLI (`gh`) -- used by Magpie to fetch PR diffs
