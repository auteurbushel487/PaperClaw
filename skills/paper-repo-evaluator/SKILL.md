---
name: paper-repo-evaluator
description: 评估论文关联的代码仓库质量（GitHub stars、语言、集成成本等）。当 paper-pipeline 编排流程执行到 Step 6.5（代码评估）时自动触发。
---

# Paper Repo Evaluator — Code Repository Assessment

你负责评估最终选中论文的代码仓库质量和可集成性。

---

## ⚠️ 关键规则

1. **三阶段搜索**：先从论文文本提取链接 → 找不到则搜索 GitHub → 完全未找到标记 `has_code: false`。
2. **API 失败降级**：GitHub API 不可用时标记 `github_api_failed: true`，不要阻塞后续流程。
3. **尊重 Rate Limit**：GitHub API 调用间隔至少 2 秒，遇到 403 时指数退避重试。

---

## 执行流程

### Step 1: 调用评估脚本

使用批量模式评估当前 run 的所有选中论文：

```bash
python $PAPER_AGENT_ROOT/scripts/repo_evaluator.py --run-id {run_id}
```

或评估单篇论文：

```bash
python $PAPER_AGENT_ROOT/scripts/repo_evaluator.py \
  --arxiv-id {arxiv_id} \
  --title "Paper Title"
```

### Step 2: 检查结果

脚本返回 JSON 统计：

```json
{
  "step": "repo-eval",
  "status": "success",
  "total": 5,
  "has_code_count": 3,
  "no_code_count": 2,
  "api_failed_count": 0
}
```

### Step 3: 向用户报告

向用户报告代码评估结果摘要：
- X/Y 篇论文有关联代码
- 主要编程语言分布
- 集成成本评估

---

## 评估维度

| 维度 | 说明 |
|------|------|
| `has_code` | 是否找到代码仓库 |
| `github_url` | 仓库 URL |
| `stars` | GitHub Stars 数 |
| `language` | 主要编程语言 |
| `integration_cost` | 集成成本（Low/Medium/High） |
| `github_api_failed` | API 是否失败 |

---

## 代码链接搜索策略

1. **文本提取**：从论文摘要、card.md 中用正则提取 GitHub/GitLab 链接
2. **GitHub 搜索**：如果文本中无链接，用论文标题搜索 GitHub
3. **标记未找到**：两种方式都失败时，标记 `has_code: false`

---

## 输出文件

每篇论文输出到 `pipeline_data/{run_id}/skill5_repo_eval/{arxiv_id}.json`：

```json
{
  "arxiv_id": "2305.05065",
  "has_code": true,
  "github_url": "https://github.com/owner/repo",
  "platform": "github",
  "stars": 256,
  "forks": 42,
  "language": "Python",
  "license": "MIT",
  "integration_cost": "Low",
  "github_api_failed": false,
  "search_method": "extracted_from_text"
}
```
