# paper-human-review

## 基本信息

- **名称**：paper-human-review
- **类型**：交互 Skill（对话式 + 异步兜底）
- **路径**：`.openclaw/skills/paper-human-review/`
- **依赖**：paper-relevance-scorer（输入评分结果）

## 适用场景

- **Pipeline 内审阅**：作为论文阅读 Pipeline 的第 4 步，Agent 在对话中展示审阅卡片
- **独立审阅**：用户直接要求审阅边缘论文
- **异步恢复**：cron 场景下用户异步审阅后恢复 Pipeline

## 使用示例

对 Agent 说：
> "人工审阅论文"
> "捞回论文"

## 核心架构

**对话式交互优先**（Agent 编排模式首选）：

1. `scripts/human_review.py --init` → 生成审阅卡片 + stdout 精简格式
2. Agent 在对话中展示精简卡片 → 用户回复 accept/reject
3. Agent 解析用户回复 → 调用 `--chat-decide '{json}'` 合并结果

**异步兜底**（cron 场景）：

1. `--init` 生成卡片 + 挂起 Pipeline
2. 用户手动编辑 `skill3_human_decisions.json`
3. `--merge` 合并结果 + 恢复 Pipeline

## 配置

- `human_review_wait_days`：异步审阅等待天数（默认 3）
- `human_review_default_policy`：超时策略（`discard` / `accept`）
- `notification_channel`：通知渠道（`local` / `webhook`）
