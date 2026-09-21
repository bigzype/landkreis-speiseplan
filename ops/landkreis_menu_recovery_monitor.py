#!/usr/bin/env python3
"""Production cron wrapper: original discovery/delivery gates + durable recovery.
No LLM invocation, shell interpolation, chat send, or GitHub write here.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys

REPO = Path('/Users/osiris/Desktop/Projekte/landkreis-speiseplan')
sys.path.insert(0, str(REPO))
import repair_guard as guard
import landkreis_menu_source_monitor as original
from landkreis_target_date import target_menu_date


def main(readonly=False, force=False):
    target = target_menu_date().isoformat()
    ledger = guard.Ledger()
    try:
        # Durable intents outrank monitor hashes AND publication flags. Never silently
        # swallow an interrupted model/repair merely because its output hash was stored.
        if ledger.root.exists():
            pending = [json.loads(p.read_text()) for p in sorted(ledger.root.glob('*.json'))]
            pending = [v for v in pending if v['target'] == target]
            if pending:
                v = pending[-1]
                if v['status'] in ('dispatched', 'awaiting_readback') and not readonly:
                    # Read-only GitHub reconciliation, never a second processing attempt.
                    result = guard.finish(v['id'], ledger)
                    if result['status'] == 'pending':
                        print(f"PHASE=AWAITING_PUBLICATION\nINCIDENT={v['id']}"); return
                    v = ledger.read(v['id'])
                phase = 'REPAIR_VERIFIED' if v['status'] == 'verified' and v['signature'].startswith('PARSE:') else 'BLOCKED'
                if v['status'] not in ('verified', 'notification_intent'):
                    print(f"PHASE=BLOCKED\nINCIDENT={v['id']}\nDETAIL=ATTEMPT_{v['status'].upper()}")
                    return
                if phase == 'REPAIR_VERIFIED':
                    print(f"PHASE=REPAIR_VERIFIED\nINCIDENT={v['id']}"); return
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            original.main(persist=not readonly, force=force)
        text = out.getvalue().strip()
        # A workflow may finish after the original model turn's time budget. Deliver
        # the normal card through the ORIGINAL intent guard, never through this script.
        if ledger.root.exists() and 'PHASE=COMPLETE' in text:
            for p in sorted(ledger.root.glob('*.json')):
                v = json.loads(p.read_text())
                if v['target'] == target and v['status'] == 'verified' and v['signature'] == 'PUBLIC_TARGET_STALE':
                    print(f"PHASE=PUBLICATION_READY\nINCIDENT={v['id']}"); return
        fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        if fields.get('PHASE') != 'UPDATE' or fields.get('STATUS') != 'CURRENT' or 'SOURCE' not in fields:
            print(text); return
        result = guard.inspect(fields['TARGET_DATE'], fields['SOURCE'], ledger, readonly=readonly)
        if result['phase'] == 'PUBLISHED':
            # Existing consumer still prepares the guarded first menu delivery.
            print('\n'.join(line for line in text.splitlines() if not line.startswith('ATTEMPT_SLOT='))); return
        print('PHASE=' + result['phase'])
        print('TARGET_DATE=' + fields['TARGET_DATE'])
        for k in ('incident', 'detail', 'source_sha256'):
            if k in result: print(k.upper() + '=' + result[k])
        if readonly: print('READ_ONLY=true')
    except Exception:
        print('PHASE=TECHNICAL_ERROR\nSTATUS=ERROR\nDETAIL=RECOVERY_STATE_OR_CHECK_FAILED')

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--read-only', action='store_true'); p.add_argument('--check-now', action='store_true')
    a = p.parse_args(); main(a.read_only, a.check_now)

