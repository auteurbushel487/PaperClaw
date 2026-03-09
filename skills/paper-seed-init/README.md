# paper-seed-init

## 基本信息

- **名称**：paper-seed-init
- **类型**：SKILL.md + scripts（元数据拉取脚本）
- **路径**：`.openclaw/skills/paper-seed-init/`
- **依赖**：`arxiv-paper-search`（复用 `ArxivSearcher` 类）

## 适用场景

- **首次初始化**：系统首次使用时，从 `profile.yaml` 中的 arXiv ID 列表拉取论文元数据，生成 `seed_papers.json`
- **增量更新**：用户新增核心论文后，自动检测变更并仅拉取新增论文
- **手动条目**：支持用户直接在 `seed_papers.json` 中手写完整 JSON 条目（方式 B，适用于非 arXiv 论文）

## 使用示例

### 首次初始化

对 Agent 说：
> "初始化核心论文"
> "生成种子论文目录"

或直接运行脚本：
```bash
python $PAPER_AGENT_ROOT/scripts/seed_init.py
```

### 增量更新

对 Agent 说：
> "更新种子论文"
> "我新增了一篇核心论文，帮我更新"

```bash
python $PAPER_AGENT_ROOT/scripts/seed_init.py --update
```

## 核心脚本

- `$PAPER_AGENT_ROOT/scripts/seed_init.py`

该脚本负责：
1. 读取 `profile.yaml` 中的 `seed_papers` arXiv ID 列表
2. 调用 `ArxivSearcher`（来自 `arxiv-paper-search`）拉取每篇论文的元数据
3. 生成/更新 `seed_papers.json`，保留用户已有的手动标注
4. 将所有核心论文 ID 注册到 `seen_papers.json`
5. 支持增量更新：检测配置变更，仅拉取新增论文

## 输出文件

| 文件 | 说明 |
|------|------|
| `seed_papers.json` | 核心论文目录（含元数据 + 用户标注） |
| `seen_papers.json` | 全局已见论文注册表（更新） |

## 注意事项

⚠️ `seed_papers.json` 是所有下游 Skill 的**只读**数据源，只有本 Skill 有写入权限。
⚠️ arXiv API 有频率限制，脚本内置指数退避重试（最多 3 次）。
