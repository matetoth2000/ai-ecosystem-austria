"""Pure URL normalization and filtering helpers for discovery steps."""

from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


TRACKING_PARAMETER_PREFIXES = ("utm_",)
TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}
ASSET_FILE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".png",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
}
STATIC_PATH_SEGMENTS = {
    "asset",
    "assets",
    "css",
    "font",
    "fonts",
    "image",
    "images",
    "img",
    "js",
    "script",
    "scripts",
    "static",
}
STATIC_ASSET_FOLDER_SEGMENTS = {
    "media",
    "uploads",
    "upload",
    "wp-content",
    "wp-includes",
}


def remove_tracking_parameters(url: str) -> str:
    """Return URL with tracking and empty query parameters removed."""
    parsed = urlsplit(url.strip())
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if value != ""
        and key.lower() not in TRACKING_PARAMETERS
        and not key.lower().startswith(TRACKING_PARAMETER_PREFIXES)
    ]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query_items, doseq=True),
            parsed.fragment,
        )
    )


def normalize_url(url: str) -> str | None:
    """Normalize a candidate URL string without fetching it.

    Canonical discovery URLs use https, strip a leading www. host prefix, remove
    fragments, strip root/final slashes, and drop tracking or empty query params.
    Other subdomains are preserved.
    """
    if not url or not url.strip():
        return None

    cleaned = url.strip()
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    parsed = urlsplit(remove_tracking_parameters(cleaned))
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None

    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if not hostname:
        return None

    netloc = hostname.removeprefix("www.")
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/")
    else:
        path = ""

    return urlunsplit(
        (
            "https",
            netloc,
            path,
            parsed.query,
            "",
        )
    )


def extract_domain(url: str) -> str | None:
    """Extract a lowercase domain from a URL-like string."""
    normalized = normalize_url(url)
    if not normalized:
        return None
    hostname = urlsplit(normalized).hostname
    return hostname.removeprefix("www.") if hostname else None


def is_internal_url(url: str, domain: str) -> bool:
    """Return whether URL belongs to domain or one of its subdomains."""
    extracted = extract_domain(url)
    normalized_domain = domain.strip().lower().removeprefix("www.")
    if not extracted or not normalized_domain:
        return False
    return extracted == normalized_domain or extracted.endswith(f".{normalized_domain}")


def is_asset_or_static_url(url: str) -> bool:
    """Return whether URL points to low-value static assets, excluding PDFs."""
    if not url or not url.strip():
        return False

    parsed = urlsplit(url.strip())
    path = unquote(parsed.path or "").lower()
    if not path:
        return False

    path_segments = [segment for segment in path.split("/") if segment]
    filename = path_segments[-1] if path_segments else ""

    if path == "/cdn-cgi" or path.startswith("/cdn-cgi/"):
        return True

    if filename.startswith(("favicon", "apple-touch-icon")):
        return True

    if any(filename.endswith(extension) for extension in ASSET_FILE_EXTENSIONS):
        return True

    if any(segment in STATIC_PATH_SEGMENTS for segment in path_segments):
        return True

    if any(segment in STATIC_ASSET_FOLDER_SEGMENTS for segment in path_segments):
        return "." in filename and not filename.endswith(".pdf")

    return False
