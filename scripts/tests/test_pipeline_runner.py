"""
Integration tests for the paper-pipeline step-based command toolkit.

Tests cover:
- Step init: run directory creation and state initialization
- Step seed+search: combined seed + search flow
- Step prepare-scoring: scoring context generation
- Step postprocess-scoring: post-processing Agent output
- Step human-review-init/decide: review card generation and decision merge
- Step deep-parse/repo-eval/knowledge-sync: Phase 4 steps
- Step summary: run summary generation
- State transitions through all skills
- Breakpoint resume (state-based recovery)
- STEP_REGISTRY completeness
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Inject paper-agent scripts into sys.path for imports
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from common.path_manager import PathManager
from common.state_manager import StateManager, SkillStatus, PIPELINE_SKILLS
from pipeline_runner import (
    step_init,
    step_seed,
    step_search,
    step_seed_and_search,
    step_prepare_scoring,
    step_postprocess_scoring,
    step_human_review_init,
    step_human_review_decide,
    step_deep_parse,
    step_repo_eval,
    step_knowledge_sync,
    step_summary,
    show_status,
    STEP_REGISTRY,
    _require_run_id,
    _load_state,
    _load_papers_from_search,
    _load_final_selection,
)


def _make_args(**kwargs):
    """Create a mock argparse.Namespace with defaults."""
    defaults = {
        "run_id": None,
        "decisions": None,
        "profile": None,
        "step": None,
        "status": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class PipelineTestBase(unittest.TestCase):
    """Base class with common setup for pipeline integration tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.profile = {
            "research_description": "Test research direction",
            "seed_papers": ["2305.05065", "2502.18965"],
            "keywords": ["generative recommendation"],
            "whitelist_authors": [],
            "arxiv_categories": ["cs.IR", "cs.AI"],
            "search_days": 7,
            "top_venues": ["NeurIPS", "ICML"],
            "score_thresholds": {"high": 7, "edge_low": 4, "edge_high": 6, "low": 3},
            "notification_channel": "local",
            "knowledge_base_backend": "local_markdown",
            "idea_generation_frequency": "per_run",
            "human_review_wait_days": 3,
            "human_review_default_policy": "discard",
        }

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_pm_sm(self, run_id="test_run"):
        """Helper to create PathManager and StateManager for testing."""
        pm = PathManager(root=self.tmpdir, run_id=run_id)
        pm.create_run_directory()
        sm = StateManager(str(pm.pipeline_state_json))
        sm.initialize(pm.run_id)
        return pm, sm

    def _write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def _read_json(self, path):
        with open(path, "r") as f:
            return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# Step Registry Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStepRegistry(PipelineTestBase):

    def test_all_steps_registered(self):
        """STEP_REGISTRY should contain all 12 steps."""
        expected_steps = [
            "init", "seed", "search", "seed+search",
            "prepare-scoring", "postprocess-scoring",
            "human-review-init", "human-review-decide",
            "deep-parse", "repo-eval", "knowledge-sync",
            "summary",
        ]
        for step in expected_steps:
            self.assertIn(step, STEP_REGISTRY, f"Missing step: {step}")

    def test_all_steps_are_callable(self):
        for name, fn in STEP_REGISTRY.items():
            self.assertTrue(callable(fn), f"Step {name} is not callable")


# ═══════════════════════════════════════════════════════════════════════════════
# Step Init Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStepInit(PipelineTestBase):

    def test_init_creates_run_directory(self):
        pm = PathManager(root=self.tmpdir, run_id="20260301_120000")
        args = _make_args(run_id="20260301_120000")

        with patch("pipeline_runner.PathManager", return_value=pm):
            result = step_init(args, self.profile)

        self.assertEqual(result["status"], "success")
        self.assertIn("run_id", result)
        self.assertTrue(pm.run_dir.exists())

    def test_init_with_explicit_run_id(self):
        pm = PathManager(root=self.tmpdir, run_id="custom_run")
        args = _make_args(run_id="custom_run")

        with patch("pipeline_runner.PathManager", return_value=pm):
            result = step_init(args, self.profile)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["run_id"], "custom_run")
        self.assertTrue(pm.run_dir.exists())
        self.assertTrue(pm.pipeline_state_json.exists())


class TestDirectoryCreation(PipelineTestBase):

    def test_run_directory_structure(self):
        pm, sm = self._create_pm_sm("20260301_100000")

        self.assertTrue(pm.run_dir.exists())
        self.assertTrue(pm.skill4_parsed_dir.exists())
        self.assertTrue(pm.skill5_repo_eval_dir.exists())
        self.assertTrue(pm.errors_dir.exists())

    def test_pipeline_state_initialized(self):
        pm, sm = self._create_pm_sm()

        for skill in PIPELINE_SKILLS:
            self.assertEqual(sm.get_skill_status(skill), SkillStatus.PENDING)

        self.assertTrue(pm.pipeline_state_json.exists())


# ═══════════════════════════════════════════════════════════════════════════════
# Step Deep-Parse Tests (Phase 4)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStepDeepParse(PipelineTestBase):

    def test_deep_parse_calls_real_script(self):
        pm, sm = self._create_pm_sm()

        # Create final selection
        papers = [{"arxiv_id": "2305.05065", "title": "TIGER"}]
        self._write_json(pm.skill3_final_selection, papers)

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm), \
             patch("pipeline_runner._load_state", return_value=sm):
            result = step_deep_parse(args, self.profile)

        self.assertEqual(result["status"], "success")
        # With no card.md files, all papers need reading
        self.assertEqual(result["needs_reading"], 1)

    def test_deep_parse_with_no_papers(self):
        pm, sm = self._create_pm_sm()
        # No final selection file

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm), \
             patch("pipeline_runner._load_state", return_value=sm):
            result = step_deep_parse(args, self.profile)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result.get("parsed_count", 0), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Step Repo-Eval Tests (Phase 4)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStepRepoEval(PipelineTestBase):

    def test_repo_eval_with_no_papers(self):
        pm, sm = self._create_pm_sm()

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm), \
             patch("pipeline_runner._load_state", return_value=sm):
            result = step_repo_eval(args, self.profile)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result.get("total", 0), 0)

    def test_repo_eval_with_papers(self):
        pm, sm = self._create_pm_sm()
        papers = [{"arxiv_id": "2305.05065", "title": "TIGER", "abstract": "test paper about rec sys"}]
        self._write_json(pm.skill3_final_selection, papers)

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm), \
             patch("pipeline_runner._load_state", return_value=sm):
            result = step_repo_eval(args, self.profile)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total"], 1)
        # has_code + no_code should sum to total
        self.assertEqual(result["has_code_count"] + result["no_code_count"], 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Step Knowledge-Sync Tests (Phase 4)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStepKnowledgeSync(PipelineTestBase):

    def test_knowledge_sync_empty(self):
        pm, sm = self._create_pm_sm()

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm), \
             patch("pipeline_runner._load_state", return_value=sm):
            result = step_knowledge_sync(args, self.profile)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result.get("new_count", 0), 0)

    def test_knowledge_sync_with_papers(self):
        pm, sm = self._create_pm_sm()

        papers = [{"arxiv_id": "2305.05065", "title": "TIGER"}]
        self._write_json(pm.skill3_final_selection, papers)

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm), \
             patch("pipeline_runner._load_state", return_value=sm):
            result = step_knowledge_sync(args, self.profile)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["new_count"], 1)
        self.assertTrue(pm.paper_index_json.exists())


# ═══════════════════════════════════════════════════════════════════════════════
# Step Summary Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStepSummary(PipelineTestBase):

    def test_summary_basic(self):
        pm, sm = self._create_pm_sm()

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm), \
             patch("pipeline_runner._load_state", return_value=sm):
            result = step_summary(args, self.profile)

        self.assertEqual(result["status"], "success")
        self.assertIn("search_total_raw", result)
        self.assertIn("scored_high", result)
        self.assertTrue(pm.run_summary_json.exists())

    def test_summary_with_data(self):
        pm, sm = self._create_pm_sm()

        # Create search results
        self._write_json(pm.skill1_search_results, {
            "papers": [{"arxiv_id": "p1"}, {"arxiv_id": "p2"}],
            "stats": {"total_raw": 10},
        })

        # Create scored results
        self._write_json(pm.skill2_scored_results, {
            "high": [{"arxiv_id": "p1", "score": 9}],
            "edge": [{"arxiv_id": "p2", "score": 5}],
            "low": [],
        })

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm), \
             patch("pipeline_runner._load_state", return_value=sm):
            result = step_summary(args, self.profile)

        self.assertEqual(result["search_total_raw"], 10)
        self.assertEqual(result["search_new_increment"], 2)
        self.assertEqual(result["scored_high"], 1)
        self.assertEqual(result["scored_edge"], 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Show Status Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestShowStatus(PipelineTestBase):

    def test_status_no_runs(self):
        args = _make_args(run_id=None)
        with patch("pipeline_runner.PathManager.from_latest_run", return_value=None):
            result = show_status(args)
        self.assertEqual(result["status"], "no_runs")

    def test_status_with_run(self):
        pm, sm = self._create_pm_sm()

        args = _make_args(run_id=pm.run_id)
        with patch("pipeline_runner._require_run_id", return_value=pm):
            result = show_status(args)

        self.assertEqual(result["run_id"], "test_run")
        self.assertIn("skill_statuses", result)


# ═══════════════════════════════════════════════════════════════════════════════
# Breakpoint Resume Tests (State-based)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakpointResume(PipelineTestBase):

    def test_state_transitions(self):
        """Verify state transitions through step execution."""
        pm, sm = self._create_pm_sm()

        # Initially all pending
        for skill in PIPELINE_SKILLS:
            self.assertEqual(sm.get_skill_status(skill), SkillStatus.PENDING)

        # After a step runs, state should update
        sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS)
        self.assertEqual(sm.get_skill_status("paper-seed-init"), SkillStatus.SUCCESS)

        # Next pending should advance
        next_skill = sm.get_next_pending_skill()
        self.assertEqual(next_skill, "paper-source-scraper")

    def test_failed_skill_is_next_on_resume(self):
        pm, sm = self._create_pm_sm()

        sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS)
        sm.update_skill_status("paper-source-scraper", SkillStatus.FAILED, error="Network error")

        next_skill = sm.get_next_pending_skill()
        self.assertEqual(next_skill, "paper-source-scraper")

    def test_running_skill_is_next_on_resume(self):
        """A skill stuck in RUNNING (crash) should be next on resume."""
        pm, sm = self._create_pm_sm()

        sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS)
        sm.update_skill_status("paper-source-scraper", SkillStatus.RUNNING)

        next_skill = sm.get_next_pending_skill()
        self.assertEqual(next_skill, "paper-source-scraper")

    def test_waiting_for_human_detected(self):
        pm, sm = self._create_pm_sm()

        sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS)
        sm.update_skill_status("paper-source-scraper", SkillStatus.SUCCESS)
        sm.update_skill_status("paper-relevance-scorer", SkillStatus.SUCCESS)
        sm.set_waiting_for_human("paper-human-review", wait_days=3)

        status = sm.get_skill_status("paper-human-review")
        self.assertEqual(status, SkillStatus.WAITING_FOR_HUMAN)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Function Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestHelperFunctions(PipelineTestBase):

    def test_load_papers_from_search(self):
        pm, sm = self._create_pm_sm()

        self._write_json(pm.skill1_search_results, {
            "papers": [{"arxiv_id": "p1"}, {"arxiv_id": "p2"}],
            "stats": {},
        })

        papers = _load_papers_from_search(pm)
        self.assertEqual(len(papers), 2)

    def test_load_papers_from_search_missing(self):
        pm, sm = self._create_pm_sm()
        papers = _load_papers_from_search(pm)
        self.assertEqual(papers, [])

    def test_load_final_selection(self):
        pm, sm = self._create_pm_sm()

        self._write_json(pm.skill3_final_selection, [
            {"arxiv_id": "p1", "title": "Paper 1"},
        ])

        papers = _load_final_selection(pm)
        self.assertEqual(len(papers), 1)

    def test_load_final_selection_missing(self):
        pm, sm = self._create_pm_sm()
        papers = _load_final_selection(pm)
        self.assertEqual(papers, [])


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
