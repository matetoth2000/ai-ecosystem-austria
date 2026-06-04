"""robots.txt helpers for URL discovery."""

from __future__ import annotations

from urllib.parse import urljoin


def extract_sitemap_urls_from_robots(robots_text: str, robots_url: str) -> list[str]:
    """Extract Sitemap references from robots.txt content."""
    sitemap_urls: list[str] = []
    seen: set[str] = set()
    for line in robots_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        if key.strip().lower() != "sitemap":
            continue
        sitemap_url = urljoin(robots_url, value.strip())
        if sitemap_url and sitemap_url not in seen:
            seen.add(sitemap_url)
            sitemap_urls.append(sitemap_url)
    return sitemap_urls
