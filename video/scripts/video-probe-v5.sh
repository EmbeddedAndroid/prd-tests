#!/bin/sh
# video-probe-v5.sh: proves the 590161 not-negotiated cause (read-only, /tmp/vp4):
# encoder stream labelling (profile/level) vs GStreamer decode of raw,
# RTP and HLS paths; B-frames encode vs decode; encoder downscale through
# V4L2 directly; FFmpeg behaviour. Never stops the LAVA shell.
O=/tmp/vp5; mkdir -p $O; T=60
r() {
	if [ -n "$3" ]; then lava-test-case "$1" --result "$2" --measurement "$3" --units "${4:-n}" || true
	else lava-test-case "$1" --result "$2" || true; fi
}
frames() { n=$(grep -c 'last-message = chain' "$1" 2>/dev/null); echo "${n:-0}"; }
run() {
	l=$1; shift
	timeout $T gst-launch-1.0 -v "$@" > "$l" 2>&1 < /dev/null
	RC=$?
	echo "VP-RUN rc=$RC $(basename "$l"): $*"
	if [ $RC -ne 0 ]; then grep -vE 'last-message = ' "$l" | grep -E 'ERROR|reason|WARN' | head -6; fi
}
dec() { l=$1; shift; run "$l" "$@" ! fakesink sync=false silent=false; N=$(frames "$l"); }
nals() {
	od -An -v -tx1 "$1" | tr -d ' \n' | grep -o '000001[0-9a-f][0-9a-f]' |
		while read -r h; do echo $(( 0x${h#000001} & 0x1f )); done
}
sps() { od -An -v -tx1 "$1" | tr -d ' \n' | grep -o '0000000167[0-9a-f]\{6\}' | head -1; }
pcaps() { grep -o "$2.GstPad:src: caps = [^;]*" "$1" | head -1 | grep -oE '(profile|level|framerate|colorimetry)=\(?[a-z]*\)?[^,]*' | tr '\n' ' '; echo; }

DEC=; ENC=
for v in /dev/video*; do
	c=$(v4l2-ctl -d "$v" --info 2>/dev/null | sed -n 's/.*Card type *: //p')
	case "$c" in *Decoder*) DEC=$v ;; *Encoder*) ENC=$v ;; esac
done
echo "VP-DEV dec=$DEC enc=$ENC"
SRC="videotestsrc pattern=smpte"
RAW="video/x-raw,format=NV12,width=1280,height=720,framerate=30/1"


CS='capssetter caps="video/x-h264,colorimetry=(string)bt601"'
HL="video/x-h264,profile=high,level=(string)4"
echo "== colorimetry override on the raw path"
run $O/hi.log $SRC num-buffers=60 ! $RAW ! v4l2h264enc ! $HL ! h264parse ! video/x-h264,stream-format=byte-stream ! filesink location=$O/hi.h264
dec $O/a.log filesrc location=$O/hi.h264 ! h264parse ! v4l2h264dec
r dec-raw-control-2-4-5-4 $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
dec $O/b.log filesrc location=$O/hi.h264 ! h264parse ! capssetter caps="video/x-h264,colorimetry=(string)bt601" ! v4l2h264dec
r dec-raw-colorimetry-bt601 $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
dec $O/c.log filesrc location=$O/hi.h264 ! h264parse ! capssetter caps="video/x-h264,colorimetry=(string)bt709" ! v4l2h264dec
r dec-raw-colorimetry-bt709 $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
run $O/d.log $SRC num-buffers=60 ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1,colorimetry=bt709 \
	! v4l2h264enc ! $HL ! h264parse ! video/x-h264,stream-format=byte-stream ! filesink location=$O/709.h264
dec $O/e.log filesrc location=$O/709.h264 ! h264parse ! v4l2h264dec
echo "VP-PARSE bt709-source: $(pcaps $O/e.log h264parse0)"
r dec-raw-bt709-source $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames

echo "== HEVC"
run $O/h.log $SRC num-buffers=60 ! $RAW ! v4l2h265enc ! h265parse ! video/x-h265,stream-format=byte-stream ! filesink location=$O/h.h265
dec $O/h1.log filesrc location=$O/h.h265 ! h265parse ! v4l2h265dec
r dec-raw-hevc-control $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
dec $O/h2.log filesrc location=$O/h.h265 ! h265parse ! capssetter caps="video/x-h265,colorimetry=(string)bt601" ! v4l2h265dec
r dec-raw-hevc-colorimetry-bt601 $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames

echo "== RTP and HLS with the override"
timeout 30 gst-launch-1.0 -v udpsrc port=5604 \
	caps='application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96' \
	! rtph264depay ! h264parse ! capssetter caps="video/x-h264,colorimetry=(string)bt601" ! v4l2h264dec \
	! fakesink sync=false silent=false > $O/rtp-rx.log 2>&1 < /dev/null &
P=$!; sleep 2
run $O/rtp-tx.log $SRC num-buffers=90 is-live=true ! $RAW ! v4l2h264enc extra-controls="c,video_gop_size=15" \
	! $HL ! h264parse config-interval=-1 ! rtph264pay pt=96 ! udpsink host=127.0.0.1 port=5604
wait $P; N=$(frames $O/rtp-rx.log)
grep -vE 'last-message = ' $O/rtp-rx.log | grep -E 'ERROR|reason' | head -4
r stream-rtp-udp-loopback-colorimetry $([ $N -ge 60 ] && echo pass || echo fail) $N frames
mkdir -p $O/hls
run $O/hlsenc.log $SRC num-buffers=90 ! $RAW ! v4l2h264enc extra-controls="c,video_gop_size=15" \
	! $HL ! h264parse ! hlssink2 location=$O/hls/seg%05d.ts playlist-location=$O/hls/p.m3u8 target-duration=1 max-files=0 playlist-length=0
(cd $O && exec python3 -m http.server 8767 --bind 127.0.0.1 > $O/http.log 2>&1 < /dev/null) &
H=$!; sleep 3
dec $O/hls-dec.log souphttpsrc location=http://127.0.0.1:8767/hls/p.m3u8 ! hlsdemux ! tsdemux ! h264parse \
	! capssetter caps="video/x-h264,colorimetry=(string)bt601" ! v4l2h264dec
r stream-hls-colorimetry $([ $RC -eq 0 ] && [ $N -ge 85 ] && echo pass || echo fail) $N frames
kill $H 2>/dev/null

echo "== B-frames (raw path with the override, and MP4)"
run $O/bf.log $SRC num-buffers=30 ! $RAW ! v4l2h264enc extra-controls="c,video_b_frames=1" ! $HL ! tee name=t \
	t. ! queue ! h264parse ! video/x-h264,stream-format=byte-stream ! filesink location=$O/bf.h264 \
	t. ! queue ! h264parse ! mp4mux ! filesink location=$O/bf.mp4
NS=$(nals $O/bf.h264 | grep -cE '^(1|5)$'); NS=${NS:-0}; echo "VP-B slice-nals=$NS"
dec $O/bf1.log filesrc location=$O/bf.h264 ! h264parse ! capssetter caps="video/x-h264,colorimetry=(string)bt601" ! v4l2h264dec
r dec-bframes-raw-colorimetry $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $N frames
dec $O/bf2.log filesrc location=$O/bf.mp4 ! qtdemux ! h264parse ! v4l2h264dec
r dec-bframes-mp4 $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $N frames
timeout $T ffmpeg -hide_banner -nostdin -c:v h264_v4l2m2m -i $O/bf.h264 -f null - > $O/bf-ff.log 2>&1
RC=$?; N=$(grep -o 'frame= *[0-9]*' $O/bf-ff.log | tail -1 | grep -o '[0-9]*$'); N=${N:-0}
r dec-bframes-ffmpeg-v4l2m2m $([ $RC -eq 0 ] && [ "$N" -eq 30 ] && echo pass || echo fail) $N frames
timeout $T ffmpeg -hide_banner -nostdin -i $O/bf.h264 -f null - > $O/bf-sw.log 2>&1
RC=$?; N=$(grep -o 'frame= *[0-9]*' $O/bf-sw.log | tail -1 | grep -o '[0-9]*$'); N=${N:-0}
r dec-bframes-ffmpeg-software $([ $RC -eq 0 ] && [ "$N" -eq 30 ] && echo pass || echo fail) $N frames
dmesg | grep -iE 'context fault|Unhandled .*fault|Oops|Internal error' | head -5
r kernel-no-oops-v5 $(dmesg | grep -qE 'Unable to handle|Internal error|Oops|BUG:' && echo fail || echo pass)
exit 0
