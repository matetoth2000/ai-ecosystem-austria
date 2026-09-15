"""Resolve and ground lexical signals from historical calibration packages."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


VISIBLE_CONTENT_STATUSES = frozenset({"complete", "partially_truncated"})


def normalize_grounding_text(value: object) -> str:
    """Normalize representation differences without applying semantic matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_reference_url(value: object) -> str:
    """Normalize a model-supplied URL enough to compare package references."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        if not hostname:
            return ""
        scheme = parsed.scheme.casefold() or "https"
        port = parsed.port
        netloc = hostname
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            netloc = f"{hostname}:{port}"
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((scheme, netloc, path, parsed.query, ""))
    except ValueError:
        return ""


def _integer_id(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not number.is_integer():
        return None
    return int(number)


def _page_identity(page: dict[str, Any] | None) -> tuple[object, object] | None:
    if page is None:
        return None
    return page.get("scraped_page_id"), page.get("extracted_page_id")


def _unique_page(pages: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    unique = {_page_identity(page): page for page in pages}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _literal_match(needle: object, haystack: object) -> bool:
    value = str(needle or "").strip()
    return bool(value and value.casefold() in str(haystack or "").casefold())


def _normalized_match(needle: object, haystack: object) -> bool:
    value = normalize_grounding_text(needle)
    return bool(value and value in normalize_grounding_text(haystack))


def ground_lexical_signal(
    signal: dict[str, Any],
    pages: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve a signal page and return auditable literal grounding fields.

    Historical packages exposed both scraped and extracted IDs while the model
    returned an ambiguous ``page_id``. An unambiguous URL is therefore the
    primary resolver and conflict signal. No semantic similarity is used.
    """
    page_list = list(pages)
    page_id = _integer_id(signal.get("page_id"))

    scraped_matches = [page for page in page_list if page_id is not None and _integer_id(page.get("scraped_page_id")) == page_id]
    extracted_matches = [page for page in page_list if page_id is not None and _integer_id(page.get("extracted_page_id")) == page_id]
    scraped_page = _unique_page(scraped_matches)
    extracted_page = _unique_page(extracted_matches)

    url_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in page_list:
        for field in ("url", "normalized_url"):
            normalized = normalize_reference_url(page.get(field))
            if normalized and page not in url_index[normalized]:
                url_index[normalized].append(page)
    supplied_url = normalize_reference_url(signal.get("url"))
    url_page = _unique_page(url_index.get(supplied_url, [])) if supplied_url else None

    scraped_identity = _page_identity(scraped_page)
    extracted_identity = _page_identity(extracted_page)
    if page_id is None:
        namespace = "missing"
    elif scraped_page and extracted_page and scraped_identity == extracted_identity:
        namespace = "both_same_page"
    elif scraped_page and extracted_page:
        namespace = "both_different_pages"
    elif scraped_page:
        namespace = "scraped_page_id"
    elif extracted_page:
        namespace = "extracted_page_id"
    else:
        namespace = "unmatched"

    # Scraped-first selection reproduces the historical notebook interpretation
    # for conflict reporting only. Final resolution gives a unique URL priority.
    id_selected_page = scraped_page or extracted_page
    page_id_url_conflict = bool(
        url_page
        and id_selected_page
        and _page_identity(url_page) != _page_identity(id_selected_page)
    )

    resolved_page: dict[str, Any] | None
    if url_page is not None:
        resolved_page = url_page
        if page_id_url_conflict:
            method = "url_conflict_resolution"
        elif id_selected_page is not None:
            method = "url_confirmed_page_id"
        else:
            method = "url_only"
        resolution_source = "url"
    elif scraped_page is not None and extracted_page is None:
        resolved_page = scraped_page
        method = "scraped_page_id_only"
        resolution_source = "scraped_page_id"
    elif extracted_page is not None and scraped_page is None:
        resolved_page = extracted_page
        method = "extracted_page_id_only"
        resolution_source = "extracted_page_id"
    elif scraped_page is not None and extracted_page is not None and scraped_identity == extracted_identity:
        resolved_page = scraped_page
        method = "page_id_both_namespaces_same_page"
        resolution_source = "page_id"
    else:
        resolved_page = None
        method = "unresolved"
        resolution_source = "unresolved"

    visible = bool(resolved_page and resolved_page.get("content_status") in VISIBLE_CONTENT_STATUSES)
    content = str(resolved_page.get("content") or "") if visible and resolved_page else ""
    phrase = signal.get("phrase")
    context = signal.get("short_context")
    phrase_strict = _literal_match(phrase, content)
    phrase_normalized = _normalized_match(phrase, content)
    context_strict = _literal_match(context, content)
    context_normalized = _normalized_match(context, content)

    if resolved_page is None:
        grounding_status = "unresolved_model_suggestion"
    elif phrase_strict:
        grounding_status = "strict_phrase_grounded"
    elif phrase_normalized:
        grounding_status = "normalized_phrase_grounded"
    else:
        grounding_status = "page_resolved_phrase_ungrounded"

    id_content = str(id_selected_page.get("content") or "") if id_selected_page else ""
    url_content = str(url_page.get("content") or "") if url_page else ""
    resolved_url = normalize_reference_url(resolved_page.get("url")) if resolved_page else ""
    return {
        "resolved_scraped_page_id": resolved_page.get("scraped_page_id") if resolved_page else None,
        "resolved_extracted_page_id": resolved_page.get("extracted_page_id") if resolved_page else None,
        "page_resolution_method": method,
        "page_resolution_source": resolution_source,
        "page_id_namespace": namespace,
        "page_id_namespace_ambiguous": namespace == "both_different_pages",
        "page_id_url_conflict": page_id_url_conflict,
        "url_matches_resolved_page": bool(supplied_url and resolved_url == supplied_url),
        "page_visible_to_model": visible,
        "phrase_grounded_strict": phrase_strict,
        "phrase_grounded_normalized": phrase_normalized,
        "context_grounded_strict": context_strict,
        "context_grounded_normalized": context_normalized,
        "grounding_status": grounding_status,
        "id_selected_scraped_page_id": id_selected_page.get("scraped_page_id") if id_selected_page else None,
        "url_selected_scraped_page_id": url_page.get("scraped_page_id") if url_page else None,
        "id_selected_phrase_grounded_strict": _literal_match(phrase, id_content),
        "id_selected_phrase_grounded_normalized": _normalized_match(phrase, id_content),
        "url_selected_phrase_grounded_strict": _literal_match(phrase, url_content),
        "url_selected_phrase_grounded_normalized": _normalized_match(phrase, url_content),
    }
