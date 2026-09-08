"""Shared PDF-content discovery: offline real PDFs, no publication side effects."""
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

import menu_source as source

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://www.landkreis-restaurant.de/'
EXPECTED = date(2026, 9, 8)


class Response:
    def __init__(self, content=b'', status=200, headers=None):
        self.content = content
        self.status_code = status
        self.headers = headers or {}
        self.encoding = 'utf-8'

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f'HTTP {self.status_code}')

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        pass


class Session:
    def __init__(self, page, files):
        self.responses = {BASE: Response(page.encode()), **{
            BASE + name: value if isinstance(value, Response) else Response(value)
            for name, value in files.items()
        }}
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append(url)
        assert kwargs['allow_redirects'] is False
        assert kwargs['stream'] is True
        return self.responses[url]

    def close(self):
        pass


def pdf(week):
    return (ROOT / f'pdf/2026-KW{week}.pdf').read_bytes()


class TargetTests(unittest.TestCase):
    def test_weekdays_and_sunday(self):
        for day in range(7, 13):
            self.assertEqual(source.target_menu_date(date(2026, 9, day)), date(2026, 9, day))
        self.assertEqual(source.target_menu_date(date(2026, 9, 6)), date(2026, 9, 7))

    def test_iso_year(self):
        target = source.target_menu_date(date(2025, 12, 28))
        self.assertEqual(target, date(2025, 12, 29))
        self.assertEqual(target.isocalendar()[:2], (2026, 1))
        self.assertEqual(source.target_menu_date(date(2027, 1, 3)), date(2027, 1, 4))

    def test_berlin_dst_and_utc_day_boundary(self):
        for instant, expected in [
            (datetime(2026, 3, 28, 23, 30, tzinfo=timezone.utc), date(2026, 3, 30)),
            (datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc), date(2026, 3, 30)),
            (datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc), date(2026, 10, 26)),
            (datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc), date(2026, 10, 26)),
            (datetime(2026, 9, 6, 22, 30, tzinfo=timezone.utc), date(2026, 9, 7)),
        ]:
            with self.subTest(instant=instant):
                self.assertEqual(source.target_menu_date(instant), expected)
        # Naive datetimes are explicitly Berlin wall time, not host-local time.
        self.assertEqual(source.target_menu_date(datetime(2026, 9, 6, 12)), date(2026, 9, 7))

    def test_default_actual_date_stays_distinct_from_target(self):
        with patch.object(source, 'current_menu_date', return_value=date(2026, 9, 6)):
            self.assertEqual(source.target_menu_date(), date(2026, 9, 7))
            self.assertEqual(source.current_menu_date(), date(2026, 9, 6))


class ProbeTests(unittest.TestCase):
    def probe(self, page, files, expected=EXPECTED):
        from menu_fetch import probe_menu
        session = Session(page, files)
        result = probe_menu(session, expected)
        return result, session

    def test_generic_pdf_in_menu_section_and_label(self):
        for page in ['<section id="speiseplan"><a href="neu.pdf">Download</a></section>',
                     '<a href="neu.pdf" title="Wöchentlicher Speiseplan">Download</a>',
                     '<a href="neu.pdf">Aktueller <b>Speiseplan</b></a>']:
            result, _ = self.probe(page, {'neu.pdf': pdf(37)})
            self.assertEqual(result.report, {'status': 'current', 'expected_date': '2026-09-08',
                'source_url': BASE + 'neu.pdf', 'actual': 'neu.pdf', 'pdf_week': '2026-KW37'})
            self.assertEqual(len(result.data['menus']), 5)
            self.assertEqual(result.pdf_bytes, pdf(37))

    def test_filename_change_old_content_waits(self):
        result, _ = self.probe('<a href="Speise38.2026.pdf">Plan</a>', {'Speise38.2026.pdf': pdf(36)})
        self.assertEqual(result.report['status'], 'waiting')
        self.assertEqual(result.report['pdf_week'], '2026-KW36')
        self.assertIsNone(result.data)
        self.assertIsNone(result.pdf_bytes)

    def test_unchanged_name_new_content_and_stale_filename(self):
        for name in ['Speiseplan.pdf', 'Speise36.2026.pdf']:
            old, _ = self.probe(f'<a href="{name}">Speiseplan</a>', {name: pdf(36)})
            new, _ = self.probe(f'<a href="{name}">Speiseplan</a>', {name: pdf(37)})
            self.assertEqual(old.report['status'], 'waiting')
            self.assertEqual(new.report['status'], 'current')

    def test_all_candidates_examined_with_wrong_filename(self):
        result, session = self.probe('<a href="Speise37.2026.pdf">Plan</a><a href="Speise36.2026.pdf">Plan</a>',
                                    {'Speise37.2026.pdf': pdf(36), 'Speise36.2026.pdf': pdf(37)})
        self.assertEqual(result.report['source_url'], BASE + 'Speise36.2026.pdf')
        self.assertEqual(len(session.calls), 3)

    def test_ambiguity_even_identical_content_fails(self):
        with self.assertRaisesRegex(ValueError, 'Mehrere'):
            self.probe('<a href="Speise37.2026.pdf">Plan</a><a href="other.pdf">Speiseplan</a>',
                       {'Speise37.2026.pdf': pdf(37), 'other.pdf': pdf(37)})

    def test_malformed_meaningful_candidate_blocks_current(self):
        with self.assertRaises(Exception):
            self.probe('<a href="Speise37.2026.pdf">Plan</a><a href="bad.pdf">Speiseplan</a>',
                       {'Speise37.2026.pdf': pdf(37), 'bad.pdf': b'%PDF- broken'})
        with self.assertRaises(Exception):
            self.probe('<a href="Speise36.2026.pdf">Plan</a>', {'Speise36.2026.pdf': b'not pdf'})

    def test_excludes_catering_random_and_foreign_pdfs(self):
        page = '''<section id="speiseplan"><h3>Speiseplan</h3><a href="new.pdf">Download</a>
          <h3>Catering Angebot</h3><a href="another.pdf">Download</a><a href="Catering.pdf">Catering!</a></section>
          <a href="random.pdf">Download</a><section id="catering"><a href="menu.pdf">Menü</a></section>
          <a href="https://evil.test/new.pdf">Speiseplan</a>'''
        result, session = self.probe(page, {'new.pdf': pdf(37)})
        self.assertEqual(result.report['status'], 'current')
        self.assertEqual(session.calls, [BASE, BASE + 'new.pdf'])

    def test_candidate_limit_is_error_not_truncation(self):
        with self.assertRaisesRegex(ValueError, 'Viele|viele'):
            self.probe(''.join(f'<a href="{i}.pdf">Speiseplan</a>' for i in range(5)), {})

    def test_percent_encoded_controls_never_become_monitor_fields(self):
        from menu_fetch import ProbeError
        for code in ['%0a', '%0D', '%00', '%09', '%7f']:
            with self.subTest(code=code):
                self.assertEqual(source.menu_candidates(f'<a href="file{code}.pdf">Speiseplan</a>', BASE), [])
                self.assertIsNone(source.menu_identity('Speise37.2026.pdf?x=' + code))
                with self.assertRaises(ProbeError):
                    self.probe('<a href="new.pdf">Speiseplan</a>', {
                        'new.pdf': Response(status=302, headers={'Location': '/file' + code + '.pdf'})})

    def test_no_candidates_is_error_not_waiting(self):
        with self.assertRaises(ValueError):
            self.probe('<a href="random.pdf">Download</a>', {})

    def test_redirect_must_stay_same_origin(self):
        with self.assertRaisesRegex(ValueError, 'Ursprung'):
            self.probe('<a href="new.pdf">Speiseplan</a>',
                       {'new.pdf': Response(status=302, headers={'Location': 'https://evil.test/file.pdf'})})
        result, session = self.probe('<a href="new.pdf">Speiseplan</a>', {
            'new.pdf': Response(status=302, headers={'Location': '/actual.pdf'}), 'actual.pdf': pdf(37)})
        self.assertEqual(result.report['source_url'], BASE + 'new.pdf')
        self.assertEqual(session.calls[-1], BASE + 'actual.pdf')

    def test_size_and_redirect_limits(self):
        with patch('menu_fetch.MAX_PDF_BYTES', 10), self.assertRaises(ValueError):
            self.probe('<a href="new.pdf">Speiseplan</a>', {'new.pdf': pdf(37)})
        with self.assertRaises(ValueError):
            self.probe('<a href="new.pdf">Speiseplan</a>', {
                'new.pdf': Response(status=302, headers={'Location': '/new.pdf'})})

    def test_cli_json_current_waiting_error(self):
        import menu_probe
        for week, status, exit_code in [(37, 'current', 0), (36, 'waiting', 0), (None, 'error', 1)]:
            session = Session('<a href="new.pdf">Speiseplan</a>', {'new.pdf': pdf(week) if week else b'broken'})
            out = StringIO()
            with patch.object(menu_probe.requests, 'Session', return_value=session), redirect_stdout(out):
                self.assertEqual(menu_probe.main(['--expected', '2026-09-08']), exit_code)
            report = json.loads(out.getvalue())
            self.assertEqual(report['status'], status)
            self.assertEqual(report['expected_date'], '2026-09-08')

    def test_cli_rejects_invalid_expected_before_network(self):
        import menu_probe
        for invalid in ['2026-9-08', '2026-02-30', '20260908', '2026-09-08extra']:
            out = StringIO()
            with patch.object(menu_probe.requests, 'Session') as session, redirect_stdout(out):
                self.assertEqual(menu_probe.main(['--expected', invalid]), 1)
                session.assert_not_called()
            report = json.loads(out.getvalue())
            self.assertEqual(report['reason'], 'INVALID_ARGUMENT')
            self.assertEqual(report['status'], 'error')

    def test_period_conflicts_and_malformed_end_rejected(self):
        from update_menu import parse_period
        for text in ['Zeitraum 7 September bis 32 September 2026',
                     'Zeitraum 7 September bis 4 September 2026',
                     'Zeitraum 7 September bis 18 September 2026',
                     'Zeitraum 7 September bis 11 September 2026 Zeitraum ???',
                     'Zeitraum 7 September bis 11 September 2026 Zeitraum 14 September bis 18 September 2026']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_period(text)
        self.assertEqual(parse_period('Zeitraum 29 Dezember bis 2 Januar 2026'), date(2025, 12, 29))

    def test_malformed_old_full_menu_is_error_not_waiting(self):
        import update_menu
        with patch.object(update_menu, 'detect_day', return_value=None):
            from menu_fetch import ProbeError
            with self.assertRaises(ProbeError) as error:
                self.probe('<a href="Speise36.2026.pdf">Plan</a>', {'Speise36.2026.pdf': pdf(36)})
            self.assertEqual(error.exception.reason, 'PDF_INVALID')

    def test_network_timeout_and_streaming_size_enforced(self):
        import requests
        from menu_fetch import ProbeError
        with patch.object(Session, 'get', side_effect=requests.Timeout('synthetic timeout')):
            with self.assertRaises(ProbeError) as error:
                self.probe('<a href="Speiseplan.pdf">Plan</a>', {})
            self.assertEqual(error.exception.reason, 'NETWORK_TIMEOUT')
        with patch('menu_fetch.time.monotonic', side_effect=[0, 76]):
            with self.assertRaises(ProbeError) as error:
                self.probe('', {})
            self.assertEqual(error.exception.reason, 'NETWORK_TIMEOUT')
        with self.assertRaises(ProbeError) as error:
            self.probe('<a href="Speiseplan.pdf">Plan</a>', {
                'Speiseplan.pdf': Response(b'', headers={'Content-Length': '9000000'})})
        self.assertEqual(error.exception.reason, 'SIZE_LIMIT')

    def test_updater_sunday_uses_fallback_without_output_changes(self):
        import update_menu
        import menu_fetch
        for broken in [False, True]:
            with self.subTest(broken=broken), TemporaryDirectory() as temp:
                root = Path(temp)
                last_good = root / 'speiseplan.txt'
                last_good.write_bytes(b'last-good')
                page = '<section id="speiseplan"><a href="renamed.pdf">Download</a>'
                files = {'renamed.pdf': pdf(37)}
                if broken:
                    page += '<a href="bad.pdf">Download</a>'
                    files['bad.pdf'] = b'broken'
                page += '</section>'
                session = Session(page, files)
                with patch.object(source, 'current_menu_date', return_value=date(2026, 9, 6)), \
                     patch.object(update_menu.requests, 'Session', return_value=session), \
                     patch('sys.argv', ['update_menu.py', '--root', temp]), redirect_stdout(StringIO()):
                    if broken:
                        with self.assertRaises(menu_fetch.ProbeError):
                            update_menu.main()
                        self.assertEqual(last_good.read_bytes(), b'last-good')
                        self.assertEqual(list(root.iterdir()), [last_good])
                    else:
                        self.assertEqual(update_menu.main(), 0)
                        data = json.loads((root / 'data/2026-KW37.json').read_text())
                        self.assertEqual(data['week_start'], '2026-09-07')
                        self.assertEqual(data['source_url'], BASE + 'renamed.pdf')
                        self.assertEqual((root / 'Speiseplan.pdf').read_bytes(), pdf(37))


if __name__ == '__main__':
    unittest.main()
