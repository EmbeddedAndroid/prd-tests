# SPDX-License-Identifier: GPL-2.0
"""Display tests on DRM/KMS (atomic API, dumb buffers, writeback) and
Wayland (Weston, EGL).

Composition tests prove their result by pixels: the planes are composed
on a CRTC that drives a writeback connector and the captured frame is
compared with what the hardware must produce, so they need no monitor.
Connector tests pick the connector by type (HDMI-A, DP, DSI, eDP) or by
role ("primary", "external": the board profile's display.primary and
display.external); they skip when no sink is attached.

Board profile keys used: display.primary, display.external (connector
types), display.compositor (systemd unit holding DRM master, default
weston) or display.compositor_cmd (a command to start it where there is
no systemd), display.wayland (XDG_RUNTIME_DIR and WAYLAND_DISPLAY of the
compositor), display.software_gl (accept a software GL renderer),
display.gpu_busy (a file whose first number is the GPU's busy time in
its last sample window, for GPU drivers without DRM client usage
stats), absent.<key> for hardware the board does not have
(<type> for a connector type, <type>-sink for a sink, writeback,
backlight, touch, displays-<n>)."""
import atexit
import functools
import glob
import os
import re
import struct
import subprocess
import time

from prd import kms
from prd.core import Missing, Skip, have, read, sh, test
from prd.kms import fourcc

XR24, AR24, NV12 = fourcc('XR24'), fourcc('AR24'), fourcc('NV12')
WB_W, WB_H = 320, 240
RED, GREEN, BLUE, WHITE, BLACK = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0)


class Display:
    """Discovery and DRM master handling. KMS tests need DRM master, so
    the compositor is stopped for them and started again for the Wayland
    tests and at exit. The compositor is a systemd unit (display.
    compositor, default weston) or, on systems without systemd, a
    command the runner starts and stops itself (display.compositor_cmd)."""

    def __init__(self, env):
        self.env = env
        self.unit = env.cfg('display', 'compositor', default='weston')
        self.cmd = env.cfg('display', 'compositor_cmd')
        self.proc = None
        self.unit_was_active = not self.cmd and self.state() == 'active'
        self.card = None
        self.wb_empty = 0
        atexit.register(self.restore)

    def state(self):
        if self.cmd:
            return 'active' if self.proc and self.proc.poll() is None else 'inactive'
        return sh('systemctl is-active %s 2>/dev/null' % self.unit)[1].strip()

    def stop(self):
        if self.cmd:
            if self.proc and self.proc.poll() is None:
                os.killpg(self.proc.pid, 15)
                self.proc.wait()
        elif self.state() == 'active':
            sh('systemctl stop %s %s.socket 2>/dev/null' % (self.unit, self.unit))

    def start(self):
        if self.cmd:
            self.log = os.path.join('/tmp', 'prd-compositor.log')
            self.proc = subprocess.Popen(self.cmd, shell=True, stdout=open(self.log, 'w'),
                                         stderr=subprocess.STDOUT, start_new_session=True)
        else:
            sh('systemctl start %s 2>/dev/null' % self.unit)
        time.sleep(3)

    # ---- master
    def kms(self):
        if self.card:
            return self
        self.stop()
        # the compositor may take a moment to drop DRM master
        held = False
        for i in range(20):
            self.card = kms.open_kms()
            if not self.card or self.card.set_master():
                held = False
                break
            held = True
            self.card.close()
            self.card = None
            time.sleep(0.5)
        if held:
            rc, who = sh('for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q /dev/dri/card && '
                         'echo "${p#/proc/} $(cat $p/comm)"; done')
            raise Exception('DRM master is held by another process for 10 s (card users: %s)' % (
                who.strip().replace('\n', ', ') or 'none'))
        if not self.card:
            raise Missing('no DRM device with atomic modesetting and connectors in /dev/dri',
                          hw='kms')
        c = self.card
        self.conns = c.connectors()
        self.planes = c.planes()
        self.crtc_props = {cr: c.props(cr, kms.OBJ_CRTC) for cr in c.crtcs}
        self.conn_props = {k['id']: c.props(k['id'], kms.OBJ_CONNECTOR) for k in self.conns}
        self.wb = next((k for k in self.conns if k['type'] == kms.CONN_IDS['Writeback']), None)
        print('KMS %s (%s): %d CRTCs, %d planes, connectors %s' % (
            c.path, self.driver(), len(c.crtcs), len(self.planes),
            ', '.join('%s %s' % (k['name'], 'connected' if k['connected'] else 'disconnected')
                      for k in self.conns)), flush=True)
        return self

    def release(self):
        if self.card:
            self.card.close()
            self.card = None

    def compositor(self):
        """Close KMS and make sure the compositor runs; returns its state
        and, when it does not run, the end of its log."""
        self.release()
        if not self.cmd and sh('systemctl cat %s >/dev/null 2>&1' % self.unit)[0]:
            raise Missing('no %s unit on this image' % self.unit, hw='compositor')
        if self.state() != 'active':
            self.start()
        st = self.state()
        log = ''
        if st != 'active':
            log = read(self.log)[-600:] if self.cmd else \
                sh('journalctl -b -u %s --no-pager -n 8 2>&1' % self.unit)[1].strip()
        return st, log

    def pid(self):
        if self.cmd:
            return str(self.proc.pid) if self.proc else ''
        return sh('systemctl show -p MainPID --value %s' % self.unit)[1].strip()

    def restore(self):
        self.release()
        if self.cmd:
            self.stop()
        elif self.unit_was_active:
            sh('systemctl start %s 2>/dev/null' % self.unit)

    def driver(self):
        return self.card.driver_name()

    # ---- lookups
    def cprop(self, conn, name):
        return self.conn_props[conn['id']].get(name)

    def planes_for(self, crtc):
        """Planes usable on a CRTC: primary first, then overlays; cursor
        planes take no full-size layer and are left out."""
        i = self.card.crtcs.index(crtc)
        rank = {'Primary': 0, 'Overlay': 1}
        return sorted((p for p in self.planes if p['possible_crtcs'] & (1 << i) and
                       p['type'] != 'Cursor'), key=lambda p: (rank.get(p['type'], 2), p['id']))

    def wb_crtc(self):
        if not self.wb:
            return None
        cr = self.card.possible_crtcs(self.wb)
        return cr[0] if cr else None

    def ctype(self, role):
        """Connector type for a role or type name."""
        if role in ('primary', 'external'):
            t = self.env.cfg('display', role)
            if isinstance(t, list):
                t = t[0] if t else None
            if not t:
                raise Missing('the board profile names no %s display (display.%s)' % (role, role),
                              hw='%s-display' % role)
            return t
        return role

    def of_type(self, role):
        t = self.ctype(role)
        if t not in kms.CONN_IDS:
            raise ValueError('unknown connector type %s' % t)
        return [k for k in self.conns if k['type'] == kms.CONN_IDS[t]]

    def need_type(self, role):
        ks = self.of_type(role)
        t = self.ctype(role)
        if not ks:
            raise Missing('no %s connector in KMS (connectors: %s)' % (
                t, ', '.join(k['name'] for k in self.conns) or 'none'), hw=t)
        return ks

    def need_connected(self, role):
        ks = self.need_type(role)
        on = [k for k in ks if k['connected']]
        if not on:
            t = self.ctype(role)
            why = self.env.cfg('absent', '%s-sink' % t)
            raise Skip('needs a sink on %s: %s reads disconnected%s' % (
                t, ', '.join(k['name'] for k in ks), '; board: %s' % why if why else
                ' (a monitor or an EDID dummy plug enables this test)'))
        return on[0]

    def need_wb(self):
        if not self.wb:
            raise Missing('no writeback connector exposed by the display driver, so the '
                          'composition cannot be captured', hw='writeback')


def discover(env):
    return Display(env)


def K(env):
    return env.ctx('display').kms()


# ---- buffers and pixels
def fb_solid(card, w, h, rgb, fmt=XR24, alpha=0xff):
    d = card.dumb(w, h, 32)
    px_ = struct.pack('<I', (alpha << 24) | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2])
    row = px_ * w + b'\0' * (d['pitch'] - 4 * w)
    d['map'][:d['pitch'] * h] = row * h
    return card.addfb2(w, h, fmt, [d['handle']], [d['pitch']], [0]), d


def fb_quadrants(card, w, h, colors):
    """XR24 buffer split in 4 quadrants TL TR BL BR."""
    d = card.dumb(w, h, 32)
    pk = [struct.pack('<I', 0xff000000 | (r << 16) | (g << 8) | b) for r, g, b in colors]
    pad = b'\0' * (d['pitch'] - 4 * w)
    top = pk[0] * (w // 2) + pk[1] * (w - w // 2) + pad
    bot = pk[2] * (w // 2) + pk[3] * (w - w // 2) + pad
    d['map'][:d['pitch'] * h] = top * (h // 2) + bot * (h - h // 2)
    return card.addfb2(w, h, XR24, [d['handle']], [d['pitch']], [0]), d


def fb_nv12(card, w, h, y, u, v):
    d = card.dumb(w, h * 3 // 2, 8)
    p = d['pitch']
    d['map'][:p * h] = bytes([y]) * (p * h)
    d['map'][p * h:p * h + p * (h // 2)] = bytes([u, v]) * (p * (h // 2) // 2)
    return card.addfb2(w, h, NV12, [d['handle'], d['handle']], [p, p], [0, p * h]), d


def free(card, *bufs):
    for fb, d in bufs:
        card.rmfb(fb)
        card.free_dumb(d)


def px(d, x, y):
    o = y * d['pitch'] + x * 4
    b, g, r, _ = d['map'][o:o + 4]
    return (r, g, b)


def near(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def sample(cap, pts):
    return [px(cap, x, y) for x, y in pts]


class Layer:
    def __init__(self, fb, sw, sh, dst, src=None, props=None):
        self.fb, self.dst, self.props = fb, dst, props or {}
        self.src = src or (0, 0, sw, sh)


def crtc_req(d, crtc, mblob, extra=None):
    """CRTC state for a commit: mode, active, the colour properties in
    RESET_CRTC cleared (no blob: identity) unless extra sets them."""
    cp = d.crtc_props[crtc]
    r = {cp['MODE_ID'][0]: mblob, cp['ACTIVE'][0]: 1}
    for k in RESET_CRTC:
        if k in cp:
            r[cp[k][0]] = 0
    for k, v in (extra or {}).items():
        r[cp[k][0]] = v
    return r


def plane_req(p, crtc, L, card):
    """Plane state for one layer; the properties in RESET_PLANE that the
    layer does not set go back to their value at discovery."""
    pp = p['props']
    x, y, dw, dh = L.dst
    sx, sy, sw, sh_ = L.src
    r = {pp['FB_ID'][0]: L.fb, pp['CRTC_ID'][0]: crtc,
         pp['SRC_X'][0]: sx << 16, pp['SRC_Y'][0]: sy << 16,
         pp['SRC_W'][0]: sw << 16, pp['SRC_H'][0]: sh_ << 16,
         pp['CRTC_X'][0]: x & 0xffffffff, pp['CRTC_Y'][0]: y & 0xffffffff,
         pp['CRTC_W'][0]: dw, pp['CRTC_H'][0]: dh}
    for k in RESET_PLANE:
        if k in pp and k not in L.props and not card.prop_info(pp[k][0])['flags'] & kms.PROP_IMMUTABLE:
            r[pp[k][0]] = pp[k][1]
    return r


def compose_once(d, layers, crtc_extra=None, wb_fmt=XR24, w=WB_W, h=WB_H, test_only=False):
    """Commit layers on the writeback CRTC and capture the output.
    Returns (capture dumb buffer, None) or (None, error string)."""
    card = d.card
    crtc = d.wb_crtc()
    if crtc is None:
        return None, 'no writeback connector'
    cand = d.planes_for(crtc)
    if len(layers) > len(cand):
        return None, 'only %d planes on the writeback CRTC' % len(cand)
    mblob = card.create_blob(kms.Mode.make(w, h).raw)
    if wb_fmt == NV12:
        cap = card.dumb(w, h * 3 // 2, 8)
        cap['map'][:] = b'\x5a' * cap['size']
        capfb = card.addfb2(w, h, wb_fmt, [cap['handle']] * 2, [cap['pitch']] * 2,
                            [0, cap['pitch'] * h])
    else:
        cap = card.dumb(w, h, 32)
        cap['map'][:] = b'\x5a' * cap['size']
        capfb = card.addfb2(w, h, wb_fmt, [cap['handle']], [cap['pitch']], [0])
    fence = kms.ctypes.c_int32(-1)
    cp = d.crtc_props[crtc]
    wp = d.conn_props[d.wb['id']]
    req = {crtc: crtc_req(d, crtc, mblob, crtc_extra),
           d.wb['id']: {wp['CRTC_ID'][0]: crtc, wp['WRITEBACK_FB_ID'][0]: capfb}}
    if not test_only:
        req[d.wb['id']][wp['WRITEBACK_OUT_FENCE_PTR'][0]] = kms.ctypes.addressof(fence)
    # whatever else drives this CRTC now (a console framebuffer, an
    # earlier output) is detached for the capture
    for k in d.conns:
        if k['id'] != d.wb['id']:
            kp = card.props(k['id'], kms.OBJ_CONNECTOR)
            if kp.get('CRTC_ID', (0, 0))[1] == crtc:
                req[k['id']] = {kp['CRTC_ID'][0]: 0}
    for p in d.planes:
        pc = card.props(p['id'], kms.OBJ_PLANE).get('CRTC_ID', (0, 0))
        if pc[1] == crtc and p not in cand[:len(layers)]:
            req[p['id']] = {p['props']['FB_ID'][0]: 0, pc[0]: 0}
    used = []
    err = None
    for L, p in zip(layers, cand):
        r = plane_req(p, crtc, L, card)
        for k, v in L.props.items():
            if k == 'zpos' and (k not in p['props'] or
                                card.prop_info(p['props'][k][0])['flags'] & kms.PROP_IMMUTABLE):
                continue    # fixed order: the plane order above is the stacking order
            if k not in p['props']:
                err = 'plane %d has no "%s" property' % (p['id'], k)
                break
            r[p['props'][k][0]] = v
        req[p['id']] = r
        used.append(p)
    if err is None:
        try:
            card.atomic(req, kms.ALLOW_MODESET | (kms.TEST_ONLY if test_only else 0))
            if not test_only:
                st = kms.wait_fence(fence.value, 2.0)
                if st != 1:
                    err = 'writeback fence %s' % ('did not signal within 2 s' if st == 0 else
                                                  'missing' if st is None else
                                                  'signalled with error %d' % st)
        except kms.DrmError as e:
            err = 'atomic commit refused: %s' % e
        if not test_only and err is None:
            off = {crtc: {cp['MODE_ID'][0]: 0, cp['ACTIVE'][0]: 0},
                   d.wb['id']: {wp['CRTC_ID'][0]: 0}}
            for p in used:
                off[p['id']] = {p['props']['FB_ID'][0]: 0, p['props']['CRTC_ID'][0]: 0}
            try:
                card.atomic(off, kms.ALLOW_MODESET)
            except kms.DrmError as e:
                print('disable after capture failed: %s' % e)
    card.rmfb(capfb)
    card.destroy_blob(mblob)
    if err or test_only:
        card.free_dumb(cap)
        return (None, err) if err else (True, None)
    return cap, None


FILL = b'\x5a\x5a\x5a\x5a'
# properties a test may change; every commit clears the CRTC ones and
# sets the plane ones back to their value at discovery unless it uses
# them, so no test sees another's state
RESET_CRTC = ('CTM', 'GAMMA_LUT', 'DEGAMMA_LUT')
RESET_PLANE = ('alpha', 'pixel blend mode', 'rotation', 'zpos', 'COLOR_ENCODING', 'COLOR_RANGE')


def untouched(cap, w, h):
    """True when every sampled pixel still holds the fill pattern."""
    pts = [(x * (w - 1) // 7, y * (h - 1) // 7) for x in range(8) for y in range(8)]
    m, p = cap['map'], cap['pitch']
    return all(m[y * p + x * 4:y * p + x * 4 + 4] == FILL for x, y in pts)


def compose(d, layers, crtc_extra=None, wb_fmt=XR24, w=WB_W, h=WB_H, test_only=False, tries=3):
    """compose_once, repeated when the fence signalled but the frame was
    not written (the capture still holds its fill pattern). Retries are
    counted in d.wb_empty; tests made with wbtest report them."""
    for i in range(tries):
        cap, err = compose_once(d, layers, crtc_extra, wb_fmt, w, h, test_only)
        if err or test_only or not untouched(cap, w, h):
            return cap, err
        d.wb_empty += 1
        d.card.free_dumb(cap)
    return None, 'writeback capture empty %d times in a row (fence signalled, frame not written)' % tries


def wbtest(name):
    """test() for writeback tests: appends the number of empty captures
    that had to be retried to the reason."""
    def reg(fn):
        @functools.wraps(fn)
        def run(env, **kw):
            d = K(env)
            d.need_wb()
            before = d.wb_empty
            r = fn(env, **kw)
            n = d.wb_empty - before
            if n:
                r = (r[0], r[1] + '; %d empty writeback capture(s) retried' % n) + tuple(r[2:])
            return r
        return test(name)(run)
    return reg


def light(d, conn, mode=None, extra_conn=None, hold=1.0):
    """Light one connector with a solid frame through its primary plane
    and check its link-status after the commit. Returns None or the
    error string."""
    c = d.card
    crtc = c.possible_crtcs(conn)[0]
    mode = mode or conn['modes'][0]
    mb = c.create_blob(mode.raw)
    fb, buf = fb_solid(c, mode.hdisplay, mode.vdisplay, (40, 80, 160))
    pls = d.planes_for(crtc)
    pl = next((p for p in pls if p['type'] == 'Primary'), pls[0])
    pp, cp, kp = pl['props'], d.crtc_props[crtc], d.conn_props[conn['id']]
    req = {crtc: crtc_req(d, crtc, mb),
           conn['id']: {kp['CRTC_ID'][0]: crtc},
           pl['id']: plane_req(pl, crtc, card=c, L=Layer(fb, mode.hdisplay, mode.vdisplay,
                                              (0, 0, mode.hdisplay, mode.vdisplay)))}
    for k, v in (extra_conn or {}).items():
        req[conn['id']][kp[k][0]] = v
    try:
        c.atomic(req, kms.ALLOW_MODESET)
        time.sleep(hold)
        err = None
        ls = c.props(conn['id'], kms.OBJ_CONNECTOR).get('link-status')
        if ls and c.enum_name(ls) == 'Bad':
            err = 'commit accepted but the connector link-status is Bad'
    except kms.DrmError as e:
        err = str(e)
    off = {crtc: {cp['MODE_ID'][0]: 0, cp['ACTIVE'][0]: 0}, conn['id']: {kp['CRTC_ID'][0]: 0},
           pl['id']: {pp['FB_ID'][0]: 0, pp['CRTC_ID'][0]: 0}}
    try:
        c.atomic(off, kms.ALLOW_MODESET)
    except kms.DrmError:
        pass
    free(c, (fb, buf))
    c.destroy_blob(mb)
    return err


def pick_mode(conn, mode):
    ms = conn['modes']
    if not ms:
        return None
    if mode in (None, 'preferred'):
        return next((m for m in ms if m.type & 0x8), ms[0])
    if mode == 'highest':
        return max(ms, key=lambda m: (m.hdisplay * m.vdisplay, m.vrefresh))
    w, _, rest = mode.partition('x')
    h, _, r = rest.partition('@')
    return next((m for m in ms if m.hdisplay == int(w) and m.vdisplay == int(h) and
                 (not r or m.vrefresh == int(r))), None)


# ---- device and connectors
@test('display.kms')
def t_kms(env):
    """Atomic KMS device: CRTCs, planes and connectors are enumerated"""
    d = K(env)
    kinds = sorted(set(kms.CONN_TYPES.get(k['type'], str(k['type'])) for k in d.conns))
    return ('pass', 'atomic KMS on %s (%s): %d CRTCs, %d planes, connectors %s' % (
        d.card.path, d.driver(), len(d.card.crtcs), len(d.planes),
        ', '.join(k['name'] for k in d.conns)),
        {'crtcs': len(d.card.crtcs), 'planes': len(d.planes), 'connector_types': kinds})


@test('display.connector')
def t_connector(env, connector='primary'):
    """Connector of a type exists and a sink on it is detected with modes"""
    d = K(env)
    k = d.need_connected(connector)
    n = len(k['modes'])
    best = pick_mode(k, 'highest')
    return ('pass' if n else 'fail', '%s connected, %d modes, highest %r, %dx%d mm' % (
        k['name'], n, best, k['mm'][0], k['mm'][1]), {'modes': n})


@test('display.modeset')
def t_modeset(env, connector='primary', mode='preferred'):
    """Mode set on a connector: a frame is scanned out in the chosen mode"""
    d = K(env)
    k = d.need_connected(connector)
    m = pick_mode(k, mode)
    if m is None:
        return 'fail', '%s offers no mode %s (modes: %s)' % (
            k['name'], mode, ', '.join(map(repr, k['modes'][:12])))
    err = light(d, k, m, hold=2.0)
    if err:
        return 'fail', '%s mode %r refused: %s' % (k['name'], m, err)
    return ('pass', '%s lit at %r through atomic modeset' % (k['name'], m),
            {'width': m.hdisplay, 'height': m.vdisplay, 'refresh': m.vrefresh,
             'pixels': m.hdisplay * m.vdisplay, 'clock_khz': m.clock})


@test('display.modes')
def t_modes(env, connector='primary'):
    """Every mode the sink offers is accepted by an atomic check"""
    d = K(env)
    k = d.need_connected(connector)
    c = d.card
    crtc = c.possible_crtcs(k)[0]
    ok, bad = [], []
    for m in k['modes']:
        mb = c.create_blob(m.raw)
        fb, buf = fb_solid(c, m.hdisplay, m.vdisplay, BLACK)
        pls = d.planes_for(crtc)
        pl = next((p for p in pls if p['type'] == 'Primary'), pls[0])
        cp, kp = d.crtc_props[crtc], d.conn_props[k['id']]
        req = {crtc: crtc_req(d, crtc, mb), k['id']: {kp['CRTC_ID'][0]: crtc},
               pl['id']: plane_req(pl, crtc, card=c, L=Layer(fb, m.hdisplay, m.vdisplay,
                                                  (0, 0, m.hdisplay, m.vdisplay)))}
        try:
            c.atomic(req, kms.ALLOW_MODESET | kms.TEST_ONLY)
            ok.append(m)
        except kms.DrmError as e:
            bad.append('%r: %s' % (m, e))
        free(c, (fb, buf))
        c.destroy_blob(mb)
    best = max(ok, key=lambda m: (m.hdisplay * m.vdisplay, m.vrefresh)) if ok else None
    return ('pass' if ok and not bad else 'fail' if not ok else 'partial',
            '%s: %d of %d sink modes pass the atomic check%s%s' % (
                k['name'], len(ok), len(k['modes']), '; highest %r' % best if best else '',
                '; refused: ' + '; '.join(bad[:6]) if bad else ''),
            {'modes': len(ok), 'width': best.hdisplay if best else 0,
             'height': best.vdisplay if best else 0, 'refresh': best.vrefresh if best else 0})


def edid_blocks(raw):
    return [raw[i:i + 128] for i in range(0, len(raw), 128)]


@test('display.edid')
def t_edid(env, connector='primary'):
    """EDID read over DDC/AUX: header, checksums and the preferred timing"""
    d = K(env)
    k = d.need_connected(connector)
    path = '/sys/class/drm/%s-%s/edid' % (os.path.basename(d.card.path), k['name'])
    try:
        raw = open(path, 'rb').read()
    except OSError as e:
        return 'fail', 'cannot read %s: %s' % (path, e)
    if not raw:
        if env.cfg('absent', 'edid'):
            raise Missing('%s reports no EDID' % k['name'], hw='edid')
        return 'fail', '%s is connected but reports no EDID' % k['name']
    if len(raw) < 128:
        return 'fail', '%s: EDID is %d bytes' % (k['name'], len(raw))
    blocks = edid_blocks(raw)
    hdr = raw[:8] == b'\x00\xff\xff\xff\xff\xff\xff\x00'
    sums = [sum(b) % 256 == 0 for b in blocks]
    m = struct.unpack('>H', raw[8:10])[0]
    mfg = ''.join(chr(((m >> s) & 0x1f) + 64) for s in (10, 5, 0))
    dt = raw[54:72]
    clk = struct.unpack('<H', dt[0:2])[0] * 10
    hact = dt[2] | ((dt[4] & 0xf0) << 4)
    vact = dt[5] | ((dt[7] & 0xf0) << 4)
    ok = hdr and all(sums) and raw[126] == len(blocks) - 1
    return ('pass' if ok else 'fail', '%s: %d EDID blocks, header %s, checksums %s, '
            'manufacturer %s, EDID %d.%d, preferred %dx%d %d kHz' % (
                k['name'], len(blocks), 'ok' if hdr else 'BAD', sums, mfg, raw[18], raw[19],
                hact, vact, clk),
            {'blocks': len(blocks), 'width': hact, 'height': vact})


@test('display.hotplug')
def t_hotplug(env, connector='primary'):
    """Hot plug detection: a forced reprobe reads the sink as connected"""
    d = K(env)
    ks = d.need_type(connector)
    card = os.path.basename(d.card.path)
    res = []
    for k in ks:
        p = '/sys/class/drm/%s-%s/status' % (card, k['name'])
        before = read(p).strip()
        try:
            with open(p, 'w') as f:
                f.write('detect')
        except OSError as e:
            res.append('%s reprobe refused: %s' % (k['name'], e))
            continue
        time.sleep(1)
        res.append((k['name'], before, read(p).strip()))
    conn = [r for r in res if isinstance(r, tuple) and r[2] == 'connected']
    if not conn:
        t = d.ctype(connector)
        why = env.cfg('absent', '%s-sink' % t)
        raise Skip('needs a sink on %s to show a detection: %s%s' % (
            t, res, '; board: %s' % why if why else ''))
    return ('pass', 'connector status after forced reprobe: %s' % ', '.join(
        '%s %s -> %s' % r if isinstance(r, tuple) else r for r in res))


# ---- composition through writeback
@wbtest('display.writeback')
def t_writeback(env):
    """Writeback: a pattern composed by the display engine is captured to memory"""
    d = K(env)
    d.need_wb()
    fb, buf = fb_quadrants(d.card, WB_W, WB_H, [RED, GREEN, BLUE, WHITE])
    cap, err = compose(d, [Layer(fb, WB_W, WB_H, (0, 0, WB_W, WB_H))])
    free(d.card, (fb, buf))
    if err:
        return 'fail', err
    got = sample(cap, [(40, 40), (280, 40), (40, 200), (280, 200)])
    d.card.free_dumb(cap)
    ok = all(near(a, b) for a, b in zip(got, [RED, GREEN, BLUE, WHITE]))
    fl = wb_formats(d)
    return ('pass' if ok else 'fail',
            'captured %dx%d quadrants %s (expected red, green, blue, white); writeback formats %s'
            % (WB_W, WB_H, got, ' '.join(fl)), {'formats': fl})


def wb_formats(d):
    pv = d.cprop(d.wb, 'WRITEBACK_PIXEL_FORMATS')
    blob = d.card.blob(pv[1]) if pv else b''
    return [kms.fourcc_str(f) for f in struct.unpack('<%dI' % (len(blob) // 4), blob)]


COLS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255),
        (128, 0, 0), (0, 128, 0), (0, 0, 128), (128, 128, 0), (128, 0, 128), (0, 128, 128),
        (64, 64, 64), (192, 192, 192), (255, 128, 0), (0, 128, 255)]


@wbtest('display.layers')
def t_layers(env):
    """Layers: as many planes as one CRTC offers are composed at once"""
    d = K(env)
    d.need_wb()
    planes = d.planes_for(d.wb_crtc())
    n = len(planes)
    best, last_err = 0, ''
    for k in range(n, 0, -1):
        bufs, layers = [], []
        sw = WB_W // k
        for i in range(k):
            fb, buf = fb_solid(d.card, sw, WB_H, COLS[i % len(COLS)])
            bufs.append((fb, buf))
            layers.append(Layer(fb, sw, WB_H, (i * sw, 0, sw, WB_H),
                                props={'zpos': i} if 'zpos' in planes[i]['props'] else None))
        cap, err = compose(d, layers)
        if not err:
            got = sample(cap, [(i * sw + sw // 2, WB_H // 2) for i in range(k)])
            d.card.free_dumb(cap)
            if all(near(got[i], COLS[i % len(COLS)]) for i in range(k)):
                best = k
        else:
            last_err = err
        free(d.card, *bufs)
        if best:
            break
    types = {}
    for p in planes:
        types[p['type']] = types.get(p['type'], 0) + 1
    if not best:
        return 'fail', 'no layer count composed correctly (%s)' % last_err
    return ('pass' if best == n else 'partial',
            '%d of %d planes on one CRTC composed side by side and verified in the writeback '
            'capture; plane types %s' % (best, n, types), {'layers': best, 'planes': n})


@wbtest('display.crop')
def t_crop(env):
    """Source cropping: a sub-rectangle of the buffer is scanned out"""
    d = K(env)
    d.need_wb()
    fb, buf = fb_quadrants(d.card, 2 * WB_W, 2 * WB_H, [RED, GREEN, BLUE, WHITE])
    cap, err = compose(d, [Layer(fb, 2 * WB_W, 2 * WB_H, (0, 0, WB_W, WB_H),
                                 src=(WB_W, WB_H, WB_W, WB_H))])
    free(d.card, (fb, buf))
    if err:
        return 'fail', err
    got = sample(cap, [(10, 10), (160, 120), (310, 230)])
    d.card.free_dumb(cap)
    ok = all(near(g, WHITE) for g in got)
    return ('pass' if ok else 'fail',
            'cropped the white bottom-right quadrant of a 640x480 buffer: corners/centre %s' % got)


@wbtest('display.scale')
def t_scale(env):
    """Scaling: upscale and downscale of a layer by the display engine"""
    d = K(env)
    d.need_wb()
    out, ok = [], True
    for sw, sh_ in ((80, 60), (1280, 960)):
        fb, buf = fb_quadrants(d.card, sw, sh_, [RED, GREEN, BLUE, WHITE])
        cap, err = compose(d, [Layer(fb, sw, sh_, (0, 0, WB_W, WB_H))])
        free(d.card, (fb, buf))
        if err:
            out.append('%dx%d->%dx%d refused: %s' % (sw, sh_, WB_W, WB_H, err))
            ok = False
            continue
        got = sample(cap, [(40, 40), (280, 40), (40, 200), (280, 200)])
        d.card.free_dumb(cap)
        good = all(near(a, b, 12) for a, b in zip(got, [RED, GREEN, BLUE, WHITE]))
        ok &= good
        out.append('%dx%d->%dx%d %s' % (sw, sh_, WB_W, WB_H, 'ok' if good else 'wrong %s' % got))
    return ('pass' if ok else 'fail', '; '.join(out))


@wbtest('display.position')
def t_position(env):
    """Arbitrary position: a layer at odd x/y offsets lands on exactly those pixels"""
    d = K(env)
    d.need_wb()
    bg = fb_solid(d.card, WB_W, WB_H, BLACK)
    fg = fb_solid(d.card, 33, 17, RED)
    x, y = 101, 57
    cap, err = compose(d, [Layer(bg[0], WB_W, WB_H, (0, 0, WB_W, WB_H), props={'zpos': 0}),
                           Layer(fg[0], 33, 17, (x, y, 33, 17), props={'zpos': 1})])
    free(d.card, bg, fg)
    if err:
        return 'fail', err
    inside = sample(cap, [(x, y), (x + 32, y + 16)])
    outside = sample(cap, [(x - 1, y), (x, y - 1), (x + 33, y + 16), (x + 32, y + 17)])
    d.card.free_dumb(cap)
    ok = all(near(p, RED) for p in inside) and all(near(p, BLACK) for p in outside)
    return ('pass' if ok else 'fail',
            '33x17 layer at (%d,%d): first/last pixel %s, neighbours %s' % (x, y, inside, outside))


@wbtest('display.zorder')
def t_zorder(env):
    """Blend order: zpos decides which overlapping layer is on top, both ways"""
    d = K(env)
    d.need_wb()
    a = fb_solid(d.card, 200, 200, RED)
    b = fb_solid(d.card, 200, 140, BLUE)
    res = []
    for za, zb, want in ((0, 1, BLUE), (1, 0, RED)):
        cap, err = compose(d, [Layer(a[0], 200, 200, (0, 0, 200, 200), props={'zpos': za}),
                               Layer(b[0], 200, 140, (100, 100, 200, 140), props={'zpos': zb})])
        if err:
            free(d.card, a, b)
            return 'fail', err
        got = px(cap, 150, 150)
        d.card.free_dumb(cap)
        res.append((za, zb, got, near(got, want)))
    free(d.card, a, b)
    zp = [p for p in d.planes_for(d.wb_crtc())[:2] if 'zpos' in p['props'] and
          not d.card.prop_info(p['props']['zpos'][0])['flags'] & kms.PROP_IMMUTABLE]
    ok = all(r[3] for r in res)
    return ('pass' if ok else 'fail', 'overlap pixel for zpos red/blue %s%s' % (
        ', '.join('%d/%d -> %s' % r[:3] for r in res),
        '' if len(zp) == 2 else '; zpos absent or immutable on these planes: the order is fixed'))


@wbtest('display.alpha')
def t_alpha(env):
    """Alpha blending: per-pixel alpha, plane alpha and blend modes"""
    d = K(env)
    d.need_wb()
    planes = d.planes_for(d.wb_crtc())
    hv = set(planes[1]['props']) if len(planes) > 1 else set()
    missing = [p for p in ('alpha', 'pixel blend mode') if p not in hv]
    bg = fb_solid(d.card, WB_W, WB_H, BLUE)
    # AR24 red with alpha 0x80, not premultiplied, over blue: coverage
    # blending gives (128,0,127), premultiplied (255,0,127), none (255,0,0)
    fg = fb_solid(d.card, WB_W, WB_H, RED, fmt=AR24, alpha=0x80)
    bufs = [bg, fg]
    out, ok = [], True
    cases = []
    if 'pixel blend mode' in hv:
        bm = d.card.prop_info(planes[1]['props']['pixel blend mode'][0])['enums']
        out.append('blend modes %s' % ','.join(bm))
        for mode, want in (('Coverage', (128, 0, 127)), ('Pre-multiplied', (255, 0, 127)),
                           ('None', RED)):
            if mode in bm:
                cases.append(('per-pixel %s' % mode, fg[0], {'pixel blend mode': bm[mode]}, want))
    else:
        cases.append(('per-pixel default (pre-multiplied)', fg[0], {}, (255, 0, 127)))
    if 'alpha' in hv:
        fg2 = fb_solid(d.card, WB_W, WB_H, RED)
        bufs.append(fg2)
        cases.append(('plane alpha', fg2[0], {'alpha': 0x8000}, (128, 0, 127)))
    for name, fb, props, want in cases:
        props = dict(props, zpos=1)
        cap, err = compose(d, [Layer(bg[0], WB_W, WB_H, (0, 0, WB_W, WB_H), props={'zpos': 0}),
                               Layer(fb, WB_W, WB_H, (0, 0, WB_W, WB_H), props=props)])
        if err:
            out.append('%s: %s' % (name, err))
            ok = False
            continue
        got = px(cap, 100, 100)
        d.card.free_dumb(cap)
        good = near(got, want, 10)
        ok &= good
        out.append('%s %s (expect ~%s)' % (name, got, want))
    free(d.card, *bufs)
    if missing:
        out.append('missing plane properties: %s' % ', '.join(missing))
        return ('partial' if ok else 'fail', '; '.join(out))
    return ('pass' if ok else 'fail', '; '.join(out))


# luma weights (Kr, Kb) of the YCbCr encodings KMS names in COLOR_ENCODING
YCBCR_K = {'BT.601': (0.299, 0.114), 'BT.709': (0.2126, 0.0722), 'BT.2020': (0.2627, 0.0593)}
# every wrong encoding/range pairing decodes this colour at least 10 levels off
CSC_RGB = (20, 220, 100)


def rgb_to_ycbcr(rgb, enc, full):
    """8-bit Y, Cb, Cr of an RGB colour in an encoding and range."""
    kr, kb = YCBCR_K[enc]
    r, g, b = [c / 255 for c in rgb]
    y = kr * r + (1 - kr - kb) * g + kb * b
    cb, cr = (b - y) / (2 * (1 - kb)), (r - y) / (2 * (1 - kr))
    if full:
        return round(y * 255), round(128 + cb * 255), round(128 + cr * 255)
    return round(16 + 219 * y), round(128 + 224 * cb), round(128 + 224 * cr)


@wbtest('display.csc')
def t_csc(env):
    """Colour space conversion: a YUV (NV12) layer converted to RGB, matrix and range selectable"""
    d = K(env)
    d.need_wb()
    pl = next((p for p in d.planes_for(d.wb_crtc()) if NV12 in p['formats']), None)
    if not pl:
        return 'fail', 'no plane on the writeback CRTC takes NV12'
    enc = pl['props'].get('COLOR_ENCODING')
    rng = pl['props'].get('COLOR_RANGE')
    ee = d.card.prop_info(enc[0])['enums'] if enc else {}
    rr = d.card.prop_info(rng[0])['enums'] if rng else {}
    pick = lambda enums, want: next((k for k in enums if want in k.replace('.', '').lower()), None)
    out, good, missing, tried = [], [], [], []
    for name in YCBCR_K:
        for full in (False, True):
            label = '%s %s' % (name, 'full' if full else 'limited')
            props = {}
            if enc and rng:
                en = pick(ee, name.replace('.', '').lower())
                rn = pick(rr, 'full' if full else 'limited')
                if en is None or rn is None:
                    missing.append(label)
                    continue
                props = {'COLOR_ENCODING': ee[en], 'COLOR_RANGE': rr[rn]}
            elif label != 'BT.601 limited':
                continue
            tried.append(label)
            y, u, v = rgb_to_ycbcr(CSC_RGB, name, full)
            fb = fb_nv12(d.card, WB_W, WB_H, y, u, v)
            cap, err = compose(d, [Layer(fb[0], WB_W, WB_H, (0, 0, WB_W, WB_H), props=props)])
            free(d.card, fb)
            if err:
                out.append('%s: %s' % (label, err))
                continue
            got = px(cap, 100, 100)
            d.card.free_dumb(cap)
            out.append('%s Y%d U%d V%d -> %s' % (label, y, u, v, got))
            if near(got, CSC_RGB, 4):
                good.append(label)
    out.insert(0, 'expect %s' % (CSC_RGB,))
    if not enc or not rng:
        return ('partial' if good else 'fail', '; '.join(out) + '; the plane has no COLOR_ENCODING/'
                'COLOR_RANGE property, so only the driver default matrix is used')
    # the colour properties must leave an RGB layer alone
    rgbp = {'COLOR_ENCODING': ee[pick(ee, 'bt709') or next(iter(ee))],
            'COLOR_RANGE': rr[pick(rr, 'full') or next(iter(rr))]}
    fb = fb_solid(d.card, WB_W, WB_H, CSC_RGB)
    cap, err = compose(d, [Layer(fb[0], WB_W, WB_H, (0, 0, WB_W, WB_H), props=rgbp)])
    free(d.card, fb)
    rgb_ok = False
    if err:
        out.append('RGB layer with colour properties: %s' % err)
    else:
        got = px(cap, 100, 100)
        d.card.free_dumb(cap)
        rgb_ok = near(got, CSC_RGB, 1)
        out.append('XR24 layer with colour properties -> %s' % (got,))
    if missing:
        out.append('not offered: %s' % ', '.join(missing))
    st = 'pass' if len(good) == len(tried) == 6 and rgb_ok else 'partial' if good else 'fail'
    return st, '; '.join(out), {'matrices': good}


BPP = {'XR24': 32, 'AR24': 32, 'XB24': 32, 'AB24': 32, 'RX24': 32, 'RA24': 32, 'BX24': 32,
       'BA24': 32, 'XR30': 32, 'AR30': 32, 'XB30': 32, 'AB30': 32, 'RG16': 16, 'BG16': 16,
       'AR15': 16, 'XR15': 16, 'AR12': 16, 'XR12': 16, 'RA12': 16, 'RX12': 16, 'RG24': 24,
       'BG24': 24, 'AB12': 16, 'XB12': 16, 'BA12': 16, 'BX12': 16, 'AB15': 16, 'XB15': 16,
       'RA15': 16, 'RX15': 16, 'BA15': 16, 'BX15': 16}


def fb_any(card, f, w=64, h=64):
    """Linear framebuffer of fourcc f, or (None, reason)."""
    n = kms.fourcc_str(f)
    if n in BPP:
        d = card.dumb(w, h, BPP[n])
        return card.addfb2(w, h, f, [d['handle']], [d['pitch']], [0]), d
    if n in ('NV12', 'NV21', 'NV16', 'NV61', 'P010'):
        rows = h * 2 if n in ('NV16', 'NV61') else h * 3 // 2
        d = card.dumb(w, rows, 16 if n == 'P010' else 8)
        return card.addfb2(w, h, f, [d['handle']] * 2, [d['pitch']] * 2, [0, d['pitch'] * h]), d
    if n in ('YU12', 'YV12'):
        d = card.dumb(w, h * 3 // 2, 8)
        p = d['pitch']
        return card.addfb2(w, h, f, [d['handle']] * 3, [p, p // 2, p // 2],
                           [0, p * h, p * h + p * h // 4]), d
    return None, 'layout unknown to this test'


@test('display.formats')
def t_formats(env):
    """Input formats: every advertised linear format commits on a plane"""
    d = K(env)
    d.need_wb()
    pl = d.planes_for(d.wb_crtc())[0]
    fmts = d.card.in_formats(pl) or {f: {kms.MOD_LINEAR} for f in pl['formats']}
    ok_l, bad, skipped = [], [], []
    for f in sorted((f for f, m in fmts.items() if kms.MOD_LINEAR in m), key=kms.fourcc_str):
        n = kms.fourcc_str(f)
        try:
            fb, buf = fb_any(d.card, f)
        except kms.DrmError as e:
            bad.append('%s addfb %s' % (n, e))
            continue
        if fb is None:
            skipped.append(n)
            continue
        _, err = compose(d, [Layer(fb, 64, 64, (0, 0, 64, 64))], test_only=True)
        (bad.append('%s %s' % (n, err)) if err else ok_l.append(n))
        free(d.card, (fb, buf))
    reason = '%d formats commit (%s)' % (len(ok_l), ' '.join(ok_l))
    if bad:
        reason += '; not committed: %s' % '; '.join(bad)
    if skipped:
        reason += '; not exercised (layout unknown to this test): %s' % ' '.join(skipped)
    return ('fail' if bad or not ok_l else 'partial' if skipped else 'pass', reason,
            {'formats': ok_l, 'count': len(ok_l)})


# DRM RGB layouts: components from the most significant bit down, and their
# widths, of a little-endian pixel word
RGB_LAYOUT = {
    'XR24': ('XRGB', (8, 8, 8, 8)), 'AR24': ('ARGB', (8, 8, 8, 8)),
    'XB24': ('XBGR', (8, 8, 8, 8)), 'AB24': ('ABGR', (8, 8, 8, 8)),
    'RX24': ('RGBX', (8, 8, 8, 8)), 'RA24': ('RGBA', (8, 8, 8, 8)),
    'BX24': ('BGRX', (8, 8, 8, 8)), 'BA24': ('BGRA', (8, 8, 8, 8)),
    'RG24': ('RGB', (8, 8, 8)), 'BG24': ('BGR', (8, 8, 8)),
    'RG16': ('RGB', (5, 6, 5)), 'BG16': ('BGR', (5, 6, 5)),
    'XR15': ('XRGB', (1, 5, 5, 5)), 'AR15': ('ARGB', (1, 5, 5, 5)),
    'XB15': ('XBGR', (1, 5, 5, 5)), 'AB15': ('ABGR', (1, 5, 5, 5)),
    'RX15': ('RGBX', (5, 5, 5, 1)), 'RA15': ('RGBA', (5, 5, 5, 1)),
    'BX15': ('BGRX', (5, 5, 5, 1)), 'BA15': ('BGRA', (5, 5, 5, 1)),
    'XR12': ('XRGB', (4, 4, 4, 4)), 'AR12': ('ARGB', (4, 4, 4, 4)),
    'XB12': ('XBGR', (4, 4, 4, 4)), 'AB12': ('ABGR', (4, 4, 4, 4)),
    'RX12': ('RGBX', (4, 4, 4, 4)), 'RA12': ('RGBA', (4, 4, 4, 4)),
    'BX12': ('BGRX', (4, 4, 4, 4)), 'BA12': ('BGRA', (4, 4, 4, 4)),
    'XR30': ('XRGB', (2, 10, 10, 10)), 'AR30': ('ARGB', (2, 10, 10, 10)),
    'XB30': ('XBGR', (2, 10, 10, 10)), 'AB30': ('ABGR', (2, 10, 10, 10)),
}
# three distinct levels: a swap of any two colour components shows
WB_RGB = (224, 96, 32)


def px_fmt(cap, x, y, name):
    """(r, g, b) at 8 bits of pixel x, y in a capture of an RGB layout."""
    order, bits = RGB_LAYOUT[name]
    n = sum(bits) // 8
    o = y * cap['pitch'] + x * n
    v = int.from_bytes(cap['map'][o:o + n], 'little')
    out, sh_ = {}, sum(bits)
    for c, w in zip(order, bits):
        sh_ -= w
        out[c] = ((v >> sh_) & ((1 << w) - 1)) * 255 / ((1 << w) - 1)
    return tuple(round(out[c]) for c in 'RGB')


def ycbcr_match(yuv, rgb, tol=4):
    """Standard encodings and ranges under which yuv encodes rgb."""
    return ['%s %s' % (e, 'full' if f else 'limited') for e in YCBCR_K for f in (False, True)
            if all(abs(a - b) <= tol for a, b in zip(yuv, rgb_to_ycbcr(rgb, e, f)))]


@wbtest('display.wb_formats')
def t_wb_formats(env):
    """Output formats: writeback captures in each advertised RGB format"""
    d = K(env)
    d.need_wb()
    fl = wb_formats(d)
    fb = fb_solid(d.card, WB_W, WB_H, WB_RGB)
    ok_l, bad, other = [], [], []
    for n in fl:
        if n not in RGB_LAYOUT and n != 'NV12':
            other.append(n)
            continue
        cap, err = compose(d, [Layer(fb[0], WB_W, WB_H, (0, 0, WB_W, WB_H))], wb_fmt=fourcc(n))
        if err:
            bad.append('%s %s' % (n, err))
            continue
        if n == 'NV12':
            p = cap['pitch']
            uv = WB_H * p + 5 * p + 10
            yuv = (cap['map'][10 * p + 10], cap['map'][uv], cap['map'][uv + 1])
            m = ycbcr_match(yuv, WB_RGB)
            d.card.free_dumb(cap)
            (ok_l.append('NV12 (%s)' % ' or '.join(m)) if m else
             bad.append('NV12 Y%d U%d V%d matches no standard encoding of %s' % (yuv + (WB_RGB,))))
            continue
        got = px_fmt(cap, 10, 10, n)
        d.card.free_dumb(cap)
        tol = 255 // ((1 << min(b for c, b in zip(*RGB_LAYOUT[n]) if c in 'RGB')) - 1) + 4
        (ok_l.append(n) if near(got, WB_RGB, tol) else bad.append('%s wrong %s' % (n, got)))
    free(d.card, fb)
    return ('fail' if bad or not ok_l else 'partial' if other else 'pass',
            'writeback formats advertised %s; %s captured and verified %s%s%s' % (
                ' '.join(fl), WB_RGB, ' '.join(ok_l), '; failed: ' + '; '.join(bad) if bad else '',
                '; advertised, not exercised: ' + ' '.join(other) if other else ''),
            {'formats': fl, 'verified': ok_l})


@wbtest('display.max_width')
def t_max_width(env):
    """Maximum width: widest frame composed from one layer and written back"""
    d = K(env)
    d.need_wb()
    best, msg = 0, ''
    for w in (8192, 7680, 5760, 5120, 4096, 3840, 2560, 1920):
        if w > d.card.max_w:
            continue
        try:
            fb, buf = fb_quadrants(d.card, w, 64, [RED, GREEN, BLUE, WHITE])
        except kms.DrmError as e:
            msg += ' %d: buffer %s;' % (w, e)
            continue
        cap, err = compose(d, [Layer(fb, w, 64, (0, 0, w, 64))], w=w, h=64)
        free(d.card, (fb, buf))
        if err:
            msg += ' %d: %s;' % (w, err)
            continue
        got = sample(cap, [(10, 10), (w - 10, 10), (10, 50), (w - 10, 50)])
        d.card.free_dumb(cap)
        if all(near(a, b) for a, b in zip(got, [RED, GREEN, BLUE, WHITE])):
            best = w
            break
        msg += ' %d: wrong pixels %s;' % (w, got)
    return ('pass' if best else 'fail',
            'widest frame composed and verified in writeback: %d px (mode_config max_width %d);%s'
            % (best, d.card.max_w, msg), {'width': best, 'max_width': d.card.max_w})


ROT_EXP = {'rotate-0': [RED, GREEN, BLUE, WHITE], 'reflect-x': [GREEN, RED, WHITE, BLUE],
           'reflect-y': [BLUE, WHITE, RED, GREEN], 'rotate-180': [WHITE, BLUE, GREEN, RED],
           'rotate-90': [GREEN, WHITE, RED, BLUE], 'rotate-270': [BLUE, RED, WHITE, GREEN]}


@wbtest('display.rotation')
def t_rotation(env):
    """Rotation and reflection: plane rotation values verified by pixels"""
    d = K(env)
    d.need_wb()
    rot = [p for p in d.planes_for(d.wb_crtc()) if 'rotation' in p['props']]
    if not rot:
        return 'fail', 'no plane exposes a rotation property'
    # prefer a plane that offers 90 degrees
    rp = next((p for p in rot if 'rotate-90' in d.card.prop_info(p['props']['rotation'][0])['enums']),
              rot[0])
    en = d.card.prop_info(rp['props']['rotation'][0])['enums']
    fb = fb_quadrants(d.card, 128, 128, [RED, GREEN, BLUE, WHITE])
    res, good_l = [], []
    for name in ('rotate-0', 'reflect-x', 'reflect-y', 'rotate-180', 'rotate-90', 'rotate-270'):
        if name == 'rotate-180' and 'rotate-180' not in en:
            if 'reflect-x' not in en or 'reflect-y' not in en:
                continue
            val = (1 << en['rotate-0']) | (1 << en['reflect-x']) | (1 << en['reflect-y'])
        elif name not in en:
            res.append('%s not offered' % name)
            continue
        elif name.startswith('reflect'):
            val = (1 << en['rotate-0']) | (1 << en[name])
        else:
            val = 1 << en[name]
        cap, err = compose(d, [Layer(fb[0], 128, 128, (0, 0, 128, 128), props={'rotation': val})],
                           w=128, h=128)
        if err:
            res.append('%s refused (%s)' % (name, err.split(':')[-1].strip()))
            continue
        got = sample(cap, [(20, 20), (108, 20), (20, 108), (108, 108)])
        d.card.free_dumb(cap)
        good = all(near(a, b, 12) for a, b in zip(got, ROT_EXP[name]))
        res.append('%s %s' % (name, 'ok' if good else 'wrong'))
        if good:
            good_l.append(name)
    free(d.card, fb)
    ok90 = 'rotate-90' in good_l
    wrong = [r for r in res if r.endswith(' wrong') or 'refused' in r]
    refused90 = any(r.startswith('rotate-90 refused') for r in res)
    if any(r.endswith(' wrong') for r in res):
        st, note = 'fail', '; an accepted rotation produced wrong pixels'
    elif refused90 and good_l:
        st, note = 'partial', ('; 90 degree rotation is offered but refused for the linear XR24 source '
                               'this test builds (it may need a tiled or compressed YUV source)')
    else:
        st = 'fail' if not ok90 else 'partial' if wrong else 'pass'
        note = '' if ok90 else '; 90 degree rotation not proven'
    return (st, 'plane %d rotation values %s; %s%s' % (rp['id'], sorted(en), '; '.join(res), note),
            {'verified': good_l})


@test('display.modifiers')
def t_modifiers(env, vendor=None):
    """Framebuffer modifiers: non-linear (tiled or compressed) layouts the planes accept"""
    d = K(env)
    seen = {}
    for p in d.planes:
        for f, mods in d.card.in_formats(p).items():
            for m in mods:
                if m not in (kms.MOD_LINEAR, kms.MOD_INVALID):
                    seen.setdefault(kms.mod_str(m), set()).add(kms.fourcc_str(f))
    if vendor:
        seen = {k: v for k, v in seen.items() if k.startswith(vendor + ':')}
    if not seen:
        return 'fail', 'no plane advertises a %snon-linear modifier in IN_FORMATS' % (
            vendor + ' ' if vendor else '')
    return ('partial', 'non-linear modifiers advertised: %s; content not verified (needs a '
            'producer of that layout such as the GPU or the video decoder)' % '; '.join(
                '%s for %s' % (k, ' '.join(sorted(v))) for k, v in sorted(seen.items())),
            {'modifiers': sorted(seen)})


@wbtest('display.crtc_lut')
def t_crtc_lut(env, prop='GAMMA_LUT'):
    """CRTC colour management (GAMMA_LUT, DEGAMMA_LUT, CTM) applied to the output"""
    d = K(env)
    crtc = d.wb_crtc()
    cp = d.crtc_props[crtc]
    if prop not in cp:
        return 'fail', 'CRTC %d has no %s property' % (crtc, prop)
    d.need_wb()
    size_prop = {'GAMMA_LUT': 'GAMMA_LUT_SIZE', 'DEGAMMA_LUT': 'DEGAMMA_LUT_SIZE'}.get(prop)
    if prop == 'CTM':
        one = 1 << 32           # S31.32: swap red and blue
        blob = d.card.create_blob(struct.pack('<9Q', 0, 0, one, 0, one, 0, one, 0, 0))
        want = (50, 100, 200)
    else:
        n = cp[size_prop][1] if size_prop in cp else 0
        if not n:
            return 'fail', '%s present but %s is 0' % (prop, size_prop)
        ent = b''.join(struct.pack('<HHHH', v, v, v, 0) for v in
                       [0xffff - (i * 0xffff // (n - 1)) for i in range(n)])
        blob = d.card.create_blob(ent)
        want = (55, 155, 205)
    fb = fb_solid(d.card, WB_W, WB_H, (200, 100, 50))
    cap, err = compose(d, [Layer(fb[0], WB_W, WB_H, (0, 0, WB_W, WB_H))], crtc_extra={prop: blob})
    d.card.destroy_blob(blob)
    free(d.card, fb)
    if err:
        return 'fail', '%s commit: %s' % (prop, err)
    got = px(cap, 100, 100)
    d.card.free_dumb(cap)
    if near(got, want, 12):
        return 'pass', '%s applied: (200,100,50) -> %s, expected ~%s' % (prop, got, want)
    if near(got, (200, 100, 50), 6):
        return ('partial', '%s accepted on the writeback CRTC but the capture at the writeback '
                'output is unchanged %s; the effect needs a sink capture to observe' % (prop, got))
    return 'fail', '%s gave %s, expected ~%s' % (prop, got, want)


@test('display.property')
def t_property(env, obj, names, feature):
    """A KMS property for a feature exists on a plane, CRTC or connector"""
    d = K(env)
    found = set()
    if obj == 'plane':
        for p in d.planes:
            found |= set(n for n in names if n in p['props'])
    elif obj == 'crtc':
        for cp in d.crtc_props.values():
            found |= set(n for n in names if n in cp)
    else:
        for cp in d.conn_props.values():
            found |= set(n for n in names if n in cp)
    if not found:
        return 'fail', '%s: no %s exposes %s' % (feature, obj, ' or '.join(names))
    return ('partial', '%s: %s property %s present; effect not verified' % (
        feature, obj, ', '.join(sorted(found))))


@test('display.connector_property')
def t_connector_property(env, connector, names, feature, commit=False):
    """A connector property for a feature on a connected sink, optionally set in a commit"""
    d = K(env)
    k = d.need_connected(connector)
    props = d.conn_props[k['id']]
    have_ = [n for n in names if n in props]
    if not have_:
        return 'fail', '%s: %s exposes none of %s (properties: %s)' % (
            feature, k['name'], ', '.join(names), ', '.join(sorted(props)))
    info = d.card.prop_info(props[have_[0]][0])
    vals = list(info['enums']) or info['values']
    if not commit:
        return 'partial', '%s: %s has %s %s; effect not verified on the sink' % (
            feature, k['name'], have_[0], vals)
    hi = info['values'][-1] if info['values'] and not info['enums'] else None
    if hi is None:
        return 'partial', '%s: %s has %s %s; not committed' % (feature, k['name'], have_[0], vals)
    err = light(d, k, extra_conn={have_[0]: hi})
    if err:
        return 'fail', '%s: %s range %s, commit at %s: %s' % (feature, have_[0], vals, hi, err)
    return ('partial', '%s: %s range %s, commit at %s accepted; the value on the link is not read '
            'back' % (feature, have_[0], vals, hi), {have_[0].replace(' ', '_'): hi})


@test('display.vrr')
def t_vrr(env):
    """Variable refresh rate: vrr_capable connector and VRR_ENABLED CRTC"""
    d = K(env)
    crtc_vrr = [cr for cr, cp in d.crtc_props.items() if 'VRR_ENABLED' in cp]
    conn_vrr = [k['name'] for k in d.conns if 'vrr_capable' in d.conn_props[k['id']]]
    if not conn_vrr:
        return ('fail', 'no connector exposes vrr_capable (VRR_ENABLED on %d of %d CRTCs)'
                % (len(crtc_vrr), len(d.card.crtcs)))
    return 'partial', 'vrr_capable on %s; not exercised on a VRR sink' % conn_vrr


@test('display.clone_writeback')
def t_clone_writeback(env, connector='primary'):
    """Concurrent writeback: capture the frame of an active display"""
    d = K(env)
    d.need_wb()
    ks = d.need_type(connector)
    wbc = d.card.possible_crtcs(d.wb)
    shared = sorted(set(wbc) & set(c for k in ks for c in d.card.possible_crtcs(k)))
    if not shared:
        return ('fail', 'the writeback connector shares no CRTC with %s (writeback CRTCs %s): '
                'no clone writeback' % (d.ctype(connector), wbc))
    k = d.need_connected(connector)
    crtc = next((c for c in d.card.possible_crtcs(k) if c in shared), None)
    if crtc is None:
        return 'fail', 'no CRTC drives both %s and the writeback connector' % k['name']
    mode = pick_mode(k, 'preferred')
    for tries in range(1, 4):
        r = clone_once(d, k, crtc, mode)
        if r:
            break
    else:
        return 'fail', ('%s lit at %dx%d and the writeback fence signalled 3 times, but every '
                        'capture still holds the fill pattern' % (k['name'], mode.hdisplay, mode.vdisplay))
    if tries > 1:
        r = (r[0], r[1] + '; %d empty capture(s) retried' % (tries - 1)) + r[2:]
    return r


def clone_once(d, k, crtc, mode):
    """One commit lighting connector k and the writeback connector from
    crtc; the result, or None when the capture was left empty."""
    w, h = mode.hdisplay, mode.vdisplay
    c = d.card
    cols = [RED, GREEN, BLUE, WHITE]
    fb = fb_quadrants(c, w, h, cols)
    mb = c.create_blob(mode.raw)
    cap = c.dumb(w, h, 32)
    cap['map'][:] = b'\x5a' * cap['size']
    capfb = c.addfb2(w, h, XR24, [cap['handle']], [cap['pitch']], [0])
    pls = d.planes_for(crtc)
    pl = next((p for p in pls if p['type'] == 'Primary'), pls[0])
    cp, kp, wp = d.crtc_props[crtc], d.conn_props[k['id']], d.conn_props[d.wb['id']]
    fence = kms.ctypes.c_int32(-1)
    req = {crtc: crtc_req(d, crtc, mb),
           k['id']: {kp['CRTC_ID'][0]: crtc},
           d.wb['id']: {wp['CRTC_ID'][0]: crtc, wp['WRITEBACK_FB_ID'][0]: capfb,
                        wp['WRITEBACK_OUT_FENCE_PTR'][0]: kms.ctypes.addressof(fence)},
           pl['id']: plane_req(pl, crtc, Layer(fb[0], w, h, (0, 0, w, h)), c)}
    for p in d.planes:
        pc = c.props(p['id'], kms.OBJ_PLANE).get('CRTC_ID', (0, 0))
        if pc[1] == crtc and p['id'] != pl['id']:
            req[p['id']] = {p['props']['FB_ID'][0]: 0, pc[0]: 0}
    err, got = None, None
    try:
        c.atomic(req, kms.ALLOW_MODESET)
        st = kms.wait_fence(fence.value, 2.0)
        if st != 1:
            err = 'writeback fence %s' % ('did not signal within 2 s' if st == 0 else
                                          'missing' if st is None else 'signalled with error %d' % st)
        else:
            got = sample(cap, [(w // 4, h // 4), (3 * w // 4, h // 4), (w // 4, 3 * h // 4),
                               (3 * w // 4, 3 * h // 4)])
        time.sleep(1.0)
        ls = c.props(k['id'], kms.OBJ_CONNECTOR).get('link-status')
        if err is None and ls and c.enum_name(ls) == 'Bad':
            err = 'clone commit accepted but %s link-status is Bad' % k['name']
        if err is None and not c.props(crtc, kms.OBJ_CRTC)['ACTIVE'][1]:
            err = 'the CRTC went inactive after the clone capture'
    except kms.DrmError as e:
        err = 'clone commit refused: %s' % e
    off = {crtc: {cp['MODE_ID'][0]: 0, cp['ACTIVE'][0]: 0}, k['id']: {kp['CRTC_ID'][0]: 0},
           d.wb['id']: {wp['CRTC_ID'][0]: 0},
           pl['id']: {pl['props']['FB_ID'][0]: 0, pl['props']['CRTC_ID'][0]: 0}}
    try:
        c.atomic(off, kms.ALLOW_MODESET)
    except kms.DrmError:
        pass
    c.rmfb(capfb)
    free(c, fb)
    c.destroy_blob(mb)
    if err:
        c.free_dumb(cap)
        return 'fail', '%s at %dx%d on CRTC %d: %s' % (k['name'], w, h, crtc, err)
    empty = untouched(cap, w, h)
    c.free_dumb(cap)
    if empty:
        return None
    ok = all(near(g, e) for g, e in zip(got, cols))
    return ('pass' if ok else 'fail',
            '%s at %dx%d and writeback on CRTC %d in one commit: quadrants %s (expected red, '
            'green, blue, white); link-status good, CRTC still active' % (k['name'], w, h, crtc, got),
            {'width': w, 'height': h})


@test('display.multi')
def t_multi(env, n=2):
    """Concurrent displays: n connected sinks lit at the same time"""
    d = K(env)
    con = [k for k in d.conns if k['connected'] and k['type'] != kms.CONN_IDS['Writeback']]
    if len(con) < n:
        why = env.cfg('absent', 'displays-%d' % n)
        raise Skip('needs %d displays; connected: %s%s' % (
            n, ', '.join(k['name'] for k in con) or 'none', '; board: %s' % why if why else ''))
    errs = [light(d, k, hold=0.2) for k in con[:n]]
    return ('partial' if not any(errs) else 'fail',
            'connected %s lit one after the other (%s); simultaneous commit not written yet' % (
                ', '.join(k['name'] for k in con[:n]), errs), {'displays': len(con)})


@test('display.backlight')
def t_backlight(env):
    """Brightness control: a backlight device changes brightness"""
    base = '/sys/class/backlight'
    bl = sorted(os.listdir(base)) if os.path.isdir(base) else []
    if not bl:
        raise Missing('no backlight device in /sys/class/backlight', hw='backlight')
    b = os.path.join(base, bl[0])
    mx, cur = int(read(b + '/max_brightness') or 0), int(read(b + '/brightness') or 0)
    try:
        with open(b + '/brightness', 'w') as f:
            f.write(str(mx // 2))
        got = int(read(b + '/actual_brightness') or -1)
        with open(b + '/brightness', 'w') as f:
            f.write(str(cur))
    except OSError as e:
        return 'fail', '%s: brightness write refused: %s' % (bl[0], e)
    return ('pass' if got == mx // 2 else 'fail',
            '%s: max %d, set %d, actual %d' % (bl[0], mx, mx // 2, got), {'levels': mx})


@test('display.touch')
def t_touch(env):
    """Touch input: a multitouch input device is registered"""
    devs = read('/proc/bus/input/devices').split('\n\n')
    touch = []
    for blk in devs:
        abs_ = next((l.split('=', 1)[1] for l in blk.splitlines() if l.startswith('B: ABS=')), '')
        bits = int(abs_.replace(' ', ''), 16) if abs_ else 0
        if bits & (1 << 0x35) and bits & (1 << 0x36):   # ABS_MT_POSITION_X/Y
            touch.append(next((l.split('=', 1)[1] for l in blk.splitlines()
                               if l.startswith('N: Name=')), '?'))
    if not touch:
        raise Missing('no multitouch input device (ABS_MT_POSITION_X/Y) registered', hw='touch')
    return 'partial', 'multitouch input devices %s; touch events not exercised' % touch


@test('display.boot_handoff')
def t_boot_handoff(env):
    """Boot display: the boot loader hands a framebuffer to Linux"""
    rc, out = sh("ls /sys/firmware/devicetree/base/chosen 2>/dev/null | grep -i framebuffer; "
                 "dmesg | grep -iE 'simple-framebuffer|simpledrm|efifb' | head -3")
    fb = read('/proc/fb').strip().replace('\n', ' | ')
    if out.strip():
        return 'pass', 'boot loader framebuffer hand-off: %s; /proc/fb: %s' % (
            out.strip().replace('\n', ' | '), fb or 'empty')
    return ('fail', 'no boot loader framebuffer hand-off (no simple-framebuffer, simpledrm or '
            'efifb; /proc/fb: %s)' % (fb or 'empty'))


@test('display.summary')
def t_summary(env, what='display configuration'):
    """Display pipeline inventory for requirements with no measurable target"""
    d = K(env)
    return ('partial', '%s: no measurable target; %s with %d CRTCs, %d planes, connectors %s, '
            'max %dx%d' % (what, d.driver(), len(d.card.crtcs), len(d.planes),
                           ', '.join(k['name'] for k in d.conns), d.card.max_w, d.card.max_h),
            {'crtcs': len(d.card.crtcs), 'planes': len(d.planes)})


# ---- Wayland
def gpu_ns(pid):
    """Sum of the drm-engine-* busy times in a process's fdinfo, None when
    the driver reports none (DRM client usage stats)."""
    tot, seen = 0, False
    for f in glob.glob('/proc/%s/fdinfo/*' % pid):
        for m in re.finditer(r'^drm-engine-\S+:\s+(\d+) ns', read(f), re.M):
            tot += int(m.group(1))
            seen = True
    return tot if seen else None


def wl_env(env):
    """Environment for a Wayland client: the profile's socket, else the
    first wayland-* socket under /run/user."""
    w = env.cfg('display', 'wayland', default={}) or {}
    rt, sock = w.get('runtime_dir'), w.get('display')
    if not (rt and sock and os.path.exists(os.path.join(rt, sock))):
        found = sorted(p for p in glob.glob('/run/user/*/wayland-*') if not p.endswith('.lock'))
        if found:
            rt, sock = os.path.split(found[0])
    rt, sock = rt or '/run/user/0', sock or 'wayland-0'
    return 'XDG_RUNTIME_DIR=%s WAYLAND_DISPLAY=%s' % (rt, sock), os.path.join(rt, sock)


@test('display.wayland_gl')
def t_wayland_gl(env, seconds=5, renderer=None, version=None,
                 renderer_not=('llvmpipe', 'softpipe', 'pixman')):
    """Wayland compositor with a hardware GL renderer, and an EGL client renders on it"""
    st, log = env.ctx('display').compositor()
    if st != 'active':
        return 'fail', 'compositor unit %s is %s after start; journal: %s' % (
            env.ctx('display').unit, st, log.replace('\n', ' | ')[-600:])
    # a compositor can take tens of seconds to open its socket: 39 s for
    # Weston on llvmpipe in an emulated arm64 guest
    for i in range(60):
        e, sock = wl_env(env)
        if os.path.exists(sock):
            break
        time.sleep(1)
    else:
        return 'fail', 'compositor active but no Wayland socket at %s after 60 s' % sock
    if not have('eglinfo'):
        raise Missing('eglinfo not on the image: the EGL renderer cannot be read', hw='eglinfo')
    rc, info = sh('%s eglinfo -B -p wayland 2>&1' % e, timeout=20)
    rend = next((l.split(':', 1)[1].strip() for l in info.splitlines()
                 if re.search(r'opengl es profile renderer|renderer string', l, re.I)), '')
    ver = next((l.split(':', 1)[1].strip() for l in info.splitlines()
                if 'opengl es profile version' in l.lower()), '')
    meas = {'renderer': rend, 'version': ver}
    if rend and any(x in rend.lower() for x in renderer_not) and \
            not env.cfg('display', 'software_gl', default=False):
        return 'fail', 'EGL renders on a software renderer: %s' % rend, meas
    if renderer and not re.search(renderer, rend or ''):
        return 'fail', 'EGL renderer %s is not the required %s' % (rend or 'unknown', renderer), meas
    if version and not re.search(version, ver or ''):
        return 'fail', 'GL ES version string "%s" does not match the required %s (renderer %s)' % (
            ver or 'unknown', version, rend), meas
    if not have('weston-simple-egl'):
        return ('partial' if rend else 'fail', 'Wayland socket %s; renderer %s; '
                'weston-simple-egl not on the image' % (sock, rend or 'unknown'), meas)
    # without a connected output the compositor sends no frame callbacks,
    # so a client renders one frame and waits
    outs = [os.path.basename(os.path.dirname(p)) for p in glob.glob('/sys/class/drm/card*-*/status')
            if 'Writeback' not in p and read(p).strip() == 'connected']
    if not outs:
        t = env.cfg('display', 'primary', default='')
        why = env.cfg('absent', '%s-sink' % t)
        raise Skip('Weston runs with EGL renderer %s (%s), but no display output is connected, so '
                   'no client can render%s' % (rend or 'unknown', ver or 'version unknown',
                                               '; board: %s' % why if why else ''))
    # the client's own GPU time proves hardware rendering; the compositor's
    # is reported too but may stay flat when it scans the client buffer
    # out on an overlay plane
    pid = env.ctx('display').pid()
    w0 = gpu_ns(pid)
    p = subprocess.Popen('%s exec weston-simple-egl' % e, shell=True, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(2)
    c0 = gpu_ns(p.pid)
    busy_path = env.cfg('display', 'gpu_busy')
    busy = []
    for _ in range(seconds):
        time.sleep(1)
        if busy_path and os.path.exists(busy_path):
            n = re.findall(r'\d+', read(busy_path))
            if n:
                busy.append(int(n[0]))
    c1, w1 = gpu_ns(p.pid), gpu_ns(pid)
    alive = p.poll() is None
    p.terminate()
    try:
        out = p.communicate(timeout=10)[0].decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        p.kill()
        out = ''
    fps = [float(m) for m in re.findall(r'frames in [0-9.]+ seconds: ([0-9.]+) fps', out)]
    if fps:
        meas['fps'] = max(fps)
    if not alive:
        return 'fail', 'weston-simple-egl exited early (rc %s): %s' % (p.returncode, out.strip()[-300:]), meas
    if w0 is not None and w1 is not None:
        meas['compositor_gpu_ms'] = round((w1 - w0) / 1e6, 1)
    if (c0 is None or c1 is None) and busy:
        meas['gpu_busy'] = busy
        if max(busy) > 0:
            return ('pass' if rend else 'partial',
                    'Wayland %s, EGL renderer %s, %s, weston-simple-egl %s; the driver reports no '
                    'GPU time per client; GPU busy counter %s while the client ran: %s' % (
                        sock, rend or 'unknown', ver or 'version unknown',
                        'fps %s' % fps[-3:] if fps else 'ran', busy_path, busy), meas)
        return 'fail', 'GPU busy counter %s stayed 0 while the EGL client ran' % busy_path, meas
    if c0 is None or c1 is None:
        return ('partial', 'Wayland %s, EGL renderer %s, %s; the driver reports no GPU time per '
                'client, so hardware rendering is not proven' % (sock, rend, ver), meas)
    meas['client_gpu_ms'] = round((c1 - c0) / 1e6, 1)
    if c1 <= c0:
        return 'fail', 'the EGL client used no GPU time in %d s of rendering' % seconds, meas
    comp = 'client GPU time +%.1f ms in %d s, compositor %s' % (
        meas['client_gpu_ms'], seconds, '+%.1f ms' % meas['compositor_gpu_ms']
        if 'compositor_gpu_ms' in meas else 'not reported')
    return ('pass' if rend else 'partial',
            'Wayland %s, EGL renderer %s, %s, weston-simple-egl %s, %s' % (
                sock, rend or 'unknown', ver or 'version unknown',
                'fps %s' % fps[-3:] if fps else 'ran', comp), meas)
