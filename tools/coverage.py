#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""coverage.py: per requirement results of a requirement map from runs of
the products it maps onto.

    coverage.py --map <map.yaml> [--lava https://<lava>] SOURCE [SOURCE ...]

SOURCE is a LAVA job id (its public plain log) or a results.jsonl of
tools/run.py, from a run of the rows (products/generic.yaml and
products/qcom.yaml) on a board. Each requirement of the map (see
tools/check-map.py) takes its row's result as the board reported it,
then the map's policy: under "missing: fail" a part skipped because its
interface is missing fails, as a product run with that policy would
report it; then the map's target (applied only to a row the board ran
without a target) and note. A requirement whose row no source ran is
"not run"; one without a row is GAP; one whose reason a console line
break in a LAVA log cut where the policy reads it (inside the " || "
part separator or the "interface absent: " prefix) is "unparsed", which
makes the exit status 1.
results.jsonl has none of these limits; read from a LAVA log, a list
measurement of one item comes back as that item. A LAVA source must
have run on the map's board. Markdown on stdout: one line per
requirement and source, with a link to the log line. Needs python3
with PyYAML."""
import argparse
import importlib.util
import json
import os
import re
import sys

import yaml

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOP)
from prd import core  # noqa: E402
spec = importlib.util.spec_from_file_location('report', os.path.join(TOP, 'tools', 'report.py'))
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
MEAS = re.compile(r'(\S+?)=(.*?)(?= \S+?=|$)')


def value(v):
    if ',' in v:
        return [value(x) for x in v.split(',')]
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    return {'True': True, 'False': False}.get(v, v)


def from_lava(lava, job):
    out = {}
    for e in report.parse(report.plain_log(lava, job)):
        if e[0] == 'result':
            meas = {k: value(v) for k, v in MEAS.findall(e[5] or '')}
            out[e[2]] = (e[3], e[4], meas, '%s/scheduler/job/%s#L%d' % (lava.rstrip('/'), job, e[1]),
                         e[6], job)
    return out


def from_jsonl(path):
    out = {}
    for line in open(path):
        r = json.loads(line)
        out[r['id']] = (r['status'], r['reason'], r.get('meas') or {}, None, None, path)
    return out


def derive(row, res, ent, missing):
    """Requirement status and reason from its row's (status, reason, meas)."""
    st, why, meas = res[:3]
    if missing != 'skip' and why.count(core.ABSENT) != \
            re.sub(r'[\s|]', '', why).count(core.ABSENT.replace(' ', '')):
        return 'unparsed', '%s [a line break cuts "%s"]' % (why, core.ABSENT.strip())
    if missing != 'skip':
        try:
            st, why = core.as_missing_fail([p['test'] for p in row.get('tests') or [row]], st, why)
        except ValueError as e:
            return 'unparsed', '%s [%s]' % (why, e)
    return core.settle(st, why, meas, ent.get('target'), ent.get('note'))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--map', required=True)
    ap.add_argument('--lava', default='https://lava.infra.foundries.io')
    ap.add_argument('sources', nargs='+')
    a = ap.parse_args()
    m = yaml.safe_load(open(a.map))
    rows = {}
    for pf in ('generic', 'qcom'):  # the rows, as in tools/check-map.py
        rows.update(yaml.safe_load(open(os.path.join(TOP, 'products', pf + '.yaml')))['requirements'])
    srcs = [from_jsonl(s) if s.endswith('.jsonl') else from_lava(a.lava, s) for s in a.sources]
    for s, res in zip(a.sources, srcs):
        boards = set(r[4] for r in res.values() if r[4] is not None)
        if boards - {m['board']}:
            sys.exit('%s ran on %s, the map is for %s' % (s, ' '.join(sorted(boards)), m['board']))
    counts = {}
    out = ['| requirement | ID | title | row | result | reason | log |', '|---|---|---|---|---|---|---|']
    for pid, ent in m['requirements'].items():
        row = rows.get(ent.get('row'))
        hits = [s[ent['row']] for s in srcs if row and ent['row'] in s]
        if row is None or not hits:
            st = 'GAP' if row is None else 'not run'
            counts[st] = counts.get(st, 0) + 1
            out.append('| %s | %s | %s | %s | %s | | |' % (pid, ent.get('id', ''), ent.get('title', ''),
                                                        ent.get('row') or '-', st))
            continue
        for res in hits:
            st, why = derive(row, res, ent, m.get('missing', 'fail'))
            counts[st] = counts.get(st, 0) + 1
            link = '[%s](%s)' % (res[3].rsplit('/', 1)[1], res[3]) if res[3] else ''
            out.append('| %s | %s | %s | %s | %s%s | %s | %s |' % (
                pid, ent.get('id', ''), ent.get('title', ''), ent['row'], st,
                ' (%s)' % res[5] if len(srcs) > 1 else '', why.replace('|', '/'), link))
    print('Counts: ' + ', '.join('%s %d' % kv for kv in sorted(counts.items())))
    print('')
    print('\n'.join(out))
    return 1 if counts.get('unparsed') else 0


if __name__ == '__main__':
    sys.exit(main())
