"""
Path manager for paper-agent pipeline data.

THIS MODULE IS THE GLOBAL CONTRACT CORE.

All skill input/output paths are defined here, ensuring consistency
across the entire pipeline. Changes to directory structure only need
to be made in this one place.

Directory structure per run:
    pipeline_data/{run_id}/
    ├── pipeline_state.json
    ├── skill1_search_results.json
    ├── skill2_scored_results.json
    ├── skill3_review_pending.json
    ├── skill3_review_cards.md
    ├── skill3_human_decisions.json    (user-created)
    ├── skill3_final_selection.json
    ├── skill4_parsed/
    │   └── {arxiv_id}.json
    ├── skill5_repo_eval/
    │   └── {arxiv_id}.json
    ├── run_summary.json
    └── errors/
        └── *.log
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Standard path injection for paper-agent common modules
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))


class PathManager:
    """Manages all pipeline data paths.

    Centralizes path generation for the entire pipeline, ensuring all
    skills use consistent file locations.

    Attributes:
        root: Paper-agent root directory.
        run_id: Current run identifier (timestamp format).
        run_dir: Directory for current run data.
    """

    def __init__(
        self,
        root: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        """Initialize PathManager.

        Args:
            root: Paper-agent root directory. Defaults to PAPER_AGENT_ROOT env var.
            run_id: Existing run_id to use. If None, generates a new one.
        """
        self.root = Path(root) if root else _PAPER_AGENT_ROOT
        self.run_id = run_id or self._generate_run_id()
        self.run_dir = self.root / "pipeline_data" / self.run_id

    @staticmethod
    def _generate_run_id() -> str:
        """Generate a unique run_id based on current timestamp."""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Global files (not per-run) ──────────────────────────────────────

    @property
    def profile_yaml(self) -> Path:
        """Path to research interest profile."""
        return self.root / "profile.yaml"

    @property
    def seed_papers_json(self) -> Path:
        """Path to core paper directory (read-only for most skills)."""
        return self.root / "seed_papers.json"

    @property
    def seen_papers_json(self) -> Path:
        """Path to global seen-papers registry (cross-run dedup)."""
        return self.root / "seen_papers.json"

    @property
    def paper_index_json(self) -> Path:
        """Path to paper knowledge base index."""
        return self.root / "paper_index.json"

    @property
    def ideas_dir(self) -> Path:
        """Path to ideas directory."""
        return self.root / "ideas"

    # ── Per-run files ───────────────────────────────────────────────────

    @property
    def pipeline_state_json(self) -> Path:
        """Path to pipeline state machine file."""
        return self.run_dir / "pipeline_state.json"

    @property
    def skill1_search_results(self) -> Path:
        """Path to paper-source-scraper output."""
        return self.run_dir / "skill1_search_results.json"

    @property
    def skill2_scored_results(self) -> Path:
        """Path to paper-relevance-scorer output."""
        return self.run_dir / "skill2_scored_results.json"

    @property
    def skill2_scoring_context(self) -> Path:
        """Path to scoring context prepared by --prepare mode (Agent orchestration)."""
        return self.run_dir / "skill2_scoring_context.json"

    @property
    def skill2_agent_raw_output(self) -> Path:
        """Path to Agent LLM raw scoring output (Agent orchestration)."""
        return self.run_dir / "skill2_agent_raw_output.json"

    @property
    def skill3_review_pending(self) -> Path:
        """Path to human review pending list."""
        return self.run_dir / "skill3_review_pending.json"

    @property
    def skill3_review_cards(self) -> Path:
        """Path to human review info cards (Markdown)."""
        return self.run_dir / "skill3_review_cards.md"

    @property
    def skill3_human_decisions(self) -> Path:
        """Path to user-created human review decisions."""
        return self.run_dir / "skill3_human_decisions.json"

    @property
    def skill3_final_selection(self) -> Path:
        """Path to final selected papers (high + rescued)."""
        return self.run_dir / "skill3_final_selection.json"

    @property
    def skill4_parsed_dir(self) -> Path:
        """Directory for paper-deep-parser output per paper."""
        return self.run_dir / "skill4_parsed"

    @property
    def skill5_repo_eval_dir(self) -> Path:
        """Directory for paper-repo-evaluator output per paper."""
        return self.run_dir / "skill5_repo_eval"

    @property
    def run_summary_json(self) -> Path:
        """Path to run execution summary."""
        return self.run_dir / "run_summary.json"

    @property
    def errors_dir(self) -> Path:
        """Directory for error logs."""
        return self.run_dir / "errors"

    # ── Per-paper files ─────────────────────────────────────────────────

    def skill4_parsed_paper(self, arxiv_id: str) -> Path:
        """Path to parsed paper JSON for a specific paper."""
        safe_id = arxiv_id.replace("/", "_")
        return self.skill4_parsed_dir / f"{safe_id}.json"

    def skill5_repo_eval_paper(self, arxiv_id: str) -> Path:
        """Path to repo evaluation JSON for a specific paper."""
        safe_id = arxiv_id.replace("/", "_")
        return self.skill5_repo_eval_dir / f"{safe_id}.json"

    def error_log(self, skill_name: str, detail: str = "") -> Path:
        """Path to an error log file for a specific skill."""
        suffix = f"_{detail}" if detail else ""
        return self.errors_dir / f"{skill_name}{suffix}.log"

    # ── Directory creation ──────────────────────────────────────────────

    def create_run_directory(self) -> Path:
        """Create the full directory tree for the current run.

        Returns:
            Path to the created run directory.
        """
        dirs = [
            self.run_dir,
            self.skill4_parsed_dir,
            self.skill5_repo_eval_dir,
            self.errors_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # Also ensure global dirs exist
        self.ideas_dir.mkdir(parents=True, exist_ok=True)

        return self.run_dir

    # ── Discovery ───────────────────────────────────────────────────────

    def get_latest_run_id(self) -> Optional[str]:
        """Find the latest run_id from existing pipeline_data directories.

        Returns:
            Latest run_id string, or None if no runs exist.
        """
        pipeline_data_dir = self.root / "pipeline_data"
        if not pipeline_data_dir.exists():
            return None

        run_dirs = sorted(
            [
                d.name
                for d in pipeline_data_dir.iterdir()
                if d.is_dir() and d.name.replace("_", "").isdigit()
            ],
            reverse=True,
        )
        return run_dirs[0] if run_dirs else None

    @classmethod
    def from_latest_run(cls, root: Optional[str] = None) -> Optional["PathManager"]:
        """Create a PathManager for the latest existing run.

        Returns:
            PathManager instance or None if no runs exist.
        """
        temp = cls(root=root, run_id="temp")
        latest = temp.get_latest_run_id()
        if latest is None:
            return None
        return cls(root=root, run_id=latest)
