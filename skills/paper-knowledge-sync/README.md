# paper-knowledge-sync

Knowledge base synchronization and idea generation Skill for the paper-reading-agent pipeline.

## Overview

Syncs paper analysis results (deep parsing + repo evaluation) into a persistent knowledge base (`paper_index.json`), detects inter-paper relationships, and generates research idea proposals via Agent LLM.

## Architecture

- **Two-phase workflow**: Deterministic sync (script) + Creative generation (Agent LLM)
- **Relation detection**: Automatic detection of shared baselines, techniques, sub-fields, authors
- **Clean Session**: Idea generation runs in a fresh Session via `sessions_spawn` for maximum context

## Scripts

| Script | Purpose |
|--------|---------|
| `$PAPER_AGENT_ROOT/scripts/knowledge_sync.py` | Index papers, detect relations, prepare idea context, save ideas |

## Modes

| Mode | Flag | Description |
|------|------|-------------|
| Sync | `--sync` | Index papers from current run into `paper_index.json` |
| Prepare Ideas | `--prepare-ideas` | Generate idea context for Agent LLM |
| Save Ideas | `--save-ideas` | Persist Agent-generated ideas to Markdown |
| Full | (no flag) | Sync + Prepare Ideas + optionally Save Ideas |

## Input/Output

- **Input**: `skill4_parsed/`, `skill5_repo_eval/`, `skill3_final_selection.json`
- **Output**: `paper_index.json` (global), `skill6_idea_context.json` (per-run), `ideas/{date}_idea_proposal.md` (persistent)
