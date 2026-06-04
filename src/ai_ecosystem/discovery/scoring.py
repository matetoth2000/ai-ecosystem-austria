"""Candidate page scoring placeholders for future discovery."""

from __future__ import annotations


HOMEPAGE_PRIORITY = 8
AI_PRIORITY = 10
PRODUCTS_PRIORITY = 6
CAREERS_PRIORITY = 5
ABOUT_PRIORITY = 4
NEWS_PRIORITY = 3
NEGATIVE_PRIORITY = -100


PAGE_TYPE_PRIORITIES = {
    "homepage": HOMEPAGE_PRIORITY,
    "ai_technology": AI_PRIORITY,
    "ai": AI_PRIORITY,
    "products": PRODUCTS_PRIORITY,
    "careers": CAREERS_PRIORITY,
    "about": ABOUT_PRIORITY,
    "news": NEWS_PRIORITY,
    "negative": NEGATIVE_PRIORITY,
}


def score_candidate_page(page_type_guess: str | None, url_depth: int | None = None) -> int:
    """Return a preliminary priority score without fetching or crawling."""
    score = PAGE_TYPE_PRIORITIES.get(page_type_guess or "unknown", 0)
    if url_depth is not None and url_depth > 3 and score > 0:
        score -= 1
    return score
