#!/bin/sh
# video-probe-v4.sh: follow-up checks after 590112 (read-only, /tmp/vp4):
# encoder stream labelling (profile/level) vs GStreamer decode of raw,
# RTP and HLS paths; B-frames encode vs decode; encoder downscale through
# V4L2 directly; FFmpeg behaviour. Never stops the LAVA shell.
O=/tmp/vp4; mkdir -p $O; T=60
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

echo "== encoder stream labelling"
run $O/def.log $SRC num-buffers=60 ! $RAW ! v4l2h264enc ! h264parse ! video/x-h264,stream-format=byte-stream ! filesink location=$O/def.h264
echo "VP-SPS default $(sps $O/def.h264) caps: $(pcaps $O/def.log h264parse0)"
run $O/hi.log $SRC num-buffers=60 ! $RAW ! v4l2h264enc ! 'video/x-h264,profile=high,level=(string)4' ! h264parse \
	! video/x-h264,stream-format=byte-stream ! filesink location=$O/hi.h264
echo "VP-SPS high-l4 $(sps $O/hi.h264) caps: $(pcaps $O/hi.log h264parse0)"
S=$(sps $O/def.h264); LL=$(echo "$S" | cut -c15-16); L=$(( 0x${LL:-0} ))
r enc-default-sps-level-idc $([ "$L" -ge 31 ] && echo pass || echo fail) $L level_idc
S=$(sps $O/hi.h264); LL=$(echo "$S" | cut -c15-16); L=$(( 0x${LL:-0} ))
r enc-high-l4-sps-level-idc $([ "$L" -eq 40 ] && echo pass || echo fail) $L level_idc

dec $O/def-dec.log filesrc location=$O/def.h264 ! h264parse ! v4l2h264dec
echo "VP-PARSE default: $(pcaps $O/def-dec.log h264parse0)"
r dec-raw-default-labelled $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
dec $O/hi-dec.log filesrc location=$O/hi.h264 ! h264parse ! v4l2h264dec
echo "VP-PARSE high-l4: $(pcaps $O/hi-dec.log h264parse0)"
r dec-raw-high-l4 $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
GST_DEBUG=v4l2*:4,h264parse:4,GST_CAPS:4 timeout $T gst-launch-1.0 filesrc location=$O/def.h264 ! h264parse \
	! v4l2h264dec ! fakesink > $O/def-dbg.log 2>&1 < /dev/null
grep -iE 'exceeds|not-negotiated|incompatible|accept|refus|colorimetry' $O/def-dbg.log | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-260 | head -15

echo "== RTP over loopback and HLS with the level set"
timeout 30 gst-launch-1.0 -v udpsrc port=5602 \
	caps='application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96' \
	! rtph264depay ! h264parse ! v4l2h264dec ! fakesink sync=false silent=false > $O/rtp-rx.log 2>&1 < /dev/null &
P=$!; sleep 2
run $O/rtp-tx.log $SRC num-buffers=90 is-live=true ! $RAW ! v4l2h264enc extra-controls="c,video_gop_size=15" \
	! 'video/x-h264,profile=high,level=(string)4' ! h264parse config-interval=-1 ! rtph264pay pt=96 ! udpsink host=127.0.0.1 port=5602
wait $P; N=$(frames $O/rtp-rx.log)
grep -vE 'last-message = ' $O/rtp-rx.log | grep -E 'ERROR|reason' | head -4
r stream-rtp-udp-loopback-high-l4 $([ $N -ge 60 ] && echo pass || echo fail) $N frames

mkdir -p $O/hls
run $O/hlsenc.log $SRC num-buffers=90 ! $RAW ! v4l2h264enc extra-controls="c,video_gop_size=15" \
	! 'video/x-h264,profile=high,level=(string)4' ! h264parse ! hlssink2 location=$O/hls/seg%05d.ts \
	playlist-location=$O/hls/p.m3u8 target-duration=1 max-files=0 playlist-length=0
(cd $O && exec python3 -m http.server 8766 --bind 127.0.0.1 > $O/http.log 2>&1 < /dev/null) &
H=$!; sleep 3
dec $O/hls-dec.log souphttpsrc location=http://127.0.0.1:8766/hls/p.m3u8 ! hlsdemux ! tsdemux ! h264parse ! v4l2h264dec
r stream-hls-high-l4 $([ $RC -eq 0 ] && [ $N -ge 85 ] && echo pass || echo fail) $N frames
kill $H 2>/dev/null

echo "== B-frames"
run $O/b.log $SRC num-buffers=30 ! $RAW ! v4l2h264enc extra-controls="c,video_b_frames=1" \
	! 'video/x-h264,profile=high,level=(string)4' ! h264parse ! video/x-h264,stream-format=byte-stream \
	! filesink location=$O/b.h264
NS=$(nals $O/b.h264 | grep -cE '^(1|5)$'); NS=${NS:-0}
echo "VP-B slice-nals=$NS"
r enc-bframes-slices $([ $RC -eq 0 ] && [ "$NS" -eq 30 ] && echo pass || echo fail) $NS slices
dec $O/b-dec.log filesrc location=$O/b.h264 ! h264parse ! v4l2h264dec
r dec-bframes-raw $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $N frames

echo "== encoder downscale through V4L2 (1280x720 NV12 in, 640x360 H.264 out)"
run $O/rawgen.log $SRC num-buffers=30 ! $RAW ! filesink location=$O/raw.nv12
ls -l $O/raw.nv12
ds() { # tag width height
	timeout $T v4l2-ctl -d $ENC --set-fmt-video-out=width=1280,height=720,pixelformat=NV12 \
		--set-fmt-video=width=$2,height=$3,pixelformat=H264 --stream-out-mmap --stream-mmap \
		--stream-from=$O/raw.nv12 --stream-to=$O/$1.h264 --stream-count=30 > $O/$1.v4l2.log 2>&1 < /dev/null
	echo "VP-V4L2 $1 rc=$? bytes=$(stat -c %s $O/$1.h264 2>/dev/null)"
	tail -5 $O/$1.v4l2.log
	dec $O/$1-dec.log filesrc location=$O/$1.h264 ! h264parse ! 'video/x-h264,framerate=30/1' ! v4l2h264dec
	W=$(grep -o 'v4l2h264dec0.GstPad:src: caps = video/x-raw[^"]*' $O/$1-dec.log | grep -o 'width=(int)[0-9]*, height=(int)[0-9]*' | tail -1)
	echo "VP-V4L2 $1 decoded=$N out=$W sps=$(sps $O/$1.h264)"
}
ds same 1280 720
r v4l2-enc-same-size $([ $N -gt 0 ] && echo "$W" | grep -q 'width=(int)1280, height=(int)720' && echo pass || echo fail) $N frames
ds down 640 360
r v4l2-enc-downscale-640x360 $([ $N -gt 0 ] && echo "$W" | grep -q 'width=(int)640, height=(int)360' && echo pass || echo fail) $N frames

echo "== FFmpeg"
ffmpeg -hide_banner -version < /dev/null 2>&1 | head -3
ffmpeg -hide_banner -decoders < /dev/null 2>&1 | grep -iE 'v4l2m2m' | head -6
timeout $T ffmpeg -hide_banner -nostdin -c:v h264_v4l2m2m -i $O/hi.h264 -f null - > $O/ff.log 2>&1
RC=$?; N=$(grep -o 'frame= *[0-9]*' $O/ff.log | tail -1 | grep -o '[0-9]*$'); N=${N:-0}
tail -6 $O/ff.log
r ffmpeg-h264-v4l2m2m-raw $([ $RC -eq 0 ] && [ "$N" -eq 60 ] && echo pass || echo fail) $N frames

dmesg | grep -iE 'context fault|Unhandled .*fault|Oops|Internal error' | head -5
dmesg | grep -i qcom-iris | grep -v ParseC2SysPacket | grep -v 'Buffer type 5' | tail -12
r kernel-no-oops-v4 $(dmesg | grep -qE 'Unable to handle|Internal error|Oops|BUG:' && echo fail || echo pass)
exit 0
