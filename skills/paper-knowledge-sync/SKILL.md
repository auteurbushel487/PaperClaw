---
name: paper-knowledge-sync
description: 将论文分析结果沉淀到知识库（paper_index.json），检测论文关联关系，并基于跨论文洞察生成研究 Idea 提案。当 paper-pipeline 编排流程执行到 Step 6.8（知识沉淀）时自动触发。
---

# Paper Knowledge Sync — Knowledge Base & Idea Generation

你负责将论文分析结果沉淀到持久化知识库，并基于跨论文洞察生成研究 Idea。

---

## ⚠️ 关键规则

1. **在干净 Session 中执行**：Idea 生成需要充足上下文窗口，通过 `sessions_spawn` 在全新 Session 中执行。
2. **Agent 生成 Idea**：知识同步是确定性脚本完成的，但 Idea 生成是你（Agent LLM）的核心价值。
3. **结合 SOUL 文件**：Idea 生成时参考 `seed_papers.json` + `paper_index.json` + 用户研究方向。

---

## 执行流程

### Step 1: 知识同步（确定性脚本）

将 skill4/skill5 产出同步到全局 `paper_index.json`：

```bash
python $PAPER_AGENT_ROOT/scripts/knowledge_sync.py --sync --run-id {run_id}
```

返回同步统计：
```json
{
  "total_indexed": 42,
  "new_count": 5,
  "updated_count": 0,
  "with_code": 18,
  "with_relations": 12
}
```

### Step 2: 准备 Idea 生成上下文

```bash
python $PAPER_AGENT_ROOT/scripts/knowledge_sync.py --prepare-ideas --run-id {run_id}
```

脚本生成 `pipeline_data/{run_id}/skill6_idea_context.json`，包含：
- 用户研究方向描述
- 种子论文关键信息
- 本次新增论文摘要
- 所有可迁移技术列表
- Idea 生成 prompt

### Step 3: Agent LLM 生成 Idea（你来做）

**请读取** `pipeline_data/{run_id}/skill6_idea_context.json`，使用其中的 prompt 生成 2-3 个研究 Idea 提案。

每个 Idea 需包含：
1. **Title**：简洁描述性标题
2. **Motivation**：为什么值得做（1-2 句）
3. **Key Insight**：跨论文洞察是什么
4. **Approach**：高层方法论（2-3 句）
5. **Feasibility**：Low / Medium / High + 简要理由
6. **Related Papers**：灵感来自哪些论文

### Step 4: 保存 Idea

将生成的 Idea 文本传回脚本保存：

```bash
python $PAPER_AGENT_ROOT/scripts/knowledge_sync.py \
  --save-ideas --run-id {run_id} \
  --ideas-text "## Idea 1: ...\n\n## Idea 2: ..."
```

Idea 保存到 `ideas/{date}_idea_proposal.md`。

---

## 论文关联检测

知识同步时自动检测新论文与已有论文的关联：

| 关联类型 | 说明 |
|---------|------|
| `same_subfield` | 同一研究子领域 |
| `shared_baselines` | 共享对比基线 |
| `shared_techniques` | 共享可迁移技术 |
| `same_authors` | 共同作者 |

关联关系存储在 `paper_index.json` 的 `relations` 字段中。

---

## 输出文件

| 文件 | 说明 |
|------|------|
| `paper_index.json` | 全局论文知识库索引（持久化） |
| `skill6_idea_context.json` | Idea 生成上下文（per-run） |
| `ideas/{date}_idea_proposal.md` | 研究 Idea 提案（持久化） |
