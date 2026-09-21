"""Authorized scope expansion: faults exist only in disposable worktrees."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import repair_guard as g

PROJECT = Path(g.__file__).parent
SOURCE = 'https://www.landkreis-restaurant.de/documents/1/Speise39.2026.pdf'
TARGET = '2026-09-21'

class ExpandedRecoveryTests(unittest.TestCase):
    def test_protected_validation_and_safety_rejected(self):
        before = (PROJECT / 'update_menu.py').read_text()
        for old, new in [
            ('if not matches:', 'if False:'),
            ('if verify_expected and', 'if False and'),
            ('if [m["day"] for m in menus] != DAYS:', 'if False:'),
            ('if missing:', 'if False:'),
            ('if len(line.encode("utf-8")) > 75:', 'if False:'),
            ('EVENT_SEQUENCE = 5', 'EVENT_SEQUENCE = 6'),
            ('return text.strip(" -/,")', 'return __import__("os").getcwd()'),
            ('return text.strip(" -/,")', 'import os\n    return text'),
            ('return text.strip(" -/,")', 'global DAYS\n    return text'),
        ]:
            with self.subTest(old=old), self.assertRaises(ValueError):
                g.parser_scope(before, before.replace(old,new,1))

    def exercise_fix(self, old, faulty, regression):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'; root.mkdir()
            for name in ('update_menu.py','menu_source.py','menu_fetch.py'):
                shutil.copyfile(PROJECT / name,root / name)
            shutil.copytree(PROJECT / 'tests/fixtures',root / 'tests/fixtures')
            shutil.copyfile(PROJECT / 'tests/test_period_colon.py',root / 'tests/test_period_colon.py')
            (root / 'lessons.md').write_text('# Lessons\n')
            (root / '.gitignore').write_text('__pycache__/\n*.pyc\n')
            good = (root / 'update_menu.py').read_text()
            self.assertIn(old,good)
            bad = good.replace(old,faulty,1)
            (root / 'update_menu.py').write_text(bad)
            g.run(['git','init','-q'],cwd=root)
            g.run(['git','add','.'],cwd=root)
            g.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],cwd=root)
            base = g.run(['git','rev-parse','HEAD'],cwd=root).strip()
            worktree = Path(directory) / 'worktree'
            g.run(['git','worktree','add','--detach',worktree,base],cwd=root)
            ledger = g.Ledger(Path(directory) / 'ledger')
            pdf = (PROJECT / 'tests/fixtures/Speise39.2026.pdf').read_bytes()
            with patch.object(g,'PYTHON',Path(sys.executable)):
                path = worktree / 'tests/fixtures/Speise39.2026.pdf'
                failure = g.parse_source(path,SOURCE,TARGET,cwd=worktree)
                self.assertFalse(failure['ok'])
                incident = g.decision(pdf,failure,TARGET,SOURCE,ledger)
                self.assertEqual(incident['phase'],'REPAIR')
                key = incident['incident']
                (worktree / 'update_menu.py').write_text(good)
                # Equal-length VERSION edits within one second must not reuse pyc.
                shutil.rmtree(worktree / '__pycache__', ignore_errors=True)
                (worktree / 'lessons.md').write_text('# Lessons\n- Restore evidenced parsing/rendering; preserve validation.\n')
                (worktree / 'tests/test_repair_observed.py').write_text(regression)
                ledger.transition(key,{'claimed'},'editing',base=base,worktree=str(worktree))
                self.assertEqual(g.verify(key,ledger)['status'],'tested')
                self.assertEqual(ledger.read(key)['attempts'],1)
                # The future repair agent cannot change the guard or workflow.
                for protected in ('repair_guard.py','.github/workflows/update-speiseplan.yml'):
                    p = worktree / protected; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('# forbidden\n')
                    with self.assertRaisesRegex(ValueError,'out-of-scope'): g.check_scope(worktree,base)
                    p.unlink()

    def test_non_date_parser_fix_real_source_and_independent_validation(self):
        self.exercise_fix('if len(letters) == len(day) and sorted(letters.lower()) == sorted(day.lower()):',
                          'if False:',
                          'import unittest\nfrom update_menu import detect_day\nclass Regression(unittest.TestCase):\n def test_day(self):\n  self.assertEqual(detect_day("Montag"),"Montag")\n')

    def test_ics_generation_fix_real_source_and_independent_validation(self):
        self.exercise_fix('"VERSION:2.0"','"VERSION:1.0"',
                          'import unittest\nfrom update_menu import render_ics\nclass Regression(unittest.TestCase):\n def test_version(self):\n  self.assertIn("VERSION:2.0",render_ics([]))\n')

    def test_independent_validator_rejects_missing_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('update_menu.py','menu_source.py','menu_fetch.py'):
                shutil.copyfile(PROJECT/name,root/name)
            p=root/'update_menu.py'
            p.write_text(p.read_text().replace('return f"{item[\'text\']} – {item[\'price\']}" if item.get("price") else item["text"]','return item["text"]'))
            with patch.object(g,'PYTHON',Path(sys.executable)):
                result=g.parse_source(PROJECT/'tests/fixtures/Speise39.2026.pdf',SOURCE,TARGET,cwd=root)
            self.assertFalse(result['ok'])
            self.assertIn('price missing',result['message'])

if __name__ == '__main__': unittest.main()
