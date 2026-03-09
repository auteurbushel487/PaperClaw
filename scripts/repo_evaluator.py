#!/usr/bin/env python3
"""Paper Repo Evaluator — Code Repository Assessment Tool.

Evaluates code repositories associated with academic papers.
This is the deterministic tool layer for the paper-repo-evaluator Skill.

Workflow:
  1. Extract GitHub/GitLab links from paper cached content or card.md
  2. If no link found, search GitHub for the paper title
  3. Call GitHub API to fetch repo metadata (stars, language, etc.)
  4. Generate quality assessment

Usage:
    # Evaluate repos for all papers in a pipeline run
    python repo_evaluator.py --run-id {run_id}

    # Evaluate a single paper
    python repo_evaluator.py --arxiv-id 2305.05065 --title "TIGER: ..."
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Standard path injection for paper-agent common modules
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from common.config_loader import load_profile
from common.path_manager import PathManager

logger = logging.getLogger("paper_agent.repo_evaluator")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# GitHub API configuration
GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_SEARCH_DELAY = 2.0  # seconds between API calls to avoid rate limit
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


# ═══════════════════════════════════════════════════════════════════════════════
# GitHub Link Extraction
# ═══════════════════════════════════════════════════════════════════════════════


# Regex patterns for code repository links
_GITHUB_PATTERN = re.compile(
    r"https?://github\.com/([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)(?:/|$|[)\]\s])",
    re.IGNORECASE,
)
_GITLAB_PATTERN = re.compile(
    r"https?://gitlab\.com/([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)(?:/|$|[)\]\s])",
    re.IGNORECASE,
)
_GENERIC_CODE_PATTERN = re.compile(
    r"(?:code|implementation|source\s*code|official\s*code)\s*(?:is\s+)?(?:available\s+)?(?:at\s+)?"
    r"(https?://[^\s)>\]]+)",
    re.IGNORECASE,
)


def extract_code_links(text: str) -> List[Dict[str, str]]:
    """Extract code repository links from text content.

    Args:
        text: Text content (abstract, card.md, etc.) to search.

    Returns:
        List of dicts with 'url', 'platform', and 'repo' keys.
    """
    links = []
    seen_repos = set()

    # Extract GitHub links
    for match in _GITHUB_PATTERN.finditer(text):
        repo = match.group(1).rstrip("/.)")
        # Filter out obviously wrong matches
        if "/" not in repo or any(skip in repo.lower() for skip in
                                  ["github.io", "github.com"]):
            continue
        if repo not in seen_repos:
            seen_repos.add(repo)
            links.append({
                "url": f"https://github.com/{repo}",
                "platform": "github",
                "repo": repo,
            })

    # Extract GitLab links
    for match in _GITLAB_PATTERN.finditer(text):
        repo = match.group(1).rstrip("/.)")
        if repo not in seen_repos:
            seen_repos.add(repo)
            links.append({
                "url": f"https://gitlab.com/{repo}",
                "platform": "gitlab",
                "repo": repo,
            })

    # Extract generic code links
    for match in _GENERIC_CODE_PATTERN.finditer(text):
        url = match.group(1).rstrip("/.)")
        # Check if it's a GitHub/GitLab URL we already found
        already_found = any(url.startswith(l["url"]) for l in links)
        if not already_found:
            links.append({
                "url": url,
                "platform": "other",
                "repo": url,
            })

    return links


# ═══════════════════════════════════════════════════════════════════════════════
# GitHub API Integration
# ═══════════════════════════════════════════════════════════════════════════════


def _github_api_request(endpoint: str) -> Optional[Dict[str, Any]]:
    """Make a GitHub API request with retry logic.

    Args:
        endpoint: API endpoint path (e.g., "/repos/owner/name").

    Returns:
        Parsed JSON response or None if failed.
    """
    url = f"{GITHUB_API_BASE}{endpoint}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "paper-agent/1.0",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.info("GitHub 404: %s", url)
                return None
            elif e.code == 403:
                # Rate limited
                logger.warning("GitHub rate limited, waiting %ds (attempt %d/%d)",
                               RETRY_DELAY * (attempt + 1), attempt + 1, MAX_RETRIES)
                time.sleep(RETRY_DELAY * (attempt + 1))
            elif e.code == 422:
                logger.warning("GitHub 422 (Unprocessable): %s", url)
                return None
            else:
                logger.warning("GitHub API error %d: %s (attempt %d/%d)",
                               e.code, url, attempt + 1, MAX_RETRIES)
                time.sleep(RETRY_DELAY)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            logger.warning("GitHub API network error: %s (attempt %d/%d)",
                           e, attempt + 1, MAX_RETRIES)
            time.sleep(RETRY_DELAY)

    return None


def fetch_github_repo_info(owner_repo: str) -> Optional[Dict[str, Any]]:
    """Fetch repository metadata from GitHub API.

    Args:
        owner_repo: Repository in "owner/name" format.

    Returns:
        Dict with repo metadata or None if failed.
    """
    data = _github_api_request(f"/repos/{owner_repo}")
    if data is None:
        return None

    return {
        "full_name": data.get("full_name", owner_repo),
        "description": data.get("description", ""),
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "language": data.get("language", ""),
        "license": (data.get("license") or {}).get("spdx_id", ""),
        "updated_at": data.get("updated_at", ""),
        "created_at": data.get("created_at", ""),
        "open_issues": data.get("open_issues_count", 0),
        "is_archived": data.get("archived", False),
        "is_fork": data.get("fork", False),
        "default_branch": data.get("default_branch", "main"),
        "topics": data.get("topics", []),
    }


def search_github_for_paper(title: str) -> Optional[Dict[str, str]]:
    """Search GitHub for a paper's code repository by title.

    Args:
        title: Paper title to search for.

    Returns:
        Dict with 'url', 'platform', 'repo' keys, or None.
    """
    if not title:
        return None

    # Clean title for search query
    clean_title = re.sub(r"[^\w\s]", " ", title)
    clean_title = " ".join(clean_title.split()[:8])  # First 8 words
    query = urllib.request.quote(f"{clean_title}")

    data = _github_api_request(f"/search/repositories?q={query}&sort=stars&per_page=3")
    if data is None or not data.get("items"):
        return None

    # Look for a plausible match
    for item in data["items"]:
        repo_name = item.get("full_name", "")
        desc = (item.get("description") or "").lower()
        repo_lower = repo_name.lower()

        # Check if description or repo name has overlap with paper title
        title_words = set(title.lower().split())
        desc_words = set(desc.split())
        overlap = title_words & desc_words

        if len(overlap) >= 3 or any(
            w in repo_lower for w in title.lower().split() if len(w) > 4
        ):
            return {
                "url": f"https://github.com/{repo_name}",
                "platform": "github",
                "repo": repo_name,
            }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Cost Assessment
# ═══════════════════════════════════════════════════════════════════════════════


def assess_integration_cost(repo_info: Dict[str, Any]) -> str:
    """Assess the cost of integrating a codebase.

    Based on stars, language, freshness, documentation quality signals.

    Args:
        repo_info: GitHub repo metadata dict.

    Returns:
        "Low", "Medium", or "High" integration cost assessment.
    """
    stars = repo_info.get("stars", 0)
    language = (repo_info.get("language") or "").lower()
    is_archived = repo_info.get("is_archived", False)
    has_license = bool(repo_info.get("license"))

    score = 0

    # Stars indicate community adoption / quality
    if stars >= 500:
        score += 3
    elif stars >= 100:
        score += 2
    elif stars >= 20:
        score += 1

    # Python is easy to integrate in our ML pipeline
    if language == "python":
        score += 2
    elif language in ("jupyter notebook", "python"):
        score += 2
    elif language in ("c++", "java", "rust"):
        score += 0

    # Archived = no longer maintained
    if is_archived:
        score -= 2

    # License present = can legally use
    if has_license:
        score += 1

    if score >= 4:
        return "Low"
    elif score >= 2:
        return "Medium"
    else:
        return "High"


# ═══════════════════════════════════════════════════════════════════════════════
# Single Paper Evaluation
# ═══════════════════════════════════════════════════════════════════════════════


def evaluate_paper_repo(
    arxiv_id: str,
    title: str = "",
    abstract: str = "",
    card_text: str = "",
) -> Dict[str, Any]:
    """Evaluate code repository for a single paper.

    Three-stage process:
    1. Extract code links from abstract/card content
    2. If no link found, search GitHub by paper title
    3. Fetch repo metadata and assess quality

    Args:
        arxiv_id: arXiv paper ID.
        title: Paper title.
        abstract: Paper abstract.
        card_text: Knowledge card content (card.md).

    Returns:
        Evaluation result dict.
    """
    result = {
        "arxiv_id": arxiv_id,
        "has_code": False,
        "github_url": None,
        "platform": None,
        "stars": 0,
        "forks": 0,
        "language": None,
        "license": None,
        "integration_cost": "N/A",
        "github_api_failed": False,
        "search_method": None,
    }

    # Stage 1: Extract links from text content
    all_text = f"{abstract}\n{card_text}"
    links = extract_code_links(all_text)

    if links:
        result["search_method"] = "extracted_from_text"
        link = links[0]  # Use the first (most relevant) link
        result["github_url"] = link["url"]
        result["platform"] = link["platform"]

        # Fetch detailed info for GitHub repos
        if link["platform"] == "github":
            repo_info = fetch_github_repo_info(link["repo"])
            if repo_info:
                result["has_code"] = True
                result["stars"] = repo_info["stars"]
                result["forks"] = repo_info["forks"]
                result["language"] = repo_info["language"]
                result["license"] = repo_info["license"]
                result["integration_cost"] = assess_integration_cost(repo_info)
                result["repo_details"] = repo_info
            else:
                # API failed but we have a URL
                result["has_code"] = True
                result["github_api_failed"] = True
                result["integration_cost"] = "Unknown"
        else:
            result["has_code"] = True
            result["integration_cost"] = "Unknown"

        return result

    # Stage 2: Search GitHub by paper title
    if title:
        logger.info("No code links found for %s, searching GitHub by title...", arxiv_id)
        time.sleep(GITHUB_SEARCH_DELAY)

        found = search_github_for_paper(title)
        if found:
            result["search_method"] = "github_search"
            result["github_url"] = found["url"]
            result["platform"] = found["platform"]

            repo_info = fetch_github_repo_info(found["repo"])
            if repo_info:
                result["has_code"] = True
                result["stars"] = repo_info["stars"]
                result["forks"] = repo_info["forks"]
                result["language"] = repo_info["language"]
                result["license"] = repo_info["license"]
                result["integration_cost"] = assess_integration_cost(repo_info)
                result["repo_details"] = repo_info
            else:
                result["has_code"] = True
                result["github_api_failed"] = True
                result["integration_cost"] = "Unknown"

            return result

    # Stage 3: No code found
    logger.info("No code repository found for %s", arxiv_id)
    result["search_method"] = "not_found"
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Batch Processing (for pipeline_runner.py integration)
# ═══════════════════════════════════════════════════════════════════════════════


def run_repo_eval(
    pm: PathManager,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate code repos for all selected papers in a pipeline run.

    Args:
        pm: PathManager for the current run.
        profile: Profile configuration.

    Returns:
        Statistics dict with evaluation results.
    """
    # Load final selection
    if not pm.skill3_final_selection.exists():
        logger.warning("Final selection not found: %s", pm.skill3_final_selection)
        return {"total": 0, "has_code_count": 0, "no_code_count": 0, "api_failed_count": 0}

    try:
        with open(pm.skill3_final_selection, "r", encoding="utf-8") as f:
            papers = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load final selection: %s", e)
        return {"total": 0, "has_code_count": 0, "no_code_count": 0, "api_failed_count": 0}

    pm.skill5_repo_eval_dir.mkdir(parents=True, exist_ok=True)

    has_code_count = 0
    no_code_count = 0
    api_failed_count = 0
    results = []

    # Also try to load parsed card data for richer text extraction
    for paper in papers:
        arxiv_id = paper.get("arxiv_id", "")
        if not arxiv_id:
            continue

        title = paper.get("title", "")
        abstract = paper.get("abstract", "")

        # Try to load card text from skill4 output
        card_text = ""
        card_parsed = pm.skill4_parsed_paper(arxiv_id)
        if card_parsed.exists():
            try:
                with open(card_parsed, "r", encoding="utf-8") as f:
                    card_data = json.load(f)
                card_path = card_data.get("card_path", "")
                if card_path and Path(card_path).exists():
                    card_text = Path(card_path).read_text(encoding="utf-8")
            except (json.JSONDecodeError, OSError):
                pass

        eval_result = evaluate_paper_repo(
            arxiv_id=arxiv_id,
            title=title,
            abstract=abstract,
            card_text=card_text,
        )

        # Save individual result
        output_path = pm.skill5_repo_eval_paper(arxiv_id)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(eval_result, f, indent=2, ensure_ascii=False)

        if eval_result.get("has_code"):
            has_code_count += 1
        else:
            no_code_count += 1
        if eval_result.get("github_api_failed"):
            api_failed_count += 1
        results.append(eval_result)

    stats = {
        "total": len(papers),
        "has_code_count": has_code_count,
        "no_code_count": no_code_count,
        "api_failed_count": api_failed_count,
    }
    logger.info("Repo evaluation complete: %s", stats)
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Paper Repo Evaluator — Code Repository Assessment"
    )
    parser.add_argument("--run-id", type=str, default=None, help="Pipeline run ID (batch mode)")
    parser.add_argument("--arxiv-id", type=str, default="", help="Single paper arXiv ID")
    parser.add_argument("--title", type=str, default="", help="Paper title (for GitHub search)")
    parser.add_argument("--profile", type=str, default=None, help="Path to profile.yaml")
    args = parser.parse_args()

    if args.run_id:
        # Batch mode
        try:
            profile = load_profile(args.profile)
        except (FileNotFoundError, ValueError) as e:
            logger.error("Failed to load profile: %s", e)
            sys.exit(1)

        pm = PathManager(run_id=args.run_id)
        stats = run_repo_eval(pm=pm, profile=profile)
        result = {
            "step": "repo-eval",
            "status": "success",
            "run_id": pm.run_id,
            **stats,
            "message": f"{stats['has_code_count']}/{stats['total']} papers have code repos.",
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.arxiv_id:
        # Single paper mode
        result = evaluate_paper_repo(arxiv_id=args.arxiv_id, title=args.title)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
