#!/usr/bin/env python3
"""Bounded menu recovery. No model/shell dispatch; cron owns the one model turn.

The ledger is independent of Hermes monitor hashes and delivery receipts.
All subprocess argv are fixed code; source/log/lesson contents are never commands.
"""
from __future__ import annotations
import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen

REPO = Path('/Users/osiris/Desktop/Projekte/landkreis-speiseplan')
HOME = Path('/Users/osiris/.hermes')
ROOT = HOME / 'cron/state/landkreis-recovery'
PYTHON = REPO / '.venv/bin/python'
VALIDATOR = HOME / 'scripts/venvs/menu-validation/bin/python'
GH = Path('/Users/osiris/.local/bin/gh')
PARSE = '''import json,sys
from pathlib import Path
from datetime import date
from update_menu import parse_pdf
try:
 d=parse_pdf(Path(sys.argv[1]),sys.argv[2],date.fromisoformat(sys.argv[3]))
 print(json.dumps({'ok':True,'week':d['week']}))
except (ValueError,RuntimeError) as e:
 print(json.dumps({'ok':False,'kind':type(e).__name__,'message':str(e)}))
'''


def run(argv, cwd=REPO, timeout=120):
    p = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError('command failed: ' + str(argv[0]) + ' (exit ' + str(p.returncode) + ')')
    return p.stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.intent-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(name): os.unlink(name)


class Ledger:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / '.lock').open('a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def path(self, key):
        if not re.fullmatch('[a-f0-9]{64}', key): raise ValueError('invalid incident id')
        return self.root / (key + '.json')

    def read(self, key):
        p = self.path(key)
        return json.loads(p.read_text()) if p.exists() else None

    def claim(self, sha, signature, target, source):
        if not re.fullmatch('[a-f0-9]{64}', sha): raise ValueError('invalid PDF hash')
        key = digest((sha + '\n' + signature).encode())
        with self.lock():
            old = self.read(key)
            if old is not None: return key, False
            atomic(self.path(key), dict(id=key, source_sha256=sha, signature=signature,
                   target=target, source=source, status='claimed', attempts=1))
        return key, True

    def transition(self, key, expected, status, **fields):
        with self.lock():
            value = self.read(key)
            if not value or value['status'] not in expected:
                raise ValueError('already attempted, blocked or uncertain; no retry')
            value.update(fields, status=status)
            atomic(self.path(key), value)
            return value


def source_bytes(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.netloc != 'www.landkreis-restaurant.de'
            or not unquote(p.path).lower().endswith('.pdf') or p.fragment
            or any(ord(c) < 32 for c in unquote(url))):
        raise ValueError('invalid source URL')
    with urlopen(Request(url, headers={'User-Agent': 'landkreis-recovery/1'}), timeout=25) as r:
        if r.url != url or r.status != 200: raise ValueError('source redirect/HTTP error')
        data = r.read(8_000_001)
    if len(data) > 8_000_000 or not data.startswith(b'%PDF-'): raise ValueError('invalid/oversized PDF')
    return data


def parse_source(path, source, target, cwd=REPO):
    return json.loads(run([PYTHON, '-c', PARSE, path, source, target], cwd=cwd))


def live(target, record=False):
    # Independent installed iCalendar validator, never candidate worktree code.
    command = '''import json,sys
from datetime import date
from landkreis_validate_live import validate_live,record
v=validate_live(*date.fromisoformat(sys.argv[1]).isocalendar()[:2])
if v['validation_mode']!='pages_and_raw' or not v.get('pages') or v['pages_content_type']!='text/calendar':
 raise ValueError('public Pages readback not current')
if sys.argv[2]=='yes': record(v)
print(json.dumps(v))
'''
    return json.loads(run([VALIDATOR, '-c', command, target, 'yes' if record else 'no'],
                          cwd=HOME / 'scripts', timeout=100))


def decision(pdf, parsed, target, source, ledger, readonly=False):
    sha = digest(pdf)
    # Signature has no run IDs, hours, log lines or model-supplied strings.
    if parsed.get('ok') is True:
        signature = 'PUBLIC_TARGET_STALE'
        phase = 'UPDATE'
    else:
        # Only deterministic parser errors are eligible, not network/auth/model errors.
        if parsed.get('kind') not in ('ValueError', 'RuntimeError'): raise ValueError('not a parser failure')
        signature = 'PARSE:' + parsed['kind'] + ':' + digest(parsed['message'].encode())
        phase = 'REPAIR'
    key = digest((sha + '\n' + signature).encode())
    if readonly: return dict(phase=phase, incident=key, readonly=True, source_sha256=sha)
    key, won = ledger.claim(sha, signature, target, source)
    if won:
        ledger.transition(key, {'claimed'}, 'claimed', processing_result=parsed)
        # Claim precedes ALL model work. A model failure cannot consume success.
        (ledger.root / (sha + '.pdf')).write_bytes(pdf)
        return dict(phase=phase, incident=key, source_sha256=sha)
    value = ledger.read(key)
    return dict(phase='COMPLETE' if value['status'] == 'verified' else 'BLOCKED',
                incident=key, detail='ATTEMPT_' + value['status'].upper())


def public_is_stale(target):
    command = '''import json,sys
from datetime import date,datetime
from icalendar import Calendar
from landkreis_validate_live import fetch,PAGES
b,t=fetch(PAGES)
if t!='text/calendar': raise ValueError('wrong public content type')
s=[e.decoded('DTSTART') for e in Calendar.from_ical(b).walk('VEVENT')]
if len(s)!=5 or not all(isinstance(x,datetime) for x in s): raise ValueError('invalid public events')
target=date.fromisoformat(sys.argv[1]).isocalendar()[:2]
print(json.dumps(all(x.date().isocalendar()[:2]<target for x in s)))
'''
    return json.loads(run([VALIDATOR, '-c', command, target], cwd=HOME / 'scripts', timeout=40))


def inspect(target, source, ledger=None, readonly=False):
    ledger = ledger or Ledger()
    # Reuse a prior claim for the same detection: no source polling or second model attempt.
    if ledger.root.exists():
        for p in sorted(ledger.root.glob('*.json')):
            v = json.loads(p.read_text())
            if v['target'] == target and v['source'] == source:
                return dict(phase='COMPLETE' if v['status'] == 'verified' else 'BLOCKED',
                            incident=v['id'], detail='ATTEMPT_' + v['status'].upper())
    try:
        live(target, record=not readonly)
        return dict(phase='PUBLISHED')
    except (RuntimeError, subprocess.TimeoutExpired, ValueError):
        if not public_is_stale(target):
            raise ValueError('public validation failed without stale target week; technical blocker')
    pdf = source_bytes(source)
    with tempfile.TemporaryDirectory(prefix='menu-inspect-') as d:
        path = Path(d) / 'source.pdf'; path.write_bytes(pdf)
        parsed = parse_source(path, source, target)
    return decision(pdf, parsed, target, source, ledger, readonly)


def begin(key, ledger):
    v = ledger.read(key)
    if not v or not v['signature'].startswith('PARSE:'): raise ValueError('no real parsing failure')
    # Intent durable BEFORE worktree creation; interrupted creation is never retried.
    run(['git', 'fetch', 'origin', 'main'])
    base = run(['git', 'rev-parse', 'origin/main']).strip()
    worktree = ledger.root / ('worktree-' + key)
    ledger.transition(key, {'claimed'}, 'editing', base=base, worktree=str(worktree))
    run(['git', 'worktree', 'add', '--detach', worktree, base])
    return dict(worktree=str(worktree), pdf=str(ledger.root / (v['source_sha256'] + '.pdf')))


def parser_scope(before, after):
    """Only period-pattern assignment may change; every date/week guard frozen."""
    a, b = ast.parse(before), ast.parse(after)
    aa = next(n for n in a.body if isinstance(n, ast.FunctionDef) and n.name == 'parse_period')
    bb = next(n for n in b.body if isinstance(n, ast.FunctionDef) and n.name == 'parse_period')
    if not isinstance(bb.body[0], ast.Assign): raise ValueError('invalid parser edit')
    # Pattern is a literal argument to the exact original re.findall call.
    old, new = aa.body[0].value, bb.body[0].value
    if not isinstance(new, ast.Call) or not new.args or not isinstance(new.args[0], ast.Constant) or not isinstance(new.args[0].value, str):
        raise ValueError('only literal period regex repair allowed')
    new.args[0] = old.args[0]
    if ast.dump(a) != ast.dump(b): raise ValueError('repair exceeds period syntax scope / changes validation')


def check_scope(worktree, base):
    # Include untracked files; no generated artifacts, hooks, workflows or hidden files.
    changed = run(['git', 'diff', '--name-only', base], cwd=worktree).splitlines()
    untracked = run(['git', 'ls-files', '--others', '--exclude-standard'], cwd=worktree).splitlines()
    names = sorted(set(changed + untracked))
    if 'update_menu.py' not in names or 'lessons.md' not in names:
        raise ValueError('repair requires parser change and lesson')
    base_tests = set(run(['git', 'ls-tree', '-r', '--name-only', base, 'tests'], cwd=worktree).splitlines())
    if not any(n.startswith('tests/test_repair_') and n.endswith('.py') for n in names):
        raise ValueError('new regression test required')
    for n in names:
        p = Path(n)
        if p.is_absolute() or '..' in p.parts or (worktree / p).is_symlink(): raise ValueError('unsafe path')
        if n in base_tests: raise ValueError('existing tests/fixtures are immutable')
        if n not in ('update_menu.py', 'lessons.md') and not re.fullmatch(r'tests/test_repair_[a-z0-9_]+\.py|tests/fixtures/repair_[a-z0-9_]+\.pdf|docs/[a-z0-9_-]+\.md', n):
            raise ValueError('out-of-scope file: ' + n)
        if not (worktree / n).is_file(): raise ValueError('deletions forbidden')
    parser_scope(run(['git', 'show', base + ':update_menu.py'], cwd=worktree), (worktree / 'update_menu.py').read_text())
    before = run(['git', 'show', base + ':lessons.md'], cwd=worktree)
    if not (worktree / 'lessons.md').read_text().startswith(before): raise ValueError('lessons must append, not rewrite')
    return names


def verify(key, ledger):
    v = ledger.transition(key, {'editing'}, 'testing')
    w = Path(v['worktree']); names = check_scope(w, v['base'])
    # Unchanged tests run with candidate parser. Existing tracked tests cannot be weakened.
    tests = run([PYTHON, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=w, timeout=120)
    if digest((ledger.root / (v['source_sha256'] + '.pdf')).read_bytes()) != v['source_sha256']:
        raise ValueError('saved source hash changed')
    parsed = parse_source(ledger.root / (v['source_sha256'] + '.pdf'), v['source'], v['target'], cwd=w)
    if not parsed.get('ok'): raise ValueError('source still fails')
    # Pin every byte validated, including newly added tests and docs.
    hashes = {n: digest((w / n).read_bytes()) for n in names}
    ledger.transition(key, {'testing'}, 'tested', files=hashes, tests='unittest discover exit 0', parse=parsed)
    return dict(status='tested', files=names)


def publish(key, ledger):
    v = ledger.read(key)
    if not v or v['status'] != 'tested': raise ValueError('independent tests required')
    w = Path(v['worktree']); names = check_scope(w, v['base'])
    if {n: digest((w / n).read_bytes()) for n in names} != v['files']: raise ValueError('candidate changed after tests')
    remote = run(['git', 'ls-remote', 'origin', 'refs/heads/main'], cwd=w).split()[0]
    if remote != v['base']: raise ValueError('main advanced; no automatic rebase/retry')
    ledger.transition(key, {'tested'}, 'push_intent')
    run(['git', 'add', '--', *names], cwd=w)
    run(['git', 'commit', '-m', 'fix: bounded menu period recovery ' + key[:12]], cwd=w)
    sha = run(['git', 'rev-parse', 'HEAD'], cwd=w).strip()
    run(['git', 'push', 'origin', 'HEAD:refs/heads/main'], cwd=w)
    if run(['git', 'ls-remote', 'origin', 'refs/heads/main'], cwd=w).split()[0] != sha:
        raise ValueError('push readback uncertain')
    if run(['git', 'status', '--porcelain']).strip(): raise ValueError('local checkout dirty; no automatic sync')
    run(['git', 'fetch', 'origin', 'main'])
    run(['git', 'merge', '--ff-only', sha])
    ledger.transition(key, {'push_intent'}, 'code_published', commit=sha)
    return dict(status='code_published', commit=sha)


def dispatch(key, ledger):
    v = ledger.read(key)
    expected = {'code_published'} if v['signature'].startswith('PARSE:') else {'claimed'}
    # Persist intent before API. Never repeat dispatch on ambiguous timeout.
    if v['status'] not in expected: raise ValueError('dispatch already attempted')
    sha = run(['git', 'ls-remote', 'origin', 'refs/heads/main']).split()[0]
    if v.get('commit') and sha != v['commit']: raise ValueError('main advanced before dispatch')
    ledger.transition(key, expected, 'dispatch_intent', dispatch_sha=sha)
    before = json.loads(run([GH, 'run', 'list', '--repo', 'bigzype/landkreis-speiseplan', '--workflow', 'update-speiseplan.yml', '--limit', '30', '--json', 'databaseId']))
    run([GH, 'workflow', 'run', 'update-speiseplan.yml', '--repo', 'bigzype/landkreis-speiseplan', '--ref', 'main'])
    ledger.transition(key, {'dispatch_intent'}, 'dispatched', prior_run_ids=[x['databaseId'] for x in before])
    return dict(status='dispatched', note='Use finish to reconcile exact run and public readback. Never dispatch again.')


def verify_published_source(sha):
    url = 'https://raw.githubusercontent.com/bigzype/landkreis-speiseplan/main/Speiseplan.pdf'
    with urlopen(Request(url, headers={'Cache-Control':'no-cache'}), timeout=25) as r:
        data = r.read(8_000_001)
        if r.status != 200 or r.url != url or len(data) > 8_000_000 or digest(data) != sha:
            raise ValueError('published source differs from tested incident PDF')


def finish(key, ledger):
    v = ledger.read(key)
    if not v or v['status'] not in {'dispatched', 'awaiting_readback'}: raise ValueError('not awaiting workflow')
    runs = json.loads(run([GH, 'run', 'list', '--repo', 'bigzype/landkreis-speiseplan', '--workflow', 'update-speiseplan.yml', '--limit', '30', '--json', 'databaseId,status,conclusion,headSha,event']))
    candidates = [r for r in runs if r['databaseId'] not in v['prior_run_ids'] and r['event'] == 'workflow_dispatch']
    if len(candidates) != 1: raise ValueError('workflow identity ambiguous; no redispatch')
    r = candidates[0]
    if r['headSha'] != v['dispatch_sha']: raise ValueError('workflow SHA mismatch')
    if r['status'] != 'completed': return dict(status='pending', run_id=r['databaseId'])
    if r['conclusion'] != 'success':
        ledger.transition(key, {'dispatched','awaiting_readback'}, 'failed', run_id=r['databaseId'])
        return dict(status='failed', run_id=r['databaseId'])
    ledger.transition(key, {'dispatched','awaiting_readback'}, 'awaiting_readback', run_id=r['databaseId'])
    verify_published_source(v['source_sha256'])
    result = live(v['target'], record=True)
    ledger.transition(key, {'awaiting_readback'}, 'verified', publication={k:x for k,x in result.items() if k != 'text'})
    return dict(status='verified', run_id=r['databaseId'], publication=result)


def main():
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['inspect','begin','verify','publish','dispatch','finish','status','notify-intent'])
    p.add_argument('--incident'); p.add_argument('--target'); p.add_argument('--source'); p.add_argument('--read-only', action='store_true')
    a = p.parse_args(); ledger = Ledger()
    try:
        if a.action == 'inspect': result = inspect(a.target, a.source, ledger, a.read_only)
        elif a.action == 'status': result = ledger.read(a.incident)
        elif a.action == 'notify-intent':
            result = ledger.transition(a.incident, {'verified'}, 'notification_intent')
        else: result = globals()[a.action](a.incident, ledger)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        # Preserve last durable intent: exceptions NEVER reset or create another attempt.
        print(json.dumps({'status':'blocked','error':str(e)}, ensure_ascii=False)); raise SystemExit(1)

if __name__ == '__main__': main()
