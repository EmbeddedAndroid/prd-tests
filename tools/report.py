#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""report.py: results table of a product run from LAVA job logs.

    report.py --product products/<p>.yaml --lava https://<lava> JOB [JOB ...]

Reads each job's public plain log, takes the "PRD <ID> <status>: <reason>"
lines the runner printed and writes a markdown table on stdout: one row
per requirement of the product with the status, the reason and a link
to the exact log line (#L anchor = 0-based log entry; a reason that
went on over several console lines is joined and linked at its first
line). A requirement
missing from every job is listed as "not run". With several jobs a
requirement's row cites every job that ran it. Needs python3 with PyYAML."""
import argparse
import os
import re
import sys
import urllib.request

import yaml

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINE = re.compile(r'PRD ([A-Z]+-\d+) (pass|partial|fail|skip): (.*?)(?: \[(.*)\])? \([\d.]+ s\)$')
START = re.compile(r'PRD [A-Z]+-\d+ (pass|partial|fail|skip): ')
END = re.compile(r'\([\d.]+ s\)$')


def entries(text):
    """(index, msg) of every target line; the index is the #L anchor."""
    for i, raw in enumerate(text.splitlines()):
        if '"lvl": "target"' not in raw and "lvl: target" not in raw:
            continue
        try:
            e = yaml.safe_load(raw)[0]
        except Exception:
            continue
        m = e.get('msg') if isinstance(e, dict) else None
        if isinstance(m, str):
            yield i, m


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--product', required=True)
    ap.add_argument('--lava', required=True)
    ap.add_argument('jobs', nargs='+')
    a = ap.parse_args()
    prod = yaml.safe_load(open(a.product))
    res = {}
    meta = []
    for j in a.jobs:
        url = '%s/scheduler/job/%s/log_file/plain' % (a.lava.rstrip('/'), j)
        text = urllib.request.urlopen(url, timeout=120).read().decode('utf-8', 'replace')

        def take(i, msg):
            m = LINE.search(msg)
            if m:
                res.setdefault(m.group(1), []).append(
                    (m.group(2), m.group(3), '%s/scheduler/job/%s#L%d' % (a.lava.rstrip('/'), j, i),
                     meta_board[0]))
        meta_board = ['?']
        pend = None     # a PRD line whose reason went on over console lines
        for i, msg in entries(text):
            if pend and (START.match(msg) or msg.startswith('=== ')):
                # never terminated: keep what was printed, end at the next result
                take(pend[0], pend[1] + ' (0.0 s)')
                pend = None
            if pend:
                pend = (pend[0], pend[1] + ' | ' + msg.strip())
                if not END.search(msg):
                    continue
                i, msg = pend
                pend = None
            elif START.match(msg) and not LINE.search(msg):
                pend = (i, msg)
                continue
            m = re.search(r'PRD BOARD (\S+) PRODUCT \S+ REV (\S+)', msg)
            if m:
                meta_board[0] = m.group(1)
                meta.append('[%s](%s/scheduler/job/%s#L%d): board %s, tests %s' % (
                    j, a.lava.rstrip('/'), j, i, m.group(1), m.group(2)))
            take(i, msg)
        if pend:
            take(pend[0], pend[1] + ' (0.0 s)')
    out = ['Jobs: ' + '; '.join(meta), '']
    counts = {}
    out.append('| ID | requirement | result | reason | log |')
    out.append('|---|---|---|---|---|')
    for rid, req in prod['requirements'].items():
        rs = res.get(rid)
        if not rs:
            counts['not run'] = counts.get('not run', 0) + 1
            out.append('| %s | %s | not run | | |' % (rid, req.get('title', '')))
            continue
        for st, why, link, board in rs:
            counts[st] = counts.get(st, 0) + 1
            out.append('| %s | %s | %s%s | %s | [%s](%s) |' % (
                rid, req.get('title', ''), st, ' (%s)' % board if len(a.jobs) > 1 else '',
                why.replace('|', '/'), link.rsplit('/', 1)[1], link))
    out.insert(1, 'Counts: ' + ', '.join('%s %d' % kv for kv in sorted(counts.items())))
    print('\n'.join(out))


if __name__ == '__main__':
    sys.exit(main())
