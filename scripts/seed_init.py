#!/usr/bin/env python3
"""
Paper Seed Init — Core Paper Directory Initializer.

Initializes and maintains the seed paper directory (seed_papers.json) that
serves as the academic taste anchor for the entire pipeline.

Supports two input modes:
  - Mode A: arXiv IDs from profile.yaml → auto-fetch metadata via ArxivSearcher
  - Mode B: Manual JSON entries already in seed_papers.json (preserved on update)

Features:
  - Incremental update: only fetches metadata for newly added arXiv IDs
  - Preserves user annotations (user_note, role, sub_field, key_concepts)
  - Auto-registers all seed paper IDs to seen_papers.json (source: "seed")
  - Exponential backoff retry for arXiv API rate limits

Usage:
    python seed_init.py               # Initialize or incremental update
    python seed_init.py --update      # Explicit incremental update
    python seed_init.py --force       # Force re-fetch all metadata
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Standard path injection for paper-agent common modules
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

# Inject arxiv-paper-search scripts for ArxivSearcher
_ARXIV_SKILL_PATH = Path(os.environ.get(
    "ARXIV_SKILL_PATH",
    str(Path("/projects/.openclaw/skills/arxiv-paper-search/scripts"))
))
if str(_ARXIV_SKILL_PATH) not in sys.path:
    sys.path.insert(0, str(_ARXIV_SKILL_PATH))

from common.config_loader import load_profile
from common.path_manager import PathManager

logger = logging.getLogger("paper_agent.seed_init")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Default fields for a new seed paper entry
SEED_PAPER_DEFAULTS = {
    "user_note": "",
    "role": "foundational",
    "sub_field": "generative_rec",
    "key_concepts": [],
    "has_card": False,
    "card_path": "",
}

# Max retries for arXiv API calls
MAX_RETRIES = 3
BASE_RETRY_DELAY = 3.0  # seconds


# ═══════════════════════════════════════════════════════════════════════════════
# ArxivSearcher Wrapper with Retry
# ═══════════════════════════════════════════════════════════════════════════════


def _import_arxiv_searcher():
    """Import ArxivSearcher lazily to handle import errors gracefully."""
    try:
        from arxiv_search import ArxivSearcher
        return ArxivSearcher
    except ImportError as e:
        logger.error(
            "Failed to import ArxivSearcher: %s. "
            "Make sure arxiv-paper-search skill is installed.", e
        )
        raise


def fetch_paper_metadata(
    arxiv_id: str,
    searcher=None,
    max_retries: int = MAX_RETRIES,
) -> Optional[Dict[str, Any]]:
    """Fetch metadata for a single paper from arXiv API.

    Args:
        arxiv_id: The arXiv paper ID (e.g., "2305.05065").
        searcher: ArxivSearcher instance (created if None).
        max_retries: Maximum retry attempts for API failures.

    Returns:
        Paper metadata dict, or None if fetch failed after all retries.
    """
    if searcher is None:
        ArxivSearcher = _import_arxiv_searcher()
        searcher = ArxivSearcher()

    for attempt in range(max_retries):
        try:
            results = searcher.search(
                arxiv_id=arxiv_id,
                max_results=1,
                days=36500,  # Very large window to find any paper
            )

            if not results:
                logger.warning("No results found for arXiv ID: %s", arxiv_id)
                return None

            paper = results[0]
            return {
                "arxiv_id": arxiv_id,
                "title": paper.get("title", ""),
                "authors": paper.get("authors", []),
                "abstract": paper.get("summary", ""),
                "url": paper.get("arxiv_url", f"https://arxiv.org/abs/{arxiv_id}"),
                "published_date": paper.get("published", ""),
                "categories": paper.get("categories", []),
                "comments": paper.get("comments", ""),
            }

        except Exception as e:
            delay = BASE_RETRY_DELAY * (2 ** attempt)
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1fs...",
                attempt + 1, max_retries, arxiv_id, str(e), delay
            )
            if attempt < max_retries - 1:
                time.sleep(delay)

    logger.error("Failed to fetch metadata for %s after %d attempts", arxiv_id, max_retries)
    return None


def fetch_papers_batch(
    arxiv_ids: List[str],
    searcher=None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch metadata for multiple papers.

    Args:
        arxiv_ids: List of arXiv IDs to fetch.
        searcher: ArxivSearcher instance (created if None).

    Returns:
        Dict mapping arxiv_id → metadata dict. Failed fetches are excluded.
    """
    if searcher is None:
        ArxivSearcher = _import_arxiv_searcher()
        searcher = ArxivSearcher()

    results = {}
    for i, arxiv_id in enumerate(arxiv_ids):
        logger.info("Fetching [%d/%d]: %s", i + 1, len(arxiv_ids), arxiv_id)
        metadata = fetch_paper_metadata(arxiv_id, searcher=searcher)
        if metadata:
            results[arxiv_id] = metadata
        else:
            logger.warning("Skipping %s: metadata fetch failed", arxiv_id)

        # Small delay between requests to respect rate limits
        if i < len(arxiv_ids) - 1:
            time.sleep(1.0)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Seed Papers Management
# ═══════════════════════════════════════════════════════════════════════════════


def load_existing_seed_papers(seed_path: Path) -> List[Dict[str, Any]]:
    """Load existing seed_papers.json if it exists.

    Args:
        seed_path: Path to seed_papers.json.

    Returns:
        List of existing seed paper entries, or empty list.
    """
    if not seed_path.exists():
        return []

    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        logger.warning("seed_papers.json is not a list, returning empty")
        return []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load existing seed_papers.json: %s", e)
        return []


def build_existing_index(
    seed_papers: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Build an index of existing seed papers by arxiv_id.

    Args:
        seed_papers: List of seed paper dicts.

    Returns:
        Dict mapping arxiv_id → paper entry.
    """
    index = {}
    for paper in seed_papers:
        arxiv_id = paper.get("arxiv_id")
        if arxiv_id:
            index[arxiv_id] = paper
    return index


def detect_new_ids(
    profile_ids: List[str],
    existing_index: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Detect which arXiv IDs in the profile are new (not yet in seed_papers.json).

    Args:
        profile_ids: arXiv IDs from profile.yaml.
        existing_index: Index of existing seed papers.

    Returns:
        List of new arXiv IDs that need metadata fetching.
    """
    return [aid for aid in profile_ids if aid not in existing_index]


def merge_seed_papers(
    existing_index: Dict[str, Dict[str, Any]],
    new_metadata: Dict[str, Dict[str, Any]],
    profile_ids: List[str],
) -> List[Dict[str, Any]]:
    """Merge existing seed papers with newly fetched metadata.

    Preserves user annotations from existing entries.
    New entries get default field values.
    Order follows profile_ids, then any extra entries from existing file.

    Args:
        existing_index: Existing seed papers indexed by arxiv_id.
        new_metadata: Newly fetched metadata indexed by arxiv_id.
        profile_ids: Ordered arXiv IDs from profile.yaml.

    Returns:
        Merged list of seed paper entries.
    """
    result = []
    seen_ids: Set[str] = set()

    # First, process all IDs from profile.yaml (in order)
    for arxiv_id in profile_ids:
        if arxiv_id in seen_ids:
            continue
        seen_ids.add(arxiv_id)

        if arxiv_id in existing_index:
            # Keep existing entry (preserves user annotations)
            entry = existing_index[arxiv_id]
            # Update metadata fields if we have new data
            if arxiv_id in new_metadata:
                meta = new_metadata[arxiv_id]
                for key in ["title", "authors", "abstract", "url",
                            "published_date", "categories", "comments"]:
                    if key in meta and meta[key]:
                        entry[key] = meta[key]
            result.append(entry)
        elif arxiv_id in new_metadata:
            # Create new entry with defaults + fetched metadata
            entry = {**new_metadata[arxiv_id], **SEED_PAPER_DEFAULTS}
            # Ensure metadata fields override defaults
            entry.update(new_metadata[arxiv_id])
            result.append(entry)
        else:
            # ID in profile but metadata fetch failed — create skeleton
            entry = {
                "arxiv_id": arxiv_id,
                "title": f"[Pending] {arxiv_id}",
                "authors": [],
                "abstract": "",
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "published_date": "",
                "categories": [],
                "comments": "",
                **SEED_PAPER_DEFAULTS,
            }
            result.append(entry)

    # Then, add any extra entries from existing file (Mode B: manual entries)
    for arxiv_id, entry in existing_index.items():
        if arxiv_id not in seen_ids:
            seen_ids.add(arxiv_id)
            result.append(entry)

    return result


def save_seed_papers(seed_papers: List[Dict[str, Any]], seed_path: Path) -> None:
    """Save seed papers to JSON file.

    Args:
        seed_papers: List of seed paper dicts.
        seed_path: Output path for seed_papers.json.
    """
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    with open(seed_path, "w", encoding="utf-8") as f:
        json.dump(seed_papers, f, indent=2, ensure_ascii=False)
    logger.info("Saved %d seed papers to %s", len(seed_papers), seed_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Seen Papers Registration
# ═══════════════════════════════════════════════════════════════════════════════


def load_seen_papers(seen_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load existing seen_papers.json.

    Args:
        seen_path: Path to seen_papers.json.

    Returns:
        Dict mapping arxiv_id → seen record.
    """
    if not seen_path.exists():
        return {}

    try:
        with open(seen_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("seen_papers.json is not a dict, returning empty")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load seen_papers.json: %s", e)
        return {}


def register_seed_ids_to_seen(
    seed_papers: List[Dict[str, Any]],
    seen_path: Path,
) -> int:
    """Register all seed paper IDs to seen_papers.json.

    Only adds entries for IDs not already present.

    Args:
        seed_papers: List of seed paper dicts.
        seen_path: Path to seen_papers.json.

    Returns:
        Number of newly registered IDs.
    """
    seen = load_seen_papers(seen_path)
    new_count = 0
    today = datetime.now().strftime("%Y-%m-%d")

    for paper in seed_papers:
        arxiv_id = paper.get("arxiv_id")
        if arxiv_id and arxiv_id not in seen:
            seen[arxiv_id] = {
                "source": "seed",
                "first_seen_date": today,
                "first_seen_run_id": "seed_init",
            }
            new_count += 1

    # Save
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)

    logger.info(
        "Registered %d new seed IDs to seen_papers.json (total: %d)",
        new_count, len(seen)
    )
    return new_count


# ═══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def run_seed_init(
    profile: Optional[Dict[str, Any]] = None,
    pm: Optional[PathManager] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Execute seed paper initialization.

    This is the main entry point, callable both from CLI and from
    pipeline_runner.py.

    Args:
        profile: Pre-loaded profile dict. If None, loads from default path.
        pm: PathManager instance. If None, creates one with default root.
        force: If True, re-fetch metadata for all papers (not just new ones).

    Returns:
        Summary dict with statistics.
    """
    # Load profile if not provided
    if profile is None:
        profile = load_profile()

    # Create PathManager if not provided
    if pm is None:
        pm = PathManager(run_id="seed_init")

    seed_path = pm.seed_papers_json
    seen_path = pm.seen_papers_json

    # Get arXiv IDs from profile
    profile_ids = profile.get("seed_papers", [])
    if not profile_ids:
        logger.warning("No seed_papers found in profile.yaml")
        return {"seed_papers_count": 0, "new_fetched": 0, "registered": 0}

    logger.info("Profile contains %d seed paper IDs", len(profile_ids))

    # Load existing seed papers
    existing = load_existing_seed_papers(seed_path)
    existing_index = build_existing_index(existing)
    logger.info("Found %d existing seed papers", len(existing_index))

    # Determine which IDs need fetching
    if force:
        ids_to_fetch = profile_ids
        logger.info("Force mode: re-fetching all %d papers", len(ids_to_fetch))
    else:
        ids_to_fetch = detect_new_ids(profile_ids, existing_index)
        logger.info("Incremental update: %d new IDs to fetch", len(ids_to_fetch))

    # Fetch metadata for new papers
    new_metadata = {}
    if ids_to_fetch:
        try:
            new_metadata = fetch_papers_batch(ids_to_fetch)
        except Exception as e:
            logger.error("ArxivSearcher import/init failed: %s", e)
            logger.info("Continuing with skeleton entries for unfetched papers")

    # Merge and save
    merged = merge_seed_papers(existing_index, new_metadata, profile_ids)
    save_seed_papers(merged, seed_path)

    # Register to seen_papers.json
    new_registered = register_seed_ids_to_seen(merged, seen_path)

    summary = {
        "seed_papers_count": len(merged),
        "new_fetched": len(new_metadata),
        "fetch_failed": len(ids_to_fetch) - len(new_metadata),
        "registered_to_seen": new_registered,
    }

    logger.info("Seed init complete: %s", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Paper Seed Init — Core Paper Directory Initializer"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Explicit incremental update (default behavior)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-fetch metadata for all papers",
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

    summary = run_seed_init(profile=profile, force=args.force)

    # Print summary
    print(f"\n{'=' * 50}")
    print("Seed Init Summary")
    print(f"{'=' * 50}")
    print(f"  Total seed papers: {summary['seed_papers_count']}")
    print(f"  Newly fetched:     {summary['new_fetched']}")
    print(f"  Fetch failed:      {summary['fetch_failed']}")
    print(f"  Registered to seen: {summary['registered_to_seen']}")
    print()


if __name__ == "__main__":
    main()
