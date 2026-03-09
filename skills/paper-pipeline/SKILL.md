---
name: paper-pipeline
description: 编排论文阅读全流程（搜索→打分→仲裁→精读→代码评估→知识沉淀），通过 Agent 分步编排模式调度各子 Skill，支持断点续跑和对话式人工仲裁。当用户说"开始论文日常巡检"、"继续之前的巡检"、"运行论文 Pipeline"时触发。
---

# Paper Pipeline — Agent 分步编排（Path B）

你是论文阅读 Pipeline 的**编排者和大脑**。你将按以下步骤逐个调用脚本工具完成完整流程，并在需要 LLM 智能的环节（评分、精读、Idea 生成）直接用自身能力完成。

---

## ⚠️ 关键规则

1. **你是编排者**：按下方步骤依次执行，每步检查返回结果后再进行下一步。
2. **数据通过文件系统传递**：所有中间数据存放在 `pipeline_data/{run_id}/` 目录下，**禁止在对话上下文中传递大量数据**。
3. **结构化 JSON 输出**：每个脚本调用都返回 JSON，你需要解析 `status` 字段判断成功/失败。
4. **断点续跑**：如果用户说"继续之前的巡检"，先执行 `--status` 查看进度，从未完成的步骤恢复。

---

## 首次运行 — "开始论文日常巡检"

### Step 0: 初始化 Pipeline

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step init
```

记录返回的 `run_id`，后续所有步骤都使用此 `run_id`。

### Step 1: 种子初始化 + 论文搜索

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step seed+search --run-id {run_id}
```

检查返回结果中的搜索统计。如果 `search_new_increment` 为 0，告知用户"本次没有新增论文"并跳到 Step 7 生成摘要。

### Step 2: 准备评分上下文

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step prepare-scoring --run-id {run_id}
```

脚本会生成 `skill2_scoring_context.json`，包含待评分论文列表和 few-shot 校准示例。

### Step 3: Agent LLM 评分（你来做）

**请读取** `pipeline_data/{run_id}/skill2_scoring_context.json` 文件内容。

根据文件中的评分提示（prompt）和 few-shot 校准示例，对每篇论文进行 0-10 分的相关性评分。

**评分维度：**
- 与用户研究方向（参见 profile.yaml 中的 research_description）的直接相关度
- 方法论创新性
- 与核心论文（seed_papers.json 中 role 为 foundational 的论文）的技术关联度
- 潜在实验价值

**输出要求 — 极其重要：**

将评分结果以**纯 JSON 数组**写入 `pipeline_data/{run_id}/skill2_agent_raw_output.json`。

格式：
```json
[
  {
    "arxiv_id": "2603.01234",
    "relevance_score": 8,
    "scoring_rationale": "该论文提出了与用户研究方向高度相关的新方法...",
    "tags": ["relevant_method", "novel_approach"]
  }
]
```

**禁止**：Markdown 修饰符、寒暄语、解释性文字。**仅输出纯 JSON**。

### Step 4: 后处理评分结果

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step postprocess-scoring --run-id {run_id}
```

脚本会解析你的评分输出、应用白名单/顶会加分、按评分排序分三区（高分/边缘/低分）。

向用户报告打分统计：高分 M 篇、边缘 K 篇、低分 L 篇。

### Step 5: 人工审阅（对话式交互）

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step human-review-init --run-id {run_id}
```

如果返回 `skipped: true`（无边缘论文），直接跳到 Step 6。

如果返回 `waiting_for_human: true`，将 `compact_cards` 字段的内容展示给用户，请求审阅。

**等待用户回复**：用户会回复类似 "accept 1, reject 2" 或 "accept 论文A, reject 论文B" 的指令。

**解析用户回复**后，构造 JSON 数组调用：

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step human-review-decide --run-id {run_id} --decisions '[{"arxiv_id":"2603.01234","decision":"accept"},{"arxiv_id":"2603.05678","decision":"reject"}]'
```

### Step 6: 深度精读（每篇论文独立 Session）

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step deep-parse --run-id {run_id}
```

脚本会尝试查找已有的 card.md 并提取结构化字段。如果某篇论文还没有 card.md（`needs_reading > 0`），你应该：

1. 为每篇缺少 card.md 的论文通过 `sessions_spawn` 创建独立 Session
2. 触发 `read-arxiv-paper` Skill 精读
3. 待全部精读完成后，重新运行 `--step deep-parse` 提取结构化字段

### Step 6.5: 代码仓库评估

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step repo-eval --run-id {run_id}
```

脚本会从论文文本中提取 GitHub 链接，查询 GitHub API 获取仓库元信息，并评估集成成本。

### Step 6.8: 知识沉淀与 Idea 生成

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step knowledge-sync --run-id {run_id}
```

脚本会将论文分析结果同步到 `paper_index.json`，并生成 Idea 上下文文件 `skill6_idea_context.json`。

你应该：
1. 读取 `pipeline_data/{run_id}/skill6_idea_context.json` 中的 prompt
2. 使用 LLM 能力生成 2-3 个研究 Idea
3. 调用脚本保存：
   ```bash
python $PAPER_AGENT_ROOT/scripts/knowledge_sync.py --save-ideas --run-id {run_id} --ideas-text "..."
   ```

> **建议**：在全新的干净 Session 中执行（通过 `sessions_spawn`），确保有充足的上下文窗口。

### Step 7: 生成运行摘要

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --step summary --run-id {run_id}
```

将摘要中的统计数据以友好格式报告给用户：
- 搜索了 N 篇 → 去重后增量 I 篇
- 高分 M / 边缘 K / 低分 L
- 捞回 P 篇
- 精读 Q 篇 → 有代码 R 篇
- 生成 Idea T 个

---

## 断点续跑 — "继续之前的巡检"

1. 先查看状态：

```bash
python $PAPER_AGENT_ROOT/scripts/pipeline_runner.py --status
```

2. 根据 `skill_statuses` 判断哪些步骤已完成，从第一个未完成的步骤恢复执行。

3. 特殊处理：如果看到 `waiting_for_human` 状态：
   - 检查 `pipeline_data/{run_id}/skill3_human_decisions.json` 是否存在
   - 如果存在：执行 `--step human-review-decide`
   - 如果不存在且未超时：提醒用户审阅边缘论文
   - 如果超时：执行 `--step human-review-decide`（使用默认策略）

---

## Session 分组策略

| 步骤 | Session 策略 | 原因 |
|------|-------------|------|
| Step 0-5（init→审阅） | 当前对话 Session | 脚本为主 + Agent 打分 + 对话式审阅 |
| Step 6（deep-parse） | 每篇论文独立 `sessions_spawn` | 精读内容量大，必须隔离 |
| Step 6.5（repo-eval） | 独立 `sessions_spawn` | 中等上下文消耗 |
| Step 6.8（knowledge-sync） | 全新干净 `sessions_spawn` | Idea 生成需充足上下文 |

---

## 输出文件

Pipeline 运行完成后，所有产物位于 `pipeline_data/{run_id}/`：

| 文件 | 说明 |
|------|------|
| `pipeline_state.json` | 状态记录（支持断点续跑） |
| `skill1_search_results.json` | 搜索到的增量新论文 |
| `skill2_scoring_context.json` | 评分上下文（--prepare 产出） |
| `skill2_agent_raw_output.json` | Agent LLM 评分原始输出 |
| `skill2_scored_results.json` | 最终评分结果（--postprocess 产出） |
| `skill3_review_cards.md` | 审阅信息卡片 |
| `skill3_review_pending.json` | 待审阅边缘论文 |
| `skill3_final_selection.json` | 最终选中论文 |
| `skill4_parsed/{arxiv_id}.json` | 结构化精读 JSON |
| `skill5_repo_eval/{arxiv_id}.json` | 代码评估 JSON |
| `run_summary.json` | 运行统计摘要 |
