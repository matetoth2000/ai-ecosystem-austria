from __future__ import annotations

import unittest

from ai_ecosystem.discovery.classification import classify_page_type, is_hard_negative_url
from ai_ecosystem.discovery.scoring import AI_PRIORITY, score_candidate_page


class PageClassificationTest(unittest.TestCase):
    def test_ai_technology_matches_boundary_aware_english_and_german_urls(self) -> None:
        urls = [
            "https://example.at/ai",
            "https://example.at/ai/",
            "https://example.at/ai-solutions",
            "https://example.at/ai_platform",
            "https://example.at/products/ai",
            "https://example.at/ki",
            "https://example.at/ki/",
            "https://example.at/ki-loesungen",
            "https://example.at/ki-lösungen",
            "https://example.at/ki-beratung",
            "https://example.at/ki-software",
            "https://example.at/ki-automatisierung",
            "https://example.at/kuenstliche-intelligenz",
            "https://example.at/künstliche-intelligenz",
            "https://example.at/machine-learning",
            "https://example.at/ml-platform",
            "https://example.at/automation",
            "https://example.at/digitalisierung",
            "https://example.at/analytics",
            "https://example.at/software",
            "https://example.at/plattform",
            "https://example.at/platform",
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(classify_page_type(url), "ai_technology")

    def test_ai_technology_does_not_match_short_tokens_inside_words(self) -> None:
        urls = [
            "https://example.at/training",
            "https://example.at/main",
            "https://example.at/hair",
            "https://example.at/plain",
            "https://example.at/saiteninstrumente",
            "https://example.at/disclaimer",
            "https://example.at/available",
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertNotEqual(classify_page_type(url), "ai_technology")

    def test_business_page_variants_are_recognized(self) -> None:
        expected = {
            "https://example.at/about-us": "about",
            "https://example.at/about_us": "about",
            "https://example.at/aboutus": "about",
            "https://example.at/who-we-are": "about",
            "https://example.at/who_we_are": "about",
            "https://example.at/our-company": "about",
            "https://example.at/our_story": "about",
            "https://example.at/ueber-uns": "about",
            "https://example.at/uber-uns": "about",
            "https://example.at/über-uns": "about",
            "https://example.at/unternehmen": "about",
            "https://example.at/firma": "about",
            "https://example.at/our-services": "products",
            "https://example.at/service": "products",
            "https://example.at/services": "products",
            "https://example.at/solutions": "products",
            "https://example.at/loesungen": "products",
            "https://example.at/lösungen": "products",
            "https://example.at/produkte": "products",
            "https://example.at/product-overview": "products",
            "https://example.at/portfolio": "products",
            "https://example.at/career": "careers",
            "https://example.at/careers": "careers",
            "https://example.at/jobs": "careers",
            "https://example.at/join-us": "careers",
            "https://example.at/karriere": "careers",
            "https://example.at/stellenangebote": "careers",
            "https://example.at/newsroom": "news",
            "https://example.at/press-releases": "news",
            "https://example.at/presse": "news",
            "https://example.at/aktuelles": "news",
            "https://example.at/insights": "news",
            "https://example.at/blog": "news",
        }

        for url, page_type in expected.items():
            with self.subTest(url=url):
                self.assertEqual(classify_page_type(url), page_type)

    def test_hard_and_soft_negative_urls_are_separated(self) -> None:
        hard_negative_urls = [
            "https://example.at/cookie-policy",
            "https://example.at/cookies",
            "https://example.at/privacy-policy",
            "https://example.at/datenschutz",
            "https://example.at/login",
            "https://example.at/signin",
            "https://example.at/sign-in",
            "https://example.at/logout",
            "https://example.at/cart",
            "https://example.at/checkout",
            "https://example.at/agb",
            "https://example.at/terms",
            "https://example.at/terms-of-service",
            "https://example.at/de?modal=cookie-consent",
        ]
        soft_negative_urls = [
            "https://example.at/impressum",
            "https://example.at/kontakt",
            "https://example.at/contact",
            "https://example.at/legal",
        ]

        for url in hard_negative_urls:
            with self.subTest(url=url):
                self.assertTrue(is_hard_negative_url(url))

        for url in soft_negative_urls:
            with self.subTest(url=url):
                self.assertFalse(is_hard_negative_url(url))
                self.assertEqual(classify_page_type(url), "negative")

    def test_ai_technology_receives_ai_priority(self) -> None:
        self.assertEqual(score_candidate_page("ai_technology"), AI_PRIORITY)


if __name__ == "__main__":
    unittest.main()
