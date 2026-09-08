"""Dependency-free target-date and bounded actual-link discovery.

Filenames are hints only. ``discover_menu_url`` is a legacy filename-only
selector, NOT proof of freshness. Publication/monitoring use menu_fetch.probe_menu
which validates all meaningful linked PDFs and their actual periods/content.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

BASE_URL = "https://www.landkreis-restaurant.de/"
MAX_PAGE_CHARS = 2_000_000
MAX_LINKS = 10_000
MAX_URL_CHARS = 4_096
MAX_CANDIDATES = 4
_MENU_NAME = re.compile(r"Speise[ _-]?([0-9]{1,2})\.([0-9]{4})\.pdf", re.I)
_MENU_LABEL = re.compile(r"speise|wochen(?:plan|karte|men[uü])|mittag|men[uü]", re.I)
_NOT_MENU = re.compile(r"catering|fingerfood|schnittchen|buffet|frühstück|fruehstueck|suppenangebot", re.I)
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


def current_menu_date() -> date:
    return datetime.now(ZoneInfo("Europe/Berlin")).date()


def target_menu_date(now: date | datetime | None = None) -> date:
    """Sunday targets upcoming Monday; other days retain their date/ISO week.

    Aware datetimes are converted to Berlin; naive ones mean Berlin wall time.
    ``current_menu_date`` always remains the actual (unshifted) Berlin date.
    """
    if now is None:
        today = current_menu_date()
    elif isinstance(now, datetime):
        berlin = ZoneInfo("Europe/Berlin")
        today = (now.replace(tzinfo=berlin) if now.tzinfo is None else now.astimezone(berlin)).date()
    elif isinstance(now, date):
        today = now
    else:
        raise TypeError("now muss date, datetime oder None sein")
    return today + timedelta(days=1) if today.weekday() == 6 else today


def menu_identity(url: str) -> tuple[int, int] | None:
    """Read only the parsed, percent-decoded basename; validate ISO week/year."""
    if len(url) > MAX_URL_CHARS or any(ord(c) < 32 or ord(c) == 127 for c in unquote(url)):
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
    if (len(url) > MAX_URL_CHARS or parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or "\\" in url or any(ord(c) < 32 or ord(c) == 127 for c in unquote(url))):
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
        self.stack: list[tuple[str, str]] = []
        self.anchor: dict | None = None
        self.heading: list[str] | None = None
        self.catering_heading = False

    def _finish_anchor(self) -> None:
        if self.anchor is None:
            return
        anchor, self.anchor = self.anchor, None
        href = anchor['href']
        if href is None:
            return
        if len(href) > MAX_URL_CHARS:
            raise ValueError("Quell-Link zu lang")
        if "\\" in href or any(ord(c) < 32 or ord(c) == 127 for c in href):
            return
        try:
            url = urljoin(self.base_url, href)
            if _origin(url) != self.origin:
                return
            basename = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
        except ValueError:
            return
        label = ' '.join(anchor['text']) + ' ' + basename
        if not basename.lower().endswith('.pdf'):
            return
        # #speiseplan also contains a Catering link on the real website. Exclude
        # catering subheadings/ancestors as well as explicitly labelled offerings.
        if anchor['catering'] or _NOT_MENU.search(label):
            return
        if not anchor['menu'] and not _MENU_LABEL.search(label):
            return
        self.links[url] = None
        if len(self.links) > MAX_CANDIDATES:
            raise ValueError("Zu viele Speiseplan-Kandidaten (maximal 4)")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {'section', 'article'}:
            self.catering_heading = False
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.heading = []
        if tag == 'a':
            # HTML permits omitted </a> tags; finish the preceding anchor first.
            self._finish_anchor()
            hrefs = [value for name, value in attrs if name == 'href']
            if hrefs:
                self.link_count += 1
                if self.link_count > MAX_LINKS:
                    raise ValueError("Zu viele Links auf der Quellseite")
                if len(hrefs) != 1:
                    raise ValueError("Mehrdeutiges href-Attribut")
                ids = [identifier for _, identifier in self.stack]
                self.anchor = {'href': hrefs[0], 'text': [values.get('title') or '', values.get('aria-label') or ''],
                               'menu': 'speiseplan' in ids,
                               'catering': any(_NOT_MENU.search(i) for i in ids) or self.catering_heading}
        if tag == 'img' and self.anchor is not None:
            self.anchor['text'].append(values.get('alt') or '')
        if tag not in _VOID:
            self.stack.append((tag, (values.get('id') or '').lower()))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.anchor is not None:
            self.anchor['text'].append(data)
        if self.heading is not None:
            self.heading.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == 'a':
            self._finish_anchor()
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'} and self.heading is not None:
            text = ' '.join(self.heading)
            if _NOT_MENU.search(text):
                self.catering_heading = True
            elif _MENU_LABEL.search(text):
                self.catering_heading = False
            self.heading = None
        if tag in {'section', 'article'}:
            self._finish_anchor()
            self.catering_heading = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def close(self) -> None:
        super().close()
        self._finish_anchor()


def menu_candidates(page: str, base_url: str) -> list[str]:
    """Up to four same-origin actual PDF hrefs in menu scope/with menu labels.

    Exact URLs deduped; generic filenames work. No catering or unrelated PDFs.
    Exceeding the bound fails, never silently selects a subset.
    """
    if len(page) > MAX_PAGE_CHARS or len(base_url) > MAX_URL_CHARS:
        raise ValueError("Quellseite oder Basis-URL zu lang")
    parser = _MenuLinks(base_url)
    parser.feed(page)
    parser.close()
    return list(parser.links)


def discover_menu_url(page: str, base_url: str, expected: date | None = None) -> str:
    """Legacy filename-only selector; not used to establish PDF freshness."""
    iso = (expected if expected is not None else target_menu_date()).isocalendar()
    wanted = (iso.year, iso.week)
    matches = [url for url in menu_candidates(page, base_url) if menu_identity(url) == wanted]
    if not matches:
        raise ValueError(f"Kein Speiseplan-Link für {iso.year}-KW{iso.week:02d} gefunden")
    if len(matches) != 1:
        raise ValueError(f"Mehrere Speiseplan-Links für {iso.year}-KW{iso.week:02d}: {matches}")
    return matches[0]
