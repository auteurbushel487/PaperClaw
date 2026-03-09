"""
Configuration loader for paper-agent.

Provides readers for:
- profile.yaml: Research interest profile with field validation
- seed_papers.json: Core paper directory reader with type checking
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

# Standard path injection for paper-agent common modules
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

logger = logging.getLogger("paper_agent.config_loader")

# Required fields in profile.yaml
PROFILE_REQUIRED_FIELDS = [
    "research_description",
    "seed_papers",
    "keywords",
]

# Optional fields with defaults
PROFILE_DEFAULTS: Dict[str, Any] = {
    "whitelist_authors": [],
    "arxiv_categories": ["cs.IR", "cs.AI", "cs.CL", "cs.LG"],
    "search_days": 7,
    "top_venues": [
        "NeurIPS", "ICML", "ICLR", "KDD", "WWW", "SIGIR",
        "RecSys", "CIKM", "WSDM", "ACL", "EMNLP", "AAAI",
    ],
    "score_thresholds": {"high": 7, "edge_low": 4, "edge_high": 6, "low": 3},
    "notification_channel": "local",
    "knowledge_base_backend": "local_markdown",
    "idea_generation_frequency": "per_run",
    "human_review_wait_days": 3,
    "human_review_default_policy": "discard",
}

# Required fields for each seed paper entry
SEED_PAPER_REQUIRED_FIELDS = ["arxiv_id", "title"]


def _load_yaml(path: str) -> Dict[str, Any]:
    """Load a YAML file, with fallback to basic parsing if PyYAML not available."""
    if yaml is not None:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    else:
        # Minimal fallback: try to parse as JSON (profile.yaml might be JSON-compatible)
        logger.warning("PyYAML not installed, attempting JSON fallback for %s", path)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def load_profile(
    profile_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Load and validate research interest profile from profile.yaml.

    Args:
        profile_path: Path to profile.yaml. Defaults to PAPER_AGENT_ROOT/profile.yaml.

    Returns:
        Validated profile dict with defaults applied for optional fields.

    Raises:
        FileNotFoundError: If profile.yaml does not exist.
        ValueError: If required fields are missing or have wrong types.
    """
    if profile_path is None:
        profile_path = str(_PAPER_AGENT_ROOT / "profile.yaml")

    if not os.path.exists(profile_path):
        raise FileNotFoundError(
            f"Profile not found: {profile_path}. "
            f"Please create it from the template."
        )

    profile = _load_yaml(profile_path)

    # Validate required fields
    missing = [f for f in PROFILE_REQUIRED_FIELDS if f not in profile]
    if missing:
        raise ValueError(
            f"Missing required fields in profile.yaml: {missing}"
        )

    # Type validation
    if not isinstance(profile["research_description"], str):
        raise ValueError("'research_description' must be a string")
    if not isinstance(profile["seed_papers"], list):
        raise ValueError("'seed_papers' must be a list of arXiv IDs")
    if not isinstance(profile["keywords"], list):
        raise ValueError("'keywords' must be a list of keyword groups")

    # Apply defaults for optional fields
    for key, default in PROFILE_DEFAULTS.items():
        if key not in profile:
            profile[key] = default
            logger.debug("Applied default for '%s': %s", key, default)

    logger.info("Profile loaded from %s", profile_path)
    return profile


def load_seed_papers(
    seed_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load and validate seed papers from seed_papers.json.

    Args:
        seed_path: Path to seed_papers.json. Defaults to PAPER_AGENT_ROOT/seed_papers.json.

    Returns:
        List of validated seed paper dicts.

    Raises:
        FileNotFoundError: If seed_papers.json does not exist.
        ValueError: If data is malformed or entries missing required fields.
    """
    if seed_path is None:
        seed_path = str(_PAPER_AGENT_ROOT / "seed_papers.json")

    if not os.path.exists(seed_path):
        raise FileNotFoundError(
            f"Seed papers not found: {seed_path}. "
            f"Run paper-seed-init to initialize."
        )

    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(
            f"seed_papers.json must contain a JSON array, got {type(data).__name__}"
        )

    # Validate each entry
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"Seed paper entry {i} must be a dict, got {type(entry).__name__}")
        missing = [f for f in SEED_PAPER_REQUIRED_FIELDS if f not in entry]
        if missing:
            raise ValueError(f"Seed paper entry {i} missing fields: {missing}")

    logger.info("Loaded %d seed papers from %s", len(data), seed_path)
    return data


def get_foundational_papers(
    seed_papers: List[Dict[str, Any]],
    max_count: int = 3,
) -> List[Dict[str, Any]]:
    """Filter seed papers by role='foundational' for few-shot examples.

    Args:
        seed_papers: List of seed paper dicts.
        max_count: Maximum number of foundational papers to return.

    Returns:
        Up to max_count foundational papers.
    """
    foundational = [
        p for p in seed_papers
        if p.get("role") == "foundational"
    ]
    return foundational[:max_count]
