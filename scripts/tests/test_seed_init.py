"""
Unit tests for paper-seed-init (scripts/seed_init.py).

Tests cover:
- Metadata fetching via ArxivSearcher (mocked)
- seed_papers.json generation with proper fields
- seen_papers.json registration logic
- Incremental update (detect new IDs only)
- Force re-fetch mode
- User annotations preserved on update
- Mode B: manual JSON entries preserved
- Fetch failure handling (skeleton entries)
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

from seed_init import (
    build_existing_index,
    detect_new_ids,
    load_existing_seed_papers,
    load_seen_papers,
    merge_seed_papers,
    register_seed_ids_to_seen,
    run_seed_init,
    save_seed_papers,
    fetch_paper_metadata,
)
from common.path_manager import PathManager


# ═══════════════════════════════════════════════════════════════════════════════
# Test Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def make_mock_metadata(arxiv_id, title=None):
    """Create a mock metadata dict for a paper."""
    return {
        "arxiv_id": arxiv_id,
        "title": title or f"Paper {arxiv_id}",
        "authors": ["Author A", "Author B"],
        "abstract": f"Abstract for {arxiv_id}",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "published_date": "2023-05-08",
        "categories": ["cs.IR", "cs.AI"],
        "comments": "Accepted at NeurIPS 2023",
    }


def make_mock_seed_entry(arxiv_id, title=None, role="foundational", user_note=""):
    """Create a complete seed paper entry."""
    meta = make_mock_metadata(arxiv_id, title)
    meta.update({
        "user_note": user_note,
        "role": role,
        "sub_field": "generative_rec",
        "key_concepts": ["concept_a"],
        "has_card": False,
        "card_path": "",
    })
    return meta


class SeedInitTestBase(unittest.TestCase):
    """Base class with common setup."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pm = PathManager(root=self.tmpdir, run_id="test")

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
# Metadata Fetching Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFetchPaperMetadata(SeedInitTestBase):
    """Test ArxivSearcher metadata fetching (mocked)."""

    @patch("seed_init._import_arxiv_searcher")
    def test_successful_fetch(self, mock_import):
        """Single paper metadata fetch succeeds."""
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [{
            "title": "TIGER: Generative Retrieval",
            "authors": ["Shashank Rajput"],
            "summary": "A great paper about generative recommendation.",
            "arxiv_url": "https://arxiv.org/abs/2305.05065",
            "published": "2023-05-08",
            "categories": ["cs.IR"],
            "comments": "NeurIPS 2023",
        }]
        mock_import.return_value = MagicMock(return_value=mock_searcher)

        result = fetch_paper_metadata("2305.05065", searcher=mock_searcher)

        self.assertIsNotNone(result)
        self.assertEqual(result["arxiv_id"], "2305.05065")
        self.assertEqual(result["title"], "TIGER: Generative Retrieval")
        self.assertIn("Shashank Rajput", result["authors"])

    @patch("seed_init._import_arxiv_searcher")
    def test_fetch_no_results(self, mock_import):
        """Fetch returns None when no results found."""
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        mock_import.return_value = MagicMock(return_value=mock_searcher)

        result = fetch_paper_metadata("9999.99999", searcher=mock_searcher)
        self.assertIsNone(result)

    @patch("seed_init.time.sleep")  # Skip actual sleep in tests
    @patch("seed_init._import_arxiv_searcher")
    def test_fetch_retry_on_failure(self, mock_import, mock_sleep):
        """Fetch retries on exception and eventually fails."""
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = Exception("Rate limited")
        mock_import.return_value = MagicMock(return_value=mock_searcher)

        result = fetch_paper_metadata("2305.05065", searcher=mock_searcher, max_retries=2)
        self.assertIsNone(result)
        self.assertEqual(mock_searcher.search.call_count, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Seed Papers Management Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadExistingSeedPapers(SeedInitTestBase):
    """Test loading existing seed_papers.json."""

    def test_load_nonexistent(self):
        result = load_existing_seed_papers(Path(self.tmpdir) / "nonexistent.json")
        self.assertEqual(result, [])

    def test_load_valid(self):
        entries = [make_mock_seed_entry("2305.05065")]
        self._write_json(self.pm.seed_papers_json, entries)

        result = load_existing_seed_papers(self.pm.seed_papers_json)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["arxiv_id"], "2305.05065")

    def test_load_corrupted(self):
        with open(self.pm.seed_papers_json, "w") as f:
            f.write("not valid json{{{")

        result = load_existing_seed_papers(self.pm.seed_papers_json)
        self.assertEqual(result, [])


class TestBuildExistingIndex(SeedInitTestBase):
    """Test building index from existing seed papers."""

    def test_index_by_arxiv_id(self):
        entries = [
            make_mock_seed_entry("2305.05065"),
            make_mock_seed_entry("2502.18965"),
        ]
        index = build_existing_index(entries)
        self.assertIn("2305.05065", index)
        self.assertIn("2502.18965", index)
        self.assertEqual(len(index), 2)


class TestDetectNewIds(SeedInitTestBase):
    """Test incremental update detection."""

    def test_all_new(self):
        result = detect_new_ids(["2305.05065", "2502.18965"], {})
        self.assertEqual(result, ["2305.05065", "2502.18965"])

    def test_some_new(self):
        existing = {"2305.05065": make_mock_seed_entry("2305.05065")}
        result = detect_new_ids(["2305.05065", "2502.18965"], existing)
        self.assertEqual(result, ["2502.18965"])

    def test_none_new(self):
        existing = {
            "2305.05065": make_mock_seed_entry("2305.05065"),
            "2502.18965": make_mock_seed_entry("2502.18965"),
        }
        result = detect_new_ids(["2305.05065", "2502.18965"], existing)
        self.assertEqual(result, [])


class TestMergeSeedPapers(SeedInitTestBase):
    """Test merging existing and new seed papers."""

    def test_merge_all_new(self):
        """All papers are new."""
        new_meta = {
            "2305.05065": make_mock_metadata("2305.05065", "TIGER"),
            "2502.18965": make_mock_metadata("2502.18965", "OneRec"),
        }
        result = merge_seed_papers({}, new_meta, ["2305.05065", "2502.18965"])

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["arxiv_id"], "2305.05065")
        self.assertEqual(result[1]["arxiv_id"], "2502.18965")
        # Should have defaults applied
        self.assertEqual(result[0]["role"], "foundational")
        self.assertEqual(result[0]["user_note"], "")

    def test_merge_preserves_annotations(self):
        """Existing user annotations are preserved."""
        existing = {
            "2305.05065": make_mock_seed_entry(
                "2305.05065", "TIGER", role="foundational",
                user_note="This is the seminal TIGER paper"
            ),
        }
        new_meta = {
            "2305.05065": make_mock_metadata("2305.05065", "TIGER: Updated Title"),
        }
        result = merge_seed_papers(existing, new_meta, ["2305.05065"])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["user_note"], "This is the seminal TIGER paper")
        self.assertEqual(result[0]["role"], "foundational")

    def test_merge_preserves_mode_b_entries(self):
        """Mode B manual entries not in profile are preserved at the end."""
        existing = {
            "2305.05065": make_mock_seed_entry("2305.05065"),
            "custom_001": make_mock_seed_entry("custom_001", "Manual Paper"),
        }
        result = merge_seed_papers(existing, {}, ["2305.05065"])

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["arxiv_id"], "2305.05065")
        self.assertEqual(result[1]["arxiv_id"], "custom_001")

    def test_merge_with_fetch_failure(self):
        """Papers with failed fetch get skeleton entries."""
        result = merge_seed_papers({}, {}, ["2305.05065"])

        self.assertEqual(len(result), 1)
        self.assertIn("[Pending]", result[0]["title"])
        self.assertEqual(result[0]["arxiv_id"], "2305.05065")

    def test_merge_respects_profile_order(self):
        """Output order follows profile.yaml order."""
        new_meta = {
            "id_a": make_mock_metadata("id_a"),
            "id_b": make_mock_metadata("id_b"),
            "id_c": make_mock_metadata("id_c"),
        }
        result = merge_seed_papers({}, new_meta, ["id_c", "id_a", "id_b"])

        self.assertEqual(result[0]["arxiv_id"], "id_c")
        self.assertEqual(result[1]["arxiv_id"], "id_a")
        self.assertEqual(result[2]["arxiv_id"], "id_b")


# ═══════════════════════════════════════════════════════════════════════════════
# Seen Papers Registration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegisterSeedIdsToSeen(SeedInitTestBase):
    """Test seen_papers.json registration."""

    def test_register_new_ids(self):
        """New seed IDs are registered."""
        seed_papers = [
            make_mock_seed_entry("2305.05065"),
            make_mock_seed_entry("2502.18965"),
        ]
        count = register_seed_ids_to_seen(seed_papers, self.pm.seen_papers_json)

        self.assertEqual(count, 2)
        seen = self._read_json(self.pm.seen_papers_json)
        self.assertIn("2305.05065", seen)
        self.assertIn("2502.18965", seen)
        self.assertEqual(seen["2305.05065"]["source"], "seed")

    def test_no_duplicate_registration(self):
        """Already-seen IDs are not re-registered."""
        # Pre-populate seen
        self._write_json(self.pm.seen_papers_json, {
            "2305.05065": {
                "source": "seed",
                "first_seen_date": "2025-01-01",
                "first_seen_run_id": "old_run",
            }
        })

        seed_papers = [
            make_mock_seed_entry("2305.05065"),
            make_mock_seed_entry("2502.18965"),
        ]
        count = register_seed_ids_to_seen(seed_papers, self.pm.seen_papers_json)

        self.assertEqual(count, 1)  # Only 2502.18965 is new
        seen = self._read_json(self.pm.seen_papers_json)
        # Original entry preserved
        self.assertEqual(seen["2305.05065"]["first_seen_date"], "2025-01-01")

    def test_register_with_corrupted_seen(self):
        """Corrupted seen_papers.json starts fresh."""
        with open(self.pm.seen_papers_json, "w") as f:
            f.write("corrupted{{{")

        seed_papers = [make_mock_seed_entry("2305.05065")]
        count = register_seed_ids_to_seen(seed_papers, self.pm.seen_papers_json)

        self.assertEqual(count, 1)
        seen = self._read_json(self.pm.seen_papers_json)
        self.assertIn("2305.05065", seen)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test: run_seed_init
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunSeedInit(SeedInitTestBase):
    """Integration test for the full run_seed_init flow."""

    @patch("seed_init.fetch_papers_batch")
    def test_full_init_flow(self, mock_fetch):
        """Full initialization from scratch."""
        mock_fetch.return_value = {
            "2305.05065": make_mock_metadata("2305.05065", "TIGER"),
            "2502.18965": make_mock_metadata("2502.18965", "OneRec"),
        }

        profile = {
            "seed_papers": ["2305.05065", "2502.18965"],
        }

        summary = run_seed_init(profile=profile, pm=self.pm)

        self.assertEqual(summary["seed_papers_count"], 2)
        self.assertEqual(summary["new_fetched"], 2)
        self.assertEqual(summary["fetch_failed"], 0)

        # Verify seed_papers.json
        seed = self._read_json(self.pm.seed_papers_json)
        self.assertEqual(len(seed), 2)
        self.assertEqual(seed[0]["title"], "TIGER")

        # Verify seen_papers.json
        seen = self._read_json(self.pm.seen_papers_json)
        self.assertIn("2305.05065", seen)
        self.assertIn("2502.18965", seen)

    @patch("seed_init.fetch_papers_batch")
    def test_incremental_update(self, mock_fetch):
        """Incremental update only fetches new papers."""
        # Pre-populate with one paper
        existing = [make_mock_seed_entry("2305.05065", "TIGER", user_note="My note")]
        self._write_json(self.pm.seed_papers_json, existing)

        # Only fetch the new one
        mock_fetch.return_value = {
            "2502.18965": make_mock_metadata("2502.18965", "OneRec"),
        }

        profile = {
            "seed_papers": ["2305.05065", "2502.18965"],
        }

        summary = run_seed_init(profile=profile, pm=self.pm)

        self.assertEqual(summary["seed_papers_count"], 2)
        self.assertEqual(summary["new_fetched"], 1)

        # Verify annotations preserved
        seed = self._read_json(self.pm.seed_papers_json)
        tiger = next(p for p in seed if p["arxiv_id"] == "2305.05065")
        self.assertEqual(tiger["user_note"], "My note")

        # Verify mock_fetch was called with only the new ID
        mock_fetch.assert_called_once_with(["2502.18965"])

    @patch("seed_init.fetch_papers_batch")
    def test_force_refetch(self, mock_fetch):
        """Force mode re-fetches all papers."""
        existing = [make_mock_seed_entry("2305.05065")]
        self._write_json(self.pm.seed_papers_json, existing)

        mock_fetch.return_value = {
            "2305.05065": make_mock_metadata("2305.05065", "TIGER Updated"),
        }

        profile = {
            "seed_papers": ["2305.05065"],
        }

        summary = run_seed_init(profile=profile, pm=self.pm, force=True)

        self.assertEqual(summary["new_fetched"], 1)
        # Verify mock was called with the existing ID
        mock_fetch.assert_called_once_with(["2305.05065"])

    @patch("seed_init.fetch_papers_batch")
    def test_fetch_failure_creates_skeleton(self, mock_fetch):
        """When fetch fails, skeleton entries are created."""
        mock_fetch.return_value = {}  # All fetches failed

        profile = {
            "seed_papers": ["2305.05065"],
        }

        summary = run_seed_init(profile=profile, pm=self.pm)

        self.assertEqual(summary["seed_papers_count"], 1)
        self.assertEqual(summary["fetch_failed"], 1)

        # Verify skeleton entry
        seed = self._read_json(self.pm.seed_papers_json)
        self.assertIn("[Pending]", seed[0]["title"])


# ═══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
