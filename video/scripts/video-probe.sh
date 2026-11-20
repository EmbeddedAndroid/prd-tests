#!/bin/sh
# video-probe.sh: Eliza video PRD inventory and short functional checks on
# the open stack (upstream qcom-iris, GStreamer v4l2 elements). Read-only:
# writes only under /tmp/vp. Every case reports through lava-test-case and
# nothing here may stop the LAVA shell (it runs under set -e).
D=$(dirname "$0"); V=$D/vectors; O=/tmp/vp
mkdir -p $O
T=60

r() { # name result [measurement units]
	if [ -n "$3" ]; then
		lava-test-case "$1" --result "$2" --measurement "$3" --units "${4:-n}" || true
	else
		lava-test-case "$1" --result "$2" || true
	fi
}
chk() { if "$@" >/dev/null 2>&1; then echo pass; else echo fail; fi; }
note() { echo "VP-NOTE $*"; }

# frames <log>: buffers that reached fakesink (silent=false, -v)
frames() { n=$(grep -c 'last-message = chain' "$1" 2>/dev/null); echo "${n:-0}"; }
# run <log> <pipeline...>: gst-launch with a timeout, rc kept in $RC
run() {
	l=$1; shift
	timeout $T gst-launch-1.0 -v "$@" > "$l" 2>&1
	RC=$?
	echo "VP-RUN rc=$RC $(basename "$l"): $*"
	grep -E 'ERROR|WARN|error' "$l" | head -5
	if [ $RC -ne 0 ]; then
		echo "VP-LOG $(basename "$l") begin"; grep -vE 'last-message = ' "$l" | tail -25; echo "VP-LOG end"
		dmesg | tail -n 8 | grep -i iris
	fi
}
# count frames of a decode into fakesink; $1 log, rest = source part
dec() {
	l=$1; shift
	run "$l" "$@" ! fakesink sync=false silent=false
	N=$(frames "$l")
}
# nal-types <file>: H.264 nal_unit_type of every start code
nals() {
	od -An -v -tx1 "$1" | tr -d ' \n' | grep -o '000001[0-9a-f][0-9a-f]' |
		while read -r h; do echo $(( 0x${h#000001} & 0x1f )); done
}

echo "== identity"
cat /etc/qcdfp 2>/dev/null; uname -a
modinfo qcom_iris 2>/dev/null | grep -E '^(filename|vermagic|firmware)'
dmesg | grep -iE 'qcom-iris|QC_IMAGE_VERSION' | head -5

DEC=; ENC=
for v in /dev/video*; do
	c=$(v4l2-ctl -d "$v" --info 2>/dev/null | sed -n 's/.*Card type *: //p')
	case "$c" in *Decoder*) DEC=$v ;; *Encoder*) ENC=$v ;; esac
done
r iris-decoder-node $([ -n "$DEC" ] && echo pass || echo fail)
r iris-encoder-node $([ -n "$ENC" ] && echo pass || echo fail)

echo "== decoder formats"
v4l2-ctl -d "$DEC" --list-formats-out-ext > $O/dec-out.txt 2>&1
v4l2-ctl -d "$DEC" --list-formats-ext > $O/dec-cap.txt 2>&1
v4l2-ctl -d "$DEC" -L > $O/dec-ctrl.txt 2>&1
cat $O/dec-out.txt $O/dec-cap.txt $O/dec-ctrl.txt
echo "== encoder formats"
v4l2-ctl -d "$ENC" --list-formats-out-ext > $O/enc-out.txt 2>&1
v4l2-ctl -d "$ENC" --list-formats-ext > $O/enc-cap.txt 2>&1
v4l2-ctl -d "$ENC" -L > $O/enc-ctrl.txt 2>&1
cat $O/enc-out.txt $O/enc-cap.txt $O/enc-ctrl.txt

for f in H264 HEVC VP90 AV01; do r dec-coded-$f $(chk grep -q "'$f'" $O/dec-out.txt); done
for f in NV12 Q08C P010 Q10C; do r dec-raw-$f $(chk grep -q "'$f'" $O/dec-cap.txt); done
for f in H264 HEVC; do r enc-coded-$f $(chk grep -q "'$f'" $O/enc-cap.txt); done
for f in NV12 Q08C P010 Q10C NV21 YU12 AR24 RGB3; do r enc-raw-$f $(chk grep -q "'$f'" $O/enc-out.txt); done

# encoder controls by V4L2 name (v4l2-ctl lower_case form)
for c in video_bitrate video_peak_bitrate video_bitrate_mode frame_level_rate_control_enable \
	video_gop_size force_key_frame frame_skip_mode number_of_b_frames \
	prepend_sps_and_pps_to_idr rotate horizontal_flip vertical_flip \
	intra_refresh_period intra_refresh_period_type ltr_count frame_ltr_index use_ltr_frames \
	h264_hierarchical_coding h264_hierarchical_coding_type h264_number_of_hc_layers \
	hevc_hierarchical_coding_type hevc_hierarchical_coding_layer \
	slice_partitioning_method maximum_number_of_mbs_per_slice maximum_bytes_in_a_slice; do
	r enc-ctrl-$c $(chk grep -qE "^ *$c " $O/enc-ctrl.txt)
done

echo "== userspace inventory"
for e in v4l2h264dec v4l2h265dec v4l2vp9dec v4l2av1dec v4l2h264enc v4l2h265enc \
	h264parse h265parse vp9parse av1parse ivfparse matroskademux qtdemux mp4mux \
	mpegtsmux tsdemux hlssink2 hlsdemux souphttpsrc rtspsrc rtspclientsink rtpbin \
	rtph264pay rtph265pay udpsink waylandsink kmssink capssetter; do
	r gst-$e $(chk gst-inspect-1.0 --exists $e)
done
for t in ffmpeg python3 heif-enc heif-dec heif-convert avahi-daemon wpa_supplicant wpa_cli iw \
	fluster gst-play-1.0; do
	printf 'VP-TOOL %s=%s\n' $t "$(command -v $t)"
done
{ iw list 2>/dev/null | sed -n '/Supported interface modes/,/Band\|software interface/p' | head -14; } || true
r wifi-p2p-go $(iw list 2>/dev/null | grep -q 'P2P-GO' && echo pass || echo fail)
r wifi-p2p-client $(iw list 2>/dev/null | grep -q 'P2P-client' && echo pass || echo fail)

SRC="videotestsrc pattern=smpte"
echo "== encode/decode round trips (720p, 60 frames)"
run $O/e264.log $SRC num-buffers=60 ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
	! v4l2h264enc ! tee name=t t. ! queue ! h264parse ! video/x-h264,stream-format=byte-stream ! filesink location=$O/e264.h264 \
	t. ! queue ! h264parse ! mp4mux ! filesink location=$O/e264.mp4
S=$(stat -c %s $O/e264.h264 2>/dev/null || echo 0)
r enc-h264-720p $([ $RC -eq 0 ] && [ $S -gt 0 ] && echo pass || echo fail) $S bytes
dec $O/d264.log filesrc location=$O/e264.mp4 ! qtdemux ! h264parse ! v4l2h264dec
r dec-h264-roundtrip $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames

run $O/e265.log $SRC num-buffers=60 ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
	! v4l2h265enc ! tee name=t t. ! queue ! h265parse ! video/x-h265,stream-format=byte-stream ! filesink location=$O/e265.h265 \
	t. ! queue ! h265parse ! mp4mux ! filesink location=$O/e265.mp4
S=$(stat -c %s $O/e265.h265 2>/dev/null || echo 0)
r enc-hevc-720p $([ $RC -eq 0 ] && [ $S -gt 0 ] && echo pass || echo fail) $S bytes
dec $O/d265.log filesrc location=$O/e265.mp4 ! qtdemux ! h265parse ! v4l2h265dec
r dec-hevc-roundtrip $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames


echo "== diag: raw byte-stream decode of the encoder output (590056 preroll failure)"
od -An -tx1 -N 64 $O/e264.h264
dec $O/diag-a.log filesrc location=$O/e264.h264 ! h264parse ! v4l2h264dec
r diag-raw-h264-filesrc $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
GST_DEBUG=v4l2*:4,h264parse:4 timeout $T gst-launch-1.0 filesrc location=$O/e264.h264 ! h264parse ! v4l2h264dec ! fakesink > $O/diag-dbg.log 2>&1
grep -iE 'error|fail|not-negotiated|reject|caps|framerate|profile|level' $O/diag-dbg.log | grep -v GST_TRACER | tail -40
dec $O/diag-f.log filesrc location=$O/e264.h264 ! h264parse ! capssetter caps="video/x-h264,framerate=(fraction)30/1" ! v4l2h264dec
r diag-raw-h264-framerate-forced $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
dec $O/diag-g.log filesrc location=$O/e265.h265 ! h265parse ! v4l2h265dec
r diag-raw-hevc-filesrc $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
dec $O/diag-h.log filesrc location=$O/e265.h265 ! h265parse ! capssetter caps="video/x-h265,framerate=(fraction)30/1" ! v4l2h265dec
r diag-raw-hevc-framerate-forced $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
grep -o 'h264parse0.GstPad:src: caps = [^;]*' $O/diag-a.log | grep -o 'framerate=(fraction)[0-9/]*' | head -1
dec $O/diag-b.log $SRC num-buffers=60 ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 ! v4l2h264enc ! h264parse ! v4l2h264dec
r diag-h264-enc-to-dec-inline $([ $RC -eq 0 ] && [ $N -eq 60 ] && echo pass || echo fail) $N frames
timeout $T ffmpeg -hide_banner -c:v h264_v4l2m2m -i $O/e264.h264 -f null - > $O/diag-c.log 2>&1; RC=$?
N=$(grep -o 'frame= *[0-9]*' $O/diag-c.log | tail -1 | grep -o '[0-9]*$'); N=${N:-0}
tail -15 $O/diag-c.log
r diag-raw-h264-ffmpeg-v4l2m2m $([ $RC -eq 0 ] && [ "$N" -eq 60 ] && echo pass || echo fail) $N frames
timeout $T ffmpeg -hide_banner -i $O/e264.h264 -f null - > $O/diag-d.log 2>&1; RC=$?
N=$(grep -o 'frame= *[0-9]*' $O/diag-d.log | tail -1 | grep -o '[0-9]*$'); N=${N:-0}
r diag-raw-h264-ffmpeg-software $([ $RC -eq 0 ] && [ "$N" -eq 60 ] && echo pass || echo fail) $N frames
grep -iE 'profile|level|Stream #' $O/diag-d.log | head -4
timeout $T ffmpeg -hide_banner -c:v hevc_v4l2m2m -i $O/e265.h265 -f null - > $O/diag-e.log 2>&1; RC=$?
N=$(grep -o 'frame= *[0-9]*' $O/diag-e.log | tail -1 | grep -o '[0-9]*$'); N=${N:-0}
r diag-raw-hevc-ffmpeg-v4l2m2m $([ $RC -eq 0 ] && [ "$N" -eq 60 ] && echo pass || echo fail) $N frames

echo "== conformance vectors (expected frame counts precomputed from the streams)"
vec() { # case file frames-file pipeline-middle...
	c=$1; f=$2; e=$3; shift 3
	if [ ! -s "$V/$f" ]; then r $c skip; return; fi
	x=$(cat "$V/$e" 2>/dev/null); x=${x:-0}
	dec $O/$c.log filesrc location=$V/$f "$@"
	grep -o 'video/x-raw[^"]*format=(string)[A-Z0-9_]*[^"]*width=(int)[0-9]*, height=(int)[0-9]*' $O/$c.log | sort -u | head -3
	echo "VP-VEC $c rc=$RC frames=$N expected=$x"
	r $c $([ $RC -eq 0 ] && [ $N -gt 0 ] && [ $N -eq $x ] && echo pass || echo fail) $N frames
}
vec dec-vp9-tile-4x4 vp90-2-08-tile-4x4.webm vp90-2-08-tile-4x4.frames ! matroskademux ! vp9parse ! v4l2vp9dec
vec dec-av1-8bit av1-1-b8-00-quantizer-00.ivf av1-1-b8-00-quantizer-00.frames ! ivfparse ! av1parse ! v4l2av1dec
vec dec-av1-10bit av1-1-b10-00-quantizer-00.ivf av1-1-b10-00-quantizer-00.frames ! ivfparse ! av1parse ! v4l2av1dec
vec dec-hevc-main10 WPP_A_ericsson_MAIN10_2.bit WPP_A_ericsson_MAIN10_2.frames ! h265parse ! v4l2h265dec
r dec-hevc-main10-p010 $(grep -q 'format=(string)P010' $O/dec-hevc-main10.log 2>/dev/null && echo pass || echo fail)

echo "VP-MAIN10-FRAMERATE $(grep -o 'h265parse0.GstPad:src: caps = [^;]*' $O/dec-hevc-main10.log | grep -o 'framerate=(fraction)[0-9/]*' | head -1)"
echo "== encoder pre-processing and coding tools (H.264 720p, 30 frames)"
# encx <case> <extra-controls> [caps after the encoder]
encx() {
	c=$1; x=$2; k=${3:-video/x-h264}
	run $O/$c.log $SRC num-buffers=30 ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
		! v4l2h264enc extra-controls="c,$x" ! "$k" ! tee name=t \
		t. ! queue ! h264parse ! video/x-h264,stream-format=byte-stream ! filesink location=$O/$c.h264 \
		t. ! queue ! h264parse ! mp4mux ! filesink location=$O/$c.mp4
	S=$(stat -c %s $O/$c.h264 2>/dev/null || echo 0)
	dec $O/$c-dec.log filesrc location=$O/$c.mp4 ! qtdemux ! h264parse ! v4l2h264dec
	W=$(grep -o 'v4l2h264dec0.GstPad:src: caps = video/x-raw[^"]*' $O/$c-dec.log | grep -o 'width=(int)[0-9]*, height=(int)[0-9]*' | tail -1)
	echo "VP-ENC $c rc=$RC bytes=$S frames=$N out=$W"
	grep -iE 'control|unknown|not supported|failed to set' $O/$c.log | head -4
}
encx enc-rotate-90 rotate=90
r enc-rotate-90 $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo "$W" | grep -q 'width=(int)720, height=(int)1280' && echo pass || echo fail) $N frames
encx enc-hflip horizontal_flip=1
r enc-hflip-accepted $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $N frames
encx enc-vflip vertical_flip=1
r enc-vflip-accepted $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $N frames
encx enc-downscale video_bitrate=2000000 'video/x-h264,width=640,height=360'
r enc-downscale-640x360 $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo "$W" | grep -q 'width=(int)640, height=(int)360' && echo pass || echo fail) $N frames
encx enc-intra-refresh "video_gop_size=1000,intra_refresh_period=10"
I=$(nals $O/enc-intra-refresh.h264 | grep -c '^5$'); I=${I:-0}
echo "VP-ENC idr-count=$I"
r enc-intra-refresh-accepted $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $I idr
encx enc-ltr ltr_count=2
r enc-ltr-accepted $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $N frames
encx enc-hier-p "enable_h264_hierarchical_coding=1,h264_hierarchical_coding_type=1,h264_number_of_hc_layers=3"
r enc-hier-p-accepted $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $N frames
encx enc-bframes video_b_frames=1
B=$(grep -c 'video_b_frames' $O/enc-bframes.log); echo "VP-ENC bframes-warn-lines=$B"
r enc-bframes-accepted $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $N frames
encx enc-gop-cbr "video_gop_size=10,video_bitrate_mode=1,video_bitrate=1000000"
I=$(nals $O/enc-gop-cbr.h264 | grep -c '^5$'); I=${I:-0}
r enc-gop-cbr-accepted $([ $RC -eq 0 ] && [ $N -eq 30 ] && echo pass || echo fail) $I idr

echo "== HEIF-style still: one HEVC intra frame"
run $O/still.log $SRC num-buffers=1 ! video/x-raw,format=NV12,width=1920,height=1080,framerate=1/1 \
	! v4l2h265enc ! h265parse ! mp4mux ! filesink location=$O/still.mp4
dec $O/still-dec.log filesrc location=$O/still.mp4 ! qtdemux ! h265parse ! v4l2h265dec
r hevc-still-roundtrip $([ $RC -eq 0 ] && [ $N -eq 1 ] && echo pass || echo fail) $N frames

echo "== streaming over localhost"
if command -v python3 >/dev/null; then
	run $O/mp4.log $SRC num-buffers=90 ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
		! v4l2h264enc ! h264parse ! mp4mux ! filesink location=$O/s.mp4
	mkdir -p $O/hls
	run $O/hlsenc.log $SRC num-buffers=90 ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
		! v4l2h264enc ! h264parse ! hlssink2 location=$O/hls/seg%05d.ts playlist-location=$O/hls/p.m3u8 \
		target-duration=1 max-files=0 playlist-length=0
	if [ ! -s $O/hls/p.m3u8 ]; then
		run $O/hlsenc2.log $SRC num-buffers=90 ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
			! v4l2h264enc extra-controls="c,video_gop_size=15" ! h264parse ! mpegtsmux ! hlssink location=$O/hls/seg%05d.ts \
			playlist-location=$O/hls/p.m3u8 target-duration=1 max-files=0 playlist-length=0
	fi
	cat $O/hls/p.m3u8 2>/dev/null | head -12
	(cd $O && exec python3 -m http.server 8765 --bind 127.0.0.1 > $O/http.log 2>&1) &
	H=$!; sleep 3
	dec $O/http.log2 souphttpsrc location=http://127.0.0.1:8765/s.mp4 ! qtdemux ! h264parse ! v4l2h264dec
	r stream-http-mp4 $([ $RC -eq 0 ] && [ $N -eq 90 ] && echo pass || echo fail) $N frames
	dec $O/hls.log2 souphttpsrc location=http://127.0.0.1:8765/hls/p.m3u8 ! hlsdemux ! tsdemux ! h264parse ! v4l2h264dec
	r stream-hls $([ $RC -eq 0 ] && [ $N -ge 85 ] && echo pass || echo fail) $N frames
	dec $O/hls.log3 souphttpsrc location=http://127.0.0.1:8765/hls/p.m3u8 ! hlsdemux ! tsdemux ! h264parse \
		! capssetter caps="video/x-h264,framerate=(fraction)30/1" ! v4l2h264dec
	r stream-hls-framerate-forced $([ $RC -eq 0 ] && [ $N -ge 85 ] && echo pass || echo fail) $N frames
	kill $H 2>/dev/null
else
	r stream-http-mp4 skip; r stream-hls skip
fi
# RTP/UDP over loopback (the transport WFD uses), H.264 payload
timeout 30 gst-launch-1.0 -v udpsrc port=5600 timeout=5000000000 \
	caps='application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96' \
	! rtph264depay ! h264parse ! capssetter caps="video/x-h264,framerate=(fraction)30/1" ! v4l2h264dec \
	! fakesink sync=false silent=false > $O/rtp-rx.log 2>&1 &
P=$!; sleep 2
run $O/rtp-tx.log $SRC num-buffers=90 is-live=true ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
	! v4l2h264enc extra-controls="c,video_gop_size=15" ! h264parse config-interval=-1 ! rtph264pay pt=96 \
	! udpsink host=127.0.0.1 port=5600
wait $P; N=$(frames $O/rtp-rx.log)
grep -vE 'last-message = ' $O/rtp-rx.log | tail -15
r stream-rtp-udp-loopback-framerate-forced $([ $N -ge 60 ] && echo pass || echo fail) $N frames

echo "== concurrency"
# one 1080p encode next to one 1080p decode (camcorder + playback)
run $O/e1080.log $SRC num-buffers=120 ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
	! v4l2h264enc ! h264parse ! mp4mux ! filesink location=$O/e1080.mp4
( timeout $T gst-launch-1.0 -v filesrc location=$O/e1080.mp4 ! qtdemux ! h264parse ! v4l2h264dec \
	! fakesink sync=true silent=false > $O/cc-dec.log 2>&1; echo $? > $O/cc-dec.rc ) &
( timeout $T gst-launch-1.0 -v $SRC num-buffers=120 ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
	! v4l2h265enc ! fakesink sync=true silent=false > $O/cc-enc.log 2>&1; echo $? > $O/cc-enc.rc ) &
wait
a=$(cat $O/cc-dec.rc); b=$(cat $O/cc-enc.rc); na=$(frames $O/cc-dec.log); nb=$(frames $O/cc-enc.log)
echo "VP-CC dec rc=$a frames=$na enc rc=$b frames=$nb"
r concurrent-1080p-dec-enc $([ "$a" = 0 ] && [ "$b" = 0 ] && [ $na -eq 120 ] && [ $nb -ge 120 ] && echo pass || echo fail)

# session count: N small decodes (256x144, 10 s each at real time) at once
run $O/esmall.log $SRC num-buffers=300 ! video/x-raw,format=NV12,width=256,height=144,framerate=30/1 \
	! v4l2h264enc ! h264parse ! mp4mux ! filesink location=$O/small.mp4
sessions() { # n -> $OK
	n=$1; i=0; rm -f $O/s-*.rc
	while [ $i -lt $n ]; do
		( timeout $T gst-launch-1.0 -v filesrc location=$O/small.mp4 ! qtdemux ! h264parse ! v4l2h264dec \
			! fakesink sync=true silent=false > $O/s-$i.log 2>&1; echo $? > $O/s-$i.rc ) &
		i=$((i + 1))
	done
	wait
	OK=0; i=0
	while [ $i -lt $n ]; do
		[ "$(cat $O/s-$i.rc)" = 0 ] && [ "$(frames $O/s-$i.log)" -eq 300 ] && OK=$((OK + 1))
		i=$((i + 1))
	done
	echo "VP-SESS n=$n ok=$OK"; grep -hiE 'error|fail' $O/s-*.log | sort | uniq -c | head -5
}
sessions 8; r sessions-8-decode $([ $OK -eq 8 ] && echo pass || echo fail) $OK sessions
sessions 16; r sessions-16-decode $([ $OK -eq 16 ] && echo pass || echo fail) $OK sessions
sessions 20; r sessions-20-decode-count pass $OK sessions
dmesg | grep -iE 'iris' | grep -iE 'session|max|limit|insufficient|overload|fail' | grep -v ParseC2SysPacket | tail -8

echo "== multi-slice (last: suspected NULL slice_caps on Eliza)"
encx enc-slices "video_bitrate_mode=1,video_bitrate=4000000,slice_partitioning_method=1,number_of_mbs_in_a_slice=900"
SL=$(nals $O/enc-slices.h264 | grep -cE '^(1|5)$'); SL=${SL:-0}
echo "VP-ENC slice-nals=$SL frames=$N"
r enc-multislice-mb $([ $RC -eq 0 ] && [ $N -eq 30 ] && [ "$SL" -ge 60 ] && echo pass || echo fail) $SL slices
encx enc-slices-bytes "video_bitrate_mode=1,video_bitrate=4000000,slice_partitioning_method=2,maximum_bytes_in_a_slice=4000"
SL=$(nals $O/enc-slices-bytes.h264 | grep -cE '^(1|5)$'); SL=${SL:-0}
echo "VP-ENC slice-bytes-nals=$SL frames=$N"
r enc-multislice-bytes $([ $RC -eq 0 ] && [ $N -eq 30 ] && [ "$SL" -ge 60 ] && echo pass || echo fail) $SL slices
encx enc-noslice-ref "video_bitrate_mode=1,video_bitrate=4000000"
SL=$(nals $O/enc-noslice-ref.h264 | grep -cE '^(1|5)$'); SL=${SL:-0}
echo "VP-ENC noslice-ref-nals=$SL frames=$N"

echo "== kernel"
dmesg | grep -iE 'context fault|Unhandled .*fault|iommu fault' | head -10
dmesg | grep -B3 -A30 'cut here' | head -80
dmesg | grep -iE 'qcom-iris' | grep -v ParseC2SysPacket | tail -20
r kernel-no-oops $(dmesg | grep -qE 'Unable to handle|Internal error|Oops|BUG:' && echo fail || echo pass)
r iris-no-smmu-fault $(dmesg | grep -qiE 'context fault|Unhandled .*fault|iommu fault' && echo fail || echo pass)
exit 0
