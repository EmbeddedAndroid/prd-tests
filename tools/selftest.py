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
codec roles, decoder output caps, the per-test time limit, board
targets and the re-derivation tools/coverage.py applies, including
reasons a console line break cut. Exit status 1 on any mismatch."""
import glob
import inspect
import json
import os
import sys
import tempfile
import time

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
    if au is not None:
        # PipeWire: the session manager's dummy sink carries no device.id
        dump = json.dumps([
            {'type': 'PipeWire:Interface:Node', 'info': {'props': {
                'media.class': 'Audio/Sink', 'node.name': 'auto_null'}}},
            {'type': 'PipeWire:Interface:Node', 'info': {'props': {
                'media.class': 'Audio/Source', 'node.name': 'mic', 'device.id': 47}}}])
        sh0 = au.sh
        au.sh = lambda cmd, timeout=None: (0, dump)
        got = au.pw_nodes(core.Env({}))
        if got != [('Audio/Sink', 'auto_null', False), ('Audio/Source', 'mic', True)]:
            bad.append('pw_nodes: got %s' % got)
        # UCM: a board that ships a profile fails without one, else skips
        class FakeAudio:
            card, info, cfg = 0, {'id': 'c'}, {'ucm': {'verb': 'HiFi'}}
        a0, have0 = au.A, au.have
        au.A, au.have = (lambda env: FakeAudio()), (lambda tool: True)
        au.sh = lambda cmd, timeout=None: (1, 'failed to import hw:0 use case configuration -2')
        for cfg, want in (({'ucm': {'verb': 'HiFi'}}, 'fail'), ({}, 'Missing')):
            FakeAudio.cfg = cfg
            try:
                got = au.t_ucm(core.Env({}))[0]
            except core.Missing:
                got = 'Missing'
            if got != want:
                bad.append('ucm missing, profile %s: got %s, want %s' % (cfg, got, want))
        au.sh, au.A, au.have = sh0, a0, have0
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
    bad += check_camx_client()
    bad += check_board_targets()
    bad += check_rederive()
    bad += check_unparsed()
    r = core.Runner(core.Env({}), tempfile.mkdtemp(prefix='prd-selftest-'))
    r.missing = 'skip'
    st_, why, _ = r.call('fake.missing', {})
    if st_ != 'skip' or not why.startswith('interface absent'):
        bad.append('missing skip product: got %s %s' % (st_, why))
    r.missing = 'fail'
    st_, why, _ = r.call('fake.slow', {}, limit=1)
    if st_ != 'fail' or 'Timeout' not in why:
        bad.append('timeout: got %s %s' % (st_, why))
    d = tempfile.mkdtemp(prefix='prd-selftest-base-')
    for ext in ('yaml', 'json'):
        with open(os.path.join(d, 'b.%s' % ext), 'w') as f:
            json.dump({'name': 'b', 'audio': {'card': 'c', 'cases': {'x': {'dir': 'a', 'params': {'P': 1}},
                                                                   'y': {'dir': 'y'}}}}, f)
        with open(os.path.join(d, 'k.%s' % ext), 'w') as f:
            json.dump({'base': 'b', 'name': 'k', 'audio': {'cases': {'x': {'dir': 'z'}}}}, f)
        got = core.load_data(os.path.join(d, 'k.%s' % ext))
        want = {'name': 'k', 'audio': {'card': 'c', 'cases': {'x': {'dir': 'z', 'params': {'P': 1}},
                                                            'y': {'dir': 'y'}}}}
        if got != want:
            bad.append('base profile (%s): got %s, want %s' % (ext, got, want))
    for x in bad:
        print('FAIL unit', x)
    print('selftest: %d unit problems' % len(bad))
    return len(bad)


# ICP firmware debug log of a run that completes its frames, and of the
# first frame stall (the OFE never ends request 1, the CDM is aborted)
FW_GOOD = ''.join('camx-hal3-test-1 [001] ..... 1%03d.000000: cam_icp_fw_dbg: %s\n' % (i, l) for i, l in enumerate(
    ['QC_IMAGE_VERSION_STRING=CICP.FW.1.0-00001'] +
    ['Responding to request %d errorType (CAMERAICP_SUCCESS)' % n for n in range(40)]))
FW_STALL = ''.join('camx-hal3-test-1 [001] ..... 1%03d.000000: cam_icp_fw_dbg: %s\n' % (i, l) for i, l in enumerate(
    ['QC_IMAGE_VERSION_STRING=CICP.FW.1.0-00001',
     'Responding to request 0 errorType (CAMERAICP_SUCCESS)',
     'ProcessHWFailure ofeScratch 0x0',
     'CDMDV:In CDMAbort: !!!Loop reach']))
SUMMARY = ('''CamX: PrintErrorSummary: ===== Error Summary =====
CamX: PrintErrorSummary: Num Unknown Frame Error : 0
CamX: PrintErrorSummary: Num Unexpected Buffer Error Notification : 1
''')


def fake_client(td, body):
    """A client script for camx.client.cmd: body runs in cwd with the
    command file as $1."""
    p = os.path.join(td, 'client.sh')
    with open(p, 'w') as f:
        f.write(body)
    return 'sh %s {cmdfile}' % p


def check_camx_trace(cx, td):
    """The real Trace on a fake tracefs and debugfs: it traces in an
    instance of its own and puts every ICP's log type and level back."""
    bad = []
    top, dbg = os.path.join(td, 'tracing'), os.path.join(td, 'debug')
    inst = os.path.join(top, 'instances', cx.INSTANCE)
    os.makedirs(os.path.join(inst, 'events', cx.FW_EVENT))
    for f, v in ((os.path.join(top, 'tracing_on'), '1'), (os.path.join(top, 'buffer_size_kb'), '1408'),
                 (os.path.join(inst, 'trace'), 'fw line\n')):
        open(f, 'w').write(v)
    for i, t, l in (('icpa', '0', '2'), ('icpb', '1', '0')):
        os.makedirs(os.path.join(dbg, i))
        open(os.path.join(dbg, i, 'icp_debug_type'), 'w').write(t + '\n')
        open(os.path.join(dbg, i, 'icp_debug_lvl'), 'w').write(l + '\n')
    old = cx.TRACING, cx.CAM_DEBUGFS
    cx.TRACING, cx.CAM_DEBUGFS = (top,), dbg
    try:
        tr = cx.Trace(core.Env({'camx': {'icps': ['icpa', 'icpb', 'icpc']}}))
        on = tr.start()
        during = [open(os.path.join(dbg, i, n)).read() for i in ('icpa', 'icpb')
                  for n in ('icp_debug_type', 'icp_debug_lvl')]
        text = tr.stop()
    finally:
        cx.TRACING, cx.CAM_DEBUGFS = old
    after = [open(os.path.join(dbg, i, n)).read() for i in ('icpa', 'icpb') for n in ('icp_debug_type', 'icp_debug_lvl')]
    if on != ['icpa', 'icpb'] or during != ['1', '63', '1', '63'] or after != ['0', '2', '1', '0'] or \
            text != 'fw line\n' or open(os.path.join(top, 'tracing_on')).read() != '1' or \
            open(os.path.join(top, 'buffer_size_kb')).read() != '1408' or \
            open(os.path.join(inst, 'events', cx.FW_EVENT, 'enable')).read() != '0':
        bad.append('camx Trace: on %s during %s after %s text %r' % (on, during, after, text))
    return bad


def check_camx_client():
    """The CamX test client runner and the parsers of its logs."""
    cx = sys.modules.get('tests.camx')
    if cx is None:
        return []
    bad = []
    g, b = cx.parse_fw(FW_GOOD), cx.parse_fw(FW_STALL)
    if g != {'version': 'CICP.FW.1.0-00001', 'frames': 40, 'hw_failures': 0, 'cdm_loops': 0,
             'frame_errors': 0} or cx.fw_problems(g):
        bad.append('camx parse_fw good: %s %s' % (g, cx.fw_problems(g)))
    if b['hw_failures'] != 1 or b['cdm_loops'] != 1 or len(cx.fw_problems(b)) != 2:
        bad.append('camx parse_fw stall: %s %s' % (b, cx.fw_problems(b)))
    if cx.fw_problems(cx.parse_fw(''))[-1:] != ['no CAMERAICP_SUCCESS (no frame completed in the firmware)']:
        bad.append('camx parse_fw: an empty log is not a failure')
    if cx.parse_summary(SUMMARY) != {'unknown_frame_error': 0, 'unexpected_buffer_error_notification': 1}:
        bad.append('camx parse_summary: %s' % cx.parse_summary(SUMMARY))
    env = core.Env({'camx': {'client': {'errors_max': {'Unknown Frame Error': 0,
                                                       'Unexpected Buffer Error Notification': 0}}}})
    if cx.summary_problems(env, SUMMARY)[1] != ['Unexpected Buffer Error Notification 1 > 0'] or \
            'no "Unknown Frame Error" in the client error summary' not in cx.summary_problems(env, '')[1]:
        bad.append('camx summary_problems: %s' % (cx.summary_problems(env, SUMMARY)[1],))
    td = tempfile.mkdtemp(prefix='prd-selftest-camxc-')
    cwd = os.path.join(td, 'out')
    os.makedirs(cwd)
    # an old image is not the run's; one the run rewrites under the same name is
    for n in ('old.jpg', 'same.jpg'):
        open(os.path.join(cwd, n), 'w').write('x')
        os.utime(os.path.join(cwd, n), (1, 1))
    cmd = fake_client(td, 'cat "$1"; echo "V=$V"; : > a.jpg; : > same.jpg\n'
                          '[ "$V" = hang ] && sleep 30\n'
                          '[ "$V" = deaf ] && trap "" TERM && sleep 30\n'
                          '[ "$V" = killed ] && exit 137\nexit 0\n')
    prof = {'camx': {'client': {'cmd': cmd, 'cwd': cwd, 'env': {'V': 'one two'}, 'timeout': 1,
                                'commands': {'c': {'file': 'A:id=0\nQ'}}}}}
    r = cx.run_client(core.Env(prof), 'c', trace=False)
    if r.rc or [os.path.basename(f) for f in r.files] != ['a.jpg', 'same.jpg'] or 'A:id=0\nQ' not in r.log or \
            'V=one two' not in r.log or r.problems() or r.fw is not None or r.meas()['rc'] != 0:
        bad.append('camx run_client: rc %s files %s log %r' % (r.rc, r.files, r.log[:80]))
    r.cleanup()
    if os.listdir(cwd) != ['old.jpg']:
        bad.append('camx run cleanup: %s left' % os.listdir(cwd))
    # a hang is a hang whether the client takes SIGTERM, ignores it and
    # takes SIGKILL, or runs under timeout(1) and reports 137
    for v, rc in (('hang', 143), ('deaf', None), ('killed', 137)):
        prof['camx']['client']['env'] = {'V': v}
        t0 = time.time()
        r = cx.run_client(core.Env(prof), 'c', trace=False)
        p = r.problems()
        if rc is not None and r.rc != rc or not r.hung or not p or \
                not p[0].startswith('the client did not exit within 1 s') or time.time() - t0 > 5:
            bad.append('camx run_client %s: rc %s hung %s %s in %.1f s' % (v, r.rc, r.hung, p, time.time() - t0))
        r.cleanup()
    bad += check_camx_trace(cx, td)
    for b, want in (({'camx': {}}, 'camx.client.cmd'), ({'camx': {'client': {'cmd': 'no-such-client -f x'}}},
                                                        'no-such-client not on the image'),
                    ({'camx': {'client': {'cmd': 'sh'}}}, "command 'c'")):
        try:
            cx.run_client(core.Env(b), 'c')
            bad.append('camx run_client %s: no Missing' % b)
        except core.Missing as e:
            if want not in str(e) or e.hw != 'camx':
                bad.append('camx run_client %s: %s' % (b, e))
    return bad


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


def check_rederive():
    """tools/coverage.py re-derives a requirement's result from the row
    result recorded under "missing: skip": as_missing_fail, then settle
    with the map's target (only for a row run without one), must give
    what a run under the default policy with that target gives."""
    bad = []
    extra = [({'tests': [{'test': 'fake.ok'}, {'test': 'fake.missing'}]}, {}),
             ({'tests': [{'test': 'fake.missing'}, {'test': 'fake.skip'}]}, {}),
             ({'tests': [{'test': 'fake.missing'}, {'test': 'fake.missing'}]}, {'DSI': 'x'}),
             ({'tests': [{'test': 'fake.ok'}, {'test': 'fake.ok'}], 'target': {'w': 1}}, {}),
             ({'tests': [{'test': 'fake.ok'}, {'test': 'fake.ok'}], 'target': 'none'}, {}),
             ({'tests': [{'test': 'fake.ok'}, {'test': 'fake.missing'}], 'target': 'none'}, {})]
    for req, absent in [c[:2] for c in CASES] + extra:
        tests = [p['test'] for p in req.get('tests') or [req]]
        for target in ([req['target']] if 'target' in req else [None, 'none', {'v': 12}]):
            row = req
            dev = run_one(row, {'absent': absent}, 'skip')
            st, why = core.as_missing_fail(tests, dev['status'], dev['reason'])
            if 'target' not in req:
                st, why = core.settle(st, why, dev['meas'], target)
            ref = run_one(dict(row, target=target), {'absent': absent}, 'fail')
            if (st, why) != (ref['status'], ref['reason']):
                bad.append('re-derive %s target %r: got %s %r, want %s %r' % (
                    req, target, st, why, ref['status'], ref['reason']))
    return bad


def check_unparsed():
    """coverage.py refuses a LAVA reason a console line break cut inside
    the part separator or the "interface absent: " prefix."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('cov', os.path.join(TOP, 'tools', 'coverage.py'))
    cov = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cov)
    bad = []
    one = {'test': 'fake.missing'}
    two = {'tests': [{'test': 'fake.ok'}, {'test': 'fake.missing'}]}
    for row, res in ((one, ('skip', 'interface ab | sent: no DSI connector', {})),
                     (two, ('partial', 'fake.ok pass: ok | | | fake.missing skip: x', {})),
                     (two, ('partial', 'fake.ok pass: ok || fake.missing skip: interface | absent: x', {})),
                     (two, ('skip', 'fake.ok skip: interface absent: y || fake.missing skip: interface | absent: x',
                            {}))):
        st = cov.derive(row, res, {}, 'fail')[0]
        if st != 'unparsed':
            bad.append('coverage of a cut reason %r: %s, want unparsed' % (res[1], st))
    st = cov.derive(one, ('skip', 'interface absent: no DSI connector', {}), {}, 'fail')[0]
    if st != 'fail':
        bad.append('coverage of an absent interface: %s, want fail' % st)
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
