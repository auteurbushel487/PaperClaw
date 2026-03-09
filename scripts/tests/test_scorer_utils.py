"""
Unit tests for paper-relevance-scorer (scripts/scorer_utils.py).

Tests cover:
- Few-shot example building from seed papers
- Whitelist author matching (case-insensitive, partial)
- Top venue matching in comments field
- Score bonuses application (+1 each, capped at 10)
- Tolerant JSON extraction from Agent output:
  - Clean JSON array
  - JSON with Markdown fences
  - JSON with preamble/postamble text
  - Completely garbled output → degradation
- Format anomaly degradation (default score 5 + scoring_failed)
- Three-zone partitioning (high / edge / low)
- Integration: run_scorer full flow
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

from scorer_utils import (
    apply_bonuses,
    build_fewshot_examples,
    check_top_venue,
    check_whitelist_author,
    format_fewshot_for_prompt,
    parse_agent_scoring_output,
    partition_by_score,
    run_scorer,
    _degrade_all_papers,
    _validate_scored_papers,
)
from common.path_manager import PathManager


# ═══════════════════════════════════════════════════════════════════════════════
# Test Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def make_seed_paper(arxiv_id, title="Seed Paper", role="foundational"):
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": ["Author A"],
        "abstract": "This is a foundational paper about generative recommendation.",
        "role": role,
    }


def make_paper(arxiv_id, title=None, authors=None, comments=""):
    return {
        "arxiv_id": arxiv_id,
        "title": title or f"Paper {arxiv_id}",
        "authors": authors or ["Unknown Author"],
        "abstract": f"Abstract for {arxiv_id}",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "categories": ["cs.IR"],
        "comments": comments,
    }


class ScorerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pm = PathManager(root=self.tmpdir, run_id="test_run")
        self.pm.create_run_directory()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def _read_json(self, path):
        with open(path, "r") as f:
            return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# Few-Shot Example Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFewShotExamples(ScorerTestBase):

    def test_build_from_foundational(self):
        """Foundational papers are selected for few-shot."""
        seeds = [
            make_seed_paper("2305.05065", "TIGER", "foundational"),
            make_seed_paper("2502.18965", "OneRec", "foundational"),
            make_seed_paper("2305.19860", "P5", "benchmark"),
        ]
        examples = build_fewshot_examples(seeds, max_examples=3)
        self.assertEqual(len(examples), 2)  # Only 2 foundational
        self.assertEqual(examples[0]["score"], 10)

    def test_max_examples_cap(self):
        """Respect max_examples limit."""
        seeds = [make_seed_paper(f"id_{i}", role="foundational") for i in range(10)]
        examples = build_fewshot_examples(seeds, max_examples=3)
        self.assertEqual(len(examples), 3)

    def test_no_foundational_uses_any(self):
        """Falls back to any seed paper if no foundational."""
        seeds = [
            make_seed_paper("2305.19860", "P5", "benchmark"),
        ]
        examples = build_fewshot_examples(seeds)
        self.assertEqual(len(examples), 1)

    def test_empty_seeds(self):
        examples = build_fewshot_examples([])
        self.assertEqual(examples, [])

    def test_format_for_prompt(self):
        """Format output is a non-empty string."""
        examples = [{"title": "TIGER", "abstract": "...", "arxiv_id": "2305.05065",
                      "score": 10, "rationale": "Core paper"}]
        text = format_fewshot_for_prompt(examples)
        self.assertIn("TIGER", text)
        self.assertIn("10", text)

    def test_format_empty(self):
        text = format_fewshot_for_prompt([])
        self.assertIn("No foundational", text)


# ═══════════════════════════════════════════════════════════════════════════════
# Whitelist / Top Venue Matching Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestWhitelistMatching(ScorerTestBase):

    def test_exact_match(self):
        paper = make_paper("id1", authors=["Shashank Rajput"])
        self.assertTrue(check_whitelist_author(paper, ["Shashank Rajput"]))

    def test_case_insensitive(self):
        paper = make_paper("id1", authors=["shashank rajput"])
        self.assertTrue(check_whitelist_author(paper, ["Shashank Rajput"]))

    def test_partial_match(self):
        paper = make_paper("id1", authors=["Shashank Rajput (Stanford)"])
        self.assertTrue(check_whitelist_author(paper, ["Shashank Rajput"]))

    def test_no_match(self):
        paper = make_paper("id1", authors=["John Doe"])
        self.assertFalse(check_whitelist_author(paper, ["Shashank Rajput"]))

    def test_empty_whitelist(self):
        paper = make_paper("id1", authors=["Anyone"])
        self.assertFalse(check_whitelist_author(paper, []))

    def test_empty_authors(self):
        paper = make_paper("id1", authors=[])
        self.assertFalse(check_whitelist_author(paper, ["Someone"]))


class TestTopVenueMatching(ScorerTestBase):

    def test_venue_in_comments(self):
        paper = make_paper("id1", comments="Accepted at NeurIPS 2023")
        self.assertTrue(check_top_venue(paper, ["NeurIPS", "ICML"]))

    def test_case_insensitive(self):
        paper = make_paper("id1", comments="accepted at neurips 2023")
        self.assertTrue(check_top_venue(paper, ["NeurIPS"]))

    def test_no_match(self):
        paper = make_paper("id1", comments="Submitted to some workshop")
        self.assertFalse(check_top_venue(paper, ["NeurIPS", "ICML"]))

    def test_empty_comments(self):
        paper = make_paper("id1", comments="")
        self.assertFalse(check_top_venue(paper, ["NeurIPS"]))

    def test_empty_venues(self):
        paper = make_paper("id1", comments="Accepted at NeurIPS")
        self.assertFalse(check_top_venue(paper, []))


class TestApplyBonuses(ScorerTestBase):

    def test_whitelist_bonus(self):
        papers = [{"arxiv_id": "id1", "authors": ["Shashank Rajput"],
                    "relevance_score": 7, "comments": ""}]
        result = apply_bonuses(papers, ["Shashank Rajput"], [])
        self.assertEqual(result[0]["relevance_score"], 8)
        self.assertTrue(result[0]["is_whitelist_author"])

    def test_venue_bonus(self):
        papers = [{"arxiv_id": "id1", "authors": ["Unknown"],
                    "relevance_score": 6, "comments": "NeurIPS 2023"}]
        result = apply_bonuses(papers, [], ["NeurIPS"])
        self.assertEqual(result[0]["relevance_score"], 7)
        self.assertTrue(result[0]["is_top_venue"])

    def test_both_bonuses_cap_at_10(self):
        papers = [{"arxiv_id": "id1", "authors": ["Shashank Rajput"],
                    "relevance_score": 9, "comments": "NeurIPS 2023"}]
        result = apply_bonuses(papers, ["Shashank Rajput"], ["NeurIPS"])
        self.assertEqual(result[0]["relevance_score"], 10)  # Capped

    def test_no_bonuses(self):
        papers = [{"arxiv_id": "id1", "authors": ["Nobody"],
                    "relevance_score": 5, "comments": ""}]
        result = apply_bonuses(papers, ["Someone Else"], ["NeurIPS"])
        self.assertEqual(result[0]["relevance_score"], 5)


# ═══════════════════════════════════════════════════════════════════════════════
# Tolerant JSON Extraction Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentOutputParsing(ScorerTestBase):

    def _make_clean_agent_output(self):
        return json.dumps([
            {"arxiv_id": "id1", "relevance_score": 8,
             "scoring_rationale": "Highly relevant", "tags": ["gen_rec"]},
            {"arxiv_id": "id2", "relevance_score": 3,
             "scoring_rationale": "Not relevant", "tags": ["other"]},
        ])

    def test_clean_json(self):
        """Clean JSON array is parsed correctly."""
        papers = [make_paper("id1"), make_paper("id2")]
        output = self._make_clean_agent_output()
        result = parse_agent_scoring_output(output, papers)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["relevance_score"], 8)

    def test_json_with_markdown_fences(self):
        """JSON wrapped in ```json ``` fences is extracted."""
        papers = [make_paper("id1"), make_paper("id2")]
        output = "```json\n" + self._make_clean_agent_output() + "\n```"
        result = parse_agent_scoring_output(output, papers)
        self.assertEqual(len(result), 2)

    def test_json_with_preamble(self):
        """JSON with preamble text is extracted."""
        papers = [make_paper("id1"), make_paper("id2")]
        output = "Here are my scoring results:\n\n" + self._make_clean_agent_output() + "\n\nHope this helps!"
        result = parse_agent_scoring_output(output, papers)
        self.assertEqual(len(result), 2)

    def test_completely_garbled_output(self):
        """Garbled output triggers degradation."""
        papers = [make_paper("id1"), make_paper("id2")]
        output = "This is completely garbled nonsense with no JSON at all!"
        result = parse_agent_scoring_output(output, papers)
        self.assertEqual(len(result), 2)
        for p in result:
            self.assertEqual(p["relevance_score"], 5)
            self.assertTrue(p["scoring_failed"])

    def test_partial_scoring(self):
        """Only some papers scored — unscored get default."""
        papers = [make_paper("id1"), make_paper("id2"), make_paper("id3")]
        output = json.dumps([
            {"arxiv_id": "id1", "relevance_score": 9,
             "scoring_rationale": "Great", "tags": ["gen_rec"]},
        ])
        result = parse_agent_scoring_output(output, papers)
        self.assertEqual(len(result), 3)
        # id1 scored normally
        scored_id1 = next(p for p in result if p["arxiv_id"] == "id1")
        self.assertEqual(scored_id1["relevance_score"], 9)
        # id2, id3 get default
        scored_id2 = next(p for p in result if p["arxiv_id"] == "id2")
        self.assertEqual(scored_id2["relevance_score"], 5)
        self.assertTrue(scored_id2.get("scoring_failed", False))

    def test_invalid_score_value(self):
        """Non-integer score triggers default."""
        papers = [make_paper("id1")]
        output = json.dumps([
            {"arxiv_id": "id1", "relevance_score": "high",
             "scoring_rationale": "Great", "tags": []},
        ])
        result = parse_agent_scoring_output(output, papers)
        self.assertEqual(result[0]["relevance_score"], 5)
        self.assertTrue(result[0].get("scoring_failed", False))

    def test_score_clamping(self):
        """Scores > 10 or < 0 are clamped."""
        papers = [make_paper("id1"), make_paper("id2")]
        output = json.dumps([
            {"arxiv_id": "id1", "relevance_score": 15, "scoring_rationale": "...", "tags": []},
            {"arxiv_id": "id2", "relevance_score": -5, "scoring_rationale": "...", "tags": []},
        ])
        result = parse_agent_scoring_output(output, papers)
        self.assertEqual(result[0]["relevance_score"], 10)
        self.assertEqual(result[1]["relevance_score"], 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Three-Zone Partitioning Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPartitionByScore(ScorerTestBase):

    def test_correct_partitioning(self):
        papers = [
            {"arxiv_id": "h1", "relevance_score": 9},
            {"arxiv_id": "h2", "relevance_score": 7},
            {"arxiv_id": "e1", "relevance_score": 5},
            {"arxiv_id": "e2", "relevance_score": 4},
            {"arxiv_id": "l1", "relevance_score": 2},
            {"arxiv_id": "l2", "relevance_score": 3},
        ]
        result = partition_by_score(papers)
        self.assertEqual(len(result["high"]), 2)
        self.assertEqual(len(result["edge"]), 2)
        self.assertEqual(len(result["low"]), 2)

    def test_sorted_within_zones(self):
        papers = [
            {"arxiv_id": "a", "relevance_score": 7},
            {"arxiv_id": "b", "relevance_score": 9},
            {"arxiv_id": "c", "relevance_score": 8},
        ]
        result = partition_by_score(papers)
        scores = [p["relevance_score"] for p in result["high"]]
        self.assertEqual(scores, [9, 8, 7])

    def test_custom_thresholds(self):
        papers = [
            {"arxiv_id": "a", "relevance_score": 8},
            {"arxiv_id": "b", "relevance_score": 5},
            {"arxiv_id": "c", "relevance_score": 2},
        ]
        result = partition_by_score(papers, high_threshold=8, edge_low=3, edge_high=7)
        self.assertEqual(len(result["high"]), 1)
        self.assertEqual(len(result["edge"]), 1)
        self.assertEqual(len(result["low"]), 1)

    def test_empty_input(self):
        result = partition_by_score([])
        self.assertEqual(result, {"high": [], "edge": [], "low": []})


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test: run_scorer
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunScorer(ScorerTestBase):

    def test_full_flow_with_agent_output(self):
        """Full scoring flow with Agent output."""
        # Write search results
        search_data = {
            "papers": [
                make_paper("id1", "Gen Rec Paper", ["Shashank Rajput"], "NeurIPS 2023"),
                make_paper("id2", "Other Paper", ["John Doe"], ""),
            ],
            "stats": {"new_increment": 2},
        }
        self._write_json(self.pm.skill1_search_results, search_data)

        # Write seed papers
        seeds = [make_seed_paper("2305.05065", "TIGER")]
        self._write_json(self.pm.seed_papers_json, seeds)

        # Simulate Agent output
        agent_output = json.dumps([
            {"arxiv_id": "id1", "relevance_score": 8,
             "scoring_rationale": "Highly relevant", "tags": ["gen_rec"]},
            {"arxiv_id": "id2", "relevance_score": 3,
             "scoring_rationale": "Not relevant", "tags": ["other"]},
        ])

        profile = {
            "whitelist_authors": ["Shashank Rajput"],
            "top_venues": ["NeurIPS"],
            "score_thresholds": {"high": 7, "edge_low": 4, "edge_high": 6},
        }

        stats = run_scorer(pm=self.pm, profile=profile, agent_output=agent_output)

        # id1: 8 + 1(wl) + 1(venue) = 10 → high
        # id2: 3 → low
        self.assertEqual(stats["scored_high"], 1)
        self.assertEqual(stats["scored_low"], 1)

        # Verify output file
        result = self._read_json(self.pm.skill2_scored_results)
        high = result["high"]
        self.assertEqual(len(high), 1)
        self.assertEqual(high[0]["relevance_score"], 10)
        self.assertTrue(high[0]["is_whitelist_author"])
        self.assertTrue(high[0]["is_top_venue"])

    def test_empty_search_results(self):
        """No papers to score."""
        self._write_json(self.pm.skill1_search_results, {"papers": []})

        profile = {"score_thresholds": {"high": 7, "edge_low": 4, "edge_high": 6}}
        stats = run_scorer(pm=self.pm, profile=profile)

        self.assertEqual(stats["scored_high"], 0)
        self.assertEqual(stats["scored_edge"], 0)
        self.assertEqual(stats["scored_low"], 0)

    def test_degradation_on_bad_output(self):
        """Bad Agent output triggers full degradation."""
        search_data = {
            "papers": [make_paper("id1"), make_paper("id2")],
            "stats": {"new_increment": 2},
        }
        self._write_json(self.pm.skill1_search_results, search_data)

        profile = {
            "whitelist_authors": [],
            "top_venues": [],
            "score_thresholds": {"high": 7, "edge_low": 4, "edge_high": 6},
        }

        stats = run_scorer(
            pm=self.pm, profile=profile,
            agent_output="totally garbled nonsense!!!"
        )

        # All papers get score 5 → edge zone
        self.assertEqual(stats["scored_edge"], 2)

        result = self._read_json(self.pm.skill2_scored_results)
        for paper in result["edge"]:
            self.assertTrue(paper.get("scoring_failed"))


# ═══════════════════════════════════════════════════════════════════════════════
# Degradation Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDegradation(ScorerTestBase):

    def test_degrade_all_papers(self):
        papers = [make_paper("id1"), make_paper("id2")]
        result = _degrade_all_papers(papers)
        self.assertEqual(len(result), 2)
        for p in result:
            self.assertEqual(p["relevance_score"], 5)
            self.assertTrue(p["scoring_failed"])

    def test_validate_missing_fields(self):
        """Validate fills missing fields."""
        scored = [{"arxiv_id": "id1"}]  # Missing score, rationale, tags
        original = [make_paper("id1")]
        result = _validate_scored_papers(scored, original)
        self.assertEqual(result[0]["relevance_score"], 5)
        self.assertEqual(result[0]["scoring_rationale"], "N/A")
        self.assertEqual(result[0]["tags"], [])


if __name__ == "__main__":
    unittest.main()
