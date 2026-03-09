#!/usr/bin/env python3
"""Unit tests for repo_evaluator.py -- Code Repository Assessment Tool."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Standard path injection
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from repo_evaluator import (
    assess_integration_cost,
    evaluate_paper_repo,
    extract_code_links,
    run_repo_eval,
    search_github_for_paper,
)
from common.path_manager import PathManager


# ═══════════════════════════════════════════════════════════════════════════════
# Test Code Link Extraction
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractCodeLinks(unittest.TestCase):
    """Tests for GitHub/GitLab link extraction from text."""

    def test_github_link(self):
        text = "Code is available at https://github.com/owner/repo"
        links = extract_code_links(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["platform"], "github")
        self.assertEqual(links[0]["repo"], "owner/repo")

    def test_github_link_in_markdown(self):
        text = "See [code](https://github.com/owner/repo) for implementation"
        links = extract_code_links(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["repo"], "owner/repo")

    def test_gitlab_link(self):
        text = "Source at https://gitlab.com/team/project"
        links = extract_code_links(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["platform"], "gitlab")

    def test_multiple_links(self):
        text = (
            "GitHub: https://github.com/owner/repo1 "
            "and https://github.com/other/repo2"
        )
        links = extract_code_links(text)
        self.assertEqual(len(links), 2)

    def test_no_links(self):
        text = "This paper has no code repository."
        links = extract_code_links(text)
        self.assertEqual(len(links), 0)

    def test_github_io_filtered(self):
        text = "See https://github.com/owner/github.io for project page"
        links = extract_code_links(text)
        self.assertEqual(len(links), 0)

    def test_dedup_same_repo(self):
        text = (
            "https://github.com/owner/repo and again "
            "https://github.com/owner/repo/tree/main"
        )
        links = extract_code_links(text)
        self.assertEqual(len(links), 1)

    def test_generic_code_link(self):
        text = "Code is available at https://some-server.com/code/release.zip"
        links = extract_code_links(text)
        self.assertTrue(len(links) >= 1)
        self.assertEqual(links[0]["platform"], "other")

    def test_github_with_trailing_slash(self):
        text = "https://github.com/owner/repo/"
        links = extract_code_links(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["repo"], "owner/repo")


# ═══════════════════════════════════════════════════════════════════════════════
# Test Integration Cost Assessment
# ═══════════════════════════════════════════════════════════════════════════════


class TestAssessIntegrationCost(unittest.TestCase):
    """Tests for integration cost assessment logic."""

    def test_high_stars_python(self):
        repo_info = {
            "stars": 1000,
            "language": "Python",
            "is_archived": False,
            "license": "MIT",
        }
        self.assertEqual(assess_integration_cost(repo_info), "Low")

    def test_low_stars_no_license(self):
        repo_info = {
            "stars": 5,
            "language": "C++",
            "is_archived": False,
            "license": "",
        }
        self.assertEqual(assess_integration_cost(repo_info), "High")

    def test_archived_repo(self):
        repo_info = {
            "stars": 200,
            "language": "Python",
            "is_archived": True,
            "license": "Apache-2.0",
        }
        # Archived repos get penalty
        cost = assess_integration_cost(repo_info)
        self.assertIn(cost, ["Medium", "Low"])

    def test_medium_stars(self):
        repo_info = {
            "stars": 50,
            "language": "Java",
            "is_archived": False,
            "license": "MIT",
        }
        self.assertEqual(assess_integration_cost(repo_info), "Medium")


# ═══════════════════════════════════════════════════════════════════════════════
# Test Single Paper Evaluation
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvaluatePaperRepo(unittest.TestCase):
    """Tests for single paper repo evaluation."""

    @patch("repo_evaluator.fetch_github_repo_info")
    def test_link_in_abstract(self, mock_fetch):
        mock_fetch.return_value = {
            "full_name": "owner/repo",
            "stars": 500,
            "forks": 50,
            "language": "Python",
            "license": "MIT",
            "is_archived": False,
            "updated_at": "2025-01-01",
            "created_at": "2024-01-01",
            "open_issues": 10,
            "is_fork": False,
            "default_branch": "main",
            "topics": [],
            "description": "Test repo",
        }

        result = evaluate_paper_repo(
            arxiv_id="2305.05065",
            abstract="Code at https://github.com/owner/repo",
        )

        self.assertTrue(result["has_code"])
        self.assertEqual(result["stars"], 500)
        self.assertEqual(result["search_method"], "extracted_from_text")

    @patch("repo_evaluator.search_github_for_paper")
    @patch("repo_evaluator.fetch_github_repo_info")
    def test_github_search_fallback(self, mock_fetch, mock_search):
        mock_search.return_value = {
            "url": "https://github.com/found/repo",
            "platform": "github",
            "repo": "found/repo",
        }
        mock_fetch.return_value = {
            "full_name": "found/repo",
            "stars": 100,
            "forks": 10,
            "language": "Python",
            "license": "MIT",
            "is_archived": False,
            "updated_at": "2025-01-01",
            "created_at": "2024-01-01",
            "open_issues": 5,
            "is_fork": False,
            "default_branch": "main",
            "topics": [],
            "description": "",
        }

        result = evaluate_paper_repo(
            arxiv_id="2305.99999",
            title="TIGER: Towards Generating Recommendations",
            abstract="No code link here.",
        )

        self.assertTrue(result["has_code"])
        self.assertEqual(result["search_method"], "github_search")

    def test_no_code_found(self):
        with patch("repo_evaluator.search_github_for_paper", return_value=None):
            result = evaluate_paper_repo(
                arxiv_id="0000.00000",
                title="",
                abstract="No code info.",
            )

        self.assertFalse(result["has_code"])
        self.assertEqual(result["search_method"], "not_found")

    @patch("repo_evaluator.fetch_github_repo_info")
    def test_api_failure_degradation(self, mock_fetch):
        mock_fetch.return_value = None

        result = evaluate_paper_repo(
            arxiv_id="2305.05065",
            abstract="Code at https://github.com/owner/repo",
        )

        self.assertTrue(result["has_code"])
        self.assertTrue(result["github_api_failed"])
        self.assertEqual(result["integration_cost"], "Unknown")


# ═══════════════════════════════════════════════════════════════════════════════
# Test Batch Processing
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunRepoEval(unittest.TestCase):
    """Tests for batch run_repo_eval function."""

    def test_no_final_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            stats = run_repo_eval(pm, {})
            self.assertEqual(stats["total"], 0)

    @patch("repo_evaluator.evaluate_paper_repo")
    def test_batch_eval(self, mock_eval):
        mock_eval.return_value = {
            "arxiv_id": "2305.05065",
            "has_code": True,
            "github_url": "https://github.com/test/repo",
            "github_api_failed": False,
            "stars": 100,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            papers = [
                {"arxiv_id": "2305.05065", "title": "TIGER", "abstract": "test"},
            ]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            stats = run_repo_eval(pm, {})
            self.assertEqual(stats["total"], 1)
            self.assertEqual(stats["has_code_count"], 1)

            # Verify output file
            output = pm.skill5_repo_eval_paper("2305.05065")
            self.assertTrue(output.exists())


# ═══════════════════════════════════════════════════════════════════════════════
# Test Output JSON Structure
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutputStructure(unittest.TestCase):
    """Tests that output JSON has all required fields."""

    def test_all_fields_present(self):
        with patch("repo_evaluator.search_github_for_paper", return_value=None):
            result = evaluate_paper_repo(arxiv_id="test", title="", abstract="")

        required_fields = [
            "arxiv_id", "has_code", "github_url", "platform",
            "stars", "forks", "language", "integration_cost",
            "github_api_failed", "search_method",
        ]
        for field in required_fields:
            self.assertIn(field, result, f"Missing field: {field}")

        # Verify JSON serializable
        json.dumps(result)


if __name__ == "__main__":
    unittest.main()
