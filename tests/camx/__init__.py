# SPDX-License-Identifier: GPL-2.0
"""Optional vendor module: the Qualcomm CamX camera stack (its camera
kernel driver, the camera service and the qtiqmmfsrc GStreamer source).

CamX does not stream through V4L2 capture nodes or libcamera: its
kernel driver registers control nodes under a media device of its own
(model "cam-req-mgr-devnode"), and frames reach applications through
the camera service and qtiqmmfsrc. A board profile enables the module
with modules.camx: true on images that ship CamX and describes the
cameras the stack exposes:

  camx:
    service: <systemd unit of the camera service>   (default qti-cam-server)
    cameras:
      - {id: <camera id>, width: W, height: H, fps: F, format: NV12}

Without modules.camx, the camx.cameras list, systemctl or qtiqmmfsrc
the tests report the missing piece (a fail, or a skip in a product with
"missing: skip"). A driver, service or camera that does not come up is
a fail. Preview and concurrent streams fail below 90% of the profile's
frame rate, and every stream fails when the camera service restarted
during it.

Images without the camera service run a CamX test client driven by a
command file instead. The profile gives the client and its commands:

  camx:
    client:
      cmd: <client> -f {cmdfile}     ({cmdfile}: the command file written
                                      from commands.<name>.file)
      cwd: <directory the client writes its images to>
      env: {VAR: value}              (optional)
      timeout: <seconds>             (default 120)
      errors_max: {<error summary counter>: <max>}   (optional)
      commands:
        <name>: {file: <command file text>, width: W, height: H,
                 jpeg: <glob>, yuv: <glob>, raw: <glob>}

A run is the client with one command file; its images are the files it
created in cwd (removed afterwards unless PRD_CAMX_KEEP is set). The ICP
firmware debug log of the run is read from the camera driver's
tracefs event (camera/cam_icp_fw_dbg) after raising the debugfs log
level of every ICP the profile lists under camx.icps."""
import ctypes
import fcntl
import glob
import hashlib
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import tempfile
import time

from prd.core import Missing, have, read, sh, test

REQ_MGR_MODEL = 'cam-req-mgr-devnode'
# entity functions of the CamX kernel driver (MEDIA_ENT_F_OLD_BASE + n,
# camera/media/cam_req_mgr.h)
F_BASE = 0x10000
ROLES = {F_BASE + 1: 'sensor', F_BASE + 2: 'ife', F_BASE + 3: 'icp', F_BASE + 5: 'jpeg',
         F_BASE + 7: 'cpas', F_BASE + 8: 'csiphy', F_BASE + 10: 'cci', F_BASE + 16: 'tfe',
         F_BASE + 17: 'cre', F_BASE + 18: 'tpg', F_BASE + 19: 'tfe_mc'}
ISP = ('ife', 'tfe', 'tfe_mc')
# seconds a camera may take to open before its stream window starts
START_S = 5


def _iowr(nr, size):
    return (3 << 30) | (size << 16) | (ord('|') << 8) | nr


DEVICE_INFO = _iowr(0, 256)
G_TOPOLOGY = _iowr(4, 72)
ENTITY = struct.Struct('<I64sII20x')
INTERFACE = struct.Struct('<III36x64x')
LINK = struct.Struct('<IIII24x')
INTF_V4L_VIDEO = 0x200
LNK_INTERFACE = 1 << 28


def addr(buf):
    return ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf)) if buf else 0


def topology(path):
    """(model, [(entity name, function, video node)]) of a media device.
    The camera driver's sensor function equals MEDIA_ENT_F_IO_V4L, so
    entities behind a V4L video interface are told apart by their
    interface links."""
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    try:
        info = bytearray(256)
        fcntl.ioctl(fd, DEVICE_INFO, info, True)
        model = info[16:48].split(b'\0')[0].decode('utf-8', 'replace')
        topo = bytearray(72)
        fcntl.ioctl(fd, G_TOPOLOGY, topo, True)
        ne, ni, nl = (struct.unpack_from('<I', topo, o)[0] for o in (8, 24, 56))
        ents, intfs, links = bytearray(ENTITY.size * ne), bytearray(INTERFACE.size * ni), \
            bytearray(LINK.size * nl)
        for o, n, b in ((8, ne, ents), (24, ni, intfs), (56, nl, links)):
            struct.pack_into('<IIQ', topo, o, n, 0, addr(b))
        fcntl.ioctl(fd, G_TOPOLOGY, topo, True)
        video = set(INTERFACE.unpack_from(intfs, i * INTERFACE.size)[0] for i in range(ni)
                    if INTERFACE.unpack_from(intfs, i * INTERFACE.size)[1] == INTF_V4L_VIDEO)
        nodes = set()
        for i in range(nl):
            _, src, sink, flags = LINK.unpack_from(links, i * LINK.size)
            if flags & LNK_INTERFACE and src in video:
                nodes.add(sink)
        out = []
        for i in range(ne):
            eid, name, func, _ = ENTITY.unpack_from(ents, i * ENTITY.size)
            out.append((name.split(b'\0')[0].decode('utf-8', 'replace'), func, eid in nodes))
        return model, out
    finally:
        os.close(fd)


def roles(ents):
    n = {}
    for _, func, node in ents:
        r = None if node else ROLES.get(func)
        if r:
            n[r] = n.get(r, 0) + 1
    return n


def kmd_graphs():
    """{media device: (model, entities)} of the CamX request manager."""
    out = {}
    for p in sorted(glob.glob('/dev/media[0-9]*')):
        try:
            model, ents = topology(p)
        except OSError:
            continue
        if model == REQ_MGR_MODEL:
            out[p] = (model, ents)
    return out


def need(env, feature):
    if not env.module('camx'):
        raise Missing('%s: no standard Linux interface provides it and the board profile does not '
                      'enable the CamX module (modules.camx)' % feature, hw='camx')


def cameras(env):
    cams = env.cfg('camx', 'cameras', default=None) or []
    if not cams:
        raise Missing('the board profile lists no CamX cameras (camx.cameras)', hw='camx')
    return cams


def find_camera(env, cid):
    for c in cameras(env):
        if int(c['id']) == int(cid):
            return c
    raise Missing('camera %s is not in the board profile (camx.cameras ids %s)' % (
        cid, [c['id'] for c in cameras(env)]), hw='camx')


def need_qmmfsrc():
    if not have('gst-launch-1.0') or sh('gst-inspect-1.0 --exists qtiqmmfsrc')[0]:
        raise Missing('GStreamer qtiqmmfsrc element not on the image', hw='camx')


def caps(c):
    """Caps of a camera's stream, quoted for the shell."""
    return "'video/x-raw(memory:GBM),format=%s,width=%d,height=%d,framerate=%d/1'" % (
        c.get('format', 'NV12'), int(c['width']), int(c['height']), int(c['fps']))


def pts_fps(times):
    """Frame rate from buffer timestamps (seconds), first frame excluded."""
    if len(times) < 3 or times[-1] <= times[1]:
        return 0.0
    return round((len(times) - 2) / (times[-1] - times[1]), 2)


def parse_sinks(log):
    """{sink index: [pts in seconds]} from gst-launch -v fakesink lines."""
    out = {}
    for m in re.finditer(r'GstFakeSink:sink(\d+): last-message = chain.*?pts: (\d+):(\d+):([\d.]+)', log):
        out.setdefault(int(m.group(1)), []).append(
            int(m.group(2)) * 3600 + int(m.group(3)) * 60 + float(m.group(4)))
    return out


def pipeline(cams):
    return ' '.join('qtiqmmfsrc name=cam%d camera=%d ! %s ! fakesink name=sink%d silent=false sync=false' % (
        int(c['id']), int(c['id']), caps(c), int(c['id'])) for c in cams)


def stream(cams, seconds):
    """Run one qtiqmmfsrc per camera into fakesinks; ({id: pts list}, rc, log)."""
    rc, log = sh('timeout -k 5 -s INT %d gst-launch-1.0 -e -v %s 2>&1' % (
        seconds + START_S, pipeline(cams)), timeout=seconds + START_S + 30)
    return parse_sinks(log), rc, log


def rate_ok(c, ts, seconds):
    """At 90% of the profile's rate over at least half the window (a stream
    that stalls after a few frames has the right rate over a short span)."""
    return len(ts) >= 3 and pts_fps(ts) >= 0.9 * int(c['fps']) and ts[-1] - ts[1] >= seconds / 2.0


def unit(env):
    return env.cfg('camx', 'service', default='qti-cam-server')


def restarts(env):
    _, out = sh('systemctl show -p NRestarts --value %s 2>/dev/null' % unit(env))
    out = out.strip()
    return int(out) if out.isdigit() else 0


def restarted(env, before, res):
    """Fail a stream result when the camera service was restarted during it."""
    n = restarts(env) - before
    if n <= 0:
        return res
    st, why = res[0], res[1]
    return ('fail', '%s; %s restarted %d times during the stream (was %s)' % (why, unit(env), n, st)) + \
        tuple(res[2:])


def tail(log, n=240):
    lines = [l for l in log.strip().splitlines() if 'last-message' not in l]
    return ' | '.join(lines)[-n:]


@test('camx.kmd')
def t_kmd(env):
    """CamX camera driver: request manager media device with sensor, CSIPHY and ISP subdevices"""
    need(env, 'CamX camera driver')
    g = kmd_graphs()
    if not g:
        others = []
        for p in sorted(glob.glob('/dev/media[0-9]*')):
            try:
                others.append('%s %s' % (p, topology(p)[0]))
            except OSError as e:
                others.append('%s (%s)' % (p, e.strerror))
        return 'fail', 'no media device with model %s (CamX driver not loaded): %s' % (
            REQ_MGR_MODEL, ', '.join(others) or 'no media device')
    md, (model, ents) = sorted(g.items())[0]
    n = roles(ents)
    want = len(cameras(env)) if env.cfg('camx', 'cameras') else 1
    isp = sum(n.get(r, 0) for r in ISP)
    miss = [k for k, v in (('sensor', n.get('sensor', 0) >= want), ('csiphy', n.get('csiphy', 0)),
                           ('isp', isp), ('cpas', n.get('cpas', 0))) if not v]
    meas = {'sensors': n.get('sensor', 0), 'csiphy': n.get('csiphy', 0), 'isp': isp,
            'icp': n.get('icp', 0), 'entities': len(ents)}
    return ('fail' if miss else 'pass', '%s: %d entities, %s%s' % (
        md, len(ents), ' '.join('%s=%d' % kv for kv in sorted(n.items())),
        '; missing %s (sensor slots wanted >= %d)' % (miss, want) if miss else ''), meas)


@test('camx.service')
def t_service(env):
    """CamX camera service: the systemd unit is active"""
    need(env, 'CamX camera service')
    u = unit(env)
    if not have('systemctl'):
        raise Missing('systemctl not on the image', hw='camx')
    rc, state = sh('systemctl is-active %s 2>&1' % u)
    state = state.strip()
    meas = {'restarts': restarts(env)}
    if state != 'active':
        _, j = sh('journalctl -b -u %s -n 6 --no-pager 2>&1' % u)
        return 'fail', '%s is %s: %s' % (u, state or 'unknown', tail(j)), meas
    return 'pass', '%s active, %d restarts' % (u, meas['restarts']), meas


@test('camx.preview')
def t_preview(env, camera=0, seconds=5):
    """CamX preview: qtiqmmfsrc streams one camera at the profile's size and rate"""
    need(env, 'CamX preview')
    c = find_camera(env, camera)
    need_qmmfsrc()
    before = restarts(env)
    got, rc, log = stream([c], seconds)
    ts = got.get(int(c['id']), [])
    meas = {'frames': len(ts), 'fps': pts_fps(ts), 'width': int(c['width']), 'height': int(c['height'])}
    ok = rate_ok(c, ts, seconds)
    return restarted(env, before, ('pass' if ok else 'fail', 'camera %s %dx%d %s@%d: %d buffers in %d s '
                                   '(%d s window + %d s to start), fps %s%s' % (
        c['id'], int(c['width']), int(c['height']), c.get('format', 'NV12'), int(c['fps']), len(ts),
        seconds + START_S, seconds, START_S, meas['fps'], '' if ok else '; rc %d: %s' % (rc, tail(log))), meas))


def luma_stats(path, nbytes, step=97):
    """(mean, standard deviation) of a byte sample over the first nbytes."""
    with open(path, 'rb') as f:
        data = f.read(nbytes)
    s = data[::step]
    if not s:
        return 0.0, 0.0
    m = sum(s) / float(len(s))
    return round(m, 2), round((sum((x - m) ** 2 for x in s) / len(s)) ** 0.5, 2)


@test('camx.capture')
def t_capture(env, camera=0, count=10):
    """CamX capture: frames written by qtiqmmfsrc carry an image (not constant, not repeated)"""
    need(env, 'CamX capture')
    c = find_camera(env, camera)
    need_qmmfsrc()
    d = tempfile.mkdtemp(prefix='prd-camx-')
    before = restarts(env)
    try:
        return restarted(env, before, capture(c, d, count))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def capture(c, d, count):
    w, h = int(c['width']), int(c['height'])
    secs = max(5, 2 * count // max(1, int(c['fps'])) + 3)
    rc, log = sh('timeout -k 5 -s INT %d gst-launch-1.0 -e qtiqmmfsrc name=cam camera=%d ! %s ! '
                 'multifilesink location=%s/f%%03d.yuv max-files=%d 2>&1' % (
                     secs, int(c['id']), caps(c), d, count), timeout=secs + 30)
    files = sorted(glob.glob(os.path.join(d, 'f*.yuv')))
    meas = {'frames': len(files), 'width': w, 'height': h}
    if len(files) < 2:
        return 'fail', 'camera %s: %d frames written; rc %d: %s' % (c['id'], len(files), rc, tail(log)), meas
    last, prev = files[-1], files[-2]
    size = os.path.getsize(last)
    meas['bytes'] = size
    if size < w * h:
        return 'fail', 'camera %s: frame of %d bytes is smaller than the %dx%d luma plane' % (
            c['id'], size, w, h), meas
    mean, std = luma_stats(last, w * h)
    digests = set(hashlib.sha256(open(f, 'rb').read()).hexdigest() for f in files[-2:])
    meas.update({'luma_mean': mean, 'luma_std': std, 'distinct': len(digests)})
    why = []
    if std < 2.0:
        why.append('constant image (luma std %.2f)' % std)
    if len(digests) < 2:
        why.append('the last two frames are identical (no new frames)')
    return ('fail' if why else 'pass', 'camera %s %dx%d: %d frames of %d bytes, luma mean %.1f std %.1f%s' % (
        c['id'], w, h, len(files), size, mean, std, '; ' + ', '.join(why) if why else ''), meas)


@test('camx.concurrent')
def t_concurrent(env, seconds=5):
    """CamX concurrency: every profile camera streams at the same time"""
    need(env, 'CamX concurrent cameras')
    cams = cameras(env)
    if len(cams) < 2:
        raise Missing('the board profile lists %d CamX camera, concurrency needs two' % len(cams),
                      hw='camera-2')
    need_qmmfsrc()
    before = restarts(env)
    got, rc, log = stream(cams, seconds)
    meas, low, parts = {}, [], []
    for c in cams:
        ts = got.get(int(c['id']), [])
        meas['frames_%s' % c['id']] = len(ts)
        meas['fps_%s' % c['id']] = pts_fps(ts)
        parts.append('camera %s %d buffers fps %s' % (c['id'], len(ts), pts_fps(ts)))
        if not rate_ok(c, ts, seconds):
            low.append(str(c['id']))
    meas['fps_min'] = min(meas['fps_%s' % c['id']] for c in cams)
    return restarted(env, before, ('fail' if low else 'pass', '%d cameras for %d s (+%d s to start): %s%s' % (
        len(cams), seconds, START_S, ', '.join(parts),
        '; cameras %s below 90%% of their rate over the window; rc %d: %s' % (low, rc, tail(log))
        if low else ''), meas))


# The CamX test client (camx.client) and what a run of it leaves behind.
TRACING = ('/sys/kernel/tracing', '/sys/kernel/debug/tracing')
FW_EVENT = 'camera/cam_icp_fw_dbg'
CAM_DEBUGFS = '/sys/kernel/debug/camera'
# tracefs instance of the firmware log: its own buffer and switches, so
# the global trace buffer and its settings stay as they were
INSTANCE = 'prd-camx'


def client(env):
    c = env.cfg('camx', 'client') or {}
    if not c.get('cmd'):
        raise Missing('the board profile names no CamX test client (camx.client.cmd)', hw='camx')
    exe = c['cmd'].split()[0]
    if not (os.path.isabs(exe) and os.access(exe, os.X_OK)) and not have(exe):
        raise Missing('CamX test client %s not on the image' % exe, hw='camx')
    return c


def command(env, name):
    cmds = env.cfg('camx', 'client', 'commands', default=None) or {}
    if name not in cmds or not cmds[name].get('file'):
        raise Missing('the board profile has no CamX client command %r (camx.client.commands)' % name,
                      hw='camx')
    return cmds[name]


def klog():
    """Kernel log of this boot."""
    if have('journalctl'):
        rc, out = sh('journalctl -k -b --no-pager -o cat 2>/dev/null')
        if rc == 0 and out.strip():
            return out
    return sh('dmesg 2>/dev/null')[1]


def mark(what):
    """Write a unique line to the kernel log and return it, so the log
    after it can be told apart (klog_after)."""
    m = 'prd-camx: %s %d.%d' % (what, os.getpid(), int(time.time() * 1000))
    return m if put('/dev/kmsg', m + '\n') else None


def klog_after(m):
    """Kernel log after mark m; None when m is not in it."""
    log = klog()
    i = log.rfind(m) if m else -1
    return log[i + len(m):] if i >= 0 else None


def put(path, val):
    try:
        with open(path, 'w') as f:
            f.write(str(val))
        return True
    except OSError:
        return False


def get(path):
    try:
        with open(path, errors='replace') as f:
            return f.read()
    except OSError:
        return None


class Trace:
    """ICP firmware debug log of one client run: the camera driver's
    tracefs event in an instance of its own, with each ICP's debugfs
    log type and level raised for the run and restored after it."""

    def __init__(self, env):
        self.icps = env.cfg('camx', 'icps', default=None) or []
        top = next((d for d in TRACING if os.path.isdir(os.path.join(d, 'instances'))), None)
        self.dir = os.path.join(top, 'instances', INSTANCE) if top else None
        self.saved, self.on = {}, []

    def start(self):
        """The ICPs whose log is traced (empty when none can be)."""
        if not self.dir:
            return []
        try:
            os.mkdir(self.dir)
        except FileExistsError:
            pass
        except OSError:
            return []
        if not put(os.path.join(self.dir, 'buffer_size_kb'), 8192) or \
                not put(os.path.join(self.dir, 'events', FW_EVENT, 'enable'), 1):
            self.stop()
            return []
        for i in self.icps:
            files = [os.path.join(CAM_DEBUGFS, i, n) for n in ('icp_debug_type', 'icp_debug_lvl')]
            old = [get(f) for f in files]
            if None in old:
                continue
            self.saved.update(zip(files, (o.strip() for o in old)))
            if put(files[0], 1) and put(files[1], 63):
                self.on.append(i)
        if not self.on:
            self.stop()
        return self.on

    def stop(self):
        text = get(os.path.join(self.dir, 'trace')) or ''
        for f, v in self.saved.items():
            put(f, v)
        put(os.path.join(self.dir, 'events', FW_EVENT, 'enable'), 0)
        try:
            os.rmdir(self.dir)
        except OSError:
            pass
        return text


class Run:
    """One client run: exit status, whether it hung, log, the files it
    wrote, the ICP firmware log (None when it could not be traced) with
    the ICPs traced, and its duration."""

    def __init__(self, rc, hung, log, files, fw, traced, seconds, limit):
        self.rc, self.hung, self.log, self.files, self.fw, self.traced, self.seconds, self.limit = \
            rc, hung, log, files, fw, traced, seconds, limit

    def problems(self):
        if self.hung:
            return ['the client did not exit within %d s (rc %d after %.1f s)' % (
                self.limit, self.rc, self.seconds)]
        return ['the client exited with %d: %s' % (self.rc, tail(self.log))] if self.rc else []

    def meas(self):
        return {'rc': self.rc, 'seconds': self.seconds}

    def cleanup(self):
        if not os.environ.get('PRD_CAMX_KEEP'):
            for f in self.files:
                try:
                    os.unlink(f)
                except OSError:
                    pass


def run_client(env, name, trace=True):
    """Run the client on command file commands.<name> from cwd, in a
    session of its own: on timeout the whole group gets SIGTERM, then
    SIGKILL. Its files are those in cwd modified since the start (one
    second earlier, for file systems with coarse timestamps)."""
    c, cmd = client(env), command(env, name)
    cwd = c.get('cwd') or tempfile.gettempdir()
    os.makedirs(cwd, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix='prd-camx-', suffix='.txt')
    with os.fdopen(fd, 'w') as f:
        f.write(cmd['file'].strip() + '\n')
    tr = Trace(env) if trace else None
    traced = tr.start() if tr else []
    limit = int(c.get('timeout', 120))
    penv = dict(os.environ, **{k: str(v) for k, v in (c.get('env') or {}).items()})
    t0, hung = time.time(), False
    p = subprocess.Popen(c['cmd'].format(cmdfile=shlex.quote(path)), shell=True, cwd=cwd, env=penv,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        try:
            out = p.communicate(timeout=limit)[0]
        except subprocess.TimeoutExpired:
            hung = True
            out = b''
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(p.pid, sig)
                    out = p.communicate(timeout=min(5, limit))[0]
                    break
                except subprocess.TimeoutExpired:
                    continue
                except ProcessLookupError:
                    break
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
        fw = tr.stop() if traced else None
        os.unlink(path)
    secs = round(time.time() - t0, 1)
    rc = p.returncode if p.returncode >= 0 else 128 - p.returncode
    # a client run under timeout(1) reports a hang as 124 or 137
    hung = hung or rc in (124, 137) or secs >= limit
    files = sorted(os.path.join(cwd, f) for f in os.listdir(cwd)
                   if os.path.isfile(os.path.join(cwd, f)) and os.path.getmtime(os.path.join(cwd, f)) >= t0 - 1)
    return Run(rc, hung, (out or b'').decode('utf-8', 'replace'), files, fw, traced, secs, limit)


def parse_fw(text):
    """Counts and version string from an ICP firmware debug log."""
    lines = text.splitlines()
    m = re.search(r'QC_IMAGE_VERSION_STRING=(\S+)', text)
    return {'version': m.group(1) if m else None,
            'frames': sum('CAMERAICP_SUCCESS' in l for l in lines),
            'hw_failures': sum('ProcessHWFailure' in l for l in lines),
            'cdm_loops': sum('Loop reach' in l for l in lines),
            'frame_errors': sum('frame error' in l.lower() for l in lines)}


def fw_problems(fw):
    p = ['%d %s' % (fw[k], w) for k, w in (('hw_failures', 'ProcessHWFailure'),
                                           ('cdm_loops', 'CDM "Loop reach" aborts'),
                                           ('frame_errors', 'frame errors')) if fw[k]]
    if not fw['frames']:
        p.append('no CAMERAICP_SUCCESS (no frame completed in the firmware)')
    return p


def parse_summary(log):
    """{counter: n} from the CamX error summary ("Num <counter> : <n>")."""
    return {re.sub(r'\W+', '_', m.group(1).strip().lower()): int(m.group(2))
            for m in re.finditer(r'^.*?\bNum (.+?)\s*:\s*(\d+)\s*$', log, re.M)}


def summary_problems(env, log):
    got = parse_summary(log)
    p = []
    for k, mx in sorted((env.cfg('camx', 'client', 'errors_max', default=None) or {}).items()):
        s = re.sub(r'\W+', '_', k.strip().lower())
        if s not in got:
            p.append('no "%s" in the client error summary' % k)
        elif got[s] > int(mx):
            p.append('%s %d > %d' % (k, got[s], int(mx)))
    return got, p
