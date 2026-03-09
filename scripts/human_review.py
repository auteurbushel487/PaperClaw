#!/usr/bin/env python3
"""
Paper Human Review — Three-mode Script for Agent Orchestration.

Supports conversational interaction (Agent orchestration preferred) and
async state machine (cron fallback):

  --init           Generate review cards + stdout compact format for Agent
  --chat-decide    Receive Agent-parsed user decisions (JSON), merge results
  --merge          Read decisions from file (cron/async fallback)
  --timeout        Apply default policy when review period expires

Key Design Principles:
  - Agent orchestration mode (preferred): --init → Agent shows cards in
    conversation → user replies accept/reject → Agent calls --chat-decide
  - Cron fallback mode: --init (suspend) → user edits JSON → --merge (resume)
  - NEVER use time.sleep() or stdin blocking
  - Exit 0 immediately after writing state

Usage:
    # Init mode: generate cards (Agent reads stdout for conversation display)
    python human_review.py --init --run-id {run_id}

    # Chat-decide mode: Agent passes user decisions as JSON
    python human_review.py --chat-decide '[{"arxiv_id":"...","decision":"accept"}]' --run-id {run_id}

    # Merge mode: read decisions from file (cron fallback)
    python human_review.py --merge --run-id {run_id}

    # Timeout mode: apply default policy
    python human_review.py --timeout --policy discard --run-id {run_id}
"""

import argparse
import json
import logging
import os
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

logger = logging.getLogger("paper_agent.human_review")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ═══════════════════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════════════════


def load_scored_results(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Load scored results from skill2_scored_results.json."""
    if not path.exists():
        logger.warning("Scored results not found: %s", path)
        return {"high": [], "edge": [], "low": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load scored results: %s", e)
        return {"high": [], "edge": [], "low": []}


def load_human_decisions(path: Path) -> List[Dict[str, Any]]:
    """Load human review decisions from file."""
    if not path.exists():
        logger.warning("Human decisions not found: %s", path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load human decisions: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Card Generation (Full Markdown + Compact Stdout for Agent)
# ═══════════════════════════════════════════════════════════════════════════════


def generate_review_cards_markdown(
    edge_papers: List[Dict[str, Any]],
    run_id: str = "",
) -> str:
    """Generate full Markdown info cards for edge papers (file fallback)."""
    lines = [
        "# 📋 Paper Review Cards",
        "",
        f"**Pipeline Run**: {run_id}",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Papers to Review**: {len(edge_papers)}",
        "",
        "---",
        "",
    ]

    for i, paper in enumerate(edge_papers, 1):
        title = paper.get("title", "Unknown Title")
        arxiv_id = paper.get("arxiv_id", "N/A")
        authors = paper.get("authors", [])
        score = paper.get("relevance_score", "N/A")
        rationale = paper.get("scoring_rationale", "N/A")
        abstract = paper.get("abstract", "")
        url = paper.get("url", f"https://arxiv.org/abs/{arxiv_id}")
        tags = paper.get("tags", [])
        is_wl = paper.get("is_whitelist_author", False)
        is_tv = paper.get("is_top_venue", False)

        abstract_preview = abstract[:200] + "..." if len(abstract) > 200 else abstract

        lines.extend([
            f"## Paper {i}: {title}",
            "",
            f"- **arXiv ID**: `{arxiv_id}`",
            f"- **Score**: {score}/10",
            f"- **Authors**: {', '.join(authors[:5])}{'...' if len(authors) > 5 else ''}",
            f"- **Tags**: {', '.join(tags) if tags else 'N/A'}",
        ])
        if is_wl:
            lines.append("- **⭐ Whitelist Author Match**")
        if is_tv:
            lines.append("- **🏆 Top Venue Paper**")
        lines.extend([
            f"- **Rationale**: {rationale}",
            f"- **Abstract**: {abstract_preview}",
            f"- **Link**: [{url}]({url})",
            "",
            "---",
            "",
        ])

    return "\n".join(lines)


def generate_compact_cards(edge_papers: List[Dict[str, Any]]) -> str:
    """Generate compact card format for Agent to display in conversation.

    This is the stdout output that the Agent reads and presents to the user.
    Designed to be concise yet informative for quick decision-making.
    """
    if not edge_papers:
        return "No edge papers to review."

    lines = [
        f"Found {len(edge_papers)} edge paper(s) requiring your review:",
        "",
    ]

    for i, paper in enumerate(edge_papers, 1):
        title = paper.get("title", "Unknown")
        arxiv_id = paper.get("arxiv_id", "N/A")
        score = paper.get("relevance_score", "N/A")
        abstract = paper.get("abstract", "")[:100]
        tags = paper.get("tags", [])
        is_wl = "⭐" if paper.get("is_whitelist_author") else ""
        is_tv = "🏆" if paper.get("is_top_venue") else ""

        flags = f" {is_wl}{is_tv}" if (is_wl or is_tv) else ""
        tag_str = f" [{', '.join(tags)}]" if tags else ""

        lines.append(f"📄 [{i}] ({score}pts{flags}) {title}")
        lines.append(f"   ID: {arxiv_id}{tag_str}")
        lines.append(f"   {abstract}...")
        lines.append("")

    lines.append("Please reply with accept/reject decisions, e.g.:")
    lines.append('  "accept 1, reject 2, accept 3"')
    lines.append('  or "accept all" / "reject all"')

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# --init Mode: Generate Cards + Suspend (or display for Agent)
# ═══════════════════════════════════════════════════════════════════════════════


def run_init_mode(
    pm: PathManager,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Init mode: generate info cards and prepare for review.

    In Agent orchestration mode: stdout outputs compact cards for Agent
    to display in conversation. Agent then collects user decisions and
    calls --chat-decide.

    In cron mode: additionally sets waiting_for_human state.

    Returns result dict with 'compact_cards' for Agent display.
    """
    scored = load_scored_results(pm.skill2_scored_results)
    high_papers = scored.get("high", [])
    edge_papers = scored.get("edge", [])

    # If already has human decisions (resume scenario)
    if pm.skill3_human_decisions.exists():
        logger.info("Human decisions already exist, running merge mode")
        return run_merge_mode(pm, profile)

    # No edge papers → skip review entirely
    if not edge_papers:
        logger.info("No edge papers to review, skipping human review")
        _save_final_selection(high_papers, pm.skill3_final_selection)
        return {
            "skipped": True,
            "reason": "no_edge_papers",
            "final_count": len(high_papers),
            "message": f"No edge papers to review. {len(high_papers)} high-score papers proceed directly.",
        }

    # Generate full Markdown cards (file fallback)
    cards_md = generate_review_cards_markdown(edge_papers, pm.run_id)
    _save_review_cards(cards_md, pm.skill3_review_cards)

    # Generate compact cards for Agent conversation display
    compact_cards = generate_compact_cards(edge_papers)

    # Write pending list
    _save_pending_list(edge_papers, pm.skill3_review_pending)

    # Attempt notification (non-blocking, for cron mode)
    notification_channel = profile.get("notification_channel", "local")
    _send_notification(notification_channel, edge_papers, pm)

    logger.info(
        "Human review: %d edge papers pending review.",
        len(edge_papers),
    )

    return {
        "waiting_for_human": True,
        "edge_count": len(edge_papers),
        "high_count": len(high_papers),
        "compact_cards": compact_cards,
        "cards_file": str(pm.skill3_review_cards),
        "message": f"{len(edge_papers)} edge papers need your review. {len(high_papers)} high-score papers already selected.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# --chat-decide Mode: Agent passes user decisions (PRIMARY PATH)
# ═══════════════════════════════════════════════════════════════════════════════


def run_chat_decide_mode(
    pm: PathManager,
    profile: Dict[str, Any],
    decisions_json: str,
) -> Dict[str, Any]:
    """Chat-decide mode: merge user decisions from Agent conversation.

    This is the PRIMARY path in Agent orchestration mode.
    The Agent parses the user's accept/reject reply and passes
    structured decisions as JSON.

    Args:
        pm: PathManager for the current run.
        profile: Profile configuration.
        decisions_json: JSON string of decisions from Agent.

    Returns:
        Result dict with merge statistics.
    """
    # Parse decisions JSON
    try:
        decisions = json.loads(decisions_json)
        if not isinstance(decisions, list):
            return {
                "status": "failed",
                "error": "Decisions must be a JSON array.",
            }
    except json.JSONDecodeError as e:
        return {
            "status": "failed",
            "error": f"Invalid JSON in decisions: {e}",
        }

    # Save decisions to file (audit trail)
    with open(pm.skill3_human_decisions, "w", encoding="utf-8") as f:
        json.dump(decisions, f, indent=2, ensure_ascii=False)

    # Merge with high-score papers
    return _merge_decisions(pm, decisions)


# ═══════════════════════════════════════════════════════════════════════════════
# --merge Mode: Read decisions from file (cron fallback)
# ═══════════════════════════════════════════════════════════════════════════════


def run_merge_mode(
    pm: PathManager,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge mode: read decisions from file and merge with high-score papers.

    This is the FALLBACK path for cron/async scenarios where the user
    manually edits skill3_human_decisions.json.
    """
    decisions = load_human_decisions(pm.skill3_human_decisions)

    if not decisions:
        logger.warning("No human decisions found, using high papers only")
        scored = load_scored_results(pm.skill2_scored_results)
        high_papers = scored.get("high", [])
        _save_final_selection(high_papers, pm.skill3_final_selection)
        return {
            "merged": True,
            "rescued": 0,
            "final_count": len(high_papers),
            "message": f"No decisions found. {len(high_papers)} high-score papers proceed.",
        }

    return _merge_decisions(pm, decisions)


# ═══════════════════════════════════════════════════════════════════════════════
# --timeout Mode: Apply Default Policy
# ═══════════════════════════════════════════════════════════════════════════════


def run_timeout_mode(
    pm: PathManager,
    profile: Dict[str, Any],
    policy: Optional[str] = None,
) -> Dict[str, Any]:
    """Timeout mode: apply default policy when review period expires."""
    if policy is None:
        policy = profile.get("human_review_default_policy", "discard")

    scored = load_scored_results(pm.skill2_scored_results)
    high_papers = scored.get("high", [])
    edge_papers = scored.get("edge", [])

    if policy == "accept":
        for paper in edge_papers:
            paper["timeout_accepted"] = True
        final_selection = high_papers + edge_papers
        logger.info("Timeout policy 'accept': including %d edge papers", len(edge_papers))
    else:
        final_selection = high_papers
        logger.info("Timeout policy 'discard': dropping %d edge papers", len(edge_papers))

    _save_final_selection(final_selection, pm.skill3_final_selection)

    return {
        "timeout": True,
        "policy": policy,
        "edge_discarded": len(edge_papers) if policy == "discard" else 0,
        "edge_accepted": len(edge_papers) if policy == "accept" else 0,
        "final_count": len(final_selection),
        "message": f"Timeout ({policy}): {len(final_selection)} papers in final selection.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Shared Merge Logic
# ═══════════════════════════════════════════════════════════════════════════════


def _merge_decisions(
    pm: PathManager,
    decisions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge human decisions with high-score papers.

    Shared by --chat-decide and --merge modes.
    """
    scored = load_scored_results(pm.skill2_scored_results)
    high_papers = scored.get("high", [])
    edge_papers = scored.get("edge", [])

    # Build accepted IDs set
    accepted_ids = {
        d["arxiv_id"]
        for d in decisions
        if d.get("decision", "").lower() == "accept" and d.get("arxiv_id")
    }
    rejected_ids = {
        d["arxiv_id"]
        for d in decisions
        if d.get("decision", "").lower() == "reject" and d.get("arxiv_id")
    }

    # Rescue accepted edge papers
    rescued = [p for p in edge_papers if p.get("arxiv_id") in accepted_ids]
    for paper in rescued:
        paper["human_rescued"] = True
        for d in decisions:
            if d.get("arxiv_id") == paper.get("arxiv_id"):
                paper["human_note"] = d.get("note", "")
                break

    final_selection = high_papers + rescued
    _save_final_selection(final_selection, pm.skill3_final_selection)

    stats = {
        "merged": True,
        "rescued": len(rescued),
        "rejected": len(rejected_ids),
        "final_count": len(final_selection),
        "message": f"Merged: {len(rescued)} rescued, {len(rejected_ids)} rejected. "
                   f"{len(final_selection)} total in final selection.",
    }
    logger.info("Merge complete: %s", stats)
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════


def _save_final_selection(papers: List[Dict[str, Any]], path: Path) -> None:
    """Save final selection to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(papers, f, indent=2, ensure_ascii=False)
    logger.info("Final selection: %d papers saved to %s", len(papers), path)


def _save_review_cards(cards_md: str, path: Path) -> None:
    """Save review cards Markdown to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(cards_md)
    logger.info("Review cards saved to %s", path)


def _save_pending_list(edge_papers: List[Dict[str, Any]], path: Path) -> None:
    """Save pending review list to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(edge_papers, f, indent=2, ensure_ascii=False)
    logger.info("Pending list: %d papers saved to %s", len(edge_papers), path)


def _send_notification(
    channel: str,
    edge_papers: List[Dict[str, Any]],
    pm: PathManager,
) -> None:
    """Attempt to send review notification (non-blocking)."""
    if channel == "webhook":
        try:
            logger.info("Attempting webhook notification for %d edge papers...", len(edge_papers))
            summary = f"📋 论文审阅通知\n\nPipeline Run: {pm.run_id}\n待审阅论文: {len(edge_papers)} 篇\n\n"
            for i, paper in enumerate(edge_papers[:5], 1):
                title = paper.get("title", "Unknown")[:60]
                score = paper.get("relevance_score", "N/A")
                summary += f"{i}. [{score}分] {title}\n"
            if len(edge_papers) > 5:
                summary += f"... 及其他 {len(edge_papers) - 5} 篇\n"
            summary += '\n请审阅后对 Agent 说 "继续之前的巡检"'
            logger.info("Webhook notification prepared (%d chars)", len(summary))
        except Exception as e:
            logger.warning("Webhook notification failed (non-critical): %s", e)
    else:
        logger.info("Local notification: review cards written to %s", pm.skill3_review_cards)


# ═══════════════════════════════════════════════════════════════════════════════
# Main API Entry Point (for pipeline_runner.py)
# ═══════════════════════════════════════════════════════════════════════════════


def run_human_review(
    pm: PathManager,
    profile: Dict[str, Any],
    mode: str = "init",
    decisions_json: Optional[str] = None,
    timeout_policy: Optional[str] = None,
) -> Dict[str, Any]:
    """Main entry point for human review, callable from pipeline_runner.py.

    Args:
        pm: PathManager for the current run.
        profile: Profile configuration.
        mode: One of "init", "chat-decide", "merge", "timeout".
        decisions_json: JSON string for chat-decide mode.
        timeout_policy: Override policy for timeout mode.
    """
    if mode == "timeout":
        return run_timeout_mode(pm, profile, policy=timeout_policy)
    elif mode == "chat-decide":
        if not decisions_json:
            return {"status": "failed", "error": "No decisions JSON provided for chat-decide mode."}
        return run_chat_decide_mode(pm, profile, decisions_json)
    elif mode == "merge":
        return run_merge_mode(pm, profile)
    else:
        return run_init_mode(pm, profile)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Paper Human Review — Three-mode Script for Agent Orchestration"
    )
    parser.add_argument("--run-id", type=str, required=True, help="Pipeline run ID")

    # Mode flags (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--init", action="store_true",
                           help="Init mode: generate review cards (Agent orchestration primary)")
    mode_group.add_argument("--chat-decide", type=str, metavar="JSON",
                           help="Chat-decide mode: pass user decisions as JSON string")
    mode_group.add_argument("--merge", action="store_true",
                           help="Merge mode: read decisions from file (cron fallback)")
    mode_group.add_argument("--timeout", action="store_true",
                           help="Timeout mode: apply default policy")

    parser.add_argument("--policy", type=str, choices=["discard", "accept"],
                       default=None, help="Timeout policy override")
    parser.add_argument("--profile", type=str, default=None,
                       help="Path to profile.yaml")
    args = parser.parse_args()

    try:
        profile = load_profile(args.profile)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Failed to load profile: %s", e)
        sys.exit(1)

    pm = PathManager(run_id=args.run_id)

    if args.chat_decide:
        result = run_human_review(pm=pm, profile=profile, mode="chat-decide",
                                  decisions_json=args.chat_decide)
    elif args.merge:
        result = run_human_review(pm=pm, profile=profile, mode="merge")
    elif args.timeout:
        result = run_human_review(pm=pm, profile=profile, mode="timeout",
                                  timeout_policy=args.policy)
    else:
        # Default to init mode
        result = run_human_review(pm=pm, profile=profile, mode="init")

    # Output structured JSON for Agent to parse
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
