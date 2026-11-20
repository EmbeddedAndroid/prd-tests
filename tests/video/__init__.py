# SPDX-License-Identifier: GPL-2.0
"""Video tests on V4L2 stateful memory-to-memory codecs and the
GStreamer v4l2 elements.

Codecs are discovered by their formats: a memory-to-memory device whose
OUTPUT queue takes a compressed format is a decoder, one whose CAPTURE
queue produces one is an encoder; nothing depends on the driver or the
device node number. Stateless (request API) decoders are reported as
such and are not used by these stateful tests. Pipelines use the GStreamer v4l2 element for the
codec (found by gst-inspect), encode generated content and decode it
back, or decode public conformance vectors from the overlay's vectors
directory (tools/fetch-vectors.sh, passed to tools/mk-overlay.sh) with
their expected frame counts."""
import glob
import os
import re
import subprocess
import tempfile
import time

from prd import v4l2
from prd.core import Missing, have, sh, test

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GST = {'H264': 'h264', 'HEVC': 'h265', 'VP90': 'vp9', 'AV01': 'av1', 'VP80': 'vp8',
       'MPG2': 'mpeg2'}
PARSE = {'H264': 'h264parse', 'HEVC': 'h265parse', 'VP90': 'vp9parse', 'AV01': 'av1parse',
         'VP80': '', 'MPG2': 'mpegvideoparse'}


class Video:
    def __init__(self, env):
        self.devs = v4l2.devices()
        self.codecs = v4l2.codecs(self.devs)
        for d in self.devs:
            print('V4L2 %s %s (%s) caps 0x%x m2m %s out %s cap %s' % (
                d.path, d.card, d.driver, d.caps, d.m2m,
                ' '.join('%s/%x' % (f, fl) for f, fl, _ in d.formats('output')),
                ' '.join('%s/%x' % (f, fl) for f, fl, _ in d.formats('capture'))), flush=True)
        self.tmp = tempfile.mkdtemp(prefix='prd-video-')
        self.elems = None
        print('V4L2 codecs: %s' % '; '.join('%s %s %s (%s): %s' % (
            c['role'], c['dev'].path, c['dev'].card, c['dev'].driver, ' '.join(c['coded']))
            for c in self.codecs) or 'none', flush=True)

    def find(self, role, codec):
        cs = [c for c in self.codecs if c['role'] == role and codec in c['coded']]
        if not cs:
            have_ = sorted(set(f for c in self.codecs if c['role'] == role for f in c['coded']))
            raise Missing('no V4L2 %s takes %s (%s formats: %s)' % (
                role, codec, role, ' '.join(have_) or 'none'), hw='video-%s-%s' % (role, codec))
        return cs[0]

    def element(self, role, codec):
        if self.elems is None:
            rc, out = sh('gst-inspect-1.0 2>/dev/null | grep -E "^video4linux2:"')
            self.elems = re.findall(r'video4linux2:\s+(\S+):', out)
        suf = GST.get(codec, codec.lower()) + ('dec' if role == 'decoder' else 'enc')
        cand = [e for e in self.elems if e.endswith(suf) and e.startswith('v4l2')]
        if not cand:
            raise Missing('no GStreamer v4l2 element for %s %s (v4l2 elements: %s)' % (
                codec, role, ' '.join(self.elems) or 'none'), hw='gst-v4l2-%s-%s' % (role, codec))
        return sorted(cand, key=len)[0]


def discover(env):
    if not have('gst-launch-1.0'):
        print('gst-launch-1.0 not on the image: pipeline tests will fail', flush=True)
    return Video(env)


def V(env):
    return env.ctx('video')


def gst(log, pipeline, timeout=120):
    rc, out = sh('timeout %d gst-launch-1.0 -v %s > %s 2>&1' % (timeout, pipeline, log),
                 timeout=timeout + 10)
    txt = open(log, errors='replace').read() if os.path.exists(log) else out
    return rc, txt


def frames(txt):
    return txt.count('last-message = chain')


def gst_err(txt):
    e = [l for l in txt.splitlines() if 'ERROR' in l or 'error' in l.split(':')[0]]
    return ' | '.join(e[:3])[:300] or txt.strip().splitlines()[-1][:200] if txt.strip() else ''


def out_caps(txt, elem):
    m = re.findall(r'%s\d*\.GstPad:src: caps = video/x-raw[^\n]*' % re.escape(elem), txt)
    if not m:
        return None, None, None
    s = m[-1]
    w = re.search(r'width=\(int\)(\d+)', s)
    h = re.search(r'height=\(int\)(\d+)', s)
    f = re.search(r'(?<![-\w])format=\(string\)(\w+)', s)
    f = f.group(1) if f else None
    if f == 'DMA_DRM':
        # dmabuf caps carry the layout as a DRM fourcc[:modifier]
        d = re.search(r'drm-format=\(string\)"?([\w:]+)', s)
        f = d.group(1) if d else f
    return (int(w.group(1)) if w else None, int(h.group(1)) if h else None, f)


# ---- discovery
@test('video.codec')
def t_codec(env, role, codec):
    """A V4L2 decoder or encoder for a codec, with its frame size range"""
    v = V(env)
    c = v.find(role, codec)
    fs = c['dev'].framesizes(codec)
    meas = {'max_width': fs[2], 'max_height': fs[3]} if fs else {}
    return ('pass', '%s %s on %s (%s, %s); coded sizes %s; raw formats %s' % (
        codec, role, c['dev'].path, c['dev'].card, c['dev'].driver,
        '%dx%d to %dx%d' % fs if fs else 'not enumerated', ' '.join(c['raw'])), meas)


@test('video.raw_formats')
def t_raw_formats(env, role, codec='H264'):
    """Raw pixel formats on the uncompressed side of a codec"""
    v = V(env)
    c = v.find(role, codec)
    side = 'capture (decoded output)' if role == 'decoder' else 'output (encoder input)'
    return 'pass', '%s %s formats: %s' % (role, side, ' '.join(c['raw'])), {'formats': c['raw']}


@test('video.controls')
def t_controls(env, names, role='encoder', codec='H264'):
    """Codec controls exposed by the V4L2 device (by V4L2 control name)"""
    v = V(env)
    c = v.find(role, codec)
    ctl = c['dev'].controls()
    got = [n for n in names if n in ctl]
    miss = [n for n in names if n not in ctl]
    desc = '%s controls present: %s; missing: %s' % (role, ' '.join(got) or 'none',
                                                     ' '.join(miss) or 'none')
    return ('pass' if not miss else 'partial' if got else 'fail', desc,
            {'present': got, 'missing': miss})


# ---- pipelines
SRC = 'videotestsrc pattern=smpte num-buffers=%d ! video/x-raw,format=%s,width=%d,height=%d,framerate=30/1'


def encode_decode(v, codec, w, h, n, controls='', out_caps_='', fmt='NV12', tag='e'):
    enc = v.element('encoder', codec)
    dec = v.element('decoder', codec)
    p = PARSE[codec]
    mp4 = os.path.join(v.tmp, '%s.mp4' % tag)
    ex = ' extra-controls="c,%s"' % controls if controls else ''
    oc = ' ! %s' % out_caps_ if out_caps_ else ''
    rc, txt = gst(os.path.join(v.tmp, tag + '-enc.log'), '%s ! %s%s%s ! %s ! mp4mux ! filesink location=%s'
                  % (SRC % (n, fmt, w, h), enc, ex, oc, p, mp4))
    size = os.path.getsize(mp4) if os.path.exists(mp4) else 0
    if rc or not size:
        return None, 'encode %s rc %d, %d bytes: %s' % (enc, rc, size, gst_err(txt))
    rc2, txt2 = gst(os.path.join(v.tmp, tag + '-dec.log'),
                    'filesrc location=%s ! qtdemux ! %s ! %s ! fakesink sync=false silent=false'
                    % (mp4, p, dec))
    ow, oh, of = out_caps(txt2, dec)
    res = {'frames': frames(txt2), 'bytes': size, 'width': ow, 'height': oh, 'format': of,
           'rc': rc2, 'encoder': enc, 'decoder': dec, 'mp4': mp4}
    if rc2:
        return res, 'decode %s rc %d: %s' % (dec, rc2, gst_err(txt2))
    return res, None


@test('video.roundtrip')
def t_roundtrip(env, codec='H264', width=1280, height=720, count=60, controls='', caps='',
                fmt='NV12', expect_width=None, expect_height=None):
    """Encode generated content with the V4L2 encoder and decode it back with the V4L2 decoder"""
    v = V(env)
    v.find('encoder', codec)
    v.find('decoder', codec)
    r, err = encode_decode(v, codec, width, height, count, controls, caps, fmt,
                           tag='rt-%s-%dx%d' % (codec, width, height))
    if r is None:
        return 'fail', err
    ew, eh = expect_width or width, expect_height or height
    meas = {'frames': r['frames'], 'width': r['width'], 'height': r['height'], 'bytes': r['bytes'],
            'pixels': (r['width'] or 0) * (r['height'] or 0)}
    desc = '%s %dx%d %s, %d frames%s -> %d bytes -> %s decoded %d frames %sx%s %s' % (
        r['encoder'], width, height, fmt, count, ' controls ' + controls if controls else '',
        r['bytes'], r['decoder'], r['frames'], r['width'], r['height'], r['format'])
    if err:
        return 'fail', desc + '; ' + err, meas
    ok = r['frames'] == count and (r['width'], r['height']) == (ew, eh)
    return ('pass' if ok else 'fail', desc + ('' if ok else '; expected %d frames %dx%d' % (
        count, ew, eh)), meas)


def vec_pipeline(path, codec, dec):
    ext = os.path.splitext(path)[1]
    p = PARSE[codec]
    if ext == '.ivf':
        return 'filesrc location=%s ! ivfparse ! %s ! %s' % (path, p, dec)
    if ext in ('.webm', '.mkv'):
        return 'filesrc location=%s ! matroskademux ! %s ! %s' % (path, p, dec)
    if ext in ('.mp4', '.mov'):
        return 'filesrc location=%s ! qtdemux ! %s ! %s' % (path, p, dec)
    return 'filesrc location=%s ! %s ! %s' % (path, p, dec)


@test('video.decode_vector')
def t_decode_vector(env, codec, vector, expect_format=None):
    """Conformance vector decoded by the V4L2 decoder: every frame, in the expected format"""
    v = V(env)
    v.find('decoder', codec)
    dec = v.element('decoder', codec)
    d = os.path.join(TOP, 'vectors')
    path = os.path.join(d, vector)
    if not os.path.exists(path):
        return 'fail', 'vector %s not in the overlay (%s): see tools/fetch-vectors.sh' % (vector, d)
    want = int(open(os.path.splitext(path)[0] + '.frames').read().split()[0])
    rc, txt = gst(os.path.join(v.tmp, vector + '.log'), '%s ! fakesink sync=false silent=false'
                  % vec_pipeline(path, codec, dec))
    n = frames(txt)
    w, h, f = out_caps(txt, dec)
    meas = {'frames': n, 'width': w, 'height': h, 'format': f}
    desc = '%s %s: %d of %d frames, %sx%s %s' % (dec, vector, n, want, w, h, f)
    if rc:
        return 'fail', desc + '; rc %d: %s' % (rc, gst_err(txt)), meas
    # GStreamer names (P010_10LE) and DRM fourccs (P010, P010:<modifier>)
    ok = n == want and (not expect_format or (f or '').startswith(expect_format))
    return ('pass' if ok else 'fail', desc + ('' if ok else '; expected %d frames%s' % (
        want, ' in ' + expect_format if expect_format else '')), meas)


@test('video.concurrent')
def t_concurrent(env, codec='H264', sessions=2, width=1920, height=1080, count=60, mix='decode'):
    """Concurrent codec sessions: n decodes (or decode plus encode) at the same time"""
    v = V(env)
    dec = v.element('decoder', codec)
    r, err = encode_decode(v, codec, width, height, count, tag='cc-src')
    if r is None:
        return 'fail', 'could not make the source stream: %s' % err
    p = PARSE[codec]
    procs = []
    for i in range(sessions):
        if mix == 'decode+encode' and i % 2:
            pl = '%s ! %s ! %s ! fakesink sync=false silent=false' % (
                SRC % (count, 'NV12', width, height), v.element('encoder', codec), p)
        else:
            pl = 'filesrc location=%s ! qtdemux ! %s ! %s ! fakesink sync=false silent=false' % (
                r['mp4'], p, dec)
        log = os.path.join(v.tmp, 'cc-%d.log' % i)
        procs.append((subprocess.Popen('timeout 180 gst-launch-1.0 -v %s > %s 2>&1' % (pl, log),
                                       shell=True), log, pl))
    ok, desc = 0, []
    for pr, log, pl in procs:
        rc = pr.wait()
        txt = open(log, errors='replace').read()
        n = frames(txt)
        good = rc == 0 and n == count
        ok += good
        desc.append('rc %d frames %d' % (rc, n))
    return ('pass' if ok == sessions else 'fail', '%d concurrent %s %s sessions at %dx%d: %s' % (
        sessions, codec, mix, width, height, '; '.join(desc)), {'sessions': ok})


@test('video.gst_elements')
def t_gst_elements(env, names):
    """GStreamer elements a use case needs are installed"""
    if not have('gst-inspect-1.0'):
        raise Missing('gst-inspect-1.0 not on the image', hw='gstreamer')
    miss = [n for n in names if sh('gst-inspect-1.0 --exists %s' % n)[0]]
    got = [n for n in names if n not in miss]
    return ('pass' if not miss else 'partial' if got else 'fail',
            'present: %s; missing: %s' % (' '.join(got) or 'none', ' '.join(miss) or 'none'),
            {'present': got})


@test('video.hls_local')
def t_hls_local(env, codec='H264', seconds=3):
    """Streaming: HLS segments encoded on the device, served over HTTP and decoded back"""
    v = V(env)
    enc, dec = v.element('encoder', codec), v.element('decoder', codec)
    d = os.path.join(v.tmp, 'hls')
    os.makedirs(d, exist_ok=True)
    n = 30 * seconds
    rc, txt = gst(os.path.join(v.tmp, 'hls-enc.log'),
                  '%s ! %s ! %s ! hlssink2 location=%s/seg%%05d.ts playlist-location=%s/p.m3u8 '
                  'target-duration=1 max-files=0 playlist-length=0' % (
                      SRC % (n, 'NV12', 1280, 720), enc, PARSE[codec], d, d))
    if rc or not os.path.exists(d + '/p.m3u8'):
        return 'fail', 'HLS encode rc %d: %s' % (rc, gst_err(txt))
    srv = subprocess.Popen(['python3', '-m', 'http.server', '18080', '--bind', '127.0.0.1',
                            '--directory', d], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        rc, txt = gst(os.path.join(v.tmp, 'hls-dec.log'),
                      'souphttpsrc location=http://127.0.0.1:18080/p.m3u8 ! hlsdemux ! tsdemux ! '
                      '%s ! %s ! fakesink sync=false silent=false' % (PARSE[codec], dec))
    finally:
        srv.kill()
    got = frames(txt)
    segs = len(glob.glob(d + '/*.ts'))
    return ('pass' if rc == 0 and got == n else 'fail', '%d HLS segments; %d of %d frames '
            'decoded over HTTP%s' % (segs, got, n, '' if rc == 0 else '; rc %d: %s' % (rc, gst_err(txt))),
            {'frames': got})


def yuv420_frames(w, h, n):
    """n frames of a moving gradient in planar YUV 4:2:0 (YU12 layout)."""
    out = []
    for k in range(n):
        y = bytes(((x + 4 * k) * 255 // w) & 0xff for x in range(w)) * h
        u = bytes([(64 + 2 * k) & 0xff]) * (w * h // 4)
        v = bytes([(192 - 2 * k) & 0xff]) * (w * h // 4)
        out.append(y + u + v)
    return out


def nv12_from_yu12(f, w, h):
    y, u, v = f[:w * h], f[w * h:w * h * 5 // 4], f[w * h * 5 // 4:]
    return y + bytes(b for pair in zip(u, v) for b in pair)


@test('video.v4l2ctl_roundtrip')
def t_v4l2ctl_roundtrip(env, codec=None, width=640, height=480, count=10, max_diff=12):
    """Encode and decode through the V4L2 codecs with v4l2-ctl, compared frame by frame"""
    v = V(env)
    if not have('v4l2-ctl'):
        raise Missing('v4l2-ctl not on the image', hw='v4l2-ctl')
    encs = [c for c in v.codecs if c['role'] == 'encoder']
    decs = [c for c in v.codecs if c['role'] == 'decoder']
    pairs = [(e, d, f) for e in encs for d in decs for f in e['coded'] if f in d['coded'] and
             (codec is None or f == codec)]
    if not pairs:
        raise Missing('no stateful V4L2 encoder and decoder share a coded format%s' % (
            ' %s' % codec if codec else ''), hw='video-encoder')
    e, d, f = pairs[0]
    raw = next((r for r in ('YU12', 'NV12') if r in e['raw'] and r in d['raw']), None)
    if raw is None:
        return 'fail', 'no YU12 or NV12 on both the encoder input %s and decoder output %s' % (
            e['raw'], d['raw'])
    src = yuv420_frames(width, height, count)
    if raw == 'NV12':
        src = [nv12_from_yu12(x, width, height) for x in src]
    fin, fenc, fout = (os.path.join(v.tmp, n) for n in ('in.yuv', 'enc.hdr', 'out.yuv'))
    with open(fin, 'wb') as fh:
        fh.write(b''.join(src))
    rc, log = sh('timeout 60 v4l2-ctl -d %s --set-fmt-video-out=width=%d,height=%d,pixelformat=%s '
                 '--set-fmt-video=pixelformat=%s --stream-out-mmap --stream-mmap --stream-from=%s '
                 '--stream-to-hdr=%s --stream-count=%d 2>&1' % (e['dev'].path, width, height, raw, f,
                                                                fin, fenc, count), timeout=70)
    size = os.path.getsize(fenc) if os.path.exists(fenc) else 0
    if rc or not size:
        return 'fail', 'encode %s on %s rc %d, %d bytes: %s' % (f, e['dev'].path, rc, size,
                                                                log.strip()[-200:])
    rc, log = sh('timeout 60 v4l2-ctl -d %s --set-fmt-video-out=pixelformat=%s --set-fmt-video='
                 'pixelformat=%s --stream-out-mmap --stream-mmap --stream-from-hdr=%s --stream-to=%s '
                 '2>&1' % (d['dev'].path, f, raw, fenc, fout), timeout=70)
    data = open(fout, 'rb').read() if os.path.exists(fout) else b''
    fs = width * height * 3 // 2
    n = len(data) // fs
    meas = {'frames': n, 'bytes': size}
    if rc or n < count:
        return ('fail', 'decode %s on %s rc %d: %d of %d frames: %s' % (
            f, d['dev'].path, rc, n, count, log.strip()[-200:]), meas)
    diffs = [sum(abs(a - b) for a, b in zip(src[i][:width * height:97], data[i * fs:i * fs + width * height:97]))
             / len(src[i][:width * height:97]) for i in range(count)]
    meas['luma_diff'] = round(max(diffs), 1)
    return ('pass' if max(diffs) <= max_diff else 'fail',
            '%s %dx%d %s via %s and %s: %d frames, %d bytes coded, worst mean luma difference %.1f '
            '(limit %d)' % (f, width, height, raw, e['dev'].card, d['dev'].card, count, size,
                            max(diffs), max_diff), meas)
