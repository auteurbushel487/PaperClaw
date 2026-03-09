#!/usr/bin/env python3
"""Unit tests for card_parser.py — Knowledge Card Markdown Parser."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Standard path injection
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

from card_parser import (
    NA,
    extract_baselines,
    extract_bold_value,
    extract_id_paradigm,
    extract_inspiration_ideas,
    extract_item_tokenizer,
    extract_list_items,
    extract_sections,
    extract_sub_field,
    extract_title,
    extract_transferable_techniques,
    parse_card,
    run_deep_parse,
)
from common.path_manager import PathManager


# ═══════════════════════════════════════════════════════════════════════════════
# Test Fixtures — Realistic card.md Content
# ═══════════════════════════════════════════════════════════════════════════════

SAMPLE_CARD_TIGER = """# TIGER: Towards Generating Recommendations via Semantic IDs

## Metadata

**Sub_field**: generative_rec
**Authors**: Shashank Rajput, Nikhil Mehta, Anima Singh
**Published**: 2023

## Core Method

This paper proposes TIGER, a generative recommendation framework that uses
Semantic IDs derived from RQ-VAE to represent items. The model is trained to
generate item IDs autoregressively.

**ID Paradigm**: Semantic ID via RQ-VAE
**Item Tokenizer**: RQ-VAE

## Baselines

- SASRec
- BERT4Rec
- BPR
- GRU4Rec

## Transferable Techniques

- Semantic ID generation via RQ-VAE codebook learning
- Autoregressive generation for recommendation
- Multi-task training with auxiliary losses

## Inspiration Ideas

- Combine semantic ID with large language models for cross-domain rec
- Use contrastive learning to improve ID codebook quality
- Extend to multi-modal item representation
"""

SAMPLE_CARD_MINIMAL = """# Some Paper Title

This is a paper with minimal structure. No clear sections.
"""

SAMPLE_CARD_NUMBERED = """# Paper with Numbered Lists

## Baselines

1. Method A
2. Method B
3. Method C

## Contributions

1. Novel architecture for X
2. Improved training strategy Y
"""


class TestExtractSections(unittest.TestCase):
    """Tests for extract_sections function."""

    def test_basic_sections(self):
        sections = extract_sections(SAMPLE_CARD_TIGER)
        self.assertIn("metadata", sections)
        self.assertIn("core method", sections)
        self.assertIn("baselines", sections)
        self.assertIn("transferable techniques", sections)
        self.assertIn("inspiration ideas", sections)

    def test_empty_input(self):
        sections = extract_sections("")
        self.assertEqual(sections, {})

    def test_no_headings(self):
        sections = extract_sections("Just plain text\nwith multiple lines")
        self.assertEqual(sections, {})

    def test_heading_levels(self):
        text = "# H1\nbody1\n## H2\nbody2\n### H3\nbody3"
        sections = extract_sections(text)
        self.assertIn("h2", sections)
        self.assertIn("h3", sections)


class TestExtractListItems(unittest.TestCase):
    """Tests for extract_list_items function."""

    def test_dash_bullets(self):
        text = "- Item A\n- Item B\n- Item C"
        items = extract_list_items(text)
        self.assertEqual(items, ["Item A", "Item B", "Item C"])

    def test_asterisk_bullets(self):
        text = "* Foo\n* Bar"
        items = extract_list_items(text)
        self.assertEqual(items, ["Foo", "Bar"])

    def test_numbered_list(self):
        text = "1. First\n2. Second\n3. Third"
        items = extract_list_items(text)
        self.assertEqual(items, ["First", "Second", "Third"])

    def test_mixed_content(self):
        text = "Some text\n- Item 1\nMore text\n- Item 2"
        items = extract_list_items(text)
        self.assertEqual(items, ["Item 1", "Item 2"])

    def test_empty_input(self):
        items = extract_list_items("")
        self.assertEqual(items, [])


class TestExtractBoldValue(unittest.TestCase):
    """Tests for extract_bold_value function."""

    def test_basic_extraction(self):
        text = "**Field**: some value here"
        self.assertEqual(extract_bold_value(text, "Field"), "some value here")

    def test_case_insensitive(self):
        text = "**Sub_Field**: generative_rec"
        self.assertEqual(extract_bold_value(text, "sub_field"), "generative_rec")

    def test_chinese_colon(self):
        text = "**Field**\uff1a some value"
        self.assertEqual(extract_bold_value(text, "Field"), "some value")

    def test_not_found(self):
        text = "Some text without bold keys"
        self.assertIsNone(extract_bold_value(text, "Missing"))


class TestFieldExtractors(unittest.TestCase):
    """Tests for individual field extractors using TIGER card."""

    def setUp(self):
        self.sections = extract_sections(SAMPLE_CARD_TIGER)

    def test_sub_field(self):
        result = extract_sub_field(self.sections, SAMPLE_CARD_TIGER)
        self.assertEqual(result, "generative_rec")

    def test_id_paradigm(self):
        result = extract_id_paradigm(self.sections, SAMPLE_CARD_TIGER)
        self.assertIn("Semantic ID", result)

    def test_item_tokenizer(self):
        result = extract_item_tokenizer(self.sections, SAMPLE_CARD_TIGER)
        self.assertEqual(result, "RQ-VAE")

    def test_baselines(self):
        result = extract_baselines(self.sections, SAMPLE_CARD_TIGER)
        self.assertIn("SASRec", result)
        self.assertIn("BERT4Rec", result)
        self.assertIn("BPR", result)
        self.assertIn("GRU4Rec", result)

    def test_transferable_techniques(self):
        result = extract_transferable_techniques(self.sections, SAMPLE_CARD_TIGER)
        self.assertTrue(len(result) >= 2)
        self.assertTrue(any("RQ-VAE" in t for t in result))

    def test_inspiration_ideas(self):
        result = extract_inspiration_ideas(self.sections, SAMPLE_CARD_TIGER)
        self.assertTrue(len(result) >= 2)

    def test_title(self):
        result = extract_title(self.sections, SAMPLE_CARD_TIGER)
        self.assertIn("TIGER", result)


class TestFieldExtractorsNADegradation(unittest.TestCase):
    """Tests that fields degrade to N/A when not found."""

    def setUp(self):
        self.sections = extract_sections(SAMPLE_CARD_MINIMAL)

    def test_sub_field_na(self):
        self.assertEqual(extract_sub_field(self.sections, SAMPLE_CARD_MINIMAL), NA)

    def test_id_paradigm_na(self):
        self.assertEqual(extract_id_paradigm(self.sections, SAMPLE_CARD_MINIMAL), NA)

    def test_item_tokenizer_na(self):
        self.assertEqual(extract_item_tokenizer(self.sections, SAMPLE_CARD_MINIMAL), NA)

    def test_baselines_na(self):
        self.assertEqual(extract_baselines(self.sections, SAMPLE_CARD_MINIMAL), [NA])

    def test_transferable_techniques_na(self):
        self.assertEqual(extract_transferable_techniques(self.sections, SAMPLE_CARD_MINIMAL), [NA])

    def test_inspiration_ideas_na(self):
        self.assertEqual(extract_inspiration_ideas(self.sections, SAMPLE_CARD_MINIMAL), [NA])


class TestNumberedListExtraction(unittest.TestCase):
    """Tests extraction from numbered lists."""

    def setUp(self):
        self.sections = extract_sections(SAMPLE_CARD_NUMBERED)

    def test_numbered_baselines(self):
        result = extract_baselines(self.sections, SAMPLE_CARD_NUMBERED)
        self.assertIn("Method A", result)
        self.assertIn("Method B", result)
        self.assertIn("Method C", result)


class TestParseCard(unittest.TestCase):
    """Tests for the main parse_card function."""

    def test_parse_tiger_card(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(SAMPLE_CARD_TIGER)
            f.flush()
            result = parse_card(f.name, arxiv_id="2305.05065")

        os.unlink(f.name)

        self.assertTrue(result["parse_success"])
        self.assertEqual(result["arxiv_id"], "2305.05065")
        self.assertEqual(result["sub_field"], "generative_rec")
        self.assertIn("TIGER", result["title"])
        self.assertTrue(result["fields_extracted"] >= 4)

    def test_parse_minimal_card(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(SAMPLE_CARD_MINIMAL)
            f.flush()
            result = parse_card(f.name, arxiv_id="0000.00000")

        os.unlink(f.name)

        self.assertTrue(result["parse_success"])
        self.assertEqual(result["fields_extracted"], 0)

    def test_parse_nonexistent_file(self):
        result = parse_card("/nonexistent/path/card.md", arxiv_id="test")
        self.assertFalse(result["parse_success"])
        self.assertIn("not found", result.get("parse_error", "").lower())

    def test_parse_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("")
            f.flush()
            result = parse_card(f.name, arxiv_id="empty")

        os.unlink(f.name)

        self.assertFalse(result["parse_success"])

    def test_arxiv_id_from_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card_dir = Path(tmpdir) / "2305.05065_tiger"
            card_dir.mkdir()
            card_file = card_dir / "card.md"
            card_file.write_text(SAMPLE_CARD_TIGER)

            result = parse_card(str(card_file))
            self.assertEqual(result["arxiv_id"], "2305.05065")

    def test_output_json_structure(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(SAMPLE_CARD_TIGER)
            f.flush()
            result = parse_card(f.name, arxiv_id="2305.05065")

        os.unlink(f.name)

        # Verify all required fields are present
        required_fields = [
            "arxiv_id", "title", "sub_field", "ID_paradigm",
            "item_tokenizer", "baselines_compared",
            "transferable_techniques", "inspiration_ideas",
            "card_path", "parse_success", "fields_extracted", "fields_total",
        ]
        for field in required_fields:
            self.assertIn(field, result, f"Missing field: {field}")

        # Verify JSON serializable
        json.dumps(result)


class TestRunDeepParse(unittest.TestCase):
    """Tests for batch run_deep_parse function."""

    def test_no_final_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            stats = run_deep_parse(pm, {})
            self.assertEqual(stats["parsed_count"], 0)

    def test_batch_parse_with_papers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            # Create final selection
            papers = [
                {"arxiv_id": "2305.05065", "title": "TIGER"},
                {"arxiv_id": "2401.99999", "title": "Another Paper"},
            ]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            stats = run_deep_parse(pm, {})
            self.assertEqual(stats["parsed_count"], 2)
            # No card.md exists, so all should need reading
            self.assertEqual(stats.get("needs_reading", 0), 2)

    def test_batch_parse_with_existing_card(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(root=tmpdir, run_id="test_run")
            pm.create_run_directory()

            # Create a card.md in the expected location
            research_dir = Path(tmpdir) / "research_mock"

            # Create final selection
            papers = [{"arxiv_id": "2305.05065", "title": "TIGER"}]
            with open(pm.skill3_final_selection, "w") as f:
                json.dump(papers, f)

            # Patch _find_card_path to return a temp card
            card_file = Path(tmpdir) / "card.md"
            card_file.write_text(SAMPLE_CARD_TIGER)

            with patch("card_parser._find_card_path", return_value=card_file):
                stats = run_deep_parse(pm, {})

            self.assertEqual(stats["parsed_count"], 1)
            self.assertEqual(stats["success_count"], 1)
            self.assertEqual(stats.get("needs_reading", 0), 0)

            # Verify output file was created
            output = pm.skill4_parsed_paper("2305.05065")
            self.assertTrue(output.exists())
            with open(output) as f:
                data = json.load(f)
            self.assertTrue(data["parse_success"])
            self.assertIn("TIGER", data["title"])


if __name__ == "__main__":
    unittest.main()
