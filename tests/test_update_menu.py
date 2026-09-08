"""Offline real-archive parsing and fail-closed publication regressions."""
from contextlib import redirect_stdout
from datetime import date, timedelta
from io import StringIO
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import update_menu as updater

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ('soups', 'mains', 'sides', 'vegetables', 'desserts')
# Canonical parse output from git 3f8e99d (before discovery repair). KW35's
# saved JSON was already different from that parser; do not silently rewrite it.
BASELINE_HASHES = {
    '2026-KW34': '105b1d6b798ce9c66ad0ff9ed13c1dceffbd63d301e88e164f19ec871d5e14ea',
    '2026-KW35': 'a40bf05631a6b4c70d1a76490d66fe915e2e713ac38729065f5a75cafd44c74e',
    '2026-KW36': 'ca5ccbac1f8bf3fb1f2ec3d03ab7b5be207a2e6d693e33357948d9e19506606a',
}


class ArchiveTests(unittest.TestCase):
    def test_all_retained_pdf_files_match_archived_content(self):
        records = {path.stem: json.loads(path.read_text()) for path in sorted((ROOT / 'data').glob('*.json'))}
        pdfs = sorted((ROOT / 'pdf').glob('*.pdf')) + sorted(ROOT.glob('*.pdf'))
        self.assertGreaterEqual(len(pdfs), len(records))
        for path in pdfs:
            with self.subTest(pdf=path.name):
                with updater.pdfplumber.open(path) as pdf:
                    period = updater.parse_period('\n'.join(page.extract_text() or '' for page in pdf.pages))
                iso = period.isocalendar()
                record = records[f'{iso.year}-KW{iso.week:02d}']
                parsed = updater.parse_pdf(path, record['source_url'], period)
                if parsed['week'] in BASELINE_HASHES:
                    digest = hashlib.sha256(json.dumps(parsed, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    self.assertEqual(digest, BASELINE_HASHES[parsed['week']], 'No parser content changes allowed')
                if parsed['week'] != '2026-KW35':
                    self.assertEqual(parsed, record)
                self.assertEqual([menu['day'] for menu in parsed['menus']], updater.DAYS)
                monday = date.fromisoformat(parsed['week_start'])
                for index, menu in enumerate(parsed['menus']):
                    self.assertEqual(menu['date'], (monday + timedelta(days=index)).isoformat())
                    for category in CATEGORIES:
                        self.assertTrue(menu[category], (path.name, menu['day'], category))
                ics = updater.render_ics([parsed])
                updater.validate_ics(ics, 5)

    def test_wrong_week_rejected_even_if_source_filename_claims_current(self):
        with self.assertRaisesRegex(ValueError, 'erwarteten ISO-Woche'):
            updater.parse_pdf(ROOT / 'pdf/2026-KW36.pdf', updater.BASE_URL + 'Speise_37.2026.pdf', date(2026, 9, 8))

    def test_default_pdf_guard_uses_current_berlin_date(self):
        with patch.object(updater, 'current_menu_date', return_value=date(2026, 9, 8)):
            with self.assertRaises(ValueError):
                updater.parse_pdf(ROOT / 'pdf/2026-KW36.pdf', 'claimed-current.pdf')

    def test_missing_day_and_each_required_category_fail_closed(self):
        path = ROOT / 'pdf/2026-KW36.pdf'
        with updater.pdfplumber.open(path) as pdf:
            text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
            tables = [table for page in pdf.pages for table in page.extract_tables()]
        for missing in ['day', *CATEGORIES]:
            with self.subTest(missing=missing):
                page = Mock()
                page.extract_text.return_value = text
                page.extract_tables.return_value = tables
                fake_pdf = Mock(pages=[page])
                opened = Mock()
                opened.__enter__ = Mock(return_value=fake_pdf)
                opened.__exit__ = Mock(return_value=False)
                original_add = updater.add_item
                original_detect = updater.detect_day
                def add(day, category, text, price):
                    if category != missing:
                        original_add(day, category, text, price)
                def detect(cell):
                    day = original_detect(cell)
                    return None if missing == 'day' and day == 'Montag' else day
                with patch.object(updater.pdfplumber, 'open', return_value=opened), patch.object(updater, 'add_item', side_effect=add), patch.object(updater, 'detect_day', side_effect=detect):
                    with self.assertRaises(RuntimeError):
                        updater.parse_pdf(path, 'archive', date(2026, 9, 1))


class PublicationTests(unittest.TestCase):
    def run_staged(self, root, expected, content, validator_error=False, download_error=False):
        session = Mock()
        session.headers = {}
        session.get.return_value.content = content
        if download_error:
            session.get.return_value.raise_for_status.side_effect = updater.requests.HTTPError('synthetic HTTP 503')
        with patch('sys.argv', ['update_menu.py', '--root', str(root)]), patch.object(updater, 'current_menu_date', return_value=expected), patch.object(updater.requests, 'Session', return_value=session), patch.object(updater, 'discover_pdf', return_value=updater.BASE_URL + 'Speise_37.2026.pdf'), redirect_stdout(StringIO()):
            if validator_error:
                with patch.object(updater, 'validate_ics', side_effect=RuntimeError('synthetic invalid ICS')):
                    return updater.main()
            return updater.main()

    def test_last_good_outputs_unchanged_on_every_validation_failure(self):
        for failure in ['wrong-week', 'malformed-pdf', 'invalid-ics', 'http-error']:
            with self.subTest(failure=failure), TemporaryDirectory() as temp:
                root = Path(temp)
                paths = ['Speiseplan.pdf', 'speiseplan.ics', 'speiseplan.txt', 'data/2026-KW36.json', 'pdf/2026-KW36.pdf']
                before = {path: ('last-good:' + path).encode() for path in paths}
                for path, content in before.items():
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                pdf = (ROOT / 'pdf/2026-KW36.pdf').read_bytes()
                expected = date(2026, 9, 8) if failure == 'wrong-week' else date(2026, 9, 1)
                errors = {
                    'wrong-week': (ValueError, 'erwarteten ISO-Woche'),
                    'malformed-pdf': (updater.pdfplumber.utils.exceptions.PdfminerException, 'No /Root object'),
                    'invalid-ics': (RuntimeError, 'synthetic invalid ICS'),
                    'http-error': (updater.requests.HTTPError, 'synthetic HTTP 503'),
                }
                error_type, message = errors[failure]
                with self.assertRaisesRegex(error_type, message):
                    self.run_staged(root, expected, b'not a PDF' if failure == 'malformed-pdf' else pdf, failure == 'invalid-ics', failure == 'http-error')
                after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
                self.assertEqual(before, after)

    def test_successful_staged_run_writes_only_validated_current_week(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = (ROOT / 'pdf/2026-KW36.pdf').read_bytes()
            self.assertEqual(self.run_staged(root, date(2026, 9, 1), pdf), 0)
            data = json.loads((root / 'data/2026-KW36.json').read_text())
            self.assertEqual(data['week'], '2026-KW36')
            self.assertEqual(len(data['menus']), 5)
            self.assertEqual((root / 'Speiseplan.pdf').read_bytes(), pdf)
            self.assertEqual((root / 'pdf/2026-KW36.pdf').read_bytes(), pdf)
            self.assertEqual((root / 'speiseplan.txt').read_text(), updater.render_overview(data))
            ics = (root / 'speiseplan.ics').read_bytes().decode()
            self.assertEqual(ics, updater.render_ics([data]))
            updater.validate_ics(ics, 5)


if __name__ == '__main__':
    unittest.main()
