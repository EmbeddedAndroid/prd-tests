# SPDX-License-Identifier: GPL-2.0
"""kms.py: minimal atomic KMS client over the raw DRM ioctls (python3
standard library only, no libdrm), for the display tests.

Covers what the checks need: resources, connectors, planes and their
properties, dumb buffers in packed RGB and NV12, ADDFB2 with modifiers,
property blobs, atomic commits (TEST_ONLY or real) and writeback
capture with its out fence."""
import ctypes
import errno
import fcntl
import glob
import mmap
import os
import select
import struct

# _IOWR('d', nr, size) on arm64/x86
def _iowr(nr, size):
    return (3 << 30) | (size << 16) | (0x64 << 8) | nr


def _iow(nr, size):
    return (1 << 30) | (size << 16) | (0x64 << 8) | nr


SET_CLIENT_CAP = _iow(0x0d, 16)
SET_MASTER = (0x64 << 8) | 0x1e
GETRESOURCES = _iowr(0xa0, 64)
GETENCODER = _iowr(0xa6, 20)
GETCONNECTOR = _iowr(0xa7, 80)
GETPROPERTY = _iowr(0xaa, 64)
GETPROPBLOB = _iowr(0xac, 16)
RMFB = _iowr(0xaf, 4)
CREATE_DUMB = _iowr(0xb2, 32)
MAP_DUMB = _iowr(0xb3, 16)
DESTROY_DUMB = _iowr(0xb4, 4)
GETPLANERESOURCES = _iowr(0xb5, 16)
GETPLANE = _iowr(0xb6, 32)
ADDFB2 = _iowr(0xb8, 104)
OBJ_GETPROPERTIES = _iowr(0xb9, 32)
ATOMIC = _iowr(0xbc, 56)
CREATEPROPBLOB = _iowr(0xbd, 16)
DESTROYPROPBLOB = _iowr(0xbe, 4)

CAP_UNIVERSAL_PLANES, CAP_ATOMIC, CAP_WRITEBACK = 2, 3, 5
TEST_ONLY, ALLOW_MODESET = 0x100, 0x400
OBJ_CRTC, OBJ_CONNECTOR, OBJ_PLANE = 0xcccccccc, 0xc0c0c0c0, 0xeeeeeeee
FB_MODIFIERS = 2
PROP_IMMUTABLE, PROP_ENUM, PROP_BITMASK = 1 << 2, 1 << 3, 1 << 5

CONN_TYPES = {0: 'Unknown', 1: 'VGA', 2: 'DVI-I', 3: 'DVI-D', 4: 'DVI-A', 5: 'Composite',
              6: 'SVIDEO', 7: 'LVDS', 8: 'Component', 9: 'DIN', 10: 'DP', 11: 'HDMI-A',
              12: 'HDMI-B', 13: 'TV', 14: 'eDP', 15: 'Virtual', 16: 'DSI', 17: 'DPI',
              18: 'Writeback', 19: 'SPI', 20: 'USB'}
CONN_IDS = {v: k for k, v in CONN_TYPES.items()}
MOD_LINEAR = 0
MOD_INVALID = (1 << 56) - 1
MOD_VENDORS = {1: 'INTEL', 2: 'AMD', 3: 'NVIDIA', 4: 'SAMSUNG', 5: 'QCOM', 6: 'VIVANTE',
               7: 'BROADCOM', 8: 'ARM', 9: 'ALLWINNER', 10: 'AMLOGIC', 11: 'MTK', 12: 'APPLE'}


def mod_str(m):
    if m == MOD_LINEAR:
        return 'LINEAR'
    return '%s:0x%x' % (MOD_VENDORS.get(m >> 56, 'vendor%d' % (m >> 56)), m & ((1 << 56) - 1))


def fourcc(s):
    return struct.unpack('<I', s.encode())[0]


def fourcc_str(v):
    return struct.pack('<I', v).decode('ascii', 'replace')


MODE_FMT = '<IHHHHHHHHHHIII32s'   # struct drm_mode_modeinfo, 68 bytes


class Mode:
    def __init__(self, raw):
        f = struct.unpack(MODE_FMT, raw)
        (self.clock, self.hdisplay, self.hsync_start, self.hsync_end, self.htotal, self.hskew,
         self.vdisplay, self.vsync_start, self.vsync_end, self.vtotal, self.vscan,
         self.vrefresh, self.flags, self.type, name) = f
        self.name = name.split(b'\0')[0].decode()
        self.raw = raw

    def __repr__(self):
        return '%dx%d@%d %d kHz' % (self.hdisplay, self.vdisplay, self.vrefresh, self.clock)

    @staticmethod
    def make(w, h, refresh=60):
        """CVT-like timing with small blanking (writeback has no sink)."""
        hb, vb = 160, 30
        ht, vt = w + hb, h + vb
        clock = ht * vt * refresh // 1000
        raw = struct.pack(MODE_FMT, clock, w, w + 48, w + 80, ht, 0, h, h + 3, h + 8, vt, 0,
                          refresh, 0, 0, ('%dx%d' % (w, h)).encode())
        return Mode(raw)


class DrmError(OSError):
    pass


class Card:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        self._props = {}

    def close(self):
        os.close(self.fd)

    def ioctl(self, req, buf):
        while True:
            try:
                fcntl.ioctl(self.fd, req, buf, True)
                return buf
            except OSError as e:
                if e.errno in (errno.EINTR, errno.EAGAIN):
                    continue
                raise DrmError(e.errno, '%s (ioctl 0x%x)' % (os.strerror(e.errno), req & 0xff))

    def set_master(self):
        try:
            fcntl.ioctl(self.fd, SET_MASTER, 0)
            return True
        except OSError:
            return False

    def set_cap(self, cap, val=1):
        try:
            self.ioctl(SET_CLIENT_CAP, bytearray(struct.pack('<QQ', cap, val)))
            return True
        except DrmError:
            return False

    # ---- resources
    def resources(self):
        b = self.ioctl(GETRESOURCES, bytearray(64))
        nf, nc, nk, ne = struct.unpack_from('<IIII', b, 32)
        arrs = [(ctypes.c_uint32 * max(n, 1))() for n in (nf, nc, nk, ne)]
        b = bytearray(struct.pack('<QQQQIIII', *[ctypes.addressof(a) for a in arrs], nf, nc, nk, ne)
                      + bytes(16))
        self.ioctl(GETRESOURCES, b)
        mw, xw, mh, xh = struct.unpack_from('<IIII', b, 48)
        self.min_w, self.max_w, self.min_h, self.max_h = mw, xw, mh, xh
        self.crtcs = list(arrs[1])[:nc]
        self.connector_ids = list(arrs[2])[:nk]
        self.encoder_ids = list(arrs[3])[:ne]
        return self

    def connector(self, cid):
        b = bytearray(80)
        struct.pack_into('<I', b, 32 + 16, cid)
        self.ioctl(GETCONNECTOR, b)
        nm, np_, ne = struct.unpack_from('<III', b, 32)
        modes = ctypes.create_string_buffer(68 * max(nm, 1))
        props = (ctypes.c_uint32 * max(np_, 1))()
        vals = (ctypes.c_uint64 * max(np_, 1))()
        encs = (ctypes.c_uint32 * max(ne, 1))()
        b = bytearray(struct.pack('<QQQQIIII', ctypes.addressof(encs), ctypes.addressof(modes),
                                  ctypes.addressof(props), ctypes.addressof(vals), nm, np_, ne, 0)
                      + struct.pack('<IIIIIIII', cid, 0, 0, 0, 0, 0, 0, 0))
        self.ioctl(GETCONNECTOR, b)
        f = struct.unpack_from('<IIIIIIIIIIII', b, 32)
        c = {'id': cid, 'type': f[5], 'type_id': f[6], 'connection': f[7], 'mm': (f[8], f[9]),
             'encoders': list(encs)[:ne], 'encoder_id': f[3],
             'modes': [Mode(modes.raw[i * 68:(i + 1) * 68]) for i in range(nm)]}
        c['name'] = '%s-%d' % (CONN_TYPES.get(c['type'], str(c['type'])), c['type_id'])
        c['connected'] = c['connection'] == 1
        return c

    def connectors(self):
        return [self.connector(i) for i in self.connector_ids]

    def encoder(self, eid):
        b = self.ioctl(GETENCODER, bytearray(struct.pack('<IIIII', eid, 0, 0, 0, 0)))
        _, t, crtc, possible, clones = struct.unpack('<IIIII', b)
        return {'id': eid, 'type': t, 'crtc': crtc, 'possible_crtcs': possible}

    def possible_crtcs(self, conn):
        m = 0
        for e in conn['encoders']:
            m |= self.encoder(e)['possible_crtcs']
        return [c for i, c in enumerate(self.crtcs) if m & (1 << i)]

    def planes(self):
        b = self.ioctl(GETPLANERESOURCES, bytearray(16))
        n = struct.unpack_from('<I', b, 8)[0]
        ids = (ctypes.c_uint32 * max(n, 1))()
        self.ioctl(GETPLANERESOURCES, bytearray(struct.pack('<QII', ctypes.addressof(ids), n, 0)))
        out = []
        for pid in list(ids)[:n]:
            b = self.ioctl(GETPLANE, bytearray(struct.pack('<IIIIIIQ', pid, 0, 0, 0, 0, 0, 0)))
            nf = struct.unpack_from('<I', b, 20)[0]
            fmts = (ctypes.c_uint32 * max(nf, 1))()
            b = self.ioctl(GETPLANE, bytearray(struct.pack('<IIIIIIQ', pid, 0, 0, 0, 0, nf,
                                                           ctypes.addressof(fmts))))
            _, crtc, fb, possible, gamma, _, _ = struct.unpack('<IIIIIIQ', b)
            p = {'id': pid, 'crtc': crtc, 'fb': fb, 'possible_crtcs': possible,
                 'formats': list(fmts)[:nf]}
            p['props'] = self.props(pid, OBJ_PLANE)
            p['type'] = self.enum_name(p['props'].get('type'))
            out.append(p)
        return out

    # ---- properties
    def prop_info(self, prop_id):
        if prop_id in self._props:
            return self._props[prop_id]
        b = self.ioctl(GETPROPERTY, bytearray(struct.pack('<QQII32sII', 0, 0, prop_id, 0, b'', 0, 0)))
        _, _, _, flags, name, nv, ne = struct.unpack('<QQII32sII', b)
        vals = (ctypes.c_uint64 * max(nv, 1))()
        enums = ctypes.create_string_buffer(40 * max(ne, 1))
        b = self.ioctl(GETPROPERTY, bytearray(struct.pack('<QQII32sII', ctypes.addressof(vals),
                                                          ctypes.addressof(enums), prop_id, 0,
                                                          b'', nv, ne)))
        info = {'id': prop_id, 'name': name.split(b'\0')[0].decode(), 'flags': flags,
                'values': list(vals)[:nv], 'enums': {}}
        if flags & (PROP_ENUM | PROP_BITMASK):
            for i in range(ne):
                v, n = struct.unpack_from('<Q32s', enums.raw, i * 40)
                info['enums'][n.split(b'\0')[0].decode()] = v
        self._props[prop_id] = info
        return info

    def props(self, obj, otype):
        """{name: (prop_id, value)} for one object."""
        b = self.ioctl(OBJ_GETPROPERTIES, bytearray(struct.pack('<QQIII4x', 0, 0, 0, obj, otype)))
        n = struct.unpack_from('<I', b, 16)[0]
        ids = (ctypes.c_uint32 * max(n, 1))()
        vals = (ctypes.c_uint64 * max(n, 1))()
        self.ioctl(OBJ_GETPROPERTIES, bytearray(struct.pack('<QQIII4x', ctypes.addressof(ids),
                                                            ctypes.addressof(vals), n, obj, otype)))
        out = {}
        for i in range(n):
            out[self.prop_info(ids[i])['name']] = (ids[i], vals[i])
        return out

    def enum_name(self, pv):
        if not pv:
            return None
        info = self.prop_info(pv[0])
        for k, v in info['enums'].items():
            if v == pv[1]:
                return k
        return None

    def blob(self, blob_id):
        b = self.ioctl(GETPROPBLOB, bytearray(struct.pack('<IIQ', blob_id, 0, 0)))
        n = struct.unpack_from('<I', b, 4)[0]
        data = ctypes.create_string_buffer(max(n, 1))
        self.ioctl(GETPROPBLOB, bytearray(struct.pack('<IIQ', blob_id, n, ctypes.addressof(data))))
        return data.raw[:n]

    def create_blob(self, data):
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        b = self.ioctl(CREATEPROPBLOB, bytearray(struct.pack('<QII', ctypes.addressof(buf), len(data), 0)))
        return struct.unpack('<QII', b)[2]

    def destroy_blob(self, bid):
        try:
            self.ioctl(DESTROYPROPBLOB, bytearray(struct.pack('<I', bid)))
        except DrmError:
            pass

    def in_formats(self, plane):
        """IN_FORMATS blob -> {fourcc: set(modifiers)}."""
        pv = plane['props'].get('IN_FORMATS')
        if not pv or not pv[1]:
            return {}
        d = self.blob(pv[1])
        ver, _, nfmt, foff, nmod, moff = struct.unpack_from('<IIIIII', d, 0)
        fmts = struct.unpack_from('<%dI' % nfmt, d, foff)
        out = {f: set() for f in fmts}
        for i in range(nmod):
            mask, off, _, mod = struct.unpack_from('<QIIQ', d, moff + i * 24)
            for j in range(64):
                if mask & (1 << j) and off + j < nfmt:
                    out[fmts[off + j]].add(mod)
        return out

    # ---- buffers
    def dumb(self, w, h, bpp):
        b = self.ioctl(CREATE_DUMB, bytearray(struct.pack('<IIIIIIQ', h, w, bpp, 0, 0, 0, 0)))
        _, _, _, _, handle, pitch, size = struct.unpack('<IIIIIIQ', b)
        b = self.ioctl(MAP_DUMB, bytearray(struct.pack('<IIQ', handle, 0, 0)))
        off = struct.unpack('<IIQ', b)[2]
        m = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=off)
        return {'handle': handle, 'pitch': pitch, 'size': size, 'map': m}

    def free_dumb(self, d):
        d['map'].close()
        self.ioctl(DESTROY_DUMB, bytearray(struct.pack('<I', d['handle'])))

    def addfb2(self, w, h, fmt, handles, pitches, offsets, modifier=None):
        hs = (list(handles) + [0] * 4)[:4]
        ps = (list(pitches) + [0] * 4)[:4]
        os_ = (list(offsets) + [0] * 4)[:4]
        flags = 0
        mods = [0] * 4
        if modifier is not None:
            flags = FB_MODIFIERS
            mods = [modifier if hs[i] else 0 for i in range(4)]
        b = bytearray(struct.pack('<IIIII4I4I4I4x4Q', 0, w, h, fmt, flags, *hs, *ps, *os_, *mods))
        self.ioctl(ADDFB2, b)
        return struct.unpack_from('<I', b, 0)[0]

    def rmfb(self, fb):
        try:
            self.ioctl(RMFB, bytearray(struct.pack('<I', fb)))
        except DrmError:
            pass

    # ---- atomic
    def atomic(self, req, flags=0):
        """req: {obj_id: {prop_id: value}}. Raises DrmError on refusal."""
        objs = list(req)
        counts = [len(req[o]) for o in objs]
        pids, pvals = [], []
        for o in objs:
            for k, v in req[o].items():
                pids.append(k)
                pvals.append(v & 0xffffffffffffffff)
        a_objs = (ctypes.c_uint32 * max(len(objs), 1))(*objs)
        a_cnt = (ctypes.c_uint32 * max(len(objs), 1))(*counts)
        a_pid = (ctypes.c_uint32 * max(len(pids), 1))(*pids)
        a_val = (ctypes.c_uint64 * max(len(pvals), 1))(*pvals)
        b = bytearray(struct.pack('<IIQQQQQQ', flags, len(objs), ctypes.addressof(a_objs),
                                  ctypes.addressof(a_cnt), ctypes.addressof(a_pid),
                                  ctypes.addressof(a_val), 0, 0))
        self.ioctl(ATOMIC, b)


def open_kms():
    """First /dev/dri/card* with connectors and atomic support."""
    for p in sorted(glob.glob('/dev/dri/card*')):
        c = Card(p)
        if c.set_cap(CAP_UNIVERSAL_PLANES) and c.set_cap(CAP_ATOMIC):
            c.set_cap(CAP_WRITEBACK)
            c.resources()
            if c.connector_ids:
                return c
        c.close()
    return None


SYNC_FILE_INFO = (3 << 30) | (56 << 16) | (ord('>') << 8) | 4


def wait_fence(fd, timeout_s=2.0):
    """Wait for a sync_file; returns its status: 1 signalled, 0 still
    pending at the timeout, negative errno when signalled with an error,
    None for no fence."""
    if fd < 0:
        return None
    try:
        r, _, _ = select.select([fd], [], [], timeout_s)
        b = bytearray(56)
        try:
            fcntl.ioctl(fd, SYNC_FILE_INFO, b, True)
            return struct.unpack_from('<i', b, 32)[0]
        except OSError:
            return 1 if r else 0
    finally:
        os.close(fd)
