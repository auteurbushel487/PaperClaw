---
name: paper-deep-parser
description: 对最终选中的论文进行深度精读，从 card.md 中提取结构化字段（sub_field、ID_paradigm、baselines 等）。当 paper-pipeline 编排流程执行到 Step 6（深度精读）时自动触发。
---

# Paper Deep Parser — Knowledge Card Structured Extraction

你负责对最终选中的论文进行深度精读，并从知识卡片中提取结构化信息。

---

## ⚠️ 关键规则

1. **每篇论文使用独立 Session**：通过 `sessions_spawn` 创建，避免上下文污染。
2. **先精读后解析**：先触发 `read-arxiv-paper` 生成 card.md，再调用 `card_parser.py` 提取结构化字段。
3. **N/A 降级**：无法提取的字段填入 `"N/A"`，不要猜测或编造。

---

## 执行流程

### Step 1: 获取待精读论文列表

读取 `pipeline_data/{run_id}/skill3_final_selection.json`，获取所有需要精读的论文。

### Step 2: 逐篇触发精读

对每篇论文：

1. **检查是否已有 card.md**：
   - 搜索 `research/papers/` 下是否有对应的 card.md
   - 如果已有，跳过精读，直接进入 Step 3

2. **创建独立 Session 精读**：
   ```
   sessions_spawn: 创建新 Session
   sessions_send: 触发 read-arxiv-paper Skill，输入论文 arXiv URL
   session_status: 轮询直到完成
   ```

3. **并发控制**：同时运行的精读 Session 不超过 3 个，避免资源竞争。

### Step 3: 提取结构化字段

对每篇已有 card.md 的论文，调用解析脚本：

```bash
python $PAPER_AGENT_ROOT/scripts/card_parser.py \
  --card-path /path/to/card.md \
  --arxiv-id {arxiv_id}
```

或使用批量模式（自动查找所有 card.md）：

```bash
python $PAPER_AGENT_ROOT/scripts/card_parser.py --run-id {run_id}
```

### Step 4: 检查结果

解析脚本输出 JSON 到 stdout，同时保存到 `pipeline_data/{run_id}/skill4_parsed/{arxiv_id}.json`。

检查 `parse_success` 和 `fields_extracted` 字段：
- `parse_success: true` + `fields_extracted >= 3` = 解析良好
- `parse_success: true` + `fields_extracted < 3` = 部分解析，可能需要 card.md 格式优化
- `parse_success: false` = 解析失败，检查 `parse_error`
- `needs_reading: true` = 缺少 card.md，需要先触发 read-arxiv-paper

---

## 提取字段说明

| 字段 | 含义 | 示例 |
|------|------|------|
| `sub_field` | 研究子领域 | `generative_rec`, `sequential_rec` |
| `ID_paradigm` | ID/表示范式 | `Semantic ID`, `RQ-VAE`, `Collaborative ID` |
| `item_tokenizer` | 物品标记化方法 | `RQ-VAE`, `BPE`, `SentencePiece` |
| `baselines_compared` | 对比的基线方法 | `["SASRec", "BPR", "BERT4Rec"]` |
| `transferable_techniques` | 可迁移的技术 | `["Semantic ID generation", "Multi-task loss"]` |
| `inspiration_ideas` | 启发的研究想法 | `["Combine X with Y for Z"]` |

---

## 输出文件

每篇论文输出到 `pipeline_data/{run_id}/skill4_parsed/{arxiv_id}.json`：

```json
{
  "arxiv_id": "2305.XXXXX",
  "title": "Example Paper Title...",
  "sub_field": "your_research_field",
  "ID_paradigm": "Semantic ID",
  "item_tokenizer": "Tokenizer Method",
  "baselines_compared": ["SASRec", "BPR", "BERT4Rec"],
  "transferable_techniques": ["Technique A from the paper"],
  "inspiration_ideas": ["Combine X with Y for Z"],
  "card_path": "$PAPER_AGENT_ROOT/research/papers/2305.XXXXX_example/card.md",
  "parse_success": true,
  "fields_extracted": 6,
  "fields_total": 6
}
```
