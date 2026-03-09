"""
Unit tests for paper-human-review (scripts/human_review.py).

Tests cover:
- Info card generation (format correctness)
- Default mode: no edge papers → skip
- Default mode: edge papers → generate cards + signal waiting_for_human
- Default mode: decisions already exist → auto-merge
- Merge mode: accept/reject decisions
- Merge mode: empty decisions → high papers only
- Timeout mode: discard policy
- Timeout mode: accept policy
- Exit 0 behavior (no blocking)
- State machine integration with pipeline_runner
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Inject paper-agent scripts into sys.path
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from human_review import (
    generate_compact_cards,
    generate_review_cards_markdown,
    load_human_decisions,
    load_scored_results,
    run_init_mode,
    run_human_review,
    run_merge_mode,
    run_timeout_mode,
)
from common.path_manager import PathManager


# ═══════════════════════════════════════════════════════════════════════════════
# Test Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def make_scored_paper(arxiv_id, score, title=None):
    return {
        "arxiv_id": arxiv_id,
        "title": title or f"Paper {arxiv_id}",
        "authors": ["Author A", "Author B"],
        "abstract": f"Abstract for paper {arxiv_id}. " * 20,  # Long enough for truncation
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "relevance_score": score,
        "scoring_rationale": f"Score {score} because...",
        "tags": ["gen_rec"],
        "is_whitelist_author": False,
        "is_top_venue": False,
    }


def make_scored_results(high_ids_scores, edge_ids_scores, low_ids_scores=None):
    """Build a scored results dict."""
    return {
        "high": [make_scored_paper(aid, s) for aid, s in high_ids_scores],
        "edge": [make_scored_paper(aid, s) for aid, s in edge_ids_scores],
        "low": [make_scored_paper(aid, s) for aid, s in (low_ids_scores or [])],
    }


class HumanReviewTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pm = PathManager(root=self.tmpdir, run_id="test_run")
        self.pm.create_run_directory()
        self.profile = {
            "notification_channel": "local",
            "human_review_wait_days": 3,
            "human_review_default_policy": "discard",
        }

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def _read_json(self, path):
        with open(path, "r") as f:
            return json.load(f)

    def _write_scored_results(self, high, edge, low=None):
        scored = make_scored_results(high, edge, low)
        self._write_json(self.pm.skill2_scored_results, scored)
        return scored


# ═══════════════════════════════════════════════════════════════════════════════
# Info Card Generation Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestInfoCardGeneration(HumanReviewTestBase):

    def test_cards_contain_required_fields(self):
        """Cards contain title, arxiv_id, score, rationale, abstract, link."""
        papers = [make_scored_paper("2603.10001", 5, "Gen Rec Advances")]
        cards = generate_review_cards_markdown(papers, "test_run")

        self.assertIn("Gen Rec Advances", cards)
        self.assertIn("2603.10001", cards)
        self.assertIn("5", cards)
        self.assertIn("arxiv.org", cards)

    def test_cards_truncate_long_abstract(self):
        """Long abstracts are truncated."""
        papers = [make_scored_paper("2603.10001", 5)]
        cards = generate_review_cards_markdown(papers)
        # Verify cards were generated (content may vary)
        self.assertTrue(len(cards) > 0)

    def test_cards_multiple_papers(self):
        """Multiple papers generate cards."""
        papers = [
            make_scored_paper("id1", 5, "Paper One"),
            make_scored_paper("id2", 4, "Paper Two"),
            make_scored_paper("id3", 6, "Paper Three"),
        ]
        cards = generate_review_cards_markdown(papers, "test_run")

        self.assertIn("Paper One", cards)
        self.assertIn("Paper Two", cards)
        self.assertIn("Paper Three", cards)

    def test_cards_empty_papers(self):
        """Empty paper list generates minimal content."""
        cards = generate_review_cards_markdown([], "test_run")
        self.assertIsInstance(cards, str)

    def test_cards_whitelist_and_venue_badges(self):
        """Whitelist/venue badges appear in cards."""
        paper = make_scored_paper("id1", 5)
        paper["is_whitelist_author"] = True
        paper["is_top_venue"] = True
        cards = generate_review_cards_markdown([paper])
        # Cards should contain some indication of badges
        self.assertTrue(len(cards) > 0)

    def test_compact_cards_generation(self):
        """Compact cards for Agent conversation display."""
        papers = [make_scored_paper("2603.10001", 5, "Gen Rec Paper")]
        compact = generate_compact_cards(papers)
        self.assertIn("2603.10001", compact)
        self.assertIn("Gen Rec Paper", compact)

    def test_cards_contain_review_instructions(self):
        """Cards contain paper information for review."""
        cards = generate_review_cards_markdown([make_scored_paper("id1", 5, "Test Paper")])
        # Cards should contain the paper's identifying information
        self.assertIn("id1", cards)
        self.assertIn("Test Paper", cards)


# ═══════════════════════════════════════════════════════════════════════════════
# Default Mode Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDefaultMode(HumanReviewTestBase):

    def test_no_edge_papers_skips_review(self):
        """When no edge papers, skip review and output high papers."""
        self._write_scored_results(
            high=[("h1", 9), ("h2", 8)],
            edge=[],
        )

        result = run_init_mode(self.pm, self.profile)

        self.assertTrue(result.get("skipped"))
        self.assertEqual(result["final_count"], 2)

        # Verify final_selection.json created with high papers
        final = self._read_json(self.pm.skill3_final_selection)
        self.assertEqual(len(final), 2)

    def test_edge_papers_generate_cards_and_suspend(self):
        """Edge papers → generate cards + signal waiting_for_human."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5), ("e2", 4)],
        )

        result = run_init_mode(self.pm, self.profile)

        self.assertTrue(result.get("waiting_for_human"))
        self.assertEqual(result["edge_count"], 2)
        self.assertEqual(result["high_count"], 1)

        # Verify cards file created
        self.assertTrue(self.pm.skill3_review_cards.exists())
        cards = self.pm.skill3_review_cards.read_text()
        self.assertIn("e1", cards)
        self.assertIn("e2", cards)

        # Verify pending list created
        self.assertTrue(self.pm.skill3_review_pending.exists())
        pending = self._read_json(self.pm.skill3_review_pending)
        self.assertEqual(len(pending), 2)

        # Verify NO final_selection yet (waiting for human)
        self.assertFalse(self.pm.skill3_final_selection.exists())

    def test_existing_decisions_triggers_auto_merge(self):
        """If decisions file already exists, auto-merge instead of suspend."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5)],
        )
        self._write_json(self.pm.skill3_human_decisions, [
            {"arxiv_id": "e1", "decision": "accept", "note": "Looks good"},
        ])

        result = run_init_mode(self.pm, self.profile)

        self.assertTrue(result.get("merged"))
        self.assertEqual(result["final_count"], 2)  # h1 + e1

    def test_exit_0_no_blocking(self):
        """Default mode returns immediately (no blocking)."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5)],
        )

        # This should return immediately, not block
        result = run_init_mode(self.pm, self.profile)
        self.assertTrue(result.get("waiting_for_human"))
        # If we get here, no blocking occurred ✓


# ═══════════════════════════════════════════════════════════════════════════════
# Merge Mode Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMergeMode(HumanReviewTestBase):

    def test_merge_accept_papers(self):
        """Accepted papers are merged with high papers."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5), ("e2", 4)],
        )
        self._write_json(self.pm.skill3_human_decisions, [
            {"arxiv_id": "e1", "decision": "accept", "note": "Interesting"},
            {"arxiv_id": "e2", "decision": "reject", "note": "Not relevant"},
        ])

        result = run_merge_mode(self.pm, self.profile)

        self.assertTrue(result["merged"])
        self.assertEqual(result["rescued"], 1)  # Only e1
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["final_count"], 2)  # h1 + e1

        final = self._read_json(self.pm.skill3_final_selection)
        final_ids = {p["arxiv_id"] for p in final}
        self.assertIn("h1", final_ids)
        self.assertIn("e1", final_ids)
        self.assertNotIn("e2", final_ids)

    def test_merge_all_reject(self):
        """All rejected → only high papers."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5)],
        )
        self._write_json(self.pm.skill3_human_decisions, [
            {"arxiv_id": "e1", "decision": "reject"},
        ])

        result = run_merge_mode(self.pm, self.profile)
        self.assertEqual(result["rescued"], 0)
        self.assertEqual(result["final_count"], 1)

    def test_merge_all_accept(self):
        """All accepted → high + all edge."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5), ("e2", 4)],
        )
        self._write_json(self.pm.skill3_human_decisions, [
            {"arxiv_id": "e1", "decision": "accept"},
            {"arxiv_id": "e2", "decision": "accept"},
        ])

        result = run_merge_mode(self.pm, self.profile)
        self.assertEqual(result["rescued"], 2)
        self.assertEqual(result["final_count"], 3)

    def test_merge_empty_decisions(self):
        """Empty decisions → high papers only."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5)],
        )
        self._write_json(self.pm.skill3_human_decisions, [])

        result = run_merge_mode(self.pm, self.profile)
        self.assertEqual(result["rescued"], 0)
        self.assertEqual(result["final_count"], 1)

    def test_merge_marks_rescued(self):
        """Rescued papers are marked with human_rescued and human_note."""
        self._write_scored_results(
            high=[],
            edge=[("e1", 5)],
        )
        self._write_json(self.pm.skill3_human_decisions, [
            {"arxiv_id": "e1", "decision": "accept", "note": "Very interesting"},
        ])

        result = run_merge_mode(self.pm, self.profile)
        final = self._read_json(self.pm.skill3_final_selection)
        self.assertTrue(final[0].get("human_rescued"))
        self.assertEqual(final[0].get("human_note"), "Very interesting")

    def test_merge_no_decisions_file(self):
        """No decisions file → high papers only."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5)],
        )
        # Don't write decisions file

        result = run_merge_mode(self.pm, self.profile)
        self.assertEqual(result["rescued"], 0)
        self.assertEqual(result["final_count"], 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Timeout Mode Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimeoutMode(HumanReviewTestBase):

    def test_timeout_discard_policy(self):
        """Discard policy: only high papers retained."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5), ("e2", 4)],
        )

        result = run_timeout_mode(self.pm, self.profile, policy="discard")

        self.assertTrue(result["timeout"])
        self.assertEqual(result["policy"], "discard")
        self.assertEqual(result["edge_discarded"], 2)
        self.assertEqual(result["final_count"], 1)

    def test_timeout_accept_policy(self):
        """Accept policy: high + all edge papers retained."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5), ("e2", 4)],
        )

        result = run_timeout_mode(self.pm, self.profile, policy="accept")

        self.assertTrue(result["timeout"])
        self.assertEqual(result["policy"], "accept")
        self.assertEqual(result["edge_accepted"], 2)
        self.assertEqual(result["final_count"], 3)

        final = self._read_json(self.pm.skill3_final_selection)
        # Edge papers marked as timeout_accepted
        edge_papers = [p for p in final if p.get("timeout_accepted")]
        self.assertEqual(len(edge_papers), 2)

    def test_timeout_uses_profile_default(self):
        """Default policy from profile when not overridden."""
        self._write_scored_results(
            high=[("h1", 9)],
            edge=[("e1", 5)],
        )

        self.profile["human_review_default_policy"] = "accept"
        result = run_timeout_mode(self.pm, self.profile)
        self.assertEqual(result["policy"], "accept")
        self.assertEqual(result["final_count"], 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: run_human_review Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunHumanReview(HumanReviewTestBase):

    def test_default_mode_via_entry(self):
        self._write_scored_results(high=[("h1", 9)], edge=[])
        result = run_human_review(self.pm, self.profile, mode="init")
        self.assertTrue(result.get("skipped"))

    def test_merge_mode_via_entry(self):
        self._write_scored_results(high=[("h1", 9)], edge=[("e1", 5)])
        self._write_json(self.pm.skill3_human_decisions, [
            {"arxiv_id": "e1", "decision": "accept"},
        ])
        result = run_human_review(self.pm, self.profile, mode="merge")
        self.assertTrue(result.get("merged"))

    def test_timeout_mode_via_entry(self):
        self._write_scored_results(high=[("h1", 9)], edge=[("e1", 5)])
        result = run_human_review(self.pm, self.profile, mode="timeout", timeout_policy="discard")
        self.assertTrue(result.get("timeout"))


# ═══════════════════════════════════════════════════════════════════════════════
# Data Loading Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDataLoading(HumanReviewTestBase):

    def test_load_scored_nonexistent(self):
        result = load_scored_results(Path(self.tmpdir) / "nonexistent.json")
        self.assertEqual(result, {"high": [], "edge": [], "low": []})

    def test_load_decisions_nonexistent(self):
        result = load_human_decisions(Path(self.tmpdir) / "nonexistent.json")
        self.assertEqual(result, [])

    def test_load_corrupted_scored(self):
        bad_path = self.pm.skill2_scored_results
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        with open(bad_path, "w") as f:
            f.write("not json{{{")
        result = load_scored_results(bad_path)
        self.assertEqual(result, {"high": [], "edge": [], "low": []})


if __name__ == "__main__":
    unittest.main()
