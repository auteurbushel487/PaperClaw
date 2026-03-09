---
name: paper-relevance-scorer
description: 对搜索到的论文进行相关性评分（0-10分），采用三步式 Agent 编排：脚本准备上下文→Agent LLM 打分→脚本后处理。当用户说"给这些论文打分"、"评估论文相关性"时触发。
---

# Paper Relevance Scorer — 三步式相关性打分（Agent 编排模式核心）

## 概述

本 Skill 是 Agent 编排模式的**核心体现**。评分流程分为三步：

1. **脚本准备上下文** → 生成 `skill2_scoring_context.json`
2. **Agent LLM 打分** → 逐篇评分，写入 `skill2_agent_raw_output.json`
3. **脚本后处理** → 解析、加分、分区，输出 `skill2_scored_results.json`

---

## ⚠️ 关键规则

1. **严格 JSON 输出**：Agent 打分结果必须为纯 JSON 数组，**禁止任何 Markdown 修饰符、寒暄语、解释性文字**。
2. **逐篇评分**：对每篇论文独立评分，输出评分分数、评分理由和语义标签。
3. **容错兜底**：如果 Agent 输出格式异常，后处理脚本会自动容错提取 JSON 并降级处理。

---

## 工作流程

### Step 1: 准备打分上下文（脚本）

```bash
python $PAPER_AGENT_ROOT/scripts/scorer_utils.py --prepare --run-id {run_id}
```

脚本将：
1. 从 `seed_papers.json` 中选取 `role: "foundational"` 的论文（≤3 篇）
2. 构建 few-shot 正样本（title + abstract + 高分示例）
3. 从 `profile.yaml` 读取研究方向描述
4. 读取 `skill1_search_results.json` 中的论文列表
5. 输出 `skill2_scoring_context.json`（含 papers 列表、few-shot 示例、完整打分 prompt）

### Step 2: Agent 执行打分（你来做）

请读取 `pipeline_data/{run_id}/skill2_scoring_context.json` 中的打分上下文。

对列表中的每篇论文，根据以下信息进行 0-10 分的相关性评分：

**评分维度：**
- 与用户研究方向（参见 profile.yaml 中的 research_description）的直接相关度
- 方法论创新性
- 与核心论文（seed_papers.json 中 role 为 foundational 的论文）的技术关联度
- 潜在实验价值（对比基线、技术复用）

**评分标准校准（Few-shot 正样本）：**

以下核心论文应被视为 **9-10 分** 的标杆。具体示例见 `skill2_scoring_context.json` 中的 `fewshot_examples` 字段。

**输出格式要求 — 极其重要：**

将评分结果写入 `pipeline_data/{run_id}/skill2_agent_raw_output.json`。

直接输出 JSON 数组。每个元素为一篇论文的打分结果：

```
[
  {
    "arxiv_id": "2603.01234",
    "relevance_score": 8,
    "scoring_rationale": "该论文提出了与用户研究方向高度相关的新方法...",
    "tags": ["relevant_method", "novel_approach"]
  },
  {
    "arxiv_id": "2603.05678",
    "relevance_score": 3,
    "scoring_rationale": "该论文关注传统协同过滤，与生成式推荐方向关联较弱...",
    "tags": ["collaborative_filtering"]
  }
]
```

**禁止**包含任何 Markdown 代码块修饰符（如 ` ```json `）
**禁止**包含任何解释性文字、过渡句、寒暄语
**禁止**在 JSON 之前或之后附加任何非 JSON 内容

### Step 3: 后处理（脚本）

```bash
python $PAPER_AGENT_ROOT/scripts/scorer_utils.py --postprocess --run-id {run_id}
```

脚本将：
1. 使用容错 JSON 提取器解析 `skill2_agent_raw_output.json`
2. 对白名单作者论文标注 `is_whitelist_author: true` 并 +1 分
3. 对顶会论文标注 `is_top_venue: true` 并 +1 分
4. 按评分排序，分为三个区间（高分区 ≥7 / 边缘区 4-6 / 低分区 ≤3）
5. 输出 `skill2_scored_results.json`

---

## 输出文件

| 文件 | 说明 | 产出步骤 |
|------|------|---------|
| `skill2_scoring_context.json` | 评分上下文（papers + few-shot + prompt） | Step 1 (--prepare) |
| `skill2_agent_raw_output.json` | Agent LLM 评分原始输出 | Step 2 (Agent) |
| `skill2_scored_results.json` | 最终评分结果（分三区） | Step 3 (--postprocess) |

### skill2_scored_results.json 格式

```json
{
  "high": [
    {
      "arxiv_id": "2603.01234",
      "title": "...",
      "relevance_score": 9,
      "scoring_rationale": "...",
      "tags": ["generative_rec"],
      "is_whitelist_author": true,
      "is_top_venue": false
    }
  ],
  "edge": [...],
  "low": [...]
}
```

---

## 降级策略

| 场景 | 处理 |
|------|------|
| Agent 输出夹带 Markdown 修饰符 | 容错 JSON 提取器自动剥离 |
| Agent 输出完全无法解析 | 所有论文赋予默认分数 5 + `scoring_failed: true` |
| 单篇论文缺少必要字段 | 填充默认值（score=5, rationale="N/A"） |
| Agent 遗漏某些论文 | 遗漏论文赋予默认分数 5 + `scoring_failed: true` |
