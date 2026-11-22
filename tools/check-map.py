#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""check-map.py: requirement maps against the products they map onto.

    check-map.py                          the public side (CI)
    check-map.py --map <map.yaml>         a requirement map (kept outside
                                          this repository)
    check-map.py --map <map.yaml> --before <old product.yaml>
    check-map.py --generic-before <old generic.yaml>

Always: row IDs are unique over the rows (products/generic.yaml and
products/qcom.yaml), rows carry no note
(notes belong to a map), and each count file products/counts/<map>.yaml
names only existing rows with counts adding up to its requirements.

--map: every requirement names exactly one existing row; a requirement
target only on a row that runs without one (no row target, no
targets.<row> in the map's board profile); per-row counts and the total
equal products/counts/<map product>.yaml.

--before: the old product map that ran the requirements itself (test,
args, target, note per neutral ID): the same "missing" policy, and
each old requirement is exactly one map requirement (by id) whose row
has the same tests and arguments, the same effective target (the
map's, else the board's targets.<row>, else the row's) and the same
note. Each difference is listed, so an intended change shows by name.

--generic-before: each row of an old generic product is one row of the
current products with the same tests, arguments and target (same ID or
moved to another product).

Exit status 1 on any problem. Needs python3 with PyYAML."""
import argparse
import glob
import json
import os
import sys

import yaml

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOP)
from prd import core  # noqa: E402
ROWS = ('generic', 'qcom')


def parts(r):
    return json.dumps([[p['test'], p.get('args') or {}] for p in r.get('tests') or [r]],
                      sort_keys=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--map')
    ap.add_argument('--before')
    ap.add_argument('--generic-before')
    a = ap.parse_args()
    bad = []
    rows, where = {}, {}
    for pf in (os.path.join(TOP, 'products', '%s.yaml' % p) for p in ROWS):
        for rid, r in yaml.safe_load(open(pf))['requirements'].items():
            if rid in rows:
                bad.append('%s: row %s is also in %s' % (os.path.basename(pf), rid, where[rid]))
            if 'note' in r:
                bad.append('%s %s: rows carry no note (a map does)' % (os.path.basename(pf), rid))
            rows[rid], where[rid] = r, os.path.basename(pf)
    counts = {}
    for cf in sorted(glob.glob(os.path.join(TOP, 'products', 'counts', '*.yaml'))):
        c = yaml.safe_load(open(cf))
        if c.get('map') != os.path.splitext(os.path.basename(cf))[0]:
            bad.append('%s: map is %r, not the file name' % (os.path.basename(cf), c.get('map')))
        counts[c['map']] = c
        for rid, n in c['rows'].items():
            if rid not in rows:
                bad.append('%s: no row %s' % (os.path.basename(cf), rid))
            if not isinstance(n, int) or n < 1:
                bad.append('%s %s: count %r' % (os.path.basename(cf), rid, n))
        if sum(c['rows'].values()) != c['requirements']:
            bad.append('%s: rows add up to %d, not %d requirements' % (
                os.path.basename(cf), sum(c['rows'].values()), c['requirements']))
        print('check-map: count %s: %d requirements on %d rows' % (
            c['map'], c['requirements'], len(c['rows'])))
    if a.map:
        m = yaml.safe_load(open(a.map))
        board = core.load_data(os.path.join(TOP, 'boards', '%s.yaml' % m['board']))
        bt = board.get('targets') or {}
        reqs = m['requirements']
        per = {}
        for pid, e in reqs.items():
            row = e.get('row')
            if not isinstance(row, str) or row not in rows:
                bad.append('map %s (%s): row %r is not one existing row' % (pid, e.get('id'), row))
                continue
            per[row] = per.get(row, 0) + 1
            if 'target' in e and (rows[row].get('target') is not None or row in bt):
                bad.append('map %s: target on row %s, which runs with a target' % (pid, row))
        c = counts.get(m['product'])
        if not c:
            bad.append('no products/counts/%s.yaml' % m['product'])
        else:
            if c['requirements'] != len(reqs):
                bad.append('map has %d requirements, count file %d' % (len(reqs), c['requirements']))
            for rid in sorted(set(per) | set(c['rows'])):
                if per.get(rid, 0) != c['rows'].get(rid, 0):
                    bad.append('row %s: map %d, count file %d' % (rid, per.get(rid, 0),
                                                                  c['rows'].get(rid, 0)))
        print('check-map: map %s: %d requirements, each on one of %d rows' % (
            m['product'], len(reqs), len(per)))
        if a.before:
            oldp = yaml.safe_load(open(a.before))
            old = oldp['requirements']
            if oldp.get('missing', 'fail') != m.get('missing', 'fail'):
                bad.append('before: missing policy %s, map %s' % (oldp.get('missing', 'fail'),
                                                                  m.get('missing', 'fail')))
            byid = {}
            for pid, e in reqs.items():
                if e.get('id') is None:
                    bad.append('map %s: no id to compare with the old map' % pid)
                    continue
                byid.setdefault(e['id'], []).append((pid, e))
            same = 0
            for oid, o in old.items():
                hit = byid.get(oid, [])
                if len(hit) != 1:
                    bad.append('before %s: %d map requirements' % (oid, len(hit)))
                    continue
                pid, e = hit[0]
                r = rows.get(e.get('row'))
                if r is None:
                    continue
                tgt = e['target'] if 'target' in e else bt.get(e['row'], r.get('target'))
                diff = [k for k, x, y in (('tests', parts(o), parts(r)),
                                          ('target', o.get('target'), tgt),
                                          ('note', o.get('note'), e.get('note'))) if x != y]
                if diff:
                    bad.append('before %s -> %s %s: %s differ' % (oid, pid, e['row'], ', '.join(diff)))
                else:
                    same += 1
            extra = set(byid) - set(old)
            if extra:
                bad.append('map requirements not in the old map: %s' % ' '.join(sorted(extra)))
            print('check-map: before: %d of %d requirements run the same tests, arguments, '
                  'target and note' % (same, len(old)))
    if a.generic_before:
        old = yaml.safe_load(open(a.generic_before))['requirements']
        same = 0
        for oid, o in old.items():
            key = (parts(o), json.dumps(o.get('target'), sort_keys=True))
            hit = [rid for rid, r in rows.items()
                   if (parts(r), json.dumps(r.get('target'), sort_keys=True)) == key]
            if oid in hit:
                same += 1
            elif len(hit) == 1:
                same += 1
                print('check-map: generic-before: %s moved to %s %s' % (oid, where[hit[0]], hit[0]))
            else:
                bad.append('generic-before %s: %d matching rows %s' % (oid, len(hit), hit))
        print('check-map: generic-before: %d of %d rows kept' % (same, len(old)))
    for b in bad:
        print('FAIL', b)
    print('check-map: %d problems' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
