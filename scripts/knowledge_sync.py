#!/usr/bin/env python3
"""Paper Knowledge Sync — Knowledge Base & Idea Generation Tool.

Syncs paper analysis results into a persistent knowledge base
(paper_index.json) and generates research idea proposals.
This is the deterministic tool layer for the paper-knowledge-sync Skill.

The Agent (via SKILL.md) orchestrates:
  1. Call this script with --sync to index parsed papers
  2. Agent reads the context file and uses LLM to generate ideas
  3. Call this script with --save-ideas to persist the generated ideas

Usage:
    # Sync papers to knowledge base for a run
    python knowledge_sync.py --sync --run-id {run_id}

    # Prepare idea generation context (for Agent LLM)
    python knowledge_sync.py --prepare-ideas --run-id {run_id}

    # Save Agent-generated ideas to file
    python knowledge_sync.py --save-ideas --run-id {run_id} --ideas-text "..."
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

logger = logging.getLogger("paper_agent.knowledge_sync")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ═══════════════════════════════════════════════════════════════════════════════
# Paper Index Management
# ═══════════════════════════════════════════════════════════════════════════════


def load_paper_index(index_path: Path) -> List[Dict[str, Any]]:
    """Load the paper knowledge base index.

    Args:
        index_path: Path to paper_index.json.

    Returns:
        List of indexed paper records.
    """
    if not index_path.exists():
        return []
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        logger.warning("paper_index.json is not a list, resetting")
        return []
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load paper_index.json: %s", e)
        return []


def save_paper_index(index_path: Path, index_data: List[Dict[str, Any]]) -> None:
    """Save the paper knowledge base index.

    Args:
        index_path: Path to paper_index.json.
        index_data: List of paper records to save.
    """
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
    logger.info("Paper index saved: %d entries", len(index_data))


def detect_paper_relations(
    new_paper: Dict[str, Any],
    existing_papers: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Detect relationships between a new paper and existing indexed papers.

    Uses simple heuristics:
    - Same sub_field
    - Shared baselines
    - Shared techniques
    - Same authors

    Args:
        new_paper: The new paper record to check.
        existing_papers: List of already-indexed papers.

    Returns:
        List of relation dicts with 'arxiv_id', 'relation_type', 'detail'.
    """
    relations = []
    new_sub = new_paper.get("sub_field", "N/A")
    new_baselines = set(new_paper.get("baselines_compared", []))
    new_techniques = set(new_paper.get("transferable_techniques", []))
    new_authors = set(new_paper.get("authors", []))

    for existing in existing_papers:
        ex_id = existing.get("arxiv_id", "")
        if ex_id == new_paper.get("arxiv_id"):
            continue

        relation_types = []

        # Same sub-field
        if new_sub != "N/A" and existing.get("sub_field") == new_sub:
            relation_types.append(("same_subfield", f"Both in {new_sub}"))

        # Shared baselines
        ex_baselines = set(existing.get("baselines_compared", []))
        shared_bl = (new_baselines & ex_baselines) - {"N/A"}
        if shared_bl:
            relation_types.append(("shared_baselines", f"Share baselines: {', '.join(shared_bl)}"))

        # Shared techniques
        ex_techniques = set(existing.get("transferable_techniques", []))
        shared_tech = (new_techniques & ex_techniques) - {"N/A"}
        if shared_tech:
            relation_types.append(("shared_techniques", f"Share techniques: {', '.join(shared_tech)}"))

        # Shared authors
        ex_authors = set(existing.get("authors", []))
        shared_auth = new_authors & ex_authors
        if shared_auth:
            relation_types.append(("same_authors", f"Authors: {', '.join(shared_auth)}"))

        for rtype, detail in relation_types:
            relations.append({
                "related_arxiv_id": ex_id,
                "relation_type": rtype,
                "detail": detail,
            })

    return relations


# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge Sync (paper index update)
# ═══════════════════════════════════════════════════════════════════════════════


def sync_papers_to_index(
    pm: PathManager,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Sync parsed papers from current run into global paper_index.json.

    Reads skill4_parsed/ and skill5_repo_eval/ outputs, merges them
    into enriched records, detects relations, and appends to index.

    Args:
        pm: PathManager for the current run.
        profile: Profile configuration.

    Returns:
        Statistics dict.
    """
    index_path = pm.paper_index_json
    paper_index = load_paper_index(index_path)

    # Build set of already-indexed paper IDs
    indexed_ids = {p.get("arxiv_id") for p in paper_index}

    # Load final selection for base paper info
    papers_data = {}
    if pm.skill3_final_selection.exists():
        try:
            with open(pm.skill3_final_selection, "r", encoding="utf-8") as f:
                for p in json.load(f):
                    papers_data[p.get("arxiv_id", "")] = p
        except (json.JSONDecodeError, OSError):
            pass

    # Load parsed card data
    parsed_data = {}
    if pm.skill4_parsed_dir.exists():
        for card_file in pm.skill4_parsed_dir.glob("*.json"):
            try:
                with open(card_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                parsed_data[data.get("arxiv_id", card_file.stem)] = data
            except (json.JSONDecodeError, OSError):
                continue

    # Load repo eval data
    repo_data = {}
    if pm.skill5_repo_eval_dir.exists():
        for eval_file in pm.skill5_repo_eval_dir.glob("*.json"):
            try:
                with open(eval_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                repo_data[data.get("arxiv_id", eval_file.stem)] = data
            except (json.JSONDecodeError, OSError):
                continue

    # Merge all sources into enriched records
    new_count = 0
    updated_count = 0
    all_arxiv_ids = set(papers_data.keys()) | set(parsed_data.keys())

    for arxiv_id in all_arxiv_ids:
        if not arxiv_id:
            continue

        base = papers_data.get(arxiv_id, {})
        parsed = parsed_data.get(arxiv_id, {})
        repo = repo_data.get(arxiv_id, {})

        record = {
            "arxiv_id": arxiv_id,
            "title": base.get("title") or parsed.get("title", ""),
            "authors": base.get("authors", []),
            "abstract": base.get("abstract", "")[:500],
            "url": base.get("url", f"https://arxiv.org/abs/{arxiv_id}"),
            "score": base.get("score", 0),
            "sub_field": parsed.get("sub_field", "N/A"),
            "ID_paradigm": parsed.get("ID_paradigm", "N/A"),
            "item_tokenizer": parsed.get("item_tokenizer", "N/A"),
            "baselines_compared": parsed.get("baselines_compared", ["N/A"]),
            "transferable_techniques": parsed.get("transferable_techniques", ["N/A"]),
            "inspiration_ideas": parsed.get("inspiration_ideas", ["N/A"]),
            "card_path": parsed.get("card_path", ""),
            "has_code": repo.get("has_code", False),
            "github_url": repo.get("github_url"),
            "stars": repo.get("stars", 0),
            "language": repo.get("language"),
            "integration_cost": repo.get("integration_cost", "N/A"),
            "indexed_at": datetime.now().isoformat(),
            "run_id": pm.run_id,
        }

        # Detect relations
        relations = detect_paper_relations(record, paper_index)
        if relations:
            record["relations"] = relations

        if arxiv_id in indexed_ids:
            # Update existing entry
            for i, p in enumerate(paper_index):
                if p.get("arxiv_id") == arxiv_id:
                    paper_index[i] = record
                    updated_count += 1
                    break
        else:
            paper_index.append(record)
            indexed_ids.add(arxiv_id)
            new_count += 1

    save_paper_index(index_path, paper_index)

    stats = {
        "total_indexed": len(paper_index),
        "new_count": new_count,
        "updated_count": updated_count,
        "with_code": sum(1 for p in paper_index if p.get("has_code")),
        "with_relations": sum(1 for p in paper_index if p.get("relations")),
    }
    logger.info("Knowledge sync complete: %s", stats)
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# Idea Generation Context (for Agent LLM)
# ═══════════════════════════════════════════════════════════════════════════════


def prepare_idea_context(
    pm: PathManager,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Prepare context for Agent LLM idea generation.

    Assembles seed papers, recent additions, transferable techniques,
    and research description into a structured context.

    Args:
        pm: PathManager for the current run.
        profile: Profile configuration.

    Returns:
        Context dict for idea generation.
    """
    # Load seed papers
    seed_papers = []
    if pm.seed_papers_json.exists():
        try:
            with open(pm.seed_papers_json, "r", encoding="utf-8") as f:
                seed_papers = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Load paper index
    paper_index = load_paper_index(pm.paper_index_json)

    # Get recent papers from this run
    recent_papers = [p for p in paper_index if p.get("run_id") == pm.run_id]

    # Collect all transferable techniques
    all_techniques = []
    for p in paper_index:
        techs = p.get("transferable_techniques", [])
        for t in techs:
            if t != "N/A":
                all_techniques.append({
                    "technique": t,
                    "from_paper": p.get("title", p.get("arxiv_id", "")),
                    "arxiv_id": p.get("arxiv_id", ""),
                })

    # Collect inspiration ideas from parsed cards
    all_inspirations = []
    for p in paper_index:
        ideas = p.get("inspiration_ideas", [])
        for idea in ideas:
            if idea != "N/A":
                all_inspirations.append({
                    "idea": idea,
                    "from_paper": p.get("title", p.get("arxiv_id", "")),
                })

    context = {
        "run_id": pm.run_id,
        "research_description": profile.get("research_description", ""),
        "seed_papers_count": len(seed_papers),
        "seed_papers": [
            {"arxiv_id": s.get("arxiv_id", ""), "title": s.get("title", ""),
             "user_note": s.get("user_note", ""), "key_concepts": s.get("key_concepts", [])}
            for s in seed_papers[:10]
        ],
        "recent_papers_count": len(recent_papers),
        "recent_papers": [
            {"arxiv_id": p.get("arxiv_id", ""), "title": p.get("title", ""),
             "sub_field": p.get("sub_field", "N/A"), "score": p.get("score", 0)}
            for p in recent_papers
        ],
        "total_indexed": len(paper_index),
        "transferable_techniques": all_techniques[:20],
        "inspiration_ideas": all_inspirations[:20],
        "prompt": _build_idea_prompt(profile, seed_papers, recent_papers, all_techniques),
    }

    # Save context file
    context_path = pm.run_dir / "skill6_idea_context.json"
    with open(context_path, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, ensure_ascii=False)

    return {
        "context_path": str(context_path),
        "recent_papers_count": len(recent_papers),
        "techniques_count": len(all_techniques),
        "inspirations_count": len(all_inspirations),
    }


def _build_idea_prompt(
    profile: Dict[str, Any],
    seed_papers: List[Dict[str, Any]],
    recent_papers: List[Dict[str, Any]],
    techniques: List[Dict[str, str]],
) -> str:
    """Build the idea generation prompt for Agent LLM.

    Args:
        profile: Profile config with research description.
        seed_papers: Seed paper list.
        recent_papers: Recently indexed papers.
        techniques: Transferable techniques collected.

    Returns:
        Prompt string.
    """
    research_desc = profile.get("research_description", "N/A")

    seed_section = ""
    for s in seed_papers[:5]:
        seed_section += f"  - [{s.get('arxiv_id', '')}] {s.get('title', '')}\n"
        if s.get("key_concepts"):
            seed_section += f"    Key concepts: {', '.join(s['key_concepts'])}\n"

    recent_section = ""
    for p in recent_papers[:10]:
        recent_section += (
            f"  - [{p.get('arxiv_id', '')}] {p.get('title', '')} "
            f"(score: {p.get('score', 0)}, field: {p.get('sub_field', 'N/A')})\n"
        )

    tech_section = ""
    for t in techniques[:15]:
        tech_section += f"  - {t['technique']} (from: {t['from_paper']})\n"

    prompt = f"""You are a research idea generator. Based on the following context,
generate 2-3 novel research idea proposals that combine insights from the
user's core research with techniques found in recent papers.

## Research Focus
{research_desc}

## Core Papers (seeds)
{seed_section}

## Recently Discovered Papers (this run)
{recent_section}

## Transferable Techniques
{tech_section}

## Output Format
For each idea, provide:
1. **Title**: A concise, descriptive title
2. **Motivation**: Why this idea is worth pursuing (1-2 sentences)
3. **Key Insight**: What cross-paper insight enables this idea
4. **Approach**: High-level methodology (2-3 sentences)
5. **Feasibility**: Low / Medium / High with brief justification
6. **Related Papers**: Which papers from above inspired this idea

Generate exactly 2-3 ideas. Be creative but grounded in the available evidence."""

    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
# Idea Persistence
# ═══════════════════════════════════════════════════════════════════════════════


def save_ideas(
    pm: PathManager,
    ideas_text: str,
) -> Dict[str, Any]:
    """Save Agent-generated ideas to a Markdown file.

    Args:
        pm: PathManager for the current run.
        ideas_text: Agent-generated idea text (Markdown).

    Returns:
        Result dict with file path.
    """
    pm.ideas_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    idea_path = pm.ideas_dir / f"{date_str}_idea_proposal.md"

    # Add header
    content = (
        f"# Idea Proposal -- {date_str}\n\n"
        f"Generated from Pipeline Run: {pm.run_id}\n\n"
        f"---\n\n"
        f"{ideas_text}\n"
    )

    with open(idea_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info("Ideas saved to %s", idea_path)
    return {
        "idea_path": str(idea_path),
        "ideas_saved": True,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Combined Run Function (for pipeline_runner.py integration)
# ═══════════════════════════════════════════════════════════════════════════════


def run_knowledge_sync(
    pm: PathManager,
    profile: Dict[str, Any],
    ideas_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full knowledge sync workflow.

    1. Sync papers to index
    2. Prepare idea generation context
    3. If ideas_text provided, save ideas

    Args:
        pm: PathManager for the current run.
        profile: Profile configuration.
        ideas_text: Pre-generated ideas text (optional).

    Returns:
        Combined statistics.
    """
    # Step 1: Sync
    sync_stats = sync_papers_to_index(pm, profile)

    # Step 2: Prepare idea context
    idea_ctx = prepare_idea_context(pm, profile)

    # Step 3: Save ideas if provided
    ideas_saved = False
    idea_path = ""
    if ideas_text:
        result = save_ideas(pm, ideas_text)
        ideas_saved = result["ideas_saved"]
        idea_path = result["idea_path"]

    return {
        **sync_stats,
        "idea_context_path": idea_ctx.get("context_path", ""),
        "ideas_saved": ideas_saved,
        "idea_path": idea_path,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Paper Knowledge Sync -- Knowledge Base & Idea Generation"
    )
    parser.add_argument("--sync", action="store_true", help="Sync papers to knowledge index")
    parser.add_argument("--prepare-ideas", action="store_true", help="Prepare idea generation context")
    parser.add_argument("--save-ideas", action="store_true", help="Save generated ideas")
    parser.add_argument("--ideas-text", type=str, default="", help="Agent-generated ideas text")
    parser.add_argument("--run-id", type=str, default=None, help="Pipeline run ID")
    parser.add_argument("--profile", type=str, default=None, help="Path to profile.yaml")
    args = parser.parse_args()

    try:
        profile = load_profile(args.profile)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Failed to load profile: %s", e)
        sys.exit(1)

    if not args.run_id:
        logger.error("--run-id is required")
        sys.exit(1)

    pm = PathManager(run_id=args.run_id)

    if args.sync:
        stats = sync_papers_to_index(pm, profile)
        result = {
            "step": "knowledge-sync",
            "mode": "sync",
            "status": "success",
            "run_id": pm.run_id,
            **stats,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.prepare_ideas:
        ctx = prepare_idea_context(pm, profile)
        result = {
            "step": "knowledge-sync",
            "mode": "prepare-ideas",
            "status": "success",
            "run_id": pm.run_id,
            **ctx,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.save_ideas:
        if not args.ideas_text:
            logger.error("--ideas-text is required with --save-ideas")
            sys.exit(1)
        result_data = save_ideas(pm, args.ideas_text)
        result = {
            "step": "knowledge-sync",
            "mode": "save-ideas",
            "status": "success",
            "run_id": pm.run_id,
            **result_data,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))

    else:
        # Default: full sync + prepare ideas
        stats = run_knowledge_sync(pm, profile, ideas_text=args.ideas_text or None)
        result = {
            "step": "knowledge-sync",
            "mode": "full",
            "status": "success",
            "run_id": pm.run_id,
            **stats,
            "message": (
                f"Indexed {stats['new_count']} new + {stats['updated_count']} updated papers. "
                f"Total: {stats['total_indexed']}. "
                f"Ideas {'saved' if stats['ideas_saved'] else 'context prepared (pending Agent generation)'}."
            ),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
