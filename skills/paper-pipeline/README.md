# paper-pipeline

## 基本信息

- **名称**：paper-pipeline
- **类型**：编排 Skill（Agent 分步编排模式）
- **路径**：`.openclaw/skills/paper-pipeline/`
- **依赖**：paper-seed-init, paper-source-scraper, paper-relevance-scorer, paper-human-review, paper-deep-parser, paper-repo-evaluator, paper-knowledge-sync

## 适用场景

- **日常论文巡检**：定时或手动触发完整的论文搜索→打分→仲裁→精读→知识沉淀流程
- **断点续跑**：Pipeline 中断后，Agent 读取 `pipeline_state.json` 从断点恢复
- **单步执行**：Agent 可独立调用任意 `--step` 子命令
- **对话式审阅**：Agent 在对话中展示审阅卡片，用户实时回复 accept/reject

## 使用示例

### 启动完整 Pipeline

对 Agent 说：
> "开始论文日常巡检"
> "运行论文 Pipeline"

### 断点续跑

对 Agent 说：
> "继续之前的巡检"
> "恢复 Pipeline 运行"

### 查看状态

对 Agent 说：
> "查看巡检进度"

## 核心架构

**Agent 编排模式（Path B）**：Agent（LLM）是 Pipeline 的大脑，通过 SKILL.md 定义的多步编排流程按步骤调用 `pipeline_runner.py --step` 子命令，并在评分、精读、Idea 生成等环节直接用自身 LLM 能力完成。

核心脚本：`$PAPER_AGENT_ROOT/scripts/pipeline_runner.py`

可用 `--step` 子命令：
- `init` / `seed` / `search` / `seed+search`
- `prepare-scoring` / `postprocess-scoring`
- `human-review-init` / `human-review-decide`
- `deep-parse` / `repo-eval` / `knowledge-sync` / `summary`

## 配置

Pipeline 行为由 `profile.yaml` 配置，关键字段包括：
- `human_review_wait_days`：人工仲裁等待天数
- `human_review_default_policy`：超时默认策略（discard / accept）
- `score_thresholds`：打分区间阈值
