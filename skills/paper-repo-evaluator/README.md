# paper-repo-evaluator

Code repository evaluation Skill for the paper-reading-agent pipeline.

## Overview

Evaluates code repositories associated with academic papers. Extracts GitHub links from paper text, queries GitHub API for metadata, and assesses integration feasibility.

## Architecture

- **Three-stage search**: Extract links from text -> Search GitHub by title -> Mark as no-code
- **Graceful degradation**: API failures marked as `github_api_failed: true`, never block pipeline
- **Rate limit aware**: Built-in delay and exponential backoff for GitHub API

## Scripts

| Script | Purpose |
|--------|---------|
| `$PAPER_AGENT_ROOT/scripts/repo_evaluator.py` | Extract links, query GitHub API, assess repos |

## Input/Output

- **Input**: `pipeline_data/{run_id}/skill3_final_selection.json` (papers to evaluate)
- **Output**: `pipeline_data/{run_id}/skill5_repo_eval/{arxiv_id}.json` (per-paper evaluation)

## Environment Variables

- `GITHUB_TOKEN`: GitHub personal access token (optional, increases rate limit)
