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
      errors_max: {<error summary counter>: <max>}   (optional; checked when
                  the client prints its error summary, which CamX skips on
                  some closes)
      probe: <command run once to make CamX probe the sensors when nothing
              opened the stack yet, also read for the orientation report>
              (default snapshot)
      orientation: <pattern of a reported roll, or of (camera id, roll)>
      commands:
        <name>: {file: <command file text>, width: W, height: H,
                 jpeg: <glob>, yuv: <glob>, raw: <glob>}

A run is the client with one command file; its images are the files it
created in cwd (removed afterwards unless PRD_CAMX_KEEP is set). The ICP
firmware debug log of the run is read from the camera driver's
tracefs event (camera/cam_icp_fw_dbg) after raising the debugfs log
level of every ICP the profile lists under camx.icps."""
import collections
import ctypes
import fcntl
import fnmatch
import glob
import hashlib
import math
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import tempfile
import time

from prd.core import Missing, Skip, have, read, sh, test

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


def command_cfg(env, name):
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
    after it can be told apart (klog_after). journald takes a leading
    "word:" as the syslog identifier and prints only the rest."""
    m = 'prd-camx %s %d.%d' % (what, os.getpid(), int(time.time() * 1000))
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
    c, cmd = client(env), command_cfg(env, name)
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
            if got:
                p.append('no "%s" in the client error summary' % k)
        elif got[s] > int(mx):
            p.append('%s %d > %d' % (k, got[s], int(mx)))
    return got, p


# Sensors (camx.sensors: connector, name and slot as the kernel driver
# logs its probe, mounting roll, raw command and its frame geometry).
PROBE = re.compile(r'Probe (success|failed) for (\S+?)\s+slot:\s*(\d+)')


def sensor_cfg(env, i):
    ss = env.cfg('camx', 'sensors', default=None) or []
    if not 0 <= int(i) < len(ss):
        raise Missing('the board profile lists %d CamX sensors (camx.sensors), not sensor %s' % (len(ss), i),
                      hw='camx')
    return ss[int(i)]


def probes(log):
    """[(ok, name, slot)] of the kernel driver's sensor probe lines."""
    return [(m.group(1) == 'success', m.group(2).rstrip(','), int(m.group(3)))
            for m in PROBE.finditer(log)]


def probed(env):
    """Probe lines of this boot; CamX probes when a client opens the
    stack, so a boot without any runs the client's probe command once."""
    p = probes(klog())
    if not p:
        run_client(env, env.cfg('camx', 'client', 'probe', default='snapshot'), trace=False).cleanup()
        p = probes(klog())
    return p


@test('camx.sensor')
def t_sensor(env, sensor=0):
    """CamX sensor probe: the profile's sensor probes on its slot (other candidates on the slot may fail)"""
    need(env, 'CamX sensor probe')
    s = sensor_cfg(env, sensor)
    p = probed(env)
    slot = [(ok, n) for ok, n, sl in p if sl == int(s['slot'])]
    meas = {'slot': int(s['slot']), 'candidates_failed': sum(1 for ok, _ in slot if not ok)}
    if any(ok and n.lower() == s['name'].lower() for ok, n in slot):
        return 'pass', '%s %s probed on slot %d (%d other candidates failed there)' % (
            s.get('connector', ''), s['name'], int(s['slot']), meas['candidates_failed']), meas
    why = '%s %s did not probe on slot %d: %s' % (
        s.get('connector', ''), s['name'], int(s['slot']),
        ', '.join('%s %s' % ('success' if ok else 'failed', n) for ok, n in slot) or
        'no probe on that slot (%d probe lines this boot)' % len(p))
    absent = env.cfg('absent', 'camera-sensor-%s' % s.get('connector', s['slot']))
    if absent:
        raise Skip('%s; board: %s' % (why, absent))
    return 'fail', why, meas


def raw10_stats(path, w, h, stride, rows=64, groups=128):
    """Sampled pixel statistics of a MIPI RAW10 frame (4 pixels in 5 bytes)."""
    vals, bottom = [], []
    with open(path, 'rb') as f:
        for y in sorted(set(list(range(0, h, max(1, h // rows))) + [h - 1])):
            f.seek(y * stride)
            row = f.read(w * 5 // 4)
            out = bottom if y >= h - max(1, h // 8) else vals
            for g in range(0, len(row) // 5, max(1, (w // 4) // groups)):
                b = row[g * 5:g * 5 + 5]
                out.extend((b[i] << 2) | ((b[4] >> (2 * i)) & 3) for i in range(4))
    allv = vals + bottom
    if not allv:
        return {'mean': 0.0, 'std': 0.0, 'nonzero': 0.0, 'saturated': 0.0, 'levels': 0, 'bottom_nonzero': 0.0}
    m = sum(allv) / float(len(allv))
    return {'mean': round(m, 2), 'std': round((sum((x - m) ** 2 for x in allv) / len(allv)) ** 0.5, 2),
            'nonzero': round(sum(1 for x in allv if x) / float(len(allv)), 3),
            'saturated': round(sum(1 for x in allv if x >= 1023) / float(len(allv)), 3),
            'levels': len(set(allv)),
            'bottom_nonzero': round(sum(1 for x in bottom if x) / float(len(bottom)), 3) if bottom else 0.0}


def raw_problems(st):
    """A real scene: mostly non-zero (a frame the DMA never wrote is
    zero), not saturated, not flat, written to the last rows."""
    p = []
    if st['nonzero'] < 0.5:
        p.append('%.0f%% zero pixels' % (100 * (1 - st['nonzero'])))
    if st['bottom_nonzero'] < 0.5:
        p.append('the last rows are %.0f%% zero (frame not fully written)' % (100 * (1 - st['bottom_nonzero'])))
    if st['saturated'] > 0.25:
        p.append('%.0f%% saturated pixels' % (100 * st['saturated']))
    if st['std'] < 1.0 or st['levels'] < 16:
        p.append('flat image (std %.2f, %d levels)' % (st['std'], st['levels']))
    return p


def name_size(path):
    m = re.search(r'_w\[(\d+)\]_h\[(\d+)\]', os.path.basename(path))
    return (int(m.group(1)), int(m.group(2))) if m else None


@test('camx.raw')
def t_raw(env, sensor=0):
    """CamX raw capture: RAW10 frames of a real scene from one sensor (size, not zero, not saturated, not flat)"""
    need(env, 'CamX raw capture')
    s = sensor_cfg(env, sensor)
    w, h, stride = int(s['width']), int(s['height']), int(s.get('stride', int(s['width']) * 5 // 4))
    name = s.get('raw')
    if not name:
        raise Missing('the board profile gives no raw command for sensor %s (camx.sensors[].raw)' % sensor,
                      hw='camx')
    run = run_client(env, name, trace=False)
    try:
        pat = command_cfg(env, name).get('raw', '*.raw')
        files = [f for f in run.files if fnmatch.fnmatch(os.path.basename(f), pat)]
        prob = run.problems()
        meas = dict(run.meas(), frames=len(files), width=w, height=h, stride=stride)
        if not files:
            prob.append('no raw frame (%s) written' % pat)
        digests = set()
        for i, f in enumerate(files):
            size = os.path.getsize(f)
            meas['bytes'] = size
            if name_size(f) and name_size(f) != (w, h):
                prob.append('%s is %dx%d, not %dx%d' % (os.path.basename(f), name_size(f)[0],
                                                       name_size(f)[1], w, h))
            if size < stride * h:
                prob.append('frame %d has %d bytes, below %d x %d RAW10 at stride %d' % (i, size, w, h, stride))
                continue
            st = raw10_stats(f, w, h, stride)
            meas.update(('raw_' + k, v) for k, v in st.items())
            prob += ['frame %d: %s' % (i, x) for x in raw_problems(st)]
            digests.add(hashlib.sha256(open(f, 'rb').read()).hexdigest())
        if len(files) > 1 and len(digests) < 2:
            prob.append('all %d frames are identical (no new exposure)' % len(files))
        st = 'fail' if prob else 'pass'
        return st, '%s %s %dx%d RAW10: %d frames%s%s' % (
            s.get('connector', ''), s['name'], w, h, len(files),
            ', mean %s std %s of 1023' % (meas.get('raw_mean'), meas.get('raw_std')) if 'raw_mean' in meas else '',
            '; ' + '; '.join(prob) if prob else ''), meas
    finally:
        run.cleanup()


def dt_rolls():
    """{cell-index: sensor-position-roll} of the camera sensor DT nodes."""
    out = {}
    for d, _, files in os.walk('/proc/device-tree'):
        if 'sensor-position-roll' in files and 'cell-index' in files and \
                'cam-sensor' in read(os.path.join(d, 'compatible')):
            with open(os.path.join(d, 'cell-index'), 'rb') as f:
                idx = struct.unpack('>I', f.read(4))[0]
            with open(os.path.join(d, 'sensor-position-roll'), 'rb') as f:
                out[idx] = struct.unpack('>I', f.read(4))[0]
    return out


@test('camx.orientation')
def t_orientation(env):
    """CamX sensor orientation: each sensor's DT mounting roll and the orientation CamX reports match the profile"""
    need(env, 'CamX sensor orientation')
    client(env)
    ss = env.cfg('camx', 'sensors', default=None) or []
    if not ss:
        raise Missing('the board profile lists no CamX sensors (camx.sensors)', hw='camx')
    dt = dt_rolls()
    want = sorted(int(s.get('roll', 0)) for s in ss)
    meas, prob = {}, []
    for s in ss:
        got = dt.get(int(s['slot']))
        meas['roll_%s' % s.get('connector', s['slot'])] = got
        if got is None:
            prob.append('no camera sensor DT node with cell-index %d and sensor-position-roll' % int(s['slot']))
        elif got != int(s.get('roll', 0)):
            prob.append('%s DT roll %d, profile %d' % (s['name'], got, int(s.get('roll', 0))))
    pat = env.cfg('camx', 'client', 'orientation', default=None)
    if prob or not pat:
        return ('fail' if prob else 'partial', 'DT rolls %s%s' % (
            dt, '; ' + '; '.join(prob) if prob else
            '; the orientation CamX reports is not read (no camx.client.orientation pattern)'), meas)
    probe = env.cfg('camx', 'client', 'probe', default='snapshot')
    run = run_client(env, probe, trace=False)
    run.cleanup()
    meas.update(run.meas())
    rep = re.findall(pat, run.log)
    if not rep:
        return 'partial', 'DT rolls %s; the client log of command %s carries no orientation ' \
            '(camx.client.orientation), so the orientation CamX reports is not read' % (dt, probe), meas
    if isinstance(rep[0], tuple):
        # (camera id, roll): compared per sensor (camx.sensors[].camera)
        got = dict((int(c), int(r)) for c, r in rep)
        meas.update(('reported_%s' % s.get('connector', s['slot']), got.get(int(s['camera'])))
                    for s in ss if 'camera' in s)
        bad = ['%s camera %s reports %s, mounted %d' % (s['name'], s['camera'], got[int(s['camera'])],
                                                       int(s.get('roll', 0)))
               for s in ss if 'camera' in s and int(s['camera']) in got
               and got[int(s['camera'])] != int(s.get('roll', 0))]
        lack = [s['name'] for s in ss if 'camera' not in s or int(s['camera']) not in got]
    else:
        # rolls without camera ids, in no known order: fewer than one per
        # sensor can only be checked for values the profile does not mount
        got = [int(r) for r in rep]
        meas['reported'] = got
        short = len(got) < len(ss)
        bad = [] if (set(got) <= set(want) if short else set(got) == set(want)) else \
            ['CamX reports orientations %s, the profile mounts %s' % (got, want)]
        lack = ['%d orientations reported for %d sensors' % (len(got), len(ss))] if short else []
    if bad:
        return 'fail', 'DT rolls %s; %s' % (dt, '; '.join(bad)), meas
    if lack:
        return 'partial', 'DT rolls %s; CamX orientation not read for every sensor: %s' % (
            dt, ', '.join(lack)), meas
    return 'pass', 'DT rolls %s; CamX reports orientations %s' % (dt, rep), meas


# ISP path: images the client writes through the ICP (offline) pipeline.
def jpeg_info(d):
    """Frame size, scan length and entropy (bits per byte) of a baseline
    or progressive JPEG; ValueError when it is not one. SOF markers
    inside APP segments (an EXIF thumbnail) are skipped with them."""
    if d[:2] != b'\xff\xd8':
        raise ValueError('no SOI marker')
    end = len(d.rstrip(b'\0'))
    if d[end - 2:end] != b'\xff\xd9':
        raise ValueError('no EOI marker (truncated)')
    i, size = 2, None
    while i + 4 <= end:
        if d[i] != 0xff:
            raise ValueError('no marker at byte %d' % i)
        m = d[i + 1]
        if m == 0xff:
            i += 1
            continue
        n = struct.unpack('>H', d[i + 2:i + 4])[0]
        if 0xc0 <= m <= 0xcf and m not in (0xc4, 0xc8, 0xcc):
            h, w = struct.unpack('>HH', d[i + 5:i + 9])
            size = (w, h)
        if m == 0xda:
            if not size:
                raise ValueError('no SOF marker before the scan')
            scan = d[i + 2 + n:end - 2]
            c = collections.Counter(scan)
            ent = -sum(v / float(len(scan)) * math.log(v / float(len(scan)), 2) for v in c.values()) if scan else 0
            return {'width': size[0], 'height': size[1], 'scan_bytes': len(scan), 'entropy': round(ent, 2)}
        i += 2 + n
    raise ValueError('no SOS marker')


def jpeg_problems(path, w, h):
    try:
        j = jpeg_info(open(path, 'rb').read())
    except ValueError as e:
        return ['%s: %s' % (os.path.basename(path), e)], {}
    p = []
    if (j['width'], j['height']) != (w, h):
        p.append('JPEG is %dx%d, not %dx%d' % (j['width'], j['height'], w, h))
    # a flat frame (an ISP that wrote nothing) compresses to a few bytes
    # per 8x8 block; any scene is several times that
    if j['scan_bytes'] < w * h // 40 or j['entropy'] < 7.0:
        p.append('JPEG scan of %d bytes, entropy %.2f bits/byte (a flat or empty frame)' % (
            j['scan_bytes'], j['entropy']))
    return p, {'jpeg_' + k: v for k, v in j.items()}


def nv21_problems(path, w, h):
    """NV21/NV12 preview frame: the size of w x h (or of the stride and
    slice the client names), a luma plane that is not flat, not mostly
    zero and written to the last rows."""
    size = os.path.getsize(path)
    m = re.search(r'_stride\[(\d+)\]_slice\[(\d+)\]', os.path.basename(path))
    sizes = set([w * h * 3 // 2] + ([int(m.group(1)) * int(m.group(2)) * 3 // 2] if m else []))
    meas = {'yuv_bytes': size}
    if size not in sizes:
        return ['preview frame of %d bytes, not %s for %dx%d 4:2:0' % (size, sorted(sizes), w, h)], meas
    stride = w if size == w * h * 3 // 2 else int(m.group(1))
    with open(path, 'rb') as f:
        luma = f.read(stride * h)
    mean, std = luma_stats(path, stride * h)
    meas.update({'luma_mean': mean, 'luma_std': std})
    p = []
    if std < 2.0:
        p.append('flat luma (mean %.1f std %.2f)' % (mean, std))
    if luma[::97].count(0) > len(luma[::97]) // 2:
        p.append('luma mostly zero')
    if not luma[stride * (h - h // 8):].strip(b'\0'):
        p.append('the last rows are zero (frame not fully written)')
    return p, meas


def isp(env, name, kinds, trace=True):
    """Run commands.<name>; (problems, measurements, notes, fw counts or
    None) over the images of the given kinds (jpeg, yuv) and the firmware
    log. Notes say what could not be read (partial, never a pass)."""
    cmd = command_cfg(env, name)
    run = run_client(env, name, trace=trace)
    try:
        w, h = int(cmd['width']), int(cmd['height'])
        prob, meas, notes = run.problems(), run.meas(), []
        for k, default, check in (('jpeg', '*.jpg', jpeg_problems), ('yuv', '*.yuv', nv21_problems)):
            if k not in kinds:
                continue
            files = [f for f in run.files if fnmatch.fnmatch(os.path.basename(f), cmd.get(k, default))]
            meas['%s_files' % k] = len(files)
            if not files:
                prob.append('no %s image (%s) written' % (k.upper(), cmd.get(k, default)))
            for f in files:
                p, m = check(f, w, h)
                prob += p
                meas.update(m)
        summ, sp = summary_problems(env, run.log)
        prob += sp
        meas.update(('err_' + k, v) for k, v in summ.items())
        fw = parse_fw(run.fw) if run.fw is not None else None
        if fw:
            prob += ['firmware log: %s' % x for x in fw_problems(fw)]
            meas.update(('fw_' + k, v) for k, v in fw.items() if v is not None)
        if trace:
            notes += fw_notes(env, run)
        return prob, meas, notes, fw
    finally:
        run.cleanup()


def fw_notes(env, run):
    """What of the ICP firmware log a traced run could not read."""
    if run.fw is None:
        return ['the ICP firmware log was not read (no tracefs instance with the %s event, or no ICP '
                'debugfs under %s)' % (FW_EVENT, CAM_DEBUGFS)]
    lack = [i for i in env.cfg('camx', 'icps', default=None) or [] if i not in run.traced]
    return ['no firmware log from %s (debugfs log level not set)' % ' '.join(lack)] if lack else []


def isp_result(what, prob, meas, notes, fw):
    """fail on any problem; partial when something was not read."""
    if prob:
        return 'fail', '%s: %s' % (what, '; '.join(prob)), meas
    if notes:
        return 'partial', '%s; %s' % (what, '; '.join(notes)), meas
    return 'pass', '%s; firmware %s: %d frames, 0 hardware failures' % (
        what, fw['version'] or 'version not logged', fw['frames']), meas


@test('camx.snapshot')
def t_snapshot(env, command='snapshot'):
    """CamX ISP snapshot: a JPEG through the ICP pipeline (SOI/EOI, SOF size, entropy) with a clean firmware log"""
    need(env, 'CamX ISP snapshot')
    prob, meas, notes, fw = isp(env, command, ('jpeg',))
    return isp_result('command %s: JPEG %sx%s, scan %s bytes' % (
        command, meas.get('jpeg_width', '-'), meas.get('jpeg_height', '-'), meas.get('jpeg_scan_bytes', '-')),
        prob, meas, notes, fw)


@test('camx.yuv')
def t_yuv(env, command='snapshot'):
    """CamX ISP preview: a 4:2:0 preview frame through the ICP pipeline (size, luma) with a clean firmware log"""
    need(env, 'CamX ISP preview')
    prob, meas, notes, fw = isp(env, command, ('yuv',))
    return isp_result('command %s: preview %s bytes, luma std %s' % (
        command, meas.get('yuv_bytes', '-'), meas.get('luma_std', '-')), prob, meas, notes, fw)


@test('camx.isp_log')
def t_isp_log(env, command='snapshot'):
    """CamX ISP firmware log: frames completed, no hardware failure, CDM abort or frame error, error summary within limits

    Partial when the client printed no error summary (CamX skips it on
    some closes) or the firmware log was not read."""
    need(env, 'CamX ISP firmware log')
    prob, meas, notes, fw = isp(env, command, ())
    if not any(k.startswith('err_') for k in meas):
        notes.append('the client printed no error summary')
    return isp_result('command %s in %.1f s' % (command, meas['seconds']), prob, meas, notes, fw)


@test('camx.fps')
def t_fps(env, command='snapshot'):
    """CamX ISP preview frame rate, from the frame timing the client reports"""
    need(env, 'CamX ISP preview frame rate')
    cmd = command_cfg(env, command)
    pat = env.cfg('camx', 'client', 'fps', default=None)
    if not pat:
        client(env)
        return 'partial', 'the CamX client reports no frame timing the board profile names ' \
            '(camx.client.fps); frame rate not measured'
    run = run_client(env, command, trace=False)
    run.cleanup()
    got = [float(x) for x in re.findall(pat, run.log)]
    meas = dict(run.meas(), fps=got[-1] if got else None)
    prob = run.problems()
    if not got:
        prob.append('no frame rate in the client log (%s)' % pat)
    elif cmd.get('fps') and got[-1] < 0.9 * float(cmd['fps']):
        prob.append('fps %.2f below 90%% of %s' % (got[-1], cmd['fps']))
    return ('fail' if prob else 'pass', 'command %s: fps %s%s' % (
        command, meas['fps'], '; ' + '; '.join(prob) if prob else ''), meas)


# ICP firmware and camera power (camx.firmware, camx.gdsc: the camera
# top power domain as the genpd summary names it).
GENPD = '/sys/kernel/debug/pm_genpd/pm_genpd_summary'


def gdsc_state(env):
    """on, off, or None when the profile names no domain or the genpd
    summary does not list it."""
    name = env.cfg('camx', 'gdsc', default=None)
    for l in read(GENPD).splitlines() if name else []:
        f = l.split()
        if len(f) > 1 and f[0] == name:
            return 'off' if f[1].startswith('off') else f[1]
    return None


def wait_gdsc_off(env):
    """(state, seconds waited) once the domain is off or after camx.gdsc_off_s."""
    limit, t0 = float(env.cfg('camx', 'gdsc_off_s', default=10)), time.time()
    while True:
        st = gdsc_state(env)
        if st != 'on' or time.time() - t0 >= limit:
            return st, round(time.time() - t0, 1)
        time.sleep(0.5)


@test('camx.icp_fw')
def t_icp_fw(env, command='snapshot'):
    """CamX ICP firmware: every ICP loads its firmware through the TEE PAS and boots it; the version is recorded"""
    need(env, 'CamX ICP firmware load')
    icps = env.cfg('camx', 'icps', default=None) or []
    if not icps:
        raise Missing('the board profile lists no ICPs (camx.icps)', hw='camx')
    prob = ['%s is not on the image' % f for f in env.cfg('camx', 'firmware', default=None) or []
            if not os.path.exists(f)]
    run = run_client(env, command, trace=True)
    run.cleanup()
    log = klog()
    for i in icps:
        if not re.search(r'\[%s\] FW download done successfully' % re.escape(i), log):
            prob.append('no "[%s] FW download done" in the kernel log' % i)
    pas = len(re.findall(r'\[sec-PIL\] Loaded MBN firmware', log))
    if not pas:
        prob.append('no "[sec-PIL] Loaded MBN firmware" (not loaded through the TEE PAS)')
    auth = re.findall(r'auth and reset failed rc=(-?\d+)', log)
    if auth:
        prob.append('%d PAS auth and reset failures (rc %s)' % (len(auth), ','.join(sorted(set(auth)))))
    fw = parse_fw(run.fw) if run.fw is not None else None
    meas = dict(run.meas(), pas_loads=pas, icps=len(icps), fw_version=fw['version'] if fw else None)
    what = '%d ICPs (%s), %d secure loads' % (len(icps), ' '.join(icps), pas)
    if prob:
        return 'fail', '%s: %s' % (what, '; '.join(prob)), meas
    notes = fw_notes(env, run)
    if fw and not fw['version']:
        notes.append('no version string in the ICP firmware log')
    if notes:
        return 'partial', '%s; firmware version not read: %s' % (what, '; '.join(notes)), meas
    return 'pass', '%s; firmware %s' % (what, meas['fw_version']), meas


def cycles(env, command, runs, kinds=('jpeg', 'yuv')):
    """runs client runs, each from a powered down camera (the domain must
    go off after every close); (status, reason, measurements)."""
    prob, meas, unread = [], {'runs': runs, 'clean': 0, 'gdsc_off': 0}, []
    for i in range(runs + 1):
        st, waited = wait_gdsc_off(env)
        if st is None:
            unread.append('the power state of camx.gdsc %s is not in %s' % (
                env.cfg('camx', 'gdsc', default='(not set)'), GENPD))
        elif st == 'on':
            prob.append('%s still on %.0f s %s (no power collapse)' % (
                env.cfg('camx', 'gdsc'), waited, 'after run %d' % i if i else 'before the first run'))
            break
        else:
            meas['gdsc_off'] += 1
        if i == runs:
            break
        p, m, notes, _ = isp(env, command, kinds)
        unread += notes
        meas.update(m)
        if p:
            prob.append('run %d of %d: %s' % (i + 1, runs, '; '.join(p)))
            break
        meas['clean'] += 1
    what = '%d of %d runs of %s clean, power domain off %d times' % (meas['clean'], runs, command,
                                                                     meas['gdsc_off'])
    if prob:
        return 'fail', '%s: %s' % (what, '; '.join(prob)), meas
    if unread:
        return 'partial', '%s; %s' % (what, '; '.join(sorted(set(unread)))), meas
    return 'pass', what, meas


@test('camx.power')
def t_power(env, runs=3, command='snapshot', boot=False):
    """CamX power cycle: repeated client runs, each clean, with the camera power domain off between them (boot: the first camera use of this boot)"""
    need(env, 'CamX power cycle')
    client(env)
    command_cfg(env, command)
    if boot and re.search(r'FW download done', klog()):
        return 'partial', 'the camera was already opened in this boot (ICP firmware download in the kernel ' \
            'log); run this requirement first after a reboot to measure it'
    return cycles(env, command, int(runs))


@test('camx.first_frame')
def t_first_frame(env, command='snapshot'):
    """CamX ISP first frame after a power up: a snapshot through the offline (ICP) pipeline completes with a clean firmware log

    The camera power domain must be off before the run, so the run
    starts from a full power up of the camera. It fails when the
    offline pipeline stalls on its first request, the way it does when
    the power up omits the AXI sleep stage sequence the camera needs:
    the firmware logs ProcessHWFailure and a CDM "Loop reach" abort, no
    request completes (no CAMERAICP_SUCCESS) and no JPEG is written.
    It is partial when the firmware log or the power state cannot be
    read, never a pass."""
    need(env, 'CamX ISP first frame after a power up')
    return cycles(env, command, 1, kinds=('jpeg',))


OFF = '.prd-off'


def restore(fws):
    """Move back every file of fws that is aside (f + OFF, f absent), each
    on its own, and sync; the [(file, error)] that could not be."""
    errs = []
    for f in fws:
        if os.path.exists(f + OFF) and not os.path.exists(f):
            try:
                os.rename(f + OFF, f)
            except OSError as e:
                errs.append((f, e))
    os.sync()
    return errs


@test('camx.no_firmware')
def t_no_firmware(env, command='snapshot'):
    """CamX missing ICP firmware: with the firmware moved aside the client fails cleanly within its timeout; the files are restored

    Fails when the client hangs (still running at camx.client.timeout,
    whether it then takes SIGTERM or needs SIGKILL), exits 0 or writes
    an image of the command without its firmware, when the kernel log
    of the run (after a mark the test writes) names not every missing
    firmware file, on a kernel warning or oops in it, or when a file
    cannot be put back. Files a crashed earlier run left aside are put
    back first. Partial when the firmware was already loaded in this
    boot (the driver keeps it across opens, so the missing file is never
    read: run it alone first after a reboot), when the kernel log of the
    run cannot be told apart, or when the run after the restore does not
    give a JPEG again."""
    need(env, 'CamX missing ICP firmware')
    fws = env.cfg('camx', 'firmware', default=None) or []
    if not fws:
        raise Missing('the board profile lists no ICP firmware files (camx.firmware)', hw='camx')
    client(env)
    cmd = command_cfg(env, command)
    left = [f for f in fws if os.path.exists(f + OFF) and not os.path.exists(f)]
    errs = restore(left)
    if errs:
        return 'fail', 'could not put back firmware an earlier run left aside: %s' % (
            '; '.join('%s: %s' % fe for fe in errs))
    pre = 'put back %s left aside by an earlier run; ' % ' '.join(left) if left else ''
    lost = [f for f in fws if not os.path.exists(f)]
    if lost:
        return 'fail', '%sfirmware not on the image: %s' % (pre, ' '.join(lost))
    if re.search(r'Loaded MBN firmware|FW download done', klog()):
        return 'partial', '%sthe ICP firmware was already loaded in this boot and the driver keeps it ' \
            'across opens; run this requirement alone first after a reboot to measure it' % pre
    m = mark('no_firmware')
    moved = []
    try:
        for f in fws:
            try:
                os.rename(f, f + OFF)
            except OSError as e:
                return 'partial', '%scould not move %s aside: %s' % (pre, f, e)
            moved.append(f)
        run = run_client(env, command, trace=False)
    finally:
        errs = restore(moved)
        if errs:
            raise OSError('could not put the firmware back: %s' % '; '.join('%s: %s' % fe for fe in errs))
    run.cleanup()
    names = [os.path.basename(f) for f in fws]
    imgs = [f for f in run.files if any(fnmatch.fnmatch(os.path.basename(f), cmd.get(k, d)) for k, d in (
        ('jpeg', '*.jpg'), ('yuv', '*.yuv'), ('raw', '*.raw')))]
    meas = dict(run.meas(), images=len(imgs))
    what = '%sfirmware %s moved aside: client rc %d after %.1f s' % (pre, ' '.join(names), run.rc, run.seconds)
    prob = []
    if run.hung:
        prob.append('the client hung without its firmware (%s)' % run.problems()[0])
    elif run.rc == 0:
        prob.append('the client exited 0 without its firmware')
    if imgs:
        prob.append('the client wrote %d images without its firmware' % len(imgs))
    log = klog_after(m)
    if log is not None:
        lack = [n for n in names if n not in log]
        if lack:
            prob.append('no kernel log line of the run names the missing %s' % ' '.join(lack))
        oops = sorted(set(re.findall(r'cut here|Oops|BUG:|hung_task', log)))
        if oops:
            prob.append('kernel %s during the run' % ', '.join(oops))
    if prob:
        return 'fail', '%s: %s' % (what, '; '.join(prob)), meas
    if log is None:
        return 'partial', '%s; the kernel log of the run cannot be told apart (no mark in /dev/kmsg), so the ' \
            'reason and the kernel state are not checked' % what, meas
    p, _, _, _ = isp(env, command, ('jpeg',), trace=False)
    meas['recovered'] = not p
    if p:
        return 'partial', '%s, a clean error; with the firmware restored the next run fails: %s' % (
            what, '; '.join(p)), meas
    return 'pass', '%s, a clean error; restored, the next run writes a JPEG again' % what, meas
