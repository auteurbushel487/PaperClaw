#!/usr/bin/env python3
"""
Paper Pipeline — Step-based Command Toolkit for Agent Orchestration.

This script is the Agent's deterministic toolbox. It is NOT a full-pipeline
state machine anymore. Instead, the Agent (LLM) orchestrates the pipeline
via SKILL.md instructions, calling each `--step` sub-command as needed.

Core Philosophy (Path B — Agent Orchestration Mode):
  - Agent is the brain: SKILL.md defines the multi-step orchestration flow
  - Scripts are tools: each --step sub-command performs one atomic operation
  - Dialogue is the UI: scoring, review cards shown in conversation
  - Filesystem is the state bus: all data passes through pipeline_data/{run_id}/

Available Steps:
    --step init              Create run_id, pipeline_data directory, pipeline_state.json
    --step seed              Execute seed paper initialization
    --step search            Execute paper search + two-level dedup
    --step seed+search       Combined seed init + search (convenience)
    --step prepare-scoring   Generate scoring context for Agent LLM scoring
    --step postprocess-scoring  Post-process Agent scoring output (bonuses, partition)
    --step human-review-init    Generate review cards for edge papers
    --step human-review-decide  Merge user decisions from Agent conversation
    --step deep-parse        Parse selected papers (extract structured fields from card.md)
    --step repo-eval         Evaluate paper code repositories (GitHub API)
    --step knowledge-sync    Sync knowledge base + prepare idea generation context
    --step summary           Generate run_summary.json

Legacy Commands (backward compatibility):
    --status                 Show current run status
    --resume                 (Deprecated) Use Agent orchestration via SKILL.md instead

Usage:
    python pipeline_runner.py --step init
    python pipeline_runner.py --step seed+search --run-id 20260301_120000
    python pipeline_runner.py --step prepare-scoring --run-id 20260301_120000
    python pipeline_runner.py --step postprocess-scoring --run-id 20260301_120000
    python pipeline_runner.py --step human-review-init --run-id 20260301_120000
    python pipeline_runner.py --step human-review-decide --run-id 20260301_120000 --decisions '[...]'
    python pipeline_runner.py --step summary --run-id 20260301_120000
    python pipeline_runner.py --status --run-id 20260301_120000
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Standard path injection for paper-agent common modules
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from common.config_loader import load_profile
from common.path_manager import PathManager
from common.state_manager import PIPELINE_SKILLS, SkillStatus, StateManager

logger = logging.getLogger("paper_agent.pipeline_runner")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ═══════════════════════════════════════════════════════════════════════════════
# Step Implementations — Each step is an atomic, deterministic operation
# ═══════════════════════════════════════════════════════════════════════════════


def step_init(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: init — Create run directory and initialize pipeline state.

    Creates a new run_id (or uses provided one), sets up the
    pipeline_data/{run_id}/ directory structure, and initializes
    pipeline_state.json with all skills in 'pending' state.

    Returns:
        JSON result with run_id and directory path.
    """
    pm = PathManager(run_id=args.run_id)
    pm.create_run_directory()

    sm = StateManager(str(pm.pipeline_state_json))
    sm.initialize(pm.run_id)

    result = {
        "step": "init",
        "status": "success",
        "run_id": pm.run_id,
        "run_dir": str(pm.run_dir),
        "seed_papers_exist": pm.seed_papers_json.exists(),
        "message": f"Pipeline initialized. Run directory: {pm.run_dir}",
    }
    return result


def step_seed(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: seed — Initialize seed papers from profile.yaml.

    Delegates to seed_init.run_seed_init() which fetches metadata
    from arXiv for configured seed paper IDs.
    """
    pm = _require_run_id(args)
    sm = _load_state(pm)

    sm.update_skill_status("paper-seed-init", SkillStatus.RUNNING)

    try:
        from seed_init import run_seed_init
        result = run_seed_init(profile=profile, pm=pm)

        sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS, metadata={"result": result})
        return {
            "step": "seed",
            "status": "success",
            "run_id": pm.run_id,
            **result,
        }
    except Exception as e:
        sm.update_skill_status("paper-seed-init", SkillStatus.FAILED, error=str(e))
        return {"step": "seed", "status": "failed", "error": str(e)}


def step_search(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: search — Execute paper search + two-level dedup.

    Delegates to source_scraper.run_source_scraper() which performs
    multi-keyword search and cross-run dedup.
    """
    pm = _require_run_id(args)
    sm = _load_state(pm)

    sm.update_skill_status("paper-source-scraper", SkillStatus.RUNNING)

    try:
        from source_scraper import run_source_scraper
        output = run_source_scraper(profile=profile, pm=pm)
        stats = output.get("stats", {})

        sm.update_skill_status("paper-source-scraper", SkillStatus.SUCCESS, metadata={"result": stats})
        return {
            "step": "search",
            "status": "success",
            "run_id": pm.run_id,
            **stats,
        }
    except Exception as e:
        sm.update_skill_status("paper-source-scraper", SkillStatus.FAILED, error=str(e))
        return {"step": "search", "status": "failed", "error": str(e)}


def step_seed_and_search(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: seed+search — Combined seed init + search (convenience).

    If seed_papers.json already exists, skips seed init and goes
    straight to search. This is the typical first step in a pipeline run.
    """
    pm = _require_run_id(args)

    # Auto-detect seed init need
    seed_result = {}
    if not pm.seed_papers_json.exists():
        logger.info("seed_papers.json not found, running seed init first...")
        seed_result = step_seed(args, profile)
        if seed_result.get("status") == "failed":
            return {
                "step": "seed+search",
                "status": "failed",
                "phase": "seed",
                "error": seed_result.get("error"),
            }
    else:
        # Mark seed init as skipped (already done)
        sm = _load_state(pm)
        current = sm.get_skill_status("paper-seed-init")
        if current == SkillStatus.PENDING:
            sm.update_skill_status("paper-seed-init", SkillStatus.SKIPPED,
                                   metadata={"reason": "seed_papers.json already exists"})

    search_result = step_search(args, profile)

    return {
        "step": "seed+search",
        "status": search_result.get("status", "failed"),
        "run_id": pm.run_id,
        "seed_skipped": not bool(seed_result),
        "search": search_result,
    }


def step_prepare_scoring(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: prepare-scoring — Generate scoring context for Agent LLM.

    Reads search results + seed papers + profile, assembles
    skill2_scoring_context.json with papers list, few-shot examples,
    and scoring prompt template.
    """
    pm = _require_run_id(args)

    try:
        from scorer_utils import (
            build_fewshot_examples,
            build_scoring_prompt,
            load_seed_papers,
        )

        # Load search results
        papers = _load_papers_from_search(pm)

        if not papers:
            return {
                "step": "prepare-scoring",
                "status": "success",
                "run_id": pm.run_id,
                "papers_count": 0,
                "message": "No papers to score.",
            }

        # Build few-shot examples from seed papers
        seed_papers = load_seed_papers(pm.seed_papers_json)
        fewshot = build_fewshot_examples(seed_papers)

        # Build scoring prompt
        research_desc = profile.get("research_description", "")
        prompt = build_scoring_prompt(papers, fewshot, research_desc)

        # Save scoring context
        context = {
            "run_id": pm.run_id,
            "papers_count": len(papers),
            "fewshot_examples": fewshot,
            "research_description": research_desc,
            "prompt": prompt,
            "papers": [
                {
                    "arxiv_id": p.get("arxiv_id", ""),
                    "title": p.get("title", ""),
                    "abstract": p.get("abstract", "")[:500],
                    "authors": p.get("authors", []),
                    "categories": p.get("categories", []),
                    "comments": p.get("comments", ""),
                }
                for p in papers
            ],
        }

        pm.skill2_scoring_context.parent.mkdir(parents=True, exist_ok=True)
        with open(pm.skill2_scoring_context, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, ensure_ascii=False)

        return {
            "step": "prepare-scoring",
            "status": "success",
            "run_id": pm.run_id,
            "papers_count": len(papers),
            "fewshot_count": len(fewshot),
            "context_path": str(pm.skill2_scoring_context),
            "message": f"Scoring context ready: {len(papers)} papers, {len(fewshot)} few-shot examples.",
        }
    except Exception as e:
        return {"step": "prepare-scoring", "status": "failed", "error": str(e)}


def step_postprocess_scoring(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: postprocess-scoring — Post-process Agent scoring output.

    Reads skill2_agent_raw_output.json, applies tolerant JSON extraction,
    whitelist/venue bonuses, partitions into three zones, saves results.
    """
    pm = _require_run_id(args)
    sm = _load_state(pm)

    sm.update_skill_status("paper-relevance-scorer", SkillStatus.RUNNING)

    try:
        from scorer_utils import run_scorer
        stats = run_scorer(pm=pm, profile=profile)

        sm.update_skill_status("paper-relevance-scorer", SkillStatus.SUCCESS, metadata={"result": stats})
        return {
            "step": "postprocess-scoring",
            "status": "success",
            "run_id": pm.run_id,
            **stats,
            "message": f"Scoring complete: {stats.get('scored_high', 0)} high, "
                       f"{stats.get('scored_edge', 0)} edge, {stats.get('scored_low', 0)} low.",
        }
    except Exception as e:
        sm.update_skill_status("paper-relevance-scorer", SkillStatus.FAILED, error=str(e))
        return {"step": "postprocess-scoring", "status": "failed", "error": str(e)}


def step_human_review_init(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: human-review-init — Generate review cards for edge papers.

    Generates info cards in both compact stdout format (for Agent to
    display in conversation) and Markdown file (fallback). Also writes
    skill3_review_pending.json.
    """
    pm = _require_run_id(args)
    sm = _load_state(pm)

    try:
        from human_review import run_human_review
        result = run_human_review(pm=pm, profile=profile, mode="init")

        if result.get("waiting_for_human"):
            wait_days = profile.get("human_review_wait_days", 3)
            sm.set_waiting_for_human("paper-human-review", wait_days=wait_days)
        elif result.get("skipped"):
            sm.update_skill_status("paper-human-review", SkillStatus.SKIPPED,
                                   metadata={"reason": result.get("reason", "no_edge_papers")})

        return {
            "step": "human-review-init",
            "status": "success",
            "run_id": pm.run_id,
            **result,
        }
    except Exception as e:
        sm.update_skill_status("paper-human-review", SkillStatus.FAILED, error=str(e))
        return {"step": "human-review-init", "status": "failed", "error": str(e)}


def step_human_review_decide(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: human-review-decide — Merge user decisions from Agent conversation.

    Receives user decisions as JSON (via --decisions argument or stdin),
    merges with high-score papers, outputs skill3_final_selection.json.
    This is the PRIMARY path in Agent orchestration mode.
    """
    pm = _require_run_id(args)
    sm = _load_state(pm)

    try:
        decisions_json = args.decisions
        if not decisions_json:
            # Try reading from skill3_human_decisions.json (fallback / cron path)
            if pm.skill3_human_decisions.exists():
                with open(pm.skill3_human_decisions, "r", encoding="utf-8") as f:
                    decisions_json = f.read()
            else:
                return {
                    "step": "human-review-decide",
                    "status": "failed",
                    "error": "No decisions provided. Use --decisions '{json}' or create skill3_human_decisions.json",
                }

        # Parse decisions
        try:
            decisions = json.loads(decisions_json)
        except json.JSONDecodeError as e:
            return {
                "step": "human-review-decide",
                "status": "failed",
                "error": f"Invalid JSON in decisions: {e}",
            }

        # Save decisions to file (for audit trail)
        with open(pm.skill3_human_decisions, "w", encoding="utf-8") as f:
            json.dump(decisions, f, indent=2, ensure_ascii=False)

        from human_review import run_human_review
        result = run_human_review(pm=pm, profile=profile, mode="merge")

        sm.update_skill_status("paper-human-review", SkillStatus.SUCCESS, metadata={"result": result})
        return {
            "step": "human-review-decide",
            "status": "success",
            "run_id": pm.run_id,
            **result,
        }
    except Exception as e:
        sm.update_skill_status("paper-human-review", SkillStatus.FAILED, error=str(e))
        return {"step": "human-review-decide", "status": "failed", "error": str(e)}


def _sync_directory(src_dir: Path, dst_dir: Path, file_patterns: Optional[List[str]] = None) -> Dict[str, int]:
    """Sync files from src_dir to dst_dir, preserving relative structure.

    Args:
        src_dir: Source directory to sync from.
        dst_dir: Destination directory to sync to.
        file_patterns: Glob patterns to match (default: ["*.md"] for all markdown).

    Returns:
        Dict with synced/skipped counts.
    """
    if file_patterns is None:
        file_patterns = ["*.md"]

    if not src_dir.exists():
        logger.info("Source directory does not exist, skip: %s", src_dir)
        return {"synced": 0, "skipped": 0}

    dst_dir.mkdir(parents=True, exist_ok=True)

    synced = 0
    skipped = 0
    seen = set()

    for pattern in file_patterns:
        for src_file in src_dir.rglob(pattern):
            rel_path = src_file.relative_to(src_dir)
            if rel_path in seen:
                continue
            seen.add(rel_path)

            dst_file = dst_dir / rel_path

            # Only copy if target doesn't exist or source is newer
            if dst_file.exists() and dst_file.stat().st_mtime >= src_file.stat().st_mtime:
                skipped += 1
                continue

            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_file), str(dst_file))
            logger.info("Synced: %s → %s", src_file, dst_file)
            synced += 1

    return {"synced": synced, "skipped": skipped}


def sync_cards_from_workspace(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Sync research artifacts from OpenClaw workspace to configured research_dir.

    Syncs the following sub-directories:
      - papers/  : card.md and other .md files generated by read-arxiv-paper skill
      - SOUL/    : insights.md, challenge.md and other research reflection files

    Args:
        profile: Profile configuration dict.

    Returns:
        Statistics dict with sync results per sub-directory.
    """
    workspace_src = Path(
        profile.get("workspace_research_dir", str(_PAPER_AGENT_ROOT / "workspace_research"))
    )
    target_dir = Path(
        profile.get("research_dir", str(_PAPER_AGENT_ROOT / "research"))
    )

    # Define sub-directories to sync
    sync_dirs = ["papers", "SOUL"]

    total_synced = 0
    total_skipped = 0
    details = {}

    for sub_dir in sync_dirs:
        src = workspace_src / sub_dir
        dst = target_dir / sub_dir
        result = _sync_directory(src, dst)
        total_synced += result["synced"]
        total_skipped += result["skipped"]
        details[sub_dir] = {
            "source": str(src),
            "target": str(dst),
            **result,
        }

    stats = {
        "synced": total_synced,
        "skipped": total_skipped,
        "source": str(workspace_src),
        "target": str(target_dir),
        "details": details,
    }
    logger.info(
        "Research sync complete: %d synced, %d skipped (dirs: %s)",
        total_synced, total_skipped, ", ".join(sync_dirs),
    )
    return stats


def step_sync_cards(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: sync-cards — Sync card.md from OpenClaw workspace to research_dir.

    Copies card.md files generated by read-arxiv-paper skill (in
    workspace_research_dir/papers/) to the configured research_dir.
    This ensures card_parser can find them.
    """
    try:
        stats = sync_cards_from_workspace(profile)
        return {
            "step": "sync-cards",
            "status": "success",
            **stats,
            "message": (
                f"Synced {stats['synced']} card(s), skipped {stats['skipped']} unchanged. "
                f"Source: {stats['source']} → Target: {stats['target']}"
            ),
        }
    except Exception as e:
        logger.error("Card sync failed: %s", e)
        return {"step": "sync-cards", "status": "failed", "error": str(e)}


def step_deep_parse(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: deep-parse — Parse selected papers via card_parser.py.

    Reads final selection, finds card.md files, and extracts structured
    JSON fields. Papers without card.md are flagged for Agent to trigger
    read-arxiv-paper via sessions_spawn.

    Automatically syncs card.md from workspace before parsing.
    """
    pm = _require_run_id(args)
    sm = _load_state(pm)

    sm.update_skill_status("paper-deep-parser", SkillStatus.RUNNING)

    # Auto-sync card.md files from workspace before parsing
    sync_stats = sync_cards_from_workspace(profile)
    logger.info("Pre-parse sync: %d card(s) synced", sync_stats.get("synced", 0))

    try:
        from card_parser import run_deep_parse
        stats = run_deep_parse(pm=pm, profile=profile)

        sm.update_skill_status("paper-deep-parser", SkillStatus.SUCCESS,
                               metadata={"result": stats})
        return {
            "step": "deep-parse",
            "status": "success",
            "run_id": pm.run_id,
            **stats,
            "message": (
                f"Parsed {stats.get('parsed_count', 0)} papers: "
                f"{stats.get('success_count', 0)} success, "
                f"{stats.get('failed_count', 0)} failed, "
                f"{stats.get('needs_reading', 0)} need read-arxiv-paper."
            ),
        }
    except Exception as e:
        sm.update_skill_status("paper-deep-parser", SkillStatus.FAILED, error=str(e))
        return {"step": "deep-parse", "status": "failed", "error": str(e)}


def step_repo_eval(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: repo-eval — Evaluate paper code repositories via repo_evaluator.py."""
    pm = _require_run_id(args)
    sm = _load_state(pm)

    sm.update_skill_status("paper-repo-evaluator", SkillStatus.RUNNING)

    try:
        from repo_evaluator import run_repo_eval
        stats = run_repo_eval(pm=pm, profile=profile)

        sm.update_skill_status("paper-repo-evaluator", SkillStatus.SUCCESS,
                               metadata={"result": stats})
        return {
            "step": "repo-eval",
            "status": "success",
            "run_id": pm.run_id,
            **stats,
            "message": f"{stats.get('has_code_count', 0)}/{stats.get('total', 0)} papers have code.",
        }
    except Exception as e:
        sm.update_skill_status("paper-repo-evaluator", SkillStatus.FAILED, error=str(e))
        return {"step": "repo-eval", "status": "failed", "error": str(e)}


def step_knowledge_sync(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: knowledge-sync — Sync knowledge base + prepare idea context via knowledge_sync.py."""
    pm = _require_run_id(args)
    sm = _load_state(pm)

    sm.update_skill_status("paper-knowledge-sync", SkillStatus.RUNNING)

    try:
        from knowledge_sync import run_knowledge_sync
        stats = run_knowledge_sync(pm=pm, profile=profile)

        sm.update_skill_status("paper-knowledge-sync", SkillStatus.SUCCESS,
                               metadata={"result": stats})
        return {
            "step": "knowledge-sync",
            "status": "success",
            "run_id": pm.run_id,
            **stats,
            "message": (
                f"Indexed {stats.get('new_count', 0)} new + {stats.get('updated_count', 0)} updated papers. "
                f"Total: {stats.get('total_indexed', 0)}. "
                f"Idea context prepared at: {stats.get('idea_context_path', 'N/A')}"
            ),
        }
    except Exception as e:
        sm.update_skill_status("paper-knowledge-sync", SkillStatus.FAILED, error=str(e))
        return {"step": "knowledge-sync", "status": "failed", "error": str(e)}


def step_summary(args: argparse.Namespace, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Step: summary — Generate run_summary.json with complete statistics."""
    pm = _require_run_id(args)
    sm = _load_state(pm)

    skill_statuses = sm.get_all_statuses()

    # Aggregate statistics from output files
    stats = {
        "search_total_raw": 0,
        "search_new_increment": 0,
        "scored_high": 0,
        "scored_edge": 0,
        "scored_low": 0,
        "human_rescued": 0,
        "deep_parsed": 0,
        "has_code": 0,
        "ideas_generated": 0,
    }

    # Read search results stats
    if pm.skill1_search_results.exists():
        try:
            with open(pm.skill1_search_results, "r", encoding="utf-8") as f:
                search_data = json.load(f)
            stats["search_total_raw"] = search_data.get("stats", {}).get("total_raw", 0)
            stats["search_new_increment"] = len(search_data.get("papers", []))
        except (json.JSONDecodeError, OSError):
            pass

    # Read scored results stats
    if pm.skill2_scored_results.exists():
        try:
            with open(pm.skill2_scored_results, "r", encoding="utf-8") as f:
                scored_data = json.load(f)
            stats["scored_high"] = len(scored_data.get("high", []))
            stats["scored_edge"] = len(scored_data.get("edge", []))
            stats["scored_low"] = len(scored_data.get("low", []))
        except (json.JSONDecodeError, OSError):
            pass

    # Read final selection for rescue count
    if pm.skill3_final_selection.exists():
        try:
            with open(pm.skill3_final_selection, "r", encoding="utf-8") as f:
                final = json.load(f)
            stats["human_rescued"] = sum(1 for p in final if p.get("human_rescued"))
        except (json.JSONDecodeError, OSError):
            pass

    # Count parsed papers
    if pm.skill4_parsed_dir.exists():
        stats["deep_parsed"] = sum(1 for f in pm.skill4_parsed_dir.glob("*.json"))

    # Count papers with code
    if pm.skill5_repo_eval_dir.exists():
        for eval_file in pm.skill5_repo_eval_dir.glob("*.json"):
            try:
                with open(eval_file, "r", encoding="utf-8") as f:
                    eval_data = json.load(f)
                if eval_data.get("has_code"):
                    stats["has_code"] += 1
            except (json.JSONDecodeError, OSError):
                pass

    # Check for ideas
    if pm.ideas_dir.exists():
        idea_files = list(pm.ideas_dir.glob("*_idea_proposal.md"))
        stats["ideas_generated"] = len(idea_files)

    summary = {
        "run_id": pm.run_id,
        "started_at": sm.state.get("created_at", ""),
        "completed_at": datetime.now().isoformat(),
        "stats": stats,
        "skill_statuses": skill_statuses,
    }

    with open(pm.run_summary_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    return {
        "step": "summary",
        "status": "success",
        "run_id": pm.run_id,
        **stats,
        "skill_statuses": skill_statuses,
        "message": (
            f"Pipeline summary: searched {stats['search_new_increment']} → "
            f"high {stats['scored_high']} / edge {stats['scored_edge']} / low {stats['scored_low']} → "
            f"rescued {stats['human_rescued']} → parsed {stats['deep_parsed']} → "
            f"code {stats['has_code']} → ideas {stats['ideas_generated']}"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Status Display (backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════════


def show_status(args: argparse.Namespace) -> Dict[str, Any]:
    """Show current pipeline run status."""
    if args.run_id:
        pm = PathManager(run_id=args.run_id)
    else:
        pm = PathManager.from_latest_run()
        if pm is None:
            return {"status": "no_runs", "message": "No pipeline runs found."}

    sm = StateManager(str(pm.pipeline_state_json))
    try:
        sm.load()
    except FileNotFoundError:
        return {"status": "not_found", "message": f"No state found for run {pm.run_id}"}

    statuses = sm.get_all_statuses()
    overall = sm.state.get("overall_status", "unknown")

    return {
        "run_id": pm.run_id,
        "overall_status": overall,
        "skill_statuses": statuses,
        "created_at": sm.state.get("created_at", ""),
        "updated_at": sm.state.get("updated_at", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════


def _require_run_id(args: argparse.Namespace) -> PathManager:
    """Ensure run_id is provided and return PathManager.

    If no run_id specified, attempts to find the latest run.
    """
    if args.run_id:
        return PathManager(run_id=args.run_id)

    pm = PathManager.from_latest_run()
    if pm is None:
        logger.error("No run_id specified and no previous runs found.")
        sys.exit(1)
    logger.info("Using latest run: %s", pm.run_id)
    return pm


def _load_state(pm: PathManager) -> StateManager:
    """Load pipeline state for a run."""
    sm = StateManager(str(pm.pipeline_state_json))
    try:
        sm.load()
    except FileNotFoundError:
        logger.warning("Pipeline state not found, creating fresh state")
        sm.initialize(pm.run_id)
    return sm


def _load_papers_from_search(pm: PathManager) -> List[Dict[str, Any]]:
    """Load papers from search results file."""
    if not pm.skill1_search_results.exists():
        logger.warning("Search results not found: %s", pm.skill1_search_results)
        return []
    try:
        with open(pm.skill1_search_results, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("papers", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load search results: %s", e)
        return []


def _load_final_selection(pm: PathManager) -> List[Dict[str, Any]]:
    """Load final selected papers."""
    if not pm.skill3_final_selection.exists():
        logger.warning("Final selection not found: %s", pm.skill3_final_selection)
        return []
    try:
        with open(pm.skill3_final_selection, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load final selection: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Step Registry
# ═══════════════════════════════════════════════════════════════════════════════

STEP_REGISTRY = {
    "init": step_init,
    "seed": step_seed,
    "search": step_search,
    "seed+search": step_seed_and_search,
    "prepare-scoring": step_prepare_scoring,
    "postprocess-scoring": step_postprocess_scoring,
    "human-review-init": step_human_review_init,
    "human-review-decide": step_human_review_decide,
    "sync-cards": step_sync_cards,
    "deep-parse": step_deep_parse,
    "repo-eval": step_repo_eval,
    "knowledge-sync": step_knowledge_sync,
    "summary": step_summary,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Paper Pipeline — Step-based Command Toolkit for Agent Orchestration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available Steps:
  init               Create run_id and pipeline_data directory
  seed               Initialize seed papers
  search             Execute paper search + dedup
  seed+search        Combined seed init + search
  prepare-scoring    Generate scoring context for Agent LLM
  postprocess-scoring  Post-process Agent scoring output
  human-review-init  Generate review cards for edge papers
  human-review-decide  Merge user decisions from conversation
  deep-parse         Parse selected papers (card.md extraction)
  repo-eval          Evaluate code repositories (GitHub API)
  knowledge-sync     Sync knowledge base + idea generation
  summary            Generate run summary
""",
    )
    parser.add_argument(
        "--step",
        type=str,
        choices=list(STEP_REGISTRY.keys()),
        help="Step to execute (Agent orchestration mode)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Pipeline run ID (auto-generated for --step init if not provided)",
    )
    parser.add_argument(
        "--decisions",
        type=str,
        default=None,
        help="JSON string of user decisions (for --step human-review-decide)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current run status",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Path to profile.yaml (default: auto-detect)",
    )
    args = parser.parse_args()

    # Load profile
    try:
        profile = load_profile(args.profile)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Failed to load profile: %s", e)
        sys.exit(1)

    # Route to the appropriate handler
    if args.status:
        result = show_status(args)
    elif args.step:
        step_fn = STEP_REGISTRY[args.step]
        result = step_fn(args, profile)
    else:
        parser.print_help()
        sys.exit(0)

    # Output structured JSON result to stdout (for Agent to parse)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
