"""Shared bounded network/PDF probe used by menu_probe and the real updater.

No output files are written. All candidates must parse completely before a result
is returned. ``waiting`` means valid PDFs exist, but none covers the target week.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
import time
from urllib.parse import unquote, urljoin, urlsplit

import requests

from menu_source import BASE_URL, _origin, menu_candidates, target_menu_date

MAX_HTML_BYTES = 2_000_000
MAX_PDF_BYTES = 8_000_000
MAX_REDIRECTS = 3
NETWORK_BUDGET_SECONDS = 75


class ProbeError(ValueError):
    """Stable machine reason plus separate human diagnostic."""
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


@dataclass
class ProbeResult:
    report: dict
    data: dict | None = None
    pdf_bytes: bytes | None = None


def _fetch(session: requests.Session, url: str, base_url: str, limit: int,
           deadline: float) -> tuple[bytes, str]:
    """Explicit same-origin redirects; bounded decoded bytes and socket waits."""
    allowed = _origin(base_url)
    for hop in range(MAX_REDIRECTS + 1):
        if _origin(url) != allowed:
            raise ProbeError('UNSAFE_REDIRECT', 'Weiterleitung verlässt erlaubten Ursprung')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError('NETWORK_TIMEOUT', 'Netzwerk-Zeitbudget überschritten')
        response = session.get(url, timeout=(min(5, remaining), min(10, remaining)),
                               allow_redirects=False, stream=True)
        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get('Location')
                if not location or hop == MAX_REDIRECTS:
                    raise ProbeError('REDIRECT_LIMIT', 'Fehlendes Redirect-Ziel oder zu viele Weiterleitungen')
                # Validate before urljoin (which can silently strip control chars).
                if '\\' in location or any(ord(c) < 32 or ord(c) == 127 for c in location):
                    raise ProbeError('UNSAFE_REDIRECT', 'Ungültiger Redirect-Ursprung')
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            if response.status_code != 200:
                raise ProbeError('HTTP_ERROR', f'Unerwarteter HTTP-Status {response.status_code}')
            size = response.headers.get('Content-Length')
            if size is not None and (not size.isdecimal() or int(size) > limit):
                raise ProbeError('SIZE_LIMIT', 'Ungültige oder zu große Content-Length')
            content = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                if time.monotonic() > deadline:
                    raise ProbeError('NETWORK_TIMEOUT', 'Netzwerk-Zeitbudget überschritten')
                if len(content) + len(chunk) > limit:
                    raise ProbeError('SIZE_LIMIT', 'Download überschreitet Größenlimit')
                content.extend(chunk)
            return bytes(content), url
        finally:
            response.close()
    raise ProbeError('REDIRECT_LIMIT', 'Zu viele Weiterleitungen')


def probe_menu(session: requests.Session, expected: date | None = None,
               base_url: str = BASE_URL) -> ProbeResult:
    """Validate all bounded linked PDFs, selecting by content, NEVER filename.

    Errors raise ProbeError; CLI converts them to JSON/nonzero. Multiple URLs
    claiming the same actual week are ambiguous even if bytes are identical.
    Distinct old/current/future weeks may coexist; all must be structurally valid.
    """
    # Lazy parser import avoids a module cycle; updater itself uses this function.
    from update_menu import parse_pdf

    expected = expected if expected is not None else target_menu_date()
    deadline = time.monotonic() + NETWORK_BUDGET_SECONDS
    try:
        page_bytes, page_url = _fetch(session, base_url, base_url, MAX_HTML_BYTES, deadline)
        try:
            candidates = menu_candidates(page_bytes.decode('utf-8-sig', errors='strict'), page_url)
        except (ValueError, UnicodeError) as exc:
            raise ProbeError('SOURCE_INVALID', str(exc)) from exc
        if not candidates:
            raise ProbeError('NO_MENU_LINK', 'Kein sicherer Speiseplan-PDF-Link gefunden')
        parsed = []
        for url in candidates:
            content, _ = _fetch(session, url, base_url, MAX_PDF_BYTES, deadline)
            try:
                # Parse period AND the complete menu even for outdated candidates.
                # Disabling only the expected-week guard is explicit/internal.
                data = parse_pdf(BytesIO(content), url, expected, verify_expected=False)
            except Exception as exc:
                raise ProbeError('PDF_INVALID', f'{url}: {exc}') from exc
            parsed.append((url, data, content))
    except ProbeError:
        raise
    except requests.Timeout as exc:
        raise ProbeError('NETWORK_TIMEOUT', str(exc)) from exc
    except requests.RequestException as exc:
        raise ProbeError('NETWORK_ERROR', str(exc)) from exc
    except ValueError as exc:
        raise ProbeError('SOURCE_INVALID', str(exc)) from exc

    weeks = [data['week'] for _, data, _ in parsed]
    if len(set(weeks)) != len(weeks):
        raise ProbeError('AMBIGUOUS_PDF_WEEK', 'Mehrere Speiseplan-Links für dieselbe tatsächliche PDF-Woche')
    iso = expected.isocalendar()
    wanted = f'{iso.year}-KW{iso.week:02d}'
    matches = [item for item in parsed if item[1]['week'] == wanted]
    report = {'status': 'current' if matches else 'waiting', 'expected_date': expected.isoformat()}
    selected = matches[0] if matches else (parsed[0] if len(parsed) == 1 else None)
    if selected:
        url, data, content = selected
        report.update(source_url=url, actual=unquote(urlsplit(url).path.rsplit('/', 1)[-1]), pdf_week=data['week'])
    if matches:
        return ProbeResult(report, matches[0][1], matches[0][2])
    report.update(reason='PDF_WEEK_MISMATCH', detail=f'PDF-Wochen {", ".join(sorted(weeks))}; erwartet {wanted}')
    return ProbeResult(report)
