# AI Code Review Arena

Evaluate AI code review models on real bug-introducing PRs. Models review independently, debate collaboratively, then judge each other anonymously.

Built on [Magpie](https://github.com/liliu-z/magpie) for multi-model orchestration. Test dataset: 15 PRs from [Milvus](https://github.com/milvus-io/milvus) with known bugs at L2/L3 difficulty.

## Quick Start

```bash
# Prerequisites: Python 3.10+, gh CLI, and model CLIs
pip install pyyaml

# Set up model CLIs
# claude, gemini, codex, qwen — install per their docs
# minimax — uses API directly, set MINIMAX_API_KEY env var

# Clone a local copy of the target repo (for anti-cheat checkout)
export MILVUS_REPO=~/milvus
git clone https://github.com/milvus-io/milvus.git $MILVUS_REPO

# Run the full pipeline
python run.py
```

## Three Evaluation Modes

| Mode | What it measures | How it works |
|------|-----------------|-------------|
| **Raw** | Bare model capability | Direct CLI call with PR URL, model fetches diff itself |
| **R1** | Framework-assisted review | Magpie collects context, model reviews independently (1 round) |
| **Debate** | Collaborative bug hunting | All models debate adversarially (5 rounds) |

## Pipeline

```
python run.py
 ├─ Raw      Each model × each PR → results/raw/<pr>/<model>.json
 ├─ R1       Magpie single-round per model → results/r1/<pr>/<model>.json
 ├─ Debate   All models multi-round debate → results/debate/<pr>/debate.json
 ├─ Judge    Claude evaluates bug detection (hard) + anonymous quality scoring (soft)
 └─ Report   Aggregate scores, bias analysis → results/reports/
```

## CLI

```bash
python run.py                    # Full pipeline
python run.py --raw              # Raw reviews only
python run.py --r1               # Magpie R1 only
python run.py --debate           # Multi-round debate only
python run.py --judge            # Run judges only
python run.py --report           # Generate reports only
python run.py --pr pr-43542      # Specific PR
python run.py --model claude     # Specific model (raw/r1)
python run.py --no-context       # R1/debate without context injection
python run.py --force            # Ignore checkpoints, re-run everything
```

Checkpoint/resume is automatic — completed tasks are skipped on re-run.

## Configuration

### config.yaml

```yaml
models:
  - id: claude
    magpie_provider: claude-code
    judge_cmd: "claude -p - --dangerously-skip-permissions"
  - id: gemini
    magpie_provider: gemini-cli
    judge_cmd: "gemini -y"
  # Add more models here

execution:
  concurrency: 5

hard_score:
  rounds: 1          # R1 = single independent round

soft_score:
  rounds: 5          # Debate = 5 adversarial rounds
  check_convergence: true
```

### prs/manifest.yaml

```yaml
prs:
  - id: pr-44474
    url: "https://github.com/milvus-io/milvus/pull/44474"
    category: hard
    difficulty: L3
    known_bugs:
      - id: lazy-pk-lifecycle
        description: |
          Lazy PK fetching leaves primary_keys_ partially populated...
```

## Scoring

**Hard Score** — Binary bug detection. Claude judges whether each model's review identified the known bug. Per-model for raw/r1; per-debate for debate mode.

**Soft Score** — Review quality rating (1-10). After debate, reviews are anonymized (Reviewer A/B/C/D/E, random mapping per PR). Each model judges all reviewers on 4 dimensions: accuracy, actionability, depth, clarity.

**Judge Bias** — Self-score vs others-score delta per model, detecting whether models secretly favor their own reviews despite anonymization.

## Anti-Cheating

- Local Milvus repo checked out to PR's merge commit (no post-fix code visible)
- Prompts explicitly forbid browsing master, git operations, or referencing fix/revert PRs
- Post-review validation scans for cheating signals (references to reverts, fixes, post-merge knowledge)
- All 15 test PRs selected to have NO fix/revert cross-references on their GitHub pages

## Project Structure

```
├── config.yaml              # Model configs, scoring parameters, review prompt
├── run.py                   # Main pipeline runner
├── prs/manifest.yaml        # PR dataset with known bugs
├── prompts/                 # Judge prompt templates
│   ├── hard_judge.txt
│   └── soft_judge.txt
└── scripts/
    ├── common.py            # Shared utilities (Magpie runner, model CLI, etc.)
    ├── raw_score.py         # Raw review pipeline
    ├── hard_score.py        # R1 (Magpie single-round) pipeline
    ├── soft_score.py        # Debate (multi-round) pipeline
    ├── judge.py             # Hard + soft judge pipeline
    └── report.py            # Report generator
```

## Prerequisites

- Python 3.10+
- [Magpie](https://github.com/liliu-z/magpie) (`npm install -g @anthropic/magpie` or build from source)
- [GitHub CLI](https://cli.github.com/) (`gh`)
- Model CLIs: `claude`, `gemini`, `codex`, `qwen` (install per their docs)
- For MiniMax: set `MINIMAX_API_KEY` environment variable

## License

MIT
