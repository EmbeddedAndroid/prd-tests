#!/bin/sh
# fetch-vectors.sh <dir>: download the public conformance vectors the
# video tests decode (H.264, HEVC Main and Main10, VP9, AV1 8 and 10 bit) into
# <dir> and write <name>.frames next to each: the frame count the
# decoder must produce, taken from the stream itself (IVF header, the
# VP9 per-frame md5 list, H.264 and HEVC first-slice flags). Pass the directory to
# tools/mk-overlay.sh. Needs curl, unzip and python3.
set -eu
d=$(realpath -m "${1:?dir}")
mkdir -p "$d"
cd "$d"
A=https://storage.googleapis.com/aom-test-data
V=https://storage.googleapis.com/downloads.webmproject.org/test_data/libvpx
curl -sfO $A/av1-1-b8-00-quantizer-00.ivf
curl -sfO $A/av1-1-b10-00-quantizer-00.ivf
curl -sfO $V/vp90-2-08-tile-4x4.webm
curl -sfO $V/vp90-2-08-tile-4x4.webm.md5
curl -sfO https://www.itu.int/wftp3/av-arch/jctvc-site/bitstream_exchange/draft_conformance/HEVC_v1/WPP_A_ericsson_MAIN10_2.zip
curl -sfO https://www.itu.int/wftp3/av-arch/jctvc-site/bitstream_exchange/draft_conformance/HEVC_v1/AMP_A_Samsung_7.zip
curl -sfO https://www.itu.int/wftp3/av-arch/jvt-site/draft_conformance/AVCv1/BA1_Sony_D.zip
unzip -o -q -j WPP_A_ericsson_MAIN10_2.zip '*.bit'
unzip -o -q -j AMP_A_Samsung_7.zip '*.bin'
unzip -o -q -j BA1_Sony_D.zip '*.jsv'
rm -f ./*.zip
python3 -I - <<'PY'
import re, struct
def ivf(f):
    return struct.unpack('<I', open(f, 'rb').read()[24:28])[0]
for n in ('av1-1-b8-00-quantizer-00', 'av1-1-b10-00-quantizer-00'):
    open(n + '.frames', 'w').write('%d\n' % ivf(n + '.ivf'))
open('vp90-2-08-tile-4x4.frames', 'w').write(
    '%d\n' % sum(1 for l in open('vp90-2-08-tile-4x4.webm.md5') if l.strip()))
def hevc(f):
    b = open(f, 'rb').read()
    n = 0
    for m in re.finditer(b'\x00\x00\x01', b):
        i = m.end()
        # VCL NAL (type < 32) with first_slice_segment_in_pic_flag set
        if i + 2 < len(b) and (b[i] >> 1) & 0x3f < 32 and b[i + 2] & 0x80:
            n += 1
    return n
def avc(f):
    b = open(f, 'rb').read()
    n = 0
    for m in re.finditer(b'\x00\x00\x01', b):
        i = m.end()
        # slice NAL (type 1 or 5) with first_mb_in_slice 0 (ue(v) bit 1)
        if i + 1 < len(b) and b[i] & 0x1f in (1, 5) and b[i + 1] & 0x80:
            n += 1
    return n
for f in ('WPP_A_ericsson_MAIN10_2.bit', 'AMP_A_Samsung_7.bin'):
    open(f[:-4] + '.frames', 'w').write('%d\n' % hevc(f))
open('BA1_Sony_D.frames', 'w').write('%d\n' % avc('BA1_Sony_D.jsv'))
PY
rm -f vp90-2-08-tile-4x4.webm.md5
for f in *.frames; do echo "$f $(cat "$f")"; done
sha256sum ./*.ivf ./*.webm ./*.bit ./*.bin ./*.jsv
