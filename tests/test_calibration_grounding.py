from __future__ import annotations

import unittest

from ai_ecosystem.calibration.grounding import (
    ground_lexical_signal,
    normalize_grounding_text,
)


def page(
    scraped_page_id: int,
    extracted_page_id: int,
    url: str,
    content: str,
    *,
    status: str = "complete",
) -> dict:
    return {
        "scraped_page_id": scraped_page_id,
        "extracted_page_id": extracted_page_id,
        "url": url,
        "normalized_url": f"{url}/",
        "content_status": status,
        "content": content,
    }


def signal(page_id: object, url: str, phrase: str = "machine learning", context: str = "We use machine learning.") -> dict:
    return {"page_id": page_id, "url": url, "phrase": phrase, "short_context": context}


class CalibrationGroundingTest(unittest.TestCase):
    def test_url_confirms_extracted_page_id(self) -> None:
        pages = [page(100, 5, "https://example.test/ai", "We use machine learning.")]
        result = ground_lexical_signal(signal("5", "https://example.test/ai"), pages)
        self.assertEqual(result["resolved_scraped_page_id"], 100)
        self.assertEqual(result["page_id_namespace"], "extracted_page_id")
        self.assertEqual(result["page_resolution_method"], "url_confirmed_page_id")
        self.assertTrue(result["phrase_grounded_strict"])
        self.assertTrue(result["context_grounded_strict"])

    def test_url_resolves_id_url_conflict_and_preserves_provenance(self) -> None:
        pages = [
            page(5, 1, "https://example.test/wrong", "Conventional service."),
            page(100, 5, "https://example.test/ai", "We use machine learning."),
        ]
        result = ground_lexical_signal(signal("5", "https://example.test/ai"), pages)
        self.assertEqual(result["page_id_namespace"], "both_different_pages")
        self.assertTrue(result["page_id_namespace_ambiguous"])
        self.assertTrue(result["page_id_url_conflict"])
        self.assertEqual(result["page_resolution_method"], "url_conflict_resolution")
        self.assertEqual(result["resolved_scraped_page_id"], 100)
        self.assertFalse(result["id_selected_phrase_grounded_strict"])
        self.assertTrue(result["url_selected_phrase_grounded_strict"])

    def test_scraped_and_extracted_id_fallbacks(self) -> None:
        pages = [
            page(100, 5, "https://example.test/ai", "We use machine learning."),
            page(101, 6, "https://example.test/other", "Other page."),
        ]
        scraped = ground_lexical_signal(signal("100", "https://unknown.test"), pages)
        extracted = ground_lexical_signal(signal("6", "https://unknown.test", "Other page"), pages)
        self.assertEqual(scraped["page_resolution_source"], "scraped_page_id")
        self.assertEqual(extracted["page_resolution_source"], "extracted_page_id")
        self.assertEqual(extracted["resolved_scraped_page_id"], 101)

    def test_url_only_and_unresolved(self) -> None:
        pages = [page(100, 5, "https://example.test/ai", "We use machine learning.")]
        by_url = ground_lexical_signal(signal("unknown", "https://example.test/ai"), pages)
        missing = ground_lexical_signal(signal("unknown", "https://missing.test"), pages)
        self.assertEqual(by_url["page_resolution_method"], "url_only")
        self.assertEqual(missing["grounding_status"], "unresolved_model_suggestion")
        self.assertIsNone(missing["resolved_scraped_page_id"])

    def test_strict_and_normalized_grounding_remain_distinct(self) -> None:
        pages = [page(100, 5, "https://example.test/ai", "Energy-efficient\nclosed-loop AI systems")]
        result = ground_lexical_signal(
            signal("5", "https://example.test/ai", "energy efficient", "Energy efficient closed loop AI systems"),
            pages,
        )
        self.assertFalse(result["phrase_grounded_strict"])
        self.assertTrue(result["phrase_grounded_normalized"])
        self.assertFalse(result["context_grounded_strict"])
        self.assertTrue(result["context_grounded_normalized"])
        self.assertEqual(result["grounding_status"], "normalized_phrase_grounded")
        self.assertEqual(normalize_grounding_text("Energy‑efficient"), "energy efficient")

    def test_metadata_only_page_is_not_model_grounded(self) -> None:
        pages = [page(100, 5, "https://example.test/ai", "machine learning", status="metadata_only")]
        result = ground_lexical_signal(signal("5", "https://example.test/ai"), pages)
        self.assertFalse(result["page_visible_to_model"])
        self.assertFalse(result["phrase_grounded_normalized"])
        self.assertEqual(result["grounding_status"], "page_resolved_phrase_ungrounded")


if __name__ == "__main__":
    unittest.main()
