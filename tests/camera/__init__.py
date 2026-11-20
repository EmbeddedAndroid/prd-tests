# SPDX-License-Identifier: GPL-2.0
"""Camera tests on the media controller, V4L2 capture and libcamera.

Sensors are found as media graph entities with the camera sensor
function; libcamera tests use whatever cameras `cam -l` lists. A raw
capture through the media graph needs the links and formats of the
board (board profile camera.raw: media-ctl setup lines, the video
entity, pixel format and size); without it the raw test skips.

camera.v4l2_capture streams from any V4L2 capture node that is not
part of a media pipeline (vivid, UVC). Board profile keys: camera.raw {setup: [media-ctl arguments, where
{sensor} is the first sensor entity], video: entity name, pixelformat,
width, height}, absent.camera-sensor."""
import os
import re
import tempfile

from prd.core import Missing, have, sh, test


class Camera:
    def __init__(self, env):
        self.env = env
        self.tmp = tempfile.mkdtemp(prefix='prd-camera-')
        self.graphs = {}
        for m in sorted(os.listdir('/dev')):
            if re.match(r'media\d+$', m) and have('media-ctl'):
                rc, out = sh('media-ctl -d /dev/%s -p 2>&1' % m)
                self.graphs['/dev/' + m] = out
        self.sensors = []
        for md, g in self.graphs.items():
            for ent in re.split(r'\n(?=- entity )', g):
                m = re.match(r'- entity \d+: (.+?) \(', ent)
                if m and ('V4L2_SUBDEV_CAMERA_SENSOR' in ent or 'type V4L2 subdev subtype Sensor' in ent):
                    self.sensors.append((md, m.group(1)))
        print('media devices %s; sensors %s' % (list(self.graphs), self.sensors), flush=True)

    def model(self, md):
        m = re.search(r'model\s+(.*)', self.graphs[md])
        return m.group(1).strip() if m else '?'

    def need_sensor(self):
        if not self.sensors:
            raise Missing('no camera sensor entity in any media graph (%s)' % (
                ', '.join('%s %s' % (md, self.model(md)) for md in self.graphs) or 'no media device'),
                hw='camera-sensor')


def discover(env):
    return Camera(env)


def C(env):
    return env.ctx('camera')


@test('camera.graph')
def t_graph(env):
    """Media controller: a camera media graph with a sensor linked to capture video nodes"""
    c = C(env)
    if not c.graphs:
        raise Missing('no media controller device (/dev/media*)', hw='camera')
    c.need_sensor()
    md, s = c.sensors[0]
    g = c.graphs[md]
    nodes = len(re.findall(r'device node name /dev/video', g))
    ents = len(re.findall(r'^- entity', g, re.M))
    return ('pass' if nodes else 'fail', '%s (%s): %d entities, %d video nodes, sensors %s' % (
        md, c.model(md), ents, nodes, [x[1] for x in c.sensors]),
        {'sensors': len(c.sensors), 'video_nodes': nodes})


@test('camera.raw_capture')
def t_raw_capture(env, count=8):
    """Raw capture through the media graph: frames of the sensor's size are dequeued"""
    c = C(env)
    c.need_sensor()
    raw = env.cfg('camera', 'raw')
    if not raw:
        raise Missing('the board profile gives no media graph setup for a raw capture (camera.raw)',
                      hw='camera-raw-graph')
    md, sensor = c.sensors[0]
    sh('media-ctl -d %s --reset' % md)
    for a in raw.get('setup', []):
        a = a.replace('{sensor}', sensor)
        rc, out = sh('media-ctl -d %s %s 2>&1' % (md, a))
        if rc:
            return 'fail', 'media-ctl %s: %s' % (a, out.strip()[-200:])
    rc, vd = sh('media-ctl -d %s -e "%s"' % (md, raw['video']))
    vd = vd.strip()
    out = os.path.join(c.tmp, 'raw.bin')
    w, h = raw['width'], raw['height']
    rc, log = sh('timeout 30 v4l2-ctl -d %s --set-fmt-video=width=%d,height=%d,pixelformat=%s '
                 '--stream-mmap=4 --stream-count=%d --stream-to=%s --verbose 2>&1' % (
                     vd, w, h, raw['pixelformat'], count, out), timeout=40)
    n = log.count('cap dqbuf')
    size = os.path.getsize(out) if os.path.exists(out) else 0
    fps = re.findall(r'fps: ([\d.]+)', log)
    meas = {'frames': n, 'width': w, 'height': h, 'fps': float(fps[-1]) if fps else 0}
    ok = rc == 0 and n >= count and size > 0
    return ('pass' if ok else 'fail', '%s %dx%d %s: %d buffers dequeued, %d bytes, fps %s%s' % (
        vd, w, h, raw['pixelformat'], n, size, fps[-1] if fps else '-',
        '' if ok else '; rc %d: %s' % (rc, log.strip()[-200:])), meas)


@test('camera.libcamera')
def t_libcamera(env, count=30, width=1920, height=1080, pixelformat='BGR888'):
    """libcamera: cam lists the camera and captures frames at a size"""
    c = C(env)
    if not have('cam'):
        return 'fail', 'libcamera cam tool not on the image'
    rc, out = sh('cam -l 2>&1', timeout=60)
    cams = re.findall(r'^\s*\d+: (.*)$', out, re.M)
    if not cams:
        if not c.sensors:
            c.need_sensor()
        return 'fail', 'cam -l lists no camera although the media graph has sensors %s: %s' % (
            c.sensors, out.strip()[-200:])
    rc, log = sh('LIBCAMERA_SOFTISP_MODE=${LIBCAMERA_SOFTISP_MODE:-cpu} timeout 60 cam -c 1 '
                 '--capture=%d -s width=%d,height=%d,pixelformat=%s 2>&1' % (
                     count, width, height, pixelformat), timeout=70)
    n = len(re.findall(r'seq: \d+', log))
    ts = [float(x) for x in re.findall(r'\(([\d.]+) fps\)', log)]
    meas = {'frames': n, 'fps': round(sum(ts[1:]) / len(ts[1:]), 2) if len(ts) > 1 else 0,
            'width': width, 'height': height}
    return ('pass' if n >= count else 'fail', 'cameras %s; %d of %d frames %dx%d %s, fps %s%s' % (
        cams, n, count, width, height, pixelformat, meas['fps'],
        '' if n >= count else '; rc %d: %s' % (rc, log.strip()[-200:])), meas)


@test('camera.libcamerasrc')
def t_libcamerasrc(env, seconds=5):
    """GStreamer libcamerasrc: frames flow from the camera through a pipeline"""
    C(env).need_sensor()
    if sh('gst-inspect-1.0 --exists libcamerasrc')[0]:
        return 'fail', 'GStreamer libcamerasrc element not on the image'
    rc, out = sh('LIBCAMERA_SOFTISP_MODE=${LIBCAMERA_SOFTISP_MODE:-cpu} timeout -s INT %d '
                 'gst-launch-1.0 -e -v libcamerasrc ! video/x-raw,width=1280,height=720 ! '
                 'fakesink silent=false 2>&1' % seconds, timeout=seconds + 20)
    n = out.count('last-message = chain')
    return ('pass' if n > seconds else 'fail', 'libcamerasrc 1280x720: %d buffers in %d s%s' % (
        n, seconds, '' if n > seconds else '; %s' % out.strip()[-200:]),
        {'frames': n, 'fps': round(n / float(seconds), 1)})


@test('camera.v4l2_capture')
def t_v4l2_capture(env, count=30, width=640, height=480):
    """V4L2 capture: a capture node outside a media pipeline streams frames"""
    from prd import v4l2
    cap = v4l2.CAP_VIDEO_CAPTURE | v4l2.CAP_VIDEO_CAPTURE_MPLANE
    devs = [d for d in v4l2.devices() if not d.m2m and d.caps & cap and
            not d.caps & v4l2.CAP_IO_MC and d.formats('capture')]
    if not devs:
        raise Missing('no V4L2 video capture node outside a media pipeline', hw='v4l2-capture')
    d = devs[0]
    fmts = [f for f, fl, _ in d.formats('capture')]
    if not have('v4l2-ctl'):
        return 'fail', 'v4l2-ctl not on the image'
    rc, log = sh('timeout 30 v4l2-ctl -d %s --set-fmt-video=width=%d,height=%d --stream-mmap=4 '
                 '--stream-count=%d --stream-to=/dev/null --verbose 2>&1' % (d.path, width, height,
                                                                            count), timeout=40)
    n = log.count('cap dqbuf')
    fps = re.findall(r'fps: ([\d.]+)', log)
    meas = {'frames': n, 'fps': float(fps[-1]) if fps else 0}
    return ('pass' if rc == 0 and n >= count else 'fail', '%s (%s, %s): %d of %d frames %dx%d, formats %s%s' % (
        d.path, d.card, d.driver, n, count, width, height, ' '.join(fmts[:8]),
        '' if rc == 0 and n >= count else '; rc %d: %s' % (rc, log.strip()[-200:])), meas)
