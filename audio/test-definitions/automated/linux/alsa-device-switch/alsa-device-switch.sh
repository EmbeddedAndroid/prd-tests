#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Device switch during a running stream: the speaker plays a sine while one
# capture stream records; the capture routing is switched from device A to
# a device that does not pick the tone up (ROUTE_OFF, e.g. microphone
# inputs with nothing fitted) and then to device B without stopping the
# stream. The tone must be detected in the A and B segments and must NOT be
# detected in the off segment, which proves the switch reached the
# hardware; the stream must deliver the whole recording.
# Skipped with the reason without the card, the PCMs or the tools.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

CARD=""
PLAY_PCM=""
CAPTURE_PCM=""
PLAY_MIXER=""
ROUTE_A=""
ROUTE_OFF=""
ROUTE_B=""
PLAY_CHANNEL="2"
RATE="48000"
FREQ="1250"
TONE_DBFS="-6"
MIN_DB="20"
SEGMENT="3"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c <card>] [-p <playback PCM>] [-q <capture PCM>] [-m <play mixer>]" 1>&2
    echo "          [-a <route A>] [-o <route off>] [-b <route B>] [-k <play channel>]" 1>&2
    echo "          [-R <rate>] [-f <Hz>] [-l <dBFS>] [-d <min dB>] [-S <segment s>] [-W <s>]" 1>&2
    exit 1
}

while getopts "c:p:q:m:a:o:b:k:R:f:l:d:S:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        p) PLAY_PCM="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        a) ROUTE_A="${OPTARG}" ;;
        o) ROUTE_OFF="${OPTARG}" ;;
        b) ROUTE_B="${OPTARG}" ;;
        k) PLAY_CHANNEL="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        S) SEGMENT="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

CASES="switch-setup switch-a-detected switch-off-not-detected switch-b-detected switch-stream-complete"
for v in SEGMENT WAIT_SECONDS; do
    case "$(eval echo "\${${v}}")" in
        ''|*[!0-9]*) echo "${v} must be a whole number of seconds" 1>&2; usage ;;
    esac
done

skip_all() {
    local c
    info_msg "$1"
    for c in ${CASES}; do report_skip "${c}"; done
    exit 0
}


AUDIO_RATE="${RATE}"
create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing)"
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
[ -n "${ROUTE_A}" ] && [ -n "${ROUTE_OFF}" ] && [ -n "${ROUTE_B}" ] || skip_all "ROUTE_A, ROUTE_OFF and ROUTE_B are required"
C="$(audio_wait_card "${CARD}" "${WAIT_SECONDS}")"
[ -n "${C}" ] || skip_all "no sound card '${CARD}' after ${WAIT_SECONDS} s"
P="$(audio_pcm "${C}" playback "${PLAY_PCM}")"
Q="$(audio_pcm "${C}" capture "${CAPTURE_PCM}")"
[ -n "${P}" ] && [ -n "${Q}" ] || skip_all "card ${C}: no playback PCM '${PLAY_PCM}' or capture PCM '${CAPTURE_PCM}'"
PLAYDEV="hw:${C},${P}"
CAPDEV="hw:${C},${Q}"

if audio_mixer "${C}" "${PLAY_MIXER}" && audio_mixer "${C}" "${ROUTE_A}" &&
   audio_record "${CAPDEV}" "${OUTPUT}/silence.raw" S16_LE "${RATE}" 2 "$((SEGMENT + 1))"; then
    report_pass "switch-setup"
else
    report_fail "switch-setup"
fi

total=$((3 * SEGMENT + 1))
audio_tone "${OUTPUT}/tone.raw" S16_LE "${RATE}" 2 "${PLAY_CHANNEL}" "${FREQ}:$((3 * SEGMENT))" "${TONE_DBFS}"
# capture starts at 0, playback at 0.5 s; switch at 0.5 + SEGMENT and 0.5 + 2 SEGMENT
(
    sleep "$(awk -v s="${SEGMENT}" 'BEGIN { print 0.5 + s }')"
    audio_mixer "${C}" "${ROUTE_OFF}" && echo "switched to off at $(cut -d' ' -f1 /proc/uptime)"
    sleep "${SEGMENT}"
    audio_mixer "${C}" "${ROUTE_B}" && echo "switched to B at $(cut -d' ' -f1 /proc/uptime)"
) > "${OUTPUT}/switch.log" 2>&1 &
spid=$!
echo "stream start at $(cut -d' ' -f1 /proc/uptime)"
audio_play_record "${PLAYDEV}" "${OUTPUT}/tone.raw" S16_LE "${RATE}" 2 \
    "${CAPDEV}" "${OUTPUT}/switch.raw" S16_LE "${RATE}" 2 "${total}"
rc=$?
wait "${spid}"
cat "${OUTPUT}/switch.log"
n="$(wc -c < "${OUTPUT}/switch.raw" 2>/dev/null || echo 0)"
want=$((total * RATE * 4))
info_msg "player/recorder rc ${rc}, captured ${n} of ${want} bytes"
if [ "${rc}" -eq 0 ] && [ "${n}" -ge $((want * 95 / 100)) ]; then
    report_pass "switch-stream-complete"
else
    report_fail "switch-stream-complete"
fi
# keep 0.5 s clear of each switch
len=$((SEGMENT - 1))
# shellcheck disable=SC2046
set -- $(audio_tone_check "${OUTPUT}/switch.raw" "${FREQ}" 1.0 "${len}" "${OUTPUT}/silence.raw" "${MIN_DB}")
ra="$1"
[ "$1" = none ] || [ "${rc}" -ne 0 ] && ra=fail
add_metric "switch-a-detected" "${ra}" "$2" dB
off="$(AUDIO_CHECK_ANY=1 audio_tone_check "${OUTPUT}/switch.raw" "${FREQ}" $((SEGMENT + 1)) "${len}" "${OUTPUT}/silence.raw" "${MIN_DB}")"
audio_report_control "switch-off-not-detected" "${ra}" "${off}"
# shellcheck disable=SC2046
set -- $(audio_tone_check "${OUTPUT}/switch.raw" "${FREQ}" $((2 * SEGMENT + 1)) "${len}" "${OUTPUT}/silence.raw" "${MIN_DB}")
rb="$1"
[ "$1" = none ] || [ "${rc}" -ne 0 ] && rb=fail
add_metric "switch-b-detected" "${rb}" "$2" dB
audio_mixer "${C}" "${ROUTE_A}"
exit 0
