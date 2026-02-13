# AI Code Review Arena - 设计文档

## 定位

一个可复现的评测 repo，用真实 Milvus PR 做擂台，让多个 AI 模型互相 review、辩论、匿名互评。基于 [Magpie](https://github.com/liliu-z/magpie)（全局安装），本 repo 是纯编排层：Python 脚本 + YAML 配置 + 数据。

**核心需求：**
- 一键跑完全部评测流程
- 新模型接入成本极低（加几行配置）
- 断点续跑（跑一半挂了不用从头来）

---

## 项目结构

```
AI-CodeReview-Arena/
├── config.yaml                # 主配置：模型、执行参数、评分设置
├── run.py                     # 一键入口
├── prs/
│   └── manifest.yaml          # PR 数据集：URL、已知bug、难度
├── scripts/
│   ├── hard_score.py          # 硬分流水线：每模型独立 review
│   ├── soft_score.py          # 软分流水线：全模型辩论
│   ├── judge.py               # 裁判：硬分判定 + 软分打分
│   └── report.py              # 结果聚合 + 可视化
├── prompts/
│   ├── hard_judge.txt         # 硬分裁判 prompt 模板
│   └── soft_judge.txt         # 软分裁判 prompt 模板
├── magpie_configs/            # 运行时生成的 Magpie 配置（gitignore）
├── results/                   # 输出目录（gitignore）
│   ├── hard/                  # results/hard/<pr-id>/<model-id>.json
│   ├── soft/                  # results/soft/<pr-id>/debate.json
│   ├── judge/                 # results/judge/hard/<pr-id>/<judge-id>.json
│   │                          # results/judge/soft/<pr-id>/<judge-id>.json
│   └── reports/               # 最终报告
└── README.md
```

---

## 配置设计

### config.yaml

```yaml
models:
  - id: claude
    magpie_provider: claude-code
    judge_cmd: "claude -p '{prompt}'"
  - id: gemini
    magpie_provider: gemini-cli
    judge_cmd: "gemini -p '{prompt}'"
  - id: codex
    magpie_provider: codex-cli
    judge_cmd: "codex -p '{prompt}'"

execution:
  concurrency: 4               # 同时跑几个任务

hard_score:
  rounds: 1                    # 独立 review，不辩论

soft_score:
  rounds: 3                    # 辩论轮数
  check_convergence: true

judge:
  dimensions:
    - id: accuracy
      name: 问题识别准确性
    - id: actionability
      name: 建议可操作性
    - id: depth
      name: 分析深度
    - id: clarity
      name: 表达清晰度
  scale: [1, 10]
```

### prs/manifest.yaml

```yaml
prs:
  # === 硬分考题：引入 bug 的原始 PR（Type A）===
  - id: pr-47154
    url: https://github.com/milvus-io/milvus/pull/47154
    category: hard
    title: "Fast finish compaction when L0Comp hit zero L1/L2"
    difficulty: L2
    language: go
    size: "+87/-36"
    known_bugs:
      - id: save-segment-meta
        description: |
          优化 compaction 提前退出逻辑时，改变了 saveSegmentMeta 的签名和语义。
          当没有 L1/L2 segment 时返回空 plan 而非 error，但下游处理路径
          未适配这一变化，导致 bug。
        fix_pr: "https://github.com/milvus-io/milvus/pull/XXXXX"

  # 需覆盖 L1 / L2 / L3，从 Type A 候选中选 ~8 个
  # ...

  # === 软分考题（仅参与辩论 + 互评，无已知 bug）===
  - id: pr-47387
    url: https://github.com/milvus-io/milvus/pull/47387
    category: soft
    title: "更新向量字段默认 auto index"
    difficulty: null
    language: go
    size: "+192/-3"
    known_bugs: []
```

---

## 执行流水线

### 总览

```
run.py
 ├─ 1. hard_score.py   每模型独立 review 每个硬分 PR
 ├─ 2. soft_score.py   全模型辩论每个 PR（硬分 + 软分 PR 全部参与）
 ├─ 3. judge.py        硬分判定 + 软分打分
 └─ 4. report.py       聚合结果，生成报告
```

可通过参数控制只跑某一步：

```bash
python run.py                    # 全部
python run.py --hard             # 只跑硬分
python run.py --soft             # 只跑软分
python run.py --judge            # 只跑裁判
python run.py --report           # 只生成报告
python run.py --pr pr-33820      # 只跑指定 PR
python run.py --model claude     # 只跑指定模型
```

### 1. 硬分流水线（hard_score.py）

**目标**：每个模型独立 review 每个引入 bug 的原始 PR，看能否发现已知 bug。

```
对每个硬分 PR × 每个模型（并发度由 execution.concurrency 控制）：
  1. 生成临时 Magpie 配置（只含该模型作为唯一 reviewer）
  2. 调用: magpie review <PR-URL> -r 1 -c <config> -o <output> --format json
  3. 存结果到 results/hard/<pr-id>/<model-id>.json
```

- 每次只放一个 reviewer，防止模型间信息泄露
- Magpie 的全部能力（上下文收集、代码分析）正常使用，不做任何阉割

### 2. 软分流水线（soft_score.py）

**目标**：全模型辩论每个 PR，产生辩论记录（同时也是博客素材）。

```
对每个 PR（硬分 + 软分全部参与）：
  1. 生成 Magpie 配置（所有模型作为 reviewers）
  2. 调用: magpie review <PR-URL> -r 3 -c <config> -o <output> --format json
  3. 存结果到 results/soft/<pr-id>/debate.json
```

- 使用博客设计文档中的强化辩论 prompt
- 辩论记录一举两得：评测数据 + 博客内容素材

### 3. 裁判（judge.py）

统一处理硬分和软分的裁判任务，通过直接调用各模型 CLI。

#### 硬分裁判

```
对每个硬分 PR × 每个被评模型的 review 输出：
  让所有模型各自独立判定：
    输入：review 原文 + 已知 bug 描述（来自 manifest.yaml）
    问题："该 review 是否发现了这个 bug？回答 YES/NO，附简短理由"
  最终判定：多数投票（>50% 判 YES 即认定为"发现"）
  存到 results/judge/hard/<pr-id>/<model-reviewed>_by_<judge-id>.json
```

#### 软分裁判

```
对每个 PR 的辩论结果：
  1. 从 debate.json 提取每个模型的 review 内容
  2. 匿名化：随机分配 Reviewer A/B/C（每个 PR 映射不同）
  3. 每个模型开新 session 担任裁判：
     输入：匿名化后的所有 review
     要求：对每个 Reviewer 打 4 维度分（1-10）
     输出：结构化 JSON 评分
  4. 存到 results/judge/soft/<pr-id>/<judge-id>.json
```

- 匿名映射随机打乱，防止裁判通过固定顺序猜测身份
- 裁判也给自己的匿名 review 打分（裁判偏见本身是博客素材）

### 4. 报告生成（report.py）

```
聚合所有 results/ 下的数据，生成：
  ├── 硬分排行榜：bug 检出率，按 L1/L2/L3 分组柱状图数据
  ├── 软分排行榜：4 维度雷达图数据
  ├── 裁判偏见分析：自评 vs 评他人的分差
  ├── 原始数据汇总表（CSV/JSON）
  └── 博客素材索引：标记最精彩的辩论片段
存到 results/reports/
```

---

## 并发控制

- 粒度：每次 Magpie 调用 / 每次 CLI 裁判调用 = 一个任务单元
- 控制：`concurrent.futures.ThreadPoolExecutor(max_workers=config.execution.concurrency)`
- 默认：4 个并发
- 硬分 8 PR × 3 模型 = 24 个任务，4 并发 ≈ 6 批跑完
- 裁判任务数更多但单次耗时短

---

## 进度输出

全程打印进度，让用户知道程序还活着：

```
[硬分] 开始: 8 PRs × 3 模型 = 24 个任务, 并发 4
[硬分] [1/24] pr-33820 × claude ... 启动
[硬分] [2/24] pr-33820 × gemini ... 启动
[硬分] [3/24] pr-33820 × codex  ... 启动
[硬分] [4/24] pr-47154 × claude ... 启动
[硬分] [1/24] pr-33820 × claude ... 完成 (2m34s)
[硬分] [5/24] pr-47154 × gemini ... 启动
...
[硬分] 全部完成: 24/24, 耗时 18m12s

[软分] 开始: 10 PRs, 并发 4
[软分] [1/10] pr-33820 辩论中 (3 模型, 3 轮) ... 启动
...
[软分] [1/10] pr-33820 ... 完成 (5m21s, 第2轮收敛)
...

[裁判-硬分] 开始: 8 PRs × 3 被评 × 3 裁判 = 72 个判定
[裁判-硬分] [1/72] pr-33820: claude的review → 裁判claude ... YES
[裁判-硬分] [2/72] pr-33820: claude的review → 裁判gemini ... YES
[裁判-硬分] [3/72] pr-33820: claude的review → 裁判codex  ... NO
[裁判-硬分] pr-33820 × claude: 最终判定 YES (2/3)
...

[裁判-软分] 开始: 10 PRs × 3 裁判 = 30 个评分
[裁判-软分] [1/30] pr-33820 → 裁判claude ...
  Reviewer A: 准确性=8, 可操作性=7, 深度=9, 清晰度=8
  Reviewer B: 准确性=6, 可操作性=8, 深度=5, 清晰度=7
  Reviewer C: 准确性=7, 可操作性=6, 深度=7, 清晰度=6
...

[报告] 生成中...
[报告] 完成: results/reports/
```

---

## 断点续跑

每步开始前扫描 `results/` 目录：
- 如果 `results/hard/<pr-id>/<model-id>.json` 已存在且非空 → 跳过该组合
- 如果 `results/soft/<pr-id>/debate.json` 已存在 → 跳过该 PR
- 如果 `results/judge/...` 已存在 → 跳过该裁判任务

跳过时也打印：`[硬分] [跳过] pr-33820 × claude (已有结果)`

支持 `--force` 参数强制重跑。

---

## 新模型接入流程

1. 确认 Magpie 已支持该模型的 provider
2. 在 `config.yaml` 的 `models` 中加一条：

```yaml
  - id: new-model
    magpie_provider: new-model-cli
    judge_cmd: "new-model -p '{prompt}'"
```

3. 运行 `python run.py`，完事

---

## 前置依赖

- Python 3.10+
- Magpie 全局安装（`npm install -g magpie`）
- 各模型 CLI 已安装并认证（claude、gemini、codex）
- `gh` CLI（GitHub CLI，Magpie 用它拉 PR）
