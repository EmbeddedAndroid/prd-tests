#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""report.py: results table of a product run from LAVA job logs.

    report.py --product products/<p>.yaml [--product ...] --lava https://<lava> JOB [JOB ...]

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
# the measurements are the last " [...]" (no brackets inside); reasons
# may hold bracketed lists
LINE = re.compile(r'PRD ([A-Z]+-\d+) (pass|partial|fail|skip): (.*?)(?: \[([^\[\]]*)\])? \([\d.]+ s\)$')
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


BOARD = re.compile(r'PRD BOARD (\S+) PRODUCT \S+ REV (\S+)')


def parse(text):
    """Every runner line of a job's plain log, in order: ('board', index,
    board, rev) for the PRD BOARD line and ('result', index, id, status,
    reason, measurements text or None, board) for each PRD result line.
    A reason that went on over several console lines is joined with " | "
    and keeps the index of its first line; one never terminated ends at
    the next result."""
    board = '?'
    pend = None

    def result(i, msg):
        m = LINE.search(msg)
        if m:
            return ('result', i, m.group(1), m.group(2), m.group(3), m.group(4), board)
        return None
    for i, msg in entries(text):
        if pend and (START.match(msg) or msg.startswith('=== ')):
            r = result(pend[0], pend[1] + ' (0.0 s)')
            pend = None
            if r:
                yield r
        if pend:
            pend = (pend[0], pend[1] + ' | ' + msg.strip())
            if not END.search(msg):
                continue
            i, msg = pend
            pend = None
        elif START.match(msg) and not LINE.search(msg):
            pend = (i, msg)
            continue
        m = BOARD.search(msg)
        if m:
            board = m.group(1)
            yield ('board', i, m.group(1), m.group(2))
        r = result(i, msg)
        if r:
            yield r
    if pend:
        r = result(pend[0], pend[1] + ' (0.0 s)')
        if r:
            yield r


def plain_log(lava, job):
    url = '%s/scheduler/job/%s/log_file/plain' % (lava.rstrip('/'), job)
    return urllib.request.urlopen(url, timeout=120).read().decode('utf-8', 'replace')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--product', required=True, action='append')
    ap.add_argument('--lava', required=True)
    ap.add_argument('jobs', nargs='+')
    a = ap.parse_args()
    reqs = {}
    for pf in a.product:
        for rid, req in yaml.safe_load(open(pf))['requirements'].items():
            if rid in reqs:
                ap.error('%s: requirement %s is in an earlier product too' % (pf, rid))
            reqs[rid] = req
    res = {}
    meta = []
    base = a.lava.rstrip('/')
    for j in a.jobs:
        for e in parse(plain_log(a.lava, j)):
            if e[0] == 'board':
                meta.append('[%s](%s/scheduler/job/%s#L%d): board %s, tests %s' % (
                    j, base, j, e[1], e[2], e[3]))
            else:
                res.setdefault(e[2], []).append(
                    (e[3], e[4], '%s/scheduler/job/%s#L%d' % (base, j, e[1]), e[6]))
    out = ['Jobs: ' + '; '.join(meta), '']
    counts = {}
    out.append('| ID | requirement | result | reason | log |')
    out.append('|---|---|---|---|---|')
    for rid, req in reqs.items():
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
