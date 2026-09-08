"""Offline source discovery regressions (stdlib unittest)."""
from datetime import date, datetime, timezone
import unittest
from unittest.mock import Mock, patch

import menu_source as source

BASE = "https://www.landkreis-restaurant.de/"
EXPECTED = date(2026, 9, 8)
CURRENT = "/documents/279/Speise_37.2026.pdf"


class DiscoveryTests(unittest.TestCase):
    def discover(self, html, expected=EXPECTED):
        return source.discover_menu_url(html, BASE, expected)

    def test_live_underscore_regression(self):
        self.assertEqual(self.discover(f'<a href="{CURRENT}">Plan</a>'), BASE.rstrip("/") + CURRENT)

    def test_actual_href_variants_and_query_fragment_preserved(self):
        for name in ["Speise37.2026.pdf", "Speise_37.2026.pdf", "Speise 37.2026.pdf",
                     "Speise-37.2026.pdf", "SPEISE_37.2026.PDF", "Speise%2037.2026.pdf"]:
            href = "/documents/279/" + name + "?download=1&v=2#page=1"
            with self.subTest(name=name):
                html = '<A class="menu" HREF = "' + href.replace("&", "&amp;") + '">Plan</A>'
                self.assertEqual(self.discover(html), BASE.rstrip("/") + href)

    def test_unquoted_relative_and_duplicate_links(self):
        self.assertEqual(self.discover('<a href=Speise_37.2026.pdf><a href="./Speise_37.2026.pdf"/>'), BASE + "Speise_37.2026.pdf")

    def test_current_selected_even_if_old_or_future_first(self):
        html = '<a href="Speise36.2026.pdf"><a href="Speise38.2026.pdf">' + f'<a href="{CURRENT}">'
        self.assertEqual(self.discover(html), BASE.rstrip("/") + CURRENT)
        self.assertEqual(len(source.menu_candidates(html, BASE)), 3)

    def test_no_fallback_to_wrong_week(self):
        for html in ['', '<a href="Speise36.2026.pdf">', '<a href="Speise38.2026.pdf">',
                     '<!-- <a href="Speise37.2026.pdf"> -->', '<script>"<a href=Speise37.2026.pdf>"</script>']:
            with self.subTest(html=html), self.assertRaises(ValueError):
                self.discover(html)

    def test_ambiguity_including_different_query_or_fragment(self):
        for other in ['/another/Speise_37.2026.pdf', CURRENT + '?v=2', CURRENT + '#page=1']:
            with self.subTest(other=other), self.assertRaisesRegex(ValueError, "Mehrere"):
                self.discover(f'<a href="{CURRENT}"><a href="{other}">')

    def test_foreign_origins_not_candidates(self):
        for origin in ['https://evil.example', '//evil.example', 'http://www.landkreis-restaurant.de',
                       'https://www.landkreis-restaurant.de:444', 'https://www.landkreis-restaurant.de:0',
                       'https://user@www.landkreis-restaurant.de',
                       'https://www.landkreis-restaurant.de.evil.example']:
            with self.subTest(origin=origin):
                html = f'<a href="{origin}{CURRENT}">'
                self.assertEqual(source.menu_candidates(html, BASE), [])
                with self.assertRaises(ValueError):
                    self.discover(html)
                self.assertEqual(self.discover(html + f'<a href="{CURRENT}">'), BASE.rstrip('/') + CURRENT)

    def test_base_tag_does_not_override_trusted_origin(self):
        self.assertEqual(self.discover('<base href="https://evil.example"><a href="Speise37.2026.pdf">'), BASE + 'Speise37.2026.pdf')

    def test_identity_strict_basename_and_iso_validity(self):
        for url in ['Speise00.2026.pdf', 'Speise54.2026.pdf', 'Speise53.2025.pdf', 'Speise37.0000.pdf',
                    'OtherSpeise37.2026.pdf', 'Speise__37.2026.pdf', 'Speise37.2026.pdf.exe',
                    '/Speise37.2026.pdf/other.pdf', '/other.pdf?file=Speise37.2026.pdf',
                    'Speise%2f37.2026.pdf', 'Speise٣٧.2026.pdf', 'Speise37.2026.pdf\n']:
            with self.subTest(url=url):
                self.assertIsNone(source.menu_identity(url))
        self.assertEqual(source.menu_identity(CURRENT + '?x#y'), (2026, 37))
        self.assertEqual(source.menu_identity('Speise53.2026.pdf'), (2026, 53))

    def test_iso_year_boundary(self):
        self.assertEqual(self.discover('<a href="Speise1.2026.pdf">', date(2025, 12, 29)), BASE + 'Speise1.2026.pdf')

    def test_default_uses_berlin_date_not_utc_week(self):
        # Sunday UTC, already Monday of week 37 in Berlin.
        instant = datetime(2026, 9, 6, 22, 30, tzinfo=timezone.utc)
        with patch.object(source, 'datetime') as clock:
            clock.now.side_effect = lambda zone: instant.astimezone(zone)
            self.assertEqual(source.discover_menu_url(f'<a href="{CURRENT}">', BASE), BASE.rstrip('/') + CURRENT)
            self.assertEqual(str(clock.now.call_args.args[0]), 'Europe/Berlin')

    def test_bounded_page_links_and_href(self):
        with patch.object(source, 'MAX_PAGE_CHARS', 10), self.assertRaises(ValueError):
            self.discover('x' * 11)
        with patch.object(source, 'MAX_LINKS', 1), self.assertRaises(ValueError):
            self.discover(f'<a href="{CURRENT}"><a href="{CURRENT}">')
        with self.assertRaises(ValueError):
            self.discover('<a href="' + 'x' * (source.MAX_URL_CHARS + 1) + '">')
        with self.assertRaises(ValueError):
            self.discover(f'<a href="{CURRENT}" href="{CURRENT}">')

    def test_updater_uses_shared_discovery(self):
        import update_menu
        from test_menu_probe import Session, pdf
        session = Session(f'<a href="{CURRENT}">', {CURRENT.lstrip('/'): pdf(37)})
        self.assertEqual(update_menu.discover_pdf(session, EXPECTED), BASE.rstrip('/') + CURRENT)


if __name__ == '__main__':
    unittest.main()
