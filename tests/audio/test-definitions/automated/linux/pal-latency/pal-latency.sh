#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Low latency streams on the AudioReach PAL path, for images where PAL owns
# the card and applications reach it through the PipeWire PAL nodes (no
# ALSA front end to open): the alsa-latency checks on PLAY_NODE and
# CAPTURE_NODE. Playback and capture run together with a node latency of
# PERIOD_FRAMES for DURATION. In each direction the period is the larger
# of the graph quantum the run got and the PAL buffer of that node, the
# size pal_stream_set_buffer_size took, which the PAL plugin logs at info
# level when the stream opens (the case raises the PipeWire log level
# while it runs); it must be at most MAX_PERIOD_MS. PipeWire rounds a node
# latency down to a power of two, so PERIOD_FRAMES should be one. No node
# or client of the run may report an xrun (pw-top ERR), the plugin may log
# no PAL read, write or start failure, and the played tone must be
# detected in the capture. A reference run at the default latency
# isolates a failure to the small period; a control with the stream
# volume at 0 must not be detected. No round-trip latency figure (alsabat
# needs ALSA devices).

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
AUDIO_BACKEND=pipewire
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

PLAY_NODE=""
CAPTURE_NODE=""
PERIOD_FRAMES="256"
DURATION="10"
PLAY_CHANNEL="2"
RATE="48000"
FREQ="1250"
TONE_DBFS="-6"
MIN_DB="20"
MAX_PERIOD_MS="10"

usage() {
    echo "Usage: $0 [-p play node] [-q capture node] [-F period frames] [-S s] [-k ch] [-R rate]" 1>&2
    echo "          [-f Hz] [-l dBFS] [-d dB] [-P max period ms]" 1>&2
    exit 1
}

while getopts "p:q:F:S:k:R:f:l:d:P:h" o; do
    case "$o" in
        p) PLAY_NODE="${OPTARG}" ;;
        q) CAPTURE_NODE="${OPTARG}" ;;
        F) PERIOD_FRAMES="${OPTARG}" ;;
        S) DURATION="${OPTARG}" ;;
        k) PLAY_CHANNEL="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        P) MAX_PERIOD_MS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

for v in DURATION PERIOD_FRAMES RATE; do
    case "$(eval echo "\${${v}}")" in
        ''|*[!0-9]*) echo "${v} must be a whole number" 1>&2; usage ;;
    esac
done
CASES="lowlatency-setup lowlatency-reference-detected lowlatency-playback-period lowlatency-capture-period lowlatency-playback-no-xrun lowlatency-capture-no-xrun lowlatency-tone-detected lowlatency-muted-not-detected roundtrip-latency"
skip_all() {
    local c
    info_msg "$1"
    for c in ${CASES}; do report_skip "${c}"; done
    exit 0
}

# top_snapshot <file>: one pw-top batch sample (after its first, empty one)
top_snapshot() {
    pw-top -b -n 2 > "$1" 2>&1
}

# top_field <file> <node> <column>: a column of the node's last pw-top line
# (3 QUANT, 9 ERR); the name is the last field
top_field() {
    awk -v n="$2" -v c="$3" '$NF == n { v = $c } END { print v }' "$1"
}

# driver_quantum <file> <node>: QUANT of the driver the node follows (the
# last line up to it that is not a follower), in the last sample
driver_quantum() {
    awk -v n="$2" '$1 != "S" && $(NF - 1) != "+" && $(NF - 1) != "=" { q = $3 } $NF == n { r = q } END { print r }' "$1"
}

# node_channels <pw-top file> <node>: channels of the node's format
node_channels() {
    awk -v n="$2" '$NF == n && $(NF - 4) ~ /^[SFU][0-9]/ { c = $(NF - 3) } END { print c }' "$1"
}

# pal_buffer_frames <node> <channels> <direction>: frames per PAL buffer
# the plugin last logged for the node this boot (S16 samples). Plugins
# without the per-node line log only the capture reader's buffer size, not
# which node it reads for: that one is taken for a capture node.
pal_buffer_frames() {
    journalctl --no-pager -b -u pipewire 2>/dev/null > "${OUTPUT}/pal.log"
    { sed -n "s/.*$1: PAL stream type [0-9]*, buffers [0-9]* x \([0-9]*\) bytes.*/\1/p" "${OUTPUT}/pal.log"
      [ "$3" = capture ] && ! grep -q ": PAL stream type " "${OUTPUT}/pal.log" &&
          sed -n "s/.*reader thread started, buf_size=\([0-9]*\).*/\1/p" "${OUTPUT}/pal.log"
    } | tail -1 | awk -v c="$2" 'NF && c > 0 { print int($1 / (2 * c)) }'
}

# ll <tag> <latency or ""> <playback volume>: record <tag>.raw from
# CAPTURE_NODE while tone.raw plays to PLAY_NODE; samples pw-top mid run
# and after it
ll() {
    local t="$1" rpid ppid prc lat=""
    [ -n "$2" ] && lat="--latency=$2"
    # shellcheck disable=SC2086
    pw-record ${lat} --target "${CAPTURE_NODE}" --rate "${RATE}" --channels 2 --format s16 \
        "${REC}/${t}.wav" > "${OUTPUT}/${t}.record.log" 2>&1 &
    rpid=$!
    sleep 0.5
    # shellcheck disable=SC2086
    pw-play ${lat} --volume="$3" --target "${PLAY_NODE}" "${OUTPUT}/tone.wav" > "${OUTPUT}/${t}.play.log" 2>&1 &
    ppid=$!
    sleep 3
    top_snapshot "${OUTPUT}/${t}.top"
    wait "${ppid}"
    prc=$?
    sleep 1
    kill -INT "${rpid}" 2>/dev/null
    wait "${rpid}"
    top_snapshot "${OUTPUT}/${t}.end.top"
    echo "${t}: pw-play rc ${prc}"
    audio_wav_raw "${REC}/${t}.wav" "${OUTPUT}/${t}.raw" && [ -s "${OUTPUT}/${t}.raw" ] && [ "${prc}" -eq 0 ]
}

# pal_errors <since>: PAL read/write failures the plugin logged since then
pal_errors() {
    journalctl --no-pager -b -u pipewire --since "@$1" 2>/dev/null |
        grep -cE "pal_stream_(read|write)[^ ]* (error|failed)|pal_stream_start failed"
}

create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing) $(for t in pw-top pw-metadata journalctl; do command -v "${t}" > /dev/null 2>&1 || printf '%s ' "${t}"; done)"
[ -z "$(echo "${missing}" | tr -d ' ')" ] || skip_all "missing tools: ${missing}"
audio_pw_start "${PLAY_NODE}" "${CAPTURE_NODE}" || {
    pw-cli ls Node 2>&1 | grep node.name
    skip_all "PipeWire nodes ${PLAY_NODE} ${CAPTURE_NODE} not both present"
}
# the plugin logs a node's PAL buffers when it opens its PAL stream, at
# info level; the nodes do not suspend when idle, so their first open this
# boot is the one to catch
t0="$(date +%s)"
# pw-record writes the file from its process callback: on a slow disk a
# write that stalls is an xrun of the client, so record into the runtime
# directory (tmpfs)
REC="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/pal-latency.XXXXXX")"
pw-metadata -n settings 0 log.level 3 > /dev/null
trap 'pw-metadata -n settings -d 0 log.level > /dev/null; rm -rf "${REC}"' EXIT
trap 'exit 1' INT TERM
info_msg "period ${PERIOD_FRAMES} frames ($(awk -v p="${PERIOD_FRAMES}" -v r="${RATE}" 'BEGIN { printf "%.2f", p * 1000 / r }') ms)"
audio_tone "${OUTPUT}/tone.raw" S16_LE "${RATE}" 2 "${PLAY_CHANNEL}" "${FREQ}:${DURATION}" "${TONE_DBFS}"
audio_wav "${OUTPUT}/tone.raw" "${OUTPUT}/tone.wav" "${RATE}" 2
if audio_record "${CAPTURE_NODE}" "${OUTPUT}/silence.raw" S16_LE "${RATE}" 2 3 > "${OUTPUT}/silence.log" 2>&1; then
    report_pass "lowlatency-setup"
else
    cat "${OUTPUT}/silence.log"
    report_fail "lowlatency-setup"
fi
audio_settle

if ll ref "" 1; then
    # shellcheck disable=SC2046
    set -- $(audio_tone_check "${OUTPUT}/ref.raw" "${FREQ}" 1.5 "$((DURATION - 2))" "${OUTPUT}/silence.raw" "${MIN_DB}")
    r="$1"; [ "$1" = none ] && r=fail
    add_metric "lowlatency-reference-detected" "${r}" "$2" dB
else
    report_fail "lowlatency-reference-detected"
fi
audio_settle

ok=0
ll run "${PERIOD_FRAMES}" 1 || ok=1
for d in "${PLAY_NODE}:playback" "${CAPTURE_NODE}:capture"; do
    n="${d%:*}"
    q="$(driver_quantum "${OUTPUT}/run.top" "${n}")"
    b="$(pal_buffer_frames "${n}" "$(node_channels "${OUTPUT}/run.top" "${n}")" "${d##*:}")"
    f="$(awk -v q="${q:-0}" -v b="${b:-0}" 'BEGIN { print (q > b ? q : b) }')"
    ms="$(awk -v f="${f}" -v r="${RATE}" 'BEGIN { printf "%.2f", f * 1000 / r }')"
    info_msg "${d##*:}: graph quantum ${q:-none} frames, PAL buffer ${b:-none} frames: period ${ms} ms"
    [ -n "${b}" ] || info_msg "${n}: no PAL buffer size logged this boot (stream opened before the log level was raised?)"
    r=fail
    [ -n "${q}" ] && [ -n "${b}" ] && awk -v m="${ms}" -v x="${MAX_PERIOD_MS}" 'BEGIN { exit !(m <= x) }' && r=pass
    add_metric "lowlatency-${d##*:}-period" "${r}" "${ms}" ms
done
pe="$(pal_errors "${t0}")"
info_msg "PAL read, write or start failures logged during the case: ${pe}"
for d in "${PLAY_NODE}:playback:pw-play" "${CAPTURE_NODE}:capture:pw-record"; do
    n="${d%%:*}"
    c="${d##*:}"
    x="${d#*:}"; x="${x%:*}"
    # mid run: the node and its client must both be there; after the run
    # the client is gone
    e="$(top_field "${OUTPUT}/run.top" "${n}" 9) $(top_field "${OUTPUT}/run.top" "${c}" 9)"
    e1="$(top_field "${OUTPUT}/run.end.top" "${n}" 9)"
    info_msg "${x}: ERR of ${n} and ${c} mid run: ${e}; of ${n} after: ${e1:-none}"
    if [ "${ok}" -eq 0 ] && [ "${pe}" -eq 0 ] &&
       echo "${e} ${e1}" | awk '{ for (i = 1; i <= NF; i++) s += $i } END { exit !(NF >= 2 && s == 0) }'; then
        report_pass "lowlatency-${x}-no-xrun"
    else
        report_fail "lowlatency-${x}-no-xrun"
    fi
done
# shellcheck disable=SC2046
set -- $(audio_tone_check "${OUTPUT}/run.raw" "${FREQ}" 1.5 "$((DURATION - 2))" "${OUTPUT}/silence.raw" "${MIN_DB}")
rt="$1"; { [ "$1" = none ] || [ "${ok}" -ne 0 ]; } && rt=fail
add_metric "lowlatency-tone-detected" "${rt}" "$2" dB
audio_settle

if ll muted "${PERIOD_FRAMES}" 0; then
    m="$(AUDIO_CHECK_ANY=1 audio_tone_check "${OUTPUT}/muted.raw" "${FREQ}" 1.5 "$((DURATION - 2))" "${OUTPUT}/silence.raw" "${MIN_DB}")"
else
    m="none -200"
fi
audio_report_control "lowlatency-muted-not-detected" "${rt}" "${m}"
info_msg "no round-trip latency figure: alsabat needs ALSA devices"
report_skip "roundtrip-latency"
exit 0
