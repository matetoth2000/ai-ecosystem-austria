"""Database-free content scoring for one extracted page.

This module implements a deterministic weighted query-family lexical scorer. It
uses query normalization, stopword removal, field-weighted term matches,
frequency saturation, and configurable field weights. It intentionally does not
use corpus IDF, document frequency, document-length normalization, or corpus
indexing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Iterable, Sequence

from ai_ecosystem.scoring.config import KnowledgeBaseConfig, ScoringProfileConfig


FIELD_NAMES = (
    "title",
    "headings",
    "markdown",
    "plain_text",
    "url",
    "internal_anchor_text",
    "external_anchor_text",
)

STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "das",
    "der",
    "des",
    "die",
    "ein",
    "eine",
    "for",
    "from",
    "für",
    "im",
    "in",
    "is",
    "mit",
    "of",
    "on",
    "or",
    "our",
    "the",
    "to",
    "und",
    "von",
    "we",
    "wir",
}


@dataclass(frozen=True)
class PageScoringInput:
    plain_text: str = ""
    markdown: str = ""
    title: str = ""
    headings: str | Sequence[str] = ""
    url: str = ""
    internal_anchor_text: str | Sequence[str] = ""
    external_anchor_text: str | Sequence[str] = ""


@dataclass(frozen=True)
class MatchContribution:
    value: str
    score: float
    matched_fields: dict[str, float]
    matched_terms: tuple[str, ...] = ()
    source_node: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class DimensionScoringResult:
    dimension: str
    query_score: float
    keyword_score: float
    boost_score: float
    negative_score: float
    final_score: float
    top_queries: tuple[MatchContribution, ...]
    top_keywords: tuple[MatchContribution, ...]
    top_boost_terms: tuple[MatchContribution, ...]
    top_negative_terms: tuple[MatchContribution, ...]
    knowledge_base_id: str
    knowledge_base_version: str


@dataclass(frozen=True)
class ScoringResult:
    dimensions: dict[str, DimensionScoringResult]
    scoring_profile_id: str
    scoring_profile_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scoring_profile_id": self.scoring_profile_id,
            "scoring_profile_version": self.scoring_profile_version,
            "dimensions": {
                dimension: asdict(result)
                for dimension, result in self.dimensions.items()
            },
        }


@dataclass(frozen=True)
class _FieldText:
    raw: str
    normalized: str
    token_counts: Counter[str]


def normalize_text(value: str) -> str:
    normalized = value.casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized, flags=re.UNICODE).strip()


def tokenize(value: str) -> list[str]:
    normalized = normalize_text(value)
    if not normalized:
        return []
    return normalized.split()


def _join(value: str | Sequence[str]) -> str:
    if isinstance(value, str):
        return value
    return " ".join(str(item) for item in value)


def _unique_substantive_terms(value: str) -> tuple[str, ...]:
    seen: set[str] = set()
    terms: list[str] = []
    for token in tokenize(value):
        if token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return tuple(terms)


def _count_normalized_phrase(field_text: _FieldText, normalized_term: str) -> int:
    term_tokens = normalized_term.split()
    if not term_tokens:
        return 0
    if len(term_tokens) == 1:
        return field_text.token_counts[term_tokens[0]]
    return len(
        re.findall(
            rf"(?<!\w){re.escape(normalized_term)}(?!\w)",
            field_text.normalized,
            flags=re.UNICODE,
        )
    )


def _round(value: float) -> float:
    return round(value, 6)


class ContentScorer:
    """Score one extracted page against loaded knowledge bases.

    The query component is a deterministic weighted query-family lexical
    scorer. It uses query-term frequency saturation over configured page fields
    and no corpus-wide IDF or corpus statistics.
    """

    def __init__(
        self,
        profile: ScoringProfileConfig,
        knowledge_bases: dict[str, KnowledgeBaseConfig],
        *,
        top_n: int = 10,
    ) -> None:
        self.profile = profile
        self.knowledge_bases = knowledge_bases
        self.top_n = top_n
        self.field_weights = self._resolve_field_weights(profile.field_weights)

    def score_page(self, page: PageScoringInput) -> ScoringResult:
        fields = self._build_fields(page)
        results: dict[str, DimensionScoringResult] = {}
        for dimension in self.profile.required_dimensions:
            knowledge_base = self.knowledge_bases.get(dimension)
            if knowledge_base is None:
                continue
            results[dimension] = self._score_dimension(knowledge_base, fields)
        return ScoringResult(
            dimensions=results,
            scoring_profile_id=self.profile.scoring_profile_id,
            scoring_profile_version=self.profile.scoring_profile_version,
        )

    @staticmethod
    def _resolve_field_weights(field_weights: dict[str, float]) -> dict[str, float]:
        resolved: dict[str, float] = {}
        for field_name in FIELD_NAMES:
            if field_name in field_weights:
                resolved[field_name] = float(field_weights[field_name])
            elif field_name in {"markdown", "plain_text"} and "text" in field_weights:
                resolved[field_name] = float(field_weights["text"])
            elif field_name in {"internal_anchor_text", "external_anchor_text"} and "anchor_text" in field_weights:
                resolved[field_name] = float(field_weights["anchor_text"])
            else:
                resolved[field_name] = 1.0
        return resolved

    def _build_fields(self, page: PageScoringInput) -> dict[str, _FieldText]:
        values = {
            "title": page.title,
            "headings": _join(page.headings),
            "markdown": page.markdown,
            "plain_text": page.plain_text,
            "url": page.url,
            "internal_anchor_text": _join(page.internal_anchor_text),
            "external_anchor_text": _join(page.external_anchor_text),
        }
        return {
            field_name: _FieldText(
                raw=value,
                normalized=normalize_text(value),
                token_counts=Counter(tokenize(value)),
            )
            for field_name, value in values.items()
        }

    def _score_dimension(
        self,
        knowledge_base: KnowledgeBaseConfig,
        fields: dict[str, _FieldText],
    ) -> DimensionScoringResult:
        nodes = knowledge_base.data[knowledge_base.dimension]
        query_matches = self._score_queries(nodes, fields)
        keyword_matches = self._score_terms(nodes, fields, "keywords")
        boost_matches = self._score_terms(nodes, fields, "boost_terms")
        negative_matches = self._score_terms(nodes, fields, "negative_terms")

        query_score = _round(sum(match.score for match in query_matches))
        keyword_score = _round(sum(match.score for match in keyword_matches))
        boost_score = _round(sum(match.score for match in boost_matches))
        negative_score = _round(sum(match.score for match in negative_matches))
        fusion = self.profile.fusion_weights
        final_score = _round(
            query_score * fusion.get("query", 1.0)
            + keyword_score * fusion.get("keyword", 1.0)
            + boost_score * fusion.get("boost", 0.5)
            + negative_score * fusion.get("negative", -1.0)
        )

        return DimensionScoringResult(
            dimension=knowledge_base.dimension,
            query_score=query_score,
            keyword_score=keyword_score,
            boost_score=boost_score,
            negative_score=negative_score,
            final_score=final_score,
            top_queries=tuple(query_matches[: self.top_n]),
            top_keywords=tuple(keyword_matches[: self.top_n]),
            top_boost_terms=tuple(boost_matches[: self.top_n]),
            top_negative_terms=tuple(negative_matches[: self.top_n]),
            knowledge_base_id=knowledge_base.knowledge_base_id,
            knowledge_base_version=knowledge_base.version,
        )

    def _score_queries(
        self,
        nodes: dict[str, Any],
        fields: dict[str, _FieldText],
    ) -> list[MatchContribution]:
        matches: list[MatchContribution] = []
        for node_name, node in nodes.items():
            if not isinstance(node, dict):
                continue
            for query in node.get("query_families", []) or []:
                terms = _unique_substantive_terms(str(query))
                if not terms:
                    continue
                matched_fields: dict[str, float] = {}
                matched_terms: set[str] = set()
                score = 0.0
                for field_name, field_text in fields.items():
                    field_score = 0.0
                    for term in terms:
                        frequency = field_text.token_counts[term]
                        if frequency <= 0:
                            continue
                        matched_terms.add(term)
                        field_score += frequency / (frequency + 1.2)
                    if field_score > 0:
                        weighted = field_score * self.field_weights[field_name]
                        matched_fields[field_name] = _round(weighted)
                        score += weighted
                if score > 0:
                    matches.append(
                        MatchContribution(
                            value=str(query),
                            score=_round(score / math.sqrt(len(terms))),
                            matched_fields=matched_fields,
                            matched_terms=tuple(sorted(matched_terms)),
                            source_node=str(node_name),
                        )
                    )
        return sorted(matches, key=lambda match: (-match.score, match.value))

    def _score_terms(
        self,
        nodes: dict[str, Any],
        fields: dict[str, _FieldText],
        term_family: str,
    ) -> list[MatchContribution]:
        matches: list[MatchContribution] = []
        seen: set[tuple[str, str, str]] = set()
        for node_name, node in nodes.items():
            if not isinstance(node, dict):
                continue
            language_terms = node.get(term_family, {}) or {}
            if not isinstance(language_terms, dict):
                continue
            for language, terms in language_terms.items():
                for term in terms or []:
                    term_value = str(term)
                    normalized_term = normalize_text(term_value)
                    if not normalized_term:
                        continue
                    key = (str(language), normalized_term, str(node_name))
                    if key in seen:
                        continue
                    seen.add(key)
                    matched_fields: dict[str, float] = {}
                    score = 0.0
                    for field_name, field_text in fields.items():
                        frequency = _count_normalized_phrase(field_text, normalized_term)
                        if frequency <= 0:
                            continue
                        weighted = frequency * self.field_weights[field_name]
                        matched_fields[field_name] = _round(weighted)
                        score += weighted
                    if score > 0:
                        matches.append(
                            MatchContribution(
                                value=term_value,
                                score=_round(score),
                                matched_fields=matched_fields,
                                source_node=str(node_name),
                                language=str(language),
                            )
                        )
        return sorted(matches, key=lambda match: (-match.score, match.value.casefold()))
