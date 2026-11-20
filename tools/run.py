#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""run.py: run a product's requirements on this device.

    run.py --board boards/<board>.yaml --product products/<product>.yaml
           [-o outdir] [--area display,audio] [ID ...]
    run.py --list                      every registered test
    run.py --board B --test display.edid [key=value ...]   one test

Each requirement of the product map names a generic test, its
arguments and an optional target; the board profile supplies what the
tests cannot discover (which connector is the primary display, audio
routes, hardware the board does not have). One "PRD <ID> <status>:
<reason>" line per requirement; <outdir>/result.txt is in the format
send-to-lava.sh reads, <outdir>/results.jsonl keeps the measurements."""
import argparse
import json
import os
import sys

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOP)
from prd import core  # noqa: E402


def parse_val(v):
    try:
        return json.loads(v)
    except ValueError:
        return v


def disrupts(board, req):
    """Index of the first run_last entry of the board profile that one of
    the requirement's tests matches, else None. An entry is a test name,
    optionally with key=value arguments that must match ("audio.case
    case=compress"); requirements run last in the order of the list."""
    for i, ent in enumerate(board.get('run_last') or []):
        name, *kv = ent.split()
        for part in req.get('tests') or [req]:
            args = part.get('args') or {}
            if name == part.get('test') and all(
                    str(args.get(k)) == v for k, v in (x.split('=', 1) for x in kv)):
                return i
    return None


def order(board, todo):
    """(id, requirement) pairs in run order: those matching run_last go
    after the rest, in the order of the run_last list."""
    last = sorted((d for d in todo if disrupts(board, d[1]) is not None),
                  key=lambda d: disrupts(board, d[1]))
    return [d for d in todo if disrupts(board, d[1]) is None] + last


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--board')
    ap.add_argument('--product')
    ap.add_argument('-o', '--outdir', default='output')
    ap.add_argument('--area', help='comma separated areas (the product\'s areas map by ID prefix, else the test interface)')
    ap.add_argument('--test', help='run one test by name instead of a product')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('ids', nargs='*')
    a = ap.parse_args()
    core.import_tests(TOP)
    if a.list:
        for n in sorted(core.REGISTRY):
            doc = (core.REGISTRY[n].__doc__ or '').strip().split('\n')[0]
            print('%-32s %s' % (n, doc))
        return 0
    board = core.load_data(a.board) if a.board else {}
    env = core.Env(board)
    run = core.Runner(env, a.outdir)
    print('PRD BOARD %s PRODUCT %s REV %s' % (
        board.get('name', '-'), os.path.basename(a.product or '-'),
        core.read(os.path.join(TOP, 'REV')).strip() or '-'), flush=True)
    if a.test:
        args = dict(kv.split('=', 1) for kv in a.ids)
        run.requirement(a.test, {'test': a.test, 'args': {k: parse_val(v) for k, v in args.items()}})
        run.done()
        return 0
    if not a.product:
        ap.error('--product or --test is required')
    prod = core.load_data(a.product)
    areas = set(a.area.split(',')) if a.area else None
    todo = []
    for rid, req in prod['requirements'].items():
        if a.ids and rid not in a.ids:
            continue
        area = (prod.get('areas') or {}).get(rid.split('-')[0]) or \
            str(req.get('test') or req['tests'][0]['test']).split('.')[0]
        if areas and area not in areas:
            continue
        todo.append((rid, req))
    for rid, req in order(board, todo):
        try:
            run.requirement(rid, req)
        except Exception as e:  # never lose the requirements after this one
            print('PRD %s fail: runner error %s: %s (0.0 s)' % (rid, type(e).__name__, e), flush=True)
            run.res.write('%s fail\n' % rid)
            run.js.write(json.dumps({'id': rid, 'status': 'fail', 'reason': 'runner error: %s' % e}) + '\n')
            run.counts['fail'] = run.counts.get('fail', 0) + 1
    run.done()
    return 0


if __name__ == '__main__':
    sys.exit(main())
