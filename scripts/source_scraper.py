#!/usr/bin/env python3
"""
Paper Source Scraper — Targeted Search & Two-Level Dedup.

Pure data acquisition tool: searches arXiv for new papers matching the
user's research profile, then performs two-level deduplication:
  1. Intra-run: merge duplicates from multiple keyword/author searches
  2. Cross-run: filter against seen_papers.json + seed_papers.json

Features:
  - Multi-keyword group search via ArxivSearcher
  - Whitelist author search (additional dimension)
  - Exponential backoff retry for rate limits
  - seen_papers.json corruption recovery
  - Detailed dedup statistics output

Usage:
    python source_scraper.py --run-id 20260301_100000
    python source_scraper.py --run-id 20260301_100000 --max-per-query 30
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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

logger = logging.getLogger("paper_agent.source_scraper")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

MAX_RETRIES = 3
BASE_RETRY_DELAY = 3.0  # seconds


# ═══════════════════════════════════════════════════════════════════════════════
# ArxivSearcher Wrapper
# ═══════════════════════════════════════════════════════════════════════════════


def _create_searcher():
    """Create an ArxivSearcher instance."""
    try:
        from arxiv_search import ArxivSearcher
        return ArxivSearcher()
    except ImportError as e:
        logger.error(
            "Failed to import ArxivSearcher: %s. "
            "Make sure arxiv-paper-search skill is installed.", e
        )
        raise


def search_with_retry(
    searcher,
    max_retries: int = MAX_RETRIES,
    **search_kwargs,
) -> List[Dict[str, Any]]:
    """Execute ArxivSearcher.search() with exponential backoff retry.

    Args:
        searcher: ArxivSearcher instance.
        max_retries: Maximum retry attempts.
        **search_kwargs: Arguments passed to searcher.search().

    Returns:
        List of search result dicts, or empty list on failure.
    """
    for attempt in range(max_retries):
        try:
            results = searcher.search(**search_kwargs)
            return results
        except Exception as e:
            error_str = str(e).lower()
            # Check for rate limit indicators
            is_rate_limit = any(
                indicator in error_str
                for indicator in ["429", "rate limit", "too many requests"]
            )

            delay = BASE_RETRY_DELAY * (2 ** attempt)
            logger.warning(
                "Search attempt %d/%d failed: %s%s. Retrying in %.1fs...",
                attempt + 1, max_retries, str(e),
                " (rate limited)" if is_rate_limit else "",
                delay,
            )
            if attempt < max_retries - 1:
                time.sleep(delay)

    logger.error("All %d search attempts failed", max_retries)
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# Search Execution
# ═══════════════════════════════════════════════════════════════════════════════


def _normalize_paper(paper: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Normalize a search result paper to the standard output format.

    ArxivSearcher returns keys like 'id', 'summary', etc.
    We normalize to our pipeline standard format.

    Args:
        paper: Raw search result from ArxivSearcher.
        source: Source tag (e.g., "keyword_search", "author_search").

    Returns:
        Normalized paper dict.
    """
    return {
        "arxiv_id": paper.get("id", ""),
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "abstract": paper.get("summary", ""),
        "url": paper.get("arxiv_url", ""),
        "source": source,
        "published_date": paper.get("published", ""),
        "categories": paper.get("categories", []),
        "comments": paper.get("comments", ""),
    }


def search_by_keywords(
    searcher,
    keywords: List[str],
    categories: List[str],
    search_days: int,
    max_per_query: int = 50,
) -> List[Dict[str, Any]]:
    """Execute keyword-based searches.

    Each keyword string is searched independently, results are collected.

    Args:
        searcher: ArxivSearcher instance.
        keywords: List of keyword strings.
        categories: arXiv categories to filter.
        search_days: How far back to search (days).
        max_per_query: Max results per query.

    Returns:
        List of normalized paper dicts (may contain duplicates).
    """
    all_papers = []

    for keyword in keywords:
        logger.info("Searching keyword: '%s'", keyword)
        results = search_with_retry(
            searcher,
            keywords=[keyword],
            categories=categories,
            days=search_days,
            max_results=max_per_query,
            sort_by="submitted",
        )
        for paper in results:
            all_papers.append(_normalize_paper(paper, f"keyword:{keyword}"))

        # Small delay between queries
        if keywords.index(keyword) < len(keywords) - 1:
            time.sleep(1.0)

    logger.info("Keyword search: %d raw results from %d queries", len(all_papers), len(keywords))
    return all_papers


def search_by_authors(
    searcher,
    authors: List[str],
    categories: List[str],
    search_days: int,
    max_per_query: int = 20,
) -> List[Dict[str, Any]]:
    """Execute author-based searches.

    Args:
        searcher: ArxivSearcher instance.
        authors: List of author names.
        categories: arXiv categories to filter.
        search_days: How far back to search.
        max_per_query: Max results per author query.

    Returns:
        List of normalized paper dicts.
    """
    all_papers = []

    for author in authors:
        logger.info("Searching author: '%s'", author)
        results = search_with_retry(
            searcher,
            author=author,
            categories=categories,
            days=search_days,
            max_results=max_per_query,
            sort_by="submitted",
        )
        for paper in results:
            all_papers.append(_normalize_paper(paper, f"author:{author}"))

        # Small delay between queries
        if authors.index(author) < len(authors) - 1:
            time.sleep(1.0)

    logger.info("Author search: %d raw results from %d queries", len(all_papers), len(authors))
    return all_papers


# ═══════════════════════════════════════════════════════════════════════════════
# Two-Level Deduplication
# ═══════════════════════════════════════════════════════════════════════════════


def dedup_intra_run(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Intra-run dedup: merge results from multiple searches by arXiv ID.

    When duplicates exist, the first occurrence is kept but the source
    field is updated to indicate multiple sources.

    Args:
        papers: List of paper dicts (may contain duplicates).

    Returns:
        Deduplicated list of paper dicts.
    """
    seen_ids: Dict[str, int] = {}  # arxiv_id -> index in result
    result: List[Dict[str, Any]] = []

    for paper in papers:
        arxiv_id = paper.get("arxiv_id", "")
        if not arxiv_id:
            continue

        if arxiv_id in seen_ids:
            # Update source to indicate multiple hits
            idx = seen_ids[arxiv_id]
            existing_source = result[idx].get("source", "")
            new_source = paper.get("source", "")
            if new_source and new_source not in existing_source:
                result[idx]["source"] = f"{existing_source}; {new_source}"
        else:
            seen_ids[arxiv_id] = len(result)
            result.append(paper)

    return result


def dedup_cross_run(
    papers: List[Dict[str, Any]],
    seen_ids: Set[str],
    seed_ids: Set[str],
) -> List[Dict[str, Any]]:
    """Cross-run dedup: filter against seen_papers.json + seed_papers.json.

    Args:
        papers: Intra-run deduplicated paper list.
        seen_ids: Set of arXiv IDs from seen_papers.json.
        seed_ids: Set of arXiv IDs from seed_papers.json.

    Returns:
        Papers that are truly new (not in seen or seed).
    """
    all_known_ids = seen_ids | seed_ids
    return [p for p in papers if p.get("arxiv_id", "") not in all_known_ids]


# ═══════════════════════════════════════════════════════════════════════════════
# Seen Papers Management
# ═══════════════════════════════════════════════════════════════════════════════


def load_seen_papers(seen_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load seen_papers.json, with corruption recovery.

    If the file is corrupted, attempts to rebuild from paper_index.json
    and seed_papers.json.

    Args:
        seen_path: Path to seen_papers.json.

    Returns:
        Dict mapping arxiv_id → record.
    """
    if not seen_path.exists():
        logger.info("seen_papers.json not found, initializing empty")
        return {}

    try:
        with open(seen_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("seen_papers.json is not a dict, attempting recovery")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("seen_papers.json corrupted: %s, attempting recovery", e)

    # Attempt recovery from paper_index.json + seed_papers.json
    return _recover_seen_papers(seen_path.parent)


def _recover_seen_papers(root: Path) -> Dict[str, Dict[str, Any]]:
    """Rebuild seen_papers.json from paper_index.json + seed_papers.json.

    Args:
        root: Paper-agent root directory.

    Returns:
        Recovered seen papers dict.
    """
    recovered = {}
    today = datetime.now().strftime("%Y-%m-%d")

    # Recover from paper_index.json
    index_path = root / "paper_index.json"
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
            if isinstance(index, list):
                for entry in index:
                    arxiv_id = entry.get("arxiv_id")
                    if arxiv_id and arxiv_id not in recovered:
                        recovered[arxiv_id] = {
                            "source": "recovered_from_index",
                            "first_seen_date": entry.get("indexed_at", today)[:10],
                            "first_seen_run_id": entry.get("run_id", "recovered"),
                        }
            logger.info("Recovered %d IDs from paper_index.json", len(recovered))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to recover from paper_index.json: %s", e)

    # Recover from seed_papers.json
    seed_path = root / "seed_papers.json"
    if seed_path.exists():
        try:
            with open(seed_path, "r", encoding="utf-8") as f:
                seeds = json.load(f)
            if isinstance(seeds, list):
                seed_count = 0
                for entry in seeds:
                    arxiv_id = entry.get("arxiv_id")
                    if arxiv_id and arxiv_id not in recovered:
                        recovered[arxiv_id] = {
                            "source": "seed",
                            "first_seen_date": today,
                            "first_seen_run_id": "recovered",
                        }
                        seed_count += 1
                logger.info("Recovered %d IDs from seed_papers.json", seed_count)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to recover from seed_papers.json: %s", e)

    logger.info("Total recovered: %d IDs", len(recovered))
    return recovered


def load_seed_ids(seed_path: Path) -> Set[str]:
    """Load arXiv IDs from seed_papers.json.

    Args:
        seed_path: Path to seed_papers.json.

    Returns:
        Set of seed paper arXiv IDs.
    """
    if not seed_path.exists():
        return set()

    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {p.get("arxiv_id", "") for p in data if p.get("arxiv_id")}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load seed_papers.json: %s", e)

    return set()


def register_new_papers_to_seen(
    papers: List[Dict[str, Any]],
    seen: Dict[str, Dict[str, Any]],
    seen_path: Path,
    run_id: str,
) -> int:
    """Register newly discovered papers to seen_papers.json.

    Args:
        papers: List of new paper dicts to register.
        seen: Current seen papers dict (will be mutated).
        seen_path: Path to write updated seen_papers.json.
        run_id: Current run ID.

    Returns:
        Number of newly registered papers.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    new_count = 0

    for paper in papers:
        arxiv_id = paper.get("arxiv_id", "")
        if arxiv_id and arxiv_id not in seen:
            seen[arxiv_id] = {
                "source": "search",
                "first_seen_date": today,
                "first_seen_run_id": run_id,
            }
            new_count += 1

    # Save updated seen_papers
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)

    logger.info("Registered %d new papers to seen_papers.json", new_count)
    return new_count


# ═══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def run_source_scraper(
    profile: Optional[Dict[str, Any]] = None,
    pm: Optional[PathManager] = None,
    searcher=None,
    max_per_query: int = 50,
) -> Dict[str, Any]:
    """Execute the source scraper pipeline.

    This is the main entry point, callable from CLI or pipeline_runner.py.

    Args:
        profile: Pre-loaded profile dict. If None, loads from default path.
        pm: PathManager with run_id set. If None, creates one.
        searcher: ArxivSearcher instance. If None, creates one.
        max_per_query: Max results per search query.

    Returns:
        Output dict with 'papers' list and 'stats' summary.
    """
    # Load profile if not provided
    if profile is None:
        profile = load_profile()

    # Create PathManager if not provided
    if pm is None:
        pm = PathManager()
        pm.create_run_directory()

    # Extract search config from profile
    keywords = profile.get("keywords", [])
    authors = profile.get("whitelist_authors", [])
    categories = profile.get("arxiv_categories", ["cs.IR", "cs.AI", "cs.CL", "cs.LG"])
    search_days = profile.get("search_days", 7)

    if not keywords and not authors:
        logger.warning("No keywords or authors configured in profile.yaml")
        output = {"papers": [], "stats": _empty_stats()}
        _save_output(output, pm.skill1_search_results)
        return output

    # Create searcher
    if searcher is None:
        try:
            searcher = _create_searcher()
        except Exception as e:
            logger.error("Failed to create ArxivSearcher: %s", e)
            output = {"papers": [], "stats": _empty_stats()}
            _save_output(output, pm.skill1_search_results)
            return output

    # ── Phase 1: Raw Search ──────────────────────────────────────────
    all_papers: List[Dict[str, Any]] = []

    if keywords:
        keyword_papers = search_by_keywords(
            searcher, keywords, categories, search_days, max_per_query
        )
        all_papers.extend(keyword_papers)

    if authors:
        author_papers = search_by_authors(
            searcher, authors, categories, search_days, max_per_query=20
        )
        all_papers.extend(author_papers)

    total_raw = len(all_papers)
    logger.info("Total raw search results: %d", total_raw)

    # ── Phase 2: Intra-run Dedup ─────────────────────────────────────
    deduped = dedup_intra_run(all_papers)
    after_intra = len(deduped)
    logger.info("After intra-run dedup: %d (removed %d)", after_intra, total_raw - after_intra)

    # ── Phase 3: Cross-run Dedup ─────────────────────────────────────
    seen = load_seen_papers(pm.seen_papers_json)
    seed_ids = load_seed_ids(pm.seed_papers_json)
    seen_ids = set(seen.keys())

    new_papers = dedup_cross_run(deduped, seen_ids, seed_ids)
    after_cross = len(new_papers)
    logger.info(
        "After cross-run dedup: %d (removed %d known papers)",
        after_cross, after_intra - after_cross
    )

    # ── Phase 4: Register to seen_papers.json ────────────────────────
    register_new_papers_to_seen(new_papers, seen, pm.seen_papers_json, pm.run_id)

    # ── Phase 5: Output ──────────────────────────────────────────────
    stats = {
        "total_raw": total_raw,
        "dedup_intra_run": after_intra,
        "dedup_cross_run": after_cross,
        "new_increment": after_cross,
    }

    output = {"papers": new_papers, "stats": stats}
    _save_output(output, pm.skill1_search_results)

    logger.info("Source scraper complete: %s", stats)
    return output


def _empty_stats() -> Dict[str, int]:
    """Return empty statistics dict."""
    return {
        "total_raw": 0,
        "dedup_intra_run": 0,
        "dedup_cross_run": 0,
        "new_increment": 0,
    }


def _save_output(output: Dict[str, Any], output_path: Path) -> None:
    """Save output to JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info("Output saved to %s", output_path)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Paper Source Scraper — Targeted Search & Two-Level Dedup"
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Run ID for this pipeline run",
    )
    parser.add_argument(
        "--max-per-query",
        type=int,
        default=50,
        help="Maximum results per search query (default: 50)",
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

    # Create PathManager
    pm = PathManager(run_id=args.run_id)
    pm.create_run_directory()

    # Run scraper
    output = run_source_scraper(
        profile=profile,
        pm=pm,
        max_per_query=args.max_per_query,
    )

    # Print summary
    stats = output.get("stats", {})
    print(f"\n{'=' * 50}")
    print("Source Scraper Summary")
    print(f"{'=' * 50}")
    print(f"  Raw search results:     {stats.get('total_raw', 0)}")
    print(f"  After intra-run dedup:  {stats.get('dedup_intra_run', 0)}")
    print(f"  After cross-run dedup:  {stats.get('dedup_cross_run', 0)}")
    print(f"  New increment papers:   {stats.get('new_increment', 0)}")
    print()


if __name__ == "__main__":
    main()
