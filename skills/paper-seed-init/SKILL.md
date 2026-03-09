---
name: paper-seed-init
description: 初始化和维护核心论文目录（Seed Papers），为论文搜索、打分、精读和 Idea 生成提供学术品味锚点。当用户说"初始化核心论文"、"更新种子论文"时触发。
---

# Paper Seed Init — 核心论文目录初始化

## 概述

本 Skill 管理核心论文目录（Seed Papers），它是整个论文阅读 Pipeline 的**学术品味锚点**。核心论文为搜索去重、打分校准、精读参考和 Idea 生成提供基准。

---

## ⚠️ 关键规则

1. **首次使用必须先初始化**：Pipeline 运行前，必须确保 `seed_papers.json` 已生成。
2. **增量更新**：当用户修改 `profile.yaml` 中的 `seed_papers` 列表时，脚本只拉取新增论文，不会重新拉取已有论文。
3. **自动注册 seen_papers.json**：所有核心论文 ID 会自动注册到去重表，避免被搜索模块重复抓取。

---

## 工作流程

### 初始化核心论文 — "初始化核心论文"

执行初始化脚本：

```bash
python $PAPER_AGENT_ROOT/scripts/seed_init.py
```

脚本将：

1. 读取 `profile.yaml` 中的 `seed_papers` arXiv ID 列表
2. 对每个 arXiv ID 调用 `ArxivSearcher` 拉取元数据（title、authors、abstract、url、categories、comments）
3. 合并已有 `seed_papers.json` 中的手动 JSON 条目（方式 B）
4. 为每篇论文提供可编辑的字段：`user_note`、`role`（foundational/benchmark/inspiring/my_work）、`sub_field`、`key_concepts`
5. 将所有核心论文 ID 注册到 `seen_papers.json`（标记 `source: "seed"`）
6. 输出/更新 `seed_papers.json`

### 增量更新 — "更新种子论文"

```bash
python $PAPER_AGENT_ROOT/scripts/seed_init.py --update
```

脚本将：
1. 对比 `profile.yaml` 中的 `seed_papers` 与现有 `seed_papers.json`
2. 仅拉取新增论文的元数据
3. 保留已有论文的手动标注（user_note、role 等）

---

## 输出文件

### seed_papers.json

每条记录格式：

```json
{
  "arxiv_id": "2305.05065",
  "title": "Recommender Systems with Generative Retrieval",
  "authors": ["Shashank Rajput", "..."],
  "abstract": "...",
  "url": "https://arxiv.org/abs/2305.05065",
  "published_date": "2023-05-08",
  "categories": ["cs.IR", "cs.AI"],
  "comments": "Accepted at NeurIPS 2023",
  "user_note": "",
  "role": "foundational",
  "sub_field": "generative_rec",
  "key_concepts": ["Semantic ID", "RQ-VAE", "autoregressive retrieval"],
  "has_card": false,
  "card_path": ""
}
```

### seen_papers.json 注册

每篇核心论文自动注册：

```json
{
  "2305.05065": {
    "source": "seed",
    "first_seen_date": "2026-03-01",
    "first_seen_run_id": "seed_init"
  }
}
```

---

## 核心论文角色（role）说明

| 角色 | 说明 | 用途 |
|------|------|------|
| `foundational` | 奠基性论文（见 seed_papers.json） | few-shot 打分示例（≤3 篇） |
| `benchmark` | 重要对比基线（如 SASRec、P5） | 实验规划参考 |
| `inspiring` | 启发性论文 | Idea 碰撞素材 |
| `my_work` | 用户自己的论文 | 知识库锚定 |

---

## 下游 Skill 如何使用核心论文

| 下游 Skill | 使用方式 |
|-----------|---------|
| paper-source-scraper | 核心论文 ID 在 `seen_papers.json` 中，搜索时自动过滤 |
| paper-relevance-scorer | 从 `seed_papers.json` 选取 `role: "foundational"` 论文（≤3篇）的 title + abstract 作为 few-shot 正样本 |
| paper-deep-parser | `read-arxiv-paper` 精读时可参考核心论文列表生成对比章节 |
| paper-knowledge-sync | 核心论文的结构化 JSON 作为 Idea 碰撞的知识底座 |
