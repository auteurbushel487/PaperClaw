# 📚 Paper-Agent

An **Agent-orchestrated academic paper discovery pipeline** built on the [OpenClaw](https://github.com/anthropics/openclaw) platform. Paper-Agent automates the full lifecycle of research paper tracking: search → relevance scoring → human review → deep reading → code evaluation → knowledge synthesis & idea generation.

## ✨ Key Features

- **Fully automated pipeline**: From arXiv search to structured knowledge cards, run with a single command
- **Agent-as-Brain architecture**: LLM Agent handles scoring, deep reading, and idea generation; deterministic scripts handle data I/O
- **Human-in-the-loop**: Interactive review of borderline papers with accept/reject decisions
- **Breakpoint resume**: Pipeline state persisted to JSON, resume from any step after interruption
- **Cross-run dedup**: Never see the same paper twice across multiple pipeline runs
- **Idea generation**: Cross-paper insight synthesis produces actionable research ideas

## 🏗️ Architecture

Paper-Agent uses a **two-layer architecture** designed for seamless OpenClaw integration:

```
┌─────────────────────────────────────────────────────┐
│                  OpenClaw Platform                    │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │  Layer 1: Skill Definitions (SKILL.md)          │ │
│  │  ┌─────────────┐  ┌─────────────┐              │ │
│  │  │paper-pipeline│  │paper-seed-  │  ...         │ │
│  │  │  (orchestr.) │  │  init       │              │ │
│  │  └──────┬───────┘  └──────┬──────┘              │ │
│  └─────────┼─────────────────┼─────────────────────┘ │
│            │ invokes         │ invokes                │
│  ┌─────────┼─────────────────┼─────────────────────┐ │
│  │  Layer 2: Python Scripts                         │ │
│  │  ┌──────┴───────┐  ┌──────┴──────┐              │ │
│  │  │pipeline_     │  │seed_init.py │  ...         │ │
│  │  │  runner.py   │  │             │              │ │
│  │  └──────────────┘  └─────────────┘              │ │
│  └──────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

- **Layer 1 — Skill Definitions** (`skills/`): SKILL.md files that tell the OpenClaw Agent *what to do* at each pipeline step
- **Layer 2 — Python Scripts** (`scripts/`): Deterministic tools that handle data fetching, dedup, parsing, and file I/O

## 📋 Prerequisites

### 1. OpenClaw Platform

Paper-Agent runs on [OpenClaw](https://github.com/anthropics/openclaw). You need a working OpenClaw deployment.

### 2. Third-Party OpenClaw Skills

Install these two skills in your OpenClaw instance:

| Skill | Purpose | Installation |
|-------|---------|-------------|
| `arxiv-paper-search` | arXiv API search wrapper | Follow its README to register in OpenClaw |
| `read-arxiv-paper` | Full paper reading & card.md generation | Follow its README to register in OpenClaw |

### 3. Python Dependencies

```bash
pip install -r requirements.txt
```

## 🚀 Quick Start

### Step 1: Configure Your Profile

```bash
cp profile.yaml.example profile.yaml
```

Edit `profile.yaml` to set your:
- **Research description**: What you're working on
- **Seed papers**: arXiv IDs of your core reference papers
- **Keywords**: Search terms for paper discovery
- **Whitelist authors**: Researchers whose papers get a relevance bonus

### Step 2: Register Skills in OpenClaw

Copy each skill directory from `skills/` into your OpenClaw skills directory:

```bash
# Example: copy all paper-agent skills to OpenClaw
cp -r skills/paper-* /path/to/your/.openclaw/skills/
```

> **Important**: Set the `PAPER_AGENT_ROOT` environment variable to point to this project's root directory, so that SKILL.md scripts can find the Python files:
> ```bash
> export PAPER_AGENT_ROOT=/path/to/paper-agent
> ```

### Step 3: Initialize Seed Papers

Tell the Agent:
> "Initialize core papers"

Or run directly:
```bash
python scripts/seed_init.py
```

### Step 4: Run the Pipeline

Tell the Agent:
> "Start daily paper patrol"

Or trigger specific steps:
```bash
python scripts/pipeline_runner.py --step init
python scripts/pipeline_runner.py --step seed+search --run-id {run_id}
# ... see Pipeline Steps below
```

## 📖 Pipeline Steps

| Step | Skill | Description |
|------|-------|-------------|
| **Step 0** | paper-pipeline | Initialize a new pipeline run, get `run_id` |
| **Step 1** | paper-source-scraper | Search arXiv by keywords & authors, two-level dedup |
| **Step 2** | paper-relevance-scorer | Prepare scoring context (few-shot examples from seed papers) |
| **Step 3** | paper-relevance-scorer | Agent LLM scores each paper 0-10 on relevance |
| **Step 4** | paper-relevance-scorer | Post-process: apply bonuses, sort into high/edge/low zones |
| **Step 5** | paper-human-review | Interactive review of borderline papers (accept/reject) |
| **Step 6** | paper-deep-parser | Deep read via `read-arxiv-paper`, extract structured fields |
| **Step 6.5** | paper-repo-evaluator | Evaluate associated GitHub repositories |
| **Step 6.8** | paper-knowledge-sync | Sync to knowledge base, generate research ideas |
| **Step 7** | paper-pipeline | Generate run summary with statistics |

## ⚙️ Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PAPER_AGENT_ROOT` | Recommended | Project root directory. Auto-detected if not set. |
| `ARXIV_SKILL_PATH` | Optional | Path to `arxiv-paper-search` skill scripts (defaults to OpenClaw convention) |
| `GITHUB_TOKEN` | Optional | GitHub personal access token for repo evaluation (increases API rate limit) |

## 📁 Directory Structure

```
paper-agent/
├── scripts/                        # Python tool scripts
│   ├── pipeline_runner.py          # Main orchestrator (--step subcommands)
│   ├── seed_init.py                # Seed paper initialization
│   ├── source_scraper.py           # arXiv search + two-level dedup
│   ├── scorer_utils.py             # Scoring context prep + post-processing
│   ├── human_review.py             # Interactive/async human review
│   ├── card_parser.py              # Knowledge card structured extraction
│   ├── repo_evaluator.py           # GitHub repo assessment
│   ├── knowledge_sync.py           # Knowledge base sync + idea generation
│   ├── common/                     # Shared utilities
│   │   ├── config_loader.py        # Profile YAML loader
│   │   ├── path_manager.py         # Centralized path management
│   │   ├── state_manager.py        # Pipeline state persistence
│   │   └── json_extractor.py       # Fault-tolerant JSON extraction
│   └── tests/                      # Unit tests
├── skills/                         # OpenClaw Skill definitions
│   ├── paper-pipeline/SKILL.md     # Main orchestration skill
│   ├── paper-seed-init/SKILL.md
│   ├── paper-source-scraper/SKILL.md
│   ├── paper-relevance-scorer/SKILL.md
│   ├── paper-human-review/SKILL.md
│   ├── paper-deep-parser/SKILL.md
│   ├── paper-repo-evaluator/SKILL.md
│   └── paper-knowledge-sync/SKILL.md
├── profile.yaml.example            # Configuration template
├── requirements.txt                # Python dependencies
├── LICENSE                         # MIT License
└── .gitignore
```

## 🧪 Running Tests

```bash
cd /path/to/paper-agent
python -m pytest scripts/tests/ -v
```

Or run individual test files:
```bash
python -m unittest scripts/tests/test_pipeline_runner.py
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 🔒 Pre-Release Security Checklist

Before publishing, verify no sensitive data leaked:

```bash
# Scan for potential secrets
grep -ri "api_key\|token\|secret\|password\|webhook" --include="*.py" --include="*.md" --include="*.yaml"

# Scan for hardcoded paths
grep -ri "/projects/\|/data/\|/home/" --include="*.py" --include="*.md" --include="*.yaml"

# (Optional) Use trufflehog for deeper scanning
# pip install trufflehog
# trufflehog filesystem --directory .
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
