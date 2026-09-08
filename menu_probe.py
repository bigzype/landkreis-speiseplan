#!/usr/bin/env python3
"""Read-only JSON probe. See README for exact schema and stable reason codes."""
from __future__ import annotations

import argparse
from datetime import date
import json
import re

import requests

from menu_fetch import ProbeError, probe_menu, probe_menu_filename_first, validate_previous_source
from menu_source import target_menu_date


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    expected = target_menu_date()
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument('--expected', metavar='YYYY-MM-DD', help='Zieldatum; Standard: Berlin, Sonntag kommende Woche')
        parser.add_argument('--filename-first', action='store_true',
                            help='Dateiname zuerst prüfen; PDF-Inhalt nur als Fallback')
        parser.add_argument('--previous-source', metavar='URL',
                            help='Zuletzt erfolgreich verwendeter PDF-Link; nur mit --filename-first')
        args = parser.parse_args(argv)
        if args.previous_source is not None:
            if not args.filename_first:
                raise ValueError('--previous-source benötigt --filename-first')
            validate_previous_source(args.previous_source)
        if args.expected is not None:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', args.expected):
                raise ValueError('--expected muss YYYY-MM-DD sein')
            expected = date.fromisoformat(args.expected)
    except ValueError as exc:
        print(json.dumps({'status': 'error', 'expected_date': expected.isoformat(),
                          'reason': 'INVALID_ARGUMENT', 'detail': str(exc)}, ensure_ascii=False))
        return 1
    session = requests.Session()
    session.headers['User-Agent'] = 'landkreis-speiseplan-probe/1.0'
    try:
        if args.filename_first:
            report = probe_menu_filename_first(session, expected, previous_source=args.previous_source).report
        else:
            report = probe_menu(session, expected).report
    except ProbeError as exc:
        report = {'status': 'error', 'expected_date': expected.isoformat(),
                  'reason': exc.reason, 'detail': str(exc)}
    except Exception as exc:
        report = {'status': 'error', 'expected_date': expected.isoformat(),
                  'reason': 'INTERNAL_ERROR', 'detail': str(exc)}
    finally:
        session.close()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
