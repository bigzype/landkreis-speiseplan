"""Bounded, dependency-free menu discovery shared by updater and source monitor.

Public API: menu_identity(url) -> (ISO year, ISO week) | None;
menu_candidates(page, base_url) -> deduplicated same-origin URLs;
discover_menu_url(page, base_url, expected=None) -> exactly one week's URL.
``expected`` is a date; omitted means today's date in Europe/Berlin.
Invalid/oversized input, absent requested weeks and ambiguity raise ValueError.
Foreign-origin links are excluded, never returned. URLs are resolved from actual
HTML hrefs; query strings/fragments are preserved, not guessed or synthesized.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

MAX_PAGE_CHARS = 2_000_000
MAX_LINKS = 10_000
MAX_URL_CHARS = 4_096
_MENU_NAME = re.compile(r"Speise[ _-]?([0-9]{1,2})\.([0-9]{4})\.pdf", re.I)


def current_menu_date() -> date:
    return datetime.now(ZoneInfo("Europe/Berlin")).date()


def menu_identity(url: str) -> tuple[int, int] | None:
    """Read only the parsed, percent-decoded basename; validate ISO week/year."""
    if len(url) > MAX_URL_CHARS or any(ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    try:
        basename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        match = _MENU_NAME.fullmatch(basename)
        if not match:
            return None
        week, year = map(int, match.groups())
        date.fromisocalendar(year, week, 1)
        return year, week
    except ValueError:
        return None


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or "\\" in url or any(ord(c) < 32 or ord(c) == 127 for c in url)):
        raise ValueError("Ungültiger HTTP-Ursprung")
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    return parts.scheme, parts.hostname.lower(), port


class _MenuLinks(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.origin = _origin(base_url)
        self.links: dict[str, None] = {}
        self.link_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        hrefs = [value for name, value in attrs if name == "href"]
        if not hrefs:
            return
        self.link_count += 1
        if self.link_count > MAX_LINKS:
            raise ValueError("Zu viele Links auf der Quellseite")
        if len(hrefs) != 1:
            raise ValueError("Mehrdeutiges href-Attribut")
        href = hrefs[0]
        if href is None:
            return
        if len(href) > MAX_URL_CHARS:
            raise ValueError("Quell-Link zu lang")
        # Do not let urljoin silently strip controls or interpret backslashes.
        if "\\" in href or any(ord(c) < 32 or ord(c) == 127 for c in href):
            return
        try:
            url = urljoin(self.base_url, href)
            if _origin(url) != self.origin or menu_identity(url) is None:
                return
        except ValueError:
            return
        self.links[url] = None


def menu_candidates(page: str, base_url: str) -> list[str]:
    """All valid same-origin menu hrefs in document order, exact URLs deduped."""
    if len(page) > MAX_PAGE_CHARS or len(base_url) > MAX_URL_CHARS:
        raise ValueError("Quellseite oder Basis-URL zu lang")
    parser = _MenuLinks(base_url)
    parser.feed(page)
    parser.close()
    return list(parser.links)


def discover_menu_url(page: str, base_url: str, expected: date | None = None) -> str:
    """Select exactly the requested ISO week, independent of HTML link order."""
    iso = (expected if expected is not None else current_menu_date()).isocalendar()
    wanted = (iso.year, iso.week)
    matches = [url for url in menu_candidates(page, base_url) if menu_identity(url) == wanted]
    if not matches:
        raise ValueError(f"Kein Speiseplan-Link für {iso.year}-KW{iso.week:02d} gefunden")
    if len(matches) != 1:
        raise ValueError(f"Mehrere Speiseplan-Links für {iso.year}-KW{iso.week:02d}: {matches}")
    return matches[0]
