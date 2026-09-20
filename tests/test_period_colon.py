"""Regression for the publisher's printed '21: September' in KW39."""
from datetime import date, timedelta
import hashlib
from pathlib import Path
import unittest

import update_menu as updater

FIXTURE = Path(__file__).parent / 'fixtures' / 'Speise39.2026.pdf'
SOURCE = 'https://www.landkreis-restaurant.de/documents/281/Speise39.2026.pdf'
HEADING = 'Speiseplan für den Zeitraum: 21: September bis zum 25.September 2026'


class ColonPeriodTests(unittest.TestCase):
    def test_printed_colon_after_start_day(self):
        self.assertEqual(updater.parse_period(HEADING), date(2026, 9, 21))

    def test_real_publisher_pdf(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                         'd6e1bcc6be3bc0cf7cdcebaad165ca97f4681107e9c83d570f18333cf8230188')
        with updater.pdfplumber.open(FIXTURE) as pdf:
            self.assertIn(HEADING, '\n'.join(p.extract_text() or '' for p in pdf.pages))
        data = updater.parse_pdf(FIXTURE, SOURCE, date(2026, 9, 21))
        self.assertEqual(data['week'], '2026-KW39')
        self.assertEqual(data['week_start'], '2026-09-21')
        self.assertEqual([m['day'] for m in data['menus']], updater.DAYS)
        self.assertEqual([m['date'] for m in data['menus']],
                         [(date(2026, 9, 21) + timedelta(days=i)).isoformat() for i in range(5)])
        for menu in data['menus']:
            for category in ('soups', 'mains', 'sides', 'vegetables', 'desserts'):
                self.assertTrue(menu[category], (menu['day'], category))
        self.assertIn('Friesisches Schweineschnitzel', data['menus'][0]['mains'][0]['text'])
        self.assertIn('Mehliertes Fischfilet', data['menus'][4]['mains'][0]['text'])
        updater.validate_ics(updater.render_ics([data]), 5)

    def test_filename_cannot_override_pdf_week(self):
        with self.assertRaisesRegex(ValueError, 'erwarteten ISO-Woche'):
            updater.parse_pdf(FIXTURE, 'https://example.invalid/Speise40.2026.pdf', date(2026, 9, 28))

    def test_colon_does_not_bypass_period_validation(self):
        invalid = [
            HEADING.replace('21:', '32:'),
            HEADING.replace('September bis', 'Unknown bis'),
            HEADING.replace('25.September', '18.September'),
            HEADING.replace('25.September', '30.September'),
            HEADING.replace('21:', '21::'),
            HEADING + '\nZeitraum ???',
            HEADING + '\nZeitraum: 14: September bis zum 18.September 2026',
        ]
        for heading in invalid:
            with self.subTest(heading=heading), self.assertRaises(ValueError):
                updater.parse_period(heading)


if __name__ == '__main__':
    unittest.main()
