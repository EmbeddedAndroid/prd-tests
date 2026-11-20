#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""expect.py <outdir> <full|bare> [board]: the CI verdict on a virtual
run. Every requirement of the product must have a result. full: each
must pass, be partial, or skip with the board's absent reason; a fail is
allowed only when boards/virtual.yaml lists it under known_fail with
the reason. bare (no virtual drivers
loaded): no requirement of an interface the drivers provide may pass,
(or be partial), which shows the tests depend on the hardware being there; requirements
the board lists under hardware_free (user space only) are exempt. Exit 1
on any violation."""
import json
import os
import sys

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, TOP)
from prd import core  # noqa: E402

out, mode = sys.argv[1], sys.argv[2]
board = core.load_data(sys.argv[3] if len(sys.argv) > 3 else os.path.join(TOP, 'boards', 'virtual.yaml'))
product = core.load_data(sys.argv[4] if len(sys.argv) > 4 else os.path.join(TOP, 'products', 'generic.yaml'))
res = [json.loads(l) for l in open(os.path.join(out, 'results.jsonl'))]
bad = []
seen = set(r['id'] for r in res)
for rid in product['requirements']:
    if rid not in seen:
        bad.append('%s has no result: the run stopped early' % rid)
if mode == 'full':
    known = board.get('known_fail') or {}
    for r in res:
        if r['status'] == 'fail' and r['id'] not in known:
            bad.append('%s %s fail: %s' % (r['id'], r.get('test'), r['reason'][:200]))
        # a skip must carry the board's reason; "interface absent" alone
        # means a driver did not come up
        if r['status'] == 'skip' and '; board:' not in r['reason']:
            bad.append('%s %s skipped without a board reason: %s' % (r['id'], r.get('test'),
                                                                     r['reason'][:160]))
    for k in known:
        if not any(r['id'] == k and r['status'] == 'fail' for r in res):
            bad.append('%s listed in known_fail but did not fail: remove it' % k)
else:
    driven = tuple(board.get('driver_areas') or ('display.', 'camera.', 'video.', 'audio.'))
    free = board.get('hardware_free') or {}
    for r in res:
        if r['status'] in ('pass', 'partial') and str(r.get('test')).startswith(driven) and \
                r['id'] not in free:
            bad.append('%s %s passed without the virtual drivers: %s' % (r['id'], r.get('test'), r['reason'][:160]))
n = {}
for r in res:
    n[r['status']] = n.get(r['status'], 0) + 1
print('expect %s: %s; %d violations' % (mode, ' '.join('%s=%d' % kv for kv in sorted(n.items())), len(bad)))
for b in bad:
    print('VIOLATION', b)
sys.exit(1 if bad else 0)
