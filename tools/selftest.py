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
codec roles, decoder output caps, the per-test time limit and board
targets. Exit status 1 on any mismatch."""
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
    if au is not None:
        # a case naming its hardware: skip only where the board lists it absent
        td, old_td = tempfile.mkdtemp(prefix='prd-selftest-td-'), au.TD
        d = os.path.join(td, 'automated', 'linux', 'probe')
        os.makedirs(d)
        with open(os.path.join(d, 'probe.yaml'), 'w') as f:
            f.write('run:\n  steps:\n  - cd automated/linux/probe; mkdir -p output; '
                    'echo "probe-x $RES" > output/result.txt\n')
        au.TD = td
        try:
            for res, absent, want in (('fail', {'hw-x': 'not fitted'}, 'skip'), ('fail', {}, 'fail'),
                                      ('pass', {'hw-x': 'not fitted'}, 'pass')):
                b = {'audio': {'cases': {'c': {'dir': 'probe', 'hw': 'hw-x', 'params': {'RES': res}}}},
                     'absent': absent}
                got = core.Runner(core.Env(b), td).call('audio.case', {'case': 'c'})[0]
                if got != want:
                    bad.append('case hw %s absent %s: got %s, want %s' % (res, absent, got, want))
        finally:
            au.TD = old_td
    cx = sys.modules.get('tests.camx')
    if cx is not None:
        cams = {'cameras': [{'id': 0, 'width': 64, 'height': 48, 'fps': 30}]}
        td = tempfile.mkdtemp(prefix='prd-selftest-camx-')
        # without CamX every case fails (skip only where the board lists camx absent)
        for b, args, want, word in (({}, {}, 'fail', 'modules.camx'),
                                    ({'absent': {'camx': 'no CamX'}}, {}, 'skip', 'no CamX'),
                                    ({'modules': {'camx': True}}, {}, 'fail', 'camx.cameras'),
                                    ({'modules': {'camx': True}, 'camx': cams}, {'camera': 5}, 'fail',
                                     'camera 5'),
                                    ({'modules': {'camx': True}, 'camx': cams}, {}, 'fail', '')):
            for t in ('camx.preview', 'camx.capture', 'camx.concurrent', 'camx.service'):
                a = args if t in ('camx.preview', 'camx.capture') else {}
                w = '' if (t == 'camx.service' and 'modules' in b) or (args and not a) else word
                got, why, _ = core.Runner(core.Env(b), td).call(t, a)
                if got != want or w not in why:
                    bad.append('%s %s %s: got %s %s, want %s' % (t, b, a, got, why[:80], want))
        if not cx.kmd_graphs():
            got, why, _ = core.Runner(core.Env({'modules': {'camx': True}}), td).call('camx.kmd', {})
            if got != 'fail' or cx.REQ_MGR_MODEL not in why:
                bad.append('camx.kmd without the driver: got %s %s' % (got, why[:80]))
        log = ('/GstPipeline:pipeline0/GstFakeSink:sink0: last-message = chain   ******* (sink0:sink) '
               '(3110400 bytes, dts: none, pts: 0:00:01.000000000, duration: 0:00:00.033333333)\n')
        log = ''.join(log.replace('01.000000000', '%012.9f' % (1 + i / 30.0)) for i in range(31)) + \
            log.replace('sink0', 'sink1')
        s = cx.parse_sinks(log)
        if sorted(s) != [0, 1] or len(s[0]) != 31 or len(s[1]) != 1 or cx.pts_fps(s[0]) != 30.0 or \
                cx.pts_fps(s[1]) != 0.0:
            bad.append('camx parse_sinks: %s' % {k: len(v) for k, v in s.items()})
        # the memory:GBM caps carry parentheses: one shell word each
        import shlex
        words = shlex.split(cx.pipeline([{'id': 0, 'width': 64, 'height': 48, 'fps': 30},
                                         {'id': 1, 'width': 32, 'height': 24, 'fps': 15}]))
        if 'video/x-raw(memory:GBM),format=NV12,width=32,height=24,framerate=15/1' not in words:
            bad.append('camx pipeline quoting: %s' % words)
        # 10 buffers in 5 s from a 30 fps camera is a starved stream, not a preview
        # so is a stream that stops after a few frames at the right rate
        slow, stall = [i / 2.0 for i in range(10)], [i / 30.0 for i in range(5)]
        if cx.rate_ok({'fps': 30}, slow, 5) or cx.rate_ok({'fps': 30}, stall, 5) or \
                not cx.rate_ok({'fps': 30}, [i / 30.0 for i in range(150)], 5):
            bad.append('camx rate_ok: 2 fps %s, stall %s' % (cx.pts_fps(slow), cx.pts_fps(stall)))
        # a service restart during a stream fails the stream
        old_sh = cx.sh
        cx.sh = lambda cmd, timeout=None: (0, '3\n') if 'NRestarts' in cmd else old_sh(cmd, timeout)
        try:
            got = cx.restarted(core.Env({}), 1, ('pass', 'ok', {}))
            same = cx.restarted(core.Env({}), 3, ('pass', 'ok', {'frames': 1}))
        finally:
            cx.sh = old_sh
        if got[0] != 'fail' or 'restarted 2 times' not in got[1] or same != ('pass', 'ok', {'frames': 1}):
            bad.append('camx restarted: %s, unchanged count %s' % (got, same))
        if cx.rate_ok({'fps': 0}, [], 5):
            bad.append('camx rate_ok: empty stream with fps 0 passed')
        flat, noisy = os.path.join(td, 'flat'), os.path.join(td, 'noisy')
        open(flat, 'wb').write(b'\x80' * 6000)
        open(noisy, 'wb').write(bytes((i * 37) % 251 for i in range(6000)))
        if cx.luma_stats(flat, 6000)[1] != 0.0 or cx.luma_stats(noisy, 6000)[1] < 2.0:
            bad.append('camx luma_stats: flat %s noisy %s' % (cx.luma_stats(flat, 6000),
                                                              cx.luma_stats(noisy, 6000)))
        # the request manager's video node has the sensor function value too
        if cx.roles([('a', 0x10001, False), ('b', 0x10001, False), ('video0', 0x10001, True),
                     ('c', 0x10008, False), ('d', 0x10013, False), ('e', 0x20001, False)]) != \
                {'sensor': 2, 'csiphy': 1, 'tfe_mc': 1}:
            bad.append('camx roles')
    dp = sys.modules.get('tests.display')
    if dp is not None:
        # every wrong encoding/range decodes CSC_RGB at least 10 levels off,
        # the right one within 1 (8-bit rounding)
        def decode(yuv, enc, full):
            kr, kb = dp.YCBCR_K[enc]
            y, cb, cr = yuv
            y, cb, cr = ((y / 255, (cb - 128) / 255, (cr - 128) / 255) if full else
                         ((y - 16) / 219, (cb - 128) / 224, (cr - 128) / 224))
            r, b = y + 2 * (1 - kr) * cr, y + 2 * (1 - kb) * cb
            g = (y - kr * r - kb * b) / (1 - kr - kb)
            return tuple(min(255, max(0, round(c * 255))) for c in (r, g, b))
        keys = [(e, f) for e in dp.YCBCR_K for f in (False, True)]
        for ek in keys:
            yuv = dp.rgb_to_ycbcr(dp.CSC_RGB, *ek)
            for dk in keys:
                err = max(abs(a - b) for a, b in zip(decode(yuv, *dk), dp.CSC_RGB))
                if (ek == dk and err > 1) or (ek != dk and err < 10):
                    bad.append('csc %s decoded as %s is %d levels off' % (ek, dk, err))
        # little-endian bytes of (R, G, B) = (0x11, 0x22, 0x33), from drm_fourcc.h
        for n, raw, want in (('XR24', b'\x33\x22\x11\x00', (0x11, 0x22, 0x33)),
                             ('XB24', b'\x11\x22\x33\x00', (0x11, 0x22, 0x33)),
                             ('RG24', b'\x33\x22\x11', (0x11, 0x22, 0x33)),
                             ('BG24', b'\x11\x22\x33', (0x11, 0x22, 0x33)),
                             ('RG16', b'\x00\xf8', (255, 0, 0)),
                             ('BG16', b'\x1f\x00', (255, 0, 0)),
                             ('AR30', b'\x00\x00\xf0\x3f', (255, 0, 0)),
                             ('RX15', b'\x00\xf8', (255, 0, 0))):
            got = dp.px_fmt({'pitch': 64, 'map': raw + bytes(64)}, 0, 0, n)
            if got != want:
                bad.append('px_fmt %s: got %s, want %s' % (n, got, want))
        # packed 4:2:2 byte order (Y0, Cb, Y1, Cr indices), drm_fourcc.h
        if dp.PACKED_YUV != {'YUYV': (0, 1, 2, 3), 'YVYU': (0, 3, 2, 1), 'UYVY': (1, 0, 3, 2),
                             'VYUY': (3, 0, 1, 2)}:
            bad.append('PACKED_YUV order %s' % dp.PACKED_YUV)
    bad += check_board_targets()
    r = core.Runner(core.Env({}), tempfile.mkdtemp(prefix='prd-selftest-'))
    r.missing = 'skip'
    st_, why, _ = r.call('fake.missing', {})
    if st_ != 'skip' or not why.startswith('interface absent'):
        bad.append('missing skip product: got %s %s' % (st_, why))
    r.missing = 'fail'
    st_, why, _ = r.call('fake.slow', {}, limit=1)
    if st_ != 'fail' or 'Timeout' not in why:
        bad.append('timeout: got %s %s' % (st_, why))
    for x in bad:
        print('FAIL unit', x)
    print('selftest: %d unit problems' % len(bad))
    return len(bad)


def run_one(req, board, missing, rid='R-1'):
    r = core.Runner(core.Env(board), tempfile.mkdtemp(prefix='prd-selftest-'))
    r.missing = missing
    r.requirement(rid, req)
    r.done()
    return json.loads(open(os.path.join(r.outdir, 'results.jsonl')).read())


def check_board_targets():
    """A board's targets.<ID> replaces the row's target, for that ID only."""
    bad = []
    got = run_one({'test': 'fake.ok', 'target': {'v': 8}}, {'targets': {'R-1': {'v': 12}}}, 'fail')
    if got['status'] != 'partial':
        bad.append('board target does not override the row target: %s' % got)
    got = run_one({'test': 'fake.ok'}, {'targets': {'R-2': {'v': 12}}}, 'fail')
    if got['status'] != 'pass':
        bad.append('board target of another row applied: %s' % got)
    return bad


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
