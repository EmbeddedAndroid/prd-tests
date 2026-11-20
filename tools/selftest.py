#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""selftest.py: checks of the runner's result rules with fake tests (no
hardware needed): target evaluation, composite requirements, Skip and
Missing mapping with and without a board 'absent' entry, crashing and
misbehaving tests. Every product map entry must name a registered test
with arguments that test takes, every audio case a product names must be
defined by each board profile that has audio cases, and every run_last
entry must name a test and match a product requirement. Regression cases
cover run_last ordering, shell case status, the latency onset channel,
codec roles, decoder output caps and the per-test time limit. Exit status 1 on any mismatch."""
import glob
import inspect
import json
import os
import sys
import tempfile

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOP)
from prd import core  # noqa: E402


@core.test('fake.ok')
def ok(env, v=10):
    return 'pass', 'ok', {'v': v, 'fmts': ['NV12', 'P010']}


@core.test('fake.part')
def part(env):
    return 'partial', 'part'


@core.test('fake.bad')
def bad(env):
    return 'fail', 'bad'


@core.test('fake.skip')
def skip(env):
    raise core.Skip('no sink')


@core.test('fake.missing')
def missing(env):
    raise core.Missing('no DSI connector', hw='DSI')


@core.test('fake.crash')
def crash(env):
    raise ValueError('boom')


@core.test('fake.weird')
def weird(env):
    return 'great', 'not a status'


CASES = [
    # (requirement, board absent, expected status)
    ({'test': 'fake.ok'}, {}, 'pass'),
    ({'test': 'fake.ok', 'target': {'v': 8}}, {}, 'pass'),
    ({'test': 'fake.ok', 'target': {'v': 12}}, {}, 'partial'),
    ({'test': 'fake.ok', 'target': {'v': {'min': 5, 'max': 9}}}, {}, 'partial'),
    ({'test': 'fake.ok', 'target': {'v': {'eq': 10}}}, {}, 'pass'),
    ({'test': 'fake.ok', 'target': {'fmts': ['NV12']}}, {}, 'pass'),
    ({'test': 'fake.ok', 'target': {'fmts': ['NV12', 'AR24']}}, {}, 'partial'),
    ({'test': 'fake.ok', 'target': {'other': 1}}, {}, 'partial'),
    ({'test': 'fake.ok', 'target': 'none'}, {}, 'partial'),
    ({'test': 'fake.bad', 'target': {'v': 1}}, {}, 'fail'),
    ({'test': 'fake.part'}, {}, 'partial'),
    ({'test': 'fake.skip'}, {}, 'skip'),
    ({'test': 'fake.missing'}, {}, 'fail'),
    ({'test': 'fake.missing'}, {'DSI': 'no panel fitted'}, 'skip'),
    ({'test': 'fake.crash'}, {}, 'fail'),
    ({'test': 'fake.weird'}, {}, 'fail'),
    ({'test': 'fake.nosuch'}, {}, 'fail'),
    ({'tests': [{'test': 'fake.ok'}, {'test': 'fake.ok'}]}, {}, 'pass'),
    ({'tests': [{'test': 'fake.ok'}, {'test': 'fake.bad'}]}, {}, 'fail'),
    ({'tests': [{'test': 'fake.ok'}, {'test': 'fake.skip'}]}, {}, 'partial'),
    ({'test': 'fake.ok', 'target': {'fmts': 3}}, {}, 'partial'),
    ({'tests': [{'test': 'fake.bad'}, {'test': 'fake.skip'}]}, {}, 'fail'),
    ({'tests': [{'test': 'fake.skip'}, {'test': 'fake.skip'}]}, {}, 'skip'),
    ({'tests': [{'test': 'fake.part'}, {'test': 'fake.skip'}]}, {}, 'partial'),
]


def check_maps():
    core.import_tests(TOP)
    bad = []
    boards = {os.path.basename(b): core.load_data(b) for b in glob.glob(os.path.join(TOP, 'boards', '*.yaml'))}
    for pf in sorted(glob.glob(os.path.join(TOP, 'products', '*.yaml'))):
        prod = core.load_data(pf)
        for rid, req in prod['requirements'].items():
            for part in req.get('tests') or [req]:
                fn = core.REGISTRY.get(part.get('test'))
                if fn is None:
                    bad.append('%s %s: no test %s' % (os.path.basename(pf), rid, part.get('test')))
                    continue
                params = inspect.signature(fn).parameters
                extra = [k for k in (part.get('args') or {}) if k not in params]
                need = [k for k, p in params.items() if k != 'env' and p.default is p.empty
                        and k not in (part.get('args') or {})]
                if extra or need:
                    bad.append('%s %s: %s unknown args %s, missing args %s' % (
                        os.path.basename(pf), rid, part['test'], extra, need))
                case = (part.get('args') or {}).get('case') if part['test'] == 'audio.case' else None
                for bn, b in boards.items():
                    cs = (b.get('audio') or {}).get('cases')
                    if case and cs and case not in cs:
                        bad.append('%s %s: case %s not in %s' % (os.path.basename(pf), rid, case, bn))
    import importlib.util
    spec = importlib.util.spec_from_file_location('runpy', os.path.join(TOP, 'tools', 'run.py'))
    rp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rp)
    reqs = [r for pf in glob.glob(os.path.join(TOP, 'products', '*.yaml'))
            for r in core.load_data(pf)['requirements'].values()]
    for bn, b in boards.items():
        for i, ent in enumerate(b.get('run_last') or []):
            if ent.split()[0] not in core.REGISTRY:
                bad.append('%s run_last: no test %s' % (bn, ent.split()[0]))
            elif reqs and not any(rp.disrupts({'run_last': [ent]}, r) == 0 for r in reqs):
                bad.append('%s run_last: "%s" matches no product requirement' % (bn, ent))
    for b in bad:
        print('FAIL map', b)
    print('selftest: %d product map problems' % len(bad))
    return len(bad)


@core.test('fake.slow')
def slow(env):
    import time
    time.sleep(3)
    return 'pass', 'too late'


def check_units():
    """Regression cases for logic that needs no hardware."""
    import importlib.util
    import math
    import struct
    bad = []
    spec = importlib.util.spec_from_file_location('runpy', os.path.join(TOP, 'tools', 'run.py'))
    rp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rp)
    b = {'run_last': ['audio.ssr', 'audio.case case=compress']}
    for req, want in (({'test': 'audio.case', 'args': {'case': 'compress'}}, 1),
                      ({'test': 'audio.case', 'args': {'case': 'volume'}}, None),
                      ({'tests': [{'test': 'fake.ok'}, {'test': 'audio.ssr'}]}, 0),
                      ({'test': 'fake.ok'}, None)):
        got = rp.disrupts(b, req)
        if got != want:
            bad.append('run_last %s: got %s, want %s' % (req, got, want))
    todo = [('A-1', {'test': 'audio.case', 'args': {'case': 'compress'}}), ('A-2', {'test': 'fake.ok'}),
            ('A-3', {'test': 'audio.ssr'})]
    got = [i for i, _ in rp.order(b, todo)]
    if got != ['A-2', 'A-3', 'A-1']:
        bad.append('run order: got %s, want A-2 A-3 A-1' % got)
    au = sys.modules.get('tests.audio')
    if au is not None:
        for res, want in (({'a': 'pass', 'b': 'skip'}, 'pass'), ({'a': 'pass', 'b': 'fail'}, 'fail'),
                          ({'a': 'skip'}, 'fail')):
            got = au.case_status('c', res)[0]
            if got != want:
                bad.append('case_status %s: got %s, want %s' % (res, got, want))
        # tone on channel 2 from 0.5 s, on channel 1 only in the last 0.5 s:
        # the onset after 0.5 s must be found on channel 2, not at 2 s
        rate, n = au.RATE, int(2.5 * au.RATE)
        tone = lambda i: int(8000 * math.sin(2 * math.pi * 1000 * i / rate))
        frames = [(tone(i) if i >= 2 * rate else 0, tone(i) if 0.5 * rate <= i < 1.5 * rate else 0)
                  for i in range(n)]
        st = object.__new__(au.Stream)
        st.data = bytearray(b''.join(struct.pack('<hh', *f) for f in frames))
        on, nf = st.onset(1000, 15.0, int(0.5 * rate))
        if on is None or not 0.45 * rate <= on <= 0.6 * rate:
            bad.append('onset: got frame %s, want about %d' % (on, rate // 2))
    # codec roles from the formats an Iris decoder and encoder report:
    # the UBWC raw formats (Q08C, Q10C) carry the compressed flag
    from prd import v4l2

    class FakeDev:
        m2m = True

        def __init__(self, out, cap):
            self.f = {'output': out, 'capture': cap}

        def formats(self, side):
            return [(n, fl, '') for n, fl in self.f[side]]
    dec = FakeDev([('H264', 9), ('HEVC', 9), ('VP90', 9), ('AV01', 9)],
                  [('NV12', 0), ('Q08C', 1), ('P010', 0), ('Q10C', 1)])
    enc = FakeDev([('NV12', 0), ('Q08C', 1)], [('H264', 11), ('HEVC', 11)])
    sl = FakeDev([('S264', 9)], [('NV12', 0)])
    roles = [(c['role'], c['coded']) for c in v4l2.codecs([dec, enc, sl])]
    want = [('decoder', ['H264', 'HEVC', 'VP90', 'AV01']), ('encoder', ['H264', 'HEVC']),
            ('stateless-decoder', ['S264'])]
    if roles != want:
        bad.append('codec roles: got %s' % roles)
    vi = sys.modules.get('tests.video')
    if vi is not None:
        for caps, want in (('d0.GstPad:src: caps = video/x-raw(memory:DMABuf), format=(string)DMA_DRM, '
                            'drm-format=(string)P010:0x0000000000000000, width=(int)416, height=(int)240',
                            (416, 240, 'P010:0x0000000000000000')),
                           ('d0.GstPad:src: caps = video/x-raw, format=(string)NV12, width=(int)1920, '
                            'height=(int)1080', (1920, 1080, 'NV12'))):
            got = vi.out_caps(caps, 'd')
            if got != want:
                bad.append('out_caps: got %s, want %s' % (got, want))
    r = core.Runner(core.Env({}), tempfile.mkdtemp(prefix='prd-selftest-'))
    st_, why, _ = r.call('fake.slow', {}, limit=1)
    if st_ != 'fail' or 'Timeout' not in why:
        bad.append('timeout: got %s %s' % (st_, why))
    for x in bad:
        print('FAIL unit', x)
    print('selftest: %d unit problems' % len(bad))
    return len(bad)


def main():
    fails = check_maps()
    fails += check_units()
    out = tempfile.mkdtemp(prefix='prd-selftest-')
    for i, (req, absent, want) in enumerate(CASES):
        r = core.Runner(core.Env({'absent': absent}), out)
        r.requirement('T-%02d' % i, req)
        r.done()
        got = json.loads(open(os.path.join(out, 'results.jsonl')).read())['status']
        lava = open(os.path.join(out, 'result.txt')).read().split()[1]
        good = got == want and lava == core.LAVA[want]
        fails += not good
        print('%s T-%02d %s: got %s (LAVA %s), want %s' % ('ok  ' if good else 'FAIL', i, req, got,
                                                          lava, want))
    print('selftest: %d failures over %d result cases and the maps' % (fails, len(CASES)))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
