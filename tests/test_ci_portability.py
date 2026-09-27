"""Portable trusted validation without a Hermes installation or candidate imports."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import repair_guard as g

PROJECT = Path(g.__file__).resolve().parent
SOURCE = 'https://www.landkreis-restaurant.de/documents/1/Speise39.2026.pdf'
TARGET = '2026-09-21'

class PortableValidationTests(unittest.TestCase):
    def test_real_source_without_installed_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'nonexistent-hermes'
            with patch.object(g, 'HOME', missing), patch.object(g, 'VALIDATOR', missing / 'python'), patch.object(g, 'PYTHON', Path(sys.executable)):
                result = g.parse_source(PROJECT / 'tests/fixtures/Speise39.2026.pdf', SOURCE, TARGET)
            self.assertEqual(result, {'ok': True, 'week': '2026-KW39'})
            self.assertFalse(missing.exists())

    def test_candidate_cannot_shadow_validator_or_icalendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp)
            for name in ('update_menu.py', 'menu_source.py', 'menu_fetch.py'):
                shutil.copyfile(PROJECT / name, candidate / name)
            for name in ('validate_artifacts.py', 'landkreis_validate_live.py', 'icalendar.py'):
                (candidate / name).write_text('raise RuntimeError("candidate validator imported")')
            with patch.object(g, 'PYTHON', Path(sys.executable)), patch.dict(os.environ, {'PYTHONPATH': str(candidate)}):
                result = g.parse_source(PROJECT / 'tests/fixtures/Speise39.2026.pdf', SOURCE, TARGET, cwd=candidate)
            self.assertTrue(result['ok'])

    def test_wrong_target_remains_rejected(self):
        with patch.object(g, 'PYTHON', Path(sys.executable)):
            result = g.parse_source(PROJECT / 'tests/fixtures/Speise39.2026.pdf', SOURCE, '2026-09-28')
        self.assertFalse(result['ok'])

    def test_pure_checks_match_installed_contract(self):
        from validate_artifacts import validate_text, validate_ics
        from update_menu import parse_pdf, render_overview, render_ics
        from datetime import date
        data = parse_pdf(PROJECT / 'tests/fixtures/Speise39.2026.pdf', SOURCE, date.fromisoformat(TARGET))
        _, bodies = validate_text(render_overview(data).encode(), 2026, 39)
        raw = render_ics([data]).encode()
        self.assertEqual(validate_ics(raw, bodies)['events'], 5)
        for bad in (raw.replace(b'VERSION:2.0', b'VERSION:1.0'), raw.replace(b'20260921T120000', b'20260928T120000')):
            with self.assertRaises(ValueError):
                validate_ics(bad, bodies)

if __name__ == '__main__':
    unittest.main()
