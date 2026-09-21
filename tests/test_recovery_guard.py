"""Temporary ledgers and mock network only: never inject faults into production."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import repair_guard as g

PDF = b'%PDF- synthetic state-machine fixture, not menu data'
SOURCE = 'https://www.landkreis-restaurant.de/documents/1/menu.pdf'
TARGET = '2026-09-21'
BAD = dict(ok=False, kind='ValueError', message='Zeitraum im PDF nicht gefunden')

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ledger = g.Ledger(Path(self.tmp.name))
        mock = patch.object(g, 'verify_published_source'); mock.start(); self.addCleanup(mock.stop)

    def decide(self, parsed=BAD):
        return g.decision(PDF, parsed, TARGET, SOURCE, self.ledger)

    def test_stable_identity_model_failure_does_not_consume_publication(self):
        first = self.decide()
        self.assertEqual(first['phase'], 'REPAIR')
        # Model never called its first tool (e.g. provider 400); monitor hash consumed.
        second = self.decide()
        self.assertEqual(second['phase'], 'BLOCKED')
        self.assertEqual(first['incident'], second['incident'])
        v = self.ledger.read(first['incident'])
        self.assertEqual(v['status'], 'claimed'); self.assertEqual(v['attempts'], 1)
        self.assertNotIn('publication', v)

    def test_failed_uncertain_attempt_never_reclaims(self):
        for status in ('failed', 'push_intent', 'dispatch_intent', 'editing', 'testing'):
            with self.subTest(status=status):
                key, won = self.ledger.claim(g.digest(PDF), status, TARGET, SOURCE)
                self.assertTrue(won)
                self.ledger.transition(key, {'claimed'}, status)
                self.assertFalse(self.ledger.claim(g.digest(PDF), status, TARGET, SOURCE)[1])
                with self.assertRaises(ValueError): self.ledger.transition(key, {'claimed'}, 'editing')

    def test_concurrent_claim_only_one_winner(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: self.ledger.claim(g.digest(PDF), 'PARSE:x', TARGET, SOURCE), range(20)))
        self.assertEqual(sum(won for _, won in values), 1)
        self.assertEqual(len({key for key, _ in values}), 1)

    def test_changed_hash_or_relevant_signature_new_identity_not_clock(self):
        a, _ = self.ledger.claim(g.digest(PDF), 'PARSE:a', TARGET, SOURCE)
        b, _ = self.ledger.claim(g.digest(PDF+b'x'), 'PARSE:a', TARGET, SOURCE)
        c, _ = self.ledger.claim(g.digest(PDF), 'PARSE:b', TARGET, SOURCE)
        d, won = self.ledger.claim(g.digest(PDF), 'PARSE:a', '2026-09-22', SOURCE)
        self.assertEqual(len({a,b,c}),3); self.assertEqual(a,d); self.assertFalse(won)

    def test_stale_feed_valid_source_selects_authoritative_updater(self):
        with patch.object(g, 'live', side_effect=RuntimeError('old week')), patch.object(g, 'public_is_stale', return_value=True), patch.object(g, 'source_bytes', return_value=PDF), patch.object(g, 'parse_source', return_value={'ok':True}):
            value = g.inspect(TARGET, SOURCE, self.ledger)
        self.assertEqual(value['phase'], 'UPDATE')
        with patch.object(g, 'live') as live, patch.object(g, 'source_bytes') as source:
            again = g.inspect(TARGET, SOURCE, self.ledger)
            self.assertEqual(again['phase'], 'BLOCKED'); live.assert_not_called(); source.assert_not_called()

    def test_readonly_no_claim_even_stale(self):
        value = g.decision(PDF, BAD, TARGET, SOURCE, self.ledger, readonly=True)
        self.assertTrue(value['readonly']); self.assertIsNone(self.ledger.read(value['incident']))

    def test_dispatch_timeout_preserves_intent_no_retry(self):
        key = self.decide({'ok':True})['incident']
        with patch.object(g, 'run', side_effect=['abc refs/heads/main', '[]', TimeoutError('uncertain API')]):
            with self.assertRaises(TimeoutError): g.dispatch(key, self.ledger)
        self.assertEqual(self.ledger.read(key)['status'], 'dispatch_intent')
        with patch.object(g, 'run') as command, self.assertRaises(ValueError):
            g.dispatch(key, self.ledger)
        command.assert_not_called()

    def test_success_requires_exact_successful_run_and_public_readback(self):
        key = self.decide({'ok':True})['incident']
        self.ledger.transition(key, {'claimed'}, 'dispatched', prior_run_ids=[1], dispatch_sha='abc')
        runs = json.dumps([dict(databaseId=2,event='workflow_dispatch',headSha='abc',status='completed',conclusion='success')])
        with patch.object(g, 'run', return_value=runs), patch.object(g, 'live', side_effect=ValueError('Pages stale')):
            with self.assertRaises(ValueError): g.finish(key, self.ledger)
        self.assertEqual(self.ledger.read(key)['status'], 'awaiting_readback')
        self.assertNotIn('publication', self.ledger.read(key))
        with patch.object(g, 'run', return_value=runs), patch.object(g, 'live', return_value={'status':'validated','validation_mode':'pages_and_raw','pages':{'events':5}}) as live:
            value = g.finish(key, self.ledger)
        self.assertEqual(value['status'], 'verified'); live.assert_called_once_with(TARGET, record=True)
        self.assertEqual(self.ledger.read(key)['status'], 'verified')

    def test_workflow_failure_cannot_verify(self):
        key = self.decide({'ok':True})['incident']
        self.ledger.transition(key, {'claimed'}, 'dispatched', prior_run_ids=[], dispatch_sha='abc')
        runs = json.dumps([dict(databaseId=2,event='workflow_dispatch',headSha='abc',status='completed',conclusion='failure')])
        with patch.object(g, 'run', return_value=runs), patch.object(g, 'live') as live:
            self.assertEqual(g.finish(key,self.ledger)['status'],'failed'); live.assert_not_called()

    def test_workflow_ambiguity_cannot_verify(self):
        key = self.decide({'ok':True})['incident']
        self.ledger.transition(key, {'claimed'}, 'dispatched', prior_run_ids=[], dispatch_sha='abc')
        with patch.object(g, 'run', return_value='[]'), patch.object(g, 'live') as live:
            with self.assertRaises(ValueError): g.finish(key,self.ledger)
            live.assert_not_called()

    def test_parser_scope_freezes_guards_and_other_functions(self):
        before = (Path(g.__file__).parent / 'update_menu.py').read_text()
        after = before.replace('[.:]?', '[.: ]?', 1)
        g.parser_scope(before,after)
        for bad in [after.replace('if not matches:', 'if False:'), after.replace('EVENT_SEQUENCE = 5','EVENT_SEQUENCE = 6'), after.replace('re.findall(', '__import__("os").system(',1)]:
            with self.subTest(bad=bad[-20:]), self.assertRaises(ValueError): g.parser_scope(before,bad)

    def test_corrupt_state_is_not_reset(self):
        key = self.decide()['incident']; path = self.ledger.path(key); path.write_text('{bad')
        with self.assertRaises(json.JSONDecodeError): self.decide()
        self.assertEqual(path.read_text(),'{bad')

    def test_begin_claim_before_worktree_and_no_retry_on_crash(self):
        key = self.decide()['incident']
        with patch.object(g,'run',side_effect=['', 'abc',RuntimeError('worktree failure')]):
            with self.assertRaises(RuntimeError):g.begin(key,self.ledger)
        self.assertEqual(self.ledger.read(key)['status'],'editing')
        with patch.object(g,'run',return_value='abc') as run, self.assertRaises(ValueError):g.begin(key,self.ledger)
        self.assertEqual(run.call_count,2) # fetch + rev-parse, never another worktree add

    def test_notifications_have_independent_single_intent(self):
        key=self.decide()['incident']
        with self.assertRaises(ValueError):self.ledger.transition(key,{'verified'},'notification_intent')
        self.ledger.transition(key,{'claimed'},'verified',publication={'test_fixture':True})
        self.ledger.transition(key,{'verified'},'notification_intent')
        with self.assertRaises(ValueError):self.ledger.transition(key,{'verified'},'notification_intent')

    def test_real_isolated_worktree_scope_tests_and_pdf(self):
        import shutil
        import sys
        root = Path(self.tmp.name) / 'repository'; root.mkdir()
        project = Path(g.__file__).parent
        for name in ('update_menu.py', 'menu_source.py', 'menu_fetch.py'):
            shutil.copyfile(project / name, root / name)
        shutil.copytree(project / 'tests/fixtures', root / 'tests/fixtures')
        shutil.copyfile(project / 'tests/test_period_colon.py', root / 'tests/test_period_colon.py')
        (root / 'lessons.md').write_text('# Lessons\n')
        (root / '.gitignore').write_text('__pycache__/\n*.pyc\n')
        # Reproduce the real old period bug ONLY inside a disposable repository.
        good = (root / 'update_menu.py').read_text()
        (root / 'update_menu.py').write_text(good.replace('[.:]?', '[.]?', 1))
        g.run(['git','init','-q'], cwd=root)
        g.run(['git','add','.'], cwd=root)
        g.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture baseline'], cwd=root)
        base = g.run(['git','rev-parse','HEAD'], cwd=root).strip()
        w = Path(self.tmp.name) / 'isolated-worktree'
        g.run(['git','worktree','add','--detach',w,base], cwd=root)
        pdf = (project / 'tests/fixtures/Speise39.2026.pdf').read_bytes()
        key, _ = self.ledger.claim(g.digest(pdf),'PARSE:fixture-real-colon',TARGET,SOURCE)
        source = self.ledger.root / (g.digest(pdf)+'.pdf'); source.write_bytes(pdf)
        with patch.object(g,'PYTHON',Path(sys.executable)):
            self.assertFalse(g.parse_source(source,SOURCE,TARGET,cwd=w)['ok'])
            (w / 'update_menu.py').write_text(good)
            (w / 'lessons.md').write_text('# Lessons\n- Preserve date guards; accept observed colon.\n')
            (w / 'tests/test_repair_colon.py').write_text('import unittest\nfrom datetime import date\nfrom update_menu import parse_period\nclass Regression(unittest.TestCase):\n def test_real_heading(self):\n  self.assertEqual(parse_period("Zeitraum: 21: September bis zum 25.September 2026"),date(2026,9,21))\n')
            self.ledger.transition(key,{'claimed'},'editing',worktree=str(w),base=base)
            self.assertEqual(g.verify(key,self.ledger)['status'],'tested')
            # A byte changed after tests must block before any remote operation.
            (w / 'lessons.md').write_text((w / 'lessons.md').read_text()+'changed\n')
            with self.assertRaisesRegex(ValueError,'candidate changed'):g.publish(key,self.ledger)
            # Existing independent tests are immutable even when replacement would pass.
            (w / 'tests/test_period_colon.py').write_text('')
            with self.assertRaisesRegex(ValueError,'existing tests'):g.check_scope(w,base)
        self.assertNotEqual(w,root)

    def test_http_error_is_not_a_stale_processing_incident(self):
        with patch.object(g,'live',side_effect=RuntimeError('HTTP')), patch.object(g,'public_is_stale',side_effect=RuntimeError('HTTP')), patch.object(g,'source_bytes') as fetch:
            with self.assertRaises(RuntimeError):g.inspect(TARGET,SOURCE,self.ledger)
            fetch.assert_not_called()
        self.assertEqual(list(self.ledger.root.glob('*.json')),[])

if __name__ == '__main__': unittest.main()
