"""Deterministic focal-company/page relational features for attribution."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

import pandas as pd


LEGAL_FORM_TOKENS = frozenset(
    {
        "ag",
        "aktiengesellschaft",
        "co",
        "company",
        "eg",
        "eu",
        "gmbh",
        "group",
        "gruppe",
        "holding",
        "inc",
        "incorporated",
        "kg",
        "kgaa",
        "limited",
        "llc",
        "ltd",
        "mbh",
        "og",
        "society",
        "stiftung",
        "verein",
    }
)
GENERIC_NAME_TOKENS = frozenset(
    {
        "and",
        "austria",
        "der",
        "des",
        "die",
        "for",
        "fur",
        "mit",
        "of",
        "osterreich",
        "the",
        "und",
        "von",
    }
)
DOMAIN_SUFFIX_TOKENS = frozenset({"at", "com", "de", "eu", "net", "org", "co"})


def normalize_lexical(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    ascii_text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def lexical_tokens(value: Any) -> tuple[str, ...]:
    return tuple(normalize_lexical(value).split())


def distinctive_company_tokens(company_name: Any) -> tuple[str, ...]:
    result: list[str] = []
    for token in lexical_tokens(company_name):
        if len(token) < 3 or token in LEGAL_FORM_TOKENS or token in GENERIC_NAME_TOKENS:
            continue
        if token not in result:
            result.append(token)
    return tuple(result)


def domain_tokens(domain: Any) -> tuple[str, ...]:
    host = str(domain or "").strip().casefold().removeprefix("www.").split(":", 1)[0]
    result: list[str] = []
    for label in host.split("."):
        for token in lexical_tokens(label):
            if token in DOMAIN_SUFFIX_TOKENS or len(token) < 2:
                continue
            if token not in result:
                result.append(token)
    return tuple(result)


def parse_headings(value: Any) -> str:
    if isinstance(value, list):
        headings = value
    else:
        try:
            headings = json.loads(str(value or "[]"))
        except (json.JSONDecodeError, TypeError):
            headings = []
    output = []
    for heading in headings if isinstance(headings, list) else []:
        output.append(str(heading.get("text", "")) if isinstance(heading, dict) else str(heading))
    return " ".join(output)


def _coverage(needles: Iterable[str], haystack: set[str]) -> float:
    values = tuple(needles)
    return sum(value in haystack for value in values) / len(values) if values else 0.0


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _exact_phrase(tokens: tuple[str, ...], normalized_field: str) -> int:
    phrase = " ".join(tokens)
    return int(bool(phrase) and bool(re.search(rf"(?:^| )({re.escape(phrase)})(?: |$)", normalized_field)))


def _host_relationship(page_url: Any, company_domain: Any) -> dict[str, int]:
    try:
        host = (urlsplit(str(page_url or "")).hostname or "").casefold().removeprefix("www.")
    except ValueError:
        host = ""
    domain = str(company_domain or "").strip().casefold().removeprefix("www.").split(":", 1)[0]
    suffix_match = bool(host and domain and (host == domain or host.endswith("." + domain)))
    subdomain_depth = max(0, len(host.split(".")) - len(domain.split("."))) if suffix_match else 0
    return {
        "relation__page_host_present": int(bool(host)),
        "relation__company_domain_present": int(bool(domain)),
        "relation__page_host_exact_company_domain": int(bool(host and host == domain)),
        "relation__page_host_subdomain_of_company_domain": int(bool(suffix_match and host != domain)),
        "relation__page_host_matches_company_domain": int(suffix_match),
        "relation__page_cross_host": int(bool(host and domain and not suffix_match)),
        "relation__page_subdomain_depth": subdomain_depth,
    }


def relational_feature_row(
    *,
    company_name: Any,
    company_domain: Any,
    page_url: Any,
    title: Any,
    headings: Any,
    markdown: Any,
) -> dict[str, float | int]:
    name = distinctive_company_tokens(company_name)
    domain = domain_tokens(company_domain)
    normalized_title = normalize_lexical(title)
    normalized_headings = normalize_lexical(parse_headings(headings))
    normalized_markdown = normalize_lexical(markdown)
    try:
        parsed = urlsplit(str(page_url or ""))
        normalized_path = normalize_lexical(unquote(parsed.path))
        normalized_host = normalize_lexical(parsed.hostname or "")
    except ValueError:
        normalized_path = ""
        normalized_host = ""

    title_tokens = set(normalized_title.split())
    heading_tokens = set(normalized_headings.split())
    body_tokens = set(normalized_markdown.split())
    path_tokens = set(normalized_path.split())
    host_tokens = set(normalized_host.split())
    all_visible_tokens = title_tokens | heading_tokens | body_tokens
    body_counts = Counter(normalized_markdown.split())
    name_set = set(name)
    domain_set = set(domain)

    row: dict[str, float | int] = {
        "relation__company_name_token_count": len(name),
        "relation__company_domain_token_count": len(domain),
        "relation__name_domain_token_jaccard": _jaccard(name_set, domain_set),
        "relation__name_coverage_title": _coverage(name, title_tokens),
        "relation__name_coverage_headings": _coverage(name, heading_tokens),
        "relation__name_coverage_body": _coverage(name, body_tokens),
        "relation__name_coverage_visible": _coverage(name, all_visible_tokens),
        "relation__name_coverage_url_path": _coverage(name, path_tokens),
        "relation__name_coverage_page_host": _coverage(name, host_tokens),
        "relation__domain_coverage_title": _coverage(domain, title_tokens),
        "relation__domain_coverage_headings": _coverage(domain, heading_tokens),
        "relation__domain_coverage_body": _coverage(domain, body_tokens),
        "relation__name_any_visible": int(bool(name_set & all_visible_tokens)),
        "relation__name_all_visible": int(bool(name) and name_set.issubset(all_visible_tokens)),
        "relation__name_exact_title": _exact_phrase(name, normalized_title),
        "relation__name_exact_headings": _exact_phrase(name, normalized_headings),
        "relation__name_exact_body": _exact_phrase(name, normalized_markdown),
        "relation__name_body_occurrences": sum(body_counts[value] for value in name),
        "relation__name_body_occurrences_per_1000_words": (
            1000.0 * sum(body_counts[value] for value in name) / max(1, sum(body_counts.values()))
        ),
        "relation__name_title_jaccard": _jaccard(name_set, title_tokens),
        "relation__name_headings_jaccard": _jaccard(name_set, heading_tokens),
        "relation__domain_title_jaccard": _jaccard(domain_set, title_tokens),
        "relation__domain_headings_jaccard": _jaccard(domain_set, heading_tokens),
    }
    row.update(_host_relationship(page_url, company_domain))
    return row


def build_relational_features(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "annotation_item_id",
        "company_id",
        "scraped_page_id",
        "company_name",
        "normalized_domain",
        "normalized_url",
        "title",
        "headings_json",
        "model_visible_markdown",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Missing relational feature inputs: {', '.join(missing)}")
    rows = []
    for source in frame.itertuples(index=False):
        values = relational_feature_row(
            company_name=source.company_name,
            company_domain=source.normalized_domain,
            page_url=source.normalized_url,
            title=source.title,
            headings=source.headings_json,
            markdown=source.model_visible_markdown,
        )
        rows.append(
            {
                "annotation_item_id": source.annotation_item_id,
                "company_id": source.company_id,
                "scraped_page_id": source.scraped_page_id,
                **values,
            }
        )
    return pd.DataFrame(rows)
