"""
Pipeline state machine manager.

Manages pipeline_state.json for tracking skill execution status,
supporting resume-from-checkpoint and async human review.

State transitions:
    pending → running → success
                     → failed
                     → skipped
                     → waiting_for_human → success (after merge)
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Standard path injection for paper-agent common modules
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

logger = logging.getLogger("paper_agent.state_manager")


class SkillStatus(str, Enum):
    """Valid states for a skill in the pipeline."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_FOR_HUMAN = "waiting_for_human"

    def is_terminal(self) -> bool:
        """Check if this status is a terminal state."""
        return self in (
            SkillStatus.SUCCESS,
            SkillStatus.FAILED,
            SkillStatus.SKIPPED,
        )

    def is_resumable(self) -> bool:
        """Check if this status allows resuming."""
        return self in (
            SkillStatus.PENDING,
            SkillStatus.RUNNING,  # Crashed during execution
            SkillStatus.WAITING_FOR_HUMAN,
        )


# Ordered list of skills in the pipeline
PIPELINE_SKILLS = [
    "paper-seed-init",
    "paper-source-scraper",
    "paper-relevance-scorer",
    "paper-human-review",
    "paper-deep-parser",
    "paper-repo-evaluator",
    "paper-knowledge-sync",
]


class StateManager:
    """Manages pipeline state for a specific run.

    Reads and writes pipeline_state.json, tracks skill execution
    status, and supports resume-from-checkpoint.

    Attributes:
        state_path: Path to pipeline_state.json.
        state: Current state dict.
    """

    def __init__(self, state_path: str):
        """Initialize StateManager.

        Args:
            state_path: Path to pipeline_state.json.
        """
        self.state_path = Path(state_path)
        self.state: Dict[str, Any] = {}

    def initialize(self, run_id: str) -> Dict[str, Any]:
        """Create a fresh pipeline state with all skills pending.

        Args:
            run_id: The run identifier.

        Returns:
            Initialized state dict.
        """
        self.state = {
            "run_id": run_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "overall_status": SkillStatus.PENDING.value,
            "skills": {},
        }

        for skill_name in PIPELINE_SKILLS:
            self.state["skills"][skill_name] = {
                "status": SkillStatus.PENDING.value,
                "started_at": None,
                "completed_at": None,
                "error": None,
                "metadata": {},
            }

        self._save()
        logger.info("Initialized pipeline state for run %s", run_id)
        return self.state

    def load(self) -> Dict[str, Any]:
        """Load existing pipeline state from file.

        Returns:
            Loaded state dict.

        Raises:
            FileNotFoundError: If state file doesn't exist.
        """
        if not self.state_path.exists():
            raise FileNotFoundError(f"Pipeline state not found: {self.state_path}")

        with open(self.state_path, "r", encoding="utf-8") as f:
            self.state = json.load(f)

        logger.info("Loaded pipeline state for run %s", self.state.get("run_id"))
        return self.state

    def _save(self) -> None:
        """Persist current state to file."""
        self.state["updated_at"] = datetime.now().isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def get_skill_status(self, skill_name: str) -> SkillStatus:
        """Get the current status of a specific skill.

        Args:
            skill_name: Name of the skill.

        Returns:
            Current SkillStatus.
        """
        skill_state = self.state.get("skills", {}).get(skill_name, {})
        status_str = skill_state.get("status", SkillStatus.PENDING.value)
        return SkillStatus(status_str)

    def update_skill_status(
        self,
        skill_name: str,
        status: SkillStatus,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update the status of a specific skill.

        Args:
            skill_name: Name of the skill.
            status: New status.
            error: Error message if status is FAILED.
            metadata: Additional metadata (e.g., waiting_since timestamp).
        """
        if skill_name not in self.state.get("skills", {}):
            logger.warning("Unknown skill: %s", skill_name)
            return

        skill_state = self.state["skills"][skill_name]
        skill_state["status"] = status.value

        now = datetime.now().isoformat()
        if status == SkillStatus.RUNNING:
            skill_state["started_at"] = now
        elif status.is_terminal() or status == SkillStatus.WAITING_FOR_HUMAN:
            skill_state["completed_at"] = now

        if error is not None:
            skill_state["error"] = error

        if metadata:
            skill_state["metadata"].update(metadata)

        # Update overall status
        self._update_overall_status()
        self._save()

        logger.info("Skill %s → %s", skill_name, status.value)

    def set_waiting_for_human(
        self,
        skill_name: str,
        wait_days: int = 3,
    ) -> None:
        """Set a skill to waiting_for_human with deadline.

        Args:
            skill_name: Name of the skill.
            wait_days: Number of days to wait before timeout.
        """
        waiting_since = datetime.now().isoformat()
        wait_deadline = (datetime.now() + timedelta(days=wait_days)).isoformat()

        self.update_skill_status(
            skill_name,
            SkillStatus.WAITING_FOR_HUMAN,
            metadata={
                "waiting_since": waiting_since,
                "wait_deadline": wait_deadline,
            },
        )

    def is_waiting_expired(self, skill_name: str) -> bool:
        """Check if the waiting period for a skill has expired.

        Args:
            skill_name: Name of the skill.

        Returns:
            True if expired, False otherwise.
        """
        skill_state = self.state.get("skills", {}).get(skill_name, {})
        deadline_str = skill_state.get("metadata", {}).get("wait_deadline")
        if not deadline_str:
            return False

        try:
            deadline = datetime.fromisoformat(deadline_str)
            return datetime.now() > deadline
        except (ValueError, TypeError):
            return False

    def _update_overall_status(self) -> None:
        """Update overall pipeline status based on individual skill statuses."""
        skills = self.state.get("skills", {})
        statuses = [SkillStatus(s["status"]) for s in skills.values()]

        if any(s == SkillStatus.RUNNING for s in statuses):
            self.state["overall_status"] = SkillStatus.RUNNING.value
        elif any(s == SkillStatus.WAITING_FOR_HUMAN for s in statuses):
            self.state["overall_status"] = SkillStatus.WAITING_FOR_HUMAN.value
        elif any(s == SkillStatus.FAILED for s in statuses):
            self.state["overall_status"] = SkillStatus.FAILED.value
        elif all(s.is_terminal() for s in statuses):
            self.state["overall_status"] = SkillStatus.SUCCESS.value
        else:
            self.state["overall_status"] = SkillStatus.RUNNING.value

    def get_next_pending_skill(self) -> Optional[str]:
        """Find the next skill that needs to be executed.

        Respects pipeline ordering and handles resume scenarios:
        - Skips already successful/skipped skills
        - Resumes from running (crashed) skills
        - Checks waiting_for_human status

        Returns:
            Name of the next skill to execute, or None if pipeline is done.
        """
        for skill_name in PIPELINE_SKILLS:
            status = self.get_skill_status(skill_name)

            if status == SkillStatus.SUCCESS or status == SkillStatus.SKIPPED:
                continue

            if status == SkillStatus.WAITING_FOR_HUMAN:
                return skill_name  # Caller should check for decisions file

            if status in (SkillStatus.PENDING, SkillStatus.RUNNING, SkillStatus.FAILED):
                return skill_name

        return None  # All skills completed

    def get_all_statuses(self) -> Dict[str, str]:
        """Get a summary of all skill statuses.

        Returns:
            Dict mapping skill names to their status strings.
        """
        return {
            name: self.state["skills"][name]["status"]
            for name in PIPELINE_SKILLS
            if name in self.state.get("skills", {})
        }
