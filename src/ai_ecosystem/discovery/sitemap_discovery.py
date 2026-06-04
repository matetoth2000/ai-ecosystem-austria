"""Sitemap XML parsing helpers for URL discovery."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParsedSitemap:
    """URLs and child sitemap references parsed from one sitemap document."""

    urls: list[str] = field(default_factory=list)
    sitemap_urls: list[str] = field(default_factory=list)
    is_valid_xml: bool = True


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, child_name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == child_name and child.text:
            return child.text.strip()
    return None


def parse_sitemap_xml(xml_text: str) -> ParsedSitemap:
    """Parse urlset or sitemapindex XML without fetching referenced pages."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return ParsedSitemap(is_valid_xml=False)

    root_name = _local_name(root.tag)
    if root_name == "urlset":
        urls = [
            loc
            for element in root
            if _local_name(element.tag) == "url"
            for loc in [_child_text(element, "loc")]
            if loc
        ]
        return ParsedSitemap(urls=urls)

    if root_name == "sitemapindex":
        sitemap_urls = [
            loc
            for element in root
            if _local_name(element.tag) == "sitemap"
            for loc in [_child_text(element, "loc")]
            if loc
        ]
        return ParsedSitemap(sitemap_urls=sitemap_urls)

    return ParsedSitemap(is_valid_xml=False)
