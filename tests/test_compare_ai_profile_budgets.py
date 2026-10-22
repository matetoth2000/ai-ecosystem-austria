"""Offline AI-profile budget comparison checks."""
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ai_ecosystem.calibration.compare_ai_profile_budgets import batch_cost, compare_company, quote_coverage


class BudgetComparisonTests(unittest.TestCase):
    def test_quote_coverage_requires_cited_page_and_surviving_text(self):
        package = {"pages": [{"page_id": "1", "text": "AI activity\nby the group.", "truncated": False},
                             {"page_id": "2", "text": "A truncated", "truncated": True}]}
        citations = [{"evidence_id": "a", "page_id": "1", "quote": "AI activity\nby the group."},
                     {"evidence_id": "b", "page_id": "1", "quote": "AI activity by the group."},
                     {"evidence_id": "c", "page_id": "2", "quote": "A truncated page"},
                     {"evidence_id": "d", "page_id": "3", "quote": "Elsewhere"}]
        self.assertEqual([r["status"] for r in quote_coverage(package, citations)],
                         ["PASS_EXACT", "QUOTE_LOST_IN_TRUNCATION", "QUOTE_LOST_IN_TRUNCATION", "PAGE_OMITTED"])

    def test_batch_cost_uses_per_request_long_context_boundary(self):
        config = {"pricing": {"input_per_million": .10, "output_per_million": .50,
                              "long_context_input_threshold": 272000,
                              "long_context_input_multiplier": 2,
                              "long_context_output_multiplier": 1.5}}
        self.assertEqual(batch_cost(100000, 8000, config), (.005, .002))
        self.assertEqual(batch_cost(300000, 8000, config), (.03, .003))

    def test_parallel_company_measurement_matches_sequential(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "page.txt").write_text("AI service for a company. " * 30, encoding="utf-8")
            config = {"website_evidence_token_cap": 250000,
                      "utility_path_components": [], "utility_exceptions": [],
                      "token_counting": {"allocation_encoding": "o200k_base"},
                      "max_output_tokens": 8000,
                      "pricing": {"input_per_million": .1, "output_per_million": .5,
                                  "long_context_input_threshold": 272000,
                                  "long_context_input_multiplier": 2,
                                  "long_context_output_multiplier": 1.5}}
            def args(number):
                cid = f"AT{number}"
                domain = f"site{number}.at"
                company = {"bvd_id": cid, "company_name": cid, "normalized_domain": domain}
                website = {"website_id": domain, "mapped_company_count": 1}
                pages = [{"website_id": domain, "usable": True, "scraped_page_id": number,
                          "url": f"https://{domain}/", "final_url": f"https://{domain}/",
                          "title": cid, "text_path": "page.txt", "content_id": "same"}]
                return cid, company, website, pages, root, config, 100, []
            expected = [compare_company(*args(number)) for number in (1, 2)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                actual = list(pool.map(lambda number: compare_company(*args(number)), (1, 2)))
            self.assertEqual(actual, expected)
            self.assertTrue(all(row["complete_evidence"] for result in actual for row in result[0]))


if __name__ == "__main__":
    unittest.main()
