from __future__ import annotations

from pathlib import Path
import unittest

from ai_ecosystem.scoring.config import KnowledgeBaseConfig, REQUIRED_DIMENSIONS, ScoringProfileConfig
from ai_ecosystem.scoring.engine import ContentScorer, PageScoringInput


def make_profile(
    *,
    field_weights: dict[str, float] | None = None,
    fusion_weights: dict[str, float] | None = None,
) -> ScoringProfileConfig:
    return ScoringProfileConfig(
        scoring_profile_id="test_profile",
        scoring_profile_version="v_test",
        required_dimensions=REQUIRED_DIMENSIONS,
        knowledge_base_directory=Path("unused"),
        dimension_weights={dimension: 1.0 for dimension in REQUIRED_DIMENSIONS},
        field_weights=field_weights
        or {
            "title": 2.0,
            "headings": 1.5,
            "markdown": 1.0,
            "plain_text": 1.0,
            "url": 0.5,
            "internal_anchor_text": 0.5,
            "external_anchor_text": 0.25,
        },
        fusion_weights=fusion_weights
        or {"query": 1.0, "keyword": 1.0, "boost": 0.5, "negative": -1.0},
        raw_config={},
    )


def make_kb(dimension: str, node: dict) -> KnowledgeBaseConfig:
    return KnowledgeBaseConfig(
        knowledge_base_id=dimension,
        version="v1",
        dimension=dimension,
        path=Path(f"{dimension}_v1.yaml"),
        data={"version": "v1", dimension: {"node": node}},
    )


def make_knowledge_bases(**overrides: dict) -> dict[str, KnowledgeBaseConfig]:
    defaults = {
        "firm_profile": {
            "query_families": ["We describe products and services for customers."],
            "keywords": {"en": ["products", "services"], "de": ["dienstleistungen"]},
            "boost_terms": {"en": ["manufacturer"], "de": ["hersteller"]},
            "negative_terms": {"en": ["careers"], "de": ["karriere"]},
        },
        "technology": {
            "query_families": ["We develop machine learning software for industrial applications."],
            "keywords": {"en": ["machine learning", "software"], "de": ["künstliche intelligenz"]},
            "boost_terms": {"en": ["implementation"], "de": ["umsetzung"]},
            "negative_terms": {"en": ["webinar"], "de": ["presse"]},
        },
        "innovation": {
            "query_families": ["We run research and development projects."],
            "keywords": {"en": ["prototype"], "de": ["forschung"]},
            "boost_terms": {"en": ["pilot project"], "de": ["förderprojekt"]},
            "negative_terms": {"en": ["innovative"], "de": ["zukunft"]},
        },
        "ecosystem": {
            "query_families": ["We collaborate with universities and project partners."],
            "keywords": {"en": ["project partner"], "de": ["universität"]},
            "boost_terms": {"en": ["consortium"], "de": ["konsortium"]},
            "negative_terms": {"en": ["community"], "de": ["netzwerk"]},
        },
    }
    defaults.update(overrides)
    return {
        dimension: make_kb(dimension, node)
        for dimension, node in defaults.items()
    }


class ContentScoringEngineTest(unittest.TestCase):
    def test_query_family_scoring_and_explainability(self) -> None:
        scorer = ContentScorer(make_profile(), make_knowledge_bases())

        result = scorer.score_page(
            PageScoringInput(
                title="Industrial machine learning software",
                plain_text="We develop machine learning software for factories.",
            )
        )

        technology = result.dimensions["technology"]
        self.assertGreater(technology.query_score, 0)
        self.assertEqual(
            technology.top_queries[0].value,
            "We develop machine learning software for industrial applications.",
        )
        self.assertIn("machine", technology.top_queries[0].matched_terms)
        self.assertIn("plain_text", technology.top_queries[0].matched_fields)

    def test_query_family_scorer_documents_no_corpus_idf(self) -> None:
        self.assertIn("query-family lexical", ContentScorer.__doc__ or "")
        self.assertIn("no corpus-wide IDF", ContentScorer.__doc__ or "")

    def test_exact_multi_word_phrase_matching(self) -> None:
        scorer = ContentScorer(
            make_profile(),
            make_knowledge_bases(
                innovation={
                    "query_families": ["We run research and development projects."],
                    "keywords": {"en": ["prototype"], "de": []},
                    "boost_terms": {"en": ["pilot project"], "de": []},
                    "negative_terms": {"en": ["low content"], "de": []},
                }
            ),
        )

        phrase_result = scorer.score_page(
            PageScoringInput(plain_text="We provide machine learning systems and a pilot project.")
        )
        split_result = scorer.score_page(PageScoringInput(plain_text="We provide machine and learning systems."))
        negative_result = scorer.score_page(PageScoringInput(plain_text="This is a low content page."))

        phrase_keywords = [match.value for match in phrase_result.dimensions["technology"].top_keywords]
        split_keywords = [match.value for match in split_result.dimensions["technology"].top_keywords]
        self.assertIn("machine learning", phrase_keywords)
        self.assertNotIn("machine learning", split_keywords)
        self.assertIn(
            "pilot project",
            [match.value for match in phrase_result.dimensions["innovation"].top_boost_terms],
        )
        self.assertIn(
            "low content",
            [match.value for match in negative_result.dimensions["innovation"].top_negative_terms],
        )

    def test_single_word_and_multilingual_matching(self) -> None:
        scorer = ContentScorer(make_profile(), make_knowledge_bases())

        result = scorer.score_page(
            PageScoringInput(
                title="Künstliche Intelligenz",
                plain_text="Unsere Dienstleistungen umfassen Forschung und Umsetzung.",
            )
        )

        self.assertIn(
            "künstliche intelligenz",
            [match.value for match in result.dimensions["technology"].top_keywords],
        )
        self.assertIn(
            "dienstleistungen",
            [match.value for match in result.dimensions["firm_profile"].top_keywords],
        )
        self.assertIn(
            "umsetzung",
            [match.value for match in result.dimensions["technology"].top_boost_terms],
        )

    def test_field_weighting_changes_contribution(self) -> None:
        scorer = ContentScorer(
            make_profile(field_weights={"title": 5.0, "plain_text": 1.0}),
            make_knowledge_bases(),
        )

        title_result = scorer.score_page(PageScoringInput(title="software"))
        text_result = scorer.score_page(PageScoringInput(plain_text="software"))

        self.assertGreater(
            title_result.dimensions["technology"].keyword_score,
            text_result.dimensions["technology"].keyword_score,
        )

    def test_score_fusion_includes_negative_subtraction(self) -> None:
        scorer = ContentScorer(
            make_profile(
                field_weights={"plain_text": 1.0},
                fusion_weights={"query": 0.0, "keyword": 1.0, "boost": 2.0, "negative": -3.0},
            ),
            make_knowledge_bases(),
        )

        result = scorer.score_page(PageScoringInput(plain_text="software implementation webinar"))
        technology = result.dimensions["technology"]

        self.assertEqual(technology.keyword_score, 1.0)
        self.assertEqual(technology.boost_score, 1.0)
        self.assertEqual(technology.negative_score, 1.0)
        self.assertEqual(technology.final_score, 0.0)

    def test_dimensions_are_independent(self) -> None:
        scorer = ContentScorer(make_profile(), make_knowledge_bases())

        result = scorer.score_page(PageScoringInput(plain_text="products services manufacturer"))

        self.assertGreater(result.dimensions["firm_profile"].final_score, 0)
        self.assertEqual(result.dimensions["technology"].keyword_score, 0)
        self.assertEqual(result.dimensions["innovation"].keyword_score, 0)
        self.assertEqual(result.dimensions["ecosystem"].keyword_score, 0)

    def test_scoring_is_deterministic_and_serializable(self) -> None:
        scorer = ContentScorer(make_profile(), make_knowledge_bases())
        page = PageScoringInput(
            title="Machine Learning",
            headings=["Software implementation"],
            plain_text="Machine learning software implementation.",
            url="https://example.test/machine-learning",
        )

        first = scorer.score_page(page)
        second = scorer.score_page(page)

        self.assertEqual(first, second)
        serialized = first.to_dict()
        self.assertIn("technology", serialized["dimensions"])
        self.assertIn("top_keywords", serialized["dimensions"]["technology"])

    def test_legacy_text_and_anchor_weight_fallbacks(self) -> None:
        scorer = ContentScorer(
            make_profile(field_weights={"text": 2.0, "anchor_text": 3.0}),
            make_knowledge_bases(),
        )

        result = scorer.score_page(
            PageScoringInput(
                plain_text="software",
                internal_anchor_text=["software"],
                external_anchor_text=["software"],
            )
        )

        technology = result.dimensions["technology"]
        self.assertEqual(technology.keyword_score, 8.0)
        matched = technology.top_keywords[0].matched_fields
        self.assertEqual(matched["plain_text"], 2.0)
        self.assertEqual(matched["internal_anchor_text"], 3.0)
        self.assertEqual(matched["external_anchor_text"], 3.0)


if __name__ == "__main__":
    unittest.main()
