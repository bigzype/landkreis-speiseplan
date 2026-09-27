"""Explicit operator-authorized follow-up, never called by automatic recovery.
A failed parent is immutable. One child per parent, independent of code changes.
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
import repair_guard as g

AUTHORIZATION = 'Kai explicitly authorized Landkreis repair including one publication in chat; manual follow-up, no automatic retry.'


def child_id(parent):
    return g.digest(('MANUALLY_AUTHORIZED_FOLLOWUP_V1\n' + parent).encode())


def authorize(parent, code, ledger):
    if not re.fullmatch('[a-f0-9]{40}', code):
        raise ValueError('exact code SHA required')
    with ledger.lock():
        p = ledger.read(parent)
        key = child_id(parent)
        if not p or p['status'] != 'failed' or p.get('parent'):
            raise ValueError('terminal failed root required')
        if ledger.read(key):
            raise ValueError('manual follow-up already exists; never reset')
        source = ledger.root / (p['source_sha256'] + '.pdf')
        if g.digest(source.read_bytes()) != p['source_sha256']:
            raise ValueError('cached source mismatch')
        if g.run(['git', 'ls-remote', 'origin', 'refs/heads/main']).split()[0] != code:
            raise ValueError('main/code mismatch')
        result = g.parse_source(source, p['source'], p['target'])
        if not result.get('ok'):
            raise ValueError('source failed independent validation')
        v = dict(id=key, parent=parent, parent_sha256=g.digest(ledger.path(parent).read_bytes()),
                 authorization=AUTHORIZATION, signature=p['signature'], source=p['source'],
                 source_sha256=p['source_sha256'], target=p['target'], commit=code,
                 status='manual_authorized', attempts=1)
        g.atomic(ledger.path(key), v)
    return v


def parent_intact(v, ledger):
    if g.digest(ledger.path(v['parent']).read_bytes()) != v['parent_sha256']:
        raise ValueError('parent changed')


def dispatch(key, ledger):
    v = ledger.read(key)
    if not v or v['status'] != 'manual_authorized':
        raise ValueError('no unused manual authorization')
    parent_intact(v, ledger)
    if g.run(['git', 'ls-remote', 'origin', 'refs/heads/main']).split()[0] != v['commit']:
        raise ValueError('main advanced')
    payload = dict(ref='main', inputs=dict(recovery_id=key, code_sha=v['commit'],
                   source_sha=v['source_sha256'], source_url=v['source'], target=v['target']))
    # Full exact payload is durable before the network call; timeout never retries.
    ledger.transition(key, {'manual_authorized'}, 'dispatch_intent', payload=payload,
                      dispatch_sha=v['commit'])
    p = subprocess.run([str(g.GH), 'api', '--method', 'POST',
        'repos/bigzype/landkreis-speiseplan/actions/workflows/update-speiseplan.yml/dispatches',
        '--input', '-'], input=json.dumps(payload), text=True, capture_output=True, timeout=120)
    if p.returncode:
        raise RuntimeError('dispatch response uncertain; reconcile only')
    return ledger.transition(key, {'dispatch_intent'}, 'dispatched')


def finish(key, ledger):
    v = ledger.read(key)
    if not v or v['status'] not in {'dispatch_intent', 'dispatched', 'awaiting_readback'}:
        raise ValueError('no pending manual dispatch')
    parent_intact(v, ledger)
    runs = json.loads(g.run([g.GH, 'run', 'list', '--repo', 'bigzype/landkreis-speiseplan',
        '--workflow', 'update-speiseplan.yml', '--limit', '100', '--json',
        'databaseId,status,conclusion,headSha,event,displayTitle']))
    matches = [r for r in runs if r['displayTitle'] == 'Manual recovery ' + key]
    if len(matches) != 1:
        raise ValueError('exact run absent/ambiguous; never redispatch')
    r = matches[0]
    if r['headSha'] != v['commit'] or r['event'] != 'workflow_dispatch' or (v.get('run_id') and v['run_id'] != r['databaseId']):
        raise ValueError('run identity mismatch')
    states = {'dispatch_intent', 'dispatched', 'awaiting_readback'}
    if r['status'] != 'completed':
        return dict(status='pending', run_id=r['databaseId'])
    if r['conclusion'] != 'success':
        return ledger.transition(key, states, 'failed', run_id=r['databaseId'])
    ledger.transition(key, states, 'awaiting_readback', run_id=r['databaseId'])
    g.verify_published_source(v['source_sha256'])
    result = g.live(v['target'], record=True)
    if result['raw_ics_sha256'] != result['pages_ics_sha256']:
        raise ValueError('raw/pages bytes differ')
    return ledger.transition(key, {'awaiting_readback'}, 'verified',
        publication={k:x for k,x in result.items() if k != 'text'})


def workflow_source():
    """Run only in the authoritative workflow; use committed cached real bytes."""
    key, code, sha = (os.environ.get(k, '') for k in ('RECOVERY_ID','CODE_SHA','SOURCE_SHA'))
    if not re.fullmatch('[a-f0-9]{64}', key) or not re.fullmatch('[a-f0-9]{40}', code) or not re.fullmatch('[a-f0-9]{64}', sha):
        raise ValueError('invalid pins')
    if g.run(['git','rev-parse','HEAD']).strip() != code or os.environ.get('GITHUB_SHA') != code:
        raise ValueError('workflow code changed')
    source = g.REPO / 'tests/fixtures' / ('recovery_' + sha + '.pdf')
    if g.digest(source.read_bytes()) != sha:
        raise ValueError('source mismatch')
    from datetime import date
    from menu_source import target_menu_date
    if date.fromisoformat(os.environ['TARGET']) != target_menu_date():
        raise ValueError('target no longer current')
    result = g.parse_source(source, os.environ['SOURCE_URL'], os.environ['TARGET'])
    if not result.get('ok'):
        raise ValueError('independent validation failed')
    print(g.run([g.PYTHON, 'update_menu.py', '--pdf', source, '--source-url', os.environ['SOURCE_URL']]))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['authorize','dispatch','finish','workflow-source'])
    p.add_argument('--incident'); p.add_argument('--code-sha')
    a = p.parse_args()
    if a.action == 'workflow-source': workflow_source()
    elif a.action == 'authorize': print(json.dumps(authorize(a.incident, a.code_sha, g.Ledger())))
    else: print(json.dumps(globals()[a.action](a.incident, g.Ledger())))
