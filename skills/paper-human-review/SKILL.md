---
name: paper-human-review
description: 对边缘分数论文生成信息卡片，支持对话式实时交互（Agent 编排首选）和异步挂起（cron 兜底）。当用户说"人工审阅论文"、"捞回论文"时触发。
---

# Paper Human Review — 对话式交互 + 异步状态机

## 概述

本 Skill 拦截边缘分数（4-6 分）论文，生成信息卡片供人工审阅。支持两种交互模式：

- **对话式实时交互**（Agent 编排模式首选）：Agent 在对话中展示卡片 → 用户回复 accept/reject → Agent 调用 `--chat-decide`
- **异步状态机**（cron 场景兜底）：脚本生成卡片后挂起 → 用户异步编辑 JSON → `--merge` 恢复

---

## ⚠️ 关键规则

1. **对话式交互优先**：在对话中，Agent 应展示审阅卡片并收集用户决策，不挂起 Pipeline。
2. **异步不阻塞**：cron 场景下脚本生成卡片、写入等待状态后**立即 Exit 0 退出**。
3. **三模式脚本**：`--init`（生成卡片）、`--chat-decide`（对话式决策）、`--merge`（文件式决策）。
4. **无边缘论文时跳过**：如果没有边缘论文，直接复制高分论文到输出。

---

## 工作流程

### 对话式交互流程（Agent 编排模式首选）

#### Step 1: 初始化审阅

```bash
python $PAPER_AGENT_ROOT/scripts/human_review.py --init --run-id {run_id}
```

脚本返回 JSON，其中 `compact_cards` 字段包含适合对话展示的精简卡片。

#### Step 2: Agent 展示卡片

在对话中将 `compact_cards` 内容展示给用户，示例：

```
发现 3 篇边缘论文需要您判断：

📄 [1] (5pts) Semantic ID Generation for Recommendation
   ID: 2603.01234 [generative_rec, semantic_id]
   This paper proposes a novel approach to generate semantic...

📄 [2] (4pts) Collaborative Filtering with Transformers
   ID: 2603.05678 [collaborative_filtering]
   We present a transformer-based collaborative filtering...

请回复 accept/reject 决定，例如：
  "accept 1, reject 2"
  或 "accept all" / "reject all"
```

#### Step 3: 收集用户决策

等待用户回复。用户可能以多种方式回复：
- "accept 1, reject 2, accept 3"
- "accept 论文A, reject 论文B"
- "accept all"
- "reject all"

#### Step 4: Agent 解析决策并调用脚本

将用户回复解析为标准 JSON 格式，调用：

```bash
python $PAPER_AGENT_ROOT/scripts/human_review.py --chat-decide '[{"arxiv_id":"2603.01234","decision":"accept"},{"arxiv_id":"2603.05678","decision":"reject"}]' --run-id {run_id}
```

脚本将合并高分论文与捞回论文，输出 `skill3_final_selection.json`。

---

### 异步状态机流程（cron 场景兜底）

#### 默认模式 — 生成卡片并挂起

```bash
python $PAPER_AGENT_ROOT/scripts/human_review.py --init --run-id {run_id}
```

脚本将：
1. 从 `skill2_scored_results.json` 读取边缘区论文
2. 生成信息卡片（Markdown 文件 + stdout 精简格式）
3. 输出 `skill3_review_pending.json`
4. 如果在 cron 模式，更新 `pipeline_state.json` 为 `waiting_for_human`
5. 尝试通过 webhook 通知用户
6. **Exit 0 退出，不阻塞**

#### 用户异步审阅

用户创建 `skill3_human_decisions.json`：

```json
[
  {"arxiv_id": "2603.01234", "decision": "accept", "note": "有启发性"},
  {"arxiv_id": "2603.05678", "decision": "reject", "note": "不太相关"}
]
```

#### --merge 模式 — 恢复并合并

```bash
python $PAPER_AGENT_ROOT/scripts/human_review.py --merge --run-id {run_id}
```

---

## 超时处理

如果超过 `wait_deadline` 仍未收到决策，按 `profile.yaml` 中的策略处理：
- `"discard"`（默认）：只保留高分区论文
- `"accept"`：保留高分区 + 全部边缘论文

```bash
python $PAPER_AGENT_ROOT/scripts/human_review.py --timeout --policy discard --run-id {run_id}
```

---

## 输出文件

| 文件 | 说明 | 产出模式 |
|------|------|---------|
| `skill3_review_cards.md` | 信息卡片（Markdown 格式） | --init |
| `skill3_review_pending.json` | 待审阅边缘论文列表 | --init |
| `skill3_human_decisions.json` | 审阅决策 | --chat-decide / 用户手动 |
| `skill3_final_selection.json` | 最终选中论文列表 | --chat-decide / --merge |

---

## 通知方式

| 通知渠道 | 实现方式 | 优先级 |
|---------|---------|--------|
| 对话展示 | Agent 在对话中展示 `compact_cards` | **首选（Agent 编排模式）** |
| Webhook 通知 | 通过 webhook 推送（cron 场景） | 次选 |
| 本地 Markdown | 写入 `skill3_review_cards.md` | 必须支持（兜底） |
