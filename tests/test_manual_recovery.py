import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import repair_guard as g
import manual_recovery as m


class ManualRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.l = g.Ledger(Path(self.tmp.name))
        self.sha = g.digest(b'%PDF-real-test-placeholder')
        self.parent, _ = self.l.claim(self.sha, 'PUBLIC_TARGET_STALE', '2026-09-28', 'https://www.landkreis-restaurant.de/documents/282/Speise40.2026.pdf')
        self.l.transition(self.parent, {'claimed'}, 'failed', run_id=1)
        (self.l.root / (self.sha + '.pdf')).write_bytes(b'%PDF-real-test-placeholder')
        self.before = self.l.path(self.parent).read_bytes()
        self.code = 'a' * 40
        self.mock_run = patch.object(g, 'run', return_value=self.code + '\trefs/heads/main').start()
        self.addCleanup(patch.stopall)
        patch.object(g, 'parse_source', return_value={'ok': True}).start()
        self.v = m.authorize(self.parent, self.code, self.l)
        self.key = self.v['id']

    def test_parent_immutable_and_single_child_even_changed_code(self):
        self.assertEqual(self.before, self.l.path(self.parent).read_bytes())
        self.mock_run.return_value = 'b' * 40
        with self.assertRaises(ValueError): m.authorize(self.parent, 'b' * 40, self.l)
        self.assertEqual(len(list(self.l.root.glob('*.json'))), 2)

    def test_failed_parent_never_direct_dispatch(self):
        with self.assertRaises(ValueError): g.dispatch(self.parent, self.l)

    def test_intent_precedes_api_and_ambiguous_timeout_never_redispatch(self):
        def fail(*args, **kwargs):
            v = self.l.read(self.key)
            self.assertEqual(v['status'], 'dispatch_intent')
            self.assertEqual(v['payload']['inputs']['code_sha'], self.code)
            self.assertEqual(v['payload']['inputs']['source_sha'], self.sha)
            raise TimeoutError('uncertain')
        with patch.object(m.subprocess, 'run', side_effect=fail) as api:
            with self.assertRaises(TimeoutError): m.dispatch(self.key, self.l)
            with self.assertRaises(ValueError): m.dispatch(self.key, self.l)
            self.assertEqual(api.call_count, 1)

    def fixture_run(self):
        self.l.transition(self.key, {'manual_authorized'}, 'dispatch_intent')
        return dict(databaseId=42, status='completed', conclusion='success', headSha=self.code,
                    event='workflow_dispatch', displayTitle='Manual recovery ' + self.key)

    def test_exact_identity_and_ambiguous_runs(self):
        r = self.fixture_run()
        for runs in [[], [r, r], [dict(r, headSha='b'*40)], [dict(r,event='push')]]:
            self.mock_run.return_value = json.dumps(runs)
            with self.assertRaises(ValueError): m.finish(self.key, self.l)
        self.assertEqual(self.l.read(self.key)['status'], 'dispatch_intent')

    def test_success_requires_live_readback_and_preserves_parent(self):
        r = self.fixture_run(); self.mock_run.return_value = json.dumps([r])
        with patch.object(g, 'verify_published_source') as source, patch.object(g, 'live', side_effect=ValueError('stale')):
            with self.assertRaises(ValueError): m.finish(self.key, self.l)
            source.assert_called_once_with(self.sha)
        self.assertEqual(self.l.read(self.key)['status'], 'awaiting_readback')
        with patch.object(g, 'verify_published_source'), patch.object(g, 'live', return_value={'raw_ics_sha256':'x','pages_ics_sha256':'x'}):
            self.assertEqual(m.finish(self.key, self.l)['status'], 'verified')
        self.assertEqual(self.before, self.l.path(self.parent).read_bytes())
        self.assertEqual(g.inspect(self.v['target'], self.v['source'], self.l)['phase'], 'COMPLETE')

    def test_effective_record_is_child_no_source_poll(self):
        with patch.object(g, 'source_bytes', side_effect=AssertionError('source polled')):
            self.assertEqual(g.inspect(self.v['target'], self.v['source'], self.l)['incident'], self.key)
        self.assertEqual([v['id'] for v in g.effective_records(self.l)], [self.key])

    def test_parent_tamper_blocks(self):
        self.l.transition(self.parent, {'failed'}, 'claimed')
        with self.assertRaises(ValueError): g.effective_records(self.l)
        with self.assertRaises(ValueError): m.dispatch(self.key, self.l)

    def test_workflow_rejects_wrong_code_pin(self):
        with patch.dict(m.os.environ, RECOVERY_ID=self.key, CODE_SHA=self.code, SOURCE_SHA=self.sha, GITHUB_SHA='b'*40):
            with self.assertRaises(ValueError): m.workflow_source()

    def test_real_cached_kw40_validates_without_network(self):
        sha = '716962d9705a8b8fa87c6b61be0d99dba3b2689978e3c688d86e037d64aae9ec'
        path = Path(__file__).parent / 'fixtures' / ('recovery_' + sha + '.pdf')
        self.assertEqual(g.digest(path.read_bytes()), sha)
        # Exercise actual portable validator rather than mocked ledger setup.
        patch.stopall()
        result = g.parse_source(path.resolve(), self.v['source'], self.v['target'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['week'], '2026-KW40')
