"""Filename monitoring is an update signal, never PDF publication evidence."""
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import menu_fetch
import menu_probe
from test_menu_probe import BASE, EXPECTED, ROOT, Response, Session, pdf


class FilenameProbeTests(unittest.TestCase):
    def run_cli(self, session, previous=None, expected=EXPECTED):
        args = ['--filename-first', '--expected', expected.isoformat()]
        if previous is not None:
            args += ['--previous-source', previous]
        out = StringIO()
        with patch.object(menu_probe.requests, 'Session', return_value=session), redirect_stdout(out):
            code = menu_probe.main(args)
        return code, json.loads(out.getvalue())

    def test_changed_basename_never_downloads_or_parses_pdf_even_old_week(self):
        for name in ['new.pdf', 'Speise_36.2026.pdf', 'Speise%2036.2026.pdf']:
            with self.subTest(name=name):
                session = Session(f'<a href="{name}">Speiseplan</a>', {})
                with patch('update_menu.parse_pdf', side_effect=AssertionError('must not parse')) as parser:
                    code, report = self.run_cli(session, BASE + 'Speise35.2026.pdf')
                parser.assert_not_called()
                self.assertEqual(code, 0)
                self.assertEqual(report, {'status': 'current', 'reason': 'FILENAME_CHANGED',
                    'detected_by': 'filename', 'source_url': BASE + name,
                    'actual': name.replace('%20', ' '), 'expected_date': '2026-09-08'})
                self.assertEqual(session.calls, [BASE])

    def test_matching_week_bootstrap_reuses_recognized_filename_variants(self):
        for name, expected in [('Speise_37.2026.pdf', EXPECTED),
                               ('Speise%2037.2026.pdf?x=1#page=1', EXPECTED),
                               ('Speise1.2026.pdf', date(2025, 12, 29))]:
            session = Session(f'<a href="{name}">Plan</a>', {})
            with patch('update_menu.parse_pdf', side_effect=AssertionError('must not parse')) as parser:
                code, report = self.run_cli(session, expected=expected)
            parser.assert_not_called()
            self.assertEqual((code, report['reason'], report['detected_by']), (0, 'FILENAME_WEEK', 'filename'))
            self.assertNotIn('pdf_week', report)
            self.assertEqual(session.calls, [BASE])

    def test_same_basename_uses_actual_pdf_content_including_matching_week(self):
        for name in ['Speiseplan.pdf', 'Speise_37.2026.pdf']:
            for week, status in [(36, 'waiting'), (37, 'current')]:
                with self.subTest(name=name, week=week):
                    session = Session(f'<a href="{name}">Speiseplan</a>', {name: pdf(week)})
                    code, report = self.run_cli(session, BASE + 'old-directory/' + name + '?v=old#p')
                    self.assertEqual((code, report['status'], report['pdf_week']), (0, status, f'2026-KW{week}'))
                    self.assertNotIn('detected_by', report)
                    self.assertEqual(session.calls, [BASE, BASE + name])

    def test_equivalent_encoded_basename_is_not_a_change(self):
        name = 'Speise%2037.2026.pdf?new=1'
        session = Session(f'<a href="{name}">Plan</a>', {name: pdf(36)})
        code, report = self.run_cli(session, BASE + 'Speise 37.2026.pdf?old=1')
        self.assertEqual((code, report['status']), (0, 'waiting'))
        self.assertEqual(session.calls, [BASE, BASE + name])

    def test_no_baseline_generic_or_wrong_week_falls_back(self):
        for name in ['new.pdf', 'Speise_36.2026.pdf']:
            session = Session(f'<a href="{name}">Speiseplan</a>', {name: pdf(37)})
            code, report = self.run_cli(session)
            self.assertEqual((code, report['status'], report['pdf_week']), (0, 'current', '2026-KW37'))
            self.assertEqual(session.calls, [BASE, BASE + name])

    def test_multiple_candidates_use_bounded_fallback_not_filename_choice(self):
        for previous in [None, BASE + 'Speise36.2026.pdf']:
            for other_week, status in [(36, 'current'), (37, 'error')]:
                session = Session('<a href="Speise37.2026.pdf">Plan</a><a href="other.pdf">Menü</a>',
                                  {'Speise37.2026.pdf': pdf(37), 'other.pdf': pdf(other_week)})
                code, report = self.run_cli(session, previous)
                self.assertEqual((code, report['status']), (int(status == 'error'), status))
                if status == 'error':
                    self.assertEqual(report['reason'], 'AMBIGUOUS_PDF_WEEK')
                self.assertNotIn('detected_by', report)
                self.assertEqual(session.calls, [BASE, BASE + 'Speise37.2026.pdf', BASE + 'other.pdf'])

    def test_shared_discovery_excludes_catering_foreign_and_decoded_controls(self):
        page = '''<base href="https://evil.test/"><section id="speiseplan">
          <a href="new.pdf">Download</a><h3>Catering</h3><a href="other.pdf">Download</a></section>
          <a href="random.pdf">Download</a><a href="https://evil.test/menu.pdf">Menü</a>
          <a href="bad%0a.pdf">Speiseplan</a><a href="new.pdf">Speiseplan</a>'''
        session = Session(page, {})
        code, report = self.run_cli(session, BASE + 'old.pdf')
        self.assertEqual((code, report['source_url']), (0, BASE + 'new.pdf'))
        self.assertEqual(session.calls, [BASE])

    def test_invalid_baseline_rejected_before_network(self):
        for previous in ['old.pdf', 'https://evil.test/old.pdf', BASE + 'not-pdf',
                         'https://user@www.landkreis-restaurant.de/old.pdf',
                         BASE + 'bad\\name.pdf', BASE + 'old.pdf\n',
                         *[BASE + 'old.pdf?x=' + code for code in ['%0a', '%0d', '%09', '%00', '%7f']]]:
            with self.subTest(previous=previous):
                session = Session('', {})
                code, report = self.run_cli(session, previous)
                self.assertEqual((code, report['reason']), (1, 'INVALID_ARGUMENT'))
                self.assertEqual(session.calls, [])

    def test_previous_source_requires_filename_mode(self):
        out = StringIO()
        with patch.object(menu_probe.requests, 'Session') as session, redirect_stdout(out):
            self.assertEqual(menu_probe.main(['--previous-source', BASE + 'old.pdf']), 1)
        session.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['reason'], 'INVALID_ARGUMENT')

    def test_discovery_and_fallback_errors_are_not_current(self):
        cases = [('', {}, 'NO_MENU_LINK'),
                 ('<a href="bad%0d.pdf">Menü</a>', {}, 'NO_MENU_LINK'),
                 (''.join(f'<a href="{i}.pdf">Menü</a>' for i in range(5)), {}, 'SOURCE_INVALID'),
                 ('<a href="old.pdf">Menü</a>', {'old.pdf': b'broken'}, 'PDF_INVALID')]
        for page, files, reason in cases:
            session = Session(page, files)
            code, report = self.run_cli(session, BASE + 'old.pdf')
            self.assertEqual((code, report['status'], report['reason']), (1, 'error', reason))

    def test_index_errors_and_limits_still_apply(self):
        import requests
        for error, reason in [(requests.Timeout('test timeout'), 'NETWORK_TIMEOUT'),
                              (requests.ConnectionError('test connection'), 'NETWORK_ERROR')]:
            with patch.object(Session, 'get', side_effect=error):
                code, report = self.run_cli(Session('', {}))
            self.assertEqual((code, report['reason']), (1, reason))
        for response, reason in [(Response(status=302, headers={'Location': 'https://evil.test/'}), 'UNSAFE_REDIRECT'),
                                 (Response(status=302, headers={'Location': '/bad%0a'}), 'SOURCE_INVALID'),
                                 (Response(status=404), 'NETWORK_ERROR'),
                                 (Response(headers={'Content-Length': '2000001'}), 'SIZE_LIMIT')]:
            session = Session('', {})
            session.responses[BASE] = response
            code, report = self.run_cli(session)
            self.assertEqual((code, report['reason']), (1, reason))

    def test_both_modes_are_read_only_and_filename_result_has_no_pdf_data(self):
        before = {p.relative_to(ROOT): p.read_bytes() for p in ROOT.rglob('*')
                  if p.is_file() and (p.suffix in {'.json', '.ics', '.pdf', '.txt'}) and '.venv' not in p.parts}
        for previous in [None, BASE + 'old.pdf']:
            session = Session('<a href="new.pdf">Menü</a>', {'new.pdf': pdf(37)})
            with patch.object(Path, 'write_bytes', side_effect=AssertionError('no writes')), \
                 patch.object(Path, 'write_text', side_effect=AssertionError('no writes')):
                code, _ = self.run_cli(session, previous)
            self.assertEqual(code, 0)
        result = menu_fetch.probe_menu_filename_first(Session('<a href="new.pdf">Menü</a>', {}),
                                                      EXPECTED, previous_source=BASE + 'old.pdf')
        self.assertIsNone(result.data)
        self.assertIsNone(result.pdf_bytes)
        after = {p.relative_to(ROOT): p.read_bytes() for p in ROOT.rglob('*')
                 if p.is_file() and (p.suffix in {'.json', '.ics', '.pdf', '.txt'}) and '.venv' not in p.parts}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
