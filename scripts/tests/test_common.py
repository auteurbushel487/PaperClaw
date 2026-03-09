"""
Unit tests for paper-agent common modules.

Tests cover:
- json_extractor: Various abnormal inputs and edge cases
- config_loader: Profile validation, seed paper validation
- path_manager: Run ID generation, directory creation, path resolution
- state_manager: State transitions, resume logic, waiting expiry
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Inject paper-agent scripts into sys.path for imports
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from common.json_extractor import (
    extract_json,
    extract_json_array,
    extract_json_object,
    extract_json_with_fallback,
)
from common.config_loader import load_profile, load_seed_papers, get_foundational_papers
from common.path_manager import PathManager
from common.state_manager import StateManager, SkillStatus, PIPELINE_SKILLS


# ═══════════════════════════════════════════════════════════════════════════════
# JSON Extractor Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestJsonExtractorCleanInput(unittest.TestCase):
    """Test JSON extraction with clean, well-formed input."""

    def test_clean_json_array(self):
        text = '[{"score": 8, "title": "Test Paper"}]'
        result = extract_json_array(text)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["score"], 8)

    def test_clean_json_object(self):
        text = '{"total": 5, "papers": []}'
        result = extract_json_object(text)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["total"], 5)

    def test_extract_json_prefers_array(self):
        text = '[{"id": 1}]'
        result = extract_json(text)
        self.assertIsInstance(result, list)


class TestJsonExtractorMarkdownFences(unittest.TestCase):
    """Test JSON extraction with Markdown code block fences."""

    def test_json_with_markdown_fence(self):
        text = '```json\n[{"score": 8}]\n```'
        result = extract_json_array(text)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["score"], 8)

    def test_json_with_plain_fence(self):
        text = '```\n{"key": "value"}\n```'
        result = extract_json_object(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["key"], "value")

    def test_json_with_uppercase_fence(self):
        text = '```JSON\n[{"a": 1}]\n```'
        result = extract_json_array(text)
        self.assertIsNotNone(result)


class TestJsonExtractorPreamblePostamble(unittest.TestCase):
    """Test JSON extraction with preamble/postamble noise."""

    def test_preamble_text(self):
        text = "Sure! Here are the results:\n\n[{\"score\": 7}]"
        result = extract_json_array(text)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["score"], 7)

    def test_postamble_text(self):
        text = '[{"score": 9}]\n\nI hope this helps!'
        result = extract_json_array(text)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["score"], 9)

    def test_both_preamble_and_postamble(self):
        text = (
            "Here are the scores for the papers:\n\n"
            '```json\n[{"arxiv_id": "2305.05065", "score": 9}]\n```\n\n'
            "Let me know if you need anything else!"
        )
        result = extract_json_array(text)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["arxiv_id"], "2305.05065")


class TestJsonExtractorEdgeCases(unittest.TestCase):
    """Test JSON extraction with edge cases and failures."""

    def test_empty_input(self):
        self.assertIsNone(extract_json(""))

    def test_no_json(self):
        self.assertIsNone(extract_json("This is just plain text with no JSON."))

    def test_malformed_json(self):
        text = '[{"key": "value",}]'  # Trailing comma
        self.assertIsNone(extract_json_array(text))

    def test_nested_json(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = extract_json_object(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["outer"]["inner"], [1, 2, 3])

    def test_json_with_escaped_quotes(self):
        text = '[{"title": "A \\"great\\" paper"}]'
        result = extract_json_array(text)
        self.assertIsNotNone(result)
        self.assertIn("great", result[0]["title"])

    def test_total_garbage_with_fallback(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            error_path = f.name

        try:
            result = extract_json_with_fallback(
                "Total garbage 🤖🎉 no json here",
                default={"fallback": True},
                error_log_path=error_path,
                context="test_garbage",
            )
            self.assertEqual(result, {"fallback": True})
            # Verify error log was written
            self.assertTrue(os.path.exists(error_path))
            with open(error_path, "r") as f:
                content = f.read()
                self.assertIn("JSON Extraction Failed", content)
                self.assertIn("test_garbage", content)
        finally:
            os.unlink(error_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Config Loader Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigLoader(unittest.TestCase):
    """Test profile.yaml and seed_papers.json loading."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_yaml(self, data: dict, filename: str = "profile.yaml") -> str:
        path = os.path.join(self.tmpdir, filename)
        # Write as JSON (valid YAML subset)
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def _write_json(self, data, filename: str) -> str:
        path = os.path.join(self.tmpdir, filename)
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_load_valid_profile(self):
        path = self._write_yaml({
            "research_description": "Test research",
            "seed_papers": ["2305.05065"],
            "keywords": ["test keyword"],
        })
        profile = load_profile(path)
        self.assertEqual(profile["research_description"], "Test research")
        # Check defaults are applied
        self.assertEqual(profile["search_days"], 7)
        self.assertIn("cs.IR", profile["arxiv_categories"])

    def test_missing_required_field(self):
        path = self._write_yaml({
            "seed_papers": ["2305.05065"],
            "keywords": ["test"],
        })
        with self.assertRaises(ValueError):
            load_profile(path)

    def test_wrong_type_field(self):
        path = self._write_yaml({
            "research_description": 123,  # Should be string
            "seed_papers": ["2305.05065"],
            "keywords": ["test"],
        })
        with self.assertRaises(ValueError):
            load_profile(path)

    def test_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_profile("/nonexistent/profile.yaml")

    def test_load_valid_seed_papers(self):
        path = self._write_json([
            {"arxiv_id": "2305.05065", "title": "TIGER"},
            {"arxiv_id": "2502.18965", "title": "OneRec"},
        ], "seed_papers.json")
        papers = load_seed_papers(path)
        self.assertEqual(len(papers), 2)

    def test_seed_papers_missing_field(self):
        path = self._write_json([
            {"title": "No ID paper"},  # Missing arxiv_id
        ], "seed_papers.json")
        with self.assertRaises(ValueError):
            load_seed_papers(path)

    def test_get_foundational_papers(self):
        papers = [
            {"arxiv_id": "1", "title": "A", "role": "foundational"},
            {"arxiv_id": "2", "title": "B", "role": "benchmark"},
            {"arxiv_id": "3", "title": "C", "role": "foundational"},
            {"arxiv_id": "4", "title": "D", "role": "foundational"},
            {"arxiv_id": "5", "title": "E", "role": "foundational"},
        ]
        result = get_foundational_papers(papers, max_count=3)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(p["role"] == "foundational" for p in result))


# ═══════════════════════════════════════════════════════════════════════════════
# Path Manager Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPathManager(unittest.TestCase):
    """Test path management and directory creation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_run_id_generation(self):
        pm = PathManager(root=self.tmpdir)
        # Run ID should be in YYYYMMDD_HHMMSS format
        self.assertRegex(pm.run_id, r"\d{8}_\d{6}")

    def test_explicit_run_id(self):
        pm = PathManager(root=self.tmpdir, run_id="20260101_120000")
        self.assertEqual(pm.run_id, "20260101_120000")

    def test_directory_creation(self):
        pm = PathManager(root=self.tmpdir, run_id="test_run")
        pm.create_run_directory()

        self.assertTrue(pm.run_dir.exists())
        self.assertTrue(pm.skill4_parsed_dir.exists())
        self.assertTrue(pm.skill5_repo_eval_dir.exists())
        self.assertTrue(pm.errors_dir.exists())
        self.assertTrue(pm.ideas_dir.exists())

    def test_global_paths(self):
        pm = PathManager(root=self.tmpdir)
        self.assertEqual(pm.profile_yaml, Path(self.tmpdir) / "profile.yaml")
        self.assertEqual(pm.seed_papers_json, Path(self.tmpdir) / "seed_papers.json")
        self.assertEqual(pm.seen_papers_json, Path(self.tmpdir) / "seen_papers.json")

    def test_per_run_paths(self):
        pm = PathManager(root=self.tmpdir, run_id="test_run")
        self.assertIn("test_run", str(pm.skill1_search_results))
        self.assertIn("test_run", str(pm.skill2_scored_results))
        self.assertIn("test_run", str(pm.pipeline_state_json))

    def test_per_paper_paths(self):
        pm = PathManager(root=self.tmpdir, run_id="test_run")
        p = pm.skill4_parsed_paper("2305.05065")
        self.assertIn("2305.05065", str(p))
        self.assertTrue(str(p).endswith(".json"))

    def test_latest_run_discovery(self):
        # Create some run dirs
        for rid in ["20260101_100000", "20260102_100000", "20260103_100000"]:
            (Path(self.tmpdir) / "pipeline_data" / rid).mkdir(parents=True)

        pm = PathManager(root=self.tmpdir)
        latest = pm.get_latest_run_id()
        self.assertEqual(latest, "20260103_100000")

    def test_latest_run_empty(self):
        pm = PathManager(root=self.tmpdir)
        self.assertIsNone(pm.get_latest_run_id())

    def test_from_latest_run(self):
        for rid in ["20260101_100000", "20260102_100000"]:
            (Path(self.tmpdir) / "pipeline_data" / rid).mkdir(parents=True)

        pm = PathManager.from_latest_run(root=self.tmpdir)
        self.assertIsNotNone(pm)
        self.assertEqual(pm.run_id, "20260102_100000")


# ═══════════════════════════════════════════════════════════════════════════════
# State Manager Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStateManager(unittest.TestCase):
    """Test pipeline state machine operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmpdir, "pipeline_state.json")
        self.sm = StateManager(self.state_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_initialize(self):
        state = self.sm.initialize("test_run")
        self.assertEqual(state["run_id"], "test_run")
        self.assertEqual(state["overall_status"], "pending")
        self.assertEqual(len(state["skills"]), len(PIPELINE_SKILLS))
        for skill in PIPELINE_SKILLS:
            self.assertEqual(state["skills"][skill]["status"], "pending")

    def test_save_and_load(self):
        self.sm.initialize("test_run")
        # Create a new StateManager and load
        sm2 = StateManager(self.state_path)
        state = sm2.load()
        self.assertEqual(state["run_id"], "test_run")

    def test_status_transitions(self):
        self.sm.initialize("test_run")

        self.sm.update_skill_status("paper-seed-init", SkillStatus.RUNNING)
        self.assertEqual(
            self.sm.get_skill_status("paper-seed-init"),
            SkillStatus.RUNNING,
        )

        self.sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS)
        self.assertEqual(
            self.sm.get_skill_status("paper-seed-init"),
            SkillStatus.SUCCESS,
        )

    def test_failed_status_with_error(self):
        self.sm.initialize("test_run")
        self.sm.update_skill_status(
            "paper-source-scraper",
            SkillStatus.FAILED,
            error="Network timeout",
        )
        skill_state = self.sm.state["skills"]["paper-source-scraper"]
        self.assertEqual(skill_state["status"], "failed")
        self.assertEqual(skill_state["error"], "Network timeout")

    def test_waiting_for_human(self):
        self.sm.initialize("test_run")
        self.sm.set_waiting_for_human("paper-human-review", wait_days=3)

        status = self.sm.get_skill_status("paper-human-review")
        self.assertEqual(status, SkillStatus.WAITING_FOR_HUMAN)

        metadata = self.sm.state["skills"]["paper-human-review"]["metadata"]
        self.assertIn("waiting_since", metadata)
        self.assertIn("wait_deadline", metadata)

    def test_waiting_not_expired(self):
        self.sm.initialize("test_run")
        self.sm.set_waiting_for_human("paper-human-review", wait_days=3)
        self.assertFalse(self.sm.is_waiting_expired("paper-human-review"))

    def test_waiting_expired(self):
        self.sm.initialize("test_run")
        # Manually set expired deadline
        self.sm.state["skills"]["paper-human-review"]["metadata"] = {
            "waiting_since": (datetime.now() - timedelta(days=5)).isoformat(),
            "wait_deadline": (datetime.now() - timedelta(days=2)).isoformat(),
        }
        self.assertTrue(self.sm.is_waiting_expired("paper-human-review"))

    def test_get_next_pending_skill(self):
        self.sm.initialize("test_run")
        # First pending should be paper-seed-init
        self.assertEqual(
            self.sm.get_next_pending_skill(),
            "paper-seed-init",
        )

        # Mark first two as success
        self.sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS)
        self.sm.update_skill_status("paper-source-scraper", SkillStatus.SUCCESS)

        self.assertEqual(
            self.sm.get_next_pending_skill(),
            "paper-relevance-scorer",
        )

    def test_get_next_pending_with_waiting(self):
        self.sm.initialize("test_run")
        self.sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS)
        self.sm.update_skill_status("paper-source-scraper", SkillStatus.SUCCESS)
        self.sm.update_skill_status("paper-relevance-scorer", SkillStatus.SUCCESS)
        self.sm.set_waiting_for_human("paper-human-review")

        # Should return the waiting skill
        self.assertEqual(
            self.sm.get_next_pending_skill(),
            "paper-human-review",
        )

    def test_all_complete(self):
        self.sm.initialize("test_run")
        for skill in PIPELINE_SKILLS:
            self.sm.update_skill_status(skill, SkillStatus.SUCCESS)

        self.assertIsNone(self.sm.get_next_pending_skill())
        self.assertEqual(self.sm.state["overall_status"], "success")

    def test_resume_from_crash(self):
        """Test that a skill stuck in 'running' (crashed) is picked up on resume."""
        self.sm.initialize("test_run")
        self.sm.update_skill_status("paper-seed-init", SkillStatus.SUCCESS)
        self.sm.update_skill_status("paper-source-scraper", SkillStatus.RUNNING)

        # Simulate restart: reload state
        sm2 = StateManager(self.state_path)
        sm2.load()
        next_skill = sm2.get_next_pending_skill()
        self.assertEqual(next_skill, "paper-source-scraper")


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
