#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# Simultaneous streams: two playback streams (PLAY_A, PLAY_B: PCM names, or
# PipeWire nodes with AUDIO_BACKEND=pipewire) play sines at FREQ_A and
# FREQ_B to the speaker at the same time while the microphones record.
# Each stream alone must be detected at its own frequency, and with both
# running both frequencies must be detected, so a stream that silences
# the other fails. Controls: the capture of stream A alone must not show
# FREQ_B, and the reverse, so a detection cannot come from the room.
# Skipped with the reason without the card, PCMs or tools.

# shellcheck disable=SC1091
. ../../lib/sh-test-lib
# shellcheck disable=SC1091
. ../../lib/audio/audio.sh
OUTPUT="$(pwd)/output"
RESULT_FILE="${OUTPUT}/result.txt"
export RESULT_FILE

CARD=""
PLAY_A=""
PLAY_B=""
CAPTURE_PCM=""
PLAY_MIXER=""
CAPTURE_MIXER=""
PLAY_CHANNEL="2"
RATE="48000"
FREQ_A="1250"
FREQ_B="2000"
TONE_DBFS="-9"
MIN_DB="20"
SECONDS_PER_TONE="4"
WAIT_SECONDS="60"

usage() {
    echo "Usage: $0 [-c card] [-a play A] [-b play B] [-q capture] [-m mixer] [-n mixer] [-k ch]" 1>&2
    echo "          [-R rate] [-f Hz A] [-g Hz B] [-l dBFS] [-d dB] [-T s] [-W s]" 1>&2
    exit 1
}

while getopts "c:a:b:q:m:n:k:R:f:g:l:d:T:W:h" o; do
    case "$o" in
        c) CARD="${OPTARG}" ;;
        a) PLAY_A="${OPTARG}" ;;
        b) PLAY_B="${OPTARG}" ;;
        q) CAPTURE_PCM="${OPTARG}" ;;
        m) PLAY_MIXER="${OPTARG}" ;;
        n) CAPTURE_MIXER="${OPTARG}" ;;
        k) PLAY_CHANNEL="${OPTARG}" ;;
        R) RATE="${OPTARG}" ;;
        f) FREQ_A="${OPTARG}" ;;
        g) FREQ_B="${OPTARG}" ;;
        l) TONE_DBFS="${OPTARG}" ;;
        d) MIN_DB="${OPTARG}" ;;
        T) SECONDS_PER_TONE="${OPTARG}" ;;
        W) WAIT_SECONDS="${OPTARG}" ;;
        h|*) usage ;;
    esac
done

for v in SECONDS_PER_TONE WAIT_SECONDS; do
    case "$(eval echo "\${${v}}")" in
        ''|*[!0-9]*) echo "${v} must be a whole number" 1>&2; usage ;;
    esac
done

CASES="concurrency-setup concurrency-a-alone concurrency-b-alone concurrency-a-alone-no-b concurrency-b-alone-no-a concurrency-both-a concurrency-both-b"
skip_all() {
    local c
    info_msg "$1"
    for c in ${CASES}; do report_skip "${c}"; done
    exit 0
}


# run <case> <play A 0|1> <play B 0|1>
run() {
    local rpid
    audio_record "${CAPDEV}" "${OUTPUT}/$1.raw" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))" > "${OUTPUT}/$1.rec.log" 2>&1 &
    rpid=$!
    sleep 0.5
    rm -f "${OUTPUT}/$1".*.rc
    [ "$2" = 1 ] && { audio_play "${DEVA}" "${OUTPUT}/a.raw" S16_LE "${RATE}" 2 > "${OUTPUT}/$1.a.log" 2>&1; echo $? > "${OUTPUT}/$1.a.rc"; } &
    [ "$3" = 1 ] && { audio_play "${DEVB}" "${OUTPUT}/b.raw" S16_LE "${RATE}" 2 > "${OUTPUT}/$1.b.log" 2>&1; echo $? > "${OUTPUT}/$1.b.rc"; } &
    wait
    cat "${OUTPUT}/$1".*.log 2>/dev/null | grep -v '^$' | head -5
    audio_settle
}

AUDIO_RATE="${RATE}"
create_out_dir "${OUTPUT}"
missing="$(audio_tools_missing)"
[ -z "${missing}" ] || skip_all "missing tools: ${missing}"
C="$(audio_wait_card "${CARD}" "${WAIT_SECONDS}")"
[ -n "${C}" ] || skip_all "no sound card '${CARD}' after ${WAIT_SECONDS} s"
if [ "${AUDIO_BACKEND}" = pipewire ]; then
    audio_pw_start "${PLAY_A}" "${PLAY_B}" "${CAPTURE_PCM}" || skip_all "PipeWire nodes missing"
    DEVA="${PLAY_A}"; DEVB="${PLAY_B}"; CAPDEV="${CAPTURE_PCM}"
else
    A="$(audio_pcm "${C}" playback "${PLAY_A}")"
    B="$(audio_pcm "${C}" playback "${PLAY_B}")"
    Q="$(audio_pcm "${C}" capture "${CAPTURE_PCM}")"
    [ -n "${A}" ] && [ -n "${B}" ] && [ -n "${Q}" ] || skip_all "card ${C}: PCMs '${PLAY_A}' '${PLAY_B}' '${CAPTURE_PCM}' not all present"
    DEVA="hw:${C},${A}"; DEVB="hw:${C},${B}"; CAPDEV="hw:${C},${Q}"
fi
info_msg "streams ${DEVA} (${FREQ_A} Hz) and ${DEVB} (${FREQ_B} Hz), capture ${CAPDEV}"
audio_settle
if audio_mixer "${C}" "${PLAY_MIXER}" && audio_mixer "${C}" "${CAPTURE_MIXER}" &&
   audio_record "${CAPDEV}" "${OUTPUT}/silence.raw" S16_LE "${RATE}" 2 "$((SECONDS_PER_TONE + 1))" > "${OUTPUT}/silence.log" 2>&1; then
    report_pass concurrency-setup
else
    report_fail concurrency-setup
fi
audio_tone "${OUTPUT}/a.raw" S16_LE "${RATE}" 2 "${PLAY_CHANNEL}" "${FREQ_A}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
audio_tone "${OUTPUT}/b.raw" S16_LE "${RATE}" 2 "${PLAY_CHANNEL}" "${FREQ_B}:${SECONDS_PER_TONE}" "${TONE_DBFS}"
# check <raw> <Hz> <player rc file>: "<pass|fail|none> <dB>" (none, or a
# player that did not exit 0, counts as fail)
check() {
    if [ "$(cat "$3" 2>/dev/null)" = 0 ]; then
        audio_tone_check "$1" "$2" 1 "$((SECONDS_PER_TONE - 1))" "${OUTPUT}/silence.raw" "${MIN_DB}" | sed 's/^none/fail/'
    else
        info_msg "$(basename "$3" .rc): player exit $(cat "$3" 2>/dev/null || echo none)" >&2
        audio_tone_check "$1" "$2" 1 "$((SECONDS_PER_TONE - 1))" "${OUTPUT}/silence.raw" "${MIN_DB}" | sed 's/^[a-z]*/fail/'
    fi
}
run alone-a 1 0
run alone-b 0 1
run both 1 1
# shellcheck disable=SC2046
set -- $(check "${OUTPUT}/alone-a.raw" "${FREQ_A}" "${OUTPUT}/alone-a.a.rc"); add_metric concurrency-a-alone "$1" "$2" dB; ra="$1"
# shellcheck disable=SC2046
set -- $(check "${OUTPUT}/alone-b.raw" "${FREQ_B}" "${OUTPUT}/alone-b.b.rc"); add_metric concurrency-b-alone "$1" "$2" dB; rb="$1"
audio_report_control concurrency-a-alone-no-b "${ra}" \
    "$(AUDIO_CHECK_ANY=1 audio_tone_check "${OUTPUT}/alone-a.raw" "${FREQ_B}" 1 "$((SECONDS_PER_TONE - 1))" "${OUTPUT}/silence.raw" "${MIN_DB}")"
audio_report_control concurrency-b-alone-no-a "${rb}" \
    "$(AUDIO_CHECK_ANY=1 audio_tone_check "${OUTPUT}/alone-b.raw" "${FREQ_A}" 1 "$((SECONDS_PER_TONE - 1))" "${OUTPUT}/silence.raw" "${MIN_DB}")"
# shellcheck disable=SC2046
set -- $(check "${OUTPUT}/both.raw" "${FREQ_A}" "${OUTPUT}/both.a.rc"); add_metric concurrency-both-a "$1" "$2" dB
# shellcheck disable=SC2046
set -- $(check "${OUTPUT}/both.raw" "${FREQ_B}" "${OUTPUT}/both.b.rc"); add_metric concurrency-both-b "$1" "$2" dB
exit 0
