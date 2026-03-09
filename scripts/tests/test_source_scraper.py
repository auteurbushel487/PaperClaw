"""
Unit tests for paper-source-scraper (scripts/source_scraper.py).

Tests cover:
- Intra-run dedup (duplicate arXiv IDs from multiple searches)
- Cross-run dedup (filtering against seen_papers + seed_papers)
- Seed paper filtering (search results containing seed papers)
- seen_papers.json registration (new papers added after search)
- seen_papers.json corruption recovery (from paper_index + seed_papers)
- Rate limit retry mechanism (mocked 429 errors)
- Full integration test with mocked ArxivSearcher
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Inject paper-agent scripts into sys.path
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from source_scraper import (
    _normalize_paper,
    _recover_seen_papers,
    dedup_cross_run,
    dedup_intra_run,
    load_seed_ids,
    load_seen_papers,
    register_new_papers_to_seen,
    run_source_scraper,
    search_with_retry,
)
from common.path_manager import PathManager


# ═══════════════════════════════════════════════════════════════════════════════
# Test Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def make_raw_paper(arxiv_id, title=None, source="keyword:test"):
    """Create a raw ArxivSearcher result dict."""
    return {
        "id": arxiv_id,
        "title": title or f"Paper {arxiv_id}",
        "authors": ["Author A", "Author B"],
        "summary": f"Abstract for {arxiv_id}",
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
        "published": "2026-03-01",
        "categories": ["cs.IR", "cs.AI"],
        "comments": "",
    }


def make_normalized_paper(arxiv_id, title=None, source="keyword:test"):
    """Create a normalized paper dict (pipeline format)."""
    return {
        "arxiv_id": arxiv_id,
        "title": title or f"Paper {arxiv_id}",
        "authors": ["Author A", "Author B"],
        "abstract": f"Abstract for {arxiv_id}",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "source": source,
        "published_date": "2026-03-01",
        "categories": ["cs.IR", "cs.AI"],
        "comments": "",
    }


class ScraperTestBase(unittest.TestCase):
    """Base class with common setup."""

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
# Intra-Run Dedup Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntraRunDedup(ScraperTestBase):
    """Test intra-run deduplication (same paper from multiple queries)."""

    def test_no_duplicates(self):
        """Papers with unique IDs pass through unchanged."""
        papers = [
            make_normalized_paper("2603.10001"),
            make_normalized_paper("2603.10002"),
            make_normalized_paper("2603.10003"),
        ]
        result = dedup_intra_run(papers)
        self.assertEqual(len(result), 3)

    def test_duplicate_ids_merged(self):
        """Duplicate arXiv IDs are merged, first occurrence kept."""
        papers = [
            make_normalized_paper("2603.10001", source="keyword:gen_rec"),
            make_normalized_paper("2603.10002", source="keyword:gen_rec"),
            make_normalized_paper("2603.10001", source="author:Smith"),  # Duplicate
        ]
        result = dedup_intra_run(papers)
        self.assertEqual(len(result), 2)
        # First occurrence kept, source updated
        merged = next(p for p in result if p["arxiv_id"] == "2603.10001")
        self.assertIn("keyword:gen_rec", merged["source"])
        self.assertIn("author:Smith", merged["source"])

    def test_empty_input(self):
        result = dedup_intra_run([])
        self.assertEqual(result, [])

    def test_all_duplicates(self):
        """All papers are duplicates of the same ID."""
        papers = [
            make_normalized_paper("2603.10001", source="q1"),
            make_normalized_paper("2603.10001", source="q2"),
            make_normalized_paper("2603.10001", source="q3"),
        ]
        result = dedup_intra_run(papers)
        self.assertEqual(len(result), 1)
        self.assertIn("q1", result[0]["source"])
        self.assertIn("q2", result[0]["source"])
        self.assertIn("q3", result[0]["source"])

    def test_papers_without_id_skipped(self):
        """Papers with empty arxiv_id are filtered out."""
        papers = [
            make_normalized_paper("2603.10001"),
            {"arxiv_id": "", "title": "No ID Paper"},
        ]
        result = dedup_intra_run(papers)
        self.assertEqual(len(result), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-Run Dedup Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossRunDedup(ScraperTestBase):
    """Test cross-run deduplication (against seen + seed papers)."""

    def test_all_new(self):
        """All papers are new (not in seen or seed)."""
        papers = [
            make_normalized_paper("2603.10001"),
            make_normalized_paper("2603.10002"),
        ]
        result = dedup_cross_run(papers, seen_ids=set(), seed_ids=set())
        self.assertEqual(len(result), 2)

    def test_filter_seen_papers(self):
        """Papers in seen_papers.json are filtered."""
        papers = [
            make_normalized_paper("2603.10001"),
            make_normalized_paper("2603.10002"),
            make_normalized_paper("2603.10003"),
        ]
        seen_ids = {"2603.10001", "2603.10003"}
        result = dedup_cross_run(papers, seen_ids=seen_ids, seed_ids=set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["arxiv_id"], "2603.10002")

    def test_filter_seed_papers(self):
        """Papers in seed_papers.json are filtered."""
        papers = [
            make_normalized_paper("2305.05065"),  # TIGER (seed paper)
            make_normalized_paper("2603.10001"),
        ]
        seed_ids = {"2305.05065", "2502.18965"}
        result = dedup_cross_run(papers, seen_ids=set(), seed_ids=seed_ids)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["arxiv_id"], "2603.10001")

    def test_filter_both_seen_and_seed(self):
        """Papers in either seen or seed are filtered."""
        papers = [
            make_normalized_paper("2305.05065"),  # Seed
            make_normalized_paper("2603.10001"),  # Seen
            make_normalized_paper("2603.10002"),  # New!
        ]
        result = dedup_cross_run(
            papers,
            seen_ids={"2603.10001"},
            seed_ids={"2305.05065"},
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["arxiv_id"], "2603.10002")


# ═══════════════════════════════════════════════════════════════════════════════
# Seen Papers Management Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeenPapersManagement(ScraperTestBase):
    """Test seen_papers.json loading, registration, and recovery."""

    def test_load_nonexistent(self):
        result = load_seen_papers(Path(self.tmpdir) / "nonexistent.json")
        self.assertEqual(result, {})

    def test_load_valid(self):
        self._write_json(self.pm.seen_papers_json, {
            "2603.10001": {"source": "search", "first_seen_date": "2026-03-01"}
        })
        result = load_seen_papers(self.pm.seen_papers_json)
        self.assertIn("2603.10001", result)

    def test_load_corrupted_triggers_recovery(self):
        """Corrupted file triggers recovery from index + seed."""
        # Write corrupted seen
        with open(self.pm.seen_papers_json, "w") as f:
            f.write("corrupted{{{not json")

        # Write recoverable data
        self._write_json(self.pm.paper_index_json, [
            {"arxiv_id": "idx_001", "indexed_at": "2026-01-01", "run_id": "run1"},
        ])
        self._write_json(self.pm.seed_papers_json, [
            {"arxiv_id": "seed_001"},
        ])

        result = load_seen_papers(self.pm.seen_papers_json)
        self.assertIn("idx_001", result)
        self.assertIn("seed_001", result)

    def test_register_new_papers(self):
        """New papers are registered to seen."""
        seen = {}
        papers = [
            make_normalized_paper("2603.10001"),
            make_normalized_paper("2603.10002"),
        ]
        count = register_new_papers_to_seen(
            papers, seen, self.pm.seen_papers_json, "test_run"
        )

        self.assertEqual(count, 2)
        saved = self._read_json(self.pm.seen_papers_json)
        self.assertIn("2603.10001", saved)
        self.assertEqual(saved["2603.10001"]["source"], "search")
        self.assertEqual(saved["2603.10001"]["first_seen_run_id"], "test_run")

    def test_register_no_duplicates(self):
        """Already-seen papers are not re-registered."""
        seen = {
            "2603.10001": {"source": "search", "first_seen_date": "2026-01-01"}
        }
        papers = [
            make_normalized_paper("2603.10001"),  # Already seen
            make_normalized_paper("2603.10002"),  # New
        ]
        count = register_new_papers_to_seen(
            papers, seen, self.pm.seen_papers_json, "test_run"
        )
        self.assertEqual(count, 1)  # Only 10002 is new

    def test_recovery_from_index_only(self):
        """Recovery works with only paper_index.json."""
        self._write_json(self.pm.paper_index_json, [
            {"arxiv_id": "idx_001", "indexed_at": "2026-01-15", "run_id": "run1"},
            {"arxiv_id": "idx_002", "indexed_at": "2026-02-01", "run_id": "run2"},
        ])

        result = _recover_seen_papers(self.pm.root)
        self.assertEqual(len(result), 2)
        self.assertIn("idx_001", result)

    def test_recovery_from_seed_only(self):
        """Recovery works with only seed_papers.json."""
        self._write_json(self.pm.seed_papers_json, [
            {"arxiv_id": "2305.05065"},
            {"arxiv_id": "2502.18965"},
        ])

        result = _recover_seen_papers(self.pm.root)
        self.assertEqual(len(result), 2)
        self.assertEqual(result["2305.05065"]["source"], "seed")

    def test_recovery_empty(self):
        """Recovery with no recovery sources returns empty."""
        result = _recover_seen_papers(self.pm.root)
        self.assertEqual(result, {})


class TestLoadSeedIds(ScraperTestBase):
    """Test seed_papers.json ID loading."""

    def test_load_seed_ids(self):
        self._write_json(self.pm.seed_papers_json, [
            {"arxiv_id": "2305.05065"},
            {"arxiv_id": "2502.18965"},
        ])
        result = load_seed_ids(self.pm.seed_papers_json)
        self.assertEqual(result, {"2305.05065", "2502.18965"})

    def test_load_nonexistent(self):
        result = load_seed_ids(Path(self.tmpdir) / "nonexistent.json")
        self.assertEqual(result, set())


# ═══════════════════════════════════════════════════════════════════════════════
# Rate Limit Retry Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestRateLimitRetry(ScraperTestBase):
    """Test exponential backoff retry for API errors."""

    @patch("source_scraper.time.sleep")
    def test_retry_on_rate_limit(self, mock_sleep):
        """Retries on rate limit (429) with exponential backoff."""
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = [
            Exception("HTTP Error 429: Too Many Requests"),
            Exception("HTTP Error 429: Too Many Requests"),
            [make_raw_paper("2603.10001")],  # Success on 3rd attempt
        ]

        result = search_with_retry(
            mock_searcher,
            max_retries=3,
            keywords=["test"],
            days=7,
            max_results=10,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(mock_searcher.search.call_count, 3)
        # Verify exponential backoff delays
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("source_scraper.time.sleep")
    def test_all_retries_fail(self, mock_sleep):
        """Returns empty list when all retries fail."""
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = Exception("Network error")

        result = search_with_retry(
            mock_searcher,
            max_retries=3,
            keywords=["test"],
        )

        self.assertEqual(result, [])
        self.assertEqual(mock_searcher.search.call_count, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test: run_source_scraper
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunSourceScraper(ScraperTestBase):
    """Integration test for the full source scraper flow."""

    def _make_mock_searcher(self, results_by_query=None):
        """Create a mock searcher that returns specified results."""
        mock = MagicMock()
        if results_by_query is None:
            # Default: return 3 papers for any query
            mock.search.return_value = [
                make_raw_paper("2603.10001"),
                make_raw_paper("2603.10002"),
                make_raw_paper("2603.10003"),
            ]
        else:
            mock.search.side_effect = results_by_query
        return mock

    @patch("source_scraper.time.sleep")  # Skip delays
    def test_full_scraper_flow(self, mock_sleep):
        """Full flow: keyword search → dedup → register → output."""
        mock_searcher = self._make_mock_searcher([
            # First keyword search
            [make_raw_paper("2603.10001"), make_raw_paper("2603.10002")],
            # Second keyword search (with one overlap)
            [make_raw_paper("2603.10002"), make_raw_paper("2603.10003")],
        ])

        profile = {
            "keywords": ["generative recommendation", "semantic ID"],
            "whitelist_authors": [],
            "arxiv_categories": ["cs.IR"],
            "search_days": 7,
        }

        output = run_source_scraper(
            profile=profile, pm=self.pm, searcher=mock_searcher
        )

        # Verify stats
        stats = output["stats"]
        self.assertEqual(stats["total_raw"], 4)       # 2 + 2
        self.assertEqual(stats["dedup_intra_run"], 3)  # 1 duplicate removed
        self.assertEqual(stats["new_increment"], 3)    # All new (no seen papers)

        # Verify output file
        saved = self._read_json(self.pm.skill1_search_results)
        self.assertEqual(len(saved["papers"]), 3)

        # Verify seen_papers updated
        seen = self._read_json(self.pm.seen_papers_json)
        self.assertEqual(len(seen), 3)

    @patch("source_scraper.time.sleep")
    def test_cross_run_filters_known_papers(self, mock_sleep):
        """Known papers from previous runs are filtered."""
        # Pre-populate seen_papers
        self._write_json(self.pm.seen_papers_json, {
            "2603.10001": {"source": "search", "first_seen_date": "2026-02-01"},
        })

        mock_searcher = self._make_mock_searcher([
            [make_raw_paper("2603.10001"), make_raw_paper("2603.10002")],
        ])

        profile = {
            "keywords": ["test"],
            "whitelist_authors": [],
            "arxiv_categories": ["cs.IR"],
            "search_days": 7,
        }

        output = run_source_scraper(
            profile=profile, pm=self.pm, searcher=mock_searcher
        )

        self.assertEqual(output["stats"]["new_increment"], 1)
        self.assertEqual(output["papers"][0]["arxiv_id"], "2603.10002")

    @patch("source_scraper.time.sleep")
    def test_seed_papers_filtered(self, mock_sleep):
        """Seed papers in search results are filtered."""
        self._write_json(self.pm.seed_papers_json, [
            {"arxiv_id": "2305.05065"},
        ])

        mock_searcher = self._make_mock_searcher([
            [make_raw_paper("2305.05065"), make_raw_paper("2603.10001")],
        ])

        profile = {
            "keywords": ["test"],
            "whitelist_authors": [],
            "arxiv_categories": ["cs.IR"],
            "search_days": 7,
        }

        output = run_source_scraper(
            profile=profile, pm=self.pm, searcher=mock_searcher
        )

        self.assertEqual(output["stats"]["new_increment"], 1)
        self.assertEqual(output["papers"][0]["arxiv_id"], "2603.10001")

    @patch("source_scraper.time.sleep")
    def test_author_search_included(self, mock_sleep):
        """Whitelist author search results are included."""
        mock_searcher = self._make_mock_searcher([
            # Keyword search
            [make_raw_paper("2603.10001")],
            # Author search
            [make_raw_paper("2603.10002")],
        ])

        profile = {
            "keywords": ["test"],
            "whitelist_authors": ["Author Name"],
            "arxiv_categories": ["cs.IR"],
            "search_days": 7,
        }

        output = run_source_scraper(
            profile=profile, pm=self.pm, searcher=mock_searcher
        )

        self.assertEqual(output["stats"]["total_raw"], 2)
        self.assertEqual(output["stats"]["new_increment"], 2)

    def test_empty_config(self):
        """No keywords or authors returns empty result."""
        profile = {
            "keywords": [],
            "whitelist_authors": [],
            "arxiv_categories": ["cs.IR"],
            "search_days": 7,
        }

        output = run_source_scraper(
            profile=profile, pm=self.pm, searcher=MagicMock()
        )

        self.assertEqual(output["stats"]["new_increment"], 0)
        self.assertEqual(output["papers"], [])


# ═══════════════════════════════════════════════════════════════════════════════
# Normalization Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalizePaper(ScraperTestBase):
    """Test paper normalization from ArxivSearcher format to pipeline format."""

    def test_normalize_full_paper(self):
        raw = make_raw_paper("2603.10001", "Test Paper")
        result = _normalize_paper(raw, "keyword:test")

        self.assertEqual(result["arxiv_id"], "2603.10001")
        self.assertEqual(result["title"], "Test Paper")
        self.assertEqual(result["source"], "keyword:test")
        self.assertIn("abstract", result)
        self.assertIn("url", result)

    def test_normalize_missing_fields(self):
        """Missing fields default to empty values."""
        raw = {"id": "2603.10001"}
        result = _normalize_paper(raw, "test")

        self.assertEqual(result["arxiv_id"], "2603.10001")
        self.assertEqual(result["title"], "")
        self.assertEqual(result["authors"], [])


# ═══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
