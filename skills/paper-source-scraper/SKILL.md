---
name: paper-source-scraper
description: 按研究方向自动搜索最新论文并执行两级去重（运行内 + 跨周期），输出增量新论文列表。当用户说"搜索最新论文"、"论文巡检"时触发。
---

# Paper Source Scraper — 靶向搜索与去重

## 概述

本 Skill 是一个**纯数据获取工具**，不包含任何 LLM 逻辑。所有逻辑放在 `scripts/source_scraper.py` 中确定性执行。

---

## ⚠️ 关键规则

1. **纯脚本驱动**：触发后直接执行 Python 脚本，Agent 不需要参与搜索和去重逻辑。
2. **两级去重**：搜索结果必须经过单次运行内去重（Intra-run）和跨周期增量去重（Cross-run）。
3. **增量输出**：最终输出**仅包含真正的新论文**，已在之前 run 中见过的论文会被过滤。

---

## 工作流程

### 执行搜索 — "搜索最新论文"

执行搜索脚本：

```bash
python $PAPER_AGENT_ROOT/scripts/source_scraper.py --run-id {run_id}
```

脚本将：

1. 从 `profile.yaml` 读取关键词列表、白名单作者列表、arXiv 分类列表、时间范围
2. 对每组关键词调用 `ArxivSearcher.search()` 执行搜索
3. 对白名单作者额外执行按作者维度搜索
4. 执行**两级去重**：
   - **Intra-run**：对本次多组搜索结果基于 arXiv ID 去重合并
   - **Cross-run**：与 `seen_papers.json` + `seed_papers.json` 做 Diff，过滤已见论文
5. 将新增论文 ID 注册到 `seen_papers.json`
6. 输出 `skill1_search_results.json` + 去重统计摘要

---

## 输入

- `profile.yaml` 中的搜索配置：
  - `keywords`：关键词列表
  - `whitelist_authors`：白名单作者列表
  - `arxiv_categories`：arXiv 分类列表
  - `search_days`：搜索时间范围
- `seen_papers.json`：全局已见论文注册表
- `seed_papers.json`：核心论文目录

## 输出

### skill1_search_results.json

```json
{
  "papers": [
    {
      "arxiv_id": "2603.01234",
      "title": "...",
      "authors": ["..."],
      "abstract": "...",
      "url": "https://arxiv.org/abs/2603.01234",
      "source": "keyword_search",
      "published_date": "2026-03-01",
      "categories": ["cs.IR", "cs.AI"],
      "comments": ""
    }
  ],
  "stats": {
    "total_raw": 50,
    "dedup_intra_run": 35,
    "dedup_cross_run": 12,
    "new_increment": 12
  }
}
```

---

## 错误处理

| 场景 | 处理策略 |
|------|---------|
| arXiv API 频率限制（429） | 指数退避重试（最多 3 次） |
| 网络连接失败 | 返回空列表 + 错误日志 |
| `seen_papers.json` 不存在或损坏 | 从 `paper_index.json` + `seed_papers.json` 重建 |

---

## 去重统计示例

```
搜索统计:
  原始搜索结果:     50 篇
  运行内去重后:     35 篇 (去除 15 篇重复)
  跨周期去重后:     12 篇 (去除 23 篇已见)
  最终增量新论文:   12 篇
```
