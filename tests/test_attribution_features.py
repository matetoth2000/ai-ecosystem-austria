from __future__ import annotations

import unittest

import pandas as pd

from ai_ecosystem.calibration.attribution_features import (
    build_relational_features,
    distinctive_company_tokens,
    domain_tokens,
    relational_feature_row,
)


class AttributionFeatureTest(unittest.TestCase):
    def test_distinctive_tokens_remove_legal_forms(self) -> None:
        self.assertEqual(
            distinctive_company_tokens("Müller Data Systems GmbH & Co KG"),
            ("muller", "data", "systems"),
        )
        self.assertEqual(domain_tokens("www.mueller-data.at"), ("mueller", "data"))

    def test_relational_features_compare_company_with_page(self) -> None:
        values = relational_feature_row(
            company_name="Müller Data Systems GmbH",
            company_domain="mueller-data.at",
            page_url="https://www.mueller-data.at/about/team?source=test",
            title="About Müller Data Systems",
            headings='[{"level": 1, "text": "Müller Data Systems"}]',
            markdown="Müller Data Systems develops software in Vienna.",
        )
        self.assertEqual(values["relation__page_host_exact_company_domain"], 1)
        self.assertEqual(values["relation__page_cross_host"], 0)
        self.assertEqual(values["relation__name_coverage_visible"], 1.0)
        self.assertEqual(values["relation__name_exact_title"], 1)
        self.assertGreater(values["relation__name_body_occurrences_per_1000_words"], 0)

    def test_cross_host_and_missing_name_are_explicit(self) -> None:
        values = relational_feature_row(
            company_name="Example GmbH",
            company_domain="example.at",
            page_url="https://partner.example.org/story",
            title="Partner story",
            headings=[],
            markdown="A third-party case study.",
        )
        self.assertEqual(values["relation__page_cross_host"], 1)
        self.assertEqual(values["relation__page_host_matches_company_domain"], 0)
        self.assertEqual(values["relation__name_any_visible"], 0)

    def test_dataframe_builder_preserves_identifiers_and_order(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "annotation_item_id": "C:2",
                    "company_id": "C",
                    "scraped_page_id": 2,
                    "company_name": "Example GmbH",
                    "normalized_domain": "example.at",
                    "normalized_url": "https://example.at/",
                    "title": "Example",
                    "headings_json": "[]",
                    "model_visible_markdown": "Example content",
                },
                {
                    "annotation_item_id": "D:1",
                    "company_id": "D",
                    "scraped_page_id": 1,
                    "company_name": "Different AG",
                    "normalized_domain": "different.at",
                    "normalized_url": "https://shop.different.at/item",
                    "title": "Item",
                    "headings_json": "[]",
                    "model_visible_markdown": "Product",
                },
            ]
        )
        result = build_relational_features(frame)
        self.assertEqual(result["annotation_item_id"].tolist(), ["C:2", "D:1"])
        self.assertTrue(all(column.startswith("relation__") for column in result.columns[3:]))


if __name__ == "__main__":
    unittest.main()
