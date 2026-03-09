#!/usr/bin/env python3
"""Unit tests for knowledge_sync.py -- Knowledge Base & Idea Generation."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Standard path injection
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from knowledge_sync import (
    detect_paper_relations,
    load_paper_index,
    prepare_idea_context,
    run_knowledge_sync,
    save_ideas,
    save_paper_index,
    sync_papers_to_index,
)
from common.path_manager import PathManager


# ═══════════════════════════════════════════════════════════════════════════════
# Test Paper Index Management
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadPaperIndex(unittest.TestCase):
    """Tests for load_paper_index function."""

    def test_load_nonexistent(self):
        result = load_paper_index(Path("/nonexistent/index.json"))
        self.assertEqual(result, [])

    def test_load_valid_index(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([{"arxiv_id": "test", "title": "Test"}], f)
            f.flush()
            result = load_paper_index(Path(f.name))

        os.unlink(f.name)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["arxiv_id"], "test")

    def test_load_corrupted_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            f.flush()
            result = load_paper_index(Path(f.name))

        os.unlink(f.name)
        self.assertEqual(result, [])

    def test_load_non_list_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "value"}, f)
            f.flush()
            result = load_paper_index(Path(f.name))

        os.unlink(f.name)
        self.assertEqual(result, [])


class TestSavePaperIndex(unittest.TestCase):
    """Tests for save_paper_index function."""

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "index.json"
            data = [{"arxiv_id": "123", "title": "Test Paper"}]
            save_paper_index(index_path, data)

            self.assertTrue(index_path.exists())
            loaded = load_paper_index(index_path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["arxiv_id"], "123")

    def test_save_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "sub" / "dir" / "index.json"
            save_paper_index(index_path, [])
            self.assertTrue(index_path.exists())


# ═══════════════════════════════════════════════════════════════════════════════
# Test Relation Detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectPaperRelations(unittest.TestCase):
    """Tests for paper relation detection logic."""

    def test_same_subfield(self):
        new_paper = {"arxiv_id": "new", "sub_field": "generative_rec"}
        existing = [{"arxiv_id": "old", "sub_field": "generative_rec"}]

        relations = detect_paper_relations(new_paper, existing)
        self.assertTrue(len(relations) >= 1)
        self.assertEqual(relations[0]["relation_type"], "same_subfield")

    def test_shared_baselines(self):
        new_paper = {
            "arxiv_id": "new",
            "baselines_compared": ["SASRec", "BPR"],
        }
        existing = [{
            "arxiv_id": "old",
            "baselines_compared": ["SASRec", "GRU4Rec"],
        }]

        relations = detect_paper_relations(new_paper, existing)
        baseline_rels = [r for r in relations if r["relation_type"] == "shared_baselines"]
        self.assertEqual(len(baseline_rels), 1)
        self.assertIn("SASRec", baseline_rels[0]["detail"])

    def test_shared_techniques(self):
        new_paper = {
            "arxiv_id": "new",
            "transferable_techniques": ["RQ-VAE encoding"],
        }
        existing = [{
            "arxiv_id": "old",
            "transferable_techniques": ["RQ-VAE encoding", "Other technique"],
        }]

        relations = detect_paper_relations(new_paper, existing)
        tech_rels = [r for r in relations if r["relation_type"] == "shared_techniques"]
        self.assertEqual(len(tech_rels), 1)

    def test_same_authors(self):
        new_paper = {"arxiv_id": "new", "authors": ["Alice", "Bob"]}
        existing = [{"arxiv_id": "old", "authors": ["Bob", "Charlie"]}]

        relations = detect_paper_relations(new_paper, existing)
        auth_rels = [r for r in relations if r["relation_type"] == "same_authors"]
        self.assertEqual(len(auth_rels), 1)
        self.assertIn("Bob", auth_rels[0]["detail"])

    def test_no_relations(self):
        new_paper = {"arxiv_id": "new", "sub_field": "NLP"}
        existing = [{"arxiv_id": "old", "sub_field": "CV"}]

        relations = detect_paper_relations(new_paper, existing)
        self.assertEqual(len(relations), 0)

    def test_skip_self(self):
        new_paper = {"arxiv_id": "same", "sub_field": "gen_rec"}
        existing = [{"arxiv_id": "same", "sub_field": "gen_rec"}]

        relations = detect_paper_relations(new_paper, existing)
        self.assertEqual(len(relations), 0)

    def test_na_not_matched(self):
        new_paper = {
            "arxiv_id": "new",
            "baselines_compared": ["N/A"],
            "transferable_techniques": ["N/A"],
        }
        existing = [{
            "arxiv_id": "old",
            "baselines_compared": ["N/A"],
            "transferable_techniques": ["N/A"],
        }]

        relations = detect_paper_relations(new_paper, existing)
        # N/A should not create relations
        bl_rels = [r for r in relations if r["relation_type"] == "shared_baselines"]
        tech_rels = [r for r in relations if r["relation_type"] == "shared_techniques"]
        self.assertEqual(len(bl_rels), 0)
        self.assertEqual(len(tech_rels), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Test Knowledge Sync
# ═══════════════════════════════════════════════════════════════════════════════


class TestSyncPapersToIndex(unittest.TestCase):
    """Tests for sync_papers_to_index function."""

    def _setup_run(self, tmpdir: str):
        """Helper to set up a minimal run directory."""
        pm = PathManager(root=tmpdir, run_id="test_run")
        pm.create_run_directory()

        # Create minimal profile.yaml
        profile_path = Path(tmpdir) / "profile.yaml"
        profile_path.write_text("research_description: test\n")

        return pm

    def test_sync_new_papers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = self._setup_run(tmpdir)

            # Create final selection
            papers = [
                {"arxiv_id": "2305.05065", "title": "TIGER", "authors": ["Alice"]},
                {"arxiv_id": "2401.99999", "title": "Paper 2", "authors": ["Bob"]},
            ]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            stats = sync_papers_to_index(pm, {"research_description": "test"})

            self.assertEqual(stats["new_count"], 2)
            self.assertEqual(stats["total_indexed"], 2)

            # Verify index file
            index = load_paper_index(pm.paper_index_json)
            self.assertEqual(len(index), 2)

    def test_sync_with_parsed_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = self._setup_run(tmpdir)

            papers = [{"arxiv_id": "2305.05065", "title": "TIGER"}]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            # Create parsed data
            parsed = {
                "arxiv_id": "2305.05065",
                "sub_field": "generative_rec",
                "baselines_compared": ["SASRec"],
            }
            with open(pm.skill4_parsed_paper("2305.05065"), "w") as f:
                json.dump(parsed, f)

            stats = sync_papers_to_index(pm, {})

            index = load_paper_index(pm.paper_index_json)
            self.assertEqual(index[0]["sub_field"], "generative_rec")

    def test_sync_with_repo_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = self._setup_run(tmpdir)

            papers = [{"arxiv_id": "2305.05065", "title": "TIGER"}]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            # Create repo eval data
            repo = {"arxiv_id": "2305.05065", "has_code": True, "stars": 500}
            with open(pm.skill5_repo_eval_paper("2305.05065"), "w") as f:
                json.dump(repo, f)

            stats = sync_papers_to_index(pm, {})

            index = load_paper_index(pm.paper_index_json)
            self.assertTrue(index[0]["has_code"])
            self.assertEqual(index[0]["stars"], 500)

    def test_sync_update_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = self._setup_run(tmpdir)

            # Pre-populate index
            existing = [{"arxiv_id": "2305.05065", "title": "Old Title", "run_id": "old"}]
            save_paper_index(pm.paper_index_json, existing)

            # Create final selection with same paper
            papers = [{"arxiv_id": "2305.05065", "title": "TIGER Updated"}]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            stats = sync_papers_to_index(pm, {})

            self.assertEqual(stats["updated_count"], 1)
            self.assertEqual(stats["new_count"], 0)

            index = load_paper_index(pm.paper_index_json)
            self.assertEqual(len(index), 1)
            self.assertEqual(index[0]["title"], "TIGER Updated")

    def test_sync_detects_relations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = self._setup_run(tmpdir)

            # Pre-populate index with an existing paper
            existing = [{
                "arxiv_id": "existing",
                "title": "Existing Paper",
                "sub_field": "generative_rec",
                "baselines_compared": ["SASRec"],
            }]
            save_paper_index(pm.paper_index_json, existing)

            # New paper shares sub_field and baseline
            papers = [{"arxiv_id": "2305.05065", "title": "TIGER"}]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            parsed = {
                "arxiv_id": "2305.05065",
                "sub_field": "generative_rec",
                "baselines_compared": ["SASRec", "BPR"],
            }
            with open(pm.skill4_parsed_paper("2305.05065"), "w") as f:
                json.dump(parsed, f)

            stats = sync_papers_to_index(pm, {})
            self.assertTrue(stats["with_relations"] >= 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Test Idea Generation Context
# ═══════════════════════════════════════════════════════════════════════════════


class TestPrepareIdeaContext(unittest.TestCase):
    """Tests for prepare_idea_context function."""

    def test_basic_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            # Create minimal seed papers
            seed = [{"arxiv_id": "seed1", "title": "Seed Paper", "key_concepts": ["gen_rec"]}]
            with open(pm.seed_papers_json, "w") as f:
                json.dump(seed, f)

            profile = {"research_description": "Generative recommendation systems"}
            ctx = prepare_idea_context(pm, profile)

            self.assertIn("context_path", ctx)
            self.assertTrue(Path(ctx["context_path"]).exists())

            # Read context file
            with open(ctx["context_path"]) as f:
                data = json.load(f)
            self.assertEqual(data["seed_papers_count"], 1)
            self.assertIn("prompt", data)
            self.assertIn("Generative recommendation", data["prompt"])

    def test_context_with_indexed_papers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            # Create paper index with transferable techniques
            index = [{
                "arxiv_id": "2305.05065",
                "title": "TIGER",
                "run_id": "test_run",
                "transferable_techniques": ["RQ-VAE encoding"],
                "inspiration_ideas": ["Combine with LLM"],
            }]
            save_paper_index(pm.paper_index_json, index)

            ctx = prepare_idea_context(pm, {"research_description": "test"})
            self.assertEqual(ctx["techniques_count"], 1)
            self.assertEqual(ctx["inspirations_count"], 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Test Idea Persistence
# ═══════════════════════════════════════════════════════════════════════════════


class TestSaveIdeas(unittest.TestCase):
    """Tests for save_ideas function."""

    def test_save_ideas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            ideas_text = "## Idea 1: Test\n\nMotivation: testing"
            result = save_ideas(pm, ideas_text)

            self.assertTrue(result["ideas_saved"])
            self.assertTrue(Path(result["idea_path"]).exists())

            content = Path(result["idea_path"]).read_text()
            self.assertIn("Idea 1: Test", content)
            self.assertIn("test_run", content)


# ═══════════════════════════════════════════════════════════════════════════════
# Test Combined Run
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunKnowledgeSync(unittest.TestCase):
    """Tests for the combined run_knowledge_sync function."""

    def test_full_sync(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            papers = [{"arxiv_id": "2305.05065", "title": "TIGER"}]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            stats = run_knowledge_sync(pm, {"research_description": "test"})

            self.assertEqual(stats["new_count"], 1)
            self.assertIn("idea_context_path", stats)
            self.assertFalse(stats["ideas_saved"])

    def test_full_sync_with_ideas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            papers = [{"arxiv_id": "2305.05065", "title": "TIGER"}]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            stats = run_knowledge_sync(
                pm,
                {"research_description": "test"},
                ideas_text="## Idea 1\n\nGreat idea",
            )

            self.assertTrue(stats["ideas_saved"])
            self.assertTrue(Path(stats["idea_path"]).exists())

    def test_sync_empty_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            stats = run_knowledge_sync(pm, {})
            self.assertEqual(stats["new_count"], 0)
            self.assertEqual(stats["total_indexed"], 0)


if __name__ == "__main__":
    unittest.main()
