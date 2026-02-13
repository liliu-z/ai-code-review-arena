# AI Code Review Arena

多 AI 模型代码审查竞技场：用真实 Milvus PR 做擂台，让 AI 模型互相 review、辩论、匿名互评打分。

## Quick Start

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 确保已安装
magpie --version    # Magpie (npm install -g magpie)
claude --version    # Claude Code CLI
gemini --version    # Gemini CLI
codex --version     # Codex CLI

# 3. 一键运行
python run.py
```

## 评测流程

```
python run.py
 ├─ 1. 硬分  每模型独立 review 引入 bug 的原始 PR (-r 1)
 ├─ 2. 软分  全模型辩论每个 PR (多轮对抗)
 ├─ 3. 裁判  硬分: 三方投票判 bug 是否被发现
 │          软分: 匿名互评 4 维度打分 (1-10)
 └─ 4. 报告  排行榜 + 裁判偏见分析
```

## 分步运行

```bash
python run.py --hard             # 只跑硬分
python run.py --soft             # 只跑软分
python run.py --judge            # 只跑裁判
python run.py --report           # 只生成报告
python run.py --pr pr-47154      # 只跑指定 PR
python run.py --model claude     # 只跑指定模型
python run.py --force            # 强制重跑 (忽略已有结果)
```

支持断点续跑：已完成的任务会自动跳过。

## 配置

### config.yaml — 模型与参数

```yaml
models:
  - id: claude
    magpie_provider: claude-code
    judge_cmd: "claude -p '{prompt_file}'"
  - id: gemini
    magpie_provider: gemini-cli
    judge_cmd: "gemini '{prompt_file}'"
  - id: codex
    magpie_provider: codex-cli
    judge_cmd: "codex exec '{prompt_file}'"
```

**添加新模型**：只需加一条，填 `magpie_provider` 和 `judge_cmd`。

### prs/manifest.yaml — PR 数据集

```yaml
prs:
  - id: pr-47154
    url: "https://github.com/milvus-io/milvus/pull/47154"
    category: hard        # hard = 硬分, soft = 软分
    difficulty: L2         # L1/L2/L3
    known_bugs:
      - id: bug-name
        description: "bug 描述，用于裁判判定"
```

## 评分体系

### 硬分：Bug 检出率

- 每个模型独立 review 引入 bug 的原始 PR
- 三个模型投票判定是否发现已知 bug（多数决）
- 按 L1/L2/L3 难度分组统计

### 软分：Review 质量

- 全模型辩论后，提取每个模型的 review
- 匿名化 (Reviewer A/B/C)，随机映射
- 每个模型做裁判，对所有匿名 review 打分
- 4 个维度：准确性、可操作性、深度、清晰度 (1-10)

## 结果目录

```
results/
├── hard/<pr-id>/<model-id>.json          # 独立 review 原始输出
├── soft/<pr-id>/debate.json              # 辩论原始输出
├── judge/hard/<pr-id>/...                # 硬分裁判结果
├── judge/soft/<pr-id>/...                # 软分裁判结果
└── reports/
    ├── hard_scores.csv                   # 硬分明细
    ├── soft_scores.csv                   # 软分明细
    ├── hard_summary.json                 # 硬分汇总
    ├── soft_summary.json                 # 软分汇总
    ├── judge_bias.json                   # 裁判偏见分析
    └── summary.txt                       # 人类可读汇总
```

## 前置依赖

- Python 3.10+
- [Magpie](https://github.com/liliu-z/magpie) (`npm install -g magpie`)
- Claude Code CLI (`claude`)
- Gemini CLI (`gemini`)
- Codex CLI (`codex`)
- GitHub CLI (`gh`) — Magpie 用它拉 PR diff
