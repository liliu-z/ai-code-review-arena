# AI Code Review Arena - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a one-click reproducible evaluation repo that orchestrates Magpie to run AI code review competitions on real Milvus PRs.

**Architecture:** Python scripts call globally-installed `magpie` CLI via subprocess. Config is YAML. Results are JSON. Judge calls model CLIs directly (`claude -p`, `gemini`, `codex exec`).

**Tech Stack:** Python 3.10+, PyYAML, concurrent.futures, Magpie (global), claude/gemini/codex CLIs

---

### Task 1: Project Scaffolding

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create directory structure

**Step 1: Create .gitignore**

```gitignore
# Runtime generated
magpie_configs/
results/
__pycache__/
*.pyc

# OS
.DS_Store
```

**Step 2: Create requirements.txt**

```
pyyaml>=6.0
```

**Step 3: Create directories**

```bash
mkdir -p prs prompts scripts results magpie_configs
```

**Step 4: Create scripts/__init__.py**

Empty file.

**Step 5: Commit**

```bash
git init && git add -A && git commit -m "chore: project scaffolding"
```

---

### Task 2: Configuration Files

**Files:**
- Create: `config.yaml`
- Create: `prs/manifest.yaml`

**Step 1: Write config.yaml**

```yaml
models:
  - id: claude
    magpie_provider: claude-code
    judge_cmd: "claude -p '{prompt}'"
  - id: gemini
    magpie_provider: gemini-cli
    judge_cmd: "gemini '{prompt}'"
  - id: codex
    magpie_provider: codex-cli
    judge_cmd: "codex exec '{prompt}'"

execution:
  concurrency: 4

hard_score:
  rounds: 1

soft_score:
  rounds: 3
  check_convergence: true

judge:
  dimensions:
    - id: accuracy
      name: "问题识别准确性"
    - id: actionability
      name: "建议可操作性"
    - id: depth
      name: "分析深度"
    - id: clarity
      name: "表达清晰度"
  scale: [1, 10]
```

**Step 2: Write prs/manifest.yaml**

Draft PR selection (8 hard + 2 soft), all Type A for hard score. User will finalize.

```yaml
prs:
  # === 硬分：引入 bug 的原始 PR ===
  - id: pr-47154
    url: "https://github.com/milvus-io/milvus/pull/47154"
    category: hard
    title: "Fast finish compaction when L0Comp hit zero L1/L2"
    difficulty: L2
    language: go
    known_bugs:
      - id: save-segment-meta
        description: |
          优化 compaction 提前退出逻辑时，改变了 saveSegmentMeta 的签名和语义。
          当没有 L1/L2 segment 时返回空 plan 而非 error，但下游处理路径
          未适配这一变化，导致 segment 元数据处理错误。

  - id: pr-24358
    url: "https://github.com/milvus-io/milvus/pull/24358"
    category: hard
    title: "Retrieve segments concurrently"
    difficulty: L2
    language: go
    known_bugs:
      - id: concurrent-segment-bug
        description: |
          将 segment 获取改为并发执行时引入竞争条件。多个 goroutine 同时访问
          共享状态但缺少适当的同步机制，导致数据不一致。

  - id: pr-44324
    url: "https://github.com/milvus-io/milvus/pull/44324"
    category: hard
    title: "Encode cluster id in auto id"
    difficulty: L2
    language: go
    known_bugs:
      - id: id-format-compat
        description: |
          修改自增 ID 生成格式以编码 cluster ID，但未处理与旧格式 ID 的
          兼容性问题，导致已有数据的 ID 查找失败。

  - id: pr-43937
    url: "https://github.com/milvus-io/milvus/pull/43937"
    category: hard
    title: "Use folly::SharedMutex preventing starvation"
    difficulty: L2
    language: cpp
    known_bugs:
      - id: folly-mutex-semantic
        description: |
          将 std::shared_mutex 批量替换为 folly::SharedMutex，但 folly 版本
          的锁语义与标准库不同（如升级/降级行为），导致并发问题。

  - id: pr-43542
    url: "https://github.com/milvus-io/milvus/pull/43542"
    category: hard
    title: "DataCodec release ownership of input_data"
    difficulty: L1
    language: cpp
    known_bugs:
      - id: lifetime-issue
        description: |
          删除了 3 行释放 input_data 所有权的代码，导致对象生命周期管理
          出错，input_data 被提前释放，后续访问导致 use-after-free。

  - id: pr-35720
    url: "https://github.com/milvus-io/milvus/pull/35720"
    category: hard
    title: "Avoid coexistence of old coordinator and new node"
    difficulty: L2
    language: go
    known_bugs:
      - id: blocking-logic
        description: |
          在滚动升级时添加阻塞逻辑防止新旧节点共存，但阻塞条件设计有误，
          在特定升级顺序下导致节点启动永久阻塞。

  - id: pr-46440
    url: "https://github.com/milvus-io/milvus/pull/46440"
    category: hard
    title: "Unify RO node handling to avoid balance channel task stuck"
    difficulty: L3
    language: go
    known_bugs:
      - id: check-stale-wrong-node
        description: |
          统一 checkStale 方法时，对所有 action 类型使用 action.Node() 检查状态。
          但 LeaderAction 的 Node() 返回 worker 节点而非 leader 节点（delegator），
          导致从 RO worker 同步 segment 到 RW delegator 的任务被错误标记为 stale。

  - id: pr-38300
    url: "https://github.com/milvus-io/milvus/pull/38300"
    category: hard
    title: "Query coord stop progress is too slow"
    difficulty: L3
    language: go
    known_bugs:
      - id: qc-stop-regression
        description: |
          优化 10k+ collection 场景下 QueryCoord 关停速度，改动分散在 24 个文件，
          删除了部分必要的清理逻辑，导致关停后资源未正确释放。

  # === 软分：正常优质 PR ===
  - id: pr-47387
    url: "https://github.com/milvus-io/milvus/pull/47387"
    category: soft
    title: "更新向量字段默认 auto index"
    difficulty: null
    language: go
    known_bugs: []

  - id: pr-47032
    url: "https://github.com/milvus-io/milvus/pull/47032"
    category: soft
    title: "支持 truncate collection REST API v2"
    difficulty: null
    language: go
    known_bugs: []
```

**Step 3: Commit**

```bash
git add config.yaml prs/manifest.yaml && git commit -m "feat: add config and PR manifest"
```

---

### Task 3: Prompt Templates

**Files:**
- Create: `prompts/hard_judge.txt`
- Create: `prompts/soft_judge.txt`

**Step 1: Write hard judge prompt**

```
You are a code review evaluation judge. Determine if a code review identified a specific known bug.

## Known Bug
{bug_description}

## Code Review Output
{review_content}

## Task
Did this code review identify the bug described above? The reviewer may use different words, but the core issue must match.

Respond in JSON only:
{{
  "verdict": "YES" or "NO",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "reasoning": "Brief explanation (1-2 sentences)"
}}
```

**Step 2: Write soft judge prompt**

```
You are a code review quality judge. Score the following anonymized reviews for the same PR.

## PR
{pr_title}
{pr_url}

## Anonymized Reviews

{anonymized_reviews}

## Scoring Dimensions (1-10 each)
- accuracy: Are identified issues real? Are important issues missed?
- actionability: Are suggestions specific and implementable?
- depth: Does the review show deep understanding?
- clarity: Is the review well-organized and easy to follow?

Respond in JSON only:
{{
  "scores": {{
    {score_template}
  }},
  "reasoning": "Brief rationale for your scoring (2-3 sentences)"
}}
```

**Step 3: Commit**

```bash
git add prompts/ && git commit -m "feat: add judge prompt templates"
```

---

### Task 4: Common Utilities (scripts/common.py)

**Files:**
- Create: `scripts/common.py`

Core shared code: config loading, progress printing, Magpie config generation, result checking, CLI invocation.

Key functions:
- `load_config()` → dict
- `load_manifest()` → dict
- `print_progress(phase, index, total, pr_id, model_id, status, elapsed=None)`
- `generate_magpie_config(models, rounds, check_convergence, output_path)` → path
- `result_exists(path)` → bool
- `run_magpie(pr_url, config_path, output_path, format="json")` → subprocess result
- `run_judge(model_config, prompt)` → string response
- `parse_judge_json(response)` → dict

**Step 1: Implement**

Full implementation with subprocess calls, YAML generation, progress formatting.
CLI invocation: read `judge_cmd` from config, substitute `{prompt}` placeholder via temp file + stdin approach (prompts can be very long, avoid shell quoting issues).

**Step 2: Commit**

```bash
git add scripts/ && git commit -m "feat: add common utilities"
```

---

### Task 5: Hard Score Pipeline (scripts/hard_score.py)

**Files:**
- Create: `scripts/hard_score.py`

**Logic:**
```python
def run_hard_score(config, manifest, pr_filter=None, model_filter=None, force=False):
    hard_prs = [p for p in manifest['prs'] if p['category'] == 'hard']
    models = config['models']
    concurrency = config['execution']['concurrency']

    # Build task list: each (pr, model) pair
    tasks = []
    for pr in hard_prs:
        for model in models:
            output = f"results/hard/{pr['id']}/{model['id']}.json"
            if not force and result_exists(output):
                print_progress("硬分", ..., "跳过")
                continue
            tasks.append((pr, model, output))

    print(f"[硬分] 开始: {len(tasks)} 个任务, 并发 {concurrency}")

    # Execute with thread pool
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (pr, model, output) in enumerate(tasks):
            # Generate single-reviewer magpie config
            magpie_cfg = generate_magpie_config(
                models=[model],
                rounds=config['hard_score']['rounds'],
                check_convergence=False
            )
            future = pool.submit(run_magpie, pr['url'], magpie_cfg, output)
            futures[future] = (i, pr, model, output)

        for future in as_completed(futures):
            i, pr, model, output = futures[future]
            elapsed = ...
            print_progress("硬分", i+1, len(tasks), pr['id'], model['id'], "完成", elapsed)

    print(f"[硬分] 全部完成")
```

**Step 1: Implement**
**Step 2: Commit**

---

### Task 6: Soft Score Pipeline (scripts/soft_score.py)

**Files:**
- Create: `scripts/soft_score.py`

**Logic:**
```python
def run_soft_score(config, manifest, pr_filter=None, force=False):
    all_prs = manifest['prs']  # hard + soft PRs all participate
    models = config['models']
    concurrency = config['execution']['concurrency']

    tasks = []
    for pr in all_prs:
        output = f"results/soft/{pr['id']}/debate.json"
        if not force and result_exists(output):
            print_progress("软分", ..., "跳过")
            continue
        tasks.append((pr, output))

    print(f"[软分] 开始: {len(tasks)} PRs, 并发 {concurrency}")

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for i, (pr, output) in enumerate(tasks):
            magpie_cfg = generate_magpie_config(
                models=models,
                rounds=config['soft_score']['rounds'],
                check_convergence=config['soft_score']['check_convergence']
            )
            future = pool.submit(run_magpie, pr['url'], magpie_cfg, output)
            futures[future] = (i, pr, output)

        for future in as_completed(futures):
            # print progress
```

**Step 1: Implement**
**Step 2: Commit**

---

### Task 7: Judge Pipeline (scripts/judge.py)

**Files:**
- Create: `scripts/judge.py`

Two entry points: `run_hard_judge()` and `run_soft_judge()`.

**Hard judge logic:**
```python
def run_hard_judge(config, manifest, force=False):
    hard_prs = [p for p in manifest['prs'] if p['category'] == 'hard']
    models = config['models']
    prompt_template = read_file("prompts/hard_judge.txt")

    for pr in hard_prs:
        for bug in pr['known_bugs']:
            for reviewed_model in models:
                review_path = f"results/hard/{pr['id']}/{reviewed_model['id']}.json"
                review_content = extract_review_content(review_path)

                verdicts = {}
                for judge_model in models:
                    output = f"results/judge/hard/{pr['id']}/{reviewed_model['id']}_by_{judge_model['id']}.json"
                    if not force and result_exists(output):
                        continue

                    prompt = prompt_template.format(
                        bug_description=bug['description'],
                        review_content=review_content
                    )
                    response = run_judge(judge_model, prompt)
                    result = parse_judge_json(response)
                    save_json(output, result)

                    verdict = result['verdict']
                    print(f"[裁判-硬分] {pr['id']}: {reviewed_model['id']}的review → 裁判{judge_model['id']} ... {verdict}")
                    verdicts[judge_model['id']] = verdict

                # Majority vote
                yes_count = sum(1 for v in verdicts.values() if v == 'YES')
                final = 'YES' if yes_count > len(verdicts) / 2 else 'NO'
                print(f"[裁判-硬分] {pr['id']} × {reviewed_model['id']}: 最终判定 {final} ({yes_count}/{len(verdicts)})")
```

**Soft judge logic:**
```python
def run_soft_judge(config, manifest, force=False):
    all_prs = manifest['prs']
    models = config['models']
    prompt_template = read_file("prompts/soft_judge.txt")

    for pr in all_prs:
        debate_path = f"results/soft/{pr['id']}/debate.json"
        if not os.path.exists(debate_path):
            continue

        # Extract and anonymize reviews
        debate = load_json(debate_path)
        # Random mapping: model_id -> "Reviewer A/B/C"
        mapping = create_anonymous_mapping(models)
        anonymized = anonymize_reviews(debate, mapping)

        # Save mapping for later de-anonymization
        save_json(f"results/judge/soft/{pr['id']}/mapping.json", mapping)

        for judge_model in models:
            output = f"results/judge/soft/{pr['id']}/{judge_model['id']}.json"
            if not force and result_exists(output):
                continue

            prompt = format_soft_prompt(prompt_template, pr, anonymized, models)
            response = run_judge(judge_model, prompt)
            result = parse_judge_json(response)
            save_json(output, result)

            # Print scores
            print(f"[裁判-软分] {pr['id']} → 裁判{judge_model['id']} ...")
            for reviewer_label, scores in result['scores'].items():
                scores_str = ", ".join(f"{d}={s}" for d, s in scores.items())
                print(f"  {reviewer_label}: {scores_str}")
```

**Step 1: Implement hard judge**
**Step 2: Implement soft judge**
**Step 3: Commit**

---

### Task 8: Report Generator (scripts/report.py)

**Files:**
- Create: `scripts/report.py`

**Logic:**
- Scan all results directories
- Hard score: aggregate YES/NO per model per difficulty level → CSV + summary
- Soft score: aggregate dimension scores per model → CSV + summary
- Judge bias: compare self-score vs other-score per judge → CSV
- Print summary tables to stdout
- Save raw data to `results/reports/`

Output files:
```
results/reports/
├── hard_scores.csv          # model, pr_id, difficulty, found, vote_detail
├── soft_scores.csv          # model, pr_id, judge, dimension, score
├── hard_summary.json        # per-model bug detection rates by difficulty
├── soft_summary.json        # per-model average scores by dimension
├── judge_bias.json          # per-judge self vs other scoring gap
└── summary.txt              # human-readable summary printed to stdout
```

**Step 1: Implement**
**Step 2: Commit**

---

### Task 9: Main Entry Point (run.py)

**Files:**
- Create: `run.py`

```python
import argparse
from scripts.common import load_config, load_manifest
from scripts.hard_score import run_hard_score
from scripts.soft_score import run_soft_score
from scripts.judge import run_hard_judge, run_soft_judge
from scripts.report import run_report

def main():
    parser = argparse.ArgumentParser(description='AI Code Review Arena')
    parser.add_argument('--hard', action='store_true', help='只跑硬分')
    parser.add_argument('--soft', action='store_true', help='只跑软分')
    parser.add_argument('--judge', action='store_true', help='只跑裁判')
    parser.add_argument('--report', action='store_true', help='只生成报告')
    parser.add_argument('--pr', help='只跑指定 PR')
    parser.add_argument('--model', help='只跑指定模型')
    parser.add_argument('--force', action='store_true', help='强制重跑')
    args = parser.parse_args()

    config = load_config()
    manifest = load_manifest()

    run_all = not (args.hard or args.soft or args.judge or args.report)

    if run_all or args.hard:
        run_hard_score(config, manifest, args.pr, args.model, args.force)
    if run_all or args.soft:
        run_soft_score(config, manifest, args.pr, args.force)
    if run_all or args.judge:
        run_hard_judge(config, manifest, args.force)
        run_soft_judge(config, manifest, args.force)
    if run_all or args.report:
        run_report(config, manifest)

if __name__ == '__main__':
    main()
```

**Step 1: Implement**
**Step 2: Commit**

---

### Task 10: README

**Files:**
- Create: `README.md`

Quick start, prerequisites, usage examples, config explanation.

**Step 1: Write**
**Step 2: Commit**

---

## Task Dependencies

```
Task 1 (scaffolding)
  ├── Task 2 (config) ─────┐
  ├── Task 3 (prompts) ────┤
  └── Task 4 (common.py) ──┤
       ├── Task 5 (hard) ──┤
       ├── Task 6 (soft) ──┤
       ├── Task 7 (judge) ─┤
       ├── Task 8 (report) ┤
       └── Task 9 (run.py) ┘
            └── Task 10 (README)
```

Tasks 2, 3 can run in parallel.
Tasks 5, 6, 7, 8 can run in parallel (all depend on Task 4).
Task 9 depends on 5-8.
Task 10 can run anytime.
