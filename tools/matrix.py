#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""matrix.py: test x platform matrix of a product run on several boards.

    matrix.py --product products/generic.yaml [--lava https://<lava>]
              <label>=<LAVA job id | results.jsonl> ...

One row per requirement (its test), one column per platform, each cell
the status (pass, partial, fail, skip) linked to the LAVA log line where
the source is a job; a summary row counts each status per platform.
Markdown on stdout. Needs python3 with PyYAML."""
import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.request

import yaml

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('report', os.path.join(TOP, 'tools', 'report.py'))
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def from_jsonl(path):
    out = {}
    for line in open(path):
        r = json.loads(line)
        out[r['id']] = (r['status'], None)
    return out


def from_lava(lava, job):
    url = '%s/scheduler/job/%s/log_file/plain' % (lava.rstrip('/'), job)
    text = urllib.request.urlopen(url, timeout=120).read().decode('utf-8', 'replace')
    out, pend = {}, None
    for i, msg in report.entries(text):
        if pend and (report.START.match(msg) or msg.startswith('=== ')):
            pend = None
        if pend:
            pend = (pend[0], pend[1] + ' ' + msg.strip())
            if not report.END.search(msg):
                continue
            i, msg = pend
            pend = None
        elif report.START.match(msg) and not report.LINE.search(msg):
            pend = (i, msg)
            continue
        m = report.LINE.search(msg)
        if m:
            out[m.group(1)] = (m.group(2), '%s/scheduler/job/%s#L%d' % (lava.rstrip('/'), job, i))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--product', required=True)
    ap.add_argument('--lava', default='https://lava.infra.foundries.io')
    ap.add_argument('sources', nargs='+')
    a = ap.parse_args()
    prod = yaml.safe_load(open(a.product))
    cols = []
    for s in a.sources:
        label, src = s.split('=', 1)
        res = from_jsonl(src) if src.endswith('.jsonl') else from_lava(a.lava, src)
        cols.append((label, res))
    out = ['| ID | test | ' + ' | '.join(c[0] for c in cols) + ' |',
           '|---|---|' + '---|' * len(cols)]
    counts = [{} for _ in cols]
    for rid, req in prod['requirements'].items():
        test = req.get('test') or '+'.join(p['test'] for p in req.get('tests', []))
        args = req.get('args') or {}
        brief = ' '.join('%s' % v for k, v in sorted(args.items()) if k in ('prop', 'role', 'kind',
                                                                         'direction', 'codec', 'cls', 'n'))
        cells = []
        for k, (label, res) in enumerate(cols):
            st, link = res.get(rid, ('not run', None))
            counts[k][st] = counts[k].get(st, 0) + 1
            cells.append('[%s](%s)' % (st, link) if link else st)
        out.append('| %s | %s%s | %s |' % (rid, test, ' ' + brief if brief else '', ' | '.join(cells)))
    order = ('pass', 'partial', 'fail', 'skip', 'not run')
    out.append('| | total | ' + ' | '.join(
        ', '.join('%s %d' % (s, c[s]) for s in order if s in c) for c in counts) + ' |')
    print('\n'.join(out))


if __name__ == '__main__':
    sys.exit(main())
