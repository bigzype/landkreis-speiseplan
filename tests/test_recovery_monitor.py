"""Actual wrapper logic with temporary state and injected installed-helper seams."""
import contextlib
from datetime import date
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import repair_guard as g

class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ledger=g.Ledger(Path(self.tmp.name))
        original=types.ModuleType('landkreis_menu_source_monitor')
        original.main=lambda **kwargs: print('PHASE=COMPLETE\nSTATUS=AVAILABLE')
        target=types.ModuleType('landkreis_target_date'); target.target_menu_date=lambda:date(2026,9,21)
        with patch.dict(sys.modules,landkreis_menu_source_monitor=original,landkreis_target_date=target):
            spec=importlib.util.spec_from_file_location('recovery_wrapper_test',Path(g.__file__).parent/'ops/landkreis_menu_recovery_monitor.py')
            self.m=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        p=patch.object(g,'Ledger',return_value=self.ledger);p.start();self.addCleanup(p.stop)

    def output(self,readonly=False):
        out=io.StringIO()
        with contextlib.redirect_stdout(out): self.m.main(readonly=readonly,force=True)
        return out.getvalue()

    def claim(self):
        return self.ledger.claim('a'*64,'PARSE:ValueError:fixture','2026-09-21','https://www.landkreis-restaurant.de/fixture.pdf')[0]

    def test_current_public_noop_has_no_network_or_state_writes(self):
        with patch.object(g,'inspect') as inspect:
            self.assertIn('PHASE=COMPLETE',self.output(True));inspect.assert_not_called()
        self.assertEqual(list(self.ledger.root.iterdir()),[])

    def test_model_failure_claim_outranks_complete_and_is_stable(self):
        key=self.claim()
        a=self.output(); b=self.output()
        self.assertEqual(a,b); self.assertIn('PHASE=BLOCKED',a);self.assertIn(key,a)

    def test_stale_detection_wires_guard_and_strips_hour(self):
        self.m.original.main=lambda **kwargs:print('PHASE=UPDATE\nSTATUS=CURRENT\nTARGET_DATE=2026-09-21\nSOURCE=https://www.landkreis-restaurant.de/fixture.pdf\nATTEMPT_SLOT=hour')
        with patch.object(g,'inspect',return_value={'phase':'REPAIR','incident':'a'*64}) as inspect:
            output=self.output()
            inspect.assert_called_once_with('2026-09-21','https://www.landkreis-restaurant.de/fixture.pdf',self.ledger,readonly=False)
        self.assertIn('PHASE=REPAIR',output);self.assertNotIn('ATTEMPT_SLOT',output)

    def test_readonly_never_reconciles_pending_action(self):
        key=self.claim();self.ledger.transition(key,{'claimed'},'dispatched')
        with patch.object(g,'finish') as finish:
            self.assertIn('PHASE=BLOCKED',self.output(True));finish.assert_not_called()

    def test_notification_only_after_verified_and_not_repeated(self):
        key=self.claim()
        self.assertNotIn('REPAIR_VERIFIED',self.output())
        self.ledger.transition(key,{'claimed'},'verified')
        self.assertIn('PHASE=REPAIR_VERIFIED',self.output())
        self.ledger.transition(key,{'verified'},'notification_intent')
        self.assertNotIn('REPAIR_VERIFIED',self.output())

if __name__=='__main__':unittest.main()
