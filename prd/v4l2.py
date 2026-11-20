# SPDX-License-Identifier: GPL-2.0
"""v4l2.py: V4L2 device discovery over the raw ioctls (python3 standard
library only): capabilities, formats, frame sizes and controls."""
import fcntl
import glob
import os
import struct


def _ior(nr, size):
    return (2 << 30) | (size << 16) | (ord('V') << 8) | nr


def _iowr(nr, size):
    return (3 << 30) | (size << 16) | (ord('V') << 8) | nr


QUERYCAP = _ior(0, 104)
ENUM_FMT = _iowr(2, 64)
ENUM_FRAMESIZES = _iowr(74, 44)
QUERY_EXT_CTRL = _iowr(103, 232)

CAP_VIDEO_CAPTURE_MPLANE = 0x1000
CAP_M2M_MPLANE, CAP_M2M, CAP_DEVICE_CAPS = 0x4000, 0x8000, 0x80000000
BUF = {'capture': 1, 'output': 2, 'capture_mplane': 9, 'output_mplane': 10}
FMT_COMPRESSED = 0x1
NEXT_CTRL, NEXT_COMPOUND = 0x80000000, 0x40000000
# coded formats of stateless (request API) decoders
STATELESS = {'S264', 'S265', 'VP8F', 'VP9F', 'AV1F', 'MG2S', 'FWHT'}
CTRL_DISABLED = 0x1


def fourcc_str(v):
    return struct.pack('<I', v).decode('ascii', 'replace').rstrip()


class Dev:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        b = bytearray(104)
        fcntl.ioctl(self.fd, QUERYCAP, b, True)
        drv, card, bus, ver, caps, dcaps = struct.unpack_from('<16s32s32sIII', b)
        self.driver = drv.split(b'\0')[0].decode()
        self.card = card.split(b'\0')[0].decode()
        self.bus = bus.split(b'\0')[0].decode()
        self.caps = dcaps if caps & CAP_DEVICE_CAPS else caps
        self.mplane = bool(self.caps & (CAP_M2M_MPLANE | CAP_VIDEO_CAPTURE_MPLANE))
        self.m2m = bool(self.caps & (CAP_M2M_MPLANE | CAP_M2M))

    def close(self):
        os.close(self.fd)

    def formats(self, side):
        """side 'output' or 'capture': [(fourcc, flags, description)]."""
        t = BUF[side + ('_mplane' if self.mplane else '')]
        out = []
        for i in range(256):
            b = bytearray(struct.pack('<III32sII12x', i, t, 0, b'', 0, 0))
            try:
                fcntl.ioctl(self.fd, ENUM_FMT, b, True)
            except OSError:
                break
            _, _, flags, desc, pf, _ = struct.unpack('<III32sII12x', b)
            out.append((fourcc_str(pf), flags, desc.split(b'\0')[0].decode()))
        return out

    def framesizes(self, fourcc):
        """(min_w, min_h, max_w, max_h) over the enumerated sizes, or None."""
        pf = struct.unpack('<I', fourcc.ljust(4).encode())[0]
        ws, hs = [], []
        for i in range(64):
            b = bytearray(struct.pack('<III24s8x', i, pf, 0, b''))
            try:
                fcntl.ioctl(self.fd, ENUM_FRAMESIZES, b, True)
            except OSError:
                break
            _, _, typ = struct.unpack_from('<III', b)
            v = struct.unpack_from('<6I', b, 12)
            if typ == 1:
                ws.append(v[0])
                hs.append(v[1])
            else:
                ws += [v[0], v[1]]
                hs += [v[3], v[4]]
                break
        return (min(ws), min(hs), max(ws), max(hs)) if ws else None

    def controls(self):
        """{name: (min, max, default)} of every enabled control."""
        out = {}
        cid = NEXT_CTRL | NEXT_COMPOUND
        for _ in range(1024):
            b = bytearray(struct.pack('<II32sqqQqIIII4I128x', cid, 0, b'', 0, 0, 0, 0, 0, 0, 0, 0,
                                      0, 0, 0, 0))
            try:
                fcntl.ioctl(self.fd, QUERY_EXT_CTRL, b, True)
            except OSError:
                break
            f = struct.unpack_from('<II32sqqQqI', b)
            if not f[7] & CTRL_DISABLED and f[1] != 6:      # 6: control class
                out[norm(f[2].split(b'\0')[0].decode())] = (f[3], f[4], f[6])
            cid = f[0] | NEXT_CTRL | NEXT_COMPOUND
        return out


def norm(name):
    """V4L2 control name in the lower_case form v4l2-ctl and GStreamer use."""
    s = ''.join(c.lower() if c.isalnum() else '_' for c in name)
    while '__' in s:
        s = s.replace('__', '_')
    return s.strip('_')


def devices():
    out = []
    for p in sorted(glob.glob('/dev/video*'), key=lambda x: int(x[10:] or 0)):
        try:
            out.append(Dev(p))
        except OSError:
            pass
    return out


def codecs(devs=None):
    """Memory-to-memory codecs: [{'dev', 'role', 'coded', 'raw'}], role
    decoder, encoder or stateless-decoder."""
    out = []
    for d in devs if devs is not None else devices():
        if not d.m2m:
            continue
        o, c = d.formats('output'), d.formats('capture')
        oc = [f for f, fl, _ in o if fl & FMT_COMPRESSED]
        cc = [f for f, fl, _ in c if fl & FMT_COMPRESSED]
        # tiled or compressed raw layouts (UBWC and similar) carry the
        # compressed flag too: the raw side is the one with a plain format
        oplain = [f for f, fl, _ in o if not fl & FMT_COMPRESSED]
        cplain = [f for f, fl, _ in c if not fl & FMT_COMPRESSED]
        if oc and cplain and not oplain:
            role = 'stateless-decoder' if all(f in STATELESS for f in oc) else 'decoder'
            out.append({'dev': d, 'role': role, 'coded': oc, 'raw': [f for f, fl, _ in c]})
        elif cc and oplain and not cplain:
            out.append({'dev': d, 'role': 'encoder', 'coded': cc, 'raw': [f for f, fl, _ in o]})
    return out
