#!/usr/bin/env python3
"""Paper Relevance Scorer -- Deterministic Tool Layer.

This script is the DETERMINISTIC TOOL for the three-step Agent scoring flow:
  Step 1: --prepare  → Build scoring context (few-shot, prompt, paper list)
  Step 2: (Agent)    → Agent uses LLM to score each paper (via SKILL.md)
  Step 3: --postprocess → Parse Agent output, apply bonuses, partition, save

The Agent (LLM) handles the actual scoring in Step 2. This script only
handles the deterministic pre/post-processing that requires correctness.

Usage:
    # Step 1: Prepare scoring context for Agent
    python scorer_utils.py --prepare --run-id {run_id}

    # Step 3: Post-process Agent output (bonuses, partition, save)
    python scorer_utils.py --postprocess --run-id {run_id}
"""
import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Standard path injection for paper-agent common modules
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from common.config_loader import load_profile
from common.json_extractor import extract_json_array, extract_json_with_fallback
from common.path_manager import PathManager

logger = logging.getLogger("paper_agent.scorer_utils")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Default score for degradation
DEFAULT_SCORE = 5
MAX_FEWSHOT_EXAMPLES = 3


# ═══════════════════════════════════════════════════════════════════════════════
# Few-Shot Example Builder
# ═══════════════════════════════════════════════════════════════════════════════


def load_seed_papers(seed_path: Path) -> List[Dict[str, Any]]:
    """Load seed papers from JSON file.

    Args:
        seed_path: Path to seed_papers.json.

    Returns:
        List of seed paper dicts, or empty list on failure.
    """
    if not seed_path.exists():
        logger.warning("seed_papers.json not found at %s", seed_path)
        return []

    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load seed_papers.json: %s", e)
        return []


def build_fewshot_examples(
    seed_papers: List[Dict[str, Any]],
    max_examples: int = MAX_FEWSHOT_EXAMPLES,
) -> List[Dict[str, str]]:
    """Build few-shot positive examples from foundational seed papers.

    Selects papers with role="foundational" (up to max_examples),
    extracting title + abstract for the scoring prompt.

    Args:
        seed_papers: List of seed paper dicts.
        max_examples: Maximum number of examples to return.

    Returns:
        List of dicts with 'title', 'abstract', 'score' fields.
    """
    foundational = [
        p for p in seed_papers
        if p.get("role") == "foundational"
    ]

    # If no foundational papers, try any seed paper
    if not foundational:
        foundational = seed_papers

    examples = []
    for paper in foundational[:max_examples]:
        examples.append({
            "title": paper.get("title", ""),
            "abstract": paper.get("abstract", "")[:500],  # Trim for context efficiency
            "arxiv_id": paper.get("arxiv_id", ""),
            "score": 10,  # Seed papers are the gold standard
            "rationale": f"核心论文，定义了研究方向的核心范式。",
        })

    return examples


def format_fewshot_for_prompt(examples: List[Dict[str, str]]) -> str:
    """Format few-shot examples as a string for the SKILL.md prompt.

    Args:
        examples: Few-shot example dicts.

    Returns:
        Formatted string for prompt injection.
    """
    if not examples:
        return "(No foundational seed papers available for calibration)"

    lines = []
    for i, ex in enumerate(examples, 1):
        lines.append(f"**Example {i} (Score: {ex['score']}/10)**:")
        lines.append(f"  Title: {ex['title']}")
        lines.append(f"  Abstract: {ex['abstract']}...")
        lines.append(f"  Rationale: {ex['rationale']}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Whitelist / Top Venue Matching
# ═══════════════════════════════════════════════════════════════════════════════


def check_whitelist_author(
    paper: Dict[str, Any],
    whitelist_authors: List[str],
) -> bool:
    """Check if any paper author matches the whitelist.

    Performs case-insensitive partial matching.

    Args:
        paper: Paper dict with 'authors' field.
        whitelist_authors: List of whitelisted author names.

    Returns:
        True if any author matches.
    """
    if not whitelist_authors:
        return False

    paper_authors = paper.get("authors", [])
    if not paper_authors:
        return False

    # Normalize for comparison
    whitelist_lower = [a.lower().strip() for a in whitelist_authors]

    for author in paper_authors:
        author_lower = author.lower().strip()
        for wl in whitelist_lower:
            if wl in author_lower or author_lower in wl:
                return True

    return False


def check_top_venue(
    paper: Dict[str, Any],
    top_venues: List[str],
) -> bool:
    """Check if the paper's comments field mentions a top venue.

    Args:
        paper: Paper dict with 'comments' field.
        top_venues: List of top venue keywords.

    Returns:
        True if a top venue keyword is found in comments.
    """
    if not top_venues:
        return False

    comments = paper.get("comments", "")
    if not comments:
        return False

    comments_lower = comments.lower()
    for venue in top_venues:
        if venue.lower() in comments_lower:
            return True

    return False


def apply_bonuses(
    scored_papers: List[Dict[str, Any]],
    whitelist_authors: List[str],
    top_venues: List[str],
) -> List[Dict[str, Any]]:
    """Apply whitelist author and top venue bonuses to scored papers.

    Args:
        scored_papers: List of scored paper dicts.
        whitelist_authors: Whitelist author names.
        top_venues: Top venue keywords.

    Returns:
        Papers with bonuses applied (score capped at 10).
    """
    for paper in scored_papers:
        is_wl = check_whitelist_author(paper, whitelist_authors)
        is_tv = check_top_venue(paper, top_venues)

        paper["is_whitelist_author"] = is_wl
        paper["is_top_venue"] = is_tv

        score = paper.get("relevance_score", DEFAULT_SCORE)
        if is_wl:
            score += 1
        if is_tv:
            score += 1
        paper["relevance_score"] = min(score, 10)

    return scored_papers


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Output Processing (Tolerant JSON Extraction + Degradation)
# ═══════════════════════════════════════════════════════════════════════════════


def parse_agent_scoring_output(
    agent_output: str,
    papers: List[Dict[str, Any]],
    error_log_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Parse Agent's scoring output with tolerant JSON extraction.

    Attempts to extract a JSON array from the Agent's raw output.
    If extraction fails, applies degradation: all papers get default
    score 5 and are marked with scoring_failed=true.

    Args:
        agent_output: Raw text output from the Agent.
        papers: Original paper list (for degradation fallback).
        error_log_path: Path to save raw output on extraction failure.

    Returns:
        List of scored paper dicts (always returns a valid list).
    """
    # Try to extract JSON array
    scored = extract_json_with_fallback(
        agent_output,
        default=None,
        error_log_path=str(error_log_path) if error_log_path else None,
        context="paper-relevance-scorer",
    )

    if scored is not None and isinstance(scored, list):
        logger.info("Successfully extracted %d scored papers from Agent output", len(scored))
        return _validate_scored_papers(scored, papers)

# Extraction failed -- apply degradation
    logger.error("JSON extraction failed. Applying degradation: all papers get score %d", DEFAULT_SCORE)
    return _degrade_all_papers(papers)


def _validate_scored_papers(
    scored: List[Dict[str, Any]],
    original_papers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Validate and fill missing fields in scored papers.

    Ensures each scored paper has required fields. Merges with original
    paper data to preserve metadata not included in Agent output.

    Args:
        scored: Scored papers from Agent output.
        original_papers: Original paper list with full metadata.

    Returns:
        Validated scored papers.
    """
    # Build index of original papers for metadata merging
    original_index = {p.get("arxiv_id", ""): p for p in original_papers}

    validated = []
    for entry in scored:
        if not isinstance(entry, dict):
            continue

        arxiv_id = entry.get("arxiv_id", "")

        # Merge with original metadata
        original = original_index.get(arxiv_id, {})
        merged = {**original, **entry}

        # Ensure required fields
        if "relevance_score" not in merged:
            merged["relevance_score"] = DEFAULT_SCORE
            merged["scoring_failed"] = True

        # Clamp score to valid range
        try:
            merged["relevance_score"] = max(0, min(10, int(merged["relevance_score"])))
        except (ValueError, TypeError):
            merged["relevance_score"] = DEFAULT_SCORE
            merged["scoring_failed"] = True

        if "scoring_rationale" not in merged:
            merged["scoring_rationale"] = "N/A"

        if "tags" not in merged or not isinstance(merged["tags"], list):
            merged["tags"] = []

        validated.append(merged)

    # Check for papers that weren't scored by the Agent
    scored_ids = {p.get("arxiv_id", "") for p in validated}
    for paper in original_papers:
        aid = paper.get("arxiv_id", "")
        if aid and aid not in scored_ids:
            logger.warning("Paper %s was not scored by Agent, assigning default", aid)
            paper["relevance_score"] = DEFAULT_SCORE
            paper["scoring_rationale"] = "Not scored by Agent"
            paper["scoring_failed"] = True
            paper["tags"] = []
            validated.append(paper)

    return validated


def _degrade_all_papers(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply degradation strategy: default score for all papers.

    Args:
        papers: Original paper list.

    Returns:
        Papers with default scores and scoring_failed=true.
    """
    degraded = []
    for paper in papers:
        entry = {**paper}
        entry["relevance_score"] = DEFAULT_SCORE
        entry["scoring_rationale"] = "Scoring failed: Agent output format error"
        entry["scoring_failed"] = True
        entry["tags"] = []
        entry["is_whitelist_author"] = False
        entry["is_top_venue"] = False
        degraded.append(entry)

    return degraded




# ═══════════════════════════════════════════════════════════════════════════════
# Three-Zone Partitioning
# ═══════════════════════════════════════════════════════════════════════════════


def partition_by_score(
    papers: List[Dict[str, Any]],
    high_threshold: int = 7,
    edge_low: int = 4,
    edge_high: int = 6,
) -> Dict[str, List[Dict[str, Any]]]:
    """Partition scored papers into three zones by score.

    Args:
        papers: Scored papers with 'relevance_score' field.
        high_threshold: Score >= this → high zone.
        edge_low: Score >= this and <= edge_high → edge zone.
        edge_high: Upper bound for edge zone.

    Returns:
        Dict with 'high', 'edge', 'low' paper lists (sorted by score desc).
    """
    high = []
    edge = []
    low = []

    for paper in papers:
        score = paper.get("relevance_score", 0)
        if score >= high_threshold:
            high.append(paper)
        elif edge_low <= score <= edge_high:
            edge.append(paper)
        else:
            low.append(paper)

    # Sort each zone by score (descending)
    high.sort(key=lambda p: p.get("relevance_score", 0), reverse=True)
    edge.sort(key=lambda p: p.get("relevance_score", 0), reverse=True)
    low.sort(key=lambda p: p.get("relevance_score", 0), reverse=True)

    return {"high": high, "edge": edge, "low": low}


# ═══════════════════════════════════════════════════════════════════════════════
# Scoring Context Builder (for SKILL.md prompt)
# ═══════════════════════════════════════════════════════════════════════════════


def build_scoring_prompt(
    papers: List[Dict[str, Any]],
    fewshot_examples: List[Dict[str, str]],
    research_description: str = "",
) -> str:
    """Build the complete scoring prompt for the Agent.

    This prompt is what pipeline_runner.py will send to the Agent
    (or what SKILL.md references as scoring context).

    Args:
        papers: Papers to score.
        fewshot_examples: Few-shot calibration examples.
        research_description: User's research direction description.

    Returns:
        Complete scoring prompt string.
    """
    prompt_parts = []

    # Research direction
    prompt_parts.append("## Research Direction\n")
    prompt_parts.append(research_description or "Generative Recommendation")
    prompt_parts.append("\n")

    # Few-shot calibration
    prompt_parts.append("## Scoring Calibration (Few-shot Positive Examples)\n")
    prompt_parts.append("The following core papers define the 9-10 score standard:\n")
    prompt_parts.append(format_fewshot_for_prompt(fewshot_examples))
    prompt_parts.append("\n")

    # Papers to score
    prompt_parts.append(f"## Papers to Score ({len(papers)} papers)\n")
    for i, paper in enumerate(papers, 1):
        prompt_parts.append(f"### Paper {i}")
        prompt_parts.append(f"- arXiv ID: {paper.get('arxiv_id', 'N/A')}")
        prompt_parts.append(f"- Title: {paper.get('title', 'N/A')}")
        prompt_parts.append(f"- Authors: {', '.join(paper.get('authors', []))}")
        prompt_parts.append(f"- Abstract: {paper.get('abstract', 'N/A')}")
        prompt_parts.append(f"- Categories: {', '.join(paper.get('categories', []))}")
        prompt_parts.append(f"- Comments: {paper.get('comments', 'N/A')}")
        prompt_parts.append("")

    return "\n".join(prompt_parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Main Entry Points
# ═══════════════════════════════════════════════════════════════════════════════


def run_scorer(
    pm: PathManager,
    profile: Dict[str, Any],
    agent_output: Optional[str] = None,
) -> Dict[str, Any]:
    """Post-process Agent scoring output (Step 3).

    Reads the Agent's raw scoring output from skill2_agent_raw_output.json
    (or from provided string), applies bonuses, partitions, and saves.

    In Agent orchestration mode (Path B), this is called as:
    1. Agent writes scoring JSON to skill2_agent_raw_output.json
    2. Agent calls: python scorer_utils.py --postprocess --run-id {run_id}
    3. This function parses, validates, applies bonuses, partitions, saves

    Args:
        pm: PathManager for the current run.
        profile: Loaded profile configuration.
        agent_output: Raw Agent scoring output text. If None, reads from
            skill2_agent_raw_output.json.

    Returns:
        Statistics dict.
    """
    # Load search results (original paper list for validation)
    search_results = _load_search_results(pm.skill1_search_results)
    papers = search_results.get("papers", [])

    if not papers:
        logger.warning("No papers to score")
        _save_empty_results(pm.skill2_scored_results)
        return {"scored_high": 0, "scored_edge": 0, "scored_low": 0}

    # Read Agent output
    if agent_output is not None:
        raw_output = agent_output
    else:
        # Read from skill2_agent_raw_output.json (primary path)
        raw_path = pm.skill2_agent_raw_output
        if raw_path.exists():
            with open(raw_path, "r", encoding="utf-8") as f:
                raw_output = f.read()
        else:
            # Also try legacy .txt format
            txt_path = pm.run_dir / "skill2_agent_raw_output.txt"
            if txt_path.exists():
                with open(txt_path, "r", encoding="utf-8") as f:
                    raw_output = f.read()
            else:
# No Agent output -- degrade all papers
                logger.error(
                    "No Agent scoring output found at %s. "
                    "Agent must write scoring JSON before calling --postprocess.",
                    raw_path,
                )
                scored = _degrade_all_papers(papers)
                raw_output = None

    if raw_output is not None:
        scored = parse_agent_scoring_output(
            raw_output,
            papers,
            error_log_path=pm.error_log("paper-relevance-scorer", "raw_output"),
        )

    # Apply bonuses
    whitelist_authors = profile.get("whitelist_authors", [])
    top_venues = profile.get("top_venues", [])
    scored = apply_bonuses(scored, whitelist_authors, top_venues)

    # Partition into score zones
    thresholds = profile.get("score_thresholds", {})
    result = partition_by_score(
        scored,
        high_threshold=thresholds.get("high", 7),
        edge_low=thresholds.get("edge_low", 4),
        edge_high=thresholds.get("edge_high", 6),
    )

    # Save results
    _save_scored_results(result, pm.skill2_scored_results)

    stats = {
        "scored_high": len(result["high"]),
        "scored_edge": len(result["edge"]),
        "scored_low": len(result["low"]),
    }
    logger.info("Scoring complete: %s", stats)
    return stats


def _load_search_results(path: Path) -> Dict[str, Any]:
    """Load search results from JSON file."""
    if not path.exists():
        logger.warning("Search results not found: %s", path)
        return {"papers": []}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load search results: %s", e)
        return {"papers": []}


def _save_scored_results(result: Dict[str, List], path: Path) -> None:
    """Save scored results to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    total = sum(len(v) for v in result.values())
    logger.info("Saved %d scored papers to %s", total, path)


def _save_empty_results(path: Path) -> None:
    """Save empty scored results."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"high": [], "edge": [], "low": []}, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
description="Paper Relevance Scorer -- Utility Functions"
    )
    parser.add_argument("--run-id", type=str, required=True, help="Pipeline run ID")
    parser.add_argument("--prepare", action="store_true", help="Prepare scoring context")
    parser.add_argument("--postprocess", action="store_true", help="Post-process Agent output")
    parser.add_argument("--profile", type=str, default=None, help="Path to profile.yaml")
    args = parser.parse_args()

    try:
        profile = load_profile(args.profile)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Failed to load profile: %s", e)
        sys.exit(1)

    pm = PathManager(run_id=args.run_id)

    if args.prepare:
        # Step 1: Build scoring context
        search_results = _load_search_results(pm.skill1_search_results)
        papers = search_results.get("papers", [])
        seed_papers = load_seed_papers(pm.seed_papers_json)
        fewshot = build_fewshot_examples(seed_papers)

        research_desc = profile.get("research_description", "")
        prompt = build_scoring_prompt(papers, fewshot, research_desc)

        # Save comprehensive scoring context
        context = {
            "run_id": args.run_id,
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

        context_path = pm.skill2_scoring_context
        with open(context_path, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, ensure_ascii=False)

        # Output structured JSON for Agent to parse
        result = {
            "step": "prepare-scoring",
            "status": "success",
            "papers_count": len(papers),
            "fewshot_count": len(fewshot),
            "context_path": str(context_path),
            "message": f"Scoring context ready: {len(papers)} papers, {len(fewshot)} few-shot examples.",
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.postprocess:
        # Step 3: Post-process Agent output
        stats = run_scorer(pm=pm, profile=profile)
        result = {
            "step": "postprocess-scoring",
            "status": "success",
            **stats,
            "message": f"Scoring complete: {stats['scored_high']} high, "
                       f"{stats['scored_edge']} edge, {stats['scored_low']} low.",
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
