#!/bin/bash
# stage-overlay.sh <workdir> <probe.sh> [--no-vectors]: build the
# video-prd overlay tar (<workdir>/video-prd-overlay.tar): the probe script
# as video-prd/video-probe.sh plus the public conformance vectors with
# their expected frame counts. video-probe.sh needs the vectors; the
# follow-up probes (video-probe-v4.sh, video-probe-v5.sh) ran without them.
set -eu
W=$(realpath -m "$1"); P=$(realpath "$2"); NV=${3:-}
rm -rf "$W"; mkdir -p "$W/video-prd/vectors"
if [ "$NV" != --no-vectors ]; then
cd "$W/video-prd/vectors"
A=https://storage.googleapis.com/aom-test-data
curl -sfO $A/av1-1-b8-00-quantizer-00.ivf
curl -sfO $A/av1-1-b10-00-quantizer-00.ivf
curl -sfO https://storage.googleapis.com/downloads.webmproject.org/test_data/libvpx/vp90-2-08-tile-4x4.webm
curl -sfO https://storage.googleapis.com/downloads.webmproject.org/test_data/libvpx/vp90-2-08-tile-4x4.webm.md5
curl -sfO https://www.itu.int/wftp3/av-arch/jctvc-site/bitstream_exchange/draft_conformance/HEVC_v1/WPP_A_ericsson_MAIN10_2.zip
unzip -o -q WPP_A_ericsson_MAIN10_2.zip; find . -name '*.bit' -exec mv {} . \; ; rm -rf WPP_A_ericsson_MAIN10_2.zip
find . -mindepth 1 -type d -exec rm -rf {} + 2>/dev/null || true
python3 -I - <<'PY'
import struct, re
def ivf(f): return struct.unpack('<I', open(f,'rb').read()[24:28])[0]
for n in ('av1-1-b8-00-quantizer-00', 'av1-1-b10-00-quantizer-00'):
    open(n+'.frames','w').write('%d\n' % ivf(n+'.ivf'))
open('vp90-2-08-tile-4x4.frames','w').write('%d\n' % sum(1 for l in open('vp90-2-08-tile-4x4.webm.md5') if l.strip()))
b = open('WPP_A_ericsson_MAIN10_2.bit','rb').read()
pics = 0
for m in re.finditer(b'\x00\x00\x01', b):
    i = m.end()
    if i + 2 < len(b):
        t = (b[i] >> 1) & 0x3f
        if t < 32 and (b[i+2] & 0x80): pics += 1
open('WPP_A_ericsson_MAIN10_2.frames','w').write('%d\n' % pics)
PY
for f in *.frames; do echo "$f $(cat $f)"; done
fi
cp "$P" "$W/video-prd/video-probe.sh"; chmod +x "$W/video-prd/video-probe.sh"
cd "$W" && tar cf video-prd-overlay.tar video-prd && sha256sum video-prd-overlay.tar
