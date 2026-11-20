#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Compressed offload: an MP3 of a sine (encoded here with lame from a
# generated PCM file) is played through the card's compressed device with
# cplay (tinycompress), so the DSP decodes it. The stream must play for its
# whole length (the DSP consumed the frames in real time, not at once and
# not stalled), cplay must exit 0, and the microphones must record the
# sine's frequency a margin above silence. A control run with the output
# muted must not be detected, and a stream of random bytes must be refused
# or end cleanly without a hang or a kernel error. Skipped with the reason
# without a compressed device, cplay or lame.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

CARD=""
COMPR_DEVICE=""
CAPTURE_PCM=""
PLAY_MIXER=""
CAPTURE_MIXER=""
MUTE_MIXER=""
CAPTURE_CHANNELS="2"
FREQ="1000"
TONE_DBFS="-12"
SECONDS_LONG="6"
BITRATE="128"
MIN_DB="15"
TOLERANCE_HZ="25"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c <card>] [-D <compressed device>] [-q <capture PCM>]" 1>&2
    echo "          [-m '<ctl>=<v>;...'] [-n '<ctl>=<v>;...'] [-u '<ctl>=<v>;...']" 1>&2
    echo "          [-C <capture ch>] [-f <Hz>] [-l <dBFS>] [-s <seconds>] [-b <kbit/s>]" 1>&2
    echo "          [-d <min dB>] [-t <tolerance Hz>] [-W <wait s>]" 1>&2
    exit 1
}

while getopts "c:D:q:m:n:u:C:f:l:s:b:d:t:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        D) COMPR_DEVICE="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        n) CAPTURE_MIXER="${OPTARG}" ;;
        u) MUTE_MIXER="${OPTARG}" ;;
        C) CAPTURE_CHANNELS="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        s) SECONDS_LONG="${OPTARG}" ;;
        b) BITRATE="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        t) TOLERANCE_HZ="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

CASES="compress-mp3-encode compress-mp3-playback compress-mp3-realtime compress-mp3-${FREQ}hz compress-muted-not-detected compress-garbage-clean"

skip_all() {
    local t
    info_msg "$1"
    for t in ${CASES}; do report_skip "${t}"; done
    exit 0
}

# cplay_timed <file> <log>: play through the compressed device; sets
# CPLAY_RC and CPLAY_S (wall seconds); kills cplay after SECONDS_LONG + 15 s
cplay_timed() {
    local pid i t0
    t0="$(date +%s)"
    cplay -c "${C}" -d "${D}" "$1" > "$2" 2>&1 &
    pid=$!
    i=0
    while kill -0 "${pid}" 2>/dev/null && [ "${i}" -lt $((SECONDS_LONG + 15)) ]; do
        sleep 1
        i=$((i + 1))
    done
    if kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}"; sleep 1; kill -9 "${pid}" 2>/dev/null
        wait "${pid}"
        CPLAY_RC=124
    else
        wait "${pid}"
        CPLAY_RC=$?
    fi
    CPLAY_S=$(($(date +%s) - t0))
    cat "$2"
}

# play_and_record <mp3> <name>: capture while cplay plays the file
play_and_record() {
    arecord -q -D "hw:${C},${Q}" -t raw -f S16_LE -r 48000 -c "${CAPTURE_CHANNELS}" \
        -d "$((SECONDS_LONG + 2))" "${OUTPUT}/$2.raw" > "${OUTPUT}/$2.rec.log" 2>&1 &
    rpid=$!
    sleep 0.5
    cplay_timed "$1" "${OUTPUT}/$2.log"
    wait "${rpid}"
    audio_analyze "${OUTPUT}/$2.raw" S16_LE 48000 "${CAPTURE_CHANNELS}" 1.5 "$((SECONDS_LONG - 2))" "${FREQ}" > "${OUTPUT}/$2.txt"
    sed "s/^/$2: /" "${OUTPUT}/$2.txt"
}

best() {
    awk '{ l = $4; sub(/level=/, "", l); if (b == "" || l + 0 > bl) { b = $0; bl = l + 0 } } END { print b }' "$1"
}

create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing)"
for t in cplay lame; do command -v "${t}" > /dev/null 2>&1 || missing="${missing}${t} "; done
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
i=0
while [ -z "$(audio_card "${CARD}")" ] && [ "${i}" -lt "${WAIT_SECONDS}" ]; do sleep 1; i=$((i + 1)); done
C="$(audio_card "${CARD}")"
[ -n "${C}" ] || skip_all "no sound card '${CARD}' after ${WAIT_SECONDS} s"
D="${COMPR_DEVICE}"
if [ -z "${D}" ]; then
    D="$(ls /dev/snd/ 2>/dev/null | sed -n "s/^comprC${C}D\([0-9]*\)$/\1/p" | head -n 1)"
fi
[ -n "${D}" ] && [ -e "/dev/snd/comprC${C}D${D}" ] || skip_all "card ${C} has no compressed device (${COMPR_DEVICE:-any})"
Q="$(audio_pcm "${C}" capture "${CAPTURE_PCM}")"
[ -n "${Q}" ] || skip_all "card ${C}: no capture PCM '${CAPTURE_PCM}'"
info_msg "card ${C}: compressed device ${D}, capture hw:${C},${Q}"
t0="$(cut -d' ' -f1 /proc/uptime)"

audio_tone "${OUTPUT}/tone.raw" S16_LE 48000 2 all "${FREQ}:${SECONDS_LONG}" "${TONE_DBFS}"
if lame --quiet -r -s 48 --bitwidth 16 --signed --little-endian -m j -b "${BITRATE}" \
        "${OUTPUT}/tone.raw" "${OUTPUT}/tone.mp3" && [ -s "${OUTPUT}/tone.mp3" ]; then
    add_metric "compress-mp3-encode" pass "$(wc -c < "${OUTPUT}/tone.mp3")" "bytes"
else
    report_fail "compress-mp3-encode"
fi

audio_mixer "${C}" "${PLAY_MIXER}"
audio_mixer "${C}" "${CAPTURE_MIXER}"
arecord -q -D "hw:${C},${Q}" -t raw -f S16_LE -r 48000 -c "${CAPTURE_CHANNELS}" -d 3 \
    "${OUTPUT}/silence.raw" > "${OUTPUT}/silence.log" 2>&1
audio_analyze "${OUTPUT}/silence.raw" S16_LE 48000 "${CAPTURE_CHANNELS}" 0.5 2 "${FREQ}" > "${OUTPUT}/silence.txt"
sed 's/^/silence: /' "${OUTPUT}/silence.txt"

play_and_record "${OUTPUT}/tone.mp3" play
PLAY_RC="${CPLAY_RC}"
[ "${PLAY_RC}" -eq 0 ] && report_pass "compress-mp3-playback" || {
    info_msg "cplay exit ${PLAY_RC}"; report_fail "compress-mp3-playback"; }
# Offload decodes in real time: the stream lasts about its own length.
if [ "${CPLAY_RC}" -eq 0 ] && [ "${CPLAY_S}" -ge $((SECONDS_LONG - 1)) ] && [ "${CPLAY_S}" -le $((SECONDS_LONG + 5)) ]; then
    add_metric "compress-mp3-realtime" pass "${CPLAY_S}" "s"
else
    add_metric "compress-mp3-realtime" fail "${CPLAY_S}" "s"
fi
line="$(best "${OUTPUT}/play.txt")"
ch="$(audio_field "${line}" ch)"
base="$(audio_field "$(sed -n "${ch:-1}p" "${OUTPUT}/silence.txt")" level)"
m="$(awk -v l="$(audio_field "${line}" level)" -v b="${base}" 'BEGIN { printf "%.1f", l - b }')"
info_msg "decoded output: ${line} (silence ${base} dBFS)"
# a tone heard while cplay failed came from somewhere else
rt=fail
[ "${PLAY_RC}" -eq 0 ] && audio_detect "${line}" "${FREQ}" "${base}" "${MIN_DB}" "${TOLERANCE_HZ}" && rt=pass
add_metric "compress-mp3-${FREQ}hz" "${rt}" "${m}" "dB"

if [ -n "${MUTE_MIXER}" ] && [ "${rt}" != pass ]; then
    info_msg "compress-muted-not-detected: the positive case did not pass, the control proves nothing"
    report_skip "compress-muted-not-detected"
elif [ -n "${MUTE_MIXER}" ]; then
    audio_mixer "${C}" "${MUTE_MIXER}"
    play_and_record "${OUTPUT}/tone.mp3" muted
    line="$(best "${OUTPUT}/muted.txt")"
    m="$(awk -v l="$(audio_field "${line}" level)" -v b="${base}" 'BEGIN { printf "%.1f", l - b }')"
    if [ "${CPLAY_RC}" -eq 0 ] && ! audio_detect "${line}" "${FREQ}" "${base}" "${MIN_DB}" "${TOLERANCE_HZ}"; then
        add_metric "compress-muted-not-detected" pass "${m}" "dB"
    else
        info_msg "muted: cplay exit ${CPLAY_RC}, ${line}"
        add_metric "compress-muted-not-detected" fail "${m}" "dB"
    fi
    audio_mixer "${C}" "${PLAY_MIXER}"
else
    report_skip "compress-muted-not-detected"
fi

# Random bytes named .mp3: refused or played out, never a hang or an oops.
head -c 65536 /dev/urandom > "${OUTPUT}/garbage.mp3"
t1="$(cut -d' ' -f1 /proc/uptime)"
cplay_timed "${OUTPUT}/garbage.mp3" "${OUTPUT}/garbage.log"
n="$(audio_kernel_errors "${t1}")"
info_msg "garbage stream: cplay exit ${CPLAY_RC} after ${CPLAY_S} s, ${n} kernel error lines"
if [ "${CPLAY_RC}" -ne 124 ] && [ "${n}" -eq 0 ]; then
    report_pass "compress-garbage-clean"
else
    report_fail "compress-garbage-clean"
fi
n="$(audio_kernel_errors "${t0}")"
[ "${n}" -eq 0 ] || audio_since "${t0}" | grep -E "WARNING:|BUG:|Oops|Internal error|Unable to handle|external abort" | head
exit 0
