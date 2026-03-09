# paper-source-scraper

## 基本信息

- **名称**：paper-source-scraper
- **类型**：SKILL.md + scripts（搜索 + 两级去重脚本）
- **路径**：`.openclaw/skills/paper-source-scraper/`
- **依赖**：`arxiv-paper-search`（复用 `ArxivSearcher` 类）

## 适用场景

- **日常搜索**：按研究方向自动搜索最新 arXiv 论文
- **增量获取**：只输出真正新的论文，去除已见过的
- **Pipeline 集成**：作为 paper-pipeline 的第一个数据获取 Skill

## 使用示例

### 搜索最新论文

对 Agent 说：
> "搜索最新论文"
> "论文巡检"

或直接运行脚本：
```bash
python $PAPER_AGENT_ROOT/scripts/source_scraper.py --run-id 20260301_100000
```

## 核心脚本

- `$PAPER_AGENT_ROOT/scripts/source_scraper.py`

该脚本负责：
1. 多组关键词搜索（复用 `ArxivSearcher`）
2. 白名单作者维度搜索
3. 两级去重：Intra-run（arXiv ID 合并）+ Cross-run（与 seen_papers/seed_papers Diff）
4. 新增论文注册到 `seen_papers.json`
5. 输出 `skill1_search_results.json` + 去重统计

## 输出文件

| 文件 | 说明 |
|------|------|
| `skill1_search_results.json` | 增量新论文列表 + 去重统计 |
| `seen_papers.json` | 全局已见论文注册表（更新） |

## 注意事项

⚠️ arXiv API 有频率限制，脚本内置指数退避重试机制。
⚠️ 如果 `seen_papers.json` 损坏，脚本会自动从 `paper_index.json` + `seed_papers.json` 重建。
