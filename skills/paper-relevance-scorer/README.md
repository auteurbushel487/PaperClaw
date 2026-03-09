# paper-relevance-scorer

## 基本信息

- **名称**：paper-relevance-scorer
- **类型**：智能 Skill（Agent LLM 打分 + 脚本前后处理）
- **路径**：`.openclaw/skills/paper-relevance-scorer/`
- **依赖**：paper-source-scraper（输入）, paper-seed-init（few-shot 校准）

## 适用场景

- **Pipeline 内评分**：作为论文阅读 Pipeline 的第 3 步，由 paper-pipeline 编排调用
- **独立评分**：用户直接要求对一批论文进行相关性评估

## 使用示例

对 Agent 说：
> "给这些论文打分"
> "评估搜索到的论文相关性"

## 核心架构

**三步式 Agent 编排**（Path B 核心体现）：

1. `scripts/scorer_utils.py --prepare` → 生成 `skill2_scoring_context.json`
2. Agent 读取上下文 → 用 LLM 逐篇评分 → 写入 `skill2_agent_raw_output.json`
3. `scripts/scorer_utils.py --postprocess` → 容错解析、加分、分区 → 输出 `skill2_scored_results.json`

## 降级策略

- Agent 输出夹带 Markdown → 容错 JSON 提取器自动剥离
- Agent 输出完全无法解析 → 所有论文赋予默认分数 5 + `scoring_failed: true`
