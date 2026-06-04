"""Rule-based URL page classification for homepage discovery."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


URL_PATTERNS = {
    "ai_technology": (
        "artificial-intelligence",
        "automation",
        "analytics",
        "data-science",
        "digitalisierung",
        "kuenstliche-intelligenz",
        "künstliche-intelligenz",
        "machine-learning",
        "maschinenlernen",
        "platform",
        "plattform",
        "software",
    ),
    "products": (
        "products",
        "product",
        "services",
        "service",
        "solutions",
        "produkte",
        "leistungen",
        "angebot",
        "portfolio",
        "loesungen",
        "lösungen",
        "our-services",
        "product-overview",
    ),
    "careers": (
        "career",
        "careers",
        "jobs",
        "join-us",
        "karriere",
        "stellen",
        "stellenangebote",
    ),
    "about": (
        "about",
        "about-us",
        "about_us",
        "aboutus",
        "company",
        "corporate",
        "firma",
        "group",
        "konzern",
        "our-company",
        "our-story",
        "team",
        "uber-uns",
        "ueber-uns",
        "über-uns",
        "unternehmen",
        "who-we-are",
    ),
    "news": (
        "news",
        "newsroom",
        "blog",
        "press",
        "press-releases",
        "presse",
        "research",
        "insights",
        "aktuelles",
    ),
    "hard_negative": (
        "agb",
        "cart",
        "checkout",
        "cookie",
        "cookies",
        "datenschutz",
        "login",
        "logout",
        "privacy",
        "sign-in",
        "signin",
        "terms",
        "terms-of-service",
    ),
    "negative": (
        "contact",
        "disclaimer",
        "impressum",
        "kontakt",
        "legal",
    ),
}

AI_TOKEN_PATTERNS = {"ai", "ki", "ml"}
PATH_BOUNDARY_PATTERN = re.compile(r"[/._-]+")


def _url_path(url: str) -> str:
    return unquote(urlsplit(url).path).lower().strip("/")


def _url_path_and_query(url: str) -> str:
    parsed = urlsplit(url)
    return unquote(f"{parsed.path}?{parsed.query}").lower().strip("/?")


def _path_tokens(path: str) -> list[str]:
    return [token for token in re.split(r"[/._?&=-]+", path) if token]


def _canonical_slug(value: str) -> str:
    return re.sub(r"[/._?&=-]+", "-", value.lower()).strip("-")


def _compact_slug(value: str) -> str:
    return re.sub(r"[/._?&=-]+", "", value.lower())


def _matches_short_ai_token(path: str) -> bool:
    tokens = set(_path_tokens(path))
    return bool(tokens & AI_TOKEN_PATTERNS)


def _matches_pattern(path: str, pattern: str) -> bool:
    tokens = _path_tokens(path)
    token_set = set(tokens)
    canonical_path = _canonical_slug(path)
    compact_path = _compact_slug(path)
    canonical_pattern = _canonical_slug(pattern)
    compact_pattern = _compact_slug(pattern)

    if canonical_pattern in token_set:
        return True

    if "-" in canonical_pattern:
        return canonical_pattern in canonical_path

    if compact_pattern in {"aboutus"}:
        return compact_pattern in compact_path

    return any(token.startswith(canonical_pattern) for token in tokens)


def classify_page_type(url: str) -> str:
    """Classify URL path using bilingual, token-aware pattern matching."""
    path = _url_path(url)
    if not path:
        return "homepage"

    if any(_matches_pattern(path, pattern) for pattern in URL_PATTERNS["negative"]):
        return "negative"

    if _matches_short_ai_token(path) or any(
        _matches_pattern(path, pattern) for pattern in URL_PATTERNS["ai_technology"]
    ):
        return "ai_technology"

    for page_type in ("products", "careers", "about", "news"):
        if any(_matches_pattern(path, pattern) for pattern in URL_PATTERNS[page_type]):
            return page_type

    return "unknown"


def is_hard_negative_url(url: str) -> bool:
    """Return whether URL should be discarded before candidate storage."""
    url_signal = _url_path_and_query(url)
    if not url_signal:
        return False
    return any(_matches_pattern(url_signal, pattern) for pattern in URL_PATTERNS["hard_negative"])
